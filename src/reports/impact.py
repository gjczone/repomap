from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core import RepoMapEngine

from ..topic import TestMatch
from . import _truncate_output


def _extract_impact_areas(
    target_files: list[str],
    affected_files: list[tuple[str, str, str]],
) -> list[str]:
    areas: set[str] = set()
    all_files = target_files + [f for f, _, _ in affected_files]
    for f in all_files:
        parts = PurePosixPath(f).parts
        if len(parts) >= 2:
            top = (
                parts[0]
                if parts[0] not in ("src", "app", "lib")
                else (parts[1] if len(parts) >= 2 else parts[0])
            )
            areas.add(top)
    return sorted(areas)[:8]


def render_impact_report(
    engine: "RepoMapEngine",
    target_files: list[str],
    affected_files: list[tuple[str, str, str]],
    tests: list[TestMatch],
    risk_level: str,
    risk_notes: list[str],
    max_chars: int = 8000,
    key_symbols: list[dict[str, Any]] | None = None,
    read_next: list[dict[str, str]] | None = None,
    lsp_hint: dict[str, Any] | None = None,
    compact: bool = False,
    top_n: int = 5,
    total_tests: int | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Impact Analysis\n")

    lines.append("## Input Files\n")
    for f in target_files:
        lines.append(f"- `{f}`")
    lines.append("")

    if key_symbols or read_next:
        lines.append("## Edit Plan\n")
        if key_symbols:
            lines.append("- Review Key Symbols before changing behavior or signatures.")
        if affected_files:
            lines.append("- Inspect Likely Affected Files flagged below.")
        if lsp_hint and lsp_hint.get("available"):
            lines.append(
                "- Local LSP is available; focused diagnostics and LSP evidence are enabled by default."
            )
        lines.append("")
        checklist: list[str] = []
        checklist.append("- [ ] Review Key Symbols call chains and affected files")
        if tests:
            checklist.append("- [ ] Run Suggested Tests after making changes")
        checklist.append("- [ ] Run `repomap verify` for final evidence")
        if checklist:
            lines.append("### Edit Checklist\n")
            for checklist_item in checklist:
                lines.append(checklist_item)
            lines.append("")

    if key_symbols:
        lines.append("## Key Symbols\n")
        lines.append("| Symbol | Kind | Location | Incoming | Outgoing |")
        lines.append("| --- | --- | --- | --- | --- |")
        for symbol_item in key_symbols[:12]:
            lines.append(
                f"| `{symbol_item['name']}` | {symbol_item['kind']} | `{symbol_item['file']}:{symbol_item['line']}` | {symbol_item['incomingCount']} | {symbol_item['outgoingCount']} |"
            )
        lines.append("")

    if read_next:
        lines.append("## Read Next\n")
        for read_item in read_next[:10]:
            lines.append(
                f"- `{read_item['file']}` ({read_item['role']}): {read_item['reason']}"
            )
        lines.append("")

    if affected_files:
        lines.append("## Likely Affected Files\n")
        if compact:
            total = len(affected_files)
            lines.append(
                f"**{total}** affected file(s) total. Top {min(top_n, total)}:\n"
            )
            for f, why, conf in affected_files[:top_n]:
                lines.append(f"- `{f}` ({conf}): {why}")
            lines.append("")
        else:
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
        # compact 模式：最多展示 top-3 测试，节省 token（issue #173）
        # 注意：调用方已根据 compact 截断 tests，这里直接遍历
        for t in tests:
            lines.append(f"- `{t.test_file}` ({t.confidence} confidence: {t.reason})")
        if total_tests is not None and total_tests > len(tests):
            lines.append(f"- … and {total_tests - len(tests)} more")
        lines.append("")

    risk_icon = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
    lines.append(f"## Risk Level: {risk_icon.get(risk_level, risk_level)}\n")
    if risk_notes:
        lines.append("## Risk Notes\n")
        for note in risk_notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.append("## Related Commands\n")
    if target_files:
        lines.append(
            f"- View target file details: `repomap query --file {target_files[0]} --project .`"
        )
    if affected_files and not compact:
        top_affected = affected_files[0][0]
        lines.append(
            f"- Inspect top affected file: `repomap query --file {top_affected} --project .`"
        )
    lines.append("- Verify changes: `repomap verify --project .`")
    lines.append("")

    return _truncate_output("\n".join(lines), max_chars)
