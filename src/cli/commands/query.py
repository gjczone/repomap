from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from ... import json_dumps
from ... import (
    RepoGraph,
)
from ...ai import (
    _build_query_reading_order,
    _rank_symbols_for_file,
    render_query_report,
)
from ...core import RepoMapEngine
from ..handlers import (
    CLI_NAME,
    EXIT_NO_RESULTS,
    _scan_engine,
    _normalize_path_prefix,
    _path_matches_prefix,
    _scan_stats_payload,
)
from ...topic import (
    FileMatch,
    TestMatch,
    classify_file_role,
    compute_keyword_weights,
    expand_keywords,
    find_related_tests,
    is_test_like_file,
    split_identifier,
    topic_score,
)


def run_query(
    project: str,
    max_files: int,
    query: str,
    max_result_files: int,
    max_result_symbols: int,
    no_tests: bool,
    as_json: bool,
    paths: str | None,
    exclude: str | None,
    context_lines: int = 2,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        analysis = engine.file_analysis()

        # 过滤搜索范围
        candidate_files = list(engine.graph.file_symbols.keys())
        allowed: set[str] = set()
        excluded: set[str] = set()
        if paths:
            allowed = {
                _normalize_path_prefix(engine.project_root, p)
                for p in paths.split(",")
                if p.strip()
            }
            candidate_files = [
                f
                for f in candidate_files
                if any(_path_matches_prefix(f, a) for a in allowed)
            ]
        if exclude:
            excluded = {
                _normalize_path_prefix(engine.project_root, e)
                for e in exclude.split(",")
                if e.strip()
            }
            candidate_files = [
                f
                for f in candidate_files
                if not any(_path_matches_prefix(f, e) for e in excluded)
            ]
        if no_tests:
            candidate_files = [f for f in candidate_files if not is_test_like_file(f)]

        # 计算高频词权重（命中文件过多的关键词降权）
        kw_weights = compute_keyword_weights(
            query.lower().split(), candidate_files, engine.graph
        )

        # 主题评分
        matches: list[FileMatch] = []
        for file_path in candidate_files:
            file_data = analysis.get(file_path, {})
            score = topic_score(
                query, file_path, file_data, engine.graph, keyword_weights=kw_weights
            )
            if score > 0:
                role = classify_file_role(file_path, engine.graph)
                reasons = _build_match_reasons(
                    query, file_path, engine.graph, engine.list_routes()
                )
                matches.append(
                    FileMatch(path=file_path, role=role, score=score, reasons=reasons)
                )

        # 调用邻居传播：高分文件的调用者/被调用者文件获得传播分数
        matches = _propagate_call_neighbor_scores(
            matches, candidate_files, engine.graph
        )

        matches.sort(key=lambda m: (-m.score, m.path))

        # Fallback: expand query keywords if too few direct matches
        is_fallback = False
        if len(matches) < 3:
            words = query.lower().split()
            expanded_keywords = expand_keywords(words)
            expanded_terms = [kw for kw, _ in expanded_keywords]
            if len(expanded_terms) > len(words):
                expanded_query = " ".join(expanded_terms)
                expanded_kw_weights = compute_keyword_weights(
                    expanded_query.lower().split(), candidate_files, engine.graph
                )
                expanded_matches: list[FileMatch] = []
                for file_path in candidate_files:
                    file_data = analysis.get(file_path, {})
                    score = topic_score(
                        expanded_query,
                        file_path,
                        file_data,
                        engine.graph,
                        keyword_weights=expanded_kw_weights,
                    )
                    if score > 0:
                        role = classify_file_role(file_path, engine.graph)
                        reasons = _build_match_reasons(
                            expanded_query,
                            file_path,
                            engine.graph,
                            engine.list_routes(),
                        )
                        expanded_matches.append(
                            FileMatch(
                                path=file_path, role=role, score=score, reasons=reasons
                            )
                        )
                expanded_matches = _propagate_call_neighbor_scores(
                    expanded_matches, candidate_files, engine.graph
                )
                expanded_matches.sort(key=lambda m: (-m.score, m.path))
                if len(expanded_matches) > len(matches):
                    matches = expanded_matches
                    is_fallback = True

            # If still too few results, fall back to hotspots
            if len(matches) < 3:
                hotspot_entries = engine.hotspots(20)
                hotspot_matches: list[FileMatch] = []
                for entry in hotspot_entries:
                    file_path = entry["file"]
                    # Respect path filters
                    if allowed and not any(
                        _path_matches_prefix(file_path, a) for a in allowed
                    ):
                        continue
                    if excluded and any(
                        _path_matches_prefix(file_path, e) for e in excluded
                    ):
                        continue
                    if no_tests and is_test_like_file(file_path):
                        continue
                    hotspot_matches.append(
                        FileMatch(
                            path=file_path,
                            role="hotspot",
                            score=float(entry.get("symbol_count", 0)),
                            reasons=["(fallback — no direct matches found)"],
                        )
                    )
                if hotspot_matches:
                    matches = hotspot_matches
                    is_fallback = True
            else:
                if is_fallback:
                    for m in matches:
                        m.reasons.append("(fallback — no direct matches found)")

        top_matches = matches[:max_result_files]

        # 找相关测试
        tests: list[TestMatch] = []
        if not no_tests:
            target_files = [
                m.path for m in top_matches if not is_test_like_file(m.path)
            ]
            tests = find_related_tests(
                target_files, engine.graph, analysis, str(engine.project_root)
            )

        if as_json:
            payload = {
                "command": "query",
                "project": str(engine.project_root),
                "query": query,
                "scanStats": _scan_stats_payload(engine),
                "result": {
                    "filesConsidered": len(candidate_files),
                    "matchedFiles": len(matches),
                    "readingOrder": _build_query_reading_order(
                        top_matches, analysis, max_result_files
                    ),
                    "coreFiles": [
                        {
                            "path": m.path,
                            "role": m.role,
                            "score": m.score,
                            "reasons": m.reasons,
                        }
                        for m in top_matches
                        if m.score >= 30 and not is_test_like_file(m.path)
                    ],
                    "supportingFiles": [
                        {
                            "path": m.path,
                            "role": m.role,
                            "score": m.score,
                            "reasons": m.reasons,
                        }
                        for m in top_matches
                        if m.score < 30
                    ],
                    "tests": [
                        {
                            "testFile": t.test_file,
                            "targetFile": t.target_file,
                            "confidence": t.confidence,
                            "reason": t.reason,
                        }
                        for t in tests
                    ],
                    "symbols": _query_symbols_json(
                        engine, top_matches, max_result_symbols
                    ),
                },
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        print(
            render_query_report(
                engine,
                query,
                top_matches,
                tests,
                max_result_files,
                max_result_symbols,
                context_lines=context_lines,
            )
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] query failed: {exc}", file=sys.stderr)
        return 1


def _propagate_call_neighbor_scores(
    matches: list[FileMatch],
    candidate_files: list[str],
    graph: RepoGraph,
    decay: float = 0.25,
    min_source_score: float = 20.0,
) -> list[FileMatch]:
    """Propagate scores from high-scoring files to their call-neighbor files.

    File-level one-hop propagation (direct callers/callees).
    Only propagates from files with score >= min_source_score.
    """
    match_by_path = {m.path: m for m in matches}
    candidate_set = set(candidate_files)

    # Build file-level call-neighbor maps from symbol-level call edges
    file_callees: dict[str, set[str]] = defaultdict(set)
    file_callers: dict[str, set[str]] = defaultdict(set)

    for sid, edges in graph.outgoing.items():
        sym = graph.symbols.get(sid)
        if not sym or not sym.file:
            continue
        for edge in edges:
            if edge.kind != "call":
                continue
            target_sym = graph.symbols.get(edge.target)
            if not target_sym or not target_sym.file:
                continue
            if sym.file != target_sym.file:
                file_callees[sym.file].add(target_sym.file)
                file_callers[target_sym.file].add(sym.file)

    # Get the first representative symbol name for a file
    def _first_sym_name(file_path: str) -> str | None:
        for sid in graph.file_symbols.get(file_path, []):
            sym = graph.symbols.get(sid)
            if sym and sym.name:
                return sym.name
        return None

    # Add a reason tag to a FileMatch if not already present
    def _add_tag(fm: FileMatch, tag: str) -> None:
        if tag not in fm.reasons:
            fm.reasons.append(tag)

    new_or_updated: dict[str, FileMatch] = {}
    for m in matches:
        if m.score < min_source_score:
            continue

        sym_name = _first_sym_name(m.path)
        tag = f"call-neighbor hit: {sym_name}" if sym_name else "call-neighbor hit"

        neighbors = file_callers.get(m.path, set()) | file_callees.get(m.path, set())
        for neighbor_file in neighbors:
            if neighbor_file not in candidate_set:
                continue

            propagated = m.score * decay

            if neighbor_file in match_by_path:
                _add_tag(match_by_path[neighbor_file], tag)
                match_by_path[neighbor_file].score += propagated
            elif neighbor_file in new_or_updated:
                new_or_updated[neighbor_file].score += propagated
            else:
                new_or_updated[neighbor_file] = FileMatch(
                    path=neighbor_file,
                    role=classify_file_role(neighbor_file, graph),
                    score=propagated,
                    reasons=[tag],
                )

    result = list(matches)
    for path, fm in new_or_updated.items():
        if path not in match_by_path:
            result.append(fm)
    return result


def _build_match_reasons(
    query: str, file_path: str, graph: RepoGraph, routes: list | None = None
) -> list[str]:
    """Build match reason list with hit type tags."""
    reasons: list[str] = []
    keywords = query.lower().split()
    expanded = expand_keywords(keywords)
    path_lower = file_path.lower()
    file_name = PurePosixPath(file_path).stem.lower()
    tokens = split_identifier(PurePosixPath(file_path).stem)

    for kw, source in expanded:
        tag = "synonym hit" if source else None
        if kw in path_lower:
            label = f"synonym hit: {source} -> {kw}" if source else f"path hit: {kw}"
            reasons.append(label)
        if kw in file_name:
            label = (
                f"synonym hit: {source} -> {kw} (filename)"
                if source
                else f"filename hit: {kw}"
            )
            reasons.append(label)
        elif any(kw in t for t in tokens):
            label = (
                f"synonym hit: {source} -> {kw} (token)"
                if source
                else f"filename token hit: {kw}"
            )
            reasons.append(label)

    # Symbol name hits
    for sid in graph.file_symbols.get(file_path, []):
        sym = graph.symbols.get(sid)
        if not sym:
            continue
        for kw, source in expanded:
            if kw in sym.name.lower():
                tag = (
                    f"synonym hit: {source} -> {kw}"
                    if source
                    else f"symbol hit: {sym.name}"
                )
                if tag not in reasons:
                    reasons.append(tag)
        if len(reasons) >= 5:
            break

    # Route hits
    if routes:
        for r in routes:
            rel_file = file_path
            if hasattr(r, "file") and (
                r.file == rel_file
                or rel_file.endswith(r.file)
                or r.file.endswith(rel_file)
            ):
                for kw, source in expanded:
                    if kw in r.path.lower() or kw in r.handler.lower():
                        tag = f"route hit: {r.method} {r.path}"
                        if tag not in reasons:
                            reasons.append(tag)
                if len(reasons) >= 6:
                    break

    # Test file marker
    if is_test_like_file(file_path):
        reasons.append("test hit")

    return reasons[:6]


def _query_symbols_json(
    engine: RepoMapEngine,
    matches: list[FileMatch],
    max_symbols: int,
) -> list[dict[str, Any]]:
    """为 JSON 输出提取符号列表。"""
    result: list[dict[str, Any]] = []
    for m in matches:
        if len(result) >= max_symbols:
            break
        for sym in _rank_symbols_for_file(engine, m.path):
            if len(result) >= max_symbols:
                break
            entry: dict[str, Any] = {
                "name": sym["name"],
                "kind": sym["kind"],
                "file": m.path,
                "line": sym["line"],
                "role": classify_file_role(m.path, engine.graph),
            }
            sym_end = sym.get("end_line", sym["line"])
            if sym_end > 0:
                entry["endLine"] = sym_end
            if (sym_end - sym["line"]) > 100:
                entry["chunkRange"] = f"L{sym['line']}-L{sym_end}"
            result.append(entry)
    return result


def run_search(project: str, max_files: int, query: str, top_k: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        results = engine.search_symbols(query, top_k)
        if not results:
            # Fallback: use hotspots when no symbol matches found
            hotspot_entries = engine.hotspots(10)
            if hotspot_entries:
                lines = ["(fallback — no direct matches found)\n"]
                lines.append(f"## Hotspot files (no symbol matches for `{query}`)\n")
                for entry in hotspot_entries:
                    lines.append(
                        f"- `{entry['file']}` — {entry['symbol_count']} symbols, "
                        f"semantic density: {entry['semantic_symbol_count']}"
                    )
                print("\n".join(lines))
                return 0
            print(f"> No symbols found for query: `{query}`")
            return EXIT_NO_RESULTS

        from ...search import _HAS_BM25, _symbol_is_large

        backend = "BM25" if _HAS_BM25 else "keyword"
        lines = [f"Found {len(results)} symbols (backend: {backend})\n"]
        lines.append(f"## Search results for `{query}`\n")
        for sym, score in results:
            pr = sym.pagerank * 1000
            loc = f"`{sym.file}:{sym.line}`"
            if _symbol_is_large(sym):
                loc += f" (L{sym.line}-L{max(sym.end_line, sym.line)})"
            lines.append(
                f"- **{sym.name}** ({sym.kind}) {loc} score={score:.2f} PR={pr:.1f}"
            )
            if sym.return_type:
                lines.append(f"  - returns: `{sym.return_type}`")
            if sym.signature:
                lines.append(f"  - sig: `{sym.signature}`")
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] search failed: {exc}", file=sys.stderr)
        return 1
