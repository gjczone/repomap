from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core import RepoMapEngine

from ..topic import FileMatch, TestMatch, classify_file_role
from . import _truncate_output


def _build_query_reading_order(
    file_matches: list[FileMatch],
    analysis: dict,
    max_files: int,
) -> list[dict[str, Any]]:
    order: list[dict[str, Any]] = []
    seen: set[str] = set()

    for m in file_matches:
        if m.path in seen:
            continue
        if any(
            m.path.endswith(suffix)
            for suffix in ["index.ts", "index.tsx", "main.ts", "main.py"]
        ):
            order.append({"file": m.path, "reason": "Entry point / index"})
            seen.add(m.path)

    for m in file_matches:
        if m.path in seen:
            continue
        if m.score >= 60:
            file_data = analysis.get(m.path, {})
            neighbor_count = file_data.get("neighbor_count", 0)
            reason = f"High-score match (score={m.score:.0f})"
            if neighbor_count >= 3:
                reason += ", cross-module hub"
            order.append({"file": m.path, "reason": reason})
            seen.add(m.path)

    for m in file_matches:
        if m.path in seen:
            continue
        order.append({"file": m.path, "reason": f"Related match (score={m.score:.0f})"})
        seen.add(m.path)
        if len(order) >= max_files:
            break

    return order[:max_files]


def _rank_symbols_for_file(
    engine: "RepoMapEngine", file_path: str
) -> list[dict[str, Any]]:
    symbols = [
        engine.graph.symbols[sid]
        for sid in engine.graph.file_symbols.get(file_path, [])
        if sid in engine.graph.symbols
    ]
    ranked = sorted(
        symbols,
        key=lambda s: (-s.pagerank, s.line),
    )
    return [
        {
            "name": s.name,
            "kind": s.kind,
            "line": s.line,
            "end_line": s.end_line,
            "pagerank": s.pagerank,
            "id": s.id,
        }
        for s in ranked
    ]


def render_query_report(
    engine: "RepoMapEngine",
    query: str,
    file_matches: list[FileMatch],
    tests: list[TestMatch],
    max_files: int,
    max_symbols: int,
    max_chars: int = 12000,
    context_lines: int = 2,
) -> str:
    lines: list[str] = []
    lines.append(f"# Topic Map — {query}\n")
    lines.append(f"Query: `{query}`")
    lines.append(f"Project: `{engine.project_root}`")
    lines.append(f"Files considered: {engine.scan_stats.processed_files}")
    lines.append(f"Matched files: {len(file_matches)}")

    sym_count = sum(
        len(engine.graph.file_symbols.get(m.path, [])) for m in file_matches[:max_files]
    )
    lines.append(f"Matched symbols: {sym_count}\n")

    core_count = sum(1 for m in file_matches if m.role not in ("other", "test"))
    test_count = sum(1 for m in file_matches if m.role == "test")
    parts = [f"{len(file_matches)} files matched"]
    if core_count:
        parts.append(f"{core_count} implementation")
    if test_count:
        parts.append(f"{test_count} test")
    lines.append(f"## Summary\n{', '.join(parts)}.\n")

    analysis = engine.file_analysis()
    reading_order = _build_query_reading_order(file_matches, analysis, max_files)
    if reading_order:
        lines.append("## Recommended Reading Order\n")
        for i, item in enumerate(reading_order, 1):
            lines.append(f"{i}. `{item['file']}` — {item['reason']}")
        lines.append("")

    core = [m for m in file_matches[:max_files] if m.score >= 30 and m.role != "test"]
    if core:
        lines.append("## Core Files\n")
        lines.append("| File | Role | Score | Why |")
        lines.append("| --- | --- | ---: | --- |")
        for m in core[:10]:
            why = "; ".join(m.reasons[:2]) if m.reasons else "-"
            lines.append(f"| `{m.path}` | {m.role} | {m.score:.0f} | {why} |")
        lines.append("")

    supporting = [m for m in file_matches[:max_files] if m.score < 30]
    if supporting:
        lines.append("## Supporting Files\n")
        for m in supporting[:10]:
            why = "; ".join(m.reasons[:2]) if m.reasons else "-"
            lines.append(f"- `{m.path}` ({m.role}, score={m.score:.0f}): {why}")
        lines.append("")

    if tests:
        lines.append("## Tests\n")
        lines.append("| Test File | Covers | Confidence |")
        lines.append("| --- | --- | --- |")
        for t in tests[:15]:
            lines.append(f"| `{t.test_file}` | `{t.target_file}` | {t.confidence} |")
        lines.append("")

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
            range_str = ""
            sym_end = sym.get("end_line", sym["line"])
            if (sym_end - sym["line"]) > 100:
                range_str = f" (L{sym['line']}-L{sym_end})"
            role_hint = classify_file_role(m.path, engine.graph)
            lines.append(
                f"| `{sym['name']}`{range_str} | `{m.path}` | {sym['line']} | {role_hint} |"
            )
            symbols_shown += 1
    lines.append("")

    if file_matches:
        top_file = file_matches[0].path
        top_symbols = _rank_symbols_for_file(engine, top_file)
        lines.append("## Related Commands\n")
        lines.append(f"- `repomap query --file {top_file} --project .`")
        if top_symbols:
            lines.append(
                f"- `repomap query --symbol {top_symbols[0]['name']} --project .`"
            )
            lines.append(
                f"- `repomap call-chain --project . --symbol {top_symbols[0]['name']}`"
            )

    return _truncate_output("\n".join(lines), max_chars)
