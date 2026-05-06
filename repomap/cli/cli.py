from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from repomap.ai import (
    _build_query_reading_order,
    _get_hot_files,
    _rank_symbols_for_file,
    render_impact_report,
    render_query_report,
    render_routes_report,
    render_verify_report,
)
from repomap.check import RepoMapChecker
from repomap.core import RepoMapEngine, SKIP_DIR_NAMES, SKIP_FILE_NAMES
from repomap.parser import EXT_TO_LANG
from repomap import (
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
from repomap.toolkit import diff_project, save_cache, scan_project
from repomap.topic import (
    FileMatch,
    TestMatch,
    classify_file_role,
    compute_keyword_weights,
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
EXIT_ERROR = 1         # 命令执行失败
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
SESSION_CACHE_VERSION = 5
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
    overview_parser.add_argument("--with-co-change", action="store_true", default=False,
                                help="Include Git co-change coupling section; disabled by default for speed.")
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

    subparsers.add_parser("doctor", help="Validate runtime and build prerequisites.")

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
                           with_co_change=getattr(args, "with_co_change", False),
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
        return run_routes(args.project, args.max_files, args.json)
    if command == "doctor":
        return run_doctor()
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
        print(f"[{CLI_NAME}] 从磁盘恢复会话缓存", file=sys.stderr)
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
) -> tuple[Any | None, str | None]:
    matches = engine.query_symbol(symbol)
    if not matches:
        return None, f"> 未找到符号 `{symbol}`"

    exact_matches = [item for item in matches if item.name == symbol]
    candidates = exact_matches or matches

    if file_path:
        filtered = [item for item in candidates if item.file == file_path]
        if not filtered:
            return None, f"> 未找到符号 `{symbol}` 在 `{file_path}` 中的匹配"
        candidates = filtered

    if len(candidates) == 1:
        return candidates[0], None

    lines = [f"> 符号 `{symbol}` 存在多个候选，请用 `--file-path` 指定目标文件："]
    for item in candidates[:10]:
        lines.append(f"- `{item.file}:{item.line}` ({item.kind})")
    return None, "\n".join(lines)


def _group_symbol_matches(results: list[Any], symbol: str) -> tuple[list[Any], list[Any]]:
    exact = [item for item in results if item.name == symbol]
    fuzzy = [item for item in results if item.name != symbol]
    return exact, fuzzy


def _render_selected_call_chain(engine: RepoMapEngine, symbol: Any, depth: int) -> str:
    chain = engine.call_chain(symbol.id, "both", depth)
    lines = [
        f"## 调用链 — `{symbol.name}`\n",
        f"- **类型**: {symbol.kind}",
        f"- **位置**: `{symbol.file}:{symbol.line}`",
        f"- **重要性**: PR={symbol.pagerank * 1000:.1f}",
    ]
    if symbol.signature:
        lines.append(f"- **签名**: `{symbol.signature}`")
    lines.append("")

    callers = chain["callers"]
    lines.append(f"### 被以下符号调用（{len(callers)}）\n")
    if callers:
        for caller in callers[:20]:
            lines.append(f"- `{caller.name}` ({caller.kind}) — `{caller.file}:{caller.line}`")
        if len(callers) > 20:
            lines.append(f"- …还有 {len(callers) - 20} 个")
    else:
        lines.append("- （无，可能是入口点）")

    callees = chain["callees"]
    lines.append(f"\n### 调用了以下符号（{len(callees)}）\n")
    if callees:
        for callee in callees[:20]:
            lines.append(f"- `{callee.name}` ({callee.kind}) — `{callee.file}:{callee.line}`")
        if len(callees) > 20:
            lines.append(f"- …还有 {len(callees) - 20} 个")
    else:
        lines.append("- （无，叶子函数）")

    return "\n".join(lines)


def _truncate_output(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n…（超出字符限制，已截断）"


def run_scan(project: str, max_files: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        hot = engine.hotspots(5)
        entry_points = engine.entry_points()
        lines = [
            f"✅ 扫描完成 — `{engine.project_root}`\n",
            *engine._scan_summary_lines(),
            f"- 入口点: {', '.join(entry_points) or '未检测到'}",
            "\n**高密度文件（Top 5）**:",
        ]
        if engine.scan_stats.truncated_files:
            lines.insert(6, f"- max_files 截断: {engine.scan_stats.truncated_files}")
        for item in hot:
            lines.append(f"  - `{item['file']}` — {item['symbol_count']} symbols ({item['risk']} risk)")
        lines.append("\n> 建议下一步调用 `repomap overview --project <path>` 获取完整项目地图。")
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
        selected, error = _select_symbol_match(engine, symbol, file_path=file_path)
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
            lines = [f"## 调用链 — `{selected.name}`\n"]
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
    from repomap.lsp import collect_lsp_symbol_evidence, run_result_to_dict

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
            print(f"> 未找到匹配 `{symbol}` 的符号", file=sys.stderr)
            return EXIT_NO_RESULTS
        exact_matches, fuzzy_matches = _group_symbol_matches(results, symbol)

        lines = [f"找到 {len(results)} 个匹配结果。\n"]
        if file_path:
            lines.append(f"已按文件过滤: `{file_path}`\n")
        if len(exact_matches) > 1 and not file_path:
            lines.append(f"精确匹配有 {len(exact_matches)} 个候选，建议加 `--file-path` 锁定目标文件。\n")

        if exact_matches:
            lines.append(f"## 精确匹配 `{symbol}` ({len(exact_matches)})\n")
            for item in exact_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}")
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")

        if fuzzy_matches:
            lines.append(f"\n## 模糊匹配 ({len(fuzzy_matches)})\n")
            for item in fuzzy_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}")
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")

        if len(results) > 10 and (len(exact_matches) > 10 or len(fuzzy_matches) > 10):
            lines.append("\n> 结果较多，建议补 `--file-path` 缩小范围。")
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
        print(engine.render_file_detail(normalized_file_path, max_symbols=max_symbols, max_chars=max_chars))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] file-detail failed: {exc}", file=sys.stderr)
        return 1


def run_routes(project: str, max_files: int, as_json: bool) -> int:
    try:
        engine = _scan_engine(project, max_files)
        if as_json:
            payload = {
                "command": "routes",
                "project": str(engine.project_root),
                "scanStats": _scan_stats_payload(engine),
                "routes": [_route_payload(route) for route in engine.list_routes()],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
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
        lines = ["## 高密度文件（符号数排名）\n"]
        for index, item in enumerate(hotspots, 1):
            lines.append(f"{index}. {risk_mark[item['risk']]} `{item['file']}` — **{item['symbol_count']}** 个符号")
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
    lines = ["## 变更检测\n"]
    lines.append(f"**对比**: {result.get('last_scan', 'unknown')} → {result.get('scan_time', datetime.now().isoformat())}\n")
    lines.append(f"- 新增符号: {result['summary']['added']}")
    lines.append(f"- 删除符号: {result['summary']['removed']}")
    lines.append(f"- 修改符号: {result['summary']['modified']}")
    lines.append(f"- 新增调用: {result['summary']['edges_added']}")
    lines.append(f"- 删除调用: {result['summary']['edges_removed']}\n")
    if result["added_symbols"]:
        lines.append("**新增符号** (Top 10):")
        for item in result["added_symbols"][:10]:
            lines.append(f"  - `{item['name']}` ({item['file']}:{item['line']})")
    if result["call_chain_changes"]["new_calls"]:
        lines.append("\n**新增调用关系** (Top 10):")
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
                role = classify_file_role(file_path)
                reasons = _build_match_reasons(query, file_path, engine.graph)
                matches.append(FileMatch(path=file_path, role=role, score=score, reasons=reasons))

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


def _build_match_reasons(query: str, file_path: str, graph: RepoGraph) -> list[str]:
    """构建匹配原因列表。"""
    reasons: list[str] = []
    keywords = query.lower().split()
    path_lower = file_path.lower()
    file_name = PurePosixPath(file_path).stem.lower()
    tokens = split_identifier(PurePosixPath(file_path).stem)

    for kw in keywords:
        if kw in path_lower:
            reasons.append(f"路径包含 {kw}")
        if kw in file_name:
            reasons.append(f"文件名命中 {kw}")
        elif any(kw in t for t in tokens):
            reasons.append(f"文件名拆分匹配 {kw}")
    return reasons[:3]


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
                "role": classify_file_role(m.path),
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
        from repomap.lsp import detect_lsp_server, detection_to_dict, language_for_file
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
                        f"引用了 {_sym_name(engine, sid)}",
                        "high",
                    )

            for edge in engine.graph.outgoing.get(sid, []):
                callee = engine.graph.symbols.get(edge.target)
                if callee and callee.file not in target_files:
                    callee_name = callee.name
                    if callee.file not in affected_files:
                        affected_files[callee.file] = (
                            f"输入文件调用了 {callee_name}（via {_sym_name(engine, sid)}）",
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
                                        f"传递影响 depth={current_depth + 1}: 调用了 {affected_file} 中的 {src_sym.name}",
                                        "low",
                                    )
                        # 这个受影响文件的符号调用了谁？
                        for edge in engine.graph.outgoing.get(sid, []):
                            tgt_sym = engine.graph.symbols.get(edge.target)
                            if tgt_sym and tgt_sym.file not in processed_files:
                                next_frontier.add(tgt_sym.file)
                                if tgt_sym.file not in affected_files:
                                    affected_files[tgt_sym.file] = (
                                        f"传递影响 depth={current_depth + 1}: 被 {affected_file} 中的 {_sym_name(engine, sid)} 调用",
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
            risk_notes.append(f"`{f}` 被 {nc} 个文件关联，改动影响面很大")
        elif nc >= 5:
            structural_risk += 3
            risk_notes.append(f"`{f}` 被 {nc} 个文件关联，改动影响面大")
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
        risk_notes.append(f"涉及高风险领域（认证/安全/数据持久化）")
    elif domain_risk >= 3:
        risk_notes.append(f"涉及中风险领域（终端/配置/构建）")
    total_score += domain_risk

    # 第3层：变更类型风险
    change_type_risk = 0
    for f in target_files:
        if is_test_like_file(f):
            pass  # 只改测试不改实现，低风险
        elif any(f.endswith(ext) for ext in [".config.ts", ".config.js", "package.json"]):
            change_type_risk += 2
            risk_notes.append(f"`{f}` 是配置文件变更，影响全局")
        elif "types" in PurePosixPath(f).parts or f.endswith(".d.ts"):
            change_type_risk += 1
            risk_notes.append(f"`{f}` 是类型定义变更，影响面大")
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
                    f"引用了变更符号 {_sym_name(engine, symbol_id)}",
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
            missing_checks.append("没有检测到前端测试文件变更，建议补充前端测试")
    if ".py" in all_exts:
        if not any(test.test_file.endswith(".py") for test in tests):
            missing_checks.append("没有检测到 Python 测试文件变更，建议补充后端测试")

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
        from repomap.lsp import collect_lsp_diagnostics, run_result_to_dict

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
        from repomap.toolkit import load_cache
        from repomap import compare_graph_snapshots
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
            },
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_verify_report(payload))
        return 1 if status == "failed" else 0
    except Exception as exc:
        print(f"[{CLI_NAME}] verify failed: {exc}", file=sys.stderr)
        return 1


def run_git_history(project: str, max_files: int, symbol: str, file_path: str | None) -> int:
    try:
        engine = _scan_engine(project, max_files)
        selected, error = _select_symbol_match(engine, symbol, file_path=file_path)
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
                f"📍 符号: `{target.name}`\n📁 位置: `{target.file}:{target.line}`\n\n❌ Git 信息获取失败（可能不是 git 仓库）",
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
        lines = [f"## Git 历史 — `{target.name}`\n"]
        lines.append(f"📍 位置: `{target.file}:{target.line}`")
        lines.append(f"🔖 当前版本: `{commit_hash[:8]}`\n")
        if file_commits.returncode == 0 and file_commits.stdout:
            lines.append("**最近提交**:")
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
            selected, error = _select_symbol_match(engine, symbol, file_path=file_path)
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
                lines = [f"## 引用分析 — `{target.name}`\n"]
                lines.append(f"- 被引用次数: {payload['ref_count']}")
                lines.append(f"- 调用其他: {len(payload['calls'])}")
                lines.append(f"- 入口函数: {'是' if payload['is_entry'] else '否'}")
                lines.append(f"- 叶子函数: {'是' if payload['is_leaf'] else '否'}\n")
                if payload["called_by"]:
                    lines.append("**被调用** (Top 10):")
                    for row in payload["called_by"][:10]:
                        lines.append(f"  - `{row['name']}` ({row['file']}:{row['line']})")
                if payload["calls"]:
                    lines.append("\n**调用** (Top 10):")
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
        lines = ["## 全局引用分析\n"]
        lines.append(f"- 总符号数: {payload['total_symbols']}")
        lines.append(f"- 入口函数: {len(payload['entry_points'])}")
        lines.append(f"- 孤立符号: {len(payload['orphaned_symbols'])}\n")
        lines.append("**被引用最多** (Top 10):")
        for row in payload["most_referenced"][:10]:
            lines.append(f"  - `{row['name']}`: {row['ref_count']} 次引用 ({row['file']})")
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
            reasons.append("测试文件")
            break
    name_lower = symbol.name.lower()
    if any(name_lower.startswith(prefix) for prefix in ("test_", "it_", "should_")):
        reasons.append("测试辅助函数")
    if symbol.kind == "impl":
        reasons.append("实现块(可能宏驱动)")
    if symbol.kind in ("struct", "enum", "class"):
        reasons.append("类型定义(可能反射/宏使用)")
    if not reasons:
        reasons.append("无调用者和被调用者")
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
        lines = ["## 死代码分析\n"]
        lines.append(f"总计 {len(candidates)} 候选（已过滤 module/element/json_key 等 {filtered_structural_count} 个结构元素）")
        if min_confidence > 0:
            lines.append(f"置信度阈值: {min_confidence}（已过滤低置信项）")
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
            tier_lines = [f"### {emoji} {title} — {len(items)} 个"]
            # 按模块分组
            by_module: dict[str, list[dict]] = {}
            for item in items:
                mod = _module_for_file(item["symbol"].file)
                by_module.setdefault(mod, []).append(item)
            tier_lines.append("")
            for mod in sorted(by_module, key=lambda m: -len(by_module[m])):
                mod_items = by_module[mod][:max(3, max_items // max(len(by_module), 1))]
                tier_lines.append(f"**`{mod}/`** ({len(by_module[mod])} 个)")
                for item in mod_items:
                    sym = item["symbol"]
                    tier_lines.append(f"- `{sym.name}` ({sym.kind}) `{sym.file}:{sym.line}` — {item['confidence']}% | {item['note']}")
                if len(by_module[mod]) > len(mod_items):
                    tier_lines.append(f"  …还有 {len(by_module[mod]) - len(mod_items)} 个")
            tier_lines.append("")
            return tier_lines

        lines.extend(_render_tier("高置信（建议审查）", "🔴", high, limit))
        lines.extend(_render_tier("中置信（需要确认）", "🟡", medium, limit))
        lines.extend(_render_tier("低置信（可能为活跃代码）", "🟢", low, limit))

        if low:
            lines.append("> 使用 `--min-confidence 40` 过滤低置信项。")
        lines.append("> 不能仅据此删除，需要额外代码/业务验证。使用 `--json` 获取完整结构化输出。")
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
        from repomap.lsp import detect_lsp_servers, detection_to_dict

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
        from repomap.lsp import collect_lsp_diagnostics, run_result_to_dict

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
    lines = ["## 编译器/静态分析诊断\n"]
    lines.append(f"**项目**: `{result['project_root']}`")
    status_label = {
        "passed": "✅ 通过",
        "warning": "⚠️ 有警告",
        "unknown": "ℹ️ 未实际运行诊断工具" if result.get("message") else "ℹ️ 未检测到支持类型",
    }.get(result["status"], "❌ 有错误")
    lines.append(f"**状态**: {status_label}")
    if result.get("message"):
        lines.append(f"**说明**: {result['message']}")
    lines.append(f"**检测类型**: {', '.join(result.get('types', [])) or '自动检测'}")
    lines.append(f"**时间**: {result['timestamp']}\n")

    summary = result.get("summary", {})
    lines.append("### 汇总")
    lines.append(f"- 错误总数: **{summary.get('total_errors', 0)}** 🔴")
    lines.append(f"- 警告总数: **{summary.get('total_warnings', 0)}** ⚠️")
    lines.append(f"- 涉及文件: {summary.get('files_with_errors', 0)}")
    lines.append(f"- 运行工具: {summary.get('tools_run', 0)} | 跳过: {summary.get('tools_skipped', 0)}")
    if summary.get("tool_failures", 0):
        lines.append(f"- 工具执行失败: **{summary.get('tool_failures', 0)}**")
    lines.append("")

    runs = result.get("runs", [])
    if runs:
        lines.append("### 工具执行详情\n")
        for run in runs:
            status = "⏭️ 跳过" if run.get("skipped") else ("✅ 通过" if run["exit_code"] == 0 and run["error_count"] == 0 else "❌ 失败")
            lines.append(f"**{run['tool']}** {status} ({run['duration_ms']}ms)")
            if run.get("skipped"):
                lines.append(f"  - 原因: {run.get('skip_reason', '未知')}")
            else:
                lines.append(f"  - 命令: `{run['command']}`")
                if run.get("exit_code", 0) != 0:
                    lines.append(f"  - 退出码: {run['exit_code']}")
                if run.get("tool_failure_reason"):
                    lines.append(f"  - 原因: {run['tool_failure_reason']}")
                    excerpt = run.get("raw_excerpt") or []
                    if excerpt:
                        lines.append(f"  - 输出: {str(excerpt[0])[:120]}")
                if run["error_count"] > 0:
                    lines.append(f"  - 错误: **{run['error_count']}**")
                if run["warning_count"] > 0:
                    lines.append(f"  - 警告: {run['warning_count']}")
                if run.get("truncated"):
                    lines.append(f"  - ⚠️ 结果已截断，仅显示前 {max_issues} 条")
            lines.append("")

    errors_by_file = result.get("errors_by_file", {})
    if errors_by_file:
        lines.append("### 按文件分组的问题 (Top 10)\n")
        for file_path, issues in list(errors_by_file.items())[:10]:
            error_count = sum(1 for issue in issues if issue["severity"] == "error")
            warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
            info_count = sum(1 for issue in issues if issue["severity"] == "info")
            counts = []
            if error_count:
                counts.append(f"{error_count} 错误")
            if warning_count:
                counts.append(f"{warning_count} 警告")
            if info_count:
                counts.append(f"{info_count} 信息")
            lines.append(f"**{file_path}**: {', '.join(counts)}")
            for issue in issues[:3]:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue["severity"], "❌")
                confidence_icon = {"exact": "🎯", "line": "📍", "none": ""}.get(issue.get("symbol_confidence", "none"), "")
                symbol_info = f" {confidence_icon}`{issue['symbol']}`" if issue.get("symbol") else ""
                lines.append(f"  {icon} 行{issue['line']}{symbol_info}: [{issue['code']}] {issue['message'][:50]}")
            lines.append("")

    return "\n".join(lines)


def _module_origin(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return "not found"
    return spec.origin or "built-in"


def run_doctor() -> int:
    from repomap.parser import TreeSitterAdapter

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


def run_build_binary(output: str, name: str) -> int:
    output_dir = Path(output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(_pyinstaller_command(output_dir, name), cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        print(f"[{CLI_NAME}] build failed with exit code {result.returncode}", file=sys.stderr)
        return result.returncode or 1
    print(f"binary ready: {output_dir / name}")
    return 0
