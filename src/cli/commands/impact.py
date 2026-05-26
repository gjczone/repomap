from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from ... import json_dumps
from ... import (
    Symbol,
    get_session_cache_path,
)
from ...ai import render_impact_report
from ...core import RepoMapEngine
from ..handlers import (
    CLI_NAME,
    EXIT_SUCCESS,
    EXIT_ERROR,
    _resolve_project,
    _scan_engine,
    _normalize_project_relative_paths,
    _scan_stats_payload,
    _select_symbol_match,
    _sym_name,
    _assess_risk,
)
from ...lsp import detect_lsp_server
from ...topic import (
    TestMatch,
    find_related_tests,
    is_test_like_file,
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
        files_arg = " ".join(target_files)
        suggested.append(
            f"repomap check --project {project_root} --modified-file {files_arg}"
        )
        suggested.append(
            f"repomap refs --project {project_root} --symbol <symbol> --file-path <file>"
        )
    return {"available": available, "servers": servers, "suggestedCommands": suggested}


def _impact_type_level(
    engine: RepoMapEngine,
    target_files: list[str],
) -> list[dict[str, Any]]:
    """Detect type-level impact: return type / parameter type changes for exported symbols."""
    results: list[dict[str, Any]] = []

    for f in target_files:
        for sid in engine.graph.file_symbols.get(f, []):
            sym = engine.graph.symbols.get(sid)
            if sym is None:
                continue

            # Check if symbol has callers outside the changed files
            target_set = set(target_files)
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

            # Compare symbol signature with what callers might expect
            if sym.return_type:
                for edge in engine.graph.incoming.get(sid, []):
                    caller = engine.graph.symbols.get(edge.source)
                    if (
                        caller
                        and caller.return_type
                        and caller.return_type != sym.return_type
                    ):
                        entry["return_type_changed"] = True
                        entry["note"] = (
                            f"Return type `{sym.return_type}` may not match "
                            f"caller `{caller.name}` expectation `{caller.return_type}`"
                        )
                        break

            if sym.signature:
                for edge in engine.graph.incoming.get(sid, []):
                    caller = engine.graph.symbols.get(edge.source)
                    if (
                        caller
                        and caller.signature
                        and caller.signature != sym.signature
                    ):
                        entry["param_type_changed"] = True
                        if not entry["note"]:
                            entry["note"] = (
                                f"Signature `{sym.signature}` may conflict with "
                                f"caller `{caller.name}` signature `{caller.signature}`"
                            )
                        break

            results.append(entry)

    return results


def run_impact(
    project: str,
    max_files: int,
    target_files: list[str],
    max_affected_files: int,
    as_json: bool,
    with_symbols: bool = False,
    depth: int = 1,
    incremental: bool = False,
) -> int:
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
        if depth > 1 and affected_files:
            processed_files = set(target_files) | set(affected_files)
            frontier: set[str] = set(affected_files)
            for current_depth in range(1, depth):
                next_frontier: set[str] = set()
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
        # 按影响严重程度排序：受影响文件中符号的外部调用者越多越靠前
        affected_list.sort(
            key=lambda x: (
                {"high": 3, "medium": 2, "low": 1}.get(x[2], 0),
                -_affected_severity(x[0], engine),
                x[0],
            ),
            reverse=True,
        )
        affected_list = sorted(
            affected_list,
            key=lambda x: (
                -{"high": 3, "medium": 2, "low": 1}.get(x[2], 0),
                -_affected_severity(x[0], engine),
            ),
        )
        affected_list = affected_list[:max_affected_files]
        key_symbols = _impact_key_symbols(engine, target_files) if with_symbols else []
        read_next = _impact_read_next(target_files, affected_list, tests)
        lsp_hint = (
            _impact_lsp_hint(engine.project_root, target_files) if with_symbols else {}
        )

        if as_json:
            payload = {
                "schema_version": "1.0",
                "command": "impact",
                "project": str(engine.project_root),
                "scanStats": _scan_stats_payload(engine),
                "result": {
                    "inputFiles": target_files,
                    "affectedFiles": [
                        {"file": f, "why": why, "confidence": conf}
                        for f, why, conf in affected_list
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
                    "riskLevel": risk_level,
                    "riskNotes": risk_notes,
                    "keySymbols": key_symbols,
                    "readNext": read_next,
                    "lspHint": lsp_hint,
                    "typeImpacts": type_impacts,
                },
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
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


def _assess_risk(
    target_files: list[str],
    affected_files: set[str],
    engine: RepoMapEngine,
) -> tuple[str, list[str]]:
    """三层风险评估模型。返回 (risk_level, risk_notes)。"""
    risk_notes: list[str] = []
    total_score = 0

    # 第1层：结构风险
    analysis = engine.file_analysis()
    structural_risk = 0
    for f in target_files:
        file_data = analysis.get(f, {})
        nc = file_data.get("neighbor_count", 0)
        if nc >= 10:
            structural_risk += 4
            risk_notes.append(
                f"`{f}` associated with {nc} files, very high blast radius"
            )
        elif nc >= 5:
            structural_risk += 3
            risk_notes.append(f"`{f}` associated with {nc} files, high blast radius")
        for sid in engine.graph.file_symbols.get(f, []):
            sym = engine.graph.symbols.get(sid)
            if sym and sym.pagerank > 0.01:
                structural_risk += 1
                break
    total_score += structural_risk

    # 第2层：领域关键词风险
    domain_risk = 0
    risk_keywords_high = [
        "auth",
        "token",
        "session",
        "password",
        "security",
        "migration",
        "database",
        "schema",
        "persistence",
    ]
    risk_keywords_medium = [
        "terminal",
        "websocket",
        "pty",
        "input",
        "config",
        "build",
        "deploy",
        "ci",
    ]
    all_paths = " ".join(target_files + list(affected_files)).lower()
    for kw in risk_keywords_high:
        if kw in all_paths:
            domain_risk += 3
    for kw in risk_keywords_medium:
        if kw in all_paths:
            domain_risk += 1
    if domain_risk >= 6:
        risk_notes.append("touches high-risk domain (auth/security/data persistence)")
    elif domain_risk >= 3:
        risk_notes.append("touches medium-risk domain (terminal/config/build)")
    total_score += domain_risk

    # 第3层：变更类型风险
    change_type_risk = 0
    for f in target_files:
        if is_test_like_file(f):
            pass  # 只改测试不改实现，低风险
        elif any(
            f.endswith(ext) for ext in [".config.ts", ".config.js", "package.json"]
        ):
            change_type_risk += 2
            risk_notes.append(f"`{f}` is a config file change with global impact")
        elif "types" in PurePosixPath(f).parts or f.endswith(".d.ts"):
            change_type_risk += 1
            risk_notes.append(f"`{f}` is a type definition change with wide impact")
    total_score += change_type_risk

    level = "high" if total_score >= 6 else "medium" if total_score >= 3 else "low"
    return level, risk_notes
