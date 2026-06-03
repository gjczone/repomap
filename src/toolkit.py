#!/usr/bin/env python3
"""
RepoMap Toolkit - 内部缓存与 graph diff 辅助模块
=================================================
职责：
1. 符号缓存持久化（cache save/restore）
2. 增量扫描基线（verify graph diff 对比）
3. Git 历史关联辅助（co-change 分析）

对外命令入口：
    repomap cache save --project <path>    # 保存扫描基线
    repomap verify --project <path>        # 验证变更 + graph diff 对比
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
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
            json_dump(asdict(cache), f, indent=2)
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
        with open(cache_file, "r", encoding="utf-8", errors="replace") as f:
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
            json_dump(_inc_cache_to_dict(cache), f, indent=2)
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
        with open(cache_path, "r", encoding="utf-8", errors="replace") as f:
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


_DEFAULT_CACHE_TTL_DAYS = 7


def prune_cache(
    cache_root: Path | None = None, ttl_days: int = _DEFAULT_CACHE_TTL_DAYS
) -> tuple[list[Path], list[Path]]:
    """删除 cache_root 下 mtime 早于 ttl_days 的子目录。

    返回 (removed, kept) 两个 Path 列表。
    只处理目录（忽略散文件）；删除失败时保留目录并记录 warning。
    """
    import logging as _logging
    import shutil

    if cache_root is not None:
        root = cache_root
    else:
        from . import CACHE_DIR as _CACHE_DIR
        root = _CACHE_DIR
    if not root.exists():
        return [], []

    now = time.time()
    cutoff = now - ttl_days * 86400
    removed: list[Path] = []
    kept: list[Path] = []

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            kept.append(entry)
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(entry)
                removed.append(entry)
            except OSError as exc:
                _logging.getLogger("repomap.toolkit").warning(
                    "Failed to prune cache dir %s: %s", entry, exc
                )
                kept.append(entry)
        else:
            kept.append(entry)
    return removed, kept
