from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repomap_core import RepoMapEngine

from repomap_topic import FileMatch, TestMatch, classify_file_role


RISK_MARK = {"high": "[high]", "medium": "[medium]", "low": "[low]"}
VISIBILITY_MARK = {"exported": "[exported]", "public": "[public]", "private": "[private]"}
CONFIDENCE_MARK = {"high": "HIGH", "medium": "MED", "low": "LOW"}


def _truncate_output(output: str, max_chars: int) -> str:
    if max_chars <= 0 or len(output) <= max_chars:
        return output
    return output[:max_chars] + "\n\n…（超出字符限制，已截断）"


def render_overview_report(engine: "RepoMapEngine", max_chars: int = 16000) -> str:
    lines: list[str] = []
    lines.append(f"# 项目地图 — {engine.project_root.name}\n")
    file_analysis = engine.file_analysis()
    semantic_symbol_total = round(sum(row.get("semantic_symbol_count", 0.0) for row in file_analysis.values()), 1)

    # 计算依赖边数
    edge_count = sum(len(v) for v in engine.graph.outgoing.values())
    # 获取解析配置数量
    import_config_count = len(engine._resolver.import_configs) if engine._resolver else 0

    stats_line = (
        f"**文件数**: {engine.scan_stats.processed_files}  "
        f"**符号数**: {len(engine.graph.symbols)}  "
        f"**有效符号**: {semantic_symbol_total}  "
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
            count_text = (
                f"有效符号 {item['semantic_symbol_count']}"
                if item.get("semantic_symbol_count") is not None
                and item.get("semantic_symbol_count") != item["symbol_count"]
                else f"符号数 {item['symbol_count']}"
            )
            if (
                item.get("semantic_symbol_count") is not None
                and item.get("semantic_symbol_count") != item["symbol_count"]
            ):
                count_text += f"（总符号 {item['symbol_count']}）"
            lines.append(
                f"{index}. `{item['file']}` — {item['reason']}；"
                f"{count_text}{highlights}"
            )
        lines.append("")

    modules = engine.module_summary(8)
    if modules:
        lines.append("## 模块摘要\n")
        for module in modules:
            highlights = f"；关键符号: {', '.join(module['highlights'])}" if module["highlights"] else ""
            count_text = (
                f"有效符号 {module['semantic_symbol_count']}"
                if module.get("semantic_symbol_count") is not None
                and module.get("semantic_symbol_count") != module["symbol_count"]
                else f"{module['symbol_count']} 符号"
            )
            if (
                module.get("semantic_symbol_count") is not None
                and module.get("semantic_symbol_count") != module["symbol_count"]
            ):
                count_text += f"（总符号 {module['symbol_count']}）"
            lines.append(
                f"- `{module['module']}` — {module['file_count']} 文件 / {count_text}"
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
        lines.append("## 高密度文件（按有效符号密度，默认降低标签/配置噪音）\n")
        for hotspot in hotspots:
            count_text = (
                f"有效符号 {hotspot['semantic_symbol_count']}"
                if hotspot.get("semantic_symbol_count") is not None
                and hotspot.get("semantic_symbol_count") != hotspot["symbol_count"]
                else f"{hotspot['symbol_count']} 个符号"
            )
            if (
                hotspot.get("semantic_symbol_count") is not None
                and hotspot.get("semantic_symbol_count") != hotspot["symbol_count"]
            ):
                count_text += f"（总符号 {hotspot['symbol_count']}）"
            lines.append(
                f"- {RISK_MARK.get(hotspot['risk'], '[info]')} `{hotspot['file']}`"
                f" — {count_text}"
            )
        lines.append("")

    summary_sections = engine.summary_symbols(6, 4)
    if summary_sections:
        lines.append("## 关键实现符号\n")
        lines.append("> 这里优先展示更适合阅读和改动分析的实现符号，默认降低测试、HTML 标签、CSS selector、JSON key 等低语义噪音。\n")
        for section in summary_sections:
            lines.append(f"### `{section['file']}`\n")
            if section.get("reason"):
                lines.append(f"- 理由: {section['reason']}")
            for symbol_row in section["symbols"]:
                pagerank = symbol_row["pagerank"] * 1000
                visibility = VISIBILITY_MARK.get(symbol_row["visibility"], "[private]")
                signature = f"  \n  *`{symbol_row['signature']}`*" if symbol_row["signature"] else ""
                lines.append(
                    f"- {visibility} **{symbol_row['name']}** `({symbol_row['kind']})`"
                    f" L{symbol_row['line']} Score={symbol_row['summary_score']:.2f} PR={pagerank:.1f}{signature}"
                )
            lines.append("")

    return _truncate_output("\n".join(lines), max_chars)


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


def render_file_detail_report(
    engine: "RepoMapEngine",
    file_path: str,
    max_symbols: int = 12,
    max_chars: int = 6000,
) -> str:
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
    visible_symbols = symbols if max_symbols <= 0 else symbols[:max_symbols]

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

    if max_symbols > 0 and len(symbols) > len(visible_symbols):
        lines.append(f"默认仅展开前 {len(visible_symbols)} 个符号，剩余 {len(symbols) - len(visible_symbols)} 个可用 `--max-symbols` 查看。\n")

    for symbol in visible_symbols:
        pagerank = symbol.pagerank * 1000
        lines.append(f"- `{symbol.name}` ({symbol.kind}) — L{symbol.line} PR={pagerank:.1f}")
        if symbol.signature:
            lines.append(f"  - sig: `{symbol.signature}`")
        if symbol.docstring:
            lines.append(f"  - doc: {symbol.docstring[:120]}")
        callers = [
            engine.graph.symbols[edge.source].name
            for edge in engine.graph.incoming.get(symbol.id, [])
            if edge.kind == "call" and edge.source in engine.graph.symbols
        ][:5]
        if callers:
            lines.append(f"  - called by: {', '.join(callers)}")
        lines.append("")
    return _truncate_output("\n".join(lines), max_chars)


# ═══════════════════════════════════════════════════════════════════════════════
# query 报告渲染
# ═══════════════════════════════════════════════════════════════════════════════


def render_query_report(
    engine: "RepoMapEngine",
    query: str,
    file_matches: list[FileMatch],
    tests: list[TestMatch],
    max_files: int,
    max_symbols: int,
    max_chars: int = 12000,
) -> str:
    lines: list[str] = []
    lines.append(f"# Topic Map — {query}\n")
    lines.append(f"Query: `{query}`")
    lines.append(f"Project: `{engine.project_root}`")
    lines.append(f"Files considered: {engine.scan_stats.processed_files}")
    lines.append(f"Matched files: {len(file_matches)}")

    sym_count = sum(
        len(engine.graph.file_symbols.get(m.path, []))
        for m in file_matches[:max_files]
    )
    lines.append(f"Matched symbols: {sym_count}\n")

    # Summary
    roles = set(m.role for m in file_matches if m.role != "other")
    role_hint = f"横跨 {'、'.join(sorted(roles))}" if roles else ""
    if role_hint:
        lines.append(f"## Summary\n{query} 主题{role_hint}。\n")

    # Recommended Reading Order
    analysis = engine.file_analysis()
    reading_order = _build_query_reading_order(file_matches, analysis, max_files)
    if reading_order:
        lines.append("## Recommended Reading Order\n")
        for i, item in enumerate(reading_order, 1):
            lines.append(f"{i}. `{item['file']}` — {item['reason']}")
        lines.append("")

    # Core Files（只含非测试文件）
    core = [m for m in file_matches[:max_files] if m.score >= 30 and m.role != "test"]
    if core:
        lines.append("## Core Files\n")
        lines.append("| File | Role | Score | Why |")
        lines.append("| --- | --- | ---: | --- |")
        for m in core[:10]:
            why = "; ".join(m.reasons[:2]) if m.reasons else "-"
            lines.append(f"| `{m.path}` | {m.role} | {m.score:.0f} | {why} |")
        lines.append("")

    # Supporting Files
    supporting = [m for m in file_matches[:max_files] if m.score < 30]
    if supporting:
        lines.append("## Supporting Files\n")
        for m in supporting[:10]:
            why = "; ".join(m.reasons[:2]) if m.reasons else "-"
            lines.append(f"- `{m.path}` ({m.role}, score={m.score:.0f}): {why}")
        lines.append("")

    # Tests
    if tests:
        lines.append("## Tests\n")
        lines.append("| Test File | Covers | Confidence |")
        lines.append("| --- | --- | --- |")
        for t in tests[:15]:
            lines.append(f"| `{t.test_file}` | `{t.target_file}` | {t.confidence} |")
        lines.append("")

    # Key Symbols
    symbols_shown = 0
    lines.append("## Key Symbols\n")
    lines.append("| Symbol | File | Line | Role |")
    lines.append("| --- | --- | ---: | --- |")
    for m in file_matches[:max_files]:
        if symbols_shown >= max_symbols:
            break
        ranked = _rank_symbols_for_file(engine, m.path)
        for sym in ranked[:5]:
            if symbols_shown >= max_symbols:
                break
            role_hint = classify_file_role(m.path)
            lines.append(f"| `{sym['name']}` | `{m.path}` | {sym['line']} | {role_hint} |")
            symbols_shown += 1
    lines.append("")

    # Related Commands
    if file_matches:
        top_file = file_matches[0].path
        top_symbols = _rank_symbols_for_file(engine, top_file)
        lines.append("## Related Commands\n")
        lines.append(f"- `repomap file-detail --project . --file-path {top_file}`")
        if top_symbols:
            lines.append(f"- `repomap refs --project . --symbol {top_symbols[0]['name']}`")
            lines.append(f"- `repomap call-chain --project . --symbol {top_symbols[0]['name']}`")

    return _truncate_output("\n".join(lines), max_chars)


def _build_query_reading_order(
    file_matches: list[FileMatch],
    analysis: dict,
    max_files: int,
) -> list[dict[str, Any]]:
    order: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 入口点优先
    for m in file_matches:
        if m.path in seen:
            continue
        if any(m.path.endswith(suffix) for suffix in ["index.ts", "index.tsx", "main.ts", "main.py"]):
            order.append({"file": m.path, "reason": "入口点/索引"})
            seen.add(m.path)

    # 高分数核心文件
    for m in file_matches:
        if m.path in seen:
            continue
        if m.score >= 60:
            file_data = analysis.get(m.path, {})
            neighbor_count = file_data.get("neighbor_count", 0)
            reason = f"高分匹配 (score={m.score:.0f})"
            if neighbor_count >= 3:
                reason += "，跨模块枢纽"
            order.append({"file": m.path, "reason": reason})
            seen.add(m.path)

    # 剩余匹配
    for m in file_matches:
        if m.path in seen:
            continue
        order.append({"file": m.path, "reason": f"相关匹配 (score={m.score:.0f})"})
        seen.add(m.path)
        if len(order) >= max_files:
            break

    return order[:max_files]


def _rank_symbols_for_file(engine: "RepoMapEngine", file_path: str) -> list[dict[str, Any]]:
    symbols = [
        engine.graph.symbols[sid]
        for sid in engine.graph.file_symbols.get(file_path, [])
        if sid in engine.graph.symbols
    ]
    ranked = sorted(
        symbols,
        key=lambda s: (-s.pagerank, s.line),
    )
    return [{"name": s.name, "kind": s.kind, "line": s.line, "pagerank": s.pagerank} for s in ranked]


# ═══════════════════════════════════════════════════════════════════════════════
# impact 报告渲染
# ═══════════════════════════════════════════════════════════════════════════════


def render_impact_report(
    engine: "RepoMapEngine",
    target_files: list[str],
    affected_files: list[tuple[str, str, str]],  # (file, why, confidence)
    tests: list[TestMatch],
    risk_level: str,
    risk_notes: list[str],
    max_chars: int = 8000,
) -> str:
    lines: list[str] = []
    lines.append("# Impact Analysis\n")

    lines.append("## Input Files\n")
    for f in target_files:
        lines.append(f"- `{f}`")
    lines.append("")

    if affected_files:
        lines.append("## Likely Affected Files\n")
        lines.append("| File | Why | Confidence |")
        lines.append("| --- | --- | --- |")
        for f, why, conf in affected_files[:20]:
            lines.append(f"| `{f}` | {why} | {conf} |")
        lines.append("")

    areas = _extract_impact_areas(target_files, affected_files)
    if areas:
        lines.append("## Impact Areas\n")
        for area in areas:
            lines.append(f"- {area}")
        lines.append("")

    if tests:
        lines.append("## Suggested Tests\n")
        for t in tests:
            lines.append(f"- `{t.test_file}` ({t.confidence} confidence: {t.reason})")
        lines.append("")

    risk_icon = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
    lines.append(f"## Risk Level: {risk_icon.get(risk_level, risk_level)}\n")
    if risk_notes:
        lines.append("## Risk Notes\n")
        for note in risk_notes:
            lines.append(f"- {note}")
        lines.append("")

    return _truncate_output("\n".join(lines), max_chars)


def _extract_impact_areas(
    target_files: list[str],
    affected_files: list[tuple[str, str, str]],
) -> list[str]:
    areas: set[str] = set()
    all_files = target_files + [f for f, _, _ in affected_files]
    for f in all_files:
        parts = PurePosixPath(f).parts
        if len(parts) >= 2:
            top = parts[0] if parts[0] not in ("src", "app", "lib") else (
                parts[1] if len(parts) >= 2 else parts[0]
            )
            areas.add(top)
    return sorted(areas)[:8]


# ═══════════════════════════════════════════════════════════════════════════════
# diff-risk 报告渲染
# ═══════════════════════════════════════════════════════════════════════════════


def render_diff_risk_report(
    engine: "RepoMapEngine",
    changed_files: list[str],
    affected_files: list[tuple[str, str, str]],
    tests: list[TestMatch],
    risk_level: str,
    risk_reasons: list[str],
    missing_checks: list[str],
    max_chars: int = 8000,
) -> str:
    lines: list[str] = []
    lines.append("# Diff Risk Report\n")

    lines.append("## Changed Files\n")
    for f in changed_files:
        role = classify_file_role(f)
        lines.append(f"- `{f}` ({role})")
    lines.append("")

    areas = _extract_impact_areas(changed_files, affected_files)
    if areas:
        lines.append("## Changed Areas\n")
        for area in areas:
            lines.append(f"- {area}")
        lines.append("")

    lines.append(f"## Risk Level\n{risk_level.upper()}\n")

    if risk_reasons:
        lines.append("## Why\n")
        for reason in risk_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    if tests:
        lines.append("## Suggested Tests\n")
        test_cmds = _test_commands_for_files(tests)
        for cmd in test_cmds:
            lines.append(f"- `{cmd}`")
        lines.append("")

    manual = _suggest_manual_verification(changed_files, risk_level)
    if manual:
        lines.append("## Manual Verification\n")
        for item in manual:
            lines.append(f"- {item}")
        lines.append("")

    if missing_checks:
        lines.append("## Potentially Missing Checks\n")
        for check in missing_checks:
            lines.append(f"- {check}")
        lines.append("")

    return _truncate_output("\n".join(lines), max_chars)


def _test_commands_for_files(tests: list[TestMatch]) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for t in tests:
        if t.test_file not in seen:
            seen.add(t.test_file)
            if t.test_file.endswith((".ts", ".tsx", ".js", ".jsx")):
                commands.append(f"npx vitest run {t.test_file}")
            elif t.test_file.endswith(".py"):
                commands.append(f"python -m pytest {t.test_file} -v")
            elif t.test_file.endswith(".go"):
                commands.append(f"go test ./{PurePosixPath(t.test_file).parent}")
            elif t.test_file.endswith(".rs"):
                commands.append(f"cargo test -- {t.test_file}")
            else:
                commands.append(f"# run tests in {t.test_file}")
    return commands[:10]


def _suggest_manual_verification(changed_files: list[str], risk_level: str) -> list[str]:
    items: list[str] = []
    all_paths = " ".join(changed_files).lower()
    if any(kw in all_paths for kw in ["terminal", "cli", "tui", "input"]):
        items.append("在终端中运行常用命令验证输入/输出正常")
    if any(kw in all_paths for kw in ["auth", "login", "token", "session"]):
        items.append("验证登录/登出流程正常")
    if any(kw in all_paths for kw in ["ui", "component", "page", "view"]):
        items.append("在浏览器中检查相关页面渲染和交互")
    if risk_level == "high":
        items.append("考虑在 staging 环境做一次完整的回归测试")
    return items[:5]
