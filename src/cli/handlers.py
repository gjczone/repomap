"""Command implementations and shared helpers.
Separated from CLI dispatch (cli.py)."""

from __future__ import annotations

import hashlib
import importlib.util as importlib_util
from datetime import datetime
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .. import json_dumps, json_dump, json_loads

from .. import (
    Edge,
    HttpRoute,
    RepoGraph,
    ScanStats,
    Symbol,
    call_reference_parts,
    get_session_cache_path,
    serialize_edge,
    serialize_symbol,
    SESSION_CACHE_VERSION,
    DEFAULT_OVERVIEW_JSON_HOTSPOTS,
    DEFAULT_OVERVIEW_JSON_MODULES,
    DEFAULT_OVERVIEW_JSON_READING_ORDER,
    DEFAULT_OVERVIEW_JSON_SUMMARY_FILES,
    DEFAULT_OVERVIEW_JSON_SUPPORTING_FILES,
    DEFAULT_OVERVIEW_JSON_SYMBOLS_PER_FILE,
    DEFAULT_FILE_DETAIL_MAX_SYMBOLS,
)
from ..ai import (
    _build_query_reading_order,
    _get_hot_files,
    _rank_symbols_for_file,
    _truncate_output,
    render_impact_report,
    render_query_report,
    render_routes_report,
    render_verify_report,
)
from ..check import RepoMapChecker
from ..core import RepoMapEngine
from ..gitignore import get_gitignore
from ..parser import EXT_TO_LANG
from ..ranking import GraphAnalyzer
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

CLI_NAME = "repomap"
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
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
    "tree_sitter_c",
    "tree_sitter_java",
    "tree_sitter_kotlin",
    "tree_sitter_swift",
    "tree_sitter_cpp",
    "tree_sitter_c_sharp",
    "tree_sitter_php",
    "tree_sitter_ruby",
    "repomap_lsp",
]

_SCAN_CACHE: dict[tuple, tuple] = {}
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_ARGS = 2
EXIT_NO_RESULTS = 3


def _resolve_project(project: str) -> str:
    project_path = Path(project).expanduser().resolve()
    if not project_path.is_dir():
        raise ValueError(f"project path is not a directory: {project_path}")
    if project_path == Path.home().resolve():
        print(
            f"[{CLI_NAME}] warning: project root is your home directory: {project_path}. "
            "Run from the intended project directory or pass --project explicitly.",
            file=sys.stderr,
        )
    return str(project_path)


def _normalize_project_relative_path(
    project_root: str | Path, value: str, *, must_exist: bool = False
) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("path is empty")
    if raw.startswith("-"):
        raise ValueError(f"unsafe path starts with '-': {value}")
    project_path = Path(project_root).resolve()
    input_path = Path(raw).expanduser()
    abs_path = (
        input_path.resolve()
        if input_path.is_absolute()
        else (project_path / input_path).resolve()
    )
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


def _normalize_project_relative_paths(
    project_root: str | Path, values: list[str], *, must_exist: bool = False
) -> list[str]:
    return [
        _normalize_project_relative_path(project_root, value, must_exist=must_exist)
        for value in values
    ]


def _normalize_path_prefix(project_root: str | Path, prefix: str) -> str:
    return _normalize_project_relative_path(
        project_root, prefix.rstrip("/"), must_exist=False
    )


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
    gitignore = get_gitignore(project_root)
    files: list[str] = []
    for root, dir_names, file_names in os.walk(project_root):
        rel_root = Path(root).relative_to(project_root)
        dir_names[:] = [
            n
            for n in dir_names
            if not gitignore.is_ignored(
                rel_root / n if str(rel_root) != "." else Path(n)
            )
        ]
        for file_name in file_names:
            suffix = Path(file_name).suffix.lower()
            if suffix not in EXT_TO_LANG:
                continue
            rel_path = (
                (rel_root / file_name).as_posix() if str(rel_root) != "." else file_name
            )
            if gitignore.is_ignored(rel_path):
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


def _scan_engine(
    project: str, max_files: int, incremental: bool = False
) -> RepoMapEngine:
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


def _engine_to_session_payload(
    project_root: str, fingerprint: str, engine: RepoMapEngine
) -> dict[str, Any]:
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
            file_path: [list(call_reference_parts(c)) for c in calls]
            for file_path, calls in engine.graph.file_calls.items()
        },
        "file_import_bindings": {
            file_path: [
                {
                    "local_name": b.local_name,
                    "imported_name": b.imported_name,
                    "module": b.module,
                    "line": b.line,
                    "kind": b.kind,
                }
                for b in bindings
            ]
            for file_path, bindings in engine.graph.file_import_bindings.items()
        },
        "file_exports": {
            file_path: [
                {
                    "exported_name": b.exported_name,
                    "source_name": b.source_name,
                    "module": b.module,
                    "line": b.line,
                    "kind": b.kind,
                }
                for b in exports
            ]
            for file_path, exports in engine.graph.file_exports.items()
        },
        "routes": [_route_payload(r) for r in engine.routes],
    }


def _restore_engine_from_session_payload(
    payload: dict[str, Any],
) -> RepoMapEngine | None:
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
            return_type=row.get("return_type", ""),
            params=row.get("params", ""),
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

        graph.file_import_bindings[file_path] = [JSImportBinding(**b) for b in bindings]

    for file_path, exports in payload.get("file_exports", {}).items():
        from .. import JSExportBinding

        graph.file_exports[file_path] = [JSExportBinding(**e) for e in exports]

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
    engine.routes = [HttpRoute(**r) for r in payload.get("routes", [])]
    return engine if engine.scan_state == "scanned" else None


def _load_session_engine(project_root: str, fingerprint: str) -> RepoMapEngine | None:
    cache_path = get_session_cache_path(project_root)
    if not cache_path.exists():
        return None
    try:
        payload = json_loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("project_root") != project_root:
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    return _restore_engine_from_session_payload(payload)


def _save_session_engine(
    project_root: str, fingerprint: str, engine: RepoMapEngine
) -> None:
    if engine.scan_state != "scanned":
        return
    cache_path = get_session_cache_path(project_root)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _engine_to_session_payload(project_root, fingerprint, engine)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_path.parent,
            prefix="session_scan.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json_dump(payload, handle, ensure_ascii=False, indent=2)
            tmp_path = Path(handle.name)
        tmp_path.replace(cache_path)
    except Exception:
        try:
            if tmp_path is not None and tmp_path.exists():
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
                str(engine.project_root),
                best_match.file,
                best_match.line,
                symbol,
                timeout=lsp_timeout,
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
    lines = [
        f"> Symbol `{symbol}` has multiple candidates; use `--file-path` to specify:"
    ]
    for item in candidates[:10]:
        lines.append(f"- `{item.file}:{item.line}` ({item.kind})")
    if len(candidates) > 10:
        lines.append(f"- ... {len(candidates) - 10} more candidates")
    lines.append("\nTip: use `--file-path <file>` to specify the target file, e.g.:")
    lines.append(
        f"  repomap call-chain --symbol {symbol} --file-path {candidates[0].file}"
    )
    return None, "\n".join(lines), resolution_tier


def _group_symbol_matches(
    results: list[Any], symbol: str
) -> tuple[list[Any], list[Any]]:
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
            lines.append(
                f"- `{caller.name}` ({caller.kind}) — `{caller.file}:{caller.line}`"
            )
        if len(callers) > 20:
            lines.append(f"- ... {len(callers) - 20} more")
    else:
        lines.append("- (None — entry point)")

    callees = chain["callees"]
    lines.append(f"\n### Calls（{len(callees)}）\n")
    if callees:
        for callee in callees[:20]:
            lines.append(
                f"- `{callee.name}` ({callee.kind}) — `{callee.file}:{callee.line}`"
            )
        if len(callees) > 20:
            lines.append(f"- ... {len(callees) - 20} more")
    else:
        lines.append("- (None — leaf function)")

    return "\n".join(lines)


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
            lines.insert(
                6, f"- max_files truncated: {engine.scan_stats.truncated_files}"
            )
        for item in hot:
            lines.append(
                f"  - `{item['file']}` — {item['symbol_count']} symbols ({item['risk']} risk)"
            )
        lines.append(
            "\n> Next: run `repomap overview --project <path>` for a full project map."
        )
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


def run_overview(
    project: str,
    max_files: int,
    max_chars: int,
    as_json: bool,
    with_heat: bool = False,
    with_co_change: bool = False,
    granularity: str = "auto",
    co_change_days: int = 30,
) -> int:
    try:
        engine = _scan_engine(project, max_files)

        if as_json:
            payload = {
                "project_root": str(engine.project_root),
                "scan_stats": _scan_stats_payload(engine),
                "entry_points": engine.entry_points(),
                "hotspots": engine.hotspots(DEFAULT_OVERVIEW_JSON_HOTSPOTS),
                "reading_order": engine.suggested_reading_order(
                    DEFAULT_OVERVIEW_JSON_READING_ORDER
                ),
                "modules": engine.module_summary(DEFAULT_OVERVIEW_JSON_MODULES),
                "summary_symbols": engine.summary_symbols(
                    DEFAULT_OVERVIEW_JSON_SUMMARY_FILES,
                    DEFAULT_OVERVIEW_JSON_SYMBOLS_PER_FILE,
                ),
                "supporting_files": engine.supporting_files(
                    DEFAULT_OVERVIEW_JSON_SUPPORTING_FILES
                ),
                "hot_files": list(_get_hot_files(str(engine.project_root)))
                if with_heat
                else [],
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(
            engine.render_overview(
                max_chars,
                with_heat=with_heat,
                with_co_change=with_co_change,
                granularity=granularity,
                co_change_days=co_change_days,
            )
        )
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
        selected, error, tier = _select_symbol_match(
            engine, symbol, file_path=file_path
        )
        if error:
            print(error, file=sys.stderr)
            return 1
        assert selected is not None
        if as_json:
            chain = engine.call_chain(selected.id, direction, depth)
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
                "callers": [
                    _format_symbol_ref(engine, item.id) for item in chain["callers"]
                ],
                "callees": [
                    _format_symbol_ref(engine, item.id) for item in chain["callees"]
                ],
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if direction != "both":
            data = engine.call_chain(selected.id, direction, depth)
            lines = [f"## Call Chain — `{selected.name}`\n"]
            for item in data[direction]:
                lines.append(f"- `{item.name}` ({item.file}:{item.line})")
            print(_truncate_output("\n".join(lines), max_chars))
            return 0
        print(
            _truncate_output(
                _render_selected_call_chain(engine, selected, depth), max_chars
            )
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] call-chain failed: {exc}", file=sys.stderr)
        return 1


def _collect_lsp_evidence_for_symbol(
    engine: RepoMapEngine, symbol: Any, timeout: float
) -> dict[str, Any]:
    from ..lsp import collect_lsp_symbol_evidence, collect_lsp_hover, run_result_to_dict

    run = collect_lsp_symbol_evidence(
        engine.project_root,
        symbol.file,
        symbol.line,
        symbol.name,
        timeout=timeout,
    )
    result = run_result_to_dict(run)
    # 附加 hover 信息
    if run.status == "ok":
        hover = collect_lsp_hover(
            engine.project_root, symbol.file, symbol.line, symbol.name, timeout=timeout
        )
        if hover:
            result["hover"] = {
                "file": hover.file,
                "line": hover.line,
                "col": hover.col,
                "contents": hover.contents,
            }
    return result


def _format_lsp_evidence(evidence: dict[str, Any]) -> list[str]:
    lines = ["", "### LSP evidence", ""]
    lines.append(f"- Status: {evidence.get('status')}")
    if evidence.get("server"):
        lines.append(f"- Server: {evidence['server']}")
    if evidence.get("reason"):
        lines.append(f"- Reason: {evidence['reason']}")
    # hover 信息
    hover = evidence.get("hover")
    if hover and hover.get("contents"):
        contents = hover["contents"].strip()
        if contents:
            lines.append(f"- Hover: {contents[:300]}")
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
    as_json: bool = False,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        results = engine.query_symbol(symbol)
        if file_path:
            results = [item for item in results if item.file == file_path]
        if not results:
            if as_json:
                print(
                    json_dumps(
                        {"matches": [], "query": symbol, "file_filter": file_path},
                        ensure_ascii=False,
                    )
                )
                return 0
            print(f"> No matches found for `{symbol}`", file=sys.stderr)
            return EXIT_NO_RESULTS
        exact_matches, fuzzy_matches = _group_symbol_matches(results, symbol)

        if as_json:

            def _symbol_item(item):
                d = {
                    "name": item.name,
                    "kind": item.kind,
                    "file": item.file,
                    "line": item.line,
                    "pagerank": item.pagerank,
                }
                if item.signature:
                    d["signature"] = item.signature
                if item.return_type:
                    d["return_type"] = item.return_type
                if item.params:
                    d["params"] = item.params
                return d

            payload = {
                "query": symbol,
                "total_results": len(results),
                "exact_matches": [_symbol_item(item) for item in exact_matches[:20]],
                "fuzzy_matches": [_symbol_item(item) for item in fuzzy_matches[:20]],
            }
            if file_path:
                payload["file_filter"] = file_path
            if with_lsp and (exact_matches or results):
                selected = (exact_matches or results)[0]
                payload["lsp"] = _collect_lsp_evidence_for_symbol(
                    engine, selected, lsp_timeout
                )
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        lines = [f"Found {len(results)} matching results.\n"]
        if file_path:
            lines.append(f"Filtered by file: `{file_path}`\n")
        if len(exact_matches) > 1 and not file_path:
            lines.append(
                f"{len(exact_matches)} exact candidates; use `--file-path` to narrow.\n"
            )

        if exact_matches:
            lines.append(f"## Exact matches `{symbol}` ({len(exact_matches)})\n")
            for item in exact_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(
                    f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}"
                )
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")
                if item.return_type:
                    lines.append(f"  - returns: `{item.return_type}`")
                if item.params:
                    lines.append(f"  - params: `{item.params}`")

        if fuzzy_matches:
            lines.append(f"\n## Fuzzy matches ({len(fuzzy_matches)})\n")
            for item in fuzzy_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(
                    f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}"
                )
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")
                if item.return_type:
                    lines.append(f"  - returns: `{item.return_type}`")
                if item.params:
                    lines.append(f"  - params: `{item.params}`")

        if len(results) > 10 and (len(exact_matches) > 10 or len(fuzzy_matches) > 10):
            lines.append("\n> Many results; use `--file-path` to narrow.")
        if with_lsp:
            selected = (exact_matches or results)[0]
            lines.extend(
                _format_lsp_evidence(
                    _collect_lsp_evidence_for_symbol(engine, selected, lsp_timeout)
                )
            )
        print(_truncate_output("\n".join(lines), max_chars))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] query-symbol failed: {exc}", file=sys.stderr)
        return 1


def run_file_detail(
    project: str,
    max_files: int,
    file_path: str,
    max_symbols: int,
    max_chars: int,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
    as_json: bool = False,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        normalized_file_path = _normalize_project_relative_path(
            engine.project_root, file_path, must_exist=True
        )

        if max_symbols == DEFAULT_FILE_DETAIL_MAX_SYMBOLS:
            file_symbol_count = len(
                engine.graph.file_symbols.get(normalized_file_path, [])
            )
            if file_symbol_count > 50:
                max_symbols = min(file_symbol_count, 50)
            elif file_symbol_count > 20:
                max_symbols = file_symbol_count

        if as_json:
            symbols = []
            for sid in engine.graph.file_symbols.get(normalized_file_path, []):
                sym = engine.graph.symbols.get(sid)
                if not sym:
                    continue
                s = {
                    "name": sym.name,
                    "kind": sym.kind,
                    "line": sym.line,
                    "pagerank": sym.pagerank,
                }
                if sym.signature:
                    s["signature"] = sym.signature
                if sym.return_type:
                    s["return_type"] = sym.return_type
                if sym.params:
                    s["params"] = sym.params
                symbols.append(s)
            payload = {
                "file": normalized_file_path,
                "symbol_count": len(symbols),
                "symbols": sorted(
                    symbols, key=lambda x: x.get("pagerank", 0), reverse=True
                )[:max_symbols],
                "imports": engine.graph.file_imports.get(normalized_file_path, []),
                "calls": [
                    list(c)
                    for c in engine.graph.file_calls.get(normalized_file_path, [])
                ],
            }
            if with_lsp:
                from dataclasses import asdict as dc_asdict
                from ..lsp import collect_lsp_symbol_tree

                lsp_tree = collect_lsp_symbol_tree(
                    engine.project_root, normalized_file_path, timeout=lsp_timeout
                )
                if lsp_tree:
                    payload["lsp_symbol_tree"] = [dc_asdict(item) for item in lsp_tree]
                else:
                    payload["lsp_symbol_tree"] = []
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        lsp_tree = None
        if with_lsp:
            from ..lsp import collect_lsp_symbol_tree

            lsp_tree = collect_lsp_symbol_tree(
                engine.project_root, normalized_file_path, timeout=lsp_timeout
            )

        print(
            engine.render_file_detail(
                normalized_file_path,
                max_symbols=max_symbols,
                max_chars=max_chars,
                lsp_symbol_tree=lsp_tree,
            )
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] file-detail failed: {exc}", file=sys.stderr)
        return 1


def run_routes(
    project: str, max_files: int, as_json: bool, with_consumers: bool = False
) -> int:
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
                        {
                            "file": c.file,
                            "line": c.line,
                            "context": c.context,
                            "confidence": c.confidence,
                            "match_type": c.match_type,
                        }
                        for c in clist
                    ]
                payload["consumers"] = consumer_json
            print(json_dumps(payload, ensure_ascii=False, indent=2))
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
            lines.append(
                f"{index}. {risk_mark[item['risk']]} `{item['file']}` — **{item['symbol_count']}** symbols"
            )
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
        print(json_dumps(result, ensure_ascii=False, indent=2))
        return 0
    lines = ["## Change Detection\n"]
    lines.append(
        f"**Compare**: {result.get('last_scan', 'unknown')} → {result.get('scan_time', datetime.now().isoformat())}\n"
    )
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
            src_name = (
                change["from"].split("::")[-2]
                if "::" in change["from"]
                else change["from"]
            )
            tgt_name = (
                change["to"].split("::")[-2] if "::" in change["to"] else change["to"]
            )
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
    context_lines: int = 2,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        analysis = engine.file_analysis()

        # 过滤搜索范围
        candidate_files = list(engine.graph.file_symbols.keys())
        allowed: set[str] = set()
        excluded: set[str] = set()
        if paths:
            allowed = {
                _normalize_path_prefix(engine.project_root, p)
                for p in paths.split(",")
                if p.strip()
            }
            candidate_files = [
                f
                for f in candidate_files
                if any(_path_matches_prefix(f, a) for a in allowed)
            ]
        if exclude:
            excluded = {
                _normalize_path_prefix(engine.project_root, e)
                for e in exclude.split(",")
                if e.strip()
            }
            candidate_files = [
                f
                for f in candidate_files
                if not any(_path_matches_prefix(f, e) for e in excluded)
            ]
        if no_tests:
            candidate_files = [f for f in candidate_files if not is_test_like_file(f)]

        # 计算高频词权重（命中文件过多的关键词降权）
        kw_weights = compute_keyword_weights(
            query.lower().split(), candidate_files, engine.graph
        )

        # 主题评分
        matches: list[FileMatch] = []
        for file_path in candidate_files:
            file_data = analysis.get(file_path, {})
            score = topic_score(
                query, file_path, file_data, engine.graph, keyword_weights=kw_weights
            )
            if score > 0:
                role = classify_file_role(file_path, engine.graph)
                reasons = _build_match_reasons(
                    query, file_path, engine.graph, engine.list_routes()
                )
                matches.append(
                    FileMatch(path=file_path, role=role, score=score, reasons=reasons)
                )

        # 调用邻居传播：高分文件的调用者/被调用者文件获得传播分数
        matches = _propagate_call_neighbor_scores(
            matches, candidate_files, engine.graph
        )

        matches.sort(key=lambda m: (-m.score, m.path))

        # Fallback: expand query keywords if too few direct matches
        is_fallback = False
        if len(matches) < 3:
            words = query.lower().split()
            expanded_keywords = expand_keywords(words)
            expanded_terms = [kw for kw, _ in expanded_keywords]
            if len(expanded_terms) > len(words):
                expanded_query = " ".join(expanded_terms)
                expanded_kw_weights = compute_keyword_weights(
                    expanded_query.lower().split(), candidate_files, engine.graph
                )
                expanded_matches: list[FileMatch] = []
                for file_path in candidate_files:
                    file_data = analysis.get(file_path, {})
                    score = topic_score(
                        expanded_query,
                        file_path,
                        file_data,
                        engine.graph,
                        keyword_weights=expanded_kw_weights,
                    )
                    if score > 0:
                        role = classify_file_role(file_path, engine.graph)
                        reasons = _build_match_reasons(
                            expanded_query,
                            file_path,
                            engine.graph,
                            engine.list_routes(),
                        )
                        expanded_matches.append(
                            FileMatch(
                                path=file_path, role=role, score=score, reasons=reasons
                            )
                        )
                expanded_matches = _propagate_call_neighbor_scores(
                    expanded_matches, candidate_files, engine.graph
                )
                expanded_matches.sort(key=lambda m: (-m.score, m.path))
                if len(expanded_matches) > len(matches):
                    matches = expanded_matches
                    is_fallback = True

            # If still too few results, fall back to hotspots
            if len(matches) < 3:
                hotspot_entries = engine.hotspots(20)
                hotspot_matches: list[FileMatch] = []
                for entry in hotspot_entries:
                    file_path = entry["file"]
                    # Respect path filters
                    if allowed and not any(
                        _path_matches_prefix(file_path, a) for a in allowed
                    ):
                        continue
                    if excluded and any(
                        _path_matches_prefix(file_path, e) for e in excluded
                    ):
                        continue
                    if no_tests and is_test_like_file(file_path):
                        continue
                    hotspot_matches.append(
                        FileMatch(
                            path=file_path,
                            role="hotspot",
                            score=float(entry.get("symbol_count", 0)),
                            reasons=["(fallback — no direct matches found)"],
                        )
                    )
                if hotspot_matches:
                    matches = hotspot_matches
                    is_fallback = True
            else:
                if is_fallback:
                    for m in matches:
                        m.reasons.append("(fallback — no direct matches found)")

        top_matches = matches[:max_result_files]

        # 找相关测试
        tests: list[TestMatch] = []
        if not no_tests:
            target_files = [
                m.path for m in top_matches if not is_test_like_file(m.path)
            ]
            tests = find_related_tests(
                target_files, engine.graph, analysis, str(engine.project_root)
            )

        if as_json:
            payload = {
                "command": "query",
                "project": str(engine.project_root),
                "query": query,
                "scanStats": _scan_stats_payload(engine),
                "result": {
                    "filesConsidered": len(candidate_files),
                    "matchedFiles": len(matches),
                    "readingOrder": _build_query_reading_order(
                        top_matches, analysis, max_result_files
                    ),
                    "coreFiles": [
                        {
                            "path": m.path,
                            "role": m.role,
                            "score": m.score,
                            "reasons": m.reasons,
                        }
                        for m in top_matches
                        if m.score >= 30 and not is_test_like_file(m.path)
                    ],
                    "supportingFiles": [
                        {
                            "path": m.path,
                            "role": m.role,
                            "score": m.score,
                            "reasons": m.reasons,
                        }
                        for m in top_matches
                        if m.score < 30
                    ],
                    "tests": [
                        {
                            "testFile": t.test_file,
                            "targetFile": t.target_file,
                            "confidence": t.confidence,
                            "reason": t.reason,
                        }
                        for t in tests
                    ],
                    "symbols": _query_symbols_json(
                        engine, top_matches, max_result_symbols
                    ),
                },
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        print(
            render_query_report(
                engine,
                query,
                top_matches,
                tests,
                max_result_files,
                max_result_symbols,
                context_lines=context_lines,
            )
        )
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


def _build_match_reasons(
    query: str, file_path: str, graph: RepoGraph, routes: list | None = None
) -> list[str]:
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
            label = (
                f"synonym hit: {source} -> {kw} (filename)"
                if source
                else f"filename hit: {kw}"
            )
            reasons.append(label)
        elif any(kw in t for t in tokens):
            label = (
                f"synonym hit: {source} -> {kw} (token)"
                if source
                else f"filename token hit: {kw}"
            )
            reasons.append(label)

    # Symbol name hits
    for sid in graph.file_symbols.get(file_path, []):
        sym = graph.symbols.get(sid)
        if not sym:
            continue
        for kw, source in expanded:
            if kw in sym.name.lower():
                tag = (
                    f"synonym hit: {source} -> {kw}"
                    if source
                    else f"symbol hit: {sym.name}"
                )
                if tag not in reasons:
                    reasons.append(tag)
        if len(reasons) >= 5:
            break

    # Route hits
    if routes:
        for r in routes:
            rel_file = file_path
            if hasattr(r, "file") and (
                r.file == rel_file
                or rel_file.endswith(r.file)
                or r.file.endswith(rel_file)
            ):
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
            entry: dict[str, Any] = {
                "name": sym["name"],
                "kind": sym["kind"],
                "file": m.path,
                "line": sym["line"],
                "role": classify_file_role(m.path, engine.graph),
            }
            sym_end = sym.get("end_line", sym["line"])
            if sym_end > 0:
                entry["endLine"] = sym_end
            if (sym_end - sym["line"]) > 100:
                entry["chunkRange"] = f"L{sym['line']}-L{sym_end}"
            result.append(entry)
    return result


def _impact_key_symbols(
    engine: RepoMapEngine, target_files: list[str], limit_per_file: int = 8
) -> list[dict[str, Any]]:
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
            result.append(
                {
                    "name": symbol.name,
                    "kind": symbol.kind,
                    "file": symbol.file,
                    "line": symbol.line,
                    "pagerank": symbol.pagerank,
                    "incomingCount": len(engine.graph.incoming.get(symbol.id, [])),
                    "outgoingCount": len(engine.graph.outgoing.get(symbol.id, [])),
                    "signature": symbol.signature,
                }
            )
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


def _impact_lsp_hint(
    project_root: str | Path, target_files: list[str]
) -> dict[str, Any]:
    try:
        from ..lsp import detect_lsp_server, detection_to_dict, language_for_file
    except Exception as exc:
        return {
            "available": False,
            "servers": [],
            "suggestedCommands": [],
            "reason": str(exc),
        }

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
        suggested.append(
            f"repomap check --project {project_root} --modified-file {files_arg}"
        )
        suggested.append(
            f"repomap refs --project {project_root} --symbol <symbol> --file-path <file>"
        )
    return {"available": available, "servers": servers, "suggestedCommands": suggested}


def _impact_type_level(
    engine: RepoMapEngine,
    target_files: list[str],
) -> list[dict[str, Any]]:
    """Detect type-level impact: return type / parameter type changes for exported symbols."""
    results: list[dict[str, Any]] = []

    for f in target_files:
        for sid in engine.graph.file_symbols.get(f, []):
            sym = engine.graph.symbols.get(sid)
            if sym is None:
                continue

            # Check if symbol has callers outside the changed files
            target_set = set(target_files)
            external_callers: list[str] = []
            for edge in engine.graph.incoming.get(sid, []):
                caller = engine.graph.symbols.get(edge.source)
                if caller and caller.file not in target_set:
                    external_callers.append(f"{caller.name} ({caller.file})")

            if not external_callers:
                continue

            entry: dict[str, Any] = {
                "symbol": sym.name,
                "file": sym.file,
                "line": sym.line,
                "kind": sym.kind,
                "affected_callers": external_callers[:10],
                "return_type_changed": False,
                "param_type_changed": False,
                "note": "",
            }

            # Compare symbol signature with what callers might expect
            if sym.return_type:
                for edge in engine.graph.incoming.get(sid, []):
                    caller = engine.graph.symbols.get(edge.source)
                    if (
                        caller
                        and caller.return_type
                        and caller.return_type != sym.return_type
                    ):
                        entry["return_type_changed"] = True
                        entry["note"] = (
                            f"Return type `{sym.return_type}` may not match "
                            f"caller `{caller.name}` expectation `{caller.return_type}`"
                        )
                        break

            if sym.signature:
                for edge in engine.graph.incoming.get(sid, []):
                    caller = engine.graph.symbols.get(edge.source)
                    if (
                        caller
                        and caller.signature
                        and caller.signature != sym.signature
                    ):
                        entry["param_type_changed"] = True
                        if not entry["note"]:
                            entry["note"] = (
                                f"Signature `{sym.signature}` may conflict with "
                                f"caller `{caller.name}` signature `{caller.signature}`"
                            )
                        break

            results.append(entry)

    return results


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

        target_files = _normalize_project_relative_paths(
            engine.project_root, target_files
        )

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
        tests = find_related_tests(
            target_files, engine.graph, analysis, str(engine.project_root)
        )

        # 风险评估
        risk_level, risk_notes = _assess_risk(target_files, set(affected_files), engine)

        # Type-level impact analysis
        type_impacts = _impact_type_level(engine, target_files)

        affected_list = [(f, why, conf) for f, (why, conf) in affected_files.items()]
        # 按影响严重程度排序：受影响文件中符号的外部调用者越多越靠前
        affected_list.sort(
            key=lambda x: (
                {"high": 3, "medium": 2, "low": 1}.get(x[2], 0),
                -_affected_severity(x[0], engine),
                x[0],
            ),
            reverse=True,
        )
        affected_list = sorted(
            affected_list,
            key=lambda x: (
                -{"high": 3, "medium": 2, "low": 1}.get(x[2], 0),
                -_affected_severity(x[0], engine),
            ),
        )
        affected_list = affected_list[:max_affected_files]
        key_symbols = _impact_key_symbols(engine, target_files) if with_symbols else []
        read_next = _impact_read_next(target_files, affected_list, tests)
        lsp_hint = (
            _impact_lsp_hint(engine.project_root, target_files) if with_symbols else {}
        )

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
                        {
                            "testFile": t.test_file,
                            "targetFile": t.target_file,
                            "confidence": t.confidence,
                            "reason": t.reason,
                        }
                        for t in tests
                    ],
                    "riskLevel": risk_level,
                    "riskNotes": risk_notes,
                    "keySymbols": key_symbols,
                    "readNext": read_next,
                    "lspHint": lsp_hint,
                    "typeImpacts": type_impacts,
                },
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        print(
            render_impact_report(
                engine,
                target_files,
                affected_list,
                tests,
                risk_level,
                risk_notes,
                key_symbols=key_symbols,
                read_next=read_next,
                lsp_hint=lsp_hint,
            )
        )
        # Print type-level impacts
        if type_impacts:
            print("\n## Type-Level Impact\n")
            for ti in type_impacts:
                print(f"- **{ti['symbol']}** (`{ti['file']}:{ti['line']}`)")
                if ti.get("return_type_changed"):
                    print("  - Return type may differ from callers' expectations")
                if ti.get("param_type_changed"):
                    print("  - Parameter types may differ from callers' expectations")
                if ti.get("affected_callers"):
                    print(
                        f"  - Affected callers: {', '.join(ti['affected_callers'][:5])}"
                    )
                if ti.get("note"):
                    print(f"  - {ti['note']}")
                print("")
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
            risk_notes.append(
                f"`{f}` associated with {nc} files, very high blast radius"
            )
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
    risk_keywords_high = [
        "auth",
        "token",
        "session",
        "password",
        "security",
        "migration",
        "database",
        "schema",
        "persistence",
    ]
    risk_keywords_medium = [
        "terminal",
        "websocket",
        "pty",
        "input",
        "config",
        "build",
        "deploy",
        "ci",
    ]
    all_paths = " ".join(target_files + list(affected_files)).lower()
    for kw in risk_keywords_high:
        if kw in all_paths:
            domain_risk += 3
    for kw in risk_keywords_medium:
        if kw in all_paths:
            domain_risk += 1
    if domain_risk >= 6:
        risk_notes.append("touches high-risk domain (auth/security/data persistence)")
    elif domain_risk >= 3:
        risk_notes.append("touches medium-risk domain (terminal/config/build)")
    total_score += domain_risk

    # 第3层：变更类型风险
    change_type_risk = 0
    for f in target_files:
        if is_test_like_file(f):
            pass  # 只改测试不改实现，低风险
        elif any(
            f.endswith(ext) for ext in [".config.ts", ".config.js", "package.json"]
        ):
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
        if len(line) >= 3 and line[2] == " ":
            path = line[3:]
        elif len(line) >= 2 and line[1] == " ":
            path = line[2:]
        else:
            continue
        if " -> " in path:
            path = path.split(" -> ")[-1]
        path = path.strip()
        if path:
            paths.append(path)
    return paths


def _child_git_project_candidates(project_path: Path, limit: int = 8) -> list[Path]:
    candidates: list[Path] = []
    try:
        children = sorted(project_path.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        if (child / ".git").exists():
            candidates.append(child.resolve())
            if len(candidates) >= limit:
                break
    return candidates


def _collect_changed_files(project_root: str | Path) -> tuple[list[str], str | None]:
    from ..git_backend import GitBackend

    project_path = Path(project_root).resolve()
    git = GitBackend(str(project_path))
    git_root = git.show_toplevel()
    if not git_root:
        message = (
            f"not a git repository: {project_path}. "
            "Run from the intended project directory or pass --project explicitly."
        )
        candidates = _child_git_project_candidates(project_path)
        if candidates:
            candidate_lines = "\n".join(
                f"- --project {candidate}" for candidate in candidates
            )
            message = (
                f"{message}\n"
                "LLM action: select the intended project root, then re-run the same repomap command with exactly one --project argument.\n"
                f"Candidate --project arguments:\n{candidate_lines}"
            )
        return [], message

    status_lines = git.status_porcelain()
    changed_files: list[str] = []
    for stripped in _parse_git_status_porcelain_paths("\n".join(status_lines)):
        abs_path = Path(git_root, stripped).resolve()
        try:
            changed_files.append(abs_path.relative_to(project_path).as_posix())
        except ValueError:
            pass
    return changed_files, None


def _detect_contract_risks(
    engine: RepoMapEngine, changed_files: list[str]
) -> list[dict[str, str]]:
    """Detect contract-level risks from changed files: route changes, signature changes, test gaps."""
    warnings: list[dict[str, str]] = []
    routes = engine.list_routes()
    changed_set = set(changed_files)

    # Route/API risks
    for route in routes:
        if route.file in changed_set:
            warnings.append(
                {
                    "level": "MED",
                    "message": f"Route `{route.method} {route.path}` (handler in `{route.file}`) changed; review consumers and related tests.",
                }
            )

    # Symbol/public surface risks: check exported/public symbols in changed files
    for file_path in changed_files:
        for sid in engine.graph.file_symbols.get(file_path, []):
            sym = engine.graph.symbols.get(sid)
            if not sym:
                continue
            # Count cross-file incoming edges only (import + call references from other files)
            cross_file_refs = [
                e
                for e in engine.graph.incoming.get(sid, [])
                if engine.graph.symbols.get(e.source)
                and engine.graph.symbols[e.source].file != sym.file
            ]
            ref_count = len(cross_file_refs)
            if sym.visibility in ("exported", "public") and ref_count >= 3:
                warnings.append(
                    {
                        "level": "MED",
                        "message": f"Exported symbol `{sym.name}` in `{sym.file}` has {ref_count} cross-file references.",
                    }
                )
            elif ref_count >= 10:
                warnings.append(
                    {
                        "level": "MED",
                        "message": f"Heavily referenced symbol `{sym.name}` `({sym.kind})` in `{sym.file}` changed; {ref_count} cross-file references.",
                    }
                )

    # Enum/type risks
    for file_path in changed_files:
        for sid in engine.graph.file_symbols.get(file_path, []):
            sym = engine.graph.symbols.get(sid)
            if sym and sym.kind in ("enum", "type", "struct", "class"):
                cross_file_refs = [
                    e
                    for e in engine.graph.incoming.get(sid, [])
                    if engine.graph.symbols.get(e.source)
                    and engine.graph.symbols[e.source].file != sym.file
                ]
                if cross_file_refs:
                    warnings.append(
                        {
                            "level": "MED",
                            "message": f"Type `{sym.name}` `({sym.kind})` in `{sym.file}` changed; {len(cross_file_refs)} cross-file references.",
                        }
                    )

    # Test/implementation mismatch
    test_files = [f for f in changed_files if is_test_like_file(f)]
    impl_files = [
        f
        for f in changed_files
        if not is_test_like_file(f)
        and not f.endswith((".md",))
        and "dist/" not in f
        and "docs/" not in f
    ]
    if test_files and not impl_files:
        warnings.append(
            {
                "level": "LOW",
                "message": f"Only test files changed ({len(test_files)} file(s)); verify tests are intentional.",
            }
        )
    if impl_files and not test_files:
        warnings.append(
            {
                "level": "MED",
                "message": f"Implementation file(s) changed ({len(impl_files)} file(s)) without related tests.",
            }
        )

    # Config/runtime risks
    config_patterns = [
        ".env",
        "config",
        "Dockerfile",
        "Makefile",
        "migration",
        "schema",
    ]
    config_files = [
        f
        for f in changed_files
        if any(p in f.lower() for p in config_patterns) and not f.endswith(".md")
    ]
    if config_files:
        warnings.append(
            {
                "level": "MED",
                "message": f"Config/runtime files changed: {', '.join(f'`{f}`' for f in config_files[:3])}.",
            }
        )

    return warnings


def _diff_risk_evidence(
    engine: RepoMapEngine, changed_files: list[str]
) -> dict[str, Any]:
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

    affected_list = [
        (file_path, why, confidence)
        for file_path, (why, confidence) in affected_files_dict.items()
    ]
    affected_list.sort(key=lambda item: (item[2], item[0]))

    source_files = [
        file_path for file_path in changed_files if not is_test_like_file(file_path)
    ]
    tests = find_related_tests(
        source_files, engine.graph, analysis, str(engine.project_root)
    )
    risk_level, risk_reasons = _assess_risk(
        source_files, set(file_path for file_path, _, _ in affected_list), engine
    )

    missing_checks: list[str] = []
    all_exts = set(Path(file_path).suffix for file_path in changed_files)
    if ".ts" in all_exts or ".tsx" in all_exts:
        if not any(test.test_file.endswith((".ts", ".tsx")) for test in tests):
            missing_checks.append(
                "No frontend test file changes detected; consider adding frontend tests"
            )
    if ".py" in all_exts:
        if not any(test.test_file.endswith(".py") for test in tests):
            missing_checks.append(
                "No Python test file changes detected; consider adding backend tests"
            )

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
        return {
            "enabled": True,
            "status": "skipped",
            "runs": [],
            "summary": {},
            "reason": "no changed files",
        }
    try:
        from ..lsp import collect_lsp_diagnostics, run_result_to_dict

        runs = collect_lsp_diagnostics(
            project_root, changed_files, timeout=timeout, max_files=max_files
        )
        run_dicts = [run_result_to_dict(run) for run in runs]
        total_errors = sum(
            1 for run in runs for item in run.diagnostics if item.severity == "error"
        )
        total_warnings = sum(
            1 for run in runs for item in run.diagnostics if item.severity != "error"
        )
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
        return {
            "enabled": True,
            "status": "failed",
            "runs": [],
            "summary": {},
            "reason": str(exc),
        }


def _verify_graph_diff_payload(
    project_root: str, enabled: bool, incoming_map: dict | None = None
) -> dict[str, Any]:
    if not enabled:
        return {
            "enabled": False,
            "status": "skipped",
            "summary": {},
            "breakingChanges": [],
        }
    result = diff_project(project_root)
    if "error" in result:
        return {
            "enabled": True,
            "status": "skipped",
            "summary": {},
            "breakingChanges": [],
            "reason": result["error"],
        }
    # 如果提供了 incoming_map，二次调用带调用者分析的 compare
    if incoming_map is not None:
        from ..toolkit import load_cache
        from .. import compare_graph_snapshots

        cache = load_cache(project_root)
        if cache:
            current_symbols, current_edges = scan_project(project_root, max_files=5000)
            enriched = compare_graph_snapshots(
                current_symbols=current_symbols,
                current_edges=current_edges,
                previous_symbols=cache.symbols,
                previous_edges=cache.edges,
                incoming_map=incoming_map,
            )
            breaking = [
                ms
                for ms in enriched.get("modified_symbols", [])
                if ms.get("risk") in ("HIGH", "MEDIUM") and ms.get("signature_changed")
            ]
            result["breakingChanges"] = breaking[:20]
    if "breakingChanges" not in result:
        result["breakingChanges"] = []
    summary = result.get("summary", {})
    changed = any(
        summary.get(key, 0)
        for key in ("added", "removed", "modified", "edges_added", "edges_removed")
    )
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
    # risk_level 表示变更影响面，不等于未解决风险；只有缺证据或破坏性图谱变化才阻断交付。
    if missing_checks or graph_diff_payload.get("breakingChanges"):
        return "warning"
    check_status = check_payload.get("status")
    if check_status == "warning":
        return "warning"
    if check_status == "unknown" and lsp_payload.get("status") != "passed":
        return "warning"
    return "passed"


def _print_missed_files_section(
    engine: RepoMapEngine,
    changed_files: list[str],
) -> None:
    """Print potentially missed files: callers not in diff + co-change neighbors."""
    print("\n### Potentially missed files\n")

    # 1. For each changed file's symbols, find callers NOT in the git diff
    changed_set = set(changed_files)
    missed_callers: dict[str, list[str]] = {}
    for f in changed_files:
        for sid in engine.graph.file_symbols.get(f, []):
            sym = engine.graph.symbols.get(sid)
            if sym is None:
                continue
            for edge in engine.graph.incoming.get(sid, []):
                caller = engine.graph.symbols.get(edge.source)
                if caller and caller.file not in changed_set:
                    missed_callers.setdefault(caller.file, []).append(
                        f"{sym.name} (via {caller.name})"
                    )

    if missed_callers:
        print("Callers of changed symbols NOT in git diff:")
        for caller_file, reasons in sorted(missed_callers.items()):
            unique_reasons = list(dict.fromkeys(reasons))[:3]
            print(f"  - `{caller_file}` — called by: {', '.join(unique_reasons)}")
    else:
        print("  (no callers outside git diff)")

    # 2. Co-change neighbors
    try:
        from ..topic import get_co_change_neighbors

        co_change_found = False
        for f in changed_files:
            neighbors = get_co_change_neighbors(str(engine.project_root), f, top_n=3)
            if neighbors:
                if not co_change_found:
                    print(
                        "\nCo-change neighbors (files that frequently change together):"
                    )
                    co_change_found = True
                for neighbor_file, count in neighbors:
                    if neighbor_file not in changed_set:
                        print(
                            f"  - `{neighbor_file}` — co-changed {count} times with `{f}`"
                        )
        if not co_change_found:
            print("\n  (no co-change neighbors found)")
    except Exception:
        pass
    print("")


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
            check_payload = {
                "status": "skipped",
                "summary": {},
                "runs": [],
                "reason": "verify --quick",
            }
            lsp_payload = {
                "enabled": False,
                "status": "skipped",
                "runs": [],
                "summary": {},
                "reason": "verify --quick",
            }
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
            lsp_payload = _verify_lsp_payload(
                project_root, changed_files, with_lsp, lsp_timeout, lsp_max_files
            )

        graph_diff_payload = _verify_graph_diff_payload(
            project_root,
            with_diff,
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
                    {
                        "testFile": test.test_file,
                        "targetFile": test.target_file,
                        "confidence": test.confidence,
                        "reason": test.reason,
                    }
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
            print(json_dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_verify_report(payload))

        # 如果没有 git 变更，给出下一步建议
        if not changed_files:
            print("\n> No git changes detected.", file=sys.stderr)
            if quick:
                print(
                    "> verify --quick mode only analyzes git changes; no changes found, risk assessment unavailable.",
                    file=sys.stderr,
                )
                print(
                    "> Suggestion: make code changes first, then run `repomap verify` for full verification.",
                    file=sys.stderr,
                )
            else:
                print(
                    "> Suggestion: use `repomap overview` for project structure or `repomap check` for compilation checks.",
                    file=sys.stderr,
                )

        # Potentially missed files section
        if changed_files and not as_json:
            _print_missed_files_section(engine, changed_files)

        return 1 if status == "failed" else 0
    except Exception as exc:
        print(f"[{CLI_NAME}] verify failed: {exc}", file=sys.stderr)
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
            selected, error, tier = _select_symbol_match(
                engine, symbol, file_path=file_path
            )
            if error:
                print(error, file=sys.stderr)
                return 1
            assert selected is not None
            sid = selected.id
            target = engine.graph.symbols[sid]
            payload = {
                "symbol": target.name,
                "id": sid,
                "called_by": [
                    _format_symbol_ref(engine, item)
                    for item in sorted(calls_in[sid])[:20]
                ],
                "calls": [
                    _format_symbol_ref(engine, item)
                    for item in sorted(calls_out[sid])[:20]
                ],
                "ref_count": len(calls_in[sid]),
                "is_entry": len(calls_in[sid]) == 0,
                "is_leaf": len(calls_out[sid]) == 0,
            }
            if with_lsp:
                payload["lsp"] = _collect_lsp_evidence_for_symbol(
                    engine, target, lsp_timeout
                )
            if as_json:
                print(json_dumps(payload, ensure_ascii=False, indent=2))
            else:
                lines = [f"## Reference Analysis — `{target.name}`\n"]
                lines.append(f"- Referenced by:  {payload['ref_count']}")
                lines.append(f"- Calls: {len(payload['calls'])}")
                lines.append(
                    f"- Entry point:  {'Yes' if payload['is_entry'] else 'No'}"
                )
                lines.append(
                    f"- Leaf function:  {'Yes' if payload['is_leaf'] else 'No'}\n"
                )
                if payload["called_by"]:
                    lines.append("**Called by** (Top 10):")
                    for row in payload["called_by"][:10]:
                        lines.append(
                            f"  - `{row['name']}` ({row['file']}:{row['line']})"
                        )
                if payload["calls"]:
                    lines.append("\n**Calls** (Top 10):")
                    for row in payload["calls"][:10]:
                        lines.append(
                            f"  - `{row['name']}` ({row['file']}:{row['line']})"
                        )
                if with_lsp:
                    lines.extend(_format_lsp_evidence(payload["lsp"]))
                print("\n".join(lines))
            return 0

        entries = [sid for sid in symbol_ids if len(calls_in[sid]) == 0]
        orphans = [
            sid
            for sid in symbol_ids
            if len(calls_in[sid]) == 0 and len(calls_out[sid]) == 0
        ]
        ref_counts = sorted(
            ((sid, len(calls_in[sid])) for sid in symbol_ids),
            key=lambda item: item[1],
            reverse=True,
        )
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
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0
        lines = ["## Global Reference Analysis\n"]
        lines.append(f"- Total symbols:  {payload['total_symbols']}")
        lines.append(f"- Entry point:  {len(payload['entry_points'])}")
        lines.append(f"- Orphaned symbols:  {len(payload['orphaned_symbols'])}\n")
        lines.append("**Most referenced** (Top 10):")
        for row in payload["most_referenced"][:10]:
            lines.append(
                f"  - `{row['name']}`: {row['ref_count']}  references ({row['file']})"
            )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] refs failed: {exc}", file=sys.stderr)
        return 1


# Kinds that are always structural noise, never dead code.
_ORPHAN_EXCLUDED_KINDS: set[str] = {
    "element",  # HTML tags in JSX/HTML files
    "json_key",  # JSON object keys in config files
    "module",  # mod declarations, import wrappers
    "handler",  # web route handlers (framework-dispatched)
}

# File extensions that are pure config — skip orphan detection entirely.
_ORPHAN_EXCLUDED_EXTENSIONS: set[str] = {
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".scss",
    ".less",
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
    if any(
        name_lower.startswith(prefix) for prefix in ("test_", "it_", "should_", "test")
    ):
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


def run_orphan(
    project: str,
    max_files: int,
    as_json: bool = False,
    limit: int = 20,
    min_confidence: int = 0,
) -> int:
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
                if any(
                    symbol.file.lower().endswith(ext)
                    for ext in _ORPHAN_EXCLUDED_EXTENSIONS
                ):
                    filtered_structural_count += 1
                    continue
                candidates.append(symbol)

        # Build orphan name set for struct/impl pairing heuristic
        orphan_names: set[str] = {s.name for s in candidates}

        # Compute confidence for each candidate
        scored: list[dict] = []
        for symbol in candidates:
            conf = _orphan_confidence(symbol, orphan_names)
            scored.append(
                {
                    "symbol": symbol,
                    "confidence": conf,
                    "note": _orphan_note(symbol),
                }
            )

        scored.sort(
            key=lambda x: (
                -x["confidence"],
                x["symbol"].file,
                x["symbol"].line,
                x["symbol"].name,
            )
        )

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
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        # Text output
        lines = ["## Dead Code Analysis\n"]
        lines.append(
            f"Total {len(candidates)} candidates ({filtered_structural_count} structural elements filtered)"
        )
        if min_confidence > 0:
            lines.append(
                f"Confidence threshold: {min_confidence} (low-confidence items filtered)"
            )
        lines.append("")

        _module_for_file = GraphAnalyzer._module_bucket_for_file

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
                mod_items = by_module[mod][
                    : max(3, max_items // max(len(by_module), 1))
                ]
                tier_lines.append(f"**`{mod}/`** ({len(by_module[mod])})")
                for item in mod_items:
                    sym = item["symbol"]
                    tier_lines.append(
                        f"- `{sym.name}` ({sym.kind}) `{sym.file}:{sym.line}` — {item['confidence']}% | {item['note']}"
                    )
                if len(by_module[mod]) > len(mod_items):
                    tier_lines.append(
                        f"  ... {len(by_module[mod]) - len(mod_items)} more"
                    )
            tier_lines.append("")
            return tier_lines

        lines.extend(_render_tier("HIGH (review recommended)", "🔴", high, limit))
        lines.extend(_render_tier("MEDIUM (verify needed)", "🟡", medium, limit))
        lines.extend(_render_tier("LOW (likely active)", "🟢", low, limit))

        # 如果过滤后无结果，给出建议
        if not high and not medium and not low:
            if min_confidence > 0:
                lines.append(
                    f"\n> Using `--min-confidence {min_confidence}` filter returned no results."
                )
                lines.append(
                    f"> Try a lower threshold, e.g.: `--min-confidence {max(0, min_confidence - 20)}`"
                )
            else:
                lines.append("\n> No dead code candidates found.")
                lines.append(
                    "> This may indicate good code quality, or analysis parameters need adjustment."
                )
        else:
            if low:
                lines.append(
                    "> Using `--min-confidence 40` filter low-confidence items."
                )
            lines.append(
                "> Do not delete solely based on this output. Verify with `refs` and business review. Use `--json` for structured output."
            )
            lines.append("")
            lines.append("## Pre-deletion checklist\n")
            lines.append(
                "1. Verify each candidate with `refs --project <project> --symbol <name>` or `query-symbol` before deletion."
            )
            lines.append(
                "2. Check for dynamic references: string-based calls, reflection, macro expansions, test fixtures, config-driven dispatch."
            )
            lines.append(
                "3. Check project-specific rules about code ownership, generated code, or feature flags."
            )
            lines.append("4. Run the full test suite after deletion.")
            lines.append(
                "5. Never delete solely from `orphan` output; treat it as a starting point for investigation."
            )
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
            print(json_dumps(payload, ensure_ascii=False, indent=2))
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
                status = (
                    "available"
                    if item.status == "available"
                    else f"missing ({item.reason or 'not found'})"
                )
                lines.append(
                    f"| {item.language} | {item.server_name or '-'} | {status} | {item.source or '-'} | `{item.workspace_root or project_root}` |"
                )
        lines.append(
            "\n> repomap checks project-local executables, PATH, and trusted user tool bins such as npm/pnpm/yarn/bun/pipx/uv/mason/cargo/go directories; it does not install or bundle servers."
        )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] lsp doctor failed: {exc}", file=sys.stderr)
        return 1


def run_lsp_setup(project: str, languages: list[str] | None, dry_run: bool) -> int:
    try:
        project_root = _resolve_project(project)
        from ..lsp import detect_lsp_server, detect_lsp_servers, LSP_INSTALL_STRATEGIES

        if languages:
            detections = [detect_lsp_server(project_root, lang) for lang in languages]
        else:
            detections = detect_lsp_servers(project_root)

        missing = [d for d in detections if d.status != "available"]
        available = [d for d in detections if d.status == "available"]

        print(f"Project: {project_root}")
        print(f"Detected languages: {len(detections)}")
        print()

        if available:
            print("Already available:")
            for d in available:
                print(f"  {d.language}: {d.server_name} ({d.source})")

        if not missing:
            print("\nAll LSP servers are already available.")
            return 0

        print(
            f"\n{'Would install' if dry_run else 'Installing'} {len(missing)} server(s):"
        )
        print()
        for d in missing:
            strategy = LSP_INSTALL_STRATEGIES.get(d.language, {})
            tool = strategy.get("tool", "unknown")
            cmd = strategy.get("cmd", "manual install")
            print(f"  [{d.language}] {d.server_name}")
            print(f"    Tool: {tool}")
            print(f"    Command: {cmd}")
            print()

        if dry_run:
            print("Dry run — no changes made. Remove --dry-run to execute.")
            return 0

        print("Installation not yet automated. Run the commands above manually.")
        print("Tip: repomap cannot auto-install LSP servers without your consent.")
        print("      Use the commands listed above, then re-run `repomap lsp doctor`.")
        return 2
    except Exception as exc:
        print(f"[{CLI_NAME}] lsp setup failed: {exc}", file=sys.stderr)
        return 1


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
                normalized_modified_files = _normalize_project_relative_paths(
                    project_root, modified_files, must_exist=False
                )
            except ValueError as exc:
                print(
                    f"[{CLI_NAME}] check failed: unsafe modified file: {exc}",
                    file=sys.stderr,
                )
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
        "unknown": "ℹ️ No diagnostic tools ran"
        if result.get("message")
        else "ℹ️ No supported types detected",
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
    lines.append(
        f"- Tools run: {summary.get('tools_run', 0)} |  Skipped: {summary.get('tools_skipped', 0)}"
    )
    if summary.get("tool_failures", 0):
        lines.append(f"- Tool failures: **{summary.get('tool_failures', 0)}**")
    if summary.get("tools_run", 0) == 0 and summary.get("tools_skipped", 0) > 0:
        lines.append(
            "\n⚠️ No diagnostic tool was available; status is unknown, not passed."
        )
    lines.append("")

    runs = result.get("runs", [])
    if runs:
        lines.append("### Tool Execution Details\n")
        for run in runs:
            status = (
                "⏭️ Skipped"
                if run.get("skipped")
                else (
                    "✅ Passed"
                    if run["exit_code"] == 0 and run["error_count"] == 0
                    else "❌ Failed"
                )
            )
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
                    lines.append(
                        f"  - ⚠️ Output truncated; showing first {max_issues} items"
                    )
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
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
                    issue["severity"], "❌"
                )
                confidence_icon = {"exact": "🎯", "line": "📍", "none": ""}.get(
                    issue.get("symbol_confidence", "none"), ""
                )
                symbol_info = (
                    f" {confidence_icon}`{issue['symbol']}`"
                    if issue.get("symbol")
                    else ""
                )
                lines.append(
                    f"  {icon} line{issue['line']}{symbol_info}: [{issue['code']}] {issue['message'][:50]}"
                )
            lines.append("")

    return "\n".join(lines)


def _module_origin(module_name: str) -> str:
    spec = importlib_util.find_spec(module_name)
    if spec is None:
        return "not found"
    return spec.origin or "built-in"


def run_doctor(project: str, show_lsp: bool = False) -> int:
    from ..parser import TreeSitterAdapter

    if project:
        project_root = _resolve_project(project)
    else:
        project_root = str(Path.cwd())

    adapter = TreeSitterAdapter()
    parsers = sorted(adapter.parsers)
    pyinstaller_spec = importlib_util.find_spec("PyInstaller")
    if parsers:
        print(f"tree-sitter parsers: {', '.join(parsers)}")
    else:
        print("tree-sitter bindings are missing", file=sys.stderr)
        return 1
    if "tsx" not in adapter.parsers:
        print("TSX parser: unavailable", file=sys.stderr)
        return 1
    repomap_cli_origin = _module_origin("repomap_cli")
    if repomap_cli_origin != "not found":
        print(f"repomap_cli: {repomap_cli_origin} (dev only)")
    print(f"tree_sitter: {_module_origin('tree_sitter')}")
    print("LSP client: available")

    if show_lsp:
        from ..lsp import detect_lsp_servers
        from ..lsp import LSP_INSTALL_STRATEGIES

        detections = detect_lsp_servers(project_root)
        available = [d for d in detections if d.status == "available"]
        missing = [d for d in detections if d.status != "available"]
        print(f"\nLSP servers (project: {project_root}):")
        for d in available:
            print(f"  {d.language}: {d.server_name} ({d.source})")
        if missing:
            print(f"\nMissing ({len(missing)}):")
            for d in missing:
                strategy = LSP_INSTALL_STRATEGIES.get(d.language, {})
                print(
                    f"  {d.language}: {d.server_name} — install: {strategy.get('cmd', 'manual')}"
                )
        else:
            print("\nAll LSP servers available.")
        print("\nTip: run `repomap lsp setup --dry-run` to preview auto-install.")
    else:
        print("LSP servers: run `repomap doctor --lsp` to check")
    if pyinstaller_spec is not None:
        print("PyInstaller: available")
    else:
        print(
            "PyInstaller: not installed in current runtime, only required for build-binary"
        )
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
        # 可选 parser 未安装时仍允许构建；已安装的动态模块显式加入 hidden-import，避免二进制漏包。
        if importlib_util.find_spec(module_name) is None:
            continue
        command.extend(["--hidden-import", module_name])
    command.append(str(PACKAGE_ROOT / "__main__.py"))
    return command


def run_state_map(
    project: str, max_files: int, symbol: str | None, query: str | None, as_json: bool
) -> int:
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
                        "values": [
                            {"name": v.name, "file": v.file, "line": v.line}
                            for v in d.values
                        ],
                        "writers": [
                            {"name": w.name, "file": w.file, "line": w.line}
                            for w in d.writers
                        ],
                        "readers": [
                            {"name": r.name, "file": r.file, "line": r.line}
                            for r in d.readers
                        ],
                    }
                    for d in defs
                ],
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
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
            lines.append(
                "**Risk hint**: Adding or removing a state value requires checking all writers, readers, and tests.\n"
            )

        if not defs:
            print(
                f"> No state definitions found for symbol={symbol or 'N/A'} query={query or 'N/A'}."
            )
        else:
            print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] state-map failed: {exc}", file=sys.stderr)
        return 1


def run_build_binary(output: str, name: str) -> int:
    output_dir = Path(output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        _pyinstaller_command(output_dir, name), cwd=str(PROJECT_ROOT), check=False
    )
    if result.returncode != 0:
        print(
            f"[{CLI_NAME}] build failed with exit code {result.returncode}",
            file=sys.stderr,
        )
        return result.returncode or 1
    print(f"binary ready: {output_dir / name}")
    return 0


def run_search(project: str, max_files: int, query: str, top_k: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        results = engine.search_symbols(query, top_k)
        if not results:
            # Fallback: use hotspots when no symbol matches found
            hotspot_entries = engine.hotspots(10)
            if hotspot_entries:
                lines = ["(fallback — no direct matches found)\n"]
                lines.append(f"## Hotspot files (no symbol matches for `{query}`)\n")
                for entry in hotspot_entries:
                    lines.append(
                        f"- `{entry['file']}` — {entry['symbol_count']} symbols, "
                        f"semantic density: {entry['semantic_symbol_count']}"
                    )
                print("\n".join(lines))
                return 0
            print(f"> No symbols found for query: `{query}`")
            return EXIT_NO_RESULTS

        from ..search import _HAS_BM25, _symbol_is_large

        backend = "BM25" if _HAS_BM25 else "keyword"
        lines = [f"Found {len(results)} symbols (backend: {backend})\n"]
        lines.append(f"## Search results for `{query}`\n")
        for sym, score in results:
            pr = sym.pagerank * 1000
            loc = f"`{sym.file}:{sym.line}`"
            if _symbol_is_large(sym):
                loc += f" (L{sym.line}-L{max(sym.end_line, sym.line)})"
            lines.append(
                f"- **{sym.name}** ({sym.kind}) {loc} score={score:.2f} PR={pr:.1f}"
            )
            if sym.return_type:
                lines.append(f"  - returns: `{sym.return_type}`")
            if sym.signature:
                lines.append(f"  - sig: `{sym.signature}`")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] search failed: {exc}", file=sys.stderr)
        return 1


def run_fix(project: str, dry_run: bool = False) -> int:
    """Auto-fix: ruff --fix, eslint --fix, etc."""
    try:
        project_root = _resolve_project(project)

        fixes_applied: list[str] = []

        # Try ruff
        try:
            result = subprocess.run(
                ["ruff", "check", "--fix", str(project_root)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                fixes_applied.append("ruff --fix")
        except Exception:
            pass

        # Try eslint
        try:
            result = subprocess.run(
                ["eslint", "--fix", f"{project_root}/**/*.{{js,ts,jsx,tsx}}"],
                capture_output=True,
                text=True,
                timeout=60,
                shell=True,
            )
            if result.returncode == 0:
                fixes_applied.append("eslint --fix")
        except Exception:
            pass

        if fixes_applied:
            print(f"Applied: {', '.join(fixes_applied)}")
        else:
            print("No auto-fixable issues found.")
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] fix failed: {exc}", file=sys.stderr)
        return 1


def run_ready(project: str) -> int:
    """Quick readiness check: verify --quick + check + ruff format --check."""
    try:
        project_root = _resolve_project(project)

        print("=" * 60)
        print("Ready Check")
        print("=" * 60)

        # 1. Quick verify (risk-only)
        print("\n--- Step 1: verify --quick ---")
        verify_ok = True
        try:
            verify_rc = run_verify(
                project=project_root,
                as_json=False,
                types=None,
                max_issues=50,
                resolve_symbols=True,
                with_lsp=False,
                lsp_timeout=8.0,
                lsp_max_files=20,
                with_diff=False,
                quick=True,
                incremental=False,
            )
            if verify_rc != 0:
                verify_ok = False
        except Exception as exc:
            print(f"  verify skipped: {exc}")
            verify_ok = False

        # 2. Check (compiler/static analysis)
        print("\n--- Step 2: check ---")
        check_ok = True
        try:
            check_rc = run_check(
                project=project_root,
                types=None,
                max_issues=50,
                since_commit=None,
                modified_files=None,
                resolve_symbols=True,
                with_lsp=False,
                lsp_timeout=8.0,
                lsp_max_files=20,
            )
            if check_rc != 0:
                check_ok = False
        except Exception as exc:
            print(f"  check skipped: {exc}")
            check_ok = False

        # 3. ruff format --check
        print("\n--- Step 3: ruff format --check ---")
        format_ok = True
        try:
            result = subprocess.run(
                ["ruff", "format", "--check", str(project_root)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                print("  Format check passed.")
            else:
                print(
                    f"  Format check failed. Run `ruff format {project_root}` to fix."
                )
                format_ok = False
        except Exception:
            print("  ruff not available, skipping format check.")

        # Summary
        print("\n" + "=" * 60)
        print("Ready Check Summary")
        print("=" * 60)
        all_ok = verify_ok and check_ok and format_ok
        print(f"  verify --quick: {'PASS' if verify_ok else 'FAIL'}")
        print(f"  check:         {'PASS' if check_ok else 'FAIL'}")
        print(f"  format:        {'PASS' if format_ok else 'SKIP/FAIL'}")
        print(f"\n  Overall: {'READY' if all_ok else 'NOT READY'}")

        return 0 if all_ok else 1
    except Exception as exc:
        print(f"[{CLI_NAME}] ready failed: {exc}", file=sys.stderr)
        return 1
