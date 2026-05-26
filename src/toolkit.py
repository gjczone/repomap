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

import argparse
import hashlib
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
    json_dumps,
    json_load,
    serialize_edge,
    serialize_symbol,
)


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


@dataclass
class GitSymbolInfo:
    """符号的 Git 历史信息"""

    symbol_id: str
    first_seen: str
    last_modified: str
    commit_count: int
    authors: list[str]
    recent_commits: list[dict]


@dataclass
class RefCountInfo:
    """引用计数信息"""

    symbol_id: str
    called_by: list[str]  # 被谁调用
    calls: list[str]  # 调用谁
    ref_count: int  # 被引用次数
    is_entry: bool  # 是否是入口（不被任何人调用）
    is_leaf: bool  # 是否是叶子（不调用任何人）
    is_orphan: bool  # 是否是孤儿（不被调用也不调用别人，且非入口）


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
        if "temp_path" in locals() and os.path.exists(temp_path):
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
        # Cache file corrupted
        print(
            f"[repomap] Cache file corrupted ({cache_file}), clearing and re-scanning",
            file=sys.stderr,
        )
        try:
            os.unlink(cache_file)
        except OSError:
            pass
        return None
    except Exception:
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
    except Exception:
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
    except Exception:
        pass

    files: dict[str, dict] = {}
    for file_path in engine.graph.file_symbols:
        full = engine.project_root / file_path
        mtime = full.stat().st_mtime if full.exists() else 0.0
        files[file_path] = {
            "mtime": mtime,
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
        if "temp_path" in locals() and os.path.exists(temp_path):
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
    except (ValueError, KeyError):
        return None


def _inc_cache_to_dict(cache: IncrementalCache) -> dict:
    return {
        "project_root_hash": cache.project_root_hash,
        "git_head": cache.git_head,
        "files": {
            fp: {
                "mtime": entry.mtime,
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


def get_symbol_git_history(project_path: str, symbol_name: str) -> dict | None:
    """获取符号的 Git 历史信息"""
    # 先找到符号所在的文件和行号
    cache = load_cache(project_path)
    if not cache:
        return None

    # 查找匹配的符号
    matches = [s for s in cache.symbols if symbol_name in s["name"]]
    if not matches:
        return None

    symbol = matches[0]
    file_path = symbol["file"]
    line = symbol["line"]

    full_path = Path(project_path) / file_path
    if not full_path.exists():
        return None

    try:
        from .git_backend import GitBackend

        git = GitBackend(project_path)

        blame_info = git.blame_line(file_path, line)
        commit_hash = blame_info.get("commit", "unknown") if blame_info else "unknown"

        recent_commits = git.log_file_commits(file_path, limit=20)

        authors = git.file_authors(file_path)

        return {
            "symbol": symbol["name"],
            "file": file_path,
            "line": line,
            "current_commit": commit_hash[:8] if len(commit_hash) > 8 else commit_hash,
            "authors": authors[:5],
            "recent_commits": recent_commits[:10],
        }

    except Exception:
        return None


def get_hot_symbols(project_path: str, days: int = 30) -> list[dict]:
    """获取最近修改频繁的文件/符号"""
    try:
        from .git_backend import GitBackend

        git = GitBackend(project_path)
        changed_files = git.diff_name_only_since(days)

        cache = load_cache(project_path)
        if not cache:
            return []

        # 统计每个文件的符号数
        file_symbols: dict[str, list[str]] = {}
        for s in cache.symbols:
            f = s["file"]
            if f not in file_symbols:
                file_symbols[f] = []
            file_symbols[f].append(s["name"])

        # 找出变更文件中的符号
        hot_symbols: list[dict[str, object]] = []
        for f in changed_files:
            if f in file_symbols:
                hot_symbols.append(
                    {
                        "file": f,
                        "symbols": file_symbols[f][:10],
                        "symbol_count": len(file_symbols[f]),
                    }
                )

        def symbol_count(item: dict[str, object]) -> int:
            value = item.get("symbol_count", 0)
            return value if isinstance(value, int) else 0

        return sorted(hot_symbols, key=symbol_count, reverse=True)[:10]

    except Exception:
        return []


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
        if e.get("kind", "call") != "call":
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
    if name in {"main", "__main__"}:
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


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="RepoMap Toolkit - 轻量级代码分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s cache --save --project ./my-project
  %(prog)s diff --project ./my-project
  %(prog)s git --symbol calculate_kpi --project ./my-project
  %(prog)s refs --symbol calculate_kpi --project ./my-project
  %(prog)s orphan --project ./my-project
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # cache 命令
    cache_parser = subparsers.add_parser("cache", help="缓存管理")
    cache_parser.add_argument("--save", action="store_true", help="保存当前扫描到缓存")
    cache_parser.add_argument("--load", action="store_true", help="从缓存加载并显示")
    cache_parser.add_argument("--project", "-p", default=".", help="项目路径")

    # diff 命令
    diff_parser = subparsers.add_parser("diff", help="变更检测")
    diff_parser.add_argument("--project", "-p", default=".", help="项目路径")
    diff_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # git 命令
    git_parser = subparsers.add_parser("git", help="Git 历史关联")
    git_parser.add_argument("--symbol", "-s", required=True, help="符号名称")
    git_parser.add_argument("--hot", action="store_true", help="显示热点文件")
    git_parser.add_argument("--days", "-d", type=int, default=30, help="统计天数")
    git_parser.add_argument("--project", "-p", default=".", help="项目路径")

    # refs 命令
    refs_parser = subparsers.add_parser("refs", help="引用计数分析")
    refs_parser.add_argument("--symbol", "-s", help="特定符号名称（可选）")
    refs_parser.add_argument("--project", "-p", default=".", help="项目路径")
    refs_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # orphan 命令
    orphan_parser = subparsers.add_parser("orphan", help="查找死代码")
    orphan_parser.add_argument("--project", "-p", default=".", help="项目路径")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    project_path = os.path.abspath(args.project)

    if args.command == "cache":
        if args.save:
            print(f"Scanning project: {project_path}")
            symbols, edges = scan_project(project_path)
            cache_path = save_cache(project_path, symbols, edges)
            print(f"Cache saved: {cache_path}")
            print(f"   Symbols: {len(symbols)}, edges: {len(edges)}")
        elif args.load:
            cache = load_cache(project_path)
            if cache:
                print("Cache info:")
                print(f"   Scan time: {cache.scan_time}")
                print(f"   Files: {cache.file_count}")
                print(f"   Symbols: {cache.symbol_count}")
                print(f"   Edges: {cache.edge_count}")
            else:
                print("No cache found")
        else:
            cache_parser.print_help()

    elif args.command == "diff":
        print(f"Comparing changes: {project_path}")
        result = diff_project(project_path)

        if "error" in result:
            print(f"❌ {result['error']}")
            return

        if args.json:
            print(json_dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"\n📊 变更摘要 ({result['last_scan']} -> {result['scan_time']})")
            print(f"   新增符号: {result['summary']['added']}")
            print(f"   删除符号: {result['summary']['removed']}")
            print(f"   修改符号: {result['summary']['modified']}")
            print(f"   新增调用: {result['summary']['edges_added']}")
            print(f"   删除调用: {result['summary']['edges_removed']}")

            if result["added_symbols"]:
                print("\n➕ 新增符号 (Top 10):")
                for s in result["added_symbols"][:10]:
                    print(f"   - {s['name']} ({s['file']}:{s['line']})")

            if result["call_chain_changes"]["new_calls"]:
                print("\n🔗 新增调用关系 (Top 10):")
                for c in result["call_chain_changes"]["new_calls"][:10]:
                    from_name = (
                        c["from"].split("::")[-2] if "::" in c["from"] else c["from"]
                    )
                    to_name = c["to"].split("::")[-2] if "::" in c["to"] else c["to"]
                    print(f"   - {from_name} -[{c['kind']}]-> {to_name}")

    elif args.command == "git":
        if args.hot:
            print(f"🔥 热点文件 (最近 {args.days} 天):")
            hot = get_hot_symbols(project_path, args.days)
            for item in hot:
                print(f"\n   📄 {item['file']} ({item['symbol_count']} 个符号)")
                for s in item["symbols"][:5]:
                    print(f"      - {s}")
        else:
            print(f"📜 正在查询 Git 历史: {args.symbol}")
            result = get_symbol_git_history(project_path, args.symbol)

            if result is None:
                print("❌ 未找到符号或 Git 信息")
                return

            print(f"\n  Symbol location: {result['file']}:{result['line']}")
            print(f"  Current commit: {result['current_commit']}")
            print(f"\n  Authors: {', '.join(result['authors'])}")
            print("\n📅 最近提交:")
            for c in result["recent_commits"][:5]:
                print(f"   [{c['hash']}] {c['date'][:10]} by {c['author']}")
                print(f"       {c['message'][:60]}")

    elif args.command == "refs":
        print(f"🔗 正在分析引用关系: {project_path}")
        result = analyze_refs(project_path, args.symbol)

        if "error" in result:
            print(f"❌ {result['error']}")
            return

        if args.json:
            print(json_dumps(result, indent=2, ensure_ascii=False))
        elif args.symbol:
            print(f"\n📌 {result['symbol']}")
            print(f"   被引用次数: {result['ref_count']}")
            print(f"   入口函数: {'是' if result['is_entry'] else '否'}")
            print(f"   叶子函数: {'是' if result['is_leaf'] else '否'}")

            if result["called_by"]:
                print(f"\n📥 被调用 ({len(result['called_by'])} 个):")
                for ref in result["called_by"][:10]:
                    print(f"   - {ref['name']} ({ref['file']}:{ref['line']})")

            if result["calls"]:
                print(f"\n📤 调用 ({len(result['calls'])} 个):")
                for ref in result["calls"][:10]:
                    print(f"   - {ref['name']} ({ref['file']}:{ref['line']})")
        else:
            print("\n📊 全局引用分析")
            print(f"   总符号数: {result['total_symbols']}")
            print(f"   入口函数: {len(result['entry_points'])}")
            print(f"   死代码: {len(result['orphaned_symbols'])}")

            print("\n🔝 被引用最多 (Top 10):")
            for r in result["most_referenced"][:10]:
                status = "🚪" if r["is_entry"] else "🍃" if r["is_leaf"] else "  "
                print(f"   {status} {r['name']}: {r['ref_count']} 次引用 ({r['file']})")

    elif args.command == "orphan":
        print(f"🧹 正在查找死代码: {project_path}")
        orphans = find_orphans(project_path)

        if orphans:
            print(f"\n⚠️  发现 {len(orphans)} 个可疑死代码:")
            for o in orphans[:20]:
                print(f"   - {o['name']} ({o['file']})")
        else:
            print("\n✅ 未发现明显死代码")
