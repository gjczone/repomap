from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CACHE_DIR = Path.home() / ".cache" / "repomap"


def get_project_cache_dir(project_path: str) -> Path:
    """获取项目的缓存目录（基于路径哈希避免特殊字符）"""
    path_hash = hashlib.md5(project_path.encode()).hexdigest()[:8]
    project_name = Path(project_path).name
    cache_dir = CACHE_DIR / f"{project_name}_{path_hash}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cache_paths(project_path: str) -> tuple[Path, Path, Path]:
    """获取缓存文件路径: (symbols_cache, git_cache, last_snapshot)"""
    cache_dir = get_project_cache_dir(project_path)
    return (
        cache_dir / "symbols.json",
        cache_dir / "git.json",
        cache_dir / "last_snapshot.json",
    )


def get_session_cache_path(project_path: str) -> Path:
    """获取跨进程短期扫描缓存路径。"""
    cache_dir = get_project_cache_dir(project_path)
    return cache_dir / "session_scan.json"


@dataclass
class Symbol:
    """代码符号（函数 / 类 / 接口等）"""

    id: str
    name: str
    kind: str
    file: str
    line: int
    end_line: int = 0
    col: int = 0
    visibility: str = "private"
    docstring: str = ""
    signature: str = ""
    pagerank: float = 0.0


@dataclass
class Edge:
    source: str
    target: str
    weight: float
    kind: str


@dataclass(frozen=True)
class JSImportBinding:
    local_name: str
    imported_name: str
    module: str
    line: int
    kind: str = "named"


@dataclass(frozen=True)
class JSExportBinding:
    exported_name: str
    source_name: str | None
    module: str | None
    line: int
    kind: str = "local"


@dataclass(frozen=True)
class PathAliasRule:
    alias_pattern: str
    target_patterns: tuple[str, ...]


@dataclass
class ProjectImportConfig:
    config_path: str | None = None
    config_dir: str | None = None
    base_url: str | None = None
    alias_rules: list[PathAliasRule] = field(default_factory=list)


@dataclass
class ScanStats:
    listed_source_files: int = 0
    selected_source_files: int = 0
    processed_files: int = 0
    filtered_path_files: int = 0
    filtered_large_files: int = 0
    truncated_files: int = 0
    failed_files: list[str] = field(default_factory=list)  # 记录失败的文件路径（前N个）
    scan_duration_ms: int = 0  # 扫描耗时（毫秒）
    timeout_triggered: bool = False  # 是否触发超时熔断


@dataclass
class RepoGraph:
    symbols: dict[str, Symbol] = field(default_factory=dict)
    outgoing: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    incoming: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    file_symbols: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    file_imports: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    file_calls: dict[str, list[tuple[str, int]]] = field(default_factory=lambda: defaultdict(list))
    file_import_bindings: dict[str, list[JSImportBinding]] = field(default_factory=lambda: defaultdict(list))
    file_exports: dict[str, list[JSExportBinding]] = field(default_factory=lambda: defaultdict(list))


def call_reference_parts(call_ref: Any) -> tuple[str, int, str]:
    if isinstance(call_ref, (list, tuple)):
        if len(call_ref) >= 3:
            return str(call_ref[0]), int(call_ref[1]), str(call_ref[2] or "direct")
        if len(call_ref) >= 2:
            return str(call_ref[0]), int(call_ref[1]), "direct"
    name = getattr(call_ref, "name", "")
    line = getattr(call_ref, "line", 0)
    kind = getattr(call_ref, "kind", "direct")
    return str(name), int(line), str(kind or "direct")


def serialize_symbol(symbol: Symbol) -> dict[str, Any]:
    return {
        "id": symbol.id,
        "name": symbol.name,
        "kind": symbol.kind,
        "file": symbol.file,
        "line": symbol.line,
        "end_line": symbol.end_line,
        "col": symbol.col,
        "visibility": symbol.visibility,
        "signature": symbol.signature,
        "docstring": symbol.docstring,
        "pagerank": symbol.pagerank,
    }


def serialize_edge(edge: Edge) -> dict[str, Any]:
    return {
        "source": edge.source,
        "target": edge.target,
        "weight": edge.weight,
        "kind": edge.kind,
    }


def edge_identity_from_edge(edge: Edge) -> tuple[str, str, str] | None:
    if not edge.source or not edge.target:
        return None
    return (edge.source, edge.target, edge.kind)


def edge_identity_from_row(row: dict[str, Any]) -> tuple[str, str, str] | None:
    source = row.get("source", row.get("from_id"))
    target = row.get("target", row.get("to_id"))
    kind = row.get("kind", "call")
    if not source or not target:
        return None
    return (source, target, kind)


def compare_graph_snapshots(
    current_symbols: list[Symbol],
    current_edges: list[Edge],
    previous_symbols: list[dict[str, Any]],
    previous_edges: list[dict[str, Any]],
) -> dict[str, Any]:
    current_symbol_map = {symbol.id: symbol for symbol in current_symbols}
    previous_symbol_map = {row["id"]: row for row in previous_symbols}

    current_symbol_ids = set(current_symbol_map)
    previous_symbol_ids = set(previous_symbol_map)

    added_symbol_ids = sorted(current_symbol_ids - previous_symbol_ids)
    removed_symbol_ids = sorted(previous_symbol_ids - current_symbol_ids)

    modified_symbols = []
    for symbol_id in sorted(current_symbol_ids & previous_symbol_ids):
        current = current_symbol_map[symbol_id]
        previous = previous_symbol_map[symbol_id]
        if (
            current.line != previous.get("line")
            or current.end_line != previous.get("end_line", current.end_line)
            or current.file != previous.get("file")
            or current.signature != previous.get("signature", "")
        ):
            modified_symbols.append(
                {
                    "id": symbol_id,
                    "name": current.name,
                    "file": current.file,
                    "line_change": f"{previous.get('line')} -> {current.line}",
                }
            )

    current_edge_set = {
        edge_id
        for edge in current_edges
        for edge_id in [edge_identity_from_edge(edge)]
        if edge_id is not None
    }
    previous_edge_set = {
        edge_id
        for row in previous_edges
        for edge_id in [edge_identity_from_row(row)]
        if edge_id is not None
    }

    edges_added = sorted(current_edge_set - previous_edge_set)
    edges_removed = sorted(previous_edge_set - current_edge_set)

    return {
        "summary": {
            "added": len(added_symbol_ids),
            "removed": len(removed_symbol_ids),
            "modified": len(modified_symbols),
            "edges_added": len(edges_added),
            "edges_removed": len(edges_removed),
        },
        "added_symbols": [
            {
                "id": symbol_id,
                "name": current_symbol_map[symbol_id].name,
                "file": current_symbol_map[symbol_id].file,
                "line": current_symbol_map[symbol_id].line,
            }
            for symbol_id in added_symbol_ids
        ],
        "removed_symbols": [
            {
                "id": symbol_id,
                "name": previous_symbol_map[symbol_id].get("name", symbol_id),
                "file": previous_symbol_map[symbol_id].get("file", ""),
                "line": previous_symbol_map[symbol_id].get("line", 0),
            }
            for symbol_id in removed_symbol_ids
        ],
        "modified_symbols": modified_symbols,
        "call_chain_changes": {
            "new_calls": [
                {"from": source, "to": target, "kind": kind}
                for source, target, kind in edges_added[:20]
            ],
            "removed_calls": [
                {"from": source, "to": target, "kind": kind}
                for source, target, kind in edges_removed[:20]
            ],
        },
    }
