from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .handlers import CLI_NAME, DEFAULT_LSP_TIMEOUT
from ..core import DEFAULT_MAX_FILES


from .. import (
    DEFAULT_CALL_CHAIN_MAX_CHARS,
    DEFAULT_FILE_DETAIL_MAX_CHARS,
    DEFAULT_FILE_DETAIL_MAX_SYMBOLS,
    DEFAULT_OVERVIEW_MAX_CHARS,
    DEFAULT_QUERY_SYMBOL_MAX_CHARS,
    DEFAULT_VERIFY_MAX_CHARS,
)


def build_parser() -> argparse.ArgumentParser:
    from .. import get_repomap_version

    parser = argparse.ArgumentParser(
        prog=CLI_NAME,
        description="RepoMap CLI — repository intelligence for AI agents.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_repomap_version()}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    overview_parser = subparsers.add_parser(
        "overview", help="Scan a repository and print the overview report."
    )
    _add_project_args(overview_parser)
    overview_parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick scan: file/symbol counts and entrypoints only.",
    )
    overview_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_OVERVIEW_MAX_CHARS,
        help="Maximum overview size for AI-friendly output.",
    )
    overview_parser.add_argument(
        "--with-heat",
        action="store_true",
        default=False,
        help="Mark files changed in the last 30 days with [HOT].",
    )
    overview_parser.add_argument(
        "--with-co-change",
        action="store_true",
        default=False,
        help="Enable Git co-change coupling analysis (expensive: reads git history). Use --co-change-days to control window.",
    )
    overview_parser.add_argument(
        "--co-change-days",
        type=int,
        default=30,
        help="Days of git history for co-change analysis (default 30).",
    )
    overview_parser.add_argument(
        "--granularity",
        choices=["full", "medium", "compact", "auto"],
        default="auto",
        help="Report granularity (default: auto, based on project size).",
    )

    chain_parser = subparsers.add_parser(
        "call-chain", help="Scan a repository and print a symbol call chain."
    )
    _add_project_args(chain_parser)
    chain_parser.add_argument("--symbol", required=True, help="Symbol name to analyze.")
    chain_parser.add_argument("--file-path", help="Disambiguate by relative file path.")
    chain_parser.add_argument(
        "--direction", choices=["callers", "callees", "both"], default="both"
    )
    chain_parser.add_argument("--depth", type=int, default=3, help="Traversal depth.")
    chain_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_CALL_CHAIN_MAX_CHARS,
        help="Maximum text output size.",
    )

    query_parser = subparsers.add_parser(
        "query-symbol", help="Scan a repository and query matching symbols."
    )
    _add_project_args(query_parser)
    query_parser.add_argument(
        "--symbol", required=True, help="Symbol name to search for."
    )
    query_parser.add_argument("--file-path", help="Optional relative file path filter.")
    query_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_QUERY_SYMBOL_MAX_CHARS,
        help="Maximum text output size.",
    )
    query_parser.add_argument(
        "--lsp-timeout",
        type=float,
        default=DEFAULT_LSP_TIMEOUT,
        help="Seconds to wait for LSP responses.",
    )

    # ── 新增: query（主题关键词搜索）──────────────────────────────────────────
    topic_query_parser = subparsers.add_parser(
        "query", help="Search repository by topic keyword."
    )
    _add_project_args(topic_query_parser)
    topic_query_parser.add_argument(
        "--query", "-q", required=True, help="Topic keyword."
    )
    topic_query_parser.add_argument(
        "--max-result-files",
        type=int,
        default=20,
        help="Max result files (default 20).",
    )
    topic_query_parser.add_argument(
        "--max-symbols", type=int, default=40, help="Max result symbols (default 40)."
    )
    topic_query_parser.add_argument("--no-tests", action="store_true")
    topic_query_parser.add_argument(
        "--paths", help="Limit search to comma-separated directories."
    )
    topic_query_parser.add_argument(
        "--exclude", help="Exclude comma-separated directories."
    )
    topic_query_parser.add_argument(
        "--context-lines",
        type=int,
        default=2,
        help="Context lines around matched text (default 2).",
    )

    # ── 新增: impact（文件级影响分析）──────────────────────────────────────────
    impact_parser = subparsers.add_parser(
        "impact", help="Analyze file-level change impact."
    )
    _add_project_args(impact_parser)
    impact_parser.add_argument(
        "--files", required=True, nargs="+", help="Files to analyze (one or more)."
    )
    impact_parser.add_argument(
        "--with-symbols",
        action="store_true",
        help="Include edit-planning key symbols, read-next order, and LSP availability hint.",
    )
    impact_parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Transitive impact depth (default 1=direct, 2=one hop out).",
    )
    impact_parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="Force full scan instead of incremental.",
    )

    verify_parser = subparsers.add_parser(
        "verify", help="Aggregate post-edit evidence before final handoff."
    )
    _add_project_args(verify_parser)
    verify_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_VERIFY_MAX_CHARS,
        help="Maximum text output size for verify report.",
    )
    verify_parser.add_argument(
        "--types",
        nargs="*",
        choices=["typescript", "rust", "python", "go", "javascript"],
        help="Explicit project types to check.",
    )
    verify_parser.add_argument(
        "--max-issues", type=int, default=50, help="Maximum issues per tool."
    )
    verify_parser.add_argument(
        "--no-symbols",
        action="store_true",
        help="Skip scan-based symbol resolution for diagnostics.",
    )
    verify_parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="Force full scan instead of incremental.",
    )
    verify_parser.add_argument(
        "--lsp-timeout",
        type=float,
        default=DEFAULT_LSP_TIMEOUT,
        help="Seconds to wait for LSP responses.",
    )
    verify_parser.add_argument(
        "--lsp-max-files",
        type=int,
        default=20,
        help="Maximum changed files to open through LSP.",
    )
    verify_parser.add_argument(
        "--with-diff",
        action="store_true",
        help="Include graph diff when a cache baseline exists.",
    )
    verify_parser.add_argument(
        "--quick",
        action="store_true",
        help="Risk-only mode for current Git changes; skips compiler and LSP checks.",
    )

    file_parser = subparsers.add_parser(
        "file-detail", help="Scan a repository and print file detail."
    )
    _add_project_args(file_parser)
    file_parser.add_argument(
        "--file-path", required=True, help="Relative file path to inspect."
    )
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
    file_parser.add_argument(
        "--lsp-timeout",
        type=float,
        default=DEFAULT_LSP_TIMEOUT,
        help="Seconds to wait for LSP responses.",
    )

    hotspots_parser = subparsers.add_parser(
        "hotspots", help="Scan a repository and print hotspot files."
    )
    _add_project_args(hotspots_parser)
    hotspots_parser.add_argument(
        "--limit", type=int, default=15, help="Number of files to print."
    )

    cache_parser = subparsers.add_parser(
        "cache", help="Prepare a graph baseline before the target edits."
    )
    cache_parser.add_argument(
        "action",
        choices=["save"],
        help="Cache action. Only save is public; graph comparison reads the baseline through diff/verify --with-diff.",
    )
    _add_project_args(cache_parser)

    diff_parser = subparsers.add_parser(
        "diff",
        help="Advanced graph-only comparison against a baseline saved before the target edits.",
    )
    _add_project_args(diff_parser)

    refs_parser = subparsers.add_parser(
        "refs", help="Scan a repository and analyze references."
    )
    _add_project_args(refs_parser)
    refs_parser.add_argument("--symbol", help="Optional symbol name.")
    refs_parser.add_argument(
        "--file-path", help="Disambiguate symbol analysis by relative file path."
    )
    refs_parser.add_argument(
        "--lsp-timeout",
        type=float,
        default=DEFAULT_LSP_TIMEOUT,
        help="Seconds to wait for LSP responses.",
    )

    orphan_parser = subparsers.add_parser(
        "orphan", help="Scan a repository and find orphaned symbols."
    )
    _add_project_args(orphan_parser)
    orphan_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max candidates per confidence tier in text mode (default 20).",
    )
    orphan_parser.add_argument(
        "--min-confidence",
        type=int,
        default=0,
        help="Minimum confidence score 0-100 to include in output (default 0).",
    )

    check_parser = subparsers.add_parser(
        "check", help="Run compiler/static analysis diagnostics."
    )
    _add_project_args(check_parser)
    check_parser.add_argument(
        "--types",
        nargs="*",
        choices=["typescript", "rust", "python", "go", "javascript"],
        help="Explicit project types to check.",
    )
    check_parser.add_argument(
        "--max-issues", type=int, default=50, help="Maximum issues per tool."
    )
    check_parser.add_argument(
        "--since-commit", help="Only check files changed since the given commit."
    )
    check_parser.add_argument(
        "--modified-file",
        action="append",
        dest="modified_files",
        metavar="PATH",
        help="Explicit modified file path.",
    )
    check_parser.add_argument(
        "--no-symbols", action="store_true", help="Skip scan-based symbol resolution."
    )
    check_parser.add_argument(
        "--lsp-timeout",
        type=float,
        default=DEFAULT_LSP_TIMEOUT,
        help="Seconds to wait for LSP responses.",
    )
    check_parser.add_argument(
        "--lsp-max-files",
        type=int,
        default=20,
        help="Maximum explicit files to open through LSP.",
    )
    fix_parser = subparsers.add_parser(
        "fix", help="Auto-fix lint issues (ruff --fix, eslint --fix)."
    )
    fix_parser.add_argument(
        "--project",
        "-p",
        required=True,
        help="Project root path (absolute path recommended).",
    )
    fix_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fixed without applying changes.",
    )

    ready_parser = subparsers.add_parser(
        "ready", help="Quick readiness check: verify --quick + check + format."
    )
    _add_project_args(ready_parser)

    lsp_parser = subparsers.add_parser(
        "lsp", help="Inspect local LSP server availability."
    )
    lsp_subparsers = lsp_parser.add_subparsers(dest="lsp_command", required=True)
    lsp_doctor_parser = lsp_subparsers.add_parser(
        "doctor", help="Detect local LSP servers without starting analysis."
    )
    _add_project_args(lsp_doctor_parser)
    lsp_setup_parser = lsp_subparsers.add_parser(
        "setup",
        help="Auto-install recommended LSP servers for detected project languages.",
    )
    _add_project_args(lsp_setup_parser)
    lsp_setup_parser.add_argument(
        "--languages",
        nargs="*",
        default=None,
        help="Languages to install servers for (default: auto-detect).",
    )
    lsp_setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be installed without doing it.",
    )

    routes_parser = subparsers.add_parser(
        "routes", help="Extract direct HTTP/API route inventory."
    )
    _add_project_args(routes_parser)
    routes_parser.add_argument(
        "--with-consumers",
        action="store_true",
        help="Scan for frontend/client consumers of each route.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="Validate runtime, prerequisites, and LSP server availability."
    )
    _add_project_args(doctor_parser)
    doctor_parser.add_argument(
        "--lsp",
        action="store_true",
        help="Also check LSP server availability and suggest install commands.",
    )

    state_map_parser = subparsers.add_parser(
        "state-map", help="Map state values, writers, and readers for an enum/type."
    )
    _add_project_args(state_map_parser)
    state_map_parser.add_argument(
        "--symbol", default=None, help="Symbol name (e.g. TaskStatus)."
    )
    state_map_parser.add_argument(
        "--query", default=None, help="Keywords to find relevant state definitions."
    )

    search_parser = subparsers.add_parser(
        "search", help="BM25 symbol search by natural language query."
    )
    _add_project_args(search_parser)
    search_parser.add_argument(
        "--query",
        "-q",
        required=True,
        help="Search query (natural language or keywords).",
    )
    search_parser.add_argument(
        "--top-k", type=int, default=20, help="Maximum results (default 20)."
    )

    build_parser_cmd = subparsers.add_parser(
        "build-binary", help="Build a one-file executable with PyInstaller."
    )
    build_parser_cmd.add_argument(
        "--output", default="dist", help="Directory for the final binary."
    )
    build_parser_cmd.add_argument("--name", default=CLI_NAME, help="Binary file name.")

    return parser


def _add_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        "-p",
        required=False,
        default=None,
        help="Project root path (absolute path recommended). Auto-detected from git if not specified.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help="Maximum number of files to scan.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="Print raw JSON output (default: True).",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        default=False,
        help="Print human-readable text output instead of JSON.",
    )


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
    from .commands.overview import run_overview, run_scan  # noqa: PLC0415
    from .commands.overview import run_hotspots  # noqa: PLC0415
    from .commands.symbol import run_call_chain, run_query_symbol  # noqa: PLC0415
    from .commands.symbol import run_file_detail, run_refs, run_state_map  # noqa: PLC0415
    from .commands.query import run_query, run_search  # noqa: PLC0415
    from .commands.impact import run_impact  # noqa: PLC0415
    from .commands.verify import run_verify, run_check, run_orphan  # noqa: PLC0415
    from .commands.cache import run_cache, run_diff  # noqa: PLC0415
    from .commands.routes import run_routes  # noqa: PLC0415
    from .commands.fix import run_fix, run_ready  # noqa: PLC0415
    from .commands.doctor import run_doctor, run_lsp_doctor, run_lsp_setup  # noqa: PLC0415
    from .commands.build import run_build_binary  # noqa: PLC0415

    parser = build_parser()
    try:
        args = parser.parse_args(_prepare_argv(argv))
    except SystemExit as exc:
        return int(exc.code or 0)

    # 处理 --no-json 参数：如果指定了 --no-json，则覆盖 --json 的默认值
    if getattr(args, "no_json", False):
        args.json = False

    command = args.command
    if command == "overview":
        if getattr(args, "quick", False):
            return run_scan(args.project, args.max_files, args.json)
        return run_overview(
            args.project,
            args.max_files,
            args.max_chars,
            args.json,
            with_heat=getattr(args, "with_heat", False),
            with_co_change=getattr(args, "with_co_change", False),
            granularity=getattr(args, "granularity", "auto"),
            co_change_days=getattr(args, "co_change_days", 30),
        )
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
        return run_query_symbol(
            args.project,
            args.max_files,
            args.symbol,
            args.file_path,
            args.max_chars,
            args.lsp_timeout,
            args.json,
        )
    if command == "query":
        return run_query(
            args.project,
            args.max_files,
            args.query,
            getattr(args, "max_result_files", 20),
            getattr(args, "max_symbols", 40),
            args.no_tests,
            args.json,
            args.paths,
            args.exclude,
            getattr(args, "context_lines", 2),
        )
    if command == "impact":
        return run_impact(
            args.project,
            args.max_files,
            args.files,
            getattr(args, "max_files", 20),
            args.json,
            getattr(args, "with_symbols", False),
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
            lsp_timeout=args.lsp_timeout,
            lsp_max_files=args.lsp_max_files,
            with_diff=args.with_diff,
            quick=args.quick,
            incremental=not getattr(args, "no_incremental", False),
            max_chars=args.max_chars,
        )
    if command == "file-detail":
        return run_file_detail(
            args.project,
            args.max_files,
            args.file_path,
            args.max_symbols,
            args.max_chars,
            getattr(args, "lsp_timeout", DEFAULT_LSP_TIMEOUT),
            args.json,
        )
    if command == "hotspots":
        return run_hotspots(args.project, args.max_files, args.limit, args.json)
    if command == "cache":
        return run_cache(args.project, args.action, getattr(args, "json", False))
    if command == "diff":
        return run_diff(args.project, args.json)
    if command == "refs":
        return run_refs(
            args.project,
            args.max_files,
            args.symbol,
            args.file_path,
            args.json,
            args.lsp_timeout,
        )
    if command == "orphan":
        return run_orphan(
            args.project, args.max_files, args.json, args.limit, args.min_confidence
        )
    if command == "check":
        return run_check(
            project=args.project,
            types=args.types,
            max_issues=args.max_issues,
            since_commit=args.since_commit,
            modified_files=args.modified_files,
            resolve_symbols=not args.no_symbols,
            lsp_timeout=args.lsp_timeout,
            lsp_max_files=args.lsp_max_files,
            as_json=getattr(args, "json", False),
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
        return run_doctor(
            args.project, getattr(args, "lsp", False), getattr(args, "json", False)
        )
    if command == "state-map":
        return run_state_map(
            args.project, args.max_files, args.symbol, args.query, args.json
        )
    if command == "search":
        return run_search(
            args.project, args.max_files, args.query, args.top_k, args.json
        )
    if command == "fix":
        return run_fix(
            args.project, getattr(args, "dry_run", False), getattr(args, "json", False)
        )
    if command == "ready":
        return run_ready(args.project, getattr(args, "json", False))
    if command == "build-binary":
        return run_build_binary(args.output, args.name)
    parser.error(f"unknown command: {command}")
    return 2


# Re-exports for test compatibility
from ..core import RepoMapEngine  # noqa: E402, F401  # re-exported for tests
from .handlers import _resolve_project, _scan_engine, clear_scan_cache  # noqa: E402, F401  # re-exported for tests
