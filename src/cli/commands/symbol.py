from __future__ import annotations

import sys
from typing import Any

from ... import json_dumps
from ... import (
    DEFAULT_FILE_DETAIL_MAX_SYMBOLS,
)
from ...ai import _truncate_output
from ...core import RepoMapEngine
from ..handlers import (
    CLI_NAME,
    EXIT_NO_RESULTS,
    _scan_engine,
    _normalize_project_relative_path,
    _collect_lsp_evidence_for_symbol,
    _format_lsp_evidence,
    _format_symbol_ref,
    _select_symbol_match,
)
from ...state_map import find_state_definitions


def _group_symbol_matches(
    results: list[Any], symbol: str
) -> tuple[list[Any], list[Any]]:
    exact = [item for item in results if item.name == symbol]
    fuzzy = [item for item in results if item.name != symbol]
    return exact, fuzzy


def _render_selected_call_chain(engine: RepoMapEngine, symbol: Any, depth: int) -> str:
    chain = engine.call_chain(symbol.id, "both", depth)
    lines = [
        f"## Call Chain — `{symbol.name}`\n",
        f"- **Type**: {symbol.kind}",
        f"- **Location**: `{symbol.file}:{symbol.line}`",
        f"- **Importance**: PR={symbol.pagerank * 1000:.1f}",
    ]
    if symbol.signature:
        lines.append(f"- **Signature**: `{symbol.signature}`")
    lines.append("")

    callers = chain["callers"]
    lines.append(f"### Called by（{len(callers)}）\n")
    if callers:
        for caller in callers[:20]:
            lines.append(
                f"- `{caller.name}` ({caller.kind}) — `{caller.file}:{caller.line}`"
            )
        if len(callers) > 20:
            lines.append(f"- ... {len(callers) - 20} more")
    else:
        lines.append("- (None — entry point)")

    callees = chain["callees"]
    lines.append(f"\n### Calls（{len(callees)}）\n")
    if callees:
        for callee in callees[:20]:
            lines.append(
                f"- `{callee.name}` ({callee.kind}) — `{callee.file}:{callee.line}`"
            )
        if len(callees) > 20:
            lines.append(f"- ... {len(callees) - 20} more")
    else:
        lines.append("- (None — leaf function)")

    return "\n".join(lines)


_CALL_CHAIN_MAX_DEPTH = 10


def run_call_chain(
    project: str,
    max_files: int,
    symbol: str,
    file_path: str | None,
    direction: str,
    depth: int,
    max_chars: int,
    as_json: bool,
) -> int:
    if depth > _CALL_CHAIN_MAX_DEPTH:
        print(
            f"[{CLI_NAME}] --depth {depth} exceeds max {_CALL_CHAIN_MAX_DEPTH}, clamping to {_CALL_CHAIN_MAX_DEPTH}",
            file=sys.stderr,
        )
        depth = _CALL_CHAIN_MAX_DEPTH
    try:
        engine = _scan_engine(project, max_files)
        selected, error, tier = _select_symbol_match(
            engine, symbol, file_path=file_path
        )
        if error:
            print(error, file=sys.stderr)
            return 1
        if selected is None:
            print(f"[{CLI_NAME}] symbol not found: {symbol}", file=sys.stderr)
            return 1
        if as_json:
            chain = engine.call_chain(selected.id, direction, depth)
            payload = {
                "symbol": {
                    "id": selected.id,
                    "name": selected.name,
                    "kind": selected.kind,
                    "file": selected.file,
                    "line": selected.line,
                    "signature": selected.signature,
                    "pagerank": selected.pagerank,
                },
                "direction": direction,
                "depth": depth,
                "callers": [
                    _format_symbol_ref(engine, item.id) for item in chain["callers"]
                ],
                "callees": [
                    _format_symbol_ref(engine, item.id) for item in chain["callees"]
                ],
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if direction != "both":
            data = engine.call_chain(selected.id, direction, depth)
            lines = [f"## Call Chain — `{selected.name}`\n"]
            items = data[direction][:50]
            for item in items:
                lines.append(f"- `{item.name}` ({item.file}:{item.line})")
            if len(data[direction]) > 50:
                lines.append(f"\n... and {len(data[direction]) - 50} more")
            print(_truncate_output("\n".join(lines), max_chars))
            return 0
        print(
            _truncate_output(
                _render_selected_call_chain(engine, selected, depth), max_chars
            )
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] call-chain failed: {exc}", file=sys.stderr)
        return 1


def run_query_symbol(
    project: str,
    max_files: int,
    symbol: str,
    file_path: str | None,
    max_chars: int,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
    as_json: bool = False,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        results = engine.query_symbol(symbol)
        if file_path:
            results = [item for item in results if item.file == file_path]
        if not results:
            if as_json:
                print(
                    json_dumps(
                        {"matches": [], "query": symbol, "file_filter": file_path},
                        ensure_ascii=False,
                    )
                )
                return 0
            print(f"> No matches found for `{symbol}`", file=sys.stderr)
            return EXIT_NO_RESULTS
        exact_matches, fuzzy_matches = _group_symbol_matches(results, symbol)

        if as_json:

            def _symbol_item(item):
                d = {
                    "name": item.name,
                    "kind": item.kind,
                    "file": item.file,
                    "line": item.line,
                    "pagerank": item.pagerank,
                }
                if item.signature:
                    d["signature"] = item.signature
                if item.return_type:
                    d["return_type"] = item.return_type
                if item.params:
                    d["params"] = item.params
                return d

            payload = {
                "query": symbol,
                "total_results": len(results),
                "exact_matches": [_symbol_item(item) for item in exact_matches[:20]],
                "fuzzy_matches": [_symbol_item(item) for item in fuzzy_matches[:20]],
            }
            if file_path:
                payload["file_filter"] = file_path
            if with_lsp and (exact_matches or results):
                selected = (exact_matches or results)[0]
                payload["lsp"] = _collect_lsp_evidence_for_symbol(
                    engine, selected, lsp_timeout
                )
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        lines = [f"Found {len(results)} matching results.\n"]
        if file_path:
            lines.append(f"Filtered by file: `{file_path}`\n")
        if len(exact_matches) > 1 and not file_path:
            lines.append(
                f"{len(exact_matches)} exact candidates; use `--file-path` to narrow.\n"
            )

        if exact_matches:
            lines.append(f"## Exact matches `{symbol}` ({len(exact_matches)})\n")
            for item in exact_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(
                    f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}"
                )
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")
                if item.return_type:
                    lines.append(f"  - returns: `{item.return_type}`")
                if item.params:
                    lines.append(f"  - params: `{item.params}`")

        if fuzzy_matches:
            lines.append(f"\n## Fuzzy matches ({len(fuzzy_matches)})\n")
            for item in fuzzy_matches[:10]:
                pr = item.pagerank * 1000
                lines.append(
                    f"- **{item.name}** ({item.kind}) `{item.file}:{item.line}` PR={pr:.1f}"
                )
                if item.signature:
                    lines.append(f"  - sig: `{item.signature}`")
                if item.return_type:
                    lines.append(f"  - returns: `{item.return_type}`")
                if item.params:
                    lines.append(f"  - params: `{item.params}`")

        if len(results) > 10 and (len(exact_matches) > 10 or len(fuzzy_matches) > 10):
            lines.append("\n> Many results; use `--file-path` to narrow.")
        if with_lsp:
            selected = (exact_matches or results)[0]
            lines.extend(
                _format_lsp_evidence(
                    _collect_lsp_evidence_for_symbol(engine, selected, lsp_timeout)
                )
            )
        print(_truncate_output("\n".join(lines), max_chars))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] query-symbol failed: {exc}", file=sys.stderr)
        return 1


def run_file_detail(
    project: str,
    max_files: int,
    file_path: str,
    max_symbols: int,
    max_chars: int,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
    as_json: bool = False,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        normalized_file_path = _normalize_project_relative_path(
            engine.project_root, file_path, must_exist=True
        )

        if max_symbols == DEFAULT_FILE_DETAIL_MAX_SYMBOLS:
            file_symbol_count = len(
                engine.graph.file_symbols.get(normalized_file_path, [])
            )
            if file_symbol_count > 50:
                max_symbols = min(file_symbol_count, 50)
            elif file_symbol_count > 20:
                max_symbols = file_symbol_count

        if as_json:
            symbols = []
            for sid in engine.graph.file_symbols.get(normalized_file_path, []):
                sym = engine.graph.symbols.get(sid)
                if not sym:
                    continue
                s = {
                    "name": sym.name,
                    "kind": sym.kind,
                    "line": sym.line,
                    "pagerank": sym.pagerank,
                }
                if sym.signature:
                    s["signature"] = sym.signature
                if sym.return_type:
                    s["return_type"] = sym.return_type
                if sym.params:
                    s["params"] = sym.params
                symbols.append(s)
            payload = {
                "file": normalized_file_path,
                "symbol_count": len(symbols),
                "symbols": sorted(
                    symbols, key=lambda x: x.get("pagerank", 0), reverse=True
                )[:max_symbols],
                "imports": engine.graph.file_imports.get(normalized_file_path, []),
                "calls": [
                    list(c)
                    for c in engine.graph.file_calls.get(normalized_file_path, [])
                ],
            }
            if with_lsp:
                from dataclasses import asdict as dc_asdict
                from ...lsp import collect_lsp_symbol_tree

                lsp_tree = collect_lsp_symbol_tree(
                    engine.project_root, normalized_file_path, timeout=lsp_timeout
                )
                if lsp_tree:
                    payload["lsp_symbol_tree"] = [dc_asdict(item) for item in lsp_tree]
                else:
                    payload["lsp_symbol_tree"] = []
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        lsp_tree = None
        if with_lsp:
            from ...lsp import collect_lsp_symbol_tree

            lsp_tree = collect_lsp_symbol_tree(
                engine.project_root, normalized_file_path, timeout=lsp_timeout
            )

        print(
            engine.render_file_detail(
                normalized_file_path,
                max_symbols=max_symbols,
                max_chars=max_chars,
                lsp_symbol_tree=lsp_tree,
            )
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] file-detail failed: {exc}", file=sys.stderr)
        return 1


def run_refs(
    project: str,
    max_files: int,
    symbol: str | None,
    file_path: str | None,
    as_json: bool,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
) -> int:
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
            selected, error, tier = _select_symbol_match(
                engine, symbol, file_path=file_path
            )
            if error:
                print(error, file=sys.stderr)
                return 1
            if selected is None:
                print(f"[{CLI_NAME}] symbol not found: {symbol}", file=sys.stderr)
                return 1
            sid = selected.id
            target = engine.graph.symbols[sid]
            payload = {
                "symbol": target.name,
                "id": sid,
                "called_by": [
                    _format_symbol_ref(engine, item)
                    for item in sorted(calls_in[sid])[:20]
                ],
                "calls": [
                    _format_symbol_ref(engine, item)
                    for item in sorted(calls_out[sid])[:20]
                ],
                "ref_count": len(calls_in[sid]),
                "is_entry": len(calls_in[sid]) == 0,
                "is_leaf": len(calls_out[sid]) == 0,
            }
            if with_lsp:
                payload["lsp"] = _collect_lsp_evidence_for_symbol(
                    engine, target, lsp_timeout
                )
            if as_json:
                print(json_dumps(payload, ensure_ascii=False, indent=2))
            else:
                lines = [f"## Reference Analysis — `{target.name}`\n"]
                lines.append(f"- Referenced by:  {payload['ref_count']}")
                lines.append(f"- Calls: {len(payload['calls'])}")
                lines.append(
                    f"- Entry point:  {'Yes' if payload['is_entry'] else 'No'}"
                )
                lines.append(
                    f"- Leaf function:  {'Yes' if payload['is_leaf'] else 'No'}\n"
                )
                if payload["called_by"]:
                    lines.append("**Called by** (Top 10):")
                    for row in payload["called_by"][:10]:
                        lines.append(
                            f"  - `{row['name']}` ({row['file']}:{row['line']})"
                        )
                if payload["calls"]:
                    lines.append("\n**Calls** (Top 10):")
                    for row in payload["calls"][:10]:
                        lines.append(
                            f"  - `{row['name']}` ({row['file']}:{row['line']})"
                        )
                if with_lsp:
                    lines.extend(_format_lsp_evidence(payload["lsp"]))
                print("\n".join(lines))
            return 0

        entries = [sid for sid in symbol_ids if len(calls_in[sid]) == 0]
        orphans = [
            sid
            for sid in symbol_ids
            if len(calls_in[sid]) == 0 and len(calls_out[sid]) == 0
        ]
        ref_counts = sorted(
            ((sid, len(calls_in[sid])) for sid in symbol_ids),
            key=lambda item: item[1],
            reverse=True,
        )
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
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0
        lines = ["## Global Reference Analysis\n"]
        lines.append(f"- Total symbols:  {payload['total_symbols']}")
        lines.append(f"- Entry point:  {len(payload['entry_points'])}")
        lines.append(f"- Orphaned symbols:  {len(payload['orphaned_symbols'])}\n")
        lines.append("**Most referenced** (Top 10):")
        for row in payload["most_referenced"][:10]:
            lines.append(
                f"  - `{row['name']}`: {row['ref_count']}  references ({row['file']})"
            )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] refs failed: {exc}", file=sys.stderr)
        return 1


# Kinds that are always structural noise, never dead code.
def run_state_map(
    project: str, max_files: int, symbol: str | None, query: str | None, as_json: bool
) -> int:

    if not symbol and not query:
        print(f"[{CLI_NAME}] state-map requires --symbol or --query", file=sys.stderr)
        return 2

    try:
        engine = _scan_engine(project, max_files)
        defs = find_state_definitions(engine, query=query, symbol=symbol)

        if as_json:
            payload = {
                "command": "state-map",
                "project": str(engine.project_root),
                "query": query,
                "symbol": symbol,
                "definitions": [
                    {
                        "symbol_name": d.symbol_name,
                        "file": d.file,
                        "line": d.line,
                        "kind": d.kind,
                        "values": [
                            {"name": v.name, "file": v.file, "line": v.line}
                            for v in d.values
                        ],
                        "writers": [
                            {"name": w.name, "file": w.file, "line": w.line}
                            for w in d.writers
                        ],
                        "readers": [
                            {"name": r.name, "file": r.file, "line": r.line}
                            for r in d.readers
                        ],
                    }
                    for d in defs
                ],
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        # Text output
        lines: list[str] = []
        for d in defs:
            lines.append(f"## State Map — {d.symbol_name}\n")
            lines.append(f"- **File**: `{d.file}:{d.line}`  ({d.kind})\n")
            if d.values:
                lines.append("### Values\n")
                for v in d.values:
                    lines.append(f"- `{v.name}` — `{v.file}:{v.line}`")
                lines.append("")
            if d.writers:
                lines.append("### Writers\n")
                for w in d.writers[:10]:
                    lines.append(f"- `{w.name}` — `{w.file}:{w.line}`")
                lines.append("")
            if d.readers:
                lines.append("### Readers / Branches\n")
                for r in d.readers[:10]:
                    lines.append(f"- `{r.file}:{r.line}` — {r.name}")
                lines.append("")
            lines.append(
                "**Risk hint**: Adding or removing a state value requires checking all writers, readers, and tests.\n"
            )

        if not defs:
            print(
                f"> No state definitions found for symbol={symbol or 'N/A'} query={query or 'N/A'}."
            )
        else:
            print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] state-map failed: {exc}", file=sys.stderr)
        return 1
