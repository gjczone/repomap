from __future__ import annotations

import argparse
import sys
from pathlib import Path  # kept: Path used in _add_project_args
from typing import Sequence

from .handlers import CLI_NAME


# _SCAN_CACHE is now in handlers.py; re-exported below
# 缓存语义变更时需要升级，避免 CLI/Binary 复用旧结果误导阅读顺序和调用链。
SESSION_CACHE_VERSION = 7
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
        description="RepoMap CLI — repository intelligence for AI agents.",
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
    topic_query_parser.add_argument("--context-lines", type=int, default=2, help="Context lines around matched text (default 2).")

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
    file_parser.add_argument("--with-lsp", action="store_true", help="Include LSP symbol tree in file detail output.")
    file_parser.add_argument("--lsp-timeout", type=float, default=8.0, help="Seconds to wait for LSP responses.")

    hotspots_parser = subparsers.add_parser("hotspots", help="Scan a repository and print hotspot files.")
    _add_project_args(hotspots_parser)
    hotspots_parser.add_argument("--limit", type=int, default=15, help="Number of files to print.")

    cache_parser = subparsers.add_parser("cache", help="Prepare a graph baseline before the target edits.")
    cache_parser.add_argument("action", choices=["save"], help="Cache action. Only save is public; graph comparison reads the baseline through diff/verify --with-diff.")
    cache_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")

    diff_parser = subparsers.add_parser("diff", help="Advanced graph-only comparison against a baseline saved before the target edits.")
    diff_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    diff_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

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

    lsp_parser = subparsers.add_parser("lsp", help="Inspect local LSP server availability.")
    lsp_subparsers = lsp_parser.add_subparsers(dest="lsp_command", required=True)
    lsp_doctor_parser = lsp_subparsers.add_parser("doctor", help="Detect local LSP servers without starting analysis.")
    lsp_doctor_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    lsp_doctor_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    lsp_setup_parser = lsp_subparsers.add_parser("setup", help="Auto-install recommended LSP servers for detected project languages.")
    lsp_setup_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    lsp_setup_parser.add_argument("--languages", nargs="*", default=None, help="Languages to install servers for (default: auto-detect).")
    lsp_setup_parser.add_argument("--dry-run", action="store_true", help="Show what would be installed without doing it.")

    routes_parser = subparsers.add_parser("routes", help="Extract direct HTTP/API route inventory.")
    _add_project_args(routes_parser)
    routes_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")
    routes_parser.add_argument("--with-consumers", action="store_true", help="Scan for frontend/client consumers of each route.")

    doctor_parser = subparsers.add_parser("doctor", help="Validate runtime, prerequisites, and LSP server availability.")
    doctor_parser.add_argument("--project", "-p", default=None, help="Project root path. Defaults to the current working directory.")
    doctor_parser.add_argument("--lsp", action="store_true", help="Also check LSP server availability and suggest install commands.")

    state_map_parser = subparsers.add_parser("state-map", help="Map state values, writers, and readers for an enum/type.")
    _add_project_args(state_map_parser)
    state_map_parser.add_argument("--symbol", default=None, help="Symbol name (e.g. TaskStatus).")
    state_map_parser.add_argument("--query", default=None, help="Keywords to find relevant state definitions.")
    state_map_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

    search_parser = subparsers.add_parser("search", help="BM25 symbol search by natural language query.")
    _add_project_args(search_parser)
    search_parser.add_argument("--query", "-q", required=True, help="Search query (natural language or keywords).")
    search_parser.add_argument("--top-k", type=int, default=20, help="Maximum results (default 20).")

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
    from .handlers import (  # noqa: PLC0415
        run_scan, run_overview, run_call_chain, run_query_symbol,
        run_file_detail, run_routes, run_hotspots, run_cache, run_diff,
        run_query, run_impact, run_verify, run_refs, run_orphan,
        run_lsp_doctor, run_lsp_setup, run_check, run_doctor,
        run_state_map, run_build_binary, run_search,
    )
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
            getattr(args, "context_lines", 2),
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
        return run_file_detail(args.project, args.max_files, args.file_path, args.max_symbols, args.max_chars,
                              getattr(args, "with_lsp", False), getattr(args, "lsp_timeout", 8.0))
    if command == "hotspots":
        return run_hotspots(args.project, args.max_files, args.limit)
    if command == "cache":
        return run_cache(args.project, args.action)
    if command == "diff":
        return run_diff(args.project, args.json)
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
    if command == "lsp":
        if args.lsp_command == "doctor":
            return run_lsp_doctor(args.project, args.json)
        if args.lsp_command == "setup":
            return run_lsp_setup(args.project, args.languages, args.dry_run)
        parser.error(f"unknown lsp command: {args.lsp_command}")
        return 2
    if command == "routes":
        return run_routes(args.project, args.max_files, args.json, args.with_consumers)
    if command == "doctor":
        return run_doctor(args.project, getattr(args, "lsp", False))
    if command == "state-map":
        return run_state_map(args.project, args.max_files, args.symbol, args.query, args.json)
    if command == "search":
        return run_search(args.project, args.max_files, args.query, args.top_k)
    if command == "build-binary":
        return run_build_binary(args.output, args.name)
    parser.error(f"unknown command: {command}")
    return 2


# Re-exports for external consumers (tests, __init__.py)
from ..core import RepoMapEngine  # noqa: E402, F401
from .handlers import _resolve_project  # noqa: E402, F401
from .handlers import _scan_engine  # noqa: E402, F401
from .handlers import _parse_git_status_porcelain_paths  # noqa: E402, F401
from .handlers import _SCAN_CACHE  # noqa: E402, F401
