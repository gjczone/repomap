from __future__ import annotations

from typing import Any


def render_affected_report(
    target_files: list[str],
    affected_tests: list[tuple[str, str]],
    dependency_chains: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# Affected Tests\n")

    lines.append("## Changed Files\n")
    for f in target_files:
        lines.append(f"- `{f}`")
    lines.append("")

    lines.append("## Affected Tests\n")
    if affected_tests:
        for test_file, reason in affected_tests:
            lines.append(f"- `{test_file}`  ({reason})")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Dependency Chain\n")
    if dependency_chains:
        for chain in dependency_chains:
            src = chain.get("changed", "?")
            chain_tests = chain.get("affected_tests", [])
            if chain_tests:
                for ct in chain_tests:
                    test_name = ct.get("test", "?")
                    via = " → ".join(ct.get("via", [src, test_name]))
                    lines.append(f"`{via}`")
            else:
                lines.append(f"`{src}` → (no affected tests)")
    else:
        lines.append("- (none)")
    lines.append("")

    return "\n".join(lines)
