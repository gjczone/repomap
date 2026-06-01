from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ... import RepoGraph
from ...ai import render_impact_report
from ...core import RepoMapEngine
from ..handlers import (
    CLI_NAME,
    _scan_engine,
    _normalize_project_relative_paths,
    _scan_stats_payload,
    _sym_name,
    _assess_risk,
    save_impact_session,
)
from ...hints import impact_hint
from ...topic import (
    TestMatch,
    find_related_tests,
)


def _impact_key_symbols(
    engine: RepoMapEngine, target_files: list[str], limit_per_file: int = 8
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for file_path in target_files:
        symbols = [
            engine.graph.symbols[sid]
            for sid in engine.graph.file_symbols.get(file_path, [])
            if sid in engine.graph.symbols
        ]
        symbols.sort(
            key=lambda symbol: (
                -symbol.pagerank,
                -len(engine.graph.incoming.get(symbol.id, [])),
                symbol.line,
                symbol.name,
            )
        )
        for symbol in symbols[:limit_per_file]:
            result.append(
                {
                    "name": symbol.name,
                    "kind": symbol.kind,
                    "file": symbol.file,
                    "line": symbol.line,
                    "pagerank": symbol.pagerank,
                    "incomingCount": len(engine.graph.incoming.get(symbol.id, [])),
                    "outgoingCount": len(engine.graph.outgoing.get(symbol.id, [])),
                    "signature": symbol.signature,
                }
            )
    return result


def _impact_read_next(
    target_files: list[str],
    affected_list: list[tuple[str, str, str]],
    tests: list[TestMatch],
    limit: int = 10,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(path: str, reason: str, role: str) -> None:
        if len(result) >= limit or path in seen:
            return
        seen.add(path)
        result.append({"file": path, "reason": reason, "role": role})

    for file_path in target_files:
        add(file_path, "target file", "target")
    for file_path, why, confidence in affected_list:
        if confidence == "high":
            add(file_path, why, "affected")
    for test in tests:
        add(test.test_file, test.reason, "test")
    for file_path, why, _confidence in affected_list:
        add(file_path, why, "affected")
    return result


def _impact_lsp_hint(
    project_root: str | Path, target_files: list[str]
) -> dict[str, Any]:
    try:
        from ...lsp import detect_lsp_server, detection_to_dict, language_for_file
    except Exception as exc:
        return {
            "available": False,
            "servers": [],
            "suggestedCommands": [],
            "reason": str(exc),
        }

    servers: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for file_path in target_files:
        language = language_for_file(file_path)
        if not language:
            continue
        detection = detect_lsp_server(project_root, language, file_path)
        key = (detection.language, detection.server_name)
        if key in seen:
            continue
        seen.add(key)
        servers.append(detection_to_dict(detection))
    available = any(server.get("status") == "available" for server in servers)
    suggested: list[str] = []
    if available and target_files:
        import shlex

        files_arg = " ".join(shlex.quote(f) for f in target_files)
        suggested.append(
            f"repomap check --project {shlex.quote(str(project_root))} --modified-file {files_arg}"
        )
        suggested.append(
            f"repomap call-chain --project {shlex.quote(str(project_root))} --symbol <symbol>"
        )
    return {"available": available, "servers": servers, "suggestedCommands": suggested}


def _impact_type_level(
    engine: RepoMapEngine,
    target_files: list[str],
) -> list[dict[str, Any]]:
    """Detect type-level impact: return type / parameter type changes for exported symbols."""
    results: list[dict[str, Any]] = []
    target_set = set(target_files)

    for f in target_files:
        for sid in engine.graph.file_symbols.get(f, []):
            sym = engine.graph.symbols.get(sid)
            if sym is None:
                continue

            # Check if symbol has callers outside the changed files
            external_callers: list[str] = []
            for edge in engine.graph.incoming.get(sid, []):
                caller = engine.graph.symbols.get(edge.source)
                if caller and caller.file not in target_set:
                    external_callers.append(f"{caller.name} ({caller.file})")

            if not external_callers:
                continue

            entry: dict[str, Any] = {
                "symbol": sym.name,
                "file": sym.file,
                "line": sym.line,
                "kind": sym.kind,
                "affected_callers": external_callers[:10],
                "return_type_changed": False,
                "param_type_changed": False,
                "note": "",
            }

            # 不在当前扫描中做 caller/callee 类型比较——
            # 正确的变更检测需要前后版本对比（cache diff），此处仅标记
            # 有外部调用者的符号，由 verify --with-diff 做精确比较

            results.append(entry)

    return results


_IMPACT_MAX_DEPTH = 3
_IMPACT_LARGE_FILE_SYMBOL_THRESHOLD = 50


def _is_large_file(engine: RepoMapEngine, file_path: str) -> bool:
    return (
        len(engine.graph.file_symbols.get(file_path, []))
        > _IMPACT_LARGE_FILE_SYMBOL_THRESHOLD
    )


def run_impact(
    project: str,
    max_files: int,
    target_files: list[str],
    max_affected_files: int,
    as_json: bool,
    with_symbols: bool = False,
    depth: int = 1,
    incremental: bool = False,
    compact: bool = False,
    top_n: int = 5,
) -> int:
    if depth > _IMPACT_MAX_DEPTH:
        print(
            f"[{CLI_NAME}] --depth {depth} exceeds max {_IMPACT_MAX_DEPTH}, clamping to {_IMPACT_MAX_DEPTH}",
            file=sys.stderr,
        )
        depth = _IMPACT_MAX_DEPTH
    try:
        engine = _scan_engine(project, max_files, incremental=incremental)

        target_files = _normalize_project_relative_paths(
            engine.project_root, target_files
        )

        # 收集目标文件符号
        target_symbols: set[str] = set()
        for f in target_files:
            for sid in engine.graph.file_symbols.get(f, []):
                target_symbols.add(sid)

        # 超大文件自动降级：跳过传递影响展开以避免超时
        large_files = [f for f in target_files if _is_large_file(engine, f)]
        if large_files and depth > 1:
            large_list = ", ".join(f"`{f}`" for f in large_files[:3])
            if len(large_files) > 3:
                large_list += f" (+ {len(large_files) - 3} more)"
            print(
                f"[{CLI_NAME}] info: {large_list} has >{_IMPACT_LARGE_FILE_SYMBOL_THRESHOLD} symbols; "
                f"limiting to depth=1 to avoid timeout",
                file=sys.stderr,
            )
            depth = 1

        # 找出引用者有谁（incoming edges）
        affected_files: dict[str, tuple[str, str]] = {}  # file -> (why, confidence)
        for sid in target_symbols:
            for edge in engine.graph.incoming.get(sid, []):
                caller = engine.graph.symbols.get(edge.source)
                if caller and caller.file not in target_files:
                    affected_files[caller.file] = (
                        f"references {_sym_name(engine, sid)}",
                        "high",
                    )

            for edge in engine.graph.outgoing.get(sid, []):
                callee = engine.graph.symbols.get(edge.target)
                if callee and callee.file not in target_files:
                    callee_name = callee.name
                    if callee.file not in affected_files:
                        affected_files[callee.file] = (
                            f"input file calls {callee_name}（via {_sym_name(engine, sid)}）",
                            "medium",
                        )

        # 传递影响展开：用 BFS 从已影响文件的符号出发，找更深层的文件
        # 同时构建按 hop 分组的影响半径数据
        impact_radius: list[dict[str, Any]] = []
        if depth > 1 and affected_files:
            processed_files = set(target_files) | set(affected_files)
            frontier: set[str] = set(affected_files)
            for current_depth in range(1, depth):
                next_frontier: set[str] = set()
                hop_files: list[str] = []
                hop_symbols: list[str] = []
                for affected_file in frontier:
                    for sid in engine.graph.file_symbols.get(affected_file, []):
                        # 谁调用了这个受影响文件的符号？
                        for edge in engine.graph.incoming.get(sid, []):
                            src_sym = engine.graph.symbols.get(edge.source)
                            if src_sym and src_sym.file not in processed_files:
                                next_frontier.add(src_sym.file)
                                if src_sym.file not in affected_files:
                                    affected_files[src_sym.file] = (
                                        f"transitive impact depth={current_depth + 1}: calls {affected_file} in {src_sym.name}",
                                        "low",
                                    )
                                    hop_files.append(src_sym.file)
                                    hop_symbols.append(src_sym.name)
                        # 这个受影响文件的符号调用了谁？
                        for edge in engine.graph.outgoing.get(sid, []):
                            tgt_sym = engine.graph.symbols.get(edge.target)
                            if tgt_sym and tgt_sym.file not in processed_files:
                                next_frontier.add(tgt_sym.file)
                                if tgt_sym.file not in affected_files:
                                    affected_files[tgt_sym.file] = (
                                        f"transitive impact depth={current_depth + 1}: called by {affected_file} in {_sym_name(engine, sid)}",
                                        "low",
                                    )
                                    hop_files.append(tgt_sym.file)
                                    hop_symbols.append(tgt_sym.name)
                if hop_files:
                    impact_radius.append(
                        {
                            "hop": current_depth + 1,
                            "files": sorted(set(hop_files)),
                            "symbols": sorted(set(hop_symbols)),
                        }
                    )
                processed_files |= next_frontier
                frontier = next_frontier
                if not frontier:
                    break

        # 找相关测试
        analysis = engine.file_analysis()
        tests = find_related_tests(
            target_files, engine.graph, analysis, str(engine.project_root)
        )

        # 风险评估
        risk_level, risk_notes = _assess_risk(target_files, set(affected_files), engine)

        # Type-level impact analysis
        type_impacts = _impact_type_level(engine, target_files)

        affected_list = [(f, why, conf) for f, (why, conf) in affected_files.items()]
        # 预构建 file severity 索引（O(total_edges)），替代排序中每次 O(S*I) 计算
        file_severity = _build_file_severity_index(engine.graph)
        # 按影响严重程度排序：置信度高→严重度高→文件路径（tiebreaker）
        affected_list.sort(
            key=lambda x: (
                -{"high": 3, "medium": 2, "low": 1}.get(x[2], 0),
                -file_severity.get(x[0], 0),
                x[0],
            ),
        )
        affected_list = affected_list[:max_affected_files]

        # 将 impact 结果序列化到 <project>/.repomap/session.json，
        # 供后续 verify 命令与 git diff 对比使用。写入失败不影响本次 impact 输出。
        session_warning: str | None = None
        try:
            session_target_files = list(target_files)
            session_affected_files = [f for f, _why, _conf in affected_list]
            session_key_symbols = _impact_key_symbols(
                engine, target_files, limit_per_file=4
            )
            session_suggested_tests = [t.test_file for t in tests]
            save_impact_session(
                project_root=engine.project_root,
                target_files=session_target_files,
                affected_files=session_affected_files,
                key_symbols=session_key_symbols,
                suggested_tests=session_suggested_tests,
            )
        except Exception as exc:
            session_warning = f"failed to persist impact session: {exc}"
            print(
                f"[{CLI_NAME}] warning: {session_warning}",
                file=sys.stderr,
            )

        key_symbols = _impact_key_symbols(engine, target_files) if with_symbols else []
        read_next = _impact_read_next(target_files, affected_list, tests)
        lsp_hint = (
            _impact_lsp_hint(engine.project_root, target_files) if with_symbols else {}
        )

        if as_json:
            from ..handlers import json_envelope

            # compact 模式：限制 affectedFiles 数量，添加总数摘要
            if compact:
                total_affected = len(affected_list)
                display_affected = affected_list[:top_n]
            else:
                total_affected = None
                display_affected = affected_list

            result: dict[str, Any] = {
                "scan_stats": _scan_stats_payload(engine),
                "input_files": target_files,
                "depth": depth,
                "impact_radius": impact_radius,
                "affected_files": [
                    {"file": f, "why": why, "confidence": conf}
                    for f, why, conf in display_affected
                ],
                "tests": [
                    {
                        "test_file": t.test_file,
                        "target_file": t.target_file,
                        "confidence": t.confidence,
                        "reason": t.reason,
                    }
                    for t in tests
                ],
                "risk_level": risk_level,
                "risk_notes": risk_notes,
                "key_symbols": key_symbols,
                "read_next": read_next,
                "lsp_hint": lsp_hint,
                "type_impacts": type_impacts,
            }
            if total_affected is not None:
                result["affectedFilesCount"] = total_affected
            if session_warning:
                result["session_warning"] = session_warning
            print(json_envelope("impact", str(engine.project_root), result))
            return 0

        print(
            render_impact_report(
                engine,
                target_files,
                affected_list,
                tests,
                risk_level,
                risk_notes,
                key_symbols=key_symbols,
                read_next=read_next,
                lsp_hint=lsp_hint,
                compact=compact,
                top_n=top_n,
            )
        )
        # Print type-level impacts
        if type_impacts:
            print("\n## Type-Level Impact\n")
            for ti in type_impacts:
                print(f"- **{ti['symbol']}** (`{ti['file']}:{ti['line']}`)")
                if ti.get("return_type_changed"):
                    print("  - Return type may differ from callers' expectations")
                if ti.get("param_type_changed"):
                    print("  - Parameter types may differ from callers' expectations")
                if ti.get("affected_callers"):
                    print(
                        f"  - Affected callers: {', '.join(ti['affected_callers'][:5])}"
                    )
                if ti.get("note"):
                    print(f"  - {ti['note']}")
                print("")
        for hint in impact_hint(
            risk_level=risk_level, has_suggested_tests=len(tests) > 0
        ):
            print(hint, file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] impact failed: {exc}", file=sys.stderr)
        return 1


def _affected_severity(file_path: str, engine: RepoMapEngine) -> int:
    """计算受影响文件的严重程度：文件中符号被外部调用的总次数。"""
    total = 0
    for sid in engine.graph.file_symbols.get(file_path, []):
        for edge in engine.graph.incoming.get(sid, []):
            if edge.kind == "call":
                src_sym = engine.graph.symbols.get(edge.source)
                if src_sym and src_sym.file != file_path:
                    total += 1
    return total


def _build_file_severity_index(graph: RepoGraph) -> dict[str, int]:
    """一次遍历 incoming edges 构建 file → external_caller_count 索引。

    对每条 kind="call" 且 source file ≠ target file 的边，
    累加 target file 的计数。复杂度 O(total_edges)。
    """
    sid_to_file: dict[str, str] = {}
    for sid, sym in graph.symbols.items():
        sid_to_file[sid] = sym.file

    index: dict[str, int] = {}
    for target_sid, edges in graph.incoming.items():
        target_file = sid_to_file.get(target_sid)
        if not target_file:
            continue
        for edge in edges:
            if edge.kind == "call":
                src_file = sid_to_file.get(edge.source)
                if src_file and src_file != target_file:
                    index[target_file] = index.get(target_file, 0) + 1
    return index
