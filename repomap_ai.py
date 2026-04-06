from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from repomap_support import Symbol

if TYPE_CHECKING:
    from repomap_core import RepoMapEngine


RISK_MARK = {"high": "[high]", "medium": "[medium]", "low": "[low]"}
VISIBILITY_MARK = {"exported": "[exported]", "public": "[public]", "private": "[private]"}


def render_overview_report(engine: "RepoMapEngine", max_chars: int = 48000) -> str:
    lines: list[str] = []
    lines.append(f"# 项目地图 — {engine.project_root.name}\n")

    # 计算依赖边数
    edge_count = sum(len(v) for v in engine.graph.outgoing.values())
    # 获取解析配置数量
    import_config_count = len(engine._resolver.import_configs) if engine._resolver else 0

    stats_line = (
        f"**文件数**: {engine.scan_stats.processed_files}  "
        f"**符号数**: {len(engine.graph.symbols)}  "
        f"**依赖边**: {edge_count}  "
        f"**过滤路径**: {engine.scan_stats.filtered_path_files}  "
        f"**过滤大文件**: {engine.scan_stats.filtered_large_files}"
    )
    if import_config_count:
        stats_line += f"  **解析配置**: {import_config_count}"
    lines.append(stats_line + "\n")

    if engine.scan_stats.truncated_files:
        lines.append(f"> `max_files` 截断了 {engine.scan_stats.truncated_files} 个候选文件\n")

    suggestions = engine.suggested_reading_order(8)
    if suggestions:
        lines.append("## 推荐阅读顺序\n")
        for index, item in enumerate(suggestions, 1):
            highlights = f"；关键符号: {', '.join(item['top_symbols'])}" if item["top_symbols"] else ""
            lines.append(
                f"{index}. `{item['file']}` — {item['reason']}；"
                f"符号数 {item['symbol_count']}{highlights}"
            )
        lines.append("")

    modules = engine.module_summary(8)
    if modules:
        lines.append("## 模块摘要\n")
        for module in modules:
            highlights = f"；关键符号: {', '.join(module['highlights'])}" if module["highlights"] else ""
            lines.append(
                f"- `{module['module']}` — {module['file_count']} 文件 / {module['symbol_count']} 符号"
                f"；代表文件 `{module['representative_file']}`{highlights}"
            )
        lines.append("")

    entries = engine.entry_points()
    if entries:
        lines.append("## 入口点\n")
        for entry in entries[:6]:
            lines.append(f"- `{entry}`")
        lines.append("")

    hotspots = engine.hotspots(10)
    if hotspots:
        lines.append("## 高密度文件（符号数多，可能是核心业务）\n")
        for hotspot in hotspots:
            lines.append(
                f"- {RISK_MARK.get(hotspot['risk'], '[info]')} `{hotspot['file']}`"
                f" — {hotspot['symbol_count']} 个符号"
            )
        lines.append("")

    top_symbols = sorted(engine.graph.symbols.values(), key=lambda symbol: symbol.pagerank, reverse=True)[:80]
    by_file: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in top_symbols:
        by_file[symbol.file].append(symbol)

    lines.append("## 核心符号（按 PageRank 重要性，Top 80）\n")
    for file_path in sorted(by_file, key=lambda path: -sum(symbol.pagerank for symbol in by_file[path])):
        lines.append(f"### `{file_path}`\n")
        for symbol in sorted(by_file[file_path], key=lambda item: -item.pagerank):
            pagerank = symbol.pagerank * 1000
            visibility = VISIBILITY_MARK.get(symbol.visibility, "[private]")
            signature = f"  \n  *`{symbol.signature}`*" if symbol.signature else ""
            docstring = f"  \n  > {symbol.docstring[:120]}" if symbol.docstring else ""
            lines.append(
                f"- {visibility} **{symbol.name}** `({symbol.kind})`"
                f" L{symbol.line} PR={pagerank:.1f}{signature}{docstring}"
            )
        lines.append("")

    output = "\n".join(lines)
    if len(output) > max_chars:
        output = output[:max_chars] + "\n\n…（超出字符限制，已截断）"
    return output


def render_call_chain_report(engine: "RepoMapEngine", symbol_name: str, max_depth: int = 3) -> str:
    matches = engine.query_symbol(symbol_name)
    if not matches:
        return f"> 未找到符号 `{symbol_name}`"

    symbol = matches[0]
    chain = engine.call_chain(symbol.id, "both", max_depth)
    lines = [
        f"## 调用链 — `{symbol.name}`\n",
        f"- **类型**: {symbol.kind}",
        f"- **位置**: `{symbol.file}:{symbol.line}`",
        f"- **重要性**: PR={symbol.pagerank * 1000:.1f}",
        f"- **签名**: `{symbol.signature}`" if symbol.signature else "",
        "",
    ]

    callers = chain["callers"]
    lines.append(f"### 被以下符号调用（{len(callers)}）\n")
    if callers:
        for caller in callers[:20]:
            lines.append(f"- `{caller.name}` ({caller.kind}) — `{caller.file}:{caller.line}`")
        if len(callers) > 20:
            lines.append(f"- …还有 {len(callers) - 20} 个")
    else:
        lines.append("- （无，可能是入口点）")

    callees = chain["callees"]
    lines.append(f"\n### 调用了以下符号（{len(callees)}）\n")
    if callees:
        for callee in callees[:20]:
            lines.append(f"- `{callee.name}` ({callee.kind}) — `{callee.file}:{callee.line}`")
        if len(callees) > 20:
            lines.append(f"- …还有 {len(callees) - 20} 个")
    else:
        lines.append("- （无，叶子函数）")

    return "\n".join(line for line in lines if line is not None)


def render_file_detail_report(engine: "RepoMapEngine", file_path: str) -> str:
    symbol_ids = engine.graph.file_symbols.get(file_path, [])
    if not symbol_ids:
        matches = [path for path in engine.graph.file_symbols if file_path in path]
        if matches:
            file_path = matches[0]
            symbol_ids = engine.graph.file_symbols[file_path]
        else:
            return f"> 文件 `{file_path}` 未找到或无符号"

    analysis = engine.file_analysis().get(file_path, {})
    symbols = sorted(
        [engine.graph.symbols[symbol_id] for symbol_id in symbol_ids if symbol_id in engine.graph.symbols],
        key=lambda symbol: symbol.line,
    )

    lines = [
        f"## 文件详情 — `{file_path}`\n",
        f"共 {len(symbols)} 个符号",
    ]
    if analysis:
        lines.append(
            f"跨文件关联 {analysis.get('neighbor_count', 0)} 个，"
            f"导出符号 {analysis.get('exported_count', 0)} 个\n"
        )
    else:
        lines.append("")

    for symbol in symbols:
        pagerank = symbol.pagerank * 1000
        lines.append(f"### `{symbol.name}` ({symbol.kind}) — L{symbol.line}  PR={pagerank:.1f}")
        if symbol.signature:
            lines.append(f"```\n{symbol.signature}\n```")
        if symbol.docstring:
            lines.append(f"> {symbol.docstring[:200]}")
        callers = [
            engine.graph.symbols[edge.source].name
            for edge in engine.graph.incoming.get(symbol.id, [])
            if edge.kind == "call" and edge.source in engine.graph.symbols
        ][:5]
        if callers:
            lines.append(f"**被调用**: {', '.join(callers)}")
        lines.append("")
    return "\n".join(lines)
