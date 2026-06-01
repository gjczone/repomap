from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core import RepoMapEngine

from ..co_change import get_co_change_neighbors, co_change_load_failed
from . import (
    RISK_MARK,
    VISIBILITY_MARK,
    _truncate_output,
    logger,
)


def _get_hot_files(project_root: str, days: int = 30) -> set[str]:
    from ..git_backend import GitBackend

    try:
        git = GitBackend(project_root)
        git_root = git.show_toplevel()
    except Exception as exc:
        logger.debug(f"Git init failed for hot files: {exc}")
        return set()
    if not git_root:
        return set()

    try:
        changed = git.diff_name_only_since(days)
    except Exception as exc:
        logger.debug(f"Git diff failed for hot files: {exc}")
        return set()

    hot_files: set[str] = set()
    if project_root.startswith(git_root):
        rel = str(Path(project_root).relative_to(git_root))
        prefix = f"{rel}/" if rel not in ("", ".") else ""
    else:
        prefix = ""

    for path in changed:
        path = path.strip()
        if not path:
            continue
        if prefix and path.startswith(prefix):
            path = path[len(prefix) :]
        hot_files.add(path)
    return hot_files


def _project_summary(engine: "RepoMapEngine", granularity: str) -> str:
    from ..parser import EXT_TO_LANG

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
        "python": "Python",
        "javascript": "JS",
        "typescript": "TS",
        "tsx": "TSX",
        "go": "Go",
        "rust": "Rust",
        "c": "C",
        "cpp": "C++",
        "java": "Java",
        "kotlin": "Kotlin",
        "swift": "Swift",
        "c_sharp": "C#",
        "php": "PHP",
        "ruby": "Ruby",
        "html": "HTML",
        "css": "CSS",
        "json": "JSON",
    }
    lang_str = " + ".join(
        f"{lang_names.get(lang, lang)} ({count}f)" for lang, count in top_langs
    )

    frameworks: list[str] = []
    routes = engine.list_routes()
    if routes:
        fw_set = {r.framework for r in routes if hasattr(r, "framework")}
        frameworks.extend(sorted(fw_set))
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

    ptype = "Application"
    entries = engine.entry_points()
    if entries:
        entry_str = " ".join(entries).lower()
        if "main.rs" in entry_str or "main.go" in entry_str or "main.c" in entry_str:
            ptype = "Binary/CLI App"
        elif "lib.rs" in entry_str and "main.rs" not in entry_str:
            ptype = "Library"
        elif any("server" in e for e in entries) or routes:
            ptype = "Web Service"
    if "tui" in file_str or any("tui" in f.lower() for f in file_set):
        ptype = "TUI App" if ptype == "Binary/CLI App" else ptype

    parts = [f"**Project Type**: {ptype}"]
    parts.append(f"**Language**: {lang_str}")
    if frameworks:
        parts.append(f"**Framework**: {', '.join(frameworks)}")
    return " | ".join(parts)


def _auto_granularity(engine: "RepoMapEngine") -> str:
    file_count = engine.scan_stats.processed_files
    if file_count < 50:
        return "full"
    elif file_count <= 300:
        return "medium"
    else:
        return "compact"


def _render_co_change_section(
    engine: "RepoMapEngine", co_change_days: int = 30
) -> list[str]:
    from ..git_backend import GitBackend

    project_root = str(engine.project_root)

    try:
        git = GitBackend(project_root)
        git_root = git.show_toplevel()
    except Exception as exc:
        logger.debug(f"Git init failed for co-change: {exc}")
        return []
    if not git_root:
        return []
    if project_root.startswith(git_root):
        rel = str(Path(project_root).relative_to(git_root))
        git_rel_prefix = rel if rel not in ("", ".") else ""
    else:
        git_rel_prefix = ""

    analysis = engine.file_analysis()
    high_score_files = sorted(
        [item for item in analysis.values() if not item.get("is_test_file")],
        key=lambda item: -item.get("score", 0),
    )[:8]

    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str, int]] = []
    for entry in high_score_files:
        file_path = entry["file"]
        neighbors = get_co_change_neighbors(
            project_root, file_path, top_n=3, since_days=co_change_days
        )
        if not neighbors:
            continue
        for neighbor_git_path, count in neighbors:
            display_a = file_path
            if git_rel_prefix:
                display_b = (
                    neighbor_git_path[len(git_rel_prefix) + 1 :]
                    if neighbor_git_path.startswith(git_rel_prefix + "/")
                    else neighbor_git_path
                )
            else:
                display_b = neighbor_git_path
            key = (
                (display_a, display_b)
                if display_a <= display_b
                else (display_b, display_a)
            )
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
        "## Implicit Coupling (Git Co-change)\n",
        "> Files frequently changed together in git history; may indicate implicit dependencies not declared in code.\n",
    ]
    for file_a, file_b, count in pairs[:10]:
        lines.append(f"- `{file_a}` ↔ `{file_b}` — co-changed {count} times")
    lines.append("")
    return lines


def _render_overview_reading_order(
    lines: list[str],
    engine: "RepoMapEngine",
    reading_limit: int,
    hot_files: set[str],
) -> None:
    suggestions = engine.suggested_reading_order(reading_limit)
    if not suggestions:
        return
    lines.append("## Recommended Reading Order\n")
    for index, item in enumerate(suggestions, 1):
        hot_tag = " [HOT]" if item["file"] in hot_files else ""
        highlights = (
            f"; key symbols: {', '.join(item['top_symbols'])}"
            if item["top_symbols"]
            else ""
        )
        count_text = (
            f"Semantic symbols {item['semantic_symbol_count']}"
            if item.get("semantic_symbol_count") is not None
            and item.get("semantic_symbol_count") != item["symbol_count"]
            else f"Symbols {item['symbol_count']}"
        )
        if (
            item.get("semantic_symbol_count") is not None
            and item.get("semantic_symbol_count") != item["symbol_count"]
        ):
            count_text += f"(total symbols {item['symbol_count']})"
        lines.append(
            f"{index}. `{item['file']}`{hot_tag} — {item['reason']}；"
            f"{count_text}{highlights}"
        )
    lines.append("")


def _render_overview_supporting(
    lines: list[str], engine: "RepoMapEngine", supporting_limit: int
) -> None:
    supporting_files = engine.supporting_files(supporting_limit)
    if not supporting_files:
        return
    lines.append("## Supporting Files (non-AST)\n")
    lines.append(
        "> Source graph prioritizes source code; key docs, scripts, and configs listed below. Does not replace AGENTS.md/CLAUDE.md context.\n"
    )
    for item in supporting_files:
        lines.append(f"- `{item['file']}` — {item['reason']}（{item['role']}）")
    lines.append("")


def _render_overview_modules(
    lines: list[str], engine: "RepoMapEngine", module_limit: int
) -> None:
    modules = engine.module_summary(module_limit)
    if not modules:
        return
    lines.append("## Module Summary\n")
    for module in modules:
        highlights = (
            f"; key symbols: {', '.join(module['highlights'])}"
            if module["highlights"]
            else ""
        )
        count_text = (
            f"Semantic symbols {module['semantic_symbol_count']}"
            if module.get("semantic_symbol_count") is not None
            and module.get("semantic_symbol_count") != module["symbol_count"]
            else f"{module['symbol_count']} symbols"
        )
        if (
            module.get("semantic_symbol_count") is not None
            and module.get("semantic_symbol_count") != module["symbol_count"]
        ):
            count_text += f"(total symbols {module['symbol_count']})"
        lines.append(
            f"- `{module['module']}` — {module['file_count']} files / {count_text}"
            f"; representative `{module['representative_file']}`{highlights}"
        )
    lines.append("")


def _render_overview_clusters(lines: list[str], engine: "RepoMapEngine") -> None:
    clusters = engine.file_clusters(8)
    if not clusters:
        return
    lines.append("## Module Clusters (auto-detected)\n")
    for c in clusters:
        reps = c.get("representatives", [])[:3]
        lines.append(
            f"- **{c['label']}** ({c['size']} files): {', '.join(f'`{r}`' for r in reps)}"
        )
    lines.append("")


def _render_overview_entry_points(lines: list[str], engine: "RepoMapEngine") -> None:
    entries = engine.entry_points()
    if not entries:
        return
    lines.append("## Entry Points\n")
    for entry in entries[:6]:
        lines.append(f"- `{entry}`")
    lines.append("")


def _render_overview_hotspots(
    lines: list[str], engine: "RepoMapEngine", hotspot_limit: int
) -> None:
    hotspots = engine.hotspots(hotspot_limit)
    if not hotspots:
        return
    lines.append(
        "## High-Density Files (by semantic symbol density, label/config noise reduced)\n"
    )
    for hotspot in hotspots:
        count_text = (
            f"Semantic symbols {hotspot['semantic_symbol_count']}"
            if hotspot.get("semantic_symbol_count") is not None
            and hotspot.get("semantic_symbol_count") != hotspot["symbol_count"]
            else f"{hotspot['symbol_count']}  symbols"
        )
        if (
            hotspot.get("semantic_symbol_count") is not None
            and hotspot.get("semantic_symbol_count") != hotspot["symbol_count"]
        ):
            count_text += f"(total symbols {hotspot['symbol_count']})"
        lines.append(
            f"- {RISK_MARK.get(hotspot['risk'], '[info]')} `{hotspot['file']}`"
            f" — {count_text}"
        )
    lines.append("")


def _render_overview_key_symbols(
    lines: list[str],
    engine: "RepoMapEngine",
    summary_files: int,
    summary_per_file: int,
) -> None:
    summary_sections = engine.summary_symbols(summary_files, summary_per_file)
    if not summary_sections:
        return
    lines.append("## Key Implementation Symbols\n")
    lines.append(
        "> Implementation symbols ranked by importance; tests, HTML tags, CSS selectors, and JSON keys are deprioritized.\n"
    )
    for section in summary_sections:
        lines.append(f"### `{section['file']}`\n")
        if section.get("reason"):
            lines.append(f"- Reason: {section['reason']}")
        for symbol_row in section["symbols"]:
            pagerank = symbol_row["pagerank"] * 1000
            visibility = VISIBILITY_MARK.get(symbol_row["visibility"], "[private]")
            signature = (
                f"  \n  *`{symbol_row['signature']}`*"
                if symbol_row["signature"]
                else ""
            )
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
            importance_hint = (
                f"  ({', '.join(importance_parts)})" if importance_parts else ""
            )
            lines.append(
                f"- {visibility} **{symbol_row['name']}** `({symbol_row['kind']})`"
                f" L{symbol_row['line']} Score={symbol_row['summary_score']:.2f} PR={pagerank:.1f}{importance_hint}{signature}"
            )
        lines.append("")


def render_overview_report(
    engine: "RepoMapEngine",
    max_chars: int = 16000,
    with_heat: bool = False,
    with_co_change: bool = False,
    granularity: str = "auto",
    co_change_days: int = 30,
) -> str:
    if granularity == "auto":
        granularity = _auto_granularity(engine)

    if granularity == "compact":
        (
            reading_limit,
            module_limit,
            hotspot_limit,
            summary_files,
            summary_per_file,
            supporting_limit,
        ) = 0, 5, 0, 3, 2, 3
    elif granularity == "medium":
        (
            reading_limit,
            module_limit,
            hotspot_limit,
            summary_files,
            summary_per_file,
            supporting_limit,
        ) = 5, 5, 5, 4, 3, 6
    else:  # full
        (
            reading_limit,
            module_limit,
            hotspot_limit,
            summary_files,
            summary_per_file,
            supporting_limit,
        ) = 8, 8, 10, 6, 4, 8

    lines: list[str] = []
    lines.append(f"# Project Map — {engine.project_root.name}")
    if granularity != "full":
        lines[-1] += f" ({granularity} mode)"
    lines[-1] += "\n"
    file_analysis = engine.file_analysis()
    semantic_symbol_total = round(
        sum(row.get("semantic_symbol_count", 0.0) for row in file_analysis.values()), 1
    )

    edge_count = sum(len(v) for v in engine.graph.outgoing.values())
    import_config_count = (
        len(engine._resolver.import_configs) if engine._resolver else 0
    )

    stats_line = (
        f"**Files**: {engine.scan_stats.processed_files}  "
        f"**Symbols**: {len(engine.graph.symbols)}  "
        f"**Semantic symbols**: {semantic_symbol_total}  "
        f"**Edges**: {edge_count}  "
        f"**Filtered paths**: {engine.scan_stats.filtered_path_files}  "
        f"**Filtered large files**: {engine.scan_stats.filtered_large_files}"
    )
    if import_config_count:
        stats_line += f"  **Import configs**: {import_config_count}"
    lines.append(stats_line + "\n")

    if engine.scan_stats.truncated_files:
        lines.append(
            f"> `max_files` truncated {engine.scan_stats.truncated_files} candidate files\n"
        )

    summary = _project_summary(engine, granularity)
    if summary:
        lines.append(f"> {summary}\n")

    hot_files: set[str] = set()
    if with_heat:
        hot_files = _get_hot_files(str(engine.project_root))

    _render_overview_reading_order(lines, engine, reading_limit, hot_files)
    _render_overview_supporting(lines, engine, supporting_limit)
    _render_overview_modules(lines, engine, module_limit)
    _render_overview_clusters(lines, engine)
    _render_overview_entry_points(lines, engine)
    _render_overview_hotspots(lines, engine, hotspot_limit)
    _render_overview_key_symbols(lines, engine, summary_files, summary_per_file)

    from .routes import _render_route_section

    route_lines = _render_route_section(engine)
    if route_lines:
        lines.extend(route_lines)

    if with_co_change:
        co_change_lines = _render_co_change_section(
            engine, co_change_days=co_change_days
        )
        if co_change_lines:
            lines.extend(co_change_lines)
        if co_change_load_failed():
            lines.append("> ⚠ co-change analysis unavailable (git error)\n")
    return _truncate_output("\n".join(lines), max_chars)
