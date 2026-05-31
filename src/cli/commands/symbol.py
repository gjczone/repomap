from __future__ import annotations

import sys
import logging
from typing import Any

from ... import (
    DEFAULT_CALL_CHAIN_MAX_CHARS,
    DEFAULT_FILE_DETAIL_MAX_SYMBOLS,
    DEFAULT_QUERY_SYMBOL_MAX_CHARS,
)
from ...core import DEFAULT_MAX_FILES
from ...ai import _truncate_output
from ...core import RepoMapEngine
from ...hints import (
    call_chain_hint,
    file_detail_hint,
    query_symbol_hint,
    refs_hint,
    state_map_hint,
)
from ..handlers import (
    CLI_NAME,
    DEFAULT_LSP_TIMEOUT,
    EXIT_NO_RESULTS,
    json_envelope,
    _scan_engine,
    _normalize_project_relative_path,
    _collect_lsp_evidence_for_symbol,
    _format_lsp_evidence,
    _format_symbol_ref,
    _select_symbol_match,
)
from ...state_map import find_state_definitions


def _collect_references_for_symbol(engine: RepoMapEngine, symbol: Any) -> list[dict]:
    """收集符号的引用信息，用于 call-chain 报告。"""
    references = []
    # 查找所有引用该符号的边
    for source_id, edge_list in engine.graph.outgoing.items():
        for edge in edge_list:
            if edge.target == symbol.id:
                source_symbol = engine.graph.symbols.get(source_id)
                if source_symbol:
                    references.append(
                        {
                            "file": source_symbol.file,
                            "line": source_symbol.line,
                            "type": edge.kind,
                            "name": source_symbol.name,
                        }
                    )
    # 按文件和行号排序
    references.sort(key=lambda x: (x["file"], x["line"]))
    return references[:50]  # 限制返回数量


def _collect_state_map_for_symbol(
    engine: RepoMapEngine, symbol_name: str
) -> list[dict] | None:
    """收集符号的状态映射信息，用于 query-symbol 报告。"""
    try:
        defs = find_state_definitions(engine, symbol=symbol_name)
        if not defs:
            return None
        return [
            {
                "symbol_name": d.symbol_name,
                "file": d.file,
                "line": d.line,
                "kind": d.kind,
                "values": [
                    {"name": v.name, "file": v.file, "line": v.line} for v in d.values
                ],
                "writers": [
                    {"name": w.name, "file": w.file, "line": w.line} for w in d.writers
                ],
                "readers": [
                    {"name": r.name, "file": r.file, "line": r.line} for r in d.readers
                ],
            }
            for d in defs
        ]
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.warning("Failed to collect symbol definitions: %s", exc, exc_info=True)
        return None


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
    project: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    symbol: str = "",
    file_path: str | None = None,
    direction: str = "both",
    depth: int = 3,
    max_chars: int = DEFAULT_CALL_CHAIN_MAX_CHARS,
    as_json: bool = True,
    include_source: bool = False,
    max_source_lines: int = 80,
    trace_pattern: str | None = None,
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
            # 收集引用信息
            references = _collect_references_for_symbol(engine, selected)

            # 路径追踪：从 source symbol 到 target symbol
            trace_result: dict[str, Any] | None = None
            if trace_pattern:
                target_matches = engine.query_symbol(trace_pattern)
                if target_matches:
                    target_id = target_matches[0].id
                    trace_result = engine.trace_path(
                        selected.id,
                        target_id,
                        max_depth=depth,
                        allowed_kinds={"call", "method_call", "import_call"},
                    )
                else:
                    trace_result = {
                        "path_found": False,
                        "path": [],
                        "hop_count": 0,
                        "error": f"target symbol not found: {trace_pattern}",
                    }

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
                "references": references,
            }
            if trace_result is not None:
                payload["trace"] = trace_result
            if include_source and max_source_lines > 0:
                from ...ai import _read_symbol_source

                project_root_str = str(engine.project_root)
                # Add source for the primary symbol
                sym_dict: dict[str, Any] = payload["symbol"]  # type: ignore[assignment]
                sym_dict["source"] = _read_symbol_source(
                    project_root_str,
                    selected.file,
                    selected.line,
                    selected.end_line,
                    max_source_lines,
                )
                # Add source for callers
                caller_dicts: list[dict[str, Any]] = payload["callers"]  # type: ignore[assignment]
                for i, caller_item in enumerate(chain["callers"]):
                    if i < len(caller_dicts):
                        caller_dicts[i]["source"] = _read_symbol_source(
                            project_root_str,
                            caller_item.file,
                            caller_item.line,
                            caller_item.end_line,
                            max_source_lines,
                        )
                # Add source for callees
                callee_dicts: list[dict[str, Any]] = payload["callees"]  # type: ignore[assignment]
                for i, callee_item in enumerate(chain["callees"]):
                    if i < len(callee_dicts):
                        callee_dicts[i]["source"] = _read_symbol_source(
                            project_root_str,
                            callee_item.file,
                            callee_item.line,
                            callee_item.end_line,
                            max_source_lines,
                        )
            print(json_envelope("call-chain", str(engine.project_root), payload))
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
            chain = engine.call_chain(selected.id, "both", 1)
            for hint in call_chain_hint(
                caller_count=len(chain.get("callers", [])),
                callee_count=len(chain.get("callees", [])),
            ):
                print(hint, file=sys.stderr)
            return 0
        print(
            _truncate_output(
                _render_selected_call_chain(engine, selected, depth), max_chars
            )
        )
        chain = engine.call_chain(selected.id, "both", 1)
        for hint in call_chain_hint(
            caller_count=len(chain.get("callers", [])),
            callee_count=len(chain.get("callees", [])),
        ):
            print(hint, file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] call-chain failed: {exc}", file=sys.stderr)
        return 1


def run_query_symbol(
    project: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    symbol: str = "",
    file_path: str | None = None,
    max_chars: int = DEFAULT_QUERY_SYMBOL_MAX_CHARS,
    lsp_timeout: float = DEFAULT_LSP_TIMEOUT,
    as_json: bool = True,
    include_source: bool = False,
    max_source_lines: int = 80,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        results = engine.query_symbol(symbol)
        if file_path:
            results = [item for item in results if item.file == file_path]
        if not results:
            if as_json:
                print(
                    json_envelope(
                        "query-symbol",
                        str(engine.project_root),
                        {"matches": [], "query": symbol, "file_filter": file_path},
                        status="no_results",
                    )
                )
                return EXIT_NO_RESULTS
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
                if include_source and max_source_lines > 0:
                    from ...ai import _read_symbol_source

                    d["source"] = _read_symbol_source(
                        str(engine.project_root),
                        item.file,
                        item.line,
                        item.end_line,
                        max_source_lines,
                    )
                return d

            payload = {
                "query": symbol,
                "total_results": len(results),
                "exact_matches": [_symbol_item(item) for item in exact_matches[:20]],
                "fuzzy_matches": [_symbol_item(item) for item in fuzzy_matches[:20]],
            }
            if file_path:
                payload["file_filter"] = file_path
            if exact_matches or results:
                selected = (exact_matches or results)[0]
                payload["lsp"] = _collect_lsp_evidence_for_symbol(
                    engine, selected, lsp_timeout
                )
                # 自动收集状态映射信息（枚举/常量时）
                state_map = _collect_state_map_for_symbol(engine, symbol)
                if state_map:
                    payload["stateMap"] = state_map
            print(json_envelope("query-symbol", str(engine.project_root), payload))
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
        selected = (exact_matches or results)[0]
        lines.extend(
            _format_lsp_evidence(
                _collect_lsp_evidence_for_symbol(engine, selected, lsp_timeout)
            )
        )
        print(_truncate_output("\n".join(lines), max_chars))
        for hint in query_symbol_hint(
            match_count=len(results), has_file_filter=file_path is not None
        ):
            print(hint, file=sys.stderr)
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
    lsp_timeout: float = DEFAULT_LSP_TIMEOUT,
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
                    symbols,
                    key=lambda x: float(x.get("pagerank", 0)),  # type: ignore[arg-type]
                    reverse=True,
                )[:max_symbols],
                "imports": engine.graph.file_imports.get(normalized_file_path, []),
                "calls": [
                    list(c)
                    for c in engine.graph.file_calls.get(normalized_file_path, [])
                ],
            }
            from dataclasses import asdict as dc_asdict
            from ...lsp import collect_lsp_symbol_tree

            lsp_tree = collect_lsp_symbol_tree(
                engine.project_root, normalized_file_path, timeout=lsp_timeout
            )
            if lsp_tree:
                payload["lsp_symbol_tree"] = [dc_asdict(item) for item in lsp_tree]
            else:
                payload["lsp_symbol_tree"] = []
            print(json_envelope("file-detail", str(engine.project_root), payload))
            return 0

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
        file_sids = set(engine.graph.file_symbols.get(normalized_file_path, set()))
        has_symbols = len(file_sids) > 0
        has_callers = any(
            len(engine.graph.incoming.get(sid, set())) > 0 for sid in file_sids
        )
        for hint in file_detail_hint(has_symbols=has_symbols, has_callers=has_callers):
            print(hint, file=sys.stderr)
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
    lsp_timeout: float = DEFAULT_LSP_TIMEOUT,
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
            payload["lsp"] = _collect_lsp_evidence_for_symbol(
                engine, target, lsp_timeout
            )
            if as_json:
                print(json_envelope("refs", str(engine.project_root), payload))
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
                lines.extend(_format_lsp_evidence(payload["lsp"]))
                print("\n".join(lines))
                for hint in refs_hint(called_by_count=payload["ref_count"]):
                    print(hint, file=sys.stderr)
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
            print(json_envelope("refs", str(engine.project_root), payload))
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
            from ..handlers import json_envelope

            payload = {
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
            print(json_envelope("state-map", str(engine.project_root), payload))
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
            has_writers = any(len(d.writers) > 0 for d in defs)
            for hint in state_map_hint(has_writers=has_writers):
                print(hint, file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] state-map failed: {exc}", file=sys.stderr)
        return 1
