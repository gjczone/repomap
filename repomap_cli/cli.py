from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from repomap_check import RepoMapChecker
from repomap_core import RepoMapEngine
from repomap_support import (
    edge_identity_from_edge,
    get_cache_paths,
    serialize_edge,
    serialize_symbol,
)
from repomap_toolkit import diff_project, load_cache, save_cache, scan_project

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
CLI_NAME = "repomap"
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
]


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
    overview_parser.add_argument("--max-chars", type=int, default=48000, help="Maximum overview size.")

    chain_parser = subparsers.add_parser("call-chain", help="Scan a repository and print a symbol call chain.")
    _add_project_args(chain_parser)
    chain_parser.add_argument("--symbol", required=True, help="Symbol name to analyze.")
    chain_parser.add_argument("--direction", choices=["callers", "callees", "both"], default="both")
    chain_parser.add_argument("--depth", type=int, default=3, help="Traversal depth.")

    query_parser = subparsers.add_parser("query-symbol", help="Scan a repository and query matching symbols.")
    _add_project_args(query_parser)
    query_parser.add_argument("--symbol", required=True, help="Symbol name to search for.")

    file_parser = subparsers.add_parser("file-detail", help="Scan a repository and print file detail.")
    _add_project_args(file_parser)
    file_parser.add_argument("--file-path", required=True, help="Relative file path to inspect.")

    hotspots_parser = subparsers.add_parser("hotspots", help="Scan a repository and print hotspot files.")
    _add_project_args(hotspots_parser)
    hotspots_parser.add_argument("--limit", type=int, default=15, help="Number of files to print.")

    cache_parser = subparsers.add_parser("cache", help="Save or load scan cache.")
    cache_parser.add_argument("action", choices=["save", "load"], help="Cache action.")
    cache_parser.add_argument("--project", "-p", default=".", help="Project root path.")

    diff_parser = subparsers.add_parser("diff", help="Compare current graph with the saved cache baseline.")
    diff_parser.add_argument("--project", "-p", default=".", help="Project root path.")
    diff_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

    git_parser = subparsers.add_parser("git-history", help="Scan a repository and inspect symbol git history.")
    _add_project_args(git_parser)
    git_parser.add_argument("--symbol", required=True, help="Symbol name to inspect.")

    refs_parser = subparsers.add_parser("refs", help="Scan a repository and analyze references.")
    _add_project_args(refs_parser)
    refs_parser.add_argument("--symbol", help="Optional symbol name.")
    refs_parser.add_argument("--json", action="store_true", help="Print raw JSON output.")

    orphan_parser = subparsers.add_parser("orphan", help="Scan a repository and find orphaned symbols.")
    _add_project_args(orphan_parser)

    check_parser = subparsers.add_parser("check", help="Run compiler/static analysis diagnostics.")
    check_parser.add_argument("--project", "-p", default=".", help="Project root path.")
    check_parser.add_argument(
        "--types",
        nargs="*",
        choices=["typescript", "rust", "python", "go", "javascript"],
        help="Explicit project types to check.",
    )
    check_parser.add_argument("--max-issues", type=int, default=50, help="Maximum issues per tool.")
    check_parser.add_argument("--since-commit", help="Only check files changed since the given commit.")
    check_parser.add_argument("--modified-file", action="append", dest="modified_files", help="Explicit modified file path.")
    check_parser.add_argument("--no-symbols", action="store_true", help="Skip scan-based symbol resolution.")

    subparsers.add_parser("doctor", help="Validate runtime and build prerequisites.")

    build_parser_cmd = subparsers.add_parser("build-binary", help="Build a one-file executable with PyInstaller.")
    build_parser_cmd.add_argument("--output", default="dist", help="Directory for the final binary.")
    build_parser_cmd.add_argument("--name", default=CLI_NAME, help="Binary file name.")

    return parser


def _add_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project root path.")
    parser.add_argument("--max-files", type=int, default=8000, help="Maximum number of files to scan.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code or 0)

    command = args.command
    if command == "scan":
        return run_scan(args.project, args.max_files)
    if command == "overview":
        return run_overview(args.project, args.max_files, args.max_chars)
    if command == "call-chain":
        return run_call_chain(args.project, args.max_files, args.symbol, args.direction, args.depth)
    if command == "query-symbol":
        return run_query_symbol(args.project, args.max_files, args.symbol)
    if command == "file-detail":
        return run_file_detail(args.project, args.max_files, args.file_path)
    if command == "hotspots":
        return run_hotspots(args.project, args.max_files, args.limit)
    if command == "cache":
        return run_cache(args.project, args.action)
    if command == "diff":
        return run_diff(args.project, args.json)
    if command == "git-history":
        return run_git_history(args.project, args.max_files, args.symbol)
    if command == "refs":
        return run_refs(args.project, args.max_files, args.symbol, args.json)
    if command == "orphan":
        return run_orphan(args.project, args.max_files)
    if command == "check":
        return run_check(
            project=args.project,
            types=args.types,
            max_issues=args.max_issues,
            since_commit=args.since_commit,
            modified_files=args.modified_files,
            resolve_symbols=not args.no_symbols,
        )
    if command == "doctor":
        return run_doctor()
    if command == "build-binary":
        return run_build_binary(args.output, args.name)
    parser.error(f"unknown command: {command}")
    return 2


def _resolve_project(project: str) -> str:
    return str(Path(project).resolve())


def _scan_engine(project: str, max_files: int) -> RepoMapEngine:
    engine = RepoMapEngine(_resolve_project(project))
    engine.scan(max_files=max_files)
    return engine


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


def run_overview(project: str, max_files: int, max_chars: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        print(engine.render_overview(max_chars))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] overview failed: {exc}", file=sys.stderr)
        return 1


def run_call_chain(project: str, max_files: int, symbol: str, direction: str, depth: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        matches = engine.query_symbol(symbol)
        if not matches:
            print(f"> 未找到符号 `{symbol}`", file=sys.stderr)
            return 1
        if direction != "both":
            data = engine.call_chain(matches[0].id, direction, depth)
            lines = [f"## 调用链 — `{matches[0].name}`\n"]
            for item in data[direction]:
                lines.append(f"- `{item.name}` ({item.file}:{item.line})")
            print("\n".join(lines))
            return 0
        print(engine.render_call_chain(matches[0].name, depth))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] call-chain failed: {exc}", file=sys.stderr)
        return 1


def run_query_symbol(project: str, max_files: int, symbol: str) -> int:
    try:
        engine = _scan_engine(project, max_files)
        results = engine.query_symbol(symbol)
        if not results:
            print(f"> 未找到匹配 `{symbol}` 的符号", file=sys.stderr)
            return 1
        lines = [f"找到 {len(results)} 个匹配结果（按重要性排序）:\n"]
        for item in results[:15]:
            pr = item.pagerank * 1000
            lines.append(f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}")
            if item.signature:
                lines.append(f"  ```\n  {item.signature}\n  ```")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] query-symbol failed: {exc}", file=sys.stderr)
        return 1


def run_file_detail(project: str, max_files: int, file_path: str) -> int:
    try:
        engine = _scan_engine(project, max_files)
        print(engine.render_file_detail(file_path))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] file-detail failed: {exc}", file=sys.stderr)
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
    if action == "save":
        try:
            symbols, edges = scan_project(project_path)
            cache_path = save_cache(project_path, symbols, edges)
            print(f"✅ 缓存已保存\n- 路径: `{cache_path}`\n- 符号数: {len(symbols)}\n- 依赖边: {len(edges)}")
            return 0
        except Exception as exc:
            print(f"[{CLI_NAME}] cache save failed: {exc}", file=sys.stderr)
            return 1
    cache = load_cache(project_path)
    if cache is None:
        print("❌ 缓存不存在，请先执行 `repomap cache save --project <path>`", file=sys.stderr)
        return 1
    print(
        "\n".join(
            [
                "📂 缓存信息",
                f"- 扫描时间: {cache.scan_time}",
                f"- 文件数: {cache.file_count}",
                f"- 符号数: {cache.symbol_count}",
                f"- 依赖边: {cache.edge_count}",
            ]
        )
    )
    return 0


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


def run_git_history(project: str, max_files: int, symbol: str) -> int:
    try:
        engine = _scan_engine(project, max_files)
        matches = engine.query_symbol(symbol)
        if not matches:
            print(f"> 未找到符号 `{symbol}`", file=sys.stderr)
            return 1
        target = matches[0]
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


def run_refs(project: str, max_files: int, symbol: str | None, as_json: bool) -> int:
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
            matches = [symbol_id for symbol_id in symbol_ids if symbol in symbol_id]
            if not matches:
                print(f"> 未找到符号 `{symbol}`", file=sys.stderr)
                return 1
            sid = matches[0]
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


def run_orphan(project: str, max_files: int) -> int:
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
        orphans = []
        for sid in symbol_ids:
            if len(calls_in[sid]) == 0 and len(calls_out[sid]) == 0:
                symbol = engine.graph.symbols[sid]
                if symbol.name not in {"main", "run", "lib", "mod"} and not symbol.name.startswith("get_"):
                    orphans.append(symbol)
        orphans.sort(key=lambda symbol: (symbol.file, symbol.line, symbol.name))
        lines = ["## 死代码检测\n"]
        lines.append(f"发现 {len(orphans)} 个孤立符号（不被调用也不调用别人）:\n")
        for symbol in orphans[:20]:
            lines.append(f"- `{symbol.name}` ({symbol.kind}) — `{symbol.file}:{symbol.line}`")
        if len(orphans) > 20:
            lines.append(f"\n... 还有 {len(orphans) - 20} 个")
        lines.append("\n> ⚠️ 注意：类型定义、数据结构等可能是正常的孤立符号")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] orphan failed: {exc}", file=sys.stderr)
        return 1


def _format_symbol_ref(engine: RepoMapEngine, sid: str) -> dict[str, Any]:
    symbol = engine.graph.symbols[sid]
    return {"name": symbol.name, "file": symbol.file, "line": symbol.line}


def run_check(
    project: str,
    types: list[str] | None,
    max_issues: int,
    since_commit: str | None,
    modified_files: list[str] | None,
    resolve_symbols: bool,
) -> int:
    try:
        project_root = _resolve_project(project)
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
            modified_files=modified_files,
        )
        print(_format_check_report(result, max_issues))
        return 0 if result.get("status") in {"passed", "warning", "unknown"} else 1
    except Exception as exc:
        print(f"[{CLI_NAME}] check failed: {exc}", file=sys.stderr)
        return 1


def _format_check_report(result: dict[str, Any], max_issues: int) -> str:
    lines = ["## 编译器/静态分析诊断\n"]
    lines.append(f"**项目**: `{result['project_root']}`")
    lines.append(
        f"**状态**: {'✅ 通过' if result['status'] == 'passed' else ('⚠️ 有警告' if result['status'] == 'warning' else ('ℹ️ 未检测到支持类型' if result['status'] == 'unknown' else '❌ 有错误'))}"
    )
    lines.append(f"**检测类型**: {', '.join(result.get('types', [])) or '自动检测'}")
    lines.append(f"**时间**: {result['timestamp']}\n")

    summary = result.get("summary", {})
    lines.append("### 汇总")
    lines.append(f"- 错误总数: **{summary.get('total_errors', 0)}** 🔴")
    lines.append(f"- 警告总数: **{summary.get('total_warnings', 0)}** ⚠️")
    lines.append(f"- 涉及文件: {summary.get('files_with_errors', 0)}")
    lines.append(f"- 运行工具: {summary.get('tools_run', 0)} | 跳过: {summary.get('tools_skipped', 0)}\n")

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


def run_doctor() -> int:
    from repomap_parser import TreeSitterAdapter

    adapter = TreeSitterAdapter()
    parsers = sorted(adapter.parsers)
    pyinstaller_spec = importlib.util.find_spec("PyInstaller")
    if parsers:
        print(f"tree-sitter parsers: {', '.join(parsers)}")
    else:
        print("tree-sitter bindings are missing", file=sys.stderr)
        return 1
    print(f"PyInstaller: {'available' if pyinstaller_spec is not None else 'not installed'}")
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
