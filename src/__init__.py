from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("repomap")

try:
    import orjson as _orjson

    def json_dumps(obj: Any, *, indent: int | None = None) -> str:
        option = 0
        if indent is not None:
            option |= _orjson.OPT_INDENT_2
        # orjson 默认就是 UTF-8 输出（无 ensure_ascii 参数）
        # 注意：不要设置 OPT_NON_STR_KEYS，它会允许非字符串 key，产生非法 JSON
        if not option:
            return _orjson.dumps(obj).decode("utf-8")
        return _orjson.dumps(obj, option=option).decode("utf-8")

    def json_loads(s: str | bytes) -> Any:
        return _orjson.loads(s)

    def json_dump(obj: Any, fp: Any, *, indent: int | None = None) -> None:
        fp.write(json_dumps(obj, indent=indent))

    def json_load(fp: Any) -> Any:
        return json_loads(fp.read())

except ImportError:
    import json as _json_mod

    def json_dumps(obj: Any, *, indent: int | None = None) -> str:
        return _json_mod.dumps(obj, indent=indent)

    def json_loads(s: str | bytes) -> Any:
        return _json_mod.loads(s)

    def json_dump(obj: Any, fp: Any, *, indent: int | None = None) -> None:
        return _json_mod.dump(obj, fp, indent=indent)

    def json_load(fp: Any) -> Any:
        return _json_mod.load(fp)


CACHE_DIR = Path.home() / ".cache" / "repomap"

SESSION_CACHE_VERSION = 7


def get_repomap_version() -> str:
    """获取 repomap 版本号。

    优先级：importlib.metadata（正常安装）→ _version.py（PyInstaller 打包）→ 降级。
    """
    try:
        from importlib.metadata import version

        return version("repomap-cli")
    except Exception:
        logger.debug(
            "Failed to resolve repomap version via importlib.metadata", exc_info=True
        )
    # PyInstaller 打包时通过 _version.py 写入版本号
    try:
        from src._version import VERSION

        return VERSION
    except Exception:
        logger.warning(
            "Failed to load VERSION from _version.py, falling back to 0.0.0-dev",
            exc_info=True,
        )
    logger.warning("Version detection completely failed, using fallback 0.0.0-dev")
    return "0.0.0-dev"


DEFAULT_OVERVIEW_MAX_CHARS = 16000
DEFAULT_QUERY_SYMBOL_MAX_CHARS = 4000
DEFAULT_CALL_CHAIN_MAX_CHARS = 4000
DEFAULT_FILE_DETAIL_MAX_CHARS = 6000
DEFAULT_FILE_DETAIL_MAX_SYMBOLS = 12
DEFAULT_VERIFY_MAX_CHARS = 16000
DEFAULT_OVERVIEW_JSON_HOTSPOTS = 8
DEFAULT_OVERVIEW_JSON_READING_ORDER = 6
DEFAULT_OVERVIEW_JSON_MODULES = 6
DEFAULT_OVERVIEW_JSON_SUMMARY_FILES = 4
DEFAULT_OVERVIEW_JSON_SYMBOLS_PER_FILE = 3
DEFAULT_OVERVIEW_JSON_SUPPORTING_FILES = 8
DEFAULT_MAX_SOURCE_LINES = 80


# 低信号符号类型——这些类型在 PageRank/权重计算中降权处理
# 统一定义在此处，ranking.py 和 topic.py 共享同一来源，防止不同步
LOW_SIGNAL_KINDS = frozenset(
    {"element", "selector", "class_selector", "id_selector", "json_key"}
)
BOILERPLATE_NAMES = frozenset({"__init__", "__main__"})


def signal_weight_for_symbol(kind: str, name: str, visibility: str) -> float:
    """计算符号的信号权重（统一实现，ranking.py 和 topic.py 共享）。

    Args:
        kind: 符号类型（如 "function", "class", "element" 等）
        name: 符号名称
        visibility: 可见性（"exported", "public", "private"）

    Returns:
        权重系数：0.002（低信号）/ 0.35（样板代码）/ 0.85（私有）/ 1.0（默认）
    """
    if kind in LOW_SIGNAL_KINDS:
        return 0.002
    if name in BOILERPLATE_NAMES:
        return 0.35
    if name.startswith("_") and visibility == "private":
        return 0.85
    return 1.0


def find_child_by_type(node: Any, child_type: str) -> Any | None:
    """在 tree-sitter AST 节点中按子节点类型查找第一个匹配项。

    统一实现，供 parser.py / type_inference.py / callgraph.py 共享，
    消除三处重复定义。
    """
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def find_children_by_type(node: Any, child_type: str) -> list[Any]:
    """在 tree-sitter AST 节点中按子节点类型查找所有匹配项。"""
    return [child for child in node.children if child.type == child_type]


def get_project_cache_dir(project_path: str) -> Path:
    """获取项目的缓存目录（基于规范化后的项目路径哈希隔离）。"""
    canonical_path = str(Path(project_path).expanduser().resolve())
    path_hash = hashlib.md5(canonical_path.encode()).hexdigest()[:8]
    project_name = Path(canonical_path).name
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


def get_incremental_cache_path(project_path: str) -> Path:
    """获取增量扫描持久化缓存路径。"""
    cache_dir = get_project_cache_dir(project_path)
    return cache_dir / "incremental.json"


@dataclass
class FileCacheEntry:
    """单文件增量缓存条目——对应一次全量扫描中一个文件的解析结果"""

    mtime: float
    size: int = 0
    symbols_json: list[dict] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    import_bindings_json: list[dict] = field(default_factory=list)
    exports_json: list[dict] = field(default_factory=list)
    calls_json: list[dict] = field(default_factory=list)
    routes_json: list[dict] = field(default_factory=list)


@dataclass
class IncrementalCache:
    """持久化的增量扫描基线，用于后续增量扫描时识别变更文件并还原未变更文件"""

    project_root_hash: str = ""
    git_head: str = ""
    files: dict[str, FileCacheEntry] = field(default_factory=dict)
    scan_stats_json: dict = field(default_factory=dict)


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
    visibility: str = "public"
    docstring: str = ""
    signature: str = ""
    return_type: str = ""
    params: str = ""
    pagerank: float = 0.0


@dataclass
class Edge:
    source: str
    target: str
    weight: float
    kind: str
    confidence: float = 1.0


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
    skipped_files: int = 0  # 因 mtime 未变跳过的文件数（增量扫描）
    git_failed: bool = False  # git 操作是否失败（增量扫描可能使用过期数据）


@dataclass
class HttpRoute:
    """HTTP 路由定义（从 AST 中提取）"""

    method: str  # GET, POST, PUT, DELETE, PATCH
    path: str  # /api/users/:id
    handler: str  # 处理函数名
    file: str  # 文件路径
    line: int  # 行号
    framework: str  # fastapi, flask, express, axum


@dataclass
class RepoGraph:
    symbols: dict[str, Symbol] = field(default_factory=dict)
    outgoing: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    incoming: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    file_symbols: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    file_imports: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    file_calls: dict[str, list[tuple]] = field(
        default_factory=lambda: defaultdict(list)
    )
    file_import_bindings: dict[str, list[JSImportBinding]] = field(
        default_factory=lambda: defaultdict(list)
    )
    file_exports: dict[str, list[JSExportBinding]] = field(
        default_factory=lambda: defaultdict(list)
    )


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
        "return_type": symbol.return_type,
        "params": symbol.params,
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
    incoming_map: dict[str, list[Edge]] | None = None,
) -> dict[str, Any]:
    current_symbol_map = {symbol.id: symbol for symbol in current_symbols}
    previous_symbol_map = {row["id"]: row for row in previous_symbols}

    current_symbol_ids = set(current_symbol_map)
    previous_symbol_ids = set(previous_symbol_map)

    added_symbol_ids = sorted(current_symbol_ids - previous_symbol_ids)
    removed_symbol_ids = sorted(previous_symbol_ids - current_symbol_ids)

    # Issue #175: 用 stable key (file, name, kind, occurrence) 对 added/removed 做二次匹配，
    # 避免同一符号仅 line 漂移时被误报为 add+remove 对。
    def _stable_key_from_current(sid: str) -> tuple[str, str, str]:
        s = current_symbol_map[sid]
        return (s.file, s.name, s.kind)

    def _stable_key_from_prev(sid: str) -> tuple[str, str, str]:
        row = previous_symbol_map[sid]
        return (row.get("file", ""), row.get("name", ""), row.get("kind", ""))

    added_by_key: dict[tuple[str, str, str], list[str]] = {}
    for sid in added_symbol_ids:
        added_by_key.setdefault(_stable_key_from_current(sid), []).append(sid)
    removed_by_key: dict[tuple[str, str, str], list[str]] = {}
    for sid in removed_symbol_ids:
        removed_by_key.setdefault(_stable_key_from_prev(sid), []).append(sid)

    reconciled_pairs: list[tuple[str, str]] = []  # (prev_id, current_id)
    for key, adds in added_by_key.items():
        rems = removed_by_key.get(key, [])
        # 按顺序两两匹配（同 stable key 的第 N 个互相对应）
        for prev_id, cur_id in zip(rems, adds):
            reconciled_pairs.append((prev_id, cur_id))
    reconciled_added = {pair[1] for pair in reconciled_pairs}
    reconciled_removed = {pair[0] for pair in reconciled_pairs}
    added_symbol_ids = sorted(set(added_symbol_ids) - reconciled_added)
    removed_symbol_ids = sorted(set(removed_symbol_ids) - reconciled_removed)

    modified_symbols = []
    # 稳定 ID 完全匹配 → 检查 signature/line 变化
    for symbol_id in sorted(current_symbol_ids & previous_symbol_ids):
        current = current_symbol_map[symbol_id]
        previous = previous_symbol_map[symbol_id]
        sig_changed = current.signature != previous.get("signature", "")
        loc_changed = (
            current.line != previous.get("line")
            or current.end_line != previous.get("end_line", current.end_line)
            or current.file != previous.get("file")
        )
        if sig_changed or loc_changed:
            entry = {
                "id": symbol_id,
                "name": current.name,
                "file": current.file,
                "visibility": current.visibility,
                "kind": current.kind,
                "line_change": f"{previous.get('line')} -> {current.line}",
                "old_signature": previous.get("signature", ""),
                "new_signature": current.signature,
                "signature_changed": sig_changed,
            }
            # 附加调用者信息
            if incoming_map:
                callers = [
                    e for e in incoming_map.get(symbol_id, []) if e.kind == "call"
                ]
                entry["affected_callers"] = [
                    {"symbol_id": e.source, "kind": e.kind} for e in callers[:10]
                ]
                entry["affected_caller_count"] = len(callers)
                # 风险评级：导出符号签名变更→HIGH，否则有调用者→MEDIUM
                if sig_changed and entry.get("visibility") == "exported":
                    entry["risk"] = "HIGH"
                elif sig_changed and len(callers) >= 3:
                    entry["risk"] = "MEDIUM"
                else:
                    entry["risk"] = "LOW"
            modified_symbols.append(entry)

    # reconciled 对：stable key 匹配但 symbol_id 不同（line 漂移）→ 记为 modified
    for prev_id, cur_id in reconciled_pairs:
        current = current_symbol_map[cur_id]
        previous = previous_symbol_map[prev_id]
        sig_changed = current.signature != previous.get("signature", "")
        entry = {
            "id": cur_id,
            "name": current.name,
            "file": current.file,
            "visibility": current.visibility,
            "kind": current.kind,
            "line_change": f"{previous.get('line')} -> {current.line}",
            "old_signature": previous.get("signature", ""),
            "new_signature": current.signature,
            "signature_changed": sig_changed,
        }
        if incoming_map:
            callers = [e for e in incoming_map.get(cur_id, []) if e.kind == "call"]
            entry["affected_callers"] = [
                {"symbol_id": e.source, "kind": e.kind} for e in callers[:10]
            ]
            entry["affected_caller_count"] = len(callers)
            if sig_changed and entry.get("visibility") == "exported":
                entry["risk"] = "HIGH"
            elif sig_changed and len(callers) >= 3:
                entry["risk"] = "MEDIUM"
            else:
                entry["risk"] = "LOW"
        modified_symbols.append(entry)

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


def node_text(node: Any) -> str:
    """从 tree-sitter 节点提取文本内容。

    用于 callgraph.py 和 type_inference.py 中的符号名称提取。
    """
    return (
        node.text.decode("utf-8", errors="replace")
        if getattr(node, "text", None)
        else ""
    )
