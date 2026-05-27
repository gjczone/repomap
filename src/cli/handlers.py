"""Shared helpers used by command modules."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .. import json_dump, json_loads
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
)
from ..core import RepoMapEngine
from ..gitignore import get_gitignore
from ..parser import EXT_TO_LANG
from ..topic import is_test_like_file

CLI_NAME = "repomap"
logger = logging.getLogger(__name__)
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
_SCAN_CACHE_MAX_SIZE = 16
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
    return max(1, value)


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
        if len(_SCAN_CACHE) >= _SCAN_CACHE_MAX_SIZE:
            _SCAN_CACHE.pop(next(iter(_SCAN_CACHE)))
        _SCAN_CACHE[cache_key] = (fingerprint, session_engine)
        return session_engine

    engine = RepoMapEngine(resolved_project)
    engine.scan(max_files=max_files, incremental=incremental)
    _save_session_engine(resolved_project, fingerprint, engine)
    if len(_SCAN_CACHE) >= _SCAN_CACHE_MAX_SIZE:
        _SCAN_CACHE.pop(next(iter(_SCAN_CACHE)))
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
    # "idle" 表示扫描从未完成，丢弃；"scanned" 和 "invalid" 的数据已完整反序列化，可用
    if engine.scan_state == "idle":
        logger.debug("Discarding session engine with idle scan_state")
        return None
    if engine.scan_state != "scanned":
        logger.warning(
            f"Restoring session engine with non-standard scan_state='{engine.scan_state}'"
        )
    return engine


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
    import re

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
        if re.search(rf"\b{re.escape(kw)}\b", all_paths):
            domain_risk += 3
    for kw in risk_keywords_medium:
        if re.search(rf"\b{re.escape(kw)}\b", all_paths):
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


def _sym_name(engine: RepoMapEngine, sid: str) -> str:
    sym = engine.graph.symbols.get(sid)
    return sym.name if sym else "?"


def _format_symbol_ref(engine: RepoMapEngine, sid: str) -> dict[str, Any]:
    symbol = engine.graph.symbols.get(sid)
    if symbol is None:
        return {"name": "?", "file": "?", "line": 0}
    return {"name": symbol.name, "file": symbol.file, "line": symbol.line}
