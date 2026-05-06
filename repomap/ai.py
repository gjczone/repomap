from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repomap.core import RepoMapEngine

from repomap.topic import FileMatch, TestMatch, classify_file_role, get_co_change_neighbors


RISK_MARK = {"high": "[high]", "medium": "[medium]", "low": "[low]"}
VISIBILITY_MARK = {"exported": "[exported]", "public": "[public]", "private": "[private]"}
CONFIDENCE_MARK = {"high": "HIGH", "medium": "MED", "low": "LOW"}


def _truncate_output(output: str, max_chars: int) -> str:
    if max_chars <= 0 or len(output) <= max_chars:
        return output
    return output[:max_chars] + "\n\n…（超出字符限制，已截断）"


def _get_hot_files(project_root: str, days: int = 30) -> set[str]:
    """通过 git diff 获取近 N 天修改过的文件集合（路径相对于 project_root）。"""
    import subprocess
    from pathlib import Path

    try:
        # 获取 git root 用于路径转换
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_root, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return set()
    if not git_root:
        return set()

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"HEAD@{{{days}.days ago}}", "HEAD", "--", "."],
            cwd=project_root, capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()

    hot_files: set[str] = set()
    if project_root.startswith(git_root):
        rel = str(Path(project_root).relative_to(git_root))
        prefix = f"{rel}/" if rel not in ("", ".") else ""
    else:
        prefix = ""

    for line in result.stdout.strip().split("\n"):
        path = line.strip()
        if not path:
            continue
        # 去掉 git root 相对前缀，转为 project_root 相对路径
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        hot_files.add(path)
    return hot_files


def _project_summary(engine: "RepoMapEngine", granularity: str) -> str:
    """生成一句话项目摘要：语言、框架、项目类型。"""
    from repomap.parser import EXT_TO_LANG

    # 统计语言分布
    lang_counts: dict[str, int] = {}
    for f in engine.graph.file_symbols:
        ext = PurePosixPath(f).suffix.lower()
        lang = EXT_TO_LANG.get(ext, "")
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    if not lang_counts:
        return ""
    top_langs = sorted(lang_counts.items(), key=lambda x: -x[1])[:3]
    lang_names = {
        "python": "Python", "javascript": "JS", "typescript": "TS", "tsx": "TSX",
        "go": "Go", "rust": "Rust", "c": "C", "cpp": "C++", "java": "Java",
        "kotlin": "Kotlin", "swift": "Swift", "c_sharp": "C#", "php": "PHP",
        "ruby": "Ruby", "html": "HTML", "css": "CSS", "json": "JSON",
    }
    lang_str = " + ".join(
        f"{lang_names.get(l, l)} ({c}f)" for l, c in top_langs
    )

    # 检测框架
    frameworks: list[str] = []
    routes = engine.list_routes()
    if routes:
        fw_set = {r.framework for r in routes if hasattr(r, "framework")}
        frameworks.extend(sorted(fw_set))
    # 从文件列表推断框架
    file_set = set(engine.graph.file_symbols.keys())
    file_str = " ".join(file_set)
    if any(f.endswith(".rs") for f in file_set):
        if "axum" in file_str:
            frameworks.append("axum")
        if "tauri" in file_str or any("tauri" in f for f in file_set):
            frameworks.append("tauri")
        if "actix" in file_str:
            frameworks.append("actix-web")
    if any(f.endswith((".tsx", ".jsx")) for f in file_set):
        if "next.config" in file_str:
            frameworks.append("next.js")
        elif "vite.config" in file_str:
            frameworks.append("vite")
        else:
            frameworks.append("react")

    # 项目类型
    ptype = "应用"
    entries = engine.entry_points()
    if entries:
        entry_str = " ".join(entries).lower()
        if "main.rs" in entry_str or "main.go" in entry_str or "main.c" in entry_str:
            ptype = "二进制/CLI 应用"
        elif "lib.rs" in entry_str and "main.rs" not in entry_str:
            ptype = "库"
        elif any("server" in e for e in entries) or routes:
            ptype = "Web 服务"
    if "tui" in file_str or any("tui" in f.lower() for f in file_set):
        ptype = "TUI 应用" if ptype == "二进制/CLI 应用" else ptype

    parts = [f"**项目类型**: {ptype}"]
    parts.append(f"**语言**: {lang_str}")
    if frameworks:
        parts.append(f"**框架**: {', '.join(frameworks)}")
    return " | ".join(parts)


def _auto_granularity(engine: "RepoMapEngine") -> str:
    """根据项目规模自动选择报告粒度。

    - full:    < 50 个文件 —— 完整报告
    - medium:  50-300 个文件 —— 精简报告
    - compact: > 300 个文件 —— 极简报告
    """
    file_count = engine.scan_stats.processed_files
    if file_count < 50:
        return "full"
    elif file_count <= 300:
        return "medium"
    else:
        return "compact"


def render_routes_report(engine: "RepoMapEngine") -> str:
    """渲染 HTTP 路由表（独立命令用）。"""
    routes = engine.list_routes()
    if not routes:
        return "未检测到 HTTP 路由定义。"
    return _format_route_table(routes)


def _render_route_section(engine: "RepoMapEngine") -> list[str]:
    """为 overview 渲染 API 路由板块。"""
    routes = engine.list_routes()
    if not routes:
        return []
    return _format_route_lines(routes, compact=True)


def _format_route_lines(routes: list, compact: bool = False) -> list[str]:
    """格式化路由为 Markdown 行。"""
    from collections import Counter

    lines = ["## API 路由\n"]
    method_order = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4, "HEAD": 5, "OPTIONS": 6, "USE": 7, "ALL": 8}
    routes_sorted = sorted(routes, key=lambda r: (r.file, r.line))

    if compact and len(routes) > 12:
        # 压缩模式：按模块分组展示概览
        by_file: dict[str, list] = {}
        for r in routes_sorted:
            by_file.setdefault(r.file, []).append(r)
        for file, file_routes in list(by_file.items())[:6]:
            methods = Counter(r.method for r in file_routes)
            method_str = " ".join(f"{m}x{methods[m]}" for m in ("GET", "POST", "PUT", "DELETE", "PATCH") if methods[m])
            lines.append(f"- `{file}` — {len(file_routes)} 个路由（{method_str}）")
        if len(by_file) > 6:
            lines.append(f"- …还有 {len(by_file) - 6} 个文件包含路由")
    else:
        if len(routes_sorted) > 20 and compact:
            lines.append(f"> （共 {len(routes)} 个路由，以下展示 Top 20）\n")
        lines.append("| Method | Path | Handler | File | Framework |")
        lines.append("|--------|------|---------|------|-----------|")
        for r in routes_sorted[:20]:
            lines.append(f"| {r.method} | `{r.path}` | `{r.handler}` | `{r.file}:{r.line}` | {r.framework} |")
        if len(routes_sorted) > 20 and compact:
            lines.append(f"\n…还有 {len(routes_sorted) - 20} 个路由")
    lines.append("")
    return lines


def _format_route_table(routes: list) -> str:
    """格式化路由为纯文本表格。"""
    lines = _format_route_lines(routes, compact=False)
    return "\n".join(lines)


def _render_co_change_section(engine: "RepoMapEngine") -> list[str]:
    """为 overview 渲染隐式耦合板块（git 共变频率最高的文件对）。"""
    import subprocess
    from pathlib import Path

    project_root = str(engine.project_root)

    # 计算从 git root 到 project_root 的相对路径前缀
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_root,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return []
    if not git_root:
        return []
    if project_root.startswith(git_root):
        rel = str(Path(project_root).relative_to(git_root))
        git_rel_prefix = rel if rel not in ("", ".") else ""
    else:
        git_rel_prefix = ""

    # 选取分析得分最高的 8 个非测试文件作为种子
    analysis = engine.file_analysis()
    high_score_files = sorted(
        [item for item in analysis.values() if not item.get("is_test_file")],
        key=lambda item: -item.get("score", 0),
    )[:8]

    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str, int]] = []
    for entry in high_score_files:
        file_path = entry["file"]
        # 将分析路径（相对于 project_root）转换为 git 路径（相对于 git root）
        git_path = f"{git_rel_prefix}/{file_path}" if git_rel_prefix else file_path
        neighbors = get_co_change_neighbors(project_root, git_path, top_n=3)
        if not neighbors:
            continue
        # 将 git 路径转换回分析路径用于展示
        for neighbor_git_path, count in neighbors:
            display_a = file_path
            if git_rel_prefix:
                display_b = neighbor_git_path[len(git_rel_prefix) + 1:] if neighbor_git_path.startswith(git_rel_prefix + "/") else neighbor_git_path
            else:
                display_b = neighbor_git_path
            key = tuple(sorted([display_a, display_b]))
            if key in seen_pairs:
                continue
            if count < 2:
                continue
            seen_pairs.add(key)
            pairs.append((display_a, display_b, count))
        if len(pairs) >= 10:
            break

    if not pairs:
        return []

    pairs.sort(key=lambda x: -x[2])
    lines = [
        "## 隐式耦合（Git 共变）\n",
        "> 以下文件在 git 历史中频繁一起修改，可能存在未在代码中显式声明的隐含关联。\n",
    ]
    for file_a, file_b, count in pairs[:10]:
        lines.append(f"- `{file_a}` ↔ `{file_b}` — 共变 {count} 次")
    lines.append("")
    return lines


def render_overview_report(engine: "RepoMapEngine", max_chars: int = 16000,
                          with_heat: bool = False,
                          with_co_change: bool = False,
                          granularity: str = "auto") -> str:
    # 解析粒度
    if granularity == "auto":
        granularity = _auto_granularity(engine)

    # 根据粒度调整各板块的数量限制
    if granularity == "compact":
        reading_limit, module_limit, hotspot_limit, summary_files, summary_per_file, supporting_limit = 0, 5, 0, 3, 2, 3
    elif granularity == "medium":
        reading_limit, module_limit, hotspot_limit, summary_files, summary_per_file, supporting_limit = 5, 5, 5, 4, 3, 6
    else:  # full
        reading_limit, module_limit, hotspot_limit, summary_files, summary_per_file, supporting_limit = 8, 8, 10, 6, 4, 8

    lines: list[str] = []
    lines.append(f"# 项目地图 — {engine.project_root.name}")
    if granularity != "full":
        lines[-1] += f"（{granularity} 模式）"
    lines[-1] += "\n"
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

    # 一句话项目摘要
    summary = _project_summary(engine, granularity)
    if summary:
        lines.append(f"> {summary}\n")

    # 热度计算：如果启用，标记近 30 天频繁修改的文件
    hot_files: set[str] = set()
    if with_heat:
        hot_files = _get_hot_files(str(engine.project_root))

    suggestions = engine.suggested_reading_order(reading_limit)
    if suggestions:
        lines.append("## 推荐阅读顺序\n")
        for index, item in enumerate(suggestions, 1):
            hot_tag = " [HOT]" if item["file"] in hot_files else ""
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
                f"{index}. `{item['file']}`{hot_tag} — {item['reason']}；"
                f"{count_text}{highlights}"
            )
        lines.append("")

    supporting_files = engine.supporting_files(supporting_limit)
    if supporting_files:
        lines.append("## 支撑文件（非符号图）\n")
        lines.append(
            "> 符号图优先覆盖源码；以下仅动态列出关键文档、脚本和配置，不能替代 AGENTS.md/CLAUDE.md 的人工上下文。\n"
        )
        for item in supporting_files:
            lines.append(
                f"- `{item['file']}` — {item['reason']}"
                f"（{item['role']}）"
            )
        lines.append("")

    modules = engine.module_summary(module_limit)
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

    # API 路由板块
    route_lines = _render_route_section(engine)
    if route_lines:
        lines.extend(route_lines)

    hotspots = engine.hotspots(hotspot_limit)
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

    summary_sections = engine.summary_symbols(summary_files, summary_per_file)
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
                # 生成重要性说明
                importance_parts = []
                incoming = symbol_row.get("incoming_calls", 0)
                outgoing = symbol_row.get("outgoing_calls", 0)
                if incoming > 0:
                    importance_parts.append(f"← {incoming} callers")
                if outgoing > 0:
                    importance_parts.append(f"→ {outgoing} callees")
                if not importance_parts:
                    if symbol_row["kind"] == "class":
                        importance_parts.append("type definition")
                    elif symbol_row["visibility"] == "exported":
                        importance_parts.append("exported")
                    elif incoming == 0 and outgoing == 0:
                        if symbol_row.get("summary_score", 0) > 10:
                            importance_parts.append("high-importance leaf")
                        else:
                            importance_parts.append("leaf/entry")
                importance_hint = f"  ({', '.join(importance_parts)})" if importance_parts else ""
                lines.append(
                    f"- {visibility} **{symbol_row['name']}** `({symbol_row['kind']})`"
                    f" L{symbol_row['line']} Score={symbol_row['summary_score']:.2f} PR={pagerank:.1f}{importance_hint}{signature}"
                )
            lines.append("")

    # 隐式耦合：通过 git 共变历史发现的文件关联。默认关闭，避免普通 overview 触发重 git history。
    if with_co_change:
        co_change_lines = _render_co_change_section(engine)
        if co_change_lines:
            lines.extend(co_change_lines)

    # Quick Actions
    lines.append("## Quick Actions\n")
    top_file = suggestions[0]["file"] if suggestions else ""
    top_symbol = suggestions[0]["top_symbols"][0] if suggestions and suggestions[0].get("top_symbols") else ""
    lines.append(f"- 查看入口文件详情: `repomap file-detail --project . --file-path {top_file or 'repomap_core.py'}`")
    if top_symbol:
        lines.append(f"- 查看核心符号调用链: `repomap call-chain --project . --symbol {top_symbol}`")
    lines.append("- 搜索特定主题: `repomap query --project . --query <keyword>`")
    lines.append("- 检查诊断: `repomap check --project .`")
    lines.append("- 完整验证: `repomap verify --project .`")
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
    key_symbols: list[dict[str, Any]] | None = None,
    read_next: list[dict[str, str]] | None = None,
    lsp_hint: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Impact Analysis\n")

    lines.append("## Input Files\n")
    for f in target_files:
        lines.append(f"- `{f}`")
    lines.append("")

    if key_symbols or read_next:
        lines.append("## Edit Plan\n")
        lines.append("- Start with target files, then inspect high-confidence affected files and suggested tests.")
        if key_symbols:
            lines.append("- Review key symbols before changing behavior or signatures.")
        if lsp_hint and lsp_hint.get("available"):
            lines.append("- Local LSP is available; use focused diagnostics or `refs --with-lsp` when exact evidence matters.")
        lines.append("")
        # 编辑 checklist
        checklist: list[str] = []
        checklist.append("□ 阅读目标文件及 Read Next 中的高优先级文件")
        if key_symbols:
            checklist.append("□ 检查 Key Symbols 的调用链（repomap call-chain）确认影响范围")
        if affected_files:
            checklist.append("□ 逐个检查 Likely Affected Files 是否需要同步修改")
        if tests:
            checklist.append("□ 修改完成后运行 Suggested Tests 中的测试")
        checklist.append("□ 编辑完成后运行 repomap verify 做最终证据检查")
        if checklist:
            lines.append("### Edit Checklist\n")
            for item in checklist:
                lines.append(item)
            lines.append("")

    if key_symbols:
        lines.append("## Key Symbols\n")
        lines.append("| Symbol | Kind | Location | Incoming | Outgoing |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in key_symbols[:12]:
            lines.append(
                f"| `{item['name']}` | {item['kind']} | `{item['file']}:{item['line']}` | {item['incomingCount']} | {item['outgoingCount']} |"
            )
        lines.append("")

    if read_next:
        lines.append("## Read Next\n")
        for item in read_next[:10]:
            lines.append(f"- `{item['file']}` ({item['role']}): {item['reason']}")
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

    # Related Commands
    lines.append("## Related Commands\n")
    if target_files:
        lines.append(f"- 查看目标文件详情: `repomap file-detail --project . --file-path {target_files[0]}`")
    if affected_files:
        top_affected = affected_files[0][0]
        lines.append(f"- 检查首要受影响文件: `repomap file-detail --project . --file-path {top_affected}`")
    lines.append("- 验证变更: `repomap verify --project .`")
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




# ═══════════════════════════════════════════════════════════════════════════════
# verify 报告渲染
# ═══════════════════════════════════════════════════════════════════════════════


def render_verify_report(payload: dict[str, Any], max_chars: int = 10000) -> str:
    result = payload.get("result", {})
    status = result.get("status", "unknown")
    status_label = {"passed": "PASS", "warning": "WARNING", "failed": "FAILED"}.get(status, status.upper())
    lines: list[str] = ["# Verify Report\n"]

    lines.append("## Overall Status\n")
    lines.append(f"**{status_label}**")
    if status == "passed":
        lines.append("- Evidence looks sufficient for final handoff, assuming required project tests were actually run when needed.")
    elif status == "warning":
        lines.append("- Do not claim full confidence yet; review the warnings and missing evidence below.")
    else:
        lines.append("- Do not claim completion; at least one verification source failed.")
    lines.append("")

    changed_files = result.get("changedFiles", [])
    lines.append("## Changed Files\n")
    if changed_files:
        for file_path in changed_files[:30]:
            lines.append(f"- `{file_path}` ({classify_file_role(file_path)})")
        if len(changed_files) > 30:
            lines.append(f"- …还有 {len(changed_files) - 30} 个")
    else:
        lines.append("- No changed files detected in the project.")
    lines.append("")

    risk = result.get("risk", {})
    lines.append("## Risk Summary\n")
    lines.append(f"- Level: **{str(risk.get('level', 'unknown')).upper()}**")
    for reason in risk.get("reasons", []):
        lines.append(f"- {reason}")
    for missing in risk.get("missingChecks", []):
        lines.append(f"- Missing evidence: {missing}")
    lines.append("")

    tests = result.get("tests", [])
    if tests:
        lines.append("## Suggested Tests\n")
        for command in _test_commands_for_files([
            TestMatch(
                test_file=item.get("testFile", ""),
                target_file=item.get("targetFile", ""),
                confidence=item.get("confidence", ""),
                reason=item.get("reason", ""),
            )
            for item in tests
        ]):
            lines.append(f"- `{command}`")
        lines.append("")
    else:
        lines.append("## Suggested Tests\n")
        lines.append("- 未匹配到与变更文件相关的测试文件。")
        changed_files = result.get("changedFiles", [])
        if changed_files:
            # 给出通用测试命令建议
            test_hints: list[str] = []
            for f in changed_files[:3]:
                if f.endswith(".py"):
                    test_hints.append("python -m pytest")
                    break
                elif f.endswith((".ts", ".tsx", ".js", ".jsx")):
                    test_hints.append("npx vitest run")
                    break
                elif f.endswith(".go"):
                    test_hints.append("go test ./...")
                    break
                elif f.endswith(".rs"):
                    test_hints.append("cargo test")
                    break
            if test_hints:
                lines.append(f"- 建议手动运行: `{test_hints[0]}`")
            else:
                lines.append("- 建议手动运行项目测试套件。")
        lines.append("")

    untested = result.get("untestedSymbols", [])
    if untested:
        lines.append("## Test Coverage Gaps\n")
        lines.append("> 以下符号缺少测试覆盖，修改时需格外谨慎。\n")
        lines.append("| Symbol | Kind | File | Callers | Risk |")
        lines.append("|--------|------|------|:------:|:----:|")
        for item in untested[:15]:
            risk_label = "HIGH" if item["risk_score"] >= 10 else "MEDIUM" if item["risk_score"] >= 5 else "LOW"
            lines.append(
                f"| `{item['symbol']}` | {item['kind']} | `{item['file']}:{item['line']}` "
                f"| {item['incoming_calls']} | {risk_label} |"
            )
        lines.append("")

    check = result.get("check", {})
    lines.append("## Check Result\n")
    lines.append(f"- Status: **{str(check.get('status', 'unknown')).upper()}**")
    summary = check.get("summary", {})
    if summary:
        lines.append(
            f"- Errors: {summary.get('total_errors', 0)} | Warnings: {summary.get('total_warnings', 0)} | Tool failures: {summary.get('tool_failures', 0)}"
        )
    for run in check.get("runs", [])[:8]:
        marker = "skipped" if run.get("skipped") else f"exit={run.get('exit_code')}"
        lines.append(f"- {run.get('tool')}: {marker}")
    lines.append("")

    lsp = result.get("lsp", {})
    lines.append("## LSP Diagnostics\n")
    lines.append(f"- Status: **{str(lsp.get('status', 'skipped')).upper()}**")
    if lsp.get("reason"):
        lines.append(f"- Reason: {lsp['reason']}")
    lsp_summary = lsp.get("summary", {})
    if lsp_summary:
        lines.append(
            f"- Errors: {lsp_summary.get('totalErrors', 0)} | Warnings: {lsp_summary.get('totalWarnings', 0)} | Failed runs: {lsp_summary.get('failedRuns', 0)} | Skipped runs: {lsp_summary.get('skippedRuns', 0)}"
        )
    lines.append("")

    graph_diff = result.get("graphDiff", {})
    breaking_changes = graph_diff.get("breakingChanges", [])
    if breaking_changes:
        lines.append("## Breaking Changes\n")
        for bc in breaking_changes[:10]:
            risk_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
            lines.append(
                f"- {risk_icon.get(bc.get('risk', 'LOW'), '⚪')} "
                f"**{bc['name']}** `({bc.get('kind', '')})` in `{bc['file']}` "
                f"[{bc.get('risk', 'LOW')}]"
            )
            if bc.get("new_signature") and bc.get("old_signature") != bc.get("new_signature"):
                lines.append(f"  - 旧: `{bc.get('old_signature', '')}`")
                lines.append(f"  - 新: `{bc.get('new_signature', '')}`")
            if bc.get("affected_caller_count", 0) > 0:
                lines.append(f"  - {bc['affected_caller_count']} 个调用者受影响")
        lines.append("")

    lines.append("## Graph Diff\n")
    lines.append(f"- Status: **{str(graph_diff.get('status', 'skipped')).upper()}**")
    if graph_diff.get("reason"):
        lines.append(f"- Reason: {graph_diff['reason']}")
    if graph_diff.get("summary"):
        summary = graph_diff["summary"]
        lines.append(
            f"- Symbols +{summary.get('added', 0)} / -{summary.get('removed', 0)} / modified {summary.get('modified', 0)}; edges +{summary.get('edges_added', 0)} / -{summary.get('edges_removed', 0)}"
        )
    lines.append("")

    lines.append("## Final Evidence Checklist\n")
    if status == "passed":
        lines.append("- [x] Static diagnostics did not fail.")
        lines.append("- [x] Risk gate did not find high-risk or missing-check blockers.")
    else:
        lines.append("- [ ] Review failed/warning sections before final handoff.")
    if tests:
        lines.append("- [ ] Run or explicitly account for the suggested tests above.")
    if lsp.get("status") == "skipped":
        lines.append("- [ ] LSP evidence was skipped; use `--with-lsp` if exact local diagnostics are needed.")
    return _truncate_output("\n".join(lines), max_chars)


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
