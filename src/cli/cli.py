from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from ..ai import (
    _build_query_reading_order,
    _get_hot_files,
    _rank_symbols_for_file,
    render_impact_report,
    render_query_report,
    render_routes_report,
    render_verify_report,
)
from ..check import RepoMapChecker
from ..core import RepoMapEngine, SKIP_DIR_NAMES, SKIP_FILE_NAMES
from ..parser import EXT_TO_LANG
from .. import (
    Edge,
    HttpRoute,
    RepoGraph,
    ScanStats,
    Symbol,
    get_cache_paths,
    get_session_cache_path,
    serialize_edge,
    serialize_symbol,
)
from ..toolkit import diff_project, save_cache, scan_project
from ..topic import (
    FileMatch,
    TestMatch,
    classify_file_role,
    compute_keyword_weights,
    expand_keywords,
    find_related_tests,
    find_untested_symbols,
    is_test_like_file,
    split_identifier,
    topic_score,
)

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
CLI_NAME = "repomap"

# 统一 exit code 语义
EXIT_SUCCESS = 0       # 成功，有有效输出
EXIT_ERROR = 1         # Command execution failed
EXIT_INVALID_ARGS = 2  # 参数错误
EXIT_NO_RESULTS = 3    # 无结果（query 无匹配、routes 为空）
PYINSTALLER_BINDINGS = [
    "tree_sitter",
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_go",
    "tree_sitter_rust",
    "tree_sitter_html",
    "tree_sitter_css",
    "tree_sitter_json",
    "repomap_lsp",
]

_SCAN_CACHE: dict[tuple[str, int, int, str, bool], tuple[str, RepoMapEngine]] = {}
# 缓存语义变更时需要升级，避免 CLI/Binary 复用旧结果误导阅读顺序和调用链。
SESSION_CACHE_VERSION = 6
DEFAULT_OVERVIEW_MAX_CHARS = 16000
DEFAULT_QUERY_SYMBOL_MAX_CHARS = 4000
DEFAULT_CALL_CHAIN_MAX_CHARS = 4000
DEFAULT_FILE_DETAIL_MAX_CHARS = 6000
DEFAULT_FILE_DETAIL_MAX_SYMBOLS = 12
DEFAULT_OVERVIEW_JSON_HOTSPOTS = 8
DEFAULT_OVERVIEW_JSON_READING_ORDER = 6
DEFAULT_OVERVIEW_JSON_MODULES = 6
DEFAULT_OVERVIEW_JSON_SUMMARY_FILES = 4
DEFAULT_OVERVIEW_JSON_SYMBOLS_PER_FILE = 3
DEFAULT_OVERVIEW_JSON_SUPPORTING_FILES = 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI_NAME,
        description="Standalone RepoMap CLI. Former MCP capabilities are exposed as direct subcommands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan a repository and print the scan summary.")
    _add_project_args(scan_parser)

    overview_parser = subparsers.add_parser("overview", help="Scan a repository and print the overview report.")
    _add_project_args(overview_parser)
    overview_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_OVERVIEW_MAX_CHARS,
        help="Maximum overview size for AI-friendly output.",
    )
    overview_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    overview_parser.add_argument("--with-heat", action="store_true", default=False,
                                help="Mark files changed in the last 30 days with [HOT].")
    overview_parser.add_argument("--no-co-change", action="store_true", default=False,
                                help="Skip Git co-change coupling section.")
    overview_parser.add_argument("--granularity", choices=["full", "medium", "compact", "auto"],
                                default="auto",
                                help="Report granularity (default: auto, based on project size).")

    chain_parser = subparsers.add_parser("call-chain", help="Scan a repository and print a symbol call chain.")
    _add_project_args(chain_parser)
    chain_parser.add_argument("--symbol", required=True, help="Symbol name to analyze.")
    chain_parser.add_argument("--file-path", help="Disambiguate by relative file path.")
    chain_parser.add_argument("--direction", choices=["callers", "callees", "both"], default="both")
    chain_parser.add_argument("--depth", type=int, default=3, help="Traversal depth.")
    chain_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_CALL_CHAIN_MAX_CHARS,
        help="Maximum text output size.",
    )
    chain_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

    query_parser = subparsers.add_parser("query-symbol", help="Scan a repository and query matching symbols.")
    _add_project_args(query_parser)
    query_parser.add_argument("--symbol", required=True, help="Symbol name to search for.")
    query_parser.add_argument("--file-path", help="Optional relative file path filter.")
    query_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_QUERY_SYMBOL_MAX_CHARS,
        help="Maximum text output size.",
    )
    query_parser.add_argument("--with-lsp", action="store_true", help="Also query local LSP definition/reference evidence for the best match.")
    query_parser.add_argument("--lsp-timeout", type=float, default=8.0, help="Seconds to wait for LSP responses.")

    # ── 新增: query（主题关键词搜索）──────────────────────────────────────────
    topic_query_parser = subparsers.add_parser("query", help="Search repository by topic keyword.")
    topic_query_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    topic_query_parser.add_argument("--query", "-q", required=True, help="Topic keyword.")
    topic_query_parser.add_argument("--max-files", type=int, default=20, help="Max result files (default 20).")
    topic_query_parser.add_argument("--max-symbols", type=int, default=40, help="Max result symbols (default 40).")
    topic_query_parser.add_argument("--no-tests", action="store_true")
    topic_query_parser.add_argument("--json", action="store_true")
    topic_query_parser.add_argument("--paths", help="Limit search to comma-separated directories.")
    topic_query_parser.add_argument("--exclude", help="Exclude comma-separated directories.")

    # ── 新增: impact（文件级影响分析）──────────────────────────────────────────
    impact_parser = subparsers.add_parser("impact", help="Analyze file-level change impact.")
    impact_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    impact_parser.add_argument("--files", required=True, nargs="+", help="Files to analyze (one or more).")
    impact_parser.add_argument("--json", action="store_true")
    impact_parser.add_argument("--max-files", type=int, default=20, help="Max affected files to show.")
    impact_parser.add_argument("--with-symbols", action="store_true", help="Include edit-planning key symbols, read-next order, and LSP availability hint.")
    impact_parser.add_argument("--depth", type=int, default=1, help="Transitive impact depth (default 1=direct, 2=one hop out).")

    verify_parser = subparsers.add_parser("verify", help="Aggregate post-edit evidence before final handoff.")
    verify_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    verify_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    verify_parser.add_argument("--types", nargs="*", choices=["typescript", "rust", "python", "go", "javascript"], help="Explicit project types to check.")
    verify_parser.add_argument("--max-issues", type=int, default=50, help="Maximum issues per tool.")
    verify_parser.add_argument("--no-symbols", action="store_true", help="Skip scan-based symbol resolution for diagnostics.")
    verify_parser.add_argument("--with-lsp", action="store_true", help="Include focused LSP diagnostics for changed files.")
    verify_parser.add_argument("--no-incremental", action="store_true", help="Force full scan instead of incremental.")
    verify_parser.add_argument("--lsp-timeout", type=float, default=8.0, help="Seconds to wait for LSP responses.")
    verify_parser.add_argument("--lsp-max-files", type=int, default=20, help="Maximum changed files to open through LSP.")
    verify_parser.add_argument("--with-diff", action="store_true", help="Include graph diff when a cache baseline exists.")
    verify_parser.add_argument("--quick", action="store_true", help="Risk-only mode for current Git changes; skips compiler and LSP checks.")

    file_parser = subparsers.add_parser("file-detail", help="Scan a repository and print file detail.")
    _add_project_args(file_parser)
    file_parser.add_argument("--file-path", required=True, help="Relative file path to inspect.")
    file_parser.add_argument(
        "--max-symbols",
        type=int,
        default=DEFAULT_FILE_DETAIL_MAX_SYMBOLS,
        help="Maximum symbols to expand in text output.",
    )
    file_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_FILE_DETAIL_MAX_CHARS,
        help="Maximum text output size.",
    )

    hotspots_parser = subparsers.add_parser("hotspots", help="Scan a repository and print hotspot files.")
    _add_project_args(hotspots_parser)
    hotspots_parser.add_argument("--limit", type=int, default=15, help="Number of files to print.")

    cache_parser = subparsers.add_parser("cache", help="Prepare a graph baseline before the target edits.")
    cache_parser.add_argument("action", choices=["save"], help="Cache action. Only save is public; graph comparison reads the baseline through diff/verify --with-diff.")
    cache_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")

    diff_parser = subparsers.add_parser("diff", help="Advanced graph-only comparison against a baseline saved before the target edits.")
    diff_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    diff_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

    git_parser = subparsers.add_parser("git-history", help="Scan a repository and inspect symbol git history.")
    _add_project_args(git_parser)
    git_parser.add_argument("--symbol", required=True, help="Symbol name to inspect.")
    git_parser.add_argument("--file-path", help="Disambiguate by relative file path.")

    refs_parser = subparsers.add_parser("refs", help="Scan a repository and analyze references.")
    _add_project_args(refs_parser)
    refs_parser.add_argument("--symbol", help="Optional symbol name.")
    refs_parser.add_argument("--file-path", help="Disambiguate symbol analysis by relative file path.")
    refs_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    refs_parser.add_argument("--with-lsp", action="store_true", help="Also query local LSP definition/reference evidence for the selected symbol.")
    refs_parser.add_argument("--lsp-timeout", type=float, default=8.0, help="Seconds to wait for LSP responses.")

    orphan_parser = subparsers.add_parser("orphan", help="Scan a repository and find orphaned symbols.")
    _add_project_args(orphan_parser)
    orphan_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    orphan_parser.add_argument("--limit", type=int, default=20, help="Max candidates per confidence tier in text mode (default 20).")
    orphan_parser.add_argument("--min-confidence", type=int, default=0, help="Minimum confidence score 0-100 to include in output (default 0).")

    check_parser = subparsers.add_parser("check", help="Run compiler/static analysis diagnostics.")
    check_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    check_parser.add_argument(
        "--types",
        nargs="*",
        choices=["typescript", "rust", "python", "go", "javascript"],
        help="Explicit project types to check.",
    )
    check_parser.add_argument("--max-issues", type=int, default=50, help="Maximum issues per tool.")
    check_parser.add_argument("--since-commit", help="Only check files changed since the given commit.")
    check_parser.add_argument("--modified-file", action="append", dest="modified_files", metavar="PATH", help="Explicit modified file path.")
    check_parser.add_argument("--no-symbols", action="store_true", help="Skip scan-based symbol resolution.")
    check_parser.add_argument("--with-lsp", action="store_true", help="Also collect diagnostics from local LSP servers for explicit files.")
    check_parser.add_argument("--lsp-timeout", type=float, default=8.0, help="Seconds to wait for LSP responses.")
    check_parser.add_argument("--lsp-max-files", type=int, default=20, help="Maximum explicit files to open through LSP.")

    diagnostics_parser = subparsers.add_parser("diagnostics", help="Focused diagnostics for explicit files from optional evidence sources.")
    diagnostics_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    diagnostics_parser.add_argument("--source", choices=["lsp"], default="lsp", help="Diagnostics source.")
    diagnostics_parser.add_argument("--files", nargs="+", required=True, help="Project files to check with the diagnostics source.")
    diagnostics_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    diagnostics_parser.add_argument("--lsp-timeout", type=float, default=8.0, help="Seconds to wait for LSP responses.")
    diagnostics_parser.add_argument("--lsp-max-files", type=int, default=20, help="Maximum files to open through LSP.")

    lsp_parser = subparsers.add_parser("lsp", help="Inspect local LSP server availability.")
    lsp_subparsers = lsp_parser.add_subparsers(dest="lsp_command", required=True)
    lsp_doctor_parser = lsp_subparsers.add_parser("doctor", help="Detect local LSP servers without starting analysis.")
    lsp_doctor_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    lsp_doctor_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

    routes_parser = subparsers.add_parser("routes", help="Extract direct HTTP/API route inventory.")
    _add_project_args(routes_parser)
    routes_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    routes_parser.add_argument("--with-consumers", action="store_true", help="Scan for frontend/client consumers of each route.")

    doctor_parser = subparsers.add_parser("doctor", help="Validate runtime and build prerequisites.")
    doctor_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")

    state_map_parser = subparsers.add_parser("state-map", help="Map state values, writers, and readers for an enum/type.")
    _add_project_args(state_map_parser)
    state_map_parser.add_argument("--symbol", default=None, help="Symbol name (e.g. TaskStatus).")
    state_map_parser.add_argument("--query", default=None, help="Keywords to find relevant state definitions.")
    state_map_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

    build_parser_cmd = subparsers.add_parser("build-binary", help="Build a one-file executable with PyInstaller.")
    build_parser_cmd.add_argument("--output", default="dist", help="Directory for the final binary.")
    build_parser_cmd.add_argument("--name", default=CLI_NAME, help="Binary file name.")

    return parser


def _add_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    parser.add_argument("--max-files", type=int, default=8000, help="Maximum number of files to scan.")


def _prepare_argv(argv: Sequence[str] | None) -> list[str] | None:
    if argv is None:
        raw_args = sys.argv[1:]
    else:
        raw_args = list(argv)
    prepared: list[str] = []
    i = 0
    while i < len(raw_args):
        item = raw_args[i]
        if item == "--modified-file" and i + 1 < len(raw_args):
            prepared.append(f"--modified-file={raw_args[i + 1]}")
            i += 2
            continue
        prepared.append(item)
        i += 1
    return prepared


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(_prepare_argv(argv))
    except SystemExit as exc:
        return int(exc.code or 0)

    command = args.command
    if command == "scan":
        return run_scan(args.project, args.max_files)
    if command == "overview":
        return run_overview(args.project, args.max_files, args.max_chars, args.json,
                           with_heat=getattr(args, "with_heat", False),
                           with_co_change=not getattr(args, "no_co_change", False),
                           granularity=getattr(args, "granularity", "auto"))
    if command == "call-chain":
        return run_call_chain(
            args.project,
            args.max_files,
            args.symbol,
            args.file_path,
            args.direction,
            args.depth,
            args.max_chars,
            args.json,
        )
    if command == "query-symbol":
        return run_query_symbol(args.project, args.max_files, args.symbol, args.file_path, args.max_chars, args.with_lsp, args.lsp_timeout)
    if command == "query":
        return run_query(
            args.project, 8000, args.query,
            getattr(args, "max_files", 20), getattr(args, "max_symbols", 40),
            args.no_tests, args.json, args.paths, args.exclude,
        )
    if command == "impact":
        return run_impact(
            args.project, 8000, args.files,
            getattr(args, "max_files", 20), args.json, getattr(args, "with_symbols", False),
            depth=getattr(args, "depth", 1),
            incremental=not getattr(args, "no_incremental", False),
        )
    if command == "verify":
        return run_verify(
            project=args.project,
            as_json=args.json,
            types=args.types,
            max_issues=args.max_issues,
            resolve_symbols=not args.no_symbols,
            with_lsp=args.with_lsp,
            lsp_timeout=args.lsp_timeout,
            lsp_max_files=args.lsp_max_files,
            with_diff=args.with_diff,
            quick=args.quick,
            incremental=not getattr(args, "no_incremental", False),
        )
    if command == "file-detail":
        return run_file_detail(args.project, args.max_files, args.file_path, args.max_symbols, args.max_chars)
    if command == "hotspots":
        return run_hotspots(args.project, args.max_files, args.limit)
    if command == "cache":
        return run_cache(args.project, args.action)
    if command == "diff":
        return run_diff(args.project, args.json)
    if command == "git-history":
        return run_git_history(args.project, args.max_files, args.symbol, args.file_path)
    if command == "refs":
        return run_refs(args.project, args.max_files, args.symbol, args.file_path, args.json, args.with_lsp, args.lsp_timeout)
    if command == "orphan":
        return run_orphan(args.project, args.max_files, args.json, args.limit, args.min_confidence)
    if command == "check":
        return run_check(
            project=args.project,
            types=args.types,
            max_issues=args.max_issues,
            since_commit=args.since_commit,
            modified_files=args.modified_files,
            resolve_symbols=not args.no_symbols,
            with_lsp=args.with_lsp,
            lsp_timeout=args.lsp_timeout,
            lsp_max_files=args.lsp_max_files,
        )
    if command == "diagnostics":
        return run_diagnostics(args.project, args.source, args.files, args.json, args.lsp_timeout, args.lsp_max_files)
    if command == "lsp":
        if args.lsp_command == "doctor":
            return run_lsp_doctor(args.project, args.json)
        parser.error(f"unknown lsp command: {args.lsp_command}")
        return 2
    if command == "routes":
        return run_routes(args.project, args.max_files, args.json, args.with_consumers)
    if command == "doctor":
        return run_doctor(args.project)
    if command == "state-map":
        return run_state_map(args.project, args.max_files, args.symbol, args.query, args.json)
    if command == "build-binary":
        return run_build_binary(args.output, args.name)
    parser.error(f"unknown command: {command}")
    return 2


def _resolve_project(project: str | None) -> str:
    project_path = Path.cwd().resolve() if project is None else Path(project).expanduser().resolve()
    if not project_path.is_dir():
        raise ValueError(f"project path is not a directory: {project_path}")
    if project is None and project_path == Path.home().resolve():
        print(
            f"[{CLI_NAME}] warning: default project root is your home directory: {project_path}. "
            "Run from the intended project directory or pass --project explicitly.",
            file=sys.stderr,
        )
    return str(project_path)


def _normalize_project_relative_path(project_root: str | Path, value: str, *, must_exist: bool = False) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("path is empty")
    if raw.startswith("-"):
        raise ValueError(f"unsafe path starts with '-': {value}")
    project_path = Path(project_root).resolve()
    input_path = Path(raw).expanduser()
    abs_path = input_path.resolve() if input_path.is_absolute() else (project_path / input_path).resolve()
    try:
        rel = abs_path.relative_to(project_path)
    except ValueError:
        raise ValueError(f"path is outside project: {value}") from None
    if must_exist and not abs_path.exists():
        raise ValueError(f"path does not exist: {value}")
    rel_path = rel.as_posix()
    if rel_path in ("", "."):
        raise ValueError(f"path must reference a project file or subdirectory: {value}")
    return rel_path


def _normalize_project_relative_paths(project_root: str | Path, values: list[str], *, must_exist: bool = False) -> list[str]:
    return [_normalize_project_relative_path(project_root, value, must_exist=must_exist) for value in values]


def _normalize_path_prefix(project_root: str | Path, prefix: str) -> str:
    return _normalize_project_relative_path(project_root, prefix.rstrip("/"), must_exist=False)


def _path_matches_prefix(file_path: str, prefix: str) -> bool:
    return file_path == prefix or file_path.startswith(prefix.rstrip("/") + "/")


def _read_max_file_bytes() -> int:
    raw = os.getenv("REPOMAP_MAX_FILE_BYTES", str(512 * 1024))
    try:
        value = int(raw)
    except ValueError:
        return 512 * 1024
    return max(0, value)


def _iter_source_files(project_root: Path) -> list[str]:
    files: list[str] = []
    for root, dir_names, file_names in os.walk(project_root):
        dir_names[:] = [name for name in dir_names if name not in SKIP_DIR_NAMES]
        rel_root = Path(root).relative_to(project_root)
        for file_name in file_names:
            suffix = Path(file_name).suffix.lower()
            if suffix not in EXT_TO_LANG:
                continue
            if file_name in SKIP_FILE_NAMES or file_name.endswith(".min.js"):
                continue
            rel_path = (rel_root / file_name).as_posix() if str(rel_root) != "." else file_name
            if any(part in SKIP_DIR_NAMES for part in Path(rel_path).parts):
                continue
            files.append(rel_path)
    return sorted(files)


def _scan_fingerprint(project_root: str, max_files: int) -> str:
    root = Path(project_root)
    max_file_bytes = _read_max_file_bytes()
    scan_large_files = os.getenv("REPOMAP_SCAN_LARGE_FILES", "0")
    digest = hashlib.sha256()
    digest.update(project_root.encode("utf-8"))
    digest.update(str(max_files).encode("utf-8"))
    digest.update(str(max_file_bytes).encode("utf-8"))
    digest.update(scan_large_files.encode("utf-8"))

    selected = _iter_source_files(root)[:max_files]
    digest.update(str(len(selected)).encode("utf-8"))
    for rel_path in selected:
        path = root / rel_path
        try:
            stat = path.stat()
        except OSError:
            digest.update(f"{rel_path}:missing".encode("utf-8"))
            continue
        if scan_large_files != "1" and stat.st_size > max_file_bytes:
            digest.update(f"{rel_path}:skip:{stat.st_size}".encode("utf-8"))
            continue
        digest.update(rel_path.encode("utf-8"))
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
    return digest.hexdigest()


def _scan_engine(project: str | None, max_files: int, incremental: bool = False) -> RepoMapEngine:
    resolved_project = _resolve_project(project)
    cache_key = (
        resolved_project,
        max_files,
        _read_max_file_bytes(),
        os.getenv("REPOMAP_SCAN_LARGE_FILES", "0"),
        incremental,
    )
    fingerprint = _scan_fingerprint(resolved_project, max_files)
    cached = _SCAN_CACHE.get(cache_key)
    if cached and cached[0] == fingerprint:
        return cached[1]

    session_engine = _load_session_engine(resolved_project, fingerprint)
    if session_engine is not None:
        _SCAN_CACHE[cache_key] = (fingerprint, session_engine)
        print(f"[{CLI_NAME}] Session cache restored from disk", file=sys.stderr)
        return session_engine

    engine = RepoMapEngine(resolved_project)
    engine.scan(max_files=max_files, incremental=incremental)
    _save_session_engine(resolved_project, fingerprint, engine)
    _SCAN_CACHE[cache_key] = (fingerprint, engine)
    return engine


def _engine_to_session_payload(project_root: str, fingerprint: str, engine: RepoMapEngine) -> dict[str, Any]:
    symbols = [serialize_symbol(symbol) for symbol in engine.graph.symbols.values()]
    outgoing = {
        source_id: [serialize_edge(edge) for edge in edges]
        for source_id, edges in engine.graph.outgoing.items()
        if edges
    }
    return {
        "version": SESSION_CACHE_VERSION,
        "project_root": project_root,
        "fingerprint": fingerprint,
        "scan_state": engine.scan_state,
        "scan_stats": {
            "listed_source_files": engine.scan_stats.listed_source_files,
            "selected_source_files": engine.scan_stats.selected_source_files,
            "processed_files": engine.scan_stats.processed_files,
            "filtered_path_files": engine.scan_stats.filtered_path_files,
            "filtered_large_files": engine.scan_stats.filtered_large_files,
            "truncated_files": engine.scan_stats.truncated_files,
            "failed_files": list(engine.scan_stats.failed_files),
            "scan_duration_ms": engine.scan_stats.scan_duration_ms,
            "timeout_triggered": engine.scan_stats.timeout_triggered,
            "skipped_files": engine.scan_stats.skipped_files,
        },
        "symbols": symbols,
        "outgoing": outgoing,
        "file_symbols": {
            file_path: list(symbol_ids)
            for file_path, symbol_ids in engine.graph.file_symbols.items()
        },
        "file_imports": {
            file_path: list(imports)
            for file_path, imports in engine.graph.file_imports.items()
        },
        "file_calls": {
            file_path: [[c[0], c[1], c[2]] if len(c) >= 3 else [c[0], c[1], "direct"]
                        for c in calls]
            for file_path, calls in engine.graph.file_calls.items()
        },
        "file_import_bindings": {
            file_path: [{"local_name": b.local_name, "imported_name": b.imported_name,
                         "module": b.module, "line": b.line, "kind": b.kind}
                        for b in bindings]
            for file_path, bindings in engine.graph.file_import_bindings.items()
        },
        "file_exports": {
            file_path: [{"exported_name": b.exported_name, "source_name": b.source_name,
                         "module": b.module, "line": b.line, "kind": b.kind}
                        for b in exports]
            for file_path, exports in engine.graph.file_exports.items()
        },
        "routes": [
            _route_payload(r)
            for r in engine.routes
        ],
    }


def _restore_engine_from_session_payload(payload: dict[str, Any]) -> RepoMapEngine | None:
    if payload.get("version") != SESSION_CACHE_VERSION:
        return None
    project_root = payload.get("project_root")
    if not project_root:
        return None

    engine = RepoMapEngine(project_root)
    graph = RepoGraph()

    for row in payload.get("symbols", []):
        symbol = Symbol(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            file=row["file"],
            line=row["line"],
            end_line=row.get("end_line", 0),
            col=row.get("col", 0),
            visibility=row.get("visibility", "private"),
            docstring=row.get("docstring", ""),
            signature=row.get("signature", ""),
            pagerank=row.get("pagerank", 0.0),
        )
        graph.symbols[symbol.id] = symbol

    for source_id, rows in payload.get("outgoing", {}).items():
        for row in rows:
            edge = Edge(
                source=row["source"],
                target=row["target"],
                weight=row.get("weight", 1.0),
                kind=row.get("kind", "call"),
            )
            graph.outgoing[source_id].append(edge)
            graph.incoming[edge.target].append(edge)

    for file_path, symbol_ids in payload.get("file_symbols", {}).items():
        graph.file_symbols[file_path].extend(symbol_ids)

    for file_path, imports in payload.get("file_imports", {}).items():
        graph.file_imports[file_path].extend(imports)

    for file_path, calls in payload.get("file_calls", {}).items():
        graph.file_calls[file_path] = [tuple(c) for c in calls]

    for file_path, bindings in payload.get("file_import_bindings", {}).items():
        from .. import JSImportBinding
        graph.file_import_bindings[file_path] = [
            JSImportBinding(**b) for b in bindings
        ]

    for file_path, exports in payload.get("file_exports", {}).items():
        from .. import JSExportBinding
        graph.file_exports[file_path] = [
            JSExportBinding(**e) for e in exports
        ]

    stats_row = payload.get("scan_stats", {})
    engine.graph = graph
    engine.scan_stats = ScanStats(
        listed_source_files=stats_row.get("listed_source_files", 0),
        selected_source_files=stats_row.get("selected_source_files", 0),
        processed_files=stats_row.get("processed_files", 0),
        filtered_path_files=stats_row.get("filtered_path_files", 0),
        filtered_large_files=stats_row.get("filtered_large_files", 0),
        truncated_files=stats_row.get("truncated_files", 0),
        failed_files=list(stats_row.get("failed_files", [])),
        scan_duration_ms=stats_row.get("scan_duration_ms", 0),
        timeout_triggered=bool(stats_row.get("timeout_triggered", False)),
        skipped_files=stats_row.get("skipped_files", 0),
    )
    engine.scan_state = payload.get("scan_state", "scanned")
    engine._analyzer = type(engine._analyzer)(engine.graph)
    # 恢复路由数据
    engine.routes = [
        HttpRoute(**r) for r in payload.get("routes", [])
    ]
    return engine if engine.scan_state == "scanned" else None


def _load_session_engine(project_root: str, fingerprint: str) -> RepoMapEngine | None:
    cache_path = get_session_cache_path(project_root)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("project_root") != project_root:
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    return _restore_engine_from_session_payload(payload)


def _save_session_engine(project_root: str, fingerprint: str, engine: RepoMapEngine) -> None:
    if engine.scan_state != "scanned":
        return
    cache_path = get_session_cache_path(project_root)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _engine_to_session_payload(project_root, fingerprint, engine)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_path.parent,
            prefix="session_scan.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            tmp_path = Path(handle.name)
        tmp_path.replace(cache_path)
    except Exception:
        try:
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _select_symbol_match(
    engine: RepoMapEngine,
    symbol: str,
    *,
    file_path: str | None = None,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
) -> tuple[Any | None, str | None, str | None]:
    """3-tier symbol resolution: LSP → tree-sitter same-file → global fuzzy.

    Returns (symbol, error, resolution_tier).
    resolution_tier is "lsp", "treesitter", or "fuzzy".
    """
    matches = engine.query_symbol(symbol)
    resolution_tier = "fuzzy"

    # Tier 1: LSP definition lookup (highest precision)
    if with_lsp and matches:
        from ..lsp import collect_lsp_symbol_evidence
        try:
            best_match = matches[0]
            lsp_result = collect_lsp_symbol_evidence(
                str(engine.project_root), best_match.file,
                best_match.line, symbol, timeout=lsp_timeout,
            )
            if lsp_result.status == "ok" and lsp_result.definitions:
                # LSP confirmed the definition location
                lsp_def = lsp_result.definitions[0]
                for m in matches:
                    if m.file == lsp_def.file and abs(m.line - lsp_def.line) <= 3:
                        return m, None, "lsp"
                # LSP found but at a different location; prefer LSP over tree-sitter
                same_file = [m for m in matches if m.file == lsp_def.file]
                if same_file:
                    return same_file[0], None, "lsp"
        except Exception:
            pass  # LSP failed, fall through to tier 2

    # Tier 2: Exact name match (tree-sitter precision)
    if not matches:
        return None, f"> Symbol `{symbol}` not found", "fuzzy"

    exact_matches = [item for item in matches if item.name == symbol]
    candidates = exact_matches or matches
    if exact_matches:
        resolution_tier = "treesitter"

    # Tier 3: Global fuzzy with file filtering
    if file_path:
        filtered = [item for item in candidates if item.file == file_path]
        if not filtered:
            return None, f"> Symbol `{symbol}` not found in `{file_path}`", "fuzzy"
        candidates = filtered
        resolution_tier = "treesitter"

    if len(candidates) == 1:
        return candidates[0], None, resolution_tier

    # Ambiguous: multiple candidates
    lines = [f"> Symbol `{symbol}` has multiple candidates; use `--file-path` to specify:"]
    for item in candidates[:10]:
        lines.append(f"- `{item.file}:{item.line}` ({item.kind})")
    if len(candidates) > 10:
        lines.append(f"- ... {len(candidates) - 10} more candidates")
    lines.append(f"\nTip: use `--file-path <file>` to specify the target file, e.g.:")
    lines.append(f"  repomap call-chain --symbol {symbol} --file-path {candidates[0].file}")
    return None, "\n".join(lines), resolution_tier


def _group_symbol_matches(results: list[Any], symbol: str) -> tuple[list[Any], list[Any]]:
    exact = [item for item in results if item.name == symbol]
    fuzzy = [item for item in results if item.name != symbol]
    return exact, fuzzy


def _render_selected_call_chain(engine: RepoMapEngine, symbol: Any, depth: int) -> str:
    chain = engine.call_chain(symbol.id, "both", depth)
    lines = [
        f"## Call Chain — `{symbol.name}`\n",
        f"- **Type**: {symbol.kind}",
        f"- **Location**: `{symbol.file}:{symbol.line}`",
        f"- **Importance**: PR={symbol.pagerank * 1000:.1f}",
    ]
    if symbol.signature:
        lines.append(f"- **Signature**: `{symbol.signature}`")
    lines.append("")

    callers = chain["callers"]
    lines.append(f"### Called by（{len(callers)}）\n")
    if callers:
        for caller in callers[:20]:
            lines.append(f"- `{caller.name}` ({caller.kind}) — `{caller.file}:{caller.line}`")
        if len(callers) > 20:
            lines.append(f"- ... {len(callers) - 20} more")
    else:
        lines.append("- (None — entry point)")

    callees = chain["callees"]
    lines.append(f"\n### Calls（{len(callees)}）\n")
    if callees:
        for callee in callees[:20]:
            lines.append(f"- `{callee.name}` ({callee.kind}) — `{callee.file}:{callee.line}`")
        if len(callees) > 20:
            lines.append(f"- ... {len(callees) - 20} more")
    else:
        lines.append("- (None — leaf function)")

    return "\n".join(lines)


def _truncate_output(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[output truncated]"


def run_scan(project: str, max_files: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        hot = engine.hotspots(5)
        entry_points = engine.entry_points()
        lines = [
            f"✅ Scan complete — `{engine.project_root}`\n",
            *engine._scan_summary_lines(),
            f"- Entry points: {', '.join(entry_points) or 'None detected'}",
            "\n**High-Density Files (Top 5)**:",
        ]
        if engine.scan_stats.truncated_files:
            lines.insert(6, f"- max_files truncated: {engine.scan_stats.truncated_files}")
        for item in hot:
            lines.append(f"  - `{item['file']}` — {item['symbol_count']} symbols ({item['risk']} risk)")
        lines.append("\n> Next: run `repomap overview --project <path>` for a full project map.")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] scan failed: {exc}", file=sys.stderr)
        return 1


def _route_payload(route: HttpRoute) -> dict[str, Any]:
    return {
        "method": route.method,
        "path": route.path,
        "handler": route.handler,
        "file": route.file,
        "line": route.line,
        "framework": route.framework,
    }



def _scan_stats_payload(engine: RepoMapEngine) -> dict[str, Any]:
    return {
        "listed_source_files": engine.scan_stats.listed_source_files,
        "selected_source_files": engine.scan_stats.selected_source_files,
        "processed_files": engine.scan_stats.processed_files,
        "filtered_path_files": engine.scan_stats.filtered_path_files,
        "filtered_large_files": engine.scan_stats.filtered_large_files,
        "truncated_files": engine.scan_stats.truncated_files,
        "failed_files": list(engine.scan_stats.failed_files),
        "scan_duration_ms": engine.scan_stats.scan_duration_ms,
        "timeout_triggered": engine.scan_stats.timeout_triggered,
        "symbol_count": len(engine.graph.symbols),
        "edge_count": sum(len(edges) for edges in engine.graph.outgoing.values()),
    }


def run_overview(project: str, max_files: int, max_chars: int, as_json: bool,
                with_heat: bool = False, with_co_change: bool = False,
                granularity: str = "auto") -> int:
    try:
        engine = _scan_engine(project, max_files)

        if as_json:
            payload = {
                "project_root": str(engine.project_root),
                "scan_stats": _scan_stats_payload(engine),
                "entry_points": engine.entry_points(),
                "hotspots": engine.hotspots(DEFAULT_OVERVIEW_JSON_HOTSPOTS),
                "reading_order": engine.suggested_reading_order(DEFAULT_OVERVIEW_JSON_READING_ORDER),
                "modules": engine.module_summary(DEFAULT_OVERVIEW_JSON_MODULES),
                "summary_symbols": engine.summary_symbols(
                    DEFAULT_OVERVIEW_JSON_SUMMARY_FILES,
                    DEFAULT_OVERVIEW_JSON_SYMBOLS_PER_FILE,
                ),
                "supporting_files": engine.supporting_files(DEFAULT_OVERVIEW_JSON_SUPPORTING_FILES),
                "hot_files": list(_get_hot_files(str(engine.project_root))) if with_heat else [],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(engine.render_overview(max_chars, with_heat=with_heat, with_co_change=with_co_change, granularity=granularity))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] overview failed: {exc}", file=sys.stderr)
        return 1


def run_call_chain(
    project: str,
    max_files: int,
    symbol: str,
    file_path: str | None,
    direction: str,
    depth: int,
    max_chars: int,
    as_json: bool,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        selected, error, tier = _select_symbol_match(engine, symbol, file_path=file_path)
        if error:
            print(error, file=sys.stderr)
            return 1
        assert selected is not None
        if as_json:
            chain = engine.call_chain(selected.id, "both", depth)
            payload = {
                "symbol": {
                    "id": selected.id,
                    "name": selected.name,
                    "kind": selected.kind,
                    "file": selected.file,
                    "line": selected.line,
                    "signature": selected.signature,
                    "pagerank": selected.pagerank,
                },
                "direction": direction,
                "depth": depth,
                "callers": [_format_symbol_ref(engine, item.id) for item in chain["callers"]],
                "callees": [_format_symbol_ref(engine, item.id) for item in chain["callees"]],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if direction != "both":
            data = engine.call_chain(selected.id, direction, depth)
            lines = [f"## Call Chain — `{selected.name}`\n"]
            for item in data[direction]:
                lines.append(f"- `{item.name}` ({item.file}:{item.line})")
            print(_truncate_output("\n".join(lines), max_chars))
            return 0
        print(_truncate_output(_render_selected_call_chain(engine, selected, depth), max_chars))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] call-chain failed: {exc}", file=sys.stderr)
        return 1


def _collect_lsp_evidence_for_symbol(engine: RepoMapEngine, symbol: Any, timeout: float) -> dict[str, Any]:
    from ..lsp import collect_lsp_symbol_evidence, run_result_to_dict

    run = collect_lsp_symbol_evidence(
        engine.project_root,
        symbol.file,
        symbol.line,
        symbol.name,
        timeout=timeout,
    )
    return run_result_to_dict(run)


def _format_lsp_evidence(evidence: dict[str, Any]) -> list[str]:
    lines = ["", "### LSP evidence", ""]
    lines.append(f"- Status: {evidence.get('status')}")
    if evidence.get("server"):
        lines.append(f"- Server: {evidence['server']}")
    if evidence.get("reason"):
        lines.append(f"- Reason: {evidence['reason']}")
    definitions = evidence.get("definitions", [])
    references = evidence.get("references", [])
    lines.append(f"- Definitions: {len(definitions)}")
    for item in definitions[:10]:
        lines.append(f"  - `{item['file']}:{item['line']}:{item['col']}`")
    lines.append(f"- References: {len(references)}")
    for item in references[:20]:
        lines.append(f"  - `{item['file']}:{item['line']}:{item['col']}`")
    return lines


def run_query_symbol(
    project: str,
    max_files: int,
    symbol: str,
    file_path: str | None,
    max_chars: int,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        results = engine.query_symbol(symbol)
        if file_path:
            results = [item for item in results if item.file == file_path]
        if not results:
            print(f"> No matches found for `{symbol}`", file=sys.stderr)
            return EXIT_NO_RESULTS
        exact_matches, fuzzy_matches = _group_symbol_matches(results, symbol)

        lines = [f"Found {len(results)} matching results.\n"]
        if file_path:
            lines.append(f"Filtered by file: `{file_path}`\n")
        if len(exact_matches) > 1 and not file_path:
            lines.append(f"{len(exact_matches)} exact candidates; use `--file-path` to narrow.\n")

        if exact_matches:
            lines.append(f"## Exact matches `{symbol}` ({len(exact_matches)})\n")
            for item in exact_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}")
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")

        if fuzzy_matches:
            lines.append(f"\n## Fuzzy matches ({len(fuzzy_matches)})\n")
            for item in fuzzy_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}")
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")

        if len(results) > 10 and (len(exact_matches) > 10 or len(fuzzy_matches) > 10):
            lines.append("\n> Many results; use `--file-path` to narrow.")
        if with_lsp:
            selected = (exact_matches or results)[0]
            lines.extend(_format_lsp_evidence(_collect_lsp_evidence_for_symbol(engine, selected, lsp_timeout)))
        print(_truncate_output("\n".join(lines), max_chars))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] query-symbol failed: {exc}", file=sys.stderr)
        return 1


def run_file_detail(project: str, max_files: int, file_path: str, max_symbols: int, max_chars: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        normalized_file_path = _normalize_project_relative_path(engine.project_root, file_path, must_exist=True)

        # 动态调整 max_symbols：如果用户未指定（使用默认值），根据文件符号数量自动调整
        if max_symbols == DEFAULT_FILE_DETAIL_MAX_SYMBOLS:
            file_symbol_count = len(engine.graph.file_symbols.get(normalized_file_path, []))
            if file_symbol_count > 50:
                max_symbols = min(file_symbol_count, 50)  # 大文件最多显示 50 symbols
            elif file_symbol_count > 20:
                max_symbols = file_symbol_count  # 中等文件显示所有符号

        print(engine.render_file_detail(normalized_file_path, max_symbols=max_symbols, max_chars=max_chars))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] file-detail failed: {exc}", file=sys.stderr)
        return 1


def run_routes(project: str, max_files: int, as_json: bool, with_consumers: bool = False) -> int:
    try:
        engine = _scan_engine(project, max_files)
        if as_json:
            payload = {
                "command": "routes",
                "project": str(engine.project_root),
                "scanStats": _scan_stats_payload(engine),
                "routes": [_route_payload(route) for route in engine.list_routes()],
            }
            if with_consumers:
                from ..consumers import find_route_consumers
                consumers = find_route_consumers(engine, engine.list_routes())
                consumer_json = {}
                for key, clist in consumers.items():
                    consumer_json[key] = [
                        {"file": c.file, "line": c.line, "context": c.context,
                         "confidence": c.confidence, "match_type": c.match_type}
                        for c in clist
                    ]
                payload["consumers"] = consumer_json
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if with_consumers:
            from ..consumers import find_route_consumers
            consumers = find_route_consumers(engine, engine.list_routes())
            print(render_routes_report(engine, consumers))
        else:
            print(render_routes_report(engine))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] routes failed: {exc}", file=sys.stderr)
        return 1


def run_hotspots(project: str, max_files: int, limit: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        hotspots = engine.hotspots(limit)
        risk_mark = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        lines = ["## High-Density Files (by symbol count)\n"]
        for index, item in enumerate(hotspots, 1):
            lines.append(f"{index}. {risk_mark[item['risk']]} `{item['file']}` — **{item['symbol_count']}** symbols")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] hotspots failed: {exc}", file=sys.stderr)
        return 1


def run_cache(project: str, action: str) -> int:
    project_path = _resolve_project(project)
    if action != "save":
        print(f"[{CLI_NAME}] unsupported cache action: {action}", file=sys.stderr)
        return 2
    try:
        symbols, edges = scan_project(project_path)
        cache_path = save_cache(project_path, symbols, edges)
        print(
            "✅ Graph baseline saved for a future comparison\n"
            f"- Path: `{cache_path}`\n"
            f"- Symbols: {len(symbols)}\n"
            f"- Edges: {len(edges)}\n"
            "- Use before the target edits; saving after edits cannot prove those edits are safe."
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] cache save failed: {exc}", file=sys.stderr)
        return 1



def run_diff(project: str, as_json: bool) -> int:
    result = diff_project(_resolve_project(project))
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    lines = ["## Change Detection\n"]
    lines.append(f"**Compare**: {result.get('last_scan', 'unknown')} → {result.get('scan_time', datetime.now().isoformat())}\n")
    lines.append(f"- Added symbols: {result['summary']['added']}")
    lines.append(f"- Removed symbols: {result['summary']['removed']}")
    lines.append(f"- Modified symbols: {result['summary']['modified']}")
    lines.append(f"- Added calls: {result['summary']['edges_added']}")
    lines.append(f"- Removed calls: {result['summary']['edges_removed']}\n")
    if result["added_symbols"]:
        lines.append("**Added symbols** (Top 10):")
        for item in result["added_symbols"][:10]:
            lines.append(f"  - `{item['name']}` ({item['file']}:{item['line']})")
    if result["call_chain_changes"]["new_calls"]:
        lines.append("\n**Added calls** (Top 10):")
        for change in result["call_chain_changes"]["new_calls"][:10]:
            src_name = change["from"].split("::")[-2] if "::" in change["from"] else change["from"]
            tgt_name = change["to"].split("::")[-2] if "::" in change["to"] else change["to"]
            lines.append(f"  - `{src_name}` -[{change['kind']}]-> `{tgt_name}`")
    print("\n".join(lines))
    return 0


def run_query(
    project: str,
    max_files: int,
    query: str,
    max_result_files: int,
    max_result_symbols: int,
    no_tests: bool,
    as_json: bool,
    paths: str | None,
    exclude: str | None,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        analysis = engine.file_analysis()

        # 过滤搜索范围
        candidate_files = list(engine.graph.file_symbols.keys())
        if paths:
            allowed = {_normalize_path_prefix(engine.project_root, p) for p in paths.split(",") if p.strip()}
            candidate_files = [f for f in candidate_files if any(_path_matches_prefix(f, a) for a in allowed)]
        if exclude:
            excluded = {_normalize_path_prefix(engine.project_root, e) for e in exclude.split(",") if e.strip()}
            candidate_files = [f for f in candidate_files if not any(_path_matches_prefix(f, e) for e in excluded)]
        if no_tests:
            candidate_files = [f for f in candidate_files if not is_test_like_file(f)]

        # 计算高频词权重（命中文件过多的关键词降权）
        kw_weights = compute_keyword_weights(query.lower().split(), candidate_files, engine.graph)

        # 主题评分
        matches: list[FileMatch] = []
        for file_path in candidate_files:
            file_data = analysis.get(file_path, {})
            score = topic_score(query, file_path, file_data, engine.graph, keyword_weights=kw_weights)
            if score > 0:
                role = classify_file_role(file_path, engine.graph)
                reasons = _build_match_reasons(query, file_path, engine.graph, engine.list_routes())
                matches.append(FileMatch(path=file_path, role=role, score=score, reasons=reasons))

        # 调用邻居传播：高分文件的调用者/被调用者文件获得传播分数
        matches = _propagate_call_neighbor_scores(matches, candidate_files, engine.graph)

        matches.sort(key=lambda m: (-m.score, m.path))
        top_matches = matches[:max_result_files]

        # 找相关测试
        tests: list[TestMatch] = []
        if not no_tests:
            target_files = [m.path for m in top_matches if not is_test_like_file(m.path)]
            tests = find_related_tests(target_files, engine.graph, analysis, engine.project_root)

        if as_json:
            payload = {
                "command": "query",
                "project": str(engine.project_root),
                "query": query,
                "scanStats": _scan_stats_payload(engine),
                "result": {
                    "filesConsidered": len(candidate_files),
                    "matchedFiles": len(matches),
                    "readingOrder": _build_query_reading_order(top_matches, analysis, max_result_files),
                    "coreFiles": [
                        {"path": m.path, "role": m.role, "score": m.score, "reasons": m.reasons}
                        for m in top_matches if m.score >= 30 and not is_test_like_file(m.path)
                    ],
                    "supportingFiles": [
                        {"path": m.path, "role": m.role, "score": m.score, "reasons": m.reasons}
                        for m in top_matches if m.score < 30
                    ],
                    "tests": [
                        {"testFile": t.test_file, "targetFile": t.target_file,
                         "confidence": t.confidence, "reason": t.reason}
                        for t in tests
                    ],
                    "symbols": _query_symbols_json(engine, top_matches, max_result_symbols),
                },
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        print(render_query_report(engine, query, top_matches, tests, max_result_files, max_result_symbols))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] query failed: {exc}", file=sys.stderr)
        return 1


def _propagate_call_neighbor_scores(
    matches: list[FileMatch],
    candidate_files: list[str],
    graph: RepoGraph,
    decay: float = 0.25,
    min_source_score: float = 20.0,
) -> list[FileMatch]:
    """Propagate scores from high-scoring files to their call-neighbor files.

    File-level one-hop propagation (direct callers/callees).
    Only propagates from files with score >= min_source_score.
    """
    match_by_path = {m.path: m for m in matches}
    candidate_set = set(candidate_files)

    # Build file-level call-neighbor maps from symbol-level call edges
    file_callees: dict[str, set[str]] = defaultdict(set)
    file_callers: dict[str, set[str]] = defaultdict(set)

    for sid, edges in graph.outgoing.items():
        sym = graph.symbols.get(sid)
        if not sym or not sym.file:
            continue
        for edge in edges:
            if edge.kind != "call":
                continue
            target_sym = graph.symbols.get(edge.target)
            if not target_sym or not target_sym.file:
                continue
            if sym.file != target_sym.file:
                file_callees[sym.file].add(target_sym.file)
                file_callers[target_sym.file].add(sym.file)

    # Get the first representative symbol name for a file
    def _first_sym_name(file_path: str) -> str | None:
        for sid in graph.file_symbols.get(file_path, []):
            sym = graph.symbols.get(sid)
            if sym and sym.name:
                return sym.name
        return None

    # Add a reason tag to a FileMatch if not already present
    def _add_tag(fm: FileMatch, tag: str) -> None:
        if tag not in fm.reasons:
            fm.reasons.append(tag)

    new_or_updated: dict[str, FileMatch] = {}
    for m in matches:
        if m.score < min_source_score:
            continue

        sym_name = _first_sym_name(m.path)
        tag = f"call-neighbor hit: {sym_name}" if sym_name else "call-neighbor hit"

        neighbors = file_callers.get(m.path, set()) | file_callees.get(m.path, set())
        for neighbor_file in neighbors:
            if neighbor_file not in candidate_set:
                continue

            propagated = m.score * decay

            if neighbor_file in match_by_path:
                _add_tag(match_by_path[neighbor_file], tag)
                match_by_path[neighbor_file].score += propagated
            elif neighbor_file in new_or_updated:
                new_or_updated[neighbor_file].score += propagated
            else:
                new_or_updated[neighbor_file] = FileMatch(
                    path=neighbor_file,
                    role=classify_file_role(neighbor_file, graph),
                    score=propagated,
                    reasons=[tag],
                )

    result = list(matches)
    for path, fm in new_or_updated.items():
        if path not in match_by_path:
            result.append(fm)
    return result


def _build_match_reasons(query: str, file_path: str, graph: RepoGraph, routes: list | None = None) -> list[str]:
    """Build match reason list with hit type tags."""
    reasons: list[str] = []
    keywords = query.lower().split()
    expanded = expand_keywords(keywords)
    path_lower = file_path.lower()
    file_name = PurePosixPath(file_path).stem.lower()
    tokens = split_identifier(PurePosixPath(file_path).stem)

    for kw, source in expanded:
        tag = "synonym hit" if source else None
        if kw in path_lower:
            label = f"synonym hit: {source} -> {kw}" if source else f"path hit: {kw}"
            reasons.append(label)
        if kw in file_name:
            label = f"synonym hit: {source} -> {kw} (filename)" if source else f"filename hit: {kw}"
            reasons.append(label)
        elif any(kw in t for t in tokens):
            label = f"synonym hit: {source} -> {kw} (token)" if source else f"filename token hit: {kw}"
            reasons.append(label)

    # Symbol name hits
    for sid in graph.file_symbols.get(file_path, []):
        sym = graph.symbols.get(sid)
        if not sym:
            continue
        for kw, source in expanded:
            if kw in sym.name.lower():
                tag = f"synonym hit: {source} -> {kw}" if source else f"symbol hit: {sym.name}"
                if tag not in reasons:
                    reasons.append(tag)
        if len(reasons) >= 5:
            break

    # Route hits
    if routes:
        for r in routes:
            rel_file = file_path
            if hasattr(r, 'file') and (r.file == rel_file or rel_file.endswith(r.file) or r.file.endswith(rel_file)):
                for kw, source in expanded:
                    if kw in r.path.lower() or kw in r.handler.lower():
                        tag = f"route hit: {r.method} {r.path}"
                        if tag not in reasons:
                            reasons.append(tag)
                if len(reasons) >= 6:
                    break

    # Test file marker
    if is_test_like_file(file_path):
        reasons.append("test hit")

    return reasons[:6]


def _query_symbols_json(
    engine: RepoMapEngine,
    matches: list[FileMatch],
    max_symbols: int,
) -> list[dict[str, Any]]:
    """为 JSON 输出提取符号列表。"""
    result: list[dict[str, Any]] = []
    for m in matches:
        if len(result) >= max_symbols:
            break
        for sym in _rank_symbols_for_file(engine, m.path):
            if len(result) >= max_symbols:
                break
            result.append({
                "name": sym["name"],
                "kind": sym["kind"],
                "file": m.path,
                "line": sym["line"],
                "role": classify_file_role(m.path, engine.graph),
            })
    return result


def _impact_key_symbols(engine: RepoMapEngine, target_files: list[str], limit_per_file: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for file_path in target_files:
        symbols = [
            engine.graph.symbols[sid]
            for sid in engine.graph.file_symbols.get(file_path, [])
            if sid in engine.graph.symbols
        ]
        symbols.sort(
            key=lambda symbol: (
                -symbol.pagerank,
                -len(engine.graph.incoming.get(symbol.id, [])),
                symbol.line,
                symbol.name,
            )
        )
        for symbol in symbols[:limit_per_file]:
            result.append({
                "name": symbol.name,
                "kind": symbol.kind,
                "file": symbol.file,
                "line": symbol.line,
                "pagerank": symbol.pagerank,
                "incomingCount": len(engine.graph.incoming.get(symbol.id, [])),
                "outgoingCount": len(engine.graph.outgoing.get(symbol.id, [])),
                "signature": symbol.signature,
            })
    return result


def _impact_read_next(
    target_files: list[str],
    affected_list: list[tuple[str, str, str]],
    tests: list[TestMatch],
    limit: int = 10,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(path: str, reason: str, role: str) -> None:
        if len(result) >= limit or path in seen:
            return
        seen.add(path)
        result.append({"file": path, "reason": reason, "role": role})

    for file_path in target_files:
        add(file_path, "target file", "target")
    for file_path, why, confidence in affected_list:
        if confidence == "high":
            add(file_path, why, "affected")
    for test in tests:
        add(test.test_file, test.reason, "test")
    for file_path, why, _confidence in affected_list:
        add(file_path, why, "affected")
    return result


def _impact_lsp_hint(project_root: str | Path, target_files: list[str]) -> dict[str, Any]:
    try:
        from ..lsp import detect_lsp_server, detection_to_dict, language_for_file
    except Exception as exc:
        return {"available": False, "servers": [], "suggestedCommands": [], "reason": str(exc)}

    servers: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for file_path in target_files:
        language = language_for_file(file_path)
        if not language:
            continue
        detection = detect_lsp_server(project_root, language, file_path)
        key = (detection.language, detection.server_name)
        if key in seen:
            continue
        seen.add(key)
        servers.append(detection_to_dict(detection))
    available = any(server.get("status") == "available" for server in servers)
    suggested: list[str] = []
    if available and target_files:
        files_arg = " ".join(target_files)
        suggested.append(f"repomap diagnostics --project {project_root} --source lsp --files {files_arg}")
        suggested.append(f"repomap refs --project {project_root} --symbol <symbol> --file-path <file> --with-lsp")
    return {"available": available, "servers": servers, "suggestedCommands": suggested}


def run_impact(
    project: str,
    max_files: int,
    target_files: list[str],
    max_affected_files: int,
    as_json: bool,
    with_symbols: bool = False,
    depth: int = 1,
    incremental: bool = False,
) -> int:
    try:
        engine = _scan_engine(project, max_files, incremental=incremental)

        target_files = _normalize_project_relative_paths(engine.project_root, target_files)

        # 收集目标文件符号
        target_symbols: set[str] = set()
        for f in target_files:
            for sid in engine.graph.file_symbols.get(f, []):
                target_symbols.add(sid)

        # 找出引用者有谁（incoming edges）
        affected_files: dict[str, tuple[str, str]] = {}  # file -> (why, confidence)
        for sid in target_symbols:
            for edge in engine.graph.incoming.get(sid, []):
                caller = engine.graph.symbols.get(edge.source)
                if caller and caller.file not in target_files:
                    caller_name = caller.name
                    affected_files[caller.file] = (
                        f"references {_sym_name(engine, sid)}",
                        "high",
                    )

            for edge in engine.graph.outgoing.get(sid, []):
                callee = engine.graph.symbols.get(edge.target)
                if callee and callee.file not in target_files:
                    callee_name = callee.name
                    if callee.file not in affected_files:
                        affected_files[callee.file] = (
                            f"input file calls {callee_name}（via {_sym_name(engine, sid)}）",
                            "medium",
                        )

        # 传递影响展开：用 BFS 从已影响文件的符号出发，找更深层的文件
        if depth > 1 and affected_files:
            processed_files = set(target_files) | set(affected_files)
            frontier: set[str] = set(affected_files)
            for current_depth in range(1, depth):
                next_frontier: set[str] = set()
                for affected_file in frontier:
                    for sid in engine.graph.file_symbols.get(affected_file, []):
                        # 谁调用了这个受影响文件的符号？
                        for edge in engine.graph.incoming.get(sid, []):
                            src_sym = engine.graph.symbols.get(edge.source)
                            if src_sym and src_sym.file not in processed_files:
                                next_frontier.add(src_sym.file)
                                if src_sym.file not in affected_files:
                                    affected_files[src_sym.file] = (
                                        f"transitive impact depth={current_depth + 1}: calls {affected_file} in {src_sym.name}",
                                        "low",
                                    )
                        # 这个受影响文件的符号调用了谁？
                        for edge in engine.graph.outgoing.get(sid, []):
                            tgt_sym = engine.graph.symbols.get(edge.target)
                            if tgt_sym and tgt_sym.file not in processed_files:
                                next_frontier.add(tgt_sym.file)
                                if tgt_sym.file not in affected_files:
                                    affected_files[tgt_sym.file] = (
                                        f"transitive impact depth={current_depth + 1}: called by {affected_file} in {_sym_name(engine, sid)}",
                                        "low",
                                    )
                processed_files |= next_frontier
                frontier = next_frontier
                if not frontier:
                    break

        # 找相关测试
        analysis = engine.file_analysis()
        tests = find_related_tests(target_files, engine.graph, analysis, engine.project_root)

        # 风险评估
        risk_level, risk_notes = _assess_risk(target_files, set(affected_files), engine)

        affected_list = [(f, why, conf) for f, (why, conf) in affected_files.items()]
        # 按影响严重程度排序：受影响文件中符号的外部调用者越多越靠前
        affected_list.sort(key=lambda x: (
            {"high": 3, "medium": 2, "low": 1}.get(x[2], 0),
            -_affected_severity(x[0], engine),
            x[0],
        ), reverse=True)
        affected_list = sorted(affected_list, key=lambda x: (
            -{"high": 3, "medium": 2, "low": 1}.get(x[2], 0),
            -_affected_severity(x[0], engine),
        ))
        affected_list = affected_list[:max_affected_files]
        key_symbols = _impact_key_symbols(engine, target_files) if with_symbols else []
        read_next = _impact_read_next(target_files, affected_list, tests)
        lsp_hint = _impact_lsp_hint(engine.project_root, target_files) if with_symbols else {}

        if as_json:
            payload = {
                "schema_version": "1.0",
                "command": "impact",
                "project": str(engine.project_root),
                "scanStats": _scan_stats_payload(engine),
                "result": {
                    "inputFiles": target_files,
                    "affectedFiles": [
                        {"file": f, "why": why, "confidence": conf}
                        for f, why, conf in affected_list
                    ],
                    "tests": [
                        {"testFile": t.test_file, "targetFile": t.target_file,
                         "confidence": t.confidence, "reason": t.reason}
                        for t in tests
                    ],
                    "riskLevel": risk_level,
                    "riskNotes": risk_notes,
                    "keySymbols": key_symbols,
                    "readNext": read_next,
                    "lspHint": lsp_hint,
                },
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        print(render_impact_report(
            engine,
            target_files,
            affected_list,
            tests,
            risk_level,
            risk_notes,
            key_symbols=key_symbols,
            read_next=read_next,
            lsp_hint=lsp_hint,
        ))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] impact failed: {exc}", file=sys.stderr)
        return 1


def _affected_severity(file_path: str, engine: RepoMapEngine) -> int:
    """计算受影响文件的严重程度：文件中符号被外部调用的总次数。"""
    total = 0
    for sid in engine.graph.file_symbols.get(file_path, []):
        for edge in engine.graph.incoming.get(sid, []):
            if edge.kind == "call":
                src_sym = engine.graph.symbols.get(edge.source)
                if src_sym and src_sym.file != file_path:
                    total += 1
    return total


def _assess_risk(
    target_files: list[str],
    affected_files: set[str],
    engine: RepoMapEngine,
) -> tuple[str, list[str]]:
    """三层风险评估模型。返回 (risk_level, risk_notes)。"""
    risk_notes: list[str] = []
    total_score = 0

    # 第1层：结构风险
    analysis = engine.file_analysis()
    structural_risk = 0
    for f in target_files:
        file_data = analysis.get(f, {})
        nc = file_data.get("neighbor_count", 0)
        if nc >= 10:
            structural_risk += 4
            risk_notes.append(f"`{f}` associated with {nc} files, very high blast radius")
        elif nc >= 5:
            structural_risk += 3
            risk_notes.append(f"`{f}` associated with {nc} files, high blast radius")
        for sid in engine.graph.file_symbols.get(f, []):
            sym = engine.graph.symbols.get(sid)
            if sym and sym.pagerank > 0.01:
                structural_risk += 1
                break
    total_score += structural_risk

    # 第2层：领域关键词风险
    domain_risk = 0
    risk_keywords_high = ["auth", "token", "session", "password", "security",
                          "migration", "database", "schema", "persistence"]
    risk_keywords_medium = ["terminal", "websocket", "pty", "input", "config",
                            "build", "deploy", "ci"]
    all_paths = " ".join(target_files + list(affected_files)).lower()
    for kw in risk_keywords_high:
        if kw in all_paths:
            domain_risk += 3
    for kw in risk_keywords_medium:
        if kw in all_paths:
            domain_risk += 1
    if domain_risk >= 6:
        risk_notes.append(f"touches high-risk domain (auth/security/data persistence)")
    elif domain_risk >= 3:
        risk_notes.append(f"touches medium-risk domain (terminal/config/build)")
    total_score += domain_risk

    # 第3层：变更类型风险
    change_type_risk = 0
    for f in target_files:
        if is_test_like_file(f):
            pass  # 只改测试不改实现，低风险
        elif any(f.endswith(ext) for ext in [".config.ts", ".config.js", "package.json"]):
            change_type_risk += 2
            risk_notes.append(f"`{f}` is a config file change with global impact")
        elif "types" in PurePosixPath(f).parts or f.endswith(".d.ts"):
            change_type_risk += 1
            risk_notes.append(f"`{f}` is a type definition change with wide impact")
    total_score += change_type_risk

    level = "high" if total_score >= 6 else "medium" if total_score >= 3 else "low"
    return level, risk_notes


def _parse_git_status_porcelain_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ")[-1]
        path = path.strip()
        if path:
            paths.append(path)
    return paths


def _collect_changed_files(project_root: str | Path) -> tuple[list[str], str | None]:
    project_path = Path(project_root).resolve()
    git_root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=project_path, capture_output=True, text=True, timeout=10,
    )
    git_root = git_root_result.stdout.strip()
    if git_root_result.returncode != 0 or not git_root:
        return [], f"git root failed: {git_root_result.stderr.strip() or 'not a git repository'}"

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_path, capture_output=True, text=True, timeout=10,
    )
    if status.returncode != 0:
        return [], f"git status failed: {status.stderr.strip() or status.stdout.strip()}"

    changed_files: list[str] = []
    for git_relative_path in _parse_git_status_porcelain_paths(status.stdout):
        abs_path = Path(git_root, git_relative_path).resolve()
        try:
            changed_files.append(abs_path.relative_to(project_path).as_posix())
        except ValueError:
            pass
    return changed_files, None


def _detect_contract_risks(engine: RepoMapEngine, changed_files: list[str]) -> list[dict[str, str]]:
    """Detect contract-level risks from changed files: route changes, signature changes, test gaps."""
    warnings: list[dict[str, str]] = []
    routes = engine.list_routes()
    changed_set = set(changed_files)

    # Route/API risks
    for route in routes:
        if route.file in changed_set:
            warnings.append({
                "level": "MED",
                "message": f"Route `{route.method} {route.path}` (handler in `{route.file}`) changed; review consumers and related tests.",
            })

    # Symbol/public surface risks: check exported/public symbols in changed files
    for file_path in changed_files:
        for sid in engine.graph.file_symbols.get(file_path, []):
            sym = engine.graph.symbols.get(sid)
            if not sym:
                continue
            # Count cross-file incoming edges only (import + call references from other files)
            cross_file_refs = [
                e for e in engine.graph.incoming.get(sid, [])
                if engine.graph.symbols.get(e.source) and engine.graph.symbols[e.source].file != sym.file
            ]
            ref_count = len(cross_file_refs)
            if sym.visibility in ("exported", "public") and ref_count >= 3:
                warnings.append({
                    "level": "MED",
                    "message": f"Exported symbol `{sym.name}` in `{sym.file}` has {ref_count} cross-file references.",
                })
            elif ref_count >= 10:
                warnings.append({
                    "level": "MED",
                    "message": f"Heavily referenced symbol `{sym.name}` `({sym.kind})` in `{sym.file}` changed; {ref_count} cross-file references.",
                })

    # Enum/type risks
    for file_path in changed_files:
        for sid in engine.graph.file_symbols.get(file_path, []):
            sym = engine.graph.symbols.get(sid)
            if sym and sym.kind in ("enum", "type", "struct", "class"):
                cross_file_refs = [
                    e for e in engine.graph.incoming.get(sid, [])
                    if engine.graph.symbols.get(e.source) and engine.graph.symbols[e.source].file != sym.file
                ]
                if cross_file_refs:
                    warnings.append({
                        "level": "MED",
                        "message": f"Type `{sym.name}` `({sym.kind})` in `{sym.file}` changed; {len(cross_file_refs)} cross-file references.",
                    })

    # Test/implementation mismatch
    test_files = [f for f in changed_files if is_test_like_file(f)]
    impl_files = [f for f in changed_files if not is_test_like_file(f) and not f.endswith(('.md',)) and 'dist/' not in f and 'docs/' not in f]
    if test_files and not impl_files:
        warnings.append({
            "level": "LOW",
            "message": f"Only test files changed ({len(test_files)} file(s)); verify tests are intentional.",
        })
    if impl_files and not test_files:
        warnings.append({
            "level": "MED",
            "message": f"Implementation file(s) changed ({len(impl_files)} file(s)) without related tests.",
        })

    # Config/runtime risks
    config_patterns = [".env", "config", "Dockerfile", "Makefile", "migration", "schema"]
    config_files = [f for f in changed_files if any(p in f.lower() for p in config_patterns) and not f.endswith('.md')]
    if config_files:
        warnings.append({
            "level": "MED",
            "message": f"Config/runtime files changed: {', '.join(f'`{f}`' for f in config_files[:3])}.",
        })

    return warnings


def _diff_risk_evidence(engine: RepoMapEngine, changed_files: list[str]) -> dict[str, Any]:
    analysis = engine.file_analysis()

    target_symbols: set[str] = set()
    for file_path in changed_files:
        for symbol_id in engine.graph.file_symbols.get(file_path, []):
            target_symbols.add(symbol_id)

    affected_files_dict: dict[str, tuple[str, str]] = {}
    for symbol_id in target_symbols:
        for edge in engine.graph.incoming.get(symbol_id, []):
            caller = engine.graph.symbols.get(edge.source)
            if caller and caller.file not in changed_files:
                affected_files_dict[caller.file] = (
                    f"references changed symbol {_sym_name(engine, symbol_id)}",
                    "high",
                )

    affected_list = [(file_path, why, confidence) for file_path, (why, confidence) in affected_files_dict.items()]
    affected_list.sort(key=lambda item: (item[2], item[0]))

    source_files = [file_path for file_path in changed_files if not is_test_like_file(file_path)]
    tests = find_related_tests(source_files, engine.graph, analysis, engine.project_root)
    risk_level, risk_reasons = _assess_risk(source_files, set(file_path for file_path, _, _ in affected_list), engine)

    missing_checks: list[str] = []
    all_exts = set(Path(file_path).suffix for file_path in changed_files)
    if ".ts" in all_exts or ".tsx" in all_exts:
        if not any(test.test_file.endswith((".ts", ".tsx")) for test in tests):
            missing_checks.append("No frontend test file changes detected; consider adding frontend tests")
    if ".py" in all_exts:
        if not any(test.test_file.endswith(".py") for test in tests):
            missing_checks.append("No Python test file changes detected; consider adding backend tests")

    return {
        "affectedList": affected_list,
        "tests": tests,
        "riskLevel": risk_level,
        "riskReasons": risk_reasons,
        "missingChecks": missing_checks,
    }




def _run_check_payload(
    project_root: str,
    types: list[str] | None,
    max_issues: int,
    modified_files: list[str] | None,
    resolve_symbols: bool,
    with_lsp: bool,
    lsp_timeout: float,
    lsp_max_files: int,
) -> dict[str, Any]:
    symbols_map = None
    if resolve_symbols:
        engine = _scan_engine(project_root, 8000)
        symbols_map = engine.graph.symbols
    checker = RepoMapChecker(project_root, max_issues)
    return checker.check(
        types=types,
        resolve_symbols=resolve_symbols and symbols_map is not None,
        symbols_map=symbols_map,
        modified_files=modified_files,
        with_lsp=with_lsp,
        lsp_timeout=lsp_timeout,
        lsp_max_files=lsp_max_files,
    )


def _verify_lsp_payload(
    project_root: str,
    changed_files: list[str],
    enabled: bool,
    timeout: float,
    max_files: int,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "status": "skipped", "runs": [], "summary": {}}
    if not changed_files:
        return {"enabled": True, "status": "skipped", "runs": [], "summary": {}, "reason": "no changed files"}
    try:
        from ..lsp import collect_lsp_diagnostics, run_result_to_dict

        runs = collect_lsp_diagnostics(project_root, changed_files, timeout=timeout, max_files=max_files)
        run_dicts = [run_result_to_dict(run) for run in runs]
        total_errors = sum(1 for run in runs for item in run.diagnostics if item.severity == "error")
        total_warnings = sum(1 for run in runs for item in run.diagnostics if item.severity != "error")
        failed_runs = sum(1 for run in runs if run.status in {"failed", "timeout"})
        skipped_runs = sum(1 for run in runs if run.status == "skipped")
        status = "failed" if total_errors or failed_runs else "passed"
        if skipped_runs and skipped_runs == len(runs):
            status = "skipped"
        return {
            "enabled": True,
            "status": status,
            "runs": run_dicts,
            "summary": {
                "totalErrors": total_errors,
                "totalWarnings": total_warnings,
                "failedRuns": failed_runs,
                "skippedRuns": skipped_runs,
            },
        }
    except Exception as exc:
        return {"enabled": True, "status": "failed", "runs": [], "summary": {}, "reason": str(exc)}


def _verify_graph_diff_payload(project_root: str, enabled: bool, incoming_map: dict | None = None) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "status": "skipped", "summary": {}, "breakingChanges": []}
    result = diff_project(project_root)
    if "error" in result:
        return {"enabled": True, "status": "skipped", "summary": {}, "breakingChanges": [], "reason": result["error"]}
    # 如果提供了 incoming_map，二次调用带调用者分析的 compare
    if incoming_map is not None:
        from ..toolkit import load_cache
        from .. import compare_graph_snapshots
        cache = load_cache(project_root)
        if cache:
            current_symbols, current_edges = scan_project(project_root, max_files=5000)
            enriched = compare_graph_snapshots(
                current_symbols=current_symbols, current_edges=current_edges,
                previous_symbols=cache.symbols, previous_edges=cache.edges,
                incoming_map=incoming_map,
            )
            breaking = [
                ms for ms in enriched.get("modified_symbols", [])
                if ms.get("risk") in ("HIGH", "MEDIUM") and ms.get("signature_changed")
            ]
            result["breakingChanges"] = breaking[:20]
    if "breakingChanges" not in result:
        result["breakingChanges"] = []
    summary = result.get("summary", {})
    changed = any(summary.get(key, 0) for key in ("added", "removed", "modified", "edges_added", "edges_removed"))
    result["status"] = "changed" if changed else "unchanged"
    return result


def _overall_verify_status(
    changed_files: list[str],
    risk_level: str,
    missing_checks: list[str],
    check_payload: dict[str, Any],
    lsp_payload: dict[str, Any],
    graph_diff_payload: dict[str, Any],
) -> str:
    if check_payload.get("status") == "failed" or lsp_payload.get("status") == "failed":
        return "failed"
    if not changed_files:
        return "warning"
    if risk_level == "high" or missing_checks or graph_diff_payload.get("status") == "changed":
        return "warning"
    if check_payload.get("status") in {"warning", "unknown"}:
        return "warning"
    return "passed"


def run_verify(
    project: str,
    as_json: bool,
    types: list[str] | None,
    max_issues: int,
    resolve_symbols: bool,
    with_lsp: bool,
    lsp_timeout: float,
    lsp_max_files: int,
    with_diff: bool,
    quick: bool = False,
    incremental: bool = False,
) -> int:
    try:
        project_root = _resolve_project(project)
        changed_files, error = _collect_changed_files(project_root)
        if error:
            print(f"[{CLI_NAME}] verify failed: {error}", file=sys.stderr)
            return 1

        engine = _scan_engine(project_root, 8000, incremental=incremental)
        evidence = _diff_risk_evidence(engine, changed_files)
        contract_risks = _detect_contract_risks(engine, changed_files)

        if quick:
            check_payload = {"status": "skipped", "summary": {}, "runs": [], "reason": "verify --quick"}
            lsp_payload = {"enabled": False, "status": "skipped", "runs": [], "summary": {}, "reason": "verify --quick"}
        else:
            check_payload = _run_check_payload(
                project_root=project_root,
                types=types,
                max_issues=max_issues,
                modified_files=changed_files,
                resolve_symbols=resolve_symbols,
                with_lsp=False,
                lsp_timeout=lsp_timeout,
                lsp_max_files=lsp_max_files,
            )
            lsp_payload = _verify_lsp_payload(project_root, changed_files, with_lsp, lsp_timeout, lsp_max_files)

        graph_diff_payload = _verify_graph_diff_payload(
            project_root, with_diff,
            incoming_map=engine.graph.incoming if with_diff else None,
        )
        status = _overall_verify_status(
            changed_files,
            evidence["riskLevel"],
            evidence["missingChecks"],
            check_payload,
            lsp_payload,
            graph_diff_payload,
        )
        untested = find_untested_symbols(engine.graph) if not quick else []

        payload = {
            "schema_version": "1.0",
            "command": "verify",
            "project": str(engine.project_root),
            "scanStats": _scan_stats_payload(engine),
            "result": {
                "status": status,
                "changedFiles": changed_files,
                "risk": {
                    "level": evidence["riskLevel"],
                    "reasons": evidence["riskReasons"],
                    "missingChecks": evidence["missingChecks"],
                },
                "affectedFiles": [
                    {"file": file_path, "why": why, "confidence": confidence}
                    for file_path, why, confidence in evidence["affectedList"]
                ],
                "tests": [
                    {"testFile": test.test_file, "targetFile": test.target_file,
                     "confidence": test.confidence, "reason": test.reason}
                    for test in evidence["tests"]
                ],
                "untestedSymbols": untested,
                "check": {
                    "status": check_payload.get("status", "unknown"),
                    "summary": check_payload.get("summary", {}),
                    "incremental": check_payload.get("incremental", {}),
                    "runs": check_payload.get("runs", []),
                    "errorsByFile": check_payload.get("errors_by_file", {}),
                },
                "lsp": lsp_payload,
                "graphDiff": graph_diff_payload,
                "contractRisks": contract_risks,
            },
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_verify_report(payload))

        # 如果没有 git 变更，给出下一步建议
        if not changed_files:
            print("\n> No git changes detected.", file=sys.stderr)
            if quick:
                print("> verify --quick mode only analyzes git changes; no changes found, risk assessment unavailable.", file=sys.stderr)
                print("> Suggestion: make code changes first, then run `repomap verify` for full verification.", file=sys.stderr)
            else:
                print("> Suggestion: use `repomap overview` for project structure or `repomap check` for compilation checks.", file=sys.stderr)

        return 1 if status == "failed" else 0
    except Exception as exc:
        print(f"[{CLI_NAME}] verify failed: {exc}", file=sys.stderr)
        return 1


def run_git_history(project: str, max_files: int, symbol: str, file_path: str | None) -> int:
    try:
        engine = _scan_engine(project, max_files)
        selected, error, tier = _select_symbol_match(engine, symbol, file_path=file_path)
        if error:
            print(error, file=sys.stderr)
            return 1
        assert selected is not None
        target = selected
        result = subprocess.run(
            ["git", "blame", "-L", f"{target.line},{target.line}", "-p", target.file],
            cwd=engine.project_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"📍 Symbol: `{target.name}`\n📁 Location: `{target.file}:{target.line}`\n\n❌ Git info unavailable (may not be a git repository)",
                file=sys.stderr,
            )
            return 1
        commit_hash = result.stdout.split()[0] if result.stdout else "unknown"
        file_commits = subprocess.run(
            ["git", "log", "--follow", "-10", "--format=%H|%an|%ad|%s", "--", target.file],
            cwd=engine.project_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        lines = [f"## Git History — `{target.name}`\n"]
        lines.append(f"📍 Location: `{target.file}:{target.line}`")
        lines.append(f"🔖 Current commit: `{commit_hash[:8]}`\n")
        if file_commits.returncode == 0 and file_commits.stdout:
            lines.append("**Recent commits**:")
            for row in file_commits.stdout.strip().split("\n")[:5]:
                parts = row.split("|", 3)
                if len(parts) >= 4:
                    lines.append(f"  - `[{parts[0][:8]}]` {parts[2][:10]} by {parts[1]}: {parts[3][:50]}")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] git-history failed: {exc}", file=sys.stderr)
        return 1


def run_refs(
    project: str,
    max_files: int,
    symbol: str | None,
    file_path: str | None,
    as_json: bool,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        symbol_ids = set(engine.graph.symbols.keys())
        calls_out: dict[str, set[str]] = {symbol_id: set() for symbol_id in symbol_ids}
        calls_in: dict[str, set[str]] = {symbol_id: set() for symbol_id in symbol_ids}
        for source_id, edge_list in engine.graph.outgoing.items():
            for edge in edge_list:
                if edge.kind != "call":
                    continue
                calls_out.setdefault(source_id, set()).add(edge.target)
                calls_in.setdefault(edge.target, set()).add(source_id)

        if symbol:
            selected, error, tier = _select_symbol_match(engine, symbol, file_path=file_path)
            if error:
                print(error, file=sys.stderr)
                return 1
            assert selected is not None
            sid = selected.id
            target = engine.graph.symbols[sid]
            payload = {
                "symbol": target.name,
                "id": sid,
                "called_by": [_format_symbol_ref(engine, item) for item in sorted(calls_in[sid])[:20]],
                "calls": [_format_symbol_ref(engine, item) for item in sorted(calls_out[sid])[:20]],
                "ref_count": len(calls_in[sid]),
                "is_entry": len(calls_in[sid]) == 0,
                "is_leaf": len(calls_out[sid]) == 0,
            }
            if with_lsp:
                payload["lsp"] = _collect_lsp_evidence_for_symbol(engine, target, lsp_timeout)
            if as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                lines = [f"## Reference Analysis — `{target.name}`\n"]
                lines.append(f"- Referenced by:  {payload['ref_count']}")
                lines.append(f"- Calls: {len(payload['calls'])}")
                lines.append(f"- Entry point:  {'Yes' if payload['is_entry'] else 'No'}")
                lines.append(f"- Leaf function:  {'Yes' if payload['is_leaf'] else 'No'}\n")
                if payload["called_by"]:
                    lines.append("**Called by** (Top 10):")
                    for row in payload["called_by"][:10]:
                        lines.append(f"  - `{row['name']}` ({row['file']}:{row['line']})")
                if payload["calls"]:
                    lines.append("\n**Calls** (Top 10):")
                    for row in payload["calls"][:10]:
                        lines.append(f"  - `{row['name']}` ({row['file']}:{row['line']})")
                if with_lsp:
                    lines.extend(_format_lsp_evidence(payload["lsp"]))
                print("\n".join(lines))
            return 0

        entries = [sid for sid in symbol_ids if len(calls_in[sid]) == 0]
        orphans = [sid for sid in symbol_ids if len(calls_in[sid]) == 0 and len(calls_out[sid]) == 0]
        ref_counts = sorted(((sid, len(calls_in[sid])) for sid in symbol_ids), key=lambda item: item[1], reverse=True)
        payload = {
            "total_symbols": len(symbol_ids),
            "entry_points": [_format_symbol_ref(engine, sid) for sid in entries],
            "orphaned_symbols": [_format_symbol_ref(engine, sid) for sid in orphans],
            "most_referenced": [
                {**_format_symbol_ref(engine, sid), "ref_count": count}
                for sid, count in ref_counts[:20]
            ],
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        lines = ["## Global Reference Analysis\n"]
        lines.append(f"- Total symbols:  {payload['total_symbols']}")
        lines.append(f"- Entry point:  {len(payload['entry_points'])}")
        lines.append(f"- Orphaned symbols:  {len(payload['orphaned_symbols'])}\n")
        lines.append("**Most referenced** (Top 10):")
        for row in payload["most_referenced"][:10]:
            lines.append(f"  - `{row['name']}`: {row['ref_count']}  references ({row['file']})")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] refs failed: {exc}", file=sys.stderr)
        return 1


# Kinds that are always structural noise, never dead code.
_ORPHAN_EXCLUDED_KINDS: set[str] = {
    "element",        # HTML tags in JSX/HTML files
    "json_key",       # JSON object keys in config files
    "module",         # mod declarations, import wrappers
    "handler",        # web route handlers (framework-dispatched)
}

# File extensions that are pure config — skip orphan detection entirely.
_ORPHAN_EXCLUDED_EXTENSIONS: set[str] = {
    ".json", ".toml", ".yaml", ".yml", ".html", ".css", ".scss", ".less",
}

# Test-related path markers.
_TEST_PATH_MARKERS: tuple[str, ...] = ("test", "spec", "e2e", "__test__", "__tests__")

# Base confidence by symbol kind (0-100). Higher = more likely truly dead.
_ORPHAN_KIND_BASE: dict[str, int] = {
    "function": 60,
    "method": 60,
    "struct": 40,
    "enum": 40,
    "class": 40,
    "type": 40,
    "interface": 35,
    "anonymous_function": 30,
    "variable": 30,
    "const": 30,
    "impl": 15,
    "trait": 35,
}


def _orphan_confidence(symbol: Symbol, orphan_names: set[str]) -> int:
    """Compute a confidence score (0-100) that a symbol is truly dead code."""
    score = _ORPHAN_KIND_BASE.get(symbol.kind, 30)

    # File-level signals
    file_lower = symbol.file.lower()
    for marker in _TEST_PATH_MARKERS:
        if marker in file_lower:
            score -= 20
            break

    # Extension-based filtering (should already be excluded, defensive)
    if any(file_lower.endswith(ext) for ext in _ORPHAN_EXCLUDED_EXTENSIONS):
        score -= 50

    # Name-based signals for test helpers
    name_lower = symbol.name.lower()
    if any(name_lower.startswith(prefix) for prefix in ("test_", "it_", "should_", "test")):
        score -= 30

    # Visibility signal: private symbols are more likely truly dead
    if symbol.visibility == "private":
        score += 10

    # Struct/impl pairing heuristics
    if symbol.kind == "impl":
        # impl block whose struct also appears as orphan → the pair might all be dead
        if symbol.name in orphan_names:
            score += 25
    elif symbol.kind in ("struct", "enum", "class", "type"):
        # Struct whose impl also appears → more likely truly dead (entire unit unused)
        if symbol.name in orphan_names:
            score += 25

    return max(0, min(100, score))


def _orphan_note(symbol: Symbol) -> str:
    """Generate a brief reason string for the confidence score."""
    reasons: list[str] = []
    file_lower = symbol.file.lower()
    for marker in _TEST_PATH_MARKERS:
        if marker in file_lower:
            reasons.append("test file")
            break
    name_lower = symbol.name.lower()
    if any(name_lower.startswith(prefix) for prefix in ("test_", "it_", "should_")):
        reasons.append("test helper")
    if symbol.kind == "impl":
        reasons.append("impl block (may be macro-driven)")
    if symbol.kind in ("struct", "enum", "class"):
        reasons.append("type definition (may use reflection/macros)")
    if not reasons:
        reasons.append("no callers or callees")
    return "; ".join(reasons)


def run_orphan(project: str, max_files: int, as_json: bool = False, limit: int = 20, min_confidence: int = 0) -> int:
    try:
        engine = _scan_engine(project, max_files)
        symbol_ids = set(engine.graph.symbols.keys())
        calls_in: dict[str, set[str]] = {symbol_id: set() for symbol_id in symbol_ids}
        calls_out: dict[str, set[str]] = {symbol_id: set() for symbol_id in symbol_ids}
        for source_id, edge_list in engine.graph.outgoing.items():
            for edge in edge_list:
                if edge.kind != "call":
                    continue
                calls_out.setdefault(source_id, set()).add(edge.target)
                calls_in.setdefault(edge.target, set()).add(source_id)

        candidates: list[Symbol] = []
        filtered_structural_count = 0
        for sid in symbol_ids:
            if len(calls_in[sid]) == 0 and len(calls_out[sid]) == 0:
                symbol = engine.graph.symbols[sid]
                if symbol.name in {"main", "__main__"}:
                    continue
                if symbol.visibility == "exported":
                    continue
                if symbol.kind in _ORPHAN_EXCLUDED_KINDS:
                    filtered_structural_count += 1
                    continue
                if any(symbol.file.lower().endswith(ext) for ext in _ORPHAN_EXCLUDED_EXTENSIONS):
                    filtered_structural_count += 1
                    continue
                candidates.append(symbol)

        # Build orphan name set for struct/impl pairing heuristic
        orphan_names: set[str] = {s.name for s in candidates}

        # Compute confidence for each candidate
        scored: list[dict] = []
        for symbol in candidates:
            conf = _orphan_confidence(symbol, orphan_names)
            scored.append({
                "symbol": symbol,
                "confidence": conf,
                "note": _orphan_note(symbol),
            })

        scored.sort(key=lambda x: (-x["confidence"], x["symbol"].file, x["symbol"].line, x["symbol"].name))

        # Filter by min_confidence
        if min_confidence > 0:
            scored = [s for s in scored if s["confidence"] >= min_confidence]

        # Tier classification
        high = [s for s in scored if s["confidence"] >= 70]
        medium = [s for s in scored if 40 <= s["confidence"] < 70]
        low = [s for s in scored if s["confidence"] < 40]

        if as_json:
            def _to_dict(item):
                sym = item["symbol"]
                return {
                    "name": sym.name,
                    "kind": sym.kind,
                    "file": sym.file,
                    "line": sym.line,
                    "confidence": item["confidence"],
                    "note": item["note"],
                    "visibility": sym.visibility,
                }

            payload = {
                "project_root": str(engine.project_root),
                "total_candidates": len(candidates),
                "filtered_structural": filtered_structural_count,
                "high_confidence": [_to_dict(s) for s in high],
                "medium_confidence": [_to_dict(s) for s in medium],
                "low_confidence": [_to_dict(s) for s in low],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        # Text output
        lines = ["## Dead Code Analysis\n"]
        lines.append(f"Total {len(candidates)} candidates ({filtered_structural_count} structural elements filtered)")
        if min_confidence > 0:
            lines.append(f"Confidence threshold: {min_confidence} (low-confidence items filtered)")
        lines.append("")

        def _module_for_file(file_path: str) -> str:
            parts = [p for p in PurePosixPath(file_path).parts if p not in ("", ".")]
            if not parts:
                return "(root)"
            if len(parts) == 1:
                return "(root)"
            if parts[0] in {"src", "app", "apps", "packages", "services", "modules", "libs", "lib", "crates"}:
                return "/".join(parts[:2]) if len(parts) > 1 else parts[0]
            return parts[0]

        def _render_tier(title: str, emoji: str, items: list[dict], max_items: int):
            if not items:
                return []
            tier_lines = [f"### {emoji} {title} — {len(items)}"]
            # 按模块分组
            by_module: dict[str, list[dict]] = {}
            for item in items:
                mod = _module_for_file(item["symbol"].file)
                by_module.setdefault(mod, []).append(item)
            tier_lines.append("")
            for mod in sorted(by_module, key=lambda m: -len(by_module[m])):
                mod_items = by_module[mod][:max(3, max_items // max(len(by_module), 1))]
                tier_lines.append(f"**`{mod}/`** ({len(by_module[mod])})")
                for item in mod_items:
                    sym = item["symbol"]
                    tier_lines.append(f"- `{sym.name}` ({sym.kind}) `{sym.file}:{sym.line}` — {item['confidence']}% | {item['note']}")
                if len(by_module[mod]) > len(mod_items):
                    tier_lines.append(f"  ... {len(by_module[mod]) - len(mod_items)} more")
            tier_lines.append("")
            return tier_lines

        lines.extend(_render_tier("HIGH (review recommended)", "🔴", high, limit))
        lines.extend(_render_tier("MEDIUM (verify needed)", "🟡", medium, limit))
        lines.extend(_render_tier("LOW (likely active)", "🟢", low, limit))

        # 如果过滤后无结果，给出建议
        if not high and not medium and not low:
            if min_confidence > 0:
                lines.append(f"\n> Using `--min-confidence {min_confidence}` filter returned no results.")
                lines.append(f"> Try a lower threshold, e.g.: `--min-confidence {max(0, min_confidence - 20)}`")
            else:
                lines.append("\n> No dead code candidates found.")
                lines.append("> This may indicate good code quality, or analysis parameters need adjustment.")
        else:
            if low:
                lines.append("> Using `--min-confidence 40` filter low-confidence items.")
            lines.append("> Do not delete solely based on this output. Verify with `refs` and business review. Use `--json` for structured output.")
            lines.append("")
            lines.append("## Pre-deletion checklist\n")
            lines.append("1. Verify each candidate with `refs --project <project> --symbol <name>` or `query-symbol` before deletion.")
            lines.append("2. Check for dynamic references: string-based calls, reflection, macro expansions, test fixtures, config-driven dispatch.")
            lines.append("3. Check project-specific rules about code ownership, generated code, or feature flags.")
            lines.append("4. Run the full test suite after deletion.")
            lines.append("5. Never delete solely from `orphan` output; treat it as a starting point for investigation.")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] orphan failed: {exc}", file=sys.stderr)
        return 1


def _sym_name(engine: RepoMapEngine, sid: str) -> str:
    sym = engine.graph.symbols.get(sid)
    return sym.name if sym else "?"


def _format_symbol_ref(engine: RepoMapEngine, sid: str) -> dict[str, Any]:
    symbol = engine.graph.symbols[sid]
    return {"name": symbol.name, "file": symbol.file, "line": symbol.line}


def run_lsp_doctor(project: str, as_json: bool = False) -> int:
    try:
        project_root = _resolve_project(project)
        from ..lsp import detect_lsp_servers, detection_to_dict

        detections = detect_lsp_servers(project_root)
        payload = {
            "command": "lsp doctor",
            "project": project_root,
            "lspClient": "available",
            "bundledServers": [],
            "servers": [detection_to_dict(item) for item in detections],
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        lines = ["## LSP Doctor\n"]
        lines.append(f"Project: `{project_root}`")
        lines.append("LSP client: available")
        lines.append("Bundled LSP servers: none")
        if not detections:
            lines.append("\nNo supported source files detected.")
        else:
            lines.append("\n| Language | Server | Status | Source | Workspace |")
            lines.append("|---|---|---|---|---|")
            for item in detections:
                status = "available" if item.status == "available" else f"missing ({item.reason or 'not found'})"
                lines.append(
                    f"| {item.language} | {item.server_name or '-'} | {status} | {item.source or '-'} | `{item.workspace_root or project_root}` |"
                )
        lines.append("\n> repomap checks project-local executables, PATH, and trusted user tool bins such as npm/pnpm/yarn/bun/pipx/uv/mason/cargo/go directories; it does not install or bundle servers.")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] lsp doctor failed: {exc}", file=sys.stderr)
        return 1


def run_diagnostics(
    project: str,
    source: str,
    files: list[str],
    as_json: bool,
    lsp_timeout: float,
    lsp_max_files: int,
) -> int:
    try:
        project_root = _resolve_project(project)
        normalized_files = _normalize_project_relative_paths(project_root, files, must_exist=True)
        if source != "lsp":
            print(f"[{CLI_NAME}] unsupported diagnostics source: {source}", file=sys.stderr)
            return 2
        from ..lsp import collect_lsp_diagnostics, run_result_to_dict

        runs = collect_lsp_diagnostics(project_root, normalized_files, timeout=lsp_timeout, max_files=lsp_max_files)
        payload = {
            "command": "diagnostics",
            "project": project_root,
            "source": source,
            "files": normalized_files,
            "runs": [run_result_to_dict(run) for run in runs],
        }
        total_errors = sum(1 for run in runs for item in run.diagnostics if item.severity == "error")
        total_warnings = sum(1 for run in runs for item in run.diagnostics if item.severity != "error")
        payload["summary"] = {
            "totalErrors": total_errors,
            "totalWarnings": total_warnings,
            "failedRuns": sum(1 for run in runs if run.status in {"failed", "timeout"}),
            "skippedRuns": sum(1 for run in runs if run.status == "skipped"),
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_lsp_diagnostics_report(payload))
        return 1 if total_errors or payload["summary"]["failedRuns"] else 0
    except ValueError as exc:
        print(f"[{CLI_NAME}] diagnostics failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[{CLI_NAME}] diagnostics failed: {exc}", file=sys.stderr)
        return 1


def _format_lsp_diagnostics_report(payload: dict[str, Any]) -> str:
    lines = ["## LSP Diagnostics\n"]
    lines.append(f"Project: `{payload['project']}`")
    lines.append(f"Files: {len(payload.get('files', []))}")
    summary = payload.get("summary", {})
    lines.append(f"Errors: **{summary.get('totalErrors', 0)}** | Warnings: **{summary.get('totalWarnings', 0)}**")
    lines.append("")
    for run in payload.get("runs", []):
        status = run.get("status")
        lines.append(f"### {run.get('language')} / {run.get('server')} — {status}")
        if run.get("reason"):
            lines.append(f"- Reason: {run['reason']}")
        if run.get("workspaceRoot"):
            lines.append(f"- Workspace: `{run['workspaceRoot']}`")
        diagnostics = run.get("diagnostics", [])
        if diagnostics:
            for item in diagnostics[:20]:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(item.get("severity"), "ℹ️")
                lines.append(f"  {icon} `{item['file']}:{item['line']}:{item['col']}` [{item.get('code', '')}] {item.get('message', '')[:120]}")
        else:
            lines.append("- No diagnostics returned.")
        lines.append("")
    return "\n".join(lines)


def run_check(
    project: str,
    types: list[str] | None,
    max_issues: int,
    since_commit: str | None,
    modified_files: list[str] | None,
    resolve_symbols: bool,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
    lsp_max_files: int = 20,
) -> int:
    try:
        project_root = _resolve_project(project)
        normalized_modified_files = None
        if modified_files:
            try:
                normalized_modified_files = _normalize_project_relative_paths(project_root, modified_files, must_exist=False)
            except ValueError as exc:
                print(f"[{CLI_NAME}] check failed: unsafe modified file: {exc}", file=sys.stderr)
                return 1
        symbols_map = None
        if resolve_symbols:
            engine = _scan_engine(project_root, 8000)
            symbols_map = engine.graph.symbols

        checker = RepoMapChecker(project_root, max_issues)
        result = checker.check(
            types=types,
            resolve_symbols=resolve_symbols and symbols_map is not None,
            symbols_map=symbols_map,
            since_commit=since_commit,
            modified_files=normalized_modified_files,
            with_lsp=with_lsp,
            lsp_timeout=lsp_timeout,
            lsp_max_files=lsp_max_files,
        )
        print(_format_check_report(result, max_issues))
        return 0 if result.get("status") in {"passed", "warning", "unknown"} else 1
    except Exception as exc:
        print(f"[{CLI_NAME}] check failed: {exc}", file=sys.stderr)
        return 1


def _format_check_report(result: dict[str, Any], max_issues: int) -> str:
    lines = ["## Compiler/Static Analysis Diagnostics\n"]
    lines.append(f"**Project**: `{result['project_root']}`")
    status_label = {
        "passed": "✅ Passed",
        "warning": "⚠️ Warnings",
        "unknown": "ℹ️ No diagnostic tools ran" if result.get("message") else "ℹ️ No supported types detected",
    }.get(result["status"], "❌ Errors")
    lines.append(f"**Status**: {status_label}")
    if result.get("message"):
        lines.append(f"**Message**: {result['message']}")
    lines.append(f"**Types**: {', '.join(result.get('types', [])) or 'auto-detected'}")
    lines.append(f"**Time**: {result['timestamp']}\n")

    summary = result.get("summary", {})
    lines.append("### Summary")
    lines.append(f"- Total errors: **{summary.get('total_errors', 0)}** 🔴")
    lines.append(f"- Total warnings: **{summary.get('total_warnings', 0)}** ⚠️")
    lines.append(f"- Files with issues: {summary.get('files_with_errors', 0)}")
    lines.append(f"- Tools run: {summary.get('tools_run', 0)} |  Skipped: {summary.get('tools_skipped', 0)}")
    if summary.get("tool_failures", 0):
        lines.append(f"- Tool failures: **{summary.get('tool_failures', 0)}**")
    if summary.get("tools_run", 0) == 0 and summary.get("tools_skipped", 0) > 0:
        lines.append("\n⚠️ No diagnostic tool was available; status is unknown, not passed.")
    lines.append("")

    runs = result.get("runs", [])
    if runs:
        lines.append("### Tool Execution Details\n")
        for run in runs:
            status = "⏭️ Skipped" if run.get("skipped") else ("✅ Passed" if run["exit_code"] == 0 and run["error_count"] == 0 else "❌ Failed")
            lines.append(f"**{run['tool']}** {status} ({run['duration_ms']}ms)")
            if run.get("skipped"):
                lines.append(f"  - Reason: {run.get('skip_reason', 'unknown')}")
            else:
                lines.append(f"  - Command: `{run['command']}`")
                if run.get("exit_code", 0) != 0:
                    lines.append(f"  - Exit code: {run['exit_code']}")
                if run.get("tool_failure_reason"):
                    lines.append(f"  - Reason: {run['tool_failure_reason']}")
                    excerpt = run.get("raw_excerpt") or []
                    if excerpt:
                        lines.append(f"  - Output: {str(excerpt[0])[:120]}")
                if run["error_count"] > 0:
                    lines.append(f"  - Errors: **{run['error_count']}**")
                if run["warning_count"] > 0:
                    lines.append(f"  - Warnings: {run['warning_count']}")
                if run.get("truncated"):
                    lines.append(f"  - ⚠️ Output truncated; showing first {max_issues} items")
            lines.append("")

    errors_by_file = result.get("errors_by_file", {})
    if errors_by_file:
        lines.append("### Issues by File (Top 10)\n")
        for file_path, issues in list(errors_by_file.items())[:10]:
            error_count = sum(1 for issue in issues if issue["severity"] == "error")
            warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
            info_count = sum(1 for issue in issues if issue["severity"] == "info")
            counts = []
            if error_count:
                counts.append(f"{error_count} errors")
            if warning_count:
                counts.append(f"{warning_count} warnings")
            if info_count:
                counts.append(f"{info_count} infos")
            lines.append(f"**{file_path}**: {', '.join(counts)}")
            for issue in issues[:3]:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue["severity"], "❌")
                confidence_icon = {"exact": "🎯", "line": "📍", "none": ""}.get(issue.get("symbol_confidence", "none"), "")
                symbol_info = f" {confidence_icon}`{issue['symbol']}`" if issue.get("symbol") else ""
                lines.append(f"  {icon} line{issue['line']}{symbol_info}: [{issue['code']}] {issue['message'][:50]}")
            lines.append("")

    return "\n".join(lines)


def _module_origin(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return "not found"
    return spec.origin or "built-in"


def run_doctor(project: str | None = None) -> int:
    from ..parser import TreeSitterAdapter

    # 如果提供了 --project 参数，显示提示（doctor 不使用它，但保持一致性）
    if project:
        print(f"Note: --project is accepted for consistency but not used by doctor command.", file=sys.stderr)

    adapter = TreeSitterAdapter()
    parsers = sorted(adapter.parsers)
    pyinstaller_spec = importlib.util.find_spec("PyInstaller")
    if parsers:
        print(f"tree-sitter parsers: {', '.join(parsers)}")
    else:
        print("tree-sitter bindings are missing", file=sys.stderr)
        return 1
    if "tsx" not in adapter.parsers:
        print("TSX parser: unavailable", file=sys.stderr)
        return 1
    print(f"repomap_cli: {_module_origin('repomap_cli')}")
    print(f"repomap_parser: {_module_origin('repomap_parser')}")
    print(f"repomap_core: {_module_origin('repomap_core')}")
    print(f"tree_sitter: {_module_origin('tree_sitter')}")
    print(f"tree_sitter_typescript: {_module_origin('tree_sitter_typescript')}")
    print(f"PACKAGE_ROOT: {PACKAGE_ROOT}")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print("LSP client: available")
    print("Bundled LSP servers: none")
    print("LSP server detection: run `repomap lsp doctor --project <path>`")
    if pyinstaller_spec is not None:
        print("PyInstaller: available")
    else:
        print("PyInstaller: not installed in current runtime, only required for build-binary")
    return 0


def _pyinstaller_command(output_dir: Path, name: str) -> list[str]:
    build_root = output_dir / ".pyinstaller"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(output_dir),
        "--workpath",
        str(build_root / "build"),
        "--specpath",
        str(build_root / "spec"),
    ]
    for module_name in PYINSTALLER_BINDINGS:
        command.extend(["--hidden-import", module_name])
    command.append(str(PACKAGE_ROOT / "__main__.py"))
    return command


def run_state_map(project: str, max_files: int, symbol: str | None, query: str | None, as_json: bool) -> int:
    from ..state_map import find_state_definitions

    if not symbol and not query:
        print(f"[{CLI_NAME}] state-map requires --symbol or --query", file=sys.stderr)
        return 2

    try:
        engine = _scan_engine(project, max_files)
        defs = find_state_definitions(engine, query=query, symbol=symbol)

        if as_json:
            payload = {
                "command": "state-map",
                "project": str(engine.project_root),
                "query": query,
                "symbol": symbol,
                "definitions": [
                    {
                        "symbol_name": d.symbol_name,
                        "file": d.file,
                        "line": d.line,
                        "kind": d.kind,
                        "values": [{"name": v.name, "file": v.file, "line": v.line} for v in d.values],
                        "writers": [{"name": w.name, "file": w.file, "line": w.line} for w in d.writers],
                        "readers": [{"name": r.name, "file": r.file, "line": r.line} for r in d.readers],
                    }
                    for d in defs
                ],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        # Text output
        lines: list[str] = []
        for d in defs:
            lines.append(f"## State Map — {d.symbol_name}\n")
            lines.append(f"- **File**: `{d.file}:{d.line}`  ({d.kind})\n")
            if d.values:
                lines.append("### Values\n")
                for v in d.values:
                    lines.append(f"- `{v.name}` — `{v.file}:{v.line}`")
                lines.append("")
            if d.writers:
                lines.append("### Writers\n")
                for w in d.writers[:10]:
                    lines.append(f"- `{w.name}` — `{w.file}:{w.line}`")
                lines.append("")
            if d.readers:
                lines.append("### Readers / Branches\n")
                for r in d.readers[:10]:
                    lines.append(f"- `{r.file}:{r.line}` — {r.name}")
                lines.append("")
            lines.append("**Risk hint**: Adding or removing a state value requires checking all writers, readers, and tests.\n")

        if not defs:
            print(f"> No state definitions found for symbol={symbol or 'N/A'} query={query or 'N/A'}.")
        else:
            print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] state-map failed: {exc}", file=sys.stderr)
        return 1


def run_build_binary(output: str, name: str) -> int:
    output_dir = Path(output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(_pyinstaller_command(output_dir, name), cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        print(f"[{CLI_NAME}] build failed with exit code {result.returncode}", file=sys.stderr)
        return result.returncode or 1
    print(f"binary ready: {output_dir / name}")
    return 0
