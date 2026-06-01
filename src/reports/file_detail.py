from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core import RepoMapEngine

from . import _truncate_output


def _append_lsp_symbol_outline(
    lines: list[str], nodes: list[Any], indent: int, parent_path: str = ""
) -> None:
    prefix = "  " * indent
    for node in nodes:
        name = node.name if hasattr(node, "name") else node.get("name", "?")
        qualified = f"{parent_path}/{name}" if parent_path else name
        kind = (
            node.kind_name if hasattr(node, "kind_name") else node.get("kind_name", "")
        )
        line_num = node.line if hasattr(node, "line") else node.get("line", 0)
        end_line = (
            node.end_line if hasattr(node, "end_line") else node.get("end_line", 0)
        )
        sig = node.detail if hasattr(node, "detail") else node.get("detail", "")
        sig_str = f" — {sig}" if sig else ""
        lines.append(
            f"{prefix}- `{qualified}` ({kind}{sig_str}) L{line_num}-L{end_line}"
        )
        children = (
            node.children if hasattr(node, "children") else node.get("children", [])
        )
        if children:
            _append_lsp_symbol_outline(
                lines, children, indent + 1, parent_path=qualified
            )


def render_file_detail_report(
    engine: "RepoMapEngine",
    file_path: str,
    max_symbols: int = 12,
    max_chars: int = 6000,
    lsp_symbol_tree: list[Any] | None = None,
) -> str:
    original_file_path = file_path
    symbol_ids = engine.graph.file_symbols.get(file_path, [])
    if not symbol_ids:
        matches = [path for path in engine.graph.file_symbols if file_path in path]
        if matches:
            matched = matches[0]
            file_path = matched
            symbol_ids = engine.graph.file_symbols[file_path]
            redirect_note = (
                f"> Note: `{original_file_path}` not found directly; "
                f"showing closest match `{matched}`.\n"
            )
        else:
            return f"> File `{file_path}` not found or has no symbols"
    else:
        redirect_note = ""

    analysis = engine.file_analysis().get(file_path, {})
    symbols = sorted(
        [
            engine.graph.symbols[symbol_id]
            for symbol_id in symbol_ids
            if symbol_id in engine.graph.symbols
        ],
        key=lambda symbol: symbol.line,
    )
    visible_symbols = symbols if max_symbols <= 0 else symbols[:max_symbols]

    lines = [
        f"## File Detail — `{file_path}`\n",
        f"{len(symbols)}  symbols",
    ]
    if redirect_note:
        lines.append(redirect_note)
    if analysis:
        lines.append(
            f"Cross-file references: {analysis.get('neighbor_count', 0)}, "
            f"exported symbols: {analysis.get('exported_count', 0)}\n"
        )
    else:
        lines.append("")

    if max_symbols > 0 and len(symbols) > len(visible_symbols):
        lines.append(
            f"Showing first {len(visible_symbols)} of {len(symbols)} symbols; use `--max-symbols` to see more.\n"
        )

    for symbol in visible_symbols:
        pagerank = symbol.pagerank * 1000
        lines.append(
            f"- `{symbol.name}` ({symbol.kind}) — L{symbol.line} PR={pagerank:.1f}"
        )
        if symbol.signature:
            lines.append(f"  - sig: `{symbol.signature}`")
        if symbol.return_type:
            lines.append(f"  - returns: `{symbol.return_type}`")
        if symbol.params:
            lines.append(f"  - params: `{symbol.params}`")
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

    if lsp_symbol_tree:
        lines.append("## LSP Symbol Tree\n")
        lines.append(
            "> From language server; nested structure reflects lexical scoping.\n"
        )
        _append_lsp_symbol_outline(lines, lsp_symbol_tree, indent=0)

    return _truncate_output("\n".join(lines), max_chars)
