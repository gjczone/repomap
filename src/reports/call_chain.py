from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core import RepoMapEngine


def render_call_chain_report(
    engine: "RepoMapEngine", symbol_name: str, max_depth: int = 3
) -> str:
    matches = engine.query_symbol(symbol_name)
    if not matches:
        return f"> Symbol `{symbol_name}` not found"

    symbol = matches[0]
    chain = engine.call_chain(symbol.id, "both", max_depth)
    lines = [
        f"## Call Chain — `{symbol.name}`\n",
        f"- **Type**: {symbol.kind}",
        f"- **Location**: `{symbol.file}:{symbol.line}`",
        f"- **Importance**: PR={symbol.pagerank * 1000:.1f}",
        f"- **Signature**: `{symbol.signature}`" if symbol.signature else "",
        "",
    ]

    callers = chain["callers"]
    impl_callers = [c for c in callers if not (c.file or "").startswith("tests/")]
    test_callers = [c for c in callers if (c.file or "").startswith("tests/")]
    total = len(callers)

    if callers:
        summary_parts = [f"({total} total"]
        if impl_callers:
            summary_parts.append(f"{len(impl_callers)} impl")
        if test_callers:
            summary_parts.append(f"{len(test_callers)} test")
        lines.append(f"### Called by {', '.join(summary_parts)})\n")

        if impl_callers:
            lines.append("#### Implementation Callers\n")
            max_impl = min(len(impl_callers), 50)
            for caller in impl_callers[:max_impl]:
                conf = engine.confidence_for(symbol.id, caller.id, "caller")
                heuristic = " (heuristic)" if conf < 1.0 else ""
                lines.append(
                    f"- `{caller.name}` ({caller.kind}) — `{caller.file}:{caller.line}`{heuristic}"
                )
            if len(impl_callers) > max_impl:
                lines.append(f"- ... {len(impl_callers) - max_impl} more impl callers")
            lines.append("")

        if test_callers:
            lines.append("#### Test Callers\n")
            for caller in test_callers[:3]:
                conf = engine.confidence_for(symbol.id, caller.id, "caller")
                heuristic = " (heuristic)" if conf < 1.0 else ""
                lines.append(
                    f"- `{caller.name}` ({caller.kind}) — `{caller.file}:{caller.line}`{heuristic}"
                )
            if len(test_callers) > 3:
                lines.append(f"- ... {len(test_callers) - 3} more test callers")
            lines.append("")
    else:
        lines.append(f"### Called by ({total})\n")
        lines.append("- (None — entry point)")

    callees = chain["callees"]
    lines.append(f"\n### Calls ({len(callees)})\n")
    if callees:
        for callee in callees[:20]:
            conf = engine.confidence_for(symbol.id, callee.id, "callee")
            heuristic = " (heuristic)" if conf < 1.0 else ""
            lines.append(
                f"- `{callee.name}` ({callee.kind}) — `{callee.file}:{callee.line}`{heuristic}"
            )
        if len(callees) > 20:
            lines.append(f"- ... {len(callees) - 20} more")
    else:
        lines.append("- (None — leaf function)")

    return "\n".join(line for line in lines if line is not None)
