#!/usr/bin/env python3
"""
RepoMap Toolkit - 轻量级代码分析工具
=====================================
功能：
1. 符号缓存持久化 (cache)
2. 变更检测 (diff)
3. Git 历史关联 (git)
4. 引用计数分析 (refs)

使用：
    python repomap_toolkit.py cache --save --project /path/to/project
    python repomap_toolkit.py diff --project /path/to/project
    python repomap_toolkit.py git --symbol calculate_kpi --project /path/to/project
    python repomap_toolkit.py refs --symbol calculate_kpi --project /path/to/project
    python repomap_toolkit.py orphan --project /path/to/project
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
# Allow direct execution via `python src/toolkit.py` for development/debugging.
# When imported normally as a package, this insert is harmless (already on path).
from .core import RepoMapEngine
from . import (
    Edge,
    FileCacheEntry,
    IncrementalCache,
    Symbol,
    compare_graph_snapshots,
    get_cache_paths,
    get_incremental_cache_path,
    json_dump,
    json_load,
    serialize_edge,
    serialize_symbol,
)

logger = logging.getLogger("repomap.toolkit")


# ═══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════════

CACHE_SCHEMA_VERSION = 1


def _project_root_cache_key(project_path: str | Path) -> str:
    """为增量缓存生成稳定项目键，避免 Python 进程随机 hash 让校验不可复现。"""
    return hashlib.sha256(str(Path(project_path).resolve()).encode("utf-8")).hexdigest()


@dataclass
class SymbolCache:
    """符号缓存数据结构"""

    symbols: list[dict]
    edges: list[dict]
    scan_time: str
    project_path: str
    file_count: int
    symbol_count: int
    edge_count: int
    _schema_version: int = CACHE_SCHEMA_VERSION


# ═══════════════════════════════════════════════════════════════════════════════
# 核心功能：扫描与缓存
# ═══════════════════════════════════════════════════════════════════════════════


def scan_project(
    project_path: str, max_files: int = 5000
) -> tuple[list[Symbol], list[Edge]]:
    """扫描项目，返回符号和边"""
    engine = RepoMapEngine(project_path)
    engine.scan(max_files=max_files)

    # 从 graph 中提取所有 symbols 和 edges
    symbols = list(engine.graph.symbols.values())

    # edges 存储在 outgoing/incoming 中，需要去重收集
    edges = []
    seen_edges = set()
    for src_id, edge_list in engine.graph.outgoing.items():
        for edge in edge_list:
            edge_key = (src_id, edge.target, edge.kind)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append(edge)

    return symbols, edges


def save_cache(project_path: str, symbols: list[Symbol], edges: list[Edge]) -> Path:
    """保存扫描结果到缓存（原子写入，崩溃安全）"""
    import os
    import tempfile

    cache_file, _, last_file = get_cache_paths(project_path)
    cache_dir = cache_file.parent

    # 如果已有缓存，先备份到 last_snapshot
    if cache_file.exists():
        import shutil

        shutil.copy2(cache_file, last_file)

    cache = SymbolCache(
        symbols=sorted(
            [serialize_symbol(s) for s in symbols],
            key=lambda row: (
                row["file"],
                row["line"],
                row.get("end_line", row["line"]),
                row["name"],
                row["kind"],
            ),
        ),
        edges=sorted(
            [serialize_edge(e) for e in edges],
            key=lambda row: (row["source"], row["target"], row["kind"]),
        ),
        scan_time=datetime.now().isoformat(),
        project_path=project_path,
        file_count=len(set(s.file for s in symbols)),
        symbol_count=len(symbols),
        edge_count=len(edges),
    )

    # 原子写入：先写入临时文件，再原子替换
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_dir,
            prefix=".tmp_cache_",
            suffix=".json",
            delete=False,
        ) as f:
            temp_path = f.name
            json_dump(asdict(cache), f, indent=2, ensure_ascii=False)
        # 原子替换（Windows 和 Linux 都支持）
        os.replace(temp_path, cache_file)
    except Exception:
        # 清理临时文件（如果存在）
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    return cache_file


def load_cache(project_path: str) -> SymbolCache | None:
    """从缓存加载扫描结果"""
    import os

    cache_file, _, _ = get_cache_paths(project_path)

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json_load(f)
        # Schema version check - 不匹配时删除旧缓存，触发重建
        if data.get("_schema_version") != CACHE_SCHEMA_VERSION:
            print(
                f"[repomap] Cache schema version mismatch (cached: v{data.get('_schema_version')}, current: v{CACHE_SCHEMA_VERSION}), clearing old cache and re-scanning",
                file=sys.stderr,
            )
            try:
                os.unlink(cache_file)
            except OSError:
                pass
            return None
        return SymbolCache(**data)
    except ValueError:
        # Cache file corrupted — JSON 解析失败
        print(
            f"[repomap] Cache file corrupted ({cache_file}), clearing and re-scanning",
            file=sys.stderr,
        )
        try:
            os.unlink(cache_file)
        except OSError:
            pass
        return None
    except (TypeError, KeyError):
        # Schema 字段不匹配（版本升级后字段变更），清理后重建
        logger.warning(
            f"Cache schema mismatch ({cache_file}), clearing and re-scanning"
        )
        try:
            os.unlink(cache_file)
        except OSError:
            pass
        return None
    except OSError as exc:
        # I/O 问题（权限不足、磁盘满等），记录 warning 而非静默吞掉
        logger.warning(f"Failed to read cache file {cache_file}: {exc}")
        return None


def load_last_snapshot(project_path: str) -> SymbolCache | None:
    """加载上次快照（用于 diff）"""
    _, _, last_file = get_cache_paths(project_path)

    if not last_file.exists():
        return None

    try:
        with open(last_file, "r", encoding="utf-8") as f:
            data = json_load(f)
        return SymbolCache(**data)
    except (ValueError, TypeError, KeyError):
        # 快照文件损坏或 schema 不匹配，记录后返回 None
        logger.debug(f"Last snapshot corrupted or incompatible ({last_file})")
        return None
    except OSError as exc:
        # I/O 异常（权限、磁盘等），不应静默
        logger.warning(f"Failed to read snapshot file {last_file}: {exc}")
        return None


def save_incremental_cache(project_path: str, engine: RepoMapEngine) -> Path:
    """保存增量扫描基线——存储每个文件的解析结果以支持后续增量扫描。

    在全量扫描完成后调用，建立基线快照。
    """
    cache_path = get_incremental_cache_path(project_path)
    cache_dir = cache_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)

    git_head = ""
    try:
        from .git_backend import GitBackend

        git = GitBackend(project_path)
        head = git.rev_parse_head()
        if head:
            git_head = head
    except Exception as exc:
        logger.warning(
            f"git rev-parse HEAD failed during incremental cache save: {exc}"
        )

    files: dict[str, dict] = {}
    for file_path in engine.graph.file_symbols:
        full = engine.project_root / file_path
        try:
            full_stat = full.stat()
        except OSError as exc:
            # 文件在扫描和保存之间出现问题（删除、权限、符号链接循环等），跳过该文件
            logger.debug("无法 stat 文件，跳过缓存: %s (%s)", file_path, exc)
            continue
        mtime = full_stat.st_mtime
        files[file_path] = {
            "mtime": mtime,
            "size": full_stat.st_size,
            "symbols_json": [
                serialize_symbol(engine.graph.symbols[sid])
                for sid in engine.graph.file_symbols[file_path]
                if sid in engine.graph.symbols
            ],
            "imports": engine.graph.file_imports.get(file_path, []),
            "import_bindings_json": [
                {
                    "local_name": b.local_name,
                    "imported_name": b.imported_name,
                    "module": b.module,
                    "line": b.line,
                    "kind": b.kind,
                }
                for b in engine.graph.file_import_bindings.get(file_path, [])
            ],
            "exports_json": [
                {
                    "exported_name": b.exported_name,
                    "source_name": b.source_name,
                    "module": b.module,
                    "line": b.line,
                    "kind": b.kind,
                }
                for b in engine.graph.file_exports.get(file_path, [])
            ],
            "calls_json": [
                {"name": c[0], "line": c[1], "kind": c[2]}
                if len(c) >= 3
                else {"name": c[0], "line": c[1], "kind": "direct"}
                for c in engine.graph.file_calls.get(file_path, [])
                if len(c) >= 2  # 确保tuple至少有name和line两个元素
            ],
            "routes_json": [
                {
                    "method": r.method,
                    "path": r.path,
                    "handler": r.handler,
                    "file": r.file,
                    "line": r.line,
                    "framework": r.framework,
                }
                for r in engine.routes
                if r.file == file_path
            ],
        }

    cache = IncrementalCache(
        project_root_hash=_project_root_cache_key(engine.project_root),
        git_head=git_head,
        files={fp: FileCacheEntry(**data) for fp, data in files.items()},
        scan_stats_json={
            "processed_files": engine.scan_stats.processed_files,
            "total_symbols": len(engine.graph.symbols),
            "total_edges": sum(len(v) for v in engine.graph.outgoing.values()),
        },
    )

    import tempfile

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_dir,
            prefix=".tmp_inc_",
            suffix=".json",
            delete=False,
        ) as f:
            temp_path = f.name
            json_dump(_inc_cache_to_dict(cache), f, indent=2, ensure_ascii=False)
        os.replace(temp_path, cache_path)
    except Exception:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    return cache_path


def load_incremental_cache(project_path: str) -> IncrementalCache | None:
    """加载增量扫描基线。返回 None 表示基线不存在或已失效。"""
    cache_path = get_incremental_cache_path(project_path)
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json_load(f)
        files = {}
        for fp, entry_data in data.get("files", {}).items():
            files[fp] = FileCacheEntry(
                mtime=entry_data.get("mtime", 0.0),
                size=entry_data.get("size", 0),
                symbols_json=entry_data.get("symbols_json", []),
                imports=entry_data.get("imports", []),
                import_bindings_json=entry_data.get("import_bindings_json", []),
                exports_json=entry_data.get("exports_json", []),
                calls_json=entry_data.get("calls_json", []),
                routes_json=entry_data.get("routes_json", []),
            )
        return IncrementalCache(
            project_root_hash=data.get("project_root_hash", ""),
            git_head=data.get("git_head", ""),
            files=files,
            scan_stats_json=data.get("scan_stats_json", {}),
        )
    except (ValueError, KeyError, TypeError):
        # 缓存损坏或 schema 不匹配
        logger.debug(f"Incremental cache corrupted or incompatible ({cache_path})")
        return None
    except OSError as exc:
        # I/O 异常（权限、磁盘等）
        logger.warning(f"Failed to read incremental cache {cache_path}: {exc}")
        return None


def _inc_cache_to_dict(cache: IncrementalCache) -> dict:
    return {
        "project_root_hash": cache.project_root_hash,
        "git_head": cache.git_head,
        "files": {
            fp: {
                "mtime": entry.mtime,
                "size": entry.size,
                "symbols_json": entry.symbols_json,
                "imports": entry.imports,
                "import_bindings_json": entry.import_bindings_json,
                "exports_json": entry.exports_json,
                "calls_json": entry.calls_json,
                "routes_json": entry.routes_json,
            }
            for fp, entry in cache.files.items()
        },
        "scan_stats_json": cache.scan_stats_json,
    }


def diff_project(project_path: str) -> dict:
    """对比上次缓存与当前状态"""
    current_symbols, current_edges = scan_project(project_path)
    last = load_cache(project_path)

    if last is None:
        return {"error": "No cache found. Run cache --save first."}
    comparison = compare_graph_snapshots(
        current_symbols=current_symbols,
        current_edges=current_edges,
        previous_symbols=last.symbols,
        previous_edges=last.edges,
    )

    return {
        "scan_time": datetime.now().isoformat(),
        "last_scan": last.scan_time,
        **comparison,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 功能 3: 引用计数分析
# ═══════════════════════════════════════════════════════════════════════════════


def analyze_refs(project_path: str, symbol_name: str | None = None) -> dict:
    """分析符号引用关系"""
    cache = load_cache(project_path)
    if not cache:
        return {"error": "No cache found. Run cache --save first."}

    # 构建调用图
    symbol_ids = {s["id"] for s in cache.symbols}

    # from_id -> [to_id] (这个符号调用了谁)
    calls_out: dict[str, set] = {s: set() for s in symbol_ids}
    # to_id -> [from_id] (谁调用了这个符号)
    calls_in: dict[str, set] = {s: set() for s in symbol_ids}

    for e in cache.edges:
        if e.get("kind", "call") not in {"call", "import"}:
            continue
        from_id = e.get("source", e.get("from_id"))
        to_id = e.get("target", e.get("to_id"))
        if from_id and to_id:
            if from_id in calls_out:
                calls_out[from_id].add(to_id)
            if to_id in calls_in:
                calls_in[to_id].add(from_id)

    symbol_map = {s["id"]: s for s in cache.symbols}

    if symbol_name:
        # 查找特定符号
        matches = [sid for sid in symbol_ids if symbol_name in sid]
        if not matches:
            return {"error": f"Symbol not found: {symbol_name}"}

        sid = matches[0]
        s = symbol_map[sid]

        return {
            "symbol": s["name"],
            "id": sid,
            "called_by": [
                _format_ref(cid, symbol_map)
                for cid in sorted(
                    calls_in[sid],
                    key=lambda item: (
                        symbol_map[item]["file"],
                        symbol_map[item]["line"],
                        symbol_map[item]["name"],
                    ),
                )[:20]
            ],
            "calls": [
                _format_ref(cid, symbol_map)
                for cid in sorted(
                    calls_out[sid],
                    key=lambda item: (
                        symbol_map[item]["file"],
                        symbol_map[item]["line"],
                        symbol_map[item]["name"],
                    ),
                )[:20]
            ],
            "ref_count": len(calls_in[sid]),
            "is_entry": len(calls_in[sid]) == 0,
            "is_leaf": len(calls_out[sid]) == 0,
        }
    else:
        # 全局分析
        results = []
        for sid in symbol_ids:
            ref_count = len(calls_in[sid])
            calls_out_count = len(calls_out[sid])
            is_entry = ref_count == 0
            is_leaf = calls_out_count == 0
            is_orphan = is_entry and is_leaf and not _is_public_entry(symbol_map[sid])

            results.append(
                {
                    "id": sid,
                    "name": symbol_map[sid]["name"],
                    "file": symbol_map[sid]["file"],
                    "ref_count": ref_count,
                    "calls_count": calls_out_count,
                    "is_entry": is_entry,
                    "is_leaf": is_leaf,
                    "is_orphan": is_orphan,
                }
            )

        return {
            "total_symbols": len(results),
            "entry_points": [r for r in results if r["is_entry"]],
            "leaf_functions": sorted(
                [r for r in results if r["is_leaf"]],
                key=lambda x: x["ref_count"],
                reverse=True,
            )[:20],
            "orphaned_symbols": [r for r in results if r["is_orphan"]],
            "most_referenced": sorted(
                results, key=lambda x: x["ref_count"], reverse=True
            )[:20],
        }


def _format_ref(sid: str, symbol_map: dict) -> dict:
    """格式化引用信息"""
    s = symbol_map.get(sid, {})
    return {
        "name": s.get("name", sid),
        "file": s.get("file", "unknown"),
        "line": s.get("line", 0),
    }


def _is_public_entry(symbol: dict) -> bool:
    """判断是否是公开入口（如 main, handler 等）"""
    name = symbol.get("name", "")
    visibility = symbol.get("visibility", "")
    # 只保留有真实静态证据的入口豁免，避免用命名猜测掩盖死代码
    if name in {
        "main",
        "__main__",
        "run",
        "serve",
        "start",
        "init",
        "setup",
        "create_app",
        "mainloop",
    }:
        return True
    if visibility == "exported":
        return True
    return False


def find_orphans(project_path: str) -> list[dict]:
    """查找死代码（孤儿符号）"""
    result = analyze_refs(project_path)
    if "error" in result:
        return []
    return result.get("orphaned_symbols", [])
