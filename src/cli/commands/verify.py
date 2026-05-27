from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from ... import json_dumps
from ... import (
    Symbol,
)
from ...ai import render_verify_report
from ...check import RepoMapChecker
from ...core import RepoMapEngine
from ...git_backend import GitBackend
from ..handlers import (
    CLI_NAME,
    EXIT_NO_RESULTS,
    _resolve_project,
    _scan_engine,
    _scan_stats_payload,
    _sym_name,
    _assess_risk,
    load_impact_session,
    _normalize_project_relative_paths,
)
from ...ranking import GraphAnalyzer
from ...toolkit import diff_project, scan_project
from ...topic import (
    find_related_tests,
    find_untested_symbols,
    is_test_like_file,
)

logger = logging.getLogger("repomap")

_ORPHAN_EXCLUDED_KINDS: set[str] = {
    "element",
    "json_key",
    "module",
    "handler",
}

_ORPHAN_EXCLUDED_EXTENSIONS: set[str] = {
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".scss",
    ".less",
}

_TEST_PATH_MARKERS: tuple[str, ...] = ("test", "spec", "e2e", "__test__", "__tests__")

_ORPHAN_KIND_BASE: dict[str, int] = {
    "function": 60,
    "method": 60,
    "struct": 40,
    "enum": 40,
    "class": 40,
    "type": 40,
    "interface": 35,
    "anonymous_function": 30,
    "variable": 30,
    "const": 30,
    "impl": 15,
    "trait": 35,
}


def _parse_git_status_porcelain_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        # porcelain format: "XY path" (X=index, Y=work-tree, then space, then path)
        # Handle both "XY path" (len>=3, space at pos 2) and "X path" (len>=2, space at pos 1)
        if len(line) >= 3 and line[2] == " ":
            path = line[3:]
        elif len(line) >= 2 and line[1] == " ":
            path = line[2:]
        else:
            continue
        if " -> " in path:
            path = path.split(" -> ")[-1]
        path = path.strip()
        if path:
            paths.append(path)
    return paths


def _child_git_project_candidates(project_path: Path, limit: int = 8) -> list[Path]:
    candidates: list[Path] = []
    try:
        children = sorted(project_path.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        if (child / ".git").exists():
            candidates.append(child.resolve())
            if len(candidates) >= limit:
                break
    return candidates


def _collect_changed_files(project_root: str | Path) -> tuple[list[str], str | None]:

    project_path = Path(project_root).resolve()
    git = GitBackend(str(project_path))
    git_root = git.show_toplevel()
    if not git_root:
        message = (
            f"not a git repository: {project_path}. "
            "Run from the intended project directory or pass --project explicitly."
        )
        candidates = _child_git_project_candidates(project_path)
        if candidates:
            candidate_lines = "\n".join(
                f"- --project {candidate}" for candidate in candidates
            )
            message = (
                f"{message}\n"
                "LLM action: select the intended project root, then re-run the same repomap command with exactly one --project argument.\n"
                f"Candidate --project arguments:\n{candidate_lines}"
            )
        return [], message

    status_lines = git.status_porcelain()
    changed_files: list[str] = []
    for stripped in _parse_git_status_porcelain_paths("\n".join(status_lines)):
        abs_path = Path(git_root, stripped).resolve()
        try:
            changed_files.append(abs_path.relative_to(project_path).as_posix())
        except ValueError:
            logger.debug("Changed file outside project root: %s", abs_path)
    return changed_files, None


def _detect_contract_risks(
    engine: RepoMapEngine, changed_files: list[str]
) -> list[dict[str, str]]:
    """Detect contract-level risks from changed files: route changes, signature changes, test gaps."""
    warnings: list[dict[str, str]] = []
    routes = engine.list_routes()
    changed_set = set(changed_files)

    # Route/API risks
    for route in routes:
        if route.file in changed_set:
            warnings.append(
                {
                    "level": "MED",
                    "message": f"Route `{route.method} {route.path}` (handler in `{route.file}`) changed; review consumers and related tests.",
                }
            )

    # Symbol/public surface risks: check exported/public symbols in changed files
    for file_path in changed_files:
        for sid in engine.graph.file_symbols.get(file_path, []):
            sym = engine.graph.symbols.get(sid)
            if not sym:
                continue
            # Count cross-file incoming edges only (import + call references from other files)
            cross_file_refs = [
                e
                for e in engine.graph.incoming.get(sid, [])
                if engine.graph.symbols.get(e.source)
                and engine.graph.symbols[e.source].file != sym.file
            ]
            ref_count = len(cross_file_refs)
            if sym.visibility in ("exported", "public") and ref_count >= 3:
                warnings.append(
                    {
                        "level": "MED",
                        "message": f"Exported symbol `{sym.name}` in `{sym.file}` has {ref_count} cross-file references.",
                    }
                )
            elif ref_count >= 10:
                warnings.append(
                    {
                        "level": "MED",
                        "message": f"Heavily referenced symbol `{sym.name}` `({sym.kind})` in `{sym.file}` changed; {ref_count} cross-file references.",
                    }
                )

    # Enum/type risks
    for file_path in changed_files:
        for sid in engine.graph.file_symbols.get(file_path, []):
            sym = engine.graph.symbols.get(sid)
            if sym and sym.kind in ("enum", "type", "struct", "class"):
                cross_file_refs = [
                    e
                    for e in engine.graph.incoming.get(sid, [])
                    if engine.graph.symbols.get(e.source)
                    and engine.graph.symbols[e.source].file != sym.file
                ]
                if cross_file_refs:
                    warnings.append(
                        {
                            "level": "MED",
                            "message": f"Type `{sym.name}` `({sym.kind})` in `{sym.file}` changed; {len(cross_file_refs)} cross-file references.",
                        }
                    )

    # Test/implementation mismatch
    test_files = [f for f in changed_files if is_test_like_file(f)]
    impl_files = [
        f
        for f in changed_files
        if not is_test_like_file(f)
        and not f.endswith((".md",))
        and "dist/" not in f
        and "docs/" not in f
    ]
    if test_files and not impl_files:
        warnings.append(
            {
                "level": "LOW",
                "message": f"Only test files changed ({len(test_files)} file(s)); verify tests are intentional.",
            }
        )
    if impl_files and not test_files:
        warnings.append(
            {
                "level": "MED",
                "message": f"Implementation file(s) changed ({len(impl_files)} file(s)) without related tests.",
            }
        )

    # Config/runtime risks
    config_patterns = [
        ".env",
        "config",
        "Dockerfile",
        "Makefile",
        "migration",
        "schema",
    ]
    config_files = [
        f
        for f in changed_files
        if any(p in f.lower() for p in config_patterns) and not f.endswith(".md")
    ]
    if config_files:
        warnings.append(
            {
                "level": "MED",
                "message": f"Config/runtime files changed: {', '.join(f'`{f}`' for f in config_files[:3])}.",
            }
        )

    return warnings


def _diff_risk_evidence(
    engine: RepoMapEngine, changed_files: list[str]
) -> dict[str, Any]:
    analysis = engine.file_analysis()

    target_symbols: set[str] = set()
    for file_path in changed_files:
        for symbol_id in engine.graph.file_symbols.get(file_path, []):
            target_symbols.add(symbol_id)

    affected_files_dict: dict[str, tuple[str, str]] = {}
    for symbol_id in target_symbols:
        for edge in engine.graph.incoming.get(symbol_id, []):
            caller = engine.graph.symbols.get(edge.source)
            if caller and caller.file not in changed_files:
                affected_files_dict[caller.file] = (
                    f"references changed symbol {_sym_name(engine, symbol_id)}",
                    "high",
                )

    affected_list = [
        (file_path, why, confidence)
        for file_path, (why, confidence) in affected_files_dict.items()
    ]
    affected_list.sort(key=lambda item: (item[2], item[0]))

    source_files = [
        file_path for file_path in changed_files if not is_test_like_file(file_path)
    ]
    tests = find_related_tests(
        source_files, engine.graph, analysis, str(engine.project_root)
    )
    risk_level, risk_reasons = _assess_risk(
        source_files, set(file_path for file_path, _, _ in affected_list), engine
    )

    missing_checks: list[str] = []
    all_exts = set(Path(file_path).suffix for file_path in changed_files)
    if ".ts" in all_exts or ".tsx" in all_exts:
        if not any(test.test_file.endswith((".ts", ".tsx")) for test in tests):
            missing_checks.append(
                "No TypeScript test file changes detected; consider adding tests"
            )
    if ".py" in all_exts:
        if not any(test.test_file.endswith(".py") for test in tests):
            missing_checks.append(
                "No Python test file changes detected; consider adding tests"
            )

    return {
        "affectedList": affected_list,
        "tests": tests,
        "riskLevel": risk_level,
        "riskReasons": risk_reasons,
        "missingChecks": missing_checks,
    }


def _run_check_payload(
    project_root: str,
    types: list[str] | None,
    max_issues: int,
    modified_files: list[str] | None,
    resolve_symbols: bool,
    with_lsp: bool,
    lsp_timeout: float,
    lsp_max_files: int,
) -> dict[str, Any]:
    symbols_map = None
    if resolve_symbols:
        engine = _scan_engine(project_root, 8000)
        symbols_map = engine.graph.symbols
    checker = RepoMapChecker(project_root, max_issues)
    return checker.check(
        types=types,
        resolve_symbols=resolve_symbols and symbols_map is not None,
        symbols_map=symbols_map,
        modified_files=modified_files,
        with_lsp=with_lsp,
        lsp_timeout=lsp_timeout,
        lsp_max_files=lsp_max_files,
    )


def _verify_lsp_payload(
    project_root: str,
    changed_files: list[str],
    enabled: bool,
    timeout: float,
    max_files: int,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "status": "skipped", "runs": [], "summary": {}}
    if not changed_files:
        return {
            "enabled": True,
            "status": "skipped",
            "runs": [],
            "summary": {},
            "reason": "no changed files",
        }
    try:
        from ...lsp import collect_lsp_diagnostics, run_result_to_dict

        runs = collect_lsp_diagnostics(
            project_root, changed_files, timeout=timeout, max_files=max_files
        )
        run_dicts = [run_result_to_dict(run) for run in runs]
        total_errors = sum(
            1 for run in runs for item in run.diagnostics if item.severity == "error"
        )
        total_warnings = sum(
            1 for run in runs for item in run.diagnostics if item.severity != "error"
        )
        failed_runs = sum(1 for run in runs if run.status in {"failed", "timeout"})
        skipped_runs = sum(1 for run in runs if run.status == "skipped")
        status = "failed" if total_errors or failed_runs else "passed"
        if skipped_runs and skipped_runs == len(runs):
            status = "skipped"
        return {
            "enabled": True,
            "status": status,
            "runs": run_dicts,
            "summary": {
                "totalErrors": total_errors,
                "totalWarnings": total_warnings,
                "failedRuns": failed_runs,
                "skippedRuns": skipped_runs,
            },
        }
    except Exception as exc:
        return {
            "enabled": True,
            "status": "failed",
            "runs": [],
            "summary": {},
            "reason": str(exc),
        }


def _verify_graph_diff_payload(
    project_root: str, enabled: bool, incoming_map: dict | None = None
) -> dict[str, Any]:
    if not enabled:
        return {
            "enabled": False,
            "status": "skipped",
            "summary": {},
            "breakingChanges": [],
        }
    result = diff_project(project_root)
    if "error" in result:
        return {
            "enabled": True,
            "status": "skipped",
            "summary": {},
            "breakingChanges": [],
            "reason": result["error"],
        }
    # 如果提供了 incoming_map，二次调用带调用者分析的 compare
    if incoming_map is not None:
        from ...toolkit import load_cache
        from ... import compare_graph_snapshots

        cache = load_cache(project_root)
        if cache:
            current_symbols, current_edges = scan_project(project_root, max_files=5000)
            enriched = compare_graph_snapshots(
                current_symbols=current_symbols,
                current_edges=current_edges,
                previous_symbols=cache.symbols,
                previous_edges=cache.edges,
                incoming_map=incoming_map,
            )
            breaking = [
                ms
                for ms in enriched.get("modified_symbols", [])
                if ms.get("risk") in ("HIGH", "MEDIUM") and ms.get("signature_changed")
            ]
            result["breakingChanges"] = breaking[:20]
    if "breakingChanges" not in result:
        result["breakingChanges"] = []
    summary = result.get("summary", {})
    changed = any(
        summary.get(key, 0)
        for key in ("added", "removed", "modified", "edges_added", "edges_removed")
    )
    result["status"] = "changed" if changed else "unchanged"
    return result


def _overall_verify_status(
    changed_files: list[str],
    risk_level: str,
    missing_checks: list[str],
    check_payload: dict[str, Any],
    lsp_payload: dict[str, Any],
    graph_diff_payload: dict[str, Any],
    impact_session_payload: dict[str, Any] | None = None,
) -> str:
    if check_payload.get("status") == "failed" or lsp_payload.get("status") == "failed":
        return "failed"
    if not changed_files:
        return "warning"
    # risk_level 表示变更影响面，不等于未解决风险；只有缺证据或破坏性图谱变化才阻断交付。
    if missing_checks or graph_diff_payload.get("breakingChanges"):
        return "warning"
    check_status = check_payload.get("status")
    if check_status == "warning":
        return "warning"
    # unknown 表示没有诊断工具运行，不能视为 passed；缺失 status 同样视为 warning
    if check_status in (None, "unknown"):
        return "warning"
    # impact session 漏改：不影响其他 warning/failed 判定，但把 passed 降为 warning
    if (
        impact_session_payload is not None
        and impact_session_payload.get("status") == "missed"
    ):
        return "warning"
    return "passed"


def _verify_impact_session_payload(
    project_root: str | Path, changed_files: list[str]
) -> dict[str, Any]:
    """对比 impact session 与 git diff，返回比对结果字典。

    返回结构：
      status: ok | missed | no_changes | skipped
      missedFiles: list[str]     — impact 预期但未在 git diff 出现的 affected 文件
      unexpectedFiles: list[str] — git diff 中但 impact 未预期的文件
      coveredFiles: list[str]    — git diff 中且 impact 预期覆盖的文件
      sessionAgeSeconds: int | None
      reason: str | None         — 仅在 skipped 时给出原因

    任何内部异常（包括 session 文件被篡改导致的 set/list 构造失败）都降级为
    status=skipped，绝不让补充性校验拖垮 verify 主流程。
    """
    from datetime import datetime, timezone

    def _skipped(reason: str) -> dict[str, Any]:
        return {
            "status": "skipped",
            "missedFiles": [],
            "unexpectedFiles": [],
            "coveredFiles": [],
            "sessionAgeSeconds": None,
            "reason": reason,
        }

    try:
        session = load_impact_session(project_root)
    except Exception as exc:
        return _skipped(f"impact session load failed: {exc}")
    if session is None:
        return _skipped("no .repomap/session.json or unreadable/outdated schema")

    age_seconds: int | None = None
    created_at = session.get("created_at")
    if isinstance(created_at, str):
        try:
            normalized = created_at
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            created_dt = datetime.fromisoformat(normalized)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            age_seconds = max(
                0,
                int((datetime.now(timezone.utc) - created_dt).total_seconds()),
            )
        except (ValueError, TypeError):
            age_seconds = None

    try:
        impact = session.get("impact") or {}
        target_files = set(impact.get("target_files", []))
        affected_files = set(impact.get("affected_files", []))
    except TypeError as exc:
        return _skipped(f"impact session element type invalid: {exc}")

    expected = target_files | affected_files
    changed_set = set(changed_files)

    if not changed_set:
        return {
            "status": "no_changes",
            "missedFiles": [],
            "unexpectedFiles": [],
            "coveredFiles": [],
            "sessionAgeSeconds": age_seconds,
            "reason": "git diff is empty",
        }

    missed = sorted(affected_files - changed_set)
    covered = sorted(affected_files & changed_set)
    unexpected = sorted(changed_set - expected)

    status = "missed" if missed else "ok"
    return {
        "status": status,
        "missedFiles": missed,
        "unexpectedFiles": unexpected,
        "coveredFiles": covered,
        "sessionAgeSeconds": age_seconds,
        "reason": None,
    }


def _print_missed_files_section(
    engine: RepoMapEngine,
    changed_files: list[str],
) -> None:
    """Print potentially missed files: callers not in diff + co-change neighbors."""
    print("\n### Potentially missed files\n")

    # 1. For each changed file's symbols, find callers NOT in the git diff
    changed_set = set(changed_files)
    missed_callers: dict[str, list[str]] = {}
    for f in changed_files:
        for sid in engine.graph.file_symbols.get(f, []):
            sym = engine.graph.symbols.get(sid)
            if sym is None:
                continue
            for edge in engine.graph.incoming.get(sid, []):
                caller = engine.graph.symbols.get(edge.source)
                if caller and caller.file not in changed_set:
                    missed_callers.setdefault(caller.file, []).append(
                        f"{sym.name} (via {caller.name})"
                    )

    if missed_callers:
        print("Callers of changed symbols NOT in git diff:")
        for caller_file, reasons in sorted(missed_callers.items()):
            unique_reasons = list(dict.fromkeys(reasons))[:3]
            print(f"  - `{caller_file}` — called by: {', '.join(unique_reasons)}")
    else:
        print("  (no callers outside git diff)")

    # 2. Co-change neighbors
    try:
        from ...topic import get_co_change_neighbors

        co_change_found = False
        for f in changed_files:
            neighbors = get_co_change_neighbors(str(engine.project_root), f, top_n=3)
            if neighbors:
                if not co_change_found:
                    print(
                        "\nCo-change neighbors (files that frequently change together):"
                    )
                    co_change_found = True
                for neighbor_file, count in neighbors:
                    if neighbor_file not in changed_set:
                        print(
                            f"  - `{neighbor_file}` — co-changed {count} times with `{f}`"
                        )
        if not co_change_found:
            print("\n  (no co-change neighbors found)")
    except Exception:
        logger.warning("Co-change neighbor lookup failed", exc_info=True)
    print("")


def run_verify(
    project: str,
    as_json: bool,
    types: list[str] | None,
    max_issues: int,
    resolve_symbols: bool,
    with_lsp: bool,
    lsp_timeout: float,
    lsp_max_files: int,
    with_diff: bool,
    quick: bool = False,
    incremental: bool = False,
    max_chars: int = 16000,
) -> int:
    try:
        project_root = _resolve_project(project)
        changed_files, error = _collect_changed_files(project_root)
        if error:
            print(f"[{CLI_NAME}] verify failed: {error}", file=sys.stderr)
            return 1

        engine = _scan_engine(project_root, 8000, incremental=incremental)
        evidence = _diff_risk_evidence(engine, changed_files)
        contract_risks = _detect_contract_risks(engine, changed_files)

        if quick:
            check_payload = {
                "status": "skipped",
                "summary": {},
                "runs": [],
                "reason": "verify --quick",
            }
            lsp_payload = {
                "enabled": False,
                "status": "skipped",
                "runs": [],
                "summary": {},
                "reason": "verify --quick",
            }
        else:
            check_payload = _run_check_payload(
                project_root=project_root,
                types=types,
                max_issues=max_issues,
                modified_files=changed_files,
                resolve_symbols=resolve_symbols,
                with_lsp=with_lsp,
                lsp_timeout=lsp_timeout,
                lsp_max_files=lsp_max_files,
            )
            lsp_payload = _verify_lsp_payload(
                project_root, changed_files, with_lsp, lsp_timeout, lsp_max_files
            )

        graph_diff_payload = _verify_graph_diff_payload(
            project_root,
            with_diff,
            incoming_map=engine.graph.incoming if with_diff else None,
        )
        impact_session_payload = _verify_impact_session_payload(
            project_root, changed_files
        )
        status = _overall_verify_status(
            changed_files,
            evidence["riskLevel"],
            evidence["missingChecks"],
            check_payload,
            lsp_payload,
            graph_diff_payload,
            impact_session_payload=impact_session_payload,
        )
        untested = find_untested_symbols(engine.graph) if not quick else []

        payload = {
            "schema_version": "1.0",
            "command": "verify",
            "project": str(engine.project_root),
            "scanStats": _scan_stats_payload(engine),
            "result": {
                "status": status,
                "changedFiles": changed_files,
                "risk": {
                    "level": evidence["riskLevel"],
                    "reasons": evidence["riskReasons"],
                    "missingChecks": evidence["missingChecks"],
                },
                "affectedFiles": [
                    {"file": file_path, "why": why, "confidence": confidence}
                    for file_path, why, confidence in evidence["affectedList"]
                ],
                "tests": [
                    {
                        "testFile": test.test_file,
                        "targetFile": test.target_file,
                        "confidence": test.confidence,
                        "reason": test.reason,
                    }
                    for test in evidence["tests"]
                ],
                "untestedSymbols": untested,
                "check": {
                    "status": check_payload.get("status", "unknown"),
                    "summary": check_payload.get("summary", {}),
                    "incremental": check_payload.get("incremental", {}),
                    "runs": check_payload.get("runs", []),
                    "errorsByFile": check_payload.get("errors_by_file", {}),
                },
                "lsp": lsp_payload,
                "graphDiff": graph_diff_payload,
                "contractRisks": contract_risks,
                "impactSession": impact_session_payload,
            },
        }
        if as_json:
            print(json_dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_verify_report(payload, max_chars=max_chars))

        # 如果没有 git 变更，给出下一步建议
        if not changed_files:
            print("\n> No git changes detected.", file=sys.stderr)
            if quick:
                print(
                    "> verify --quick mode only analyzes git changes; no changes found, risk assessment unavailable.",
                    file=sys.stderr,
                )
                print(
                    "> Suggestion: make code changes first, then run `repomap verify` for full verification.",
                    file=sys.stderr,
                )
            else:
                print(
                    "> Suggestion: use `repomap overview` for project structure or `repomap check` for compilation checks.",
                    file=sys.stderr,
                )

        # Potentially missed files section
        if changed_files and not as_json:
            _print_missed_files_section(engine, changed_files)

        if status == "failed":
            return 1
        if status == "warning":
            return EXIT_NO_RESULTS
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] verify failed: {exc}", file=sys.stderr)
        return 1


def _orphan_confidence(symbol: Symbol, orphan_names: set[str]) -> int:
    """Compute a confidence score (0-100) that a symbol is truly dead code."""
    score = _ORPHAN_KIND_BASE.get(symbol.kind, 30)

    # File-level signals
    file_lower = symbol.file.lower()
    for marker in _TEST_PATH_MARKERS:
        if marker in file_lower:
            score -= 20
            break

    # Extension-based filtering (should already be excluded, defensive)
    if any(file_lower.endswith(ext) for ext in _ORPHAN_EXCLUDED_EXTENSIONS):
        score -= 50

    # Name-based signals for test helpers
    name_lower = symbol.name.lower()
    if any(
        name_lower.startswith(prefix) for prefix in ("test_", "it_", "should_", "test")
    ):
        score -= 30

    # Visibility signal: private symbols are more likely truly dead
    if symbol.visibility == "private":
        score += 10

    # Struct/impl pairing heuristics
    if symbol.kind == "impl":
        # impl block whose struct also appears as orphan → the pair might all be dead
        if symbol.name in orphan_names:
            score += 25
    elif symbol.kind in ("struct", "enum", "class", "type"):
        # Struct whose impl also appears → more likely truly dead (entire unit unused)
        if symbol.name in orphan_names:
            score += 25

    return max(0, min(100, score))


def _orphan_note(symbol: Symbol) -> str:
    """Generate a brief reason string for the confidence score."""
    reasons: list[str] = []
    file_lower = symbol.file.lower()
    for marker in _TEST_PATH_MARKERS:
        if marker in file_lower:
            reasons.append("test file")
            break
    name_lower = symbol.name.lower()
    if any(name_lower.startswith(prefix) for prefix in ("test_", "it_", "should_")):
        reasons.append("test helper")
    if symbol.kind == "impl":
        reasons.append("impl block (may be macro-driven)")
    if symbol.kind in ("struct", "enum", "class"):
        reasons.append("type definition (may use reflection/macros)")
    if not reasons:
        reasons.append("no callers or callees")
    return "; ".join(reasons)


def run_orphan(
    project: str,
    max_files: int,
    as_json: bool = False,
    limit: int = 20,
    min_confidence: int = 0,
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        symbol_ids = set(engine.graph.symbols.keys())
        calls_in: dict[str, set[str]] = {symbol_id: set() for symbol_id in symbol_ids}
        calls_out: dict[str, set[str]] = {symbol_id: set() for symbol_id in symbol_ids}
        for source_id, edge_list in engine.graph.outgoing.items():
            for edge in edge_list:
                if edge.kind != "call":
                    continue
                calls_out.setdefault(source_id, set()).add(edge.target)
                calls_in.setdefault(edge.target, set()).add(source_id)

        candidates: list[Symbol] = []
        filtered_structural_count = 0
        for sid in symbol_ids:
            if len(calls_in[sid]) == 0 and len(calls_out[sid]) == 0:
                symbol = engine.graph.symbols[sid]
                if symbol.name in {"main", "__main__"}:
                    continue
                if symbol.visibility == "exported":
                    continue
                if symbol.kind in _ORPHAN_EXCLUDED_KINDS:
                    filtered_structural_count += 1
                    continue
                if any(
                    symbol.file.lower().endswith(ext)
                    for ext in _ORPHAN_EXCLUDED_EXTENSIONS
                ):
                    filtered_structural_count += 1
                    continue
                candidates.append(symbol)

        # Build orphan name set for struct/impl pairing heuristic
        orphan_names: set[str] = {s.name for s in candidates}

        # Compute confidence for each candidate
        scored: list[dict] = []
        for symbol in candidates:
            conf = _orphan_confidence(symbol, orphan_names)
            scored.append(
                {
                    "symbol": symbol,
                    "confidence": conf,
                    "note": _orphan_note(symbol),
                }
            )

        scored.sort(
            key=lambda x: (
                -x["confidence"],
                x["symbol"].file,
                x["symbol"].line,
                x["symbol"].name,
            )
        )

        # Filter by min_confidence
        if min_confidence > 0:
            scored = [s for s in scored if s["confidence"] >= min_confidence]

        # Tier classification
        high = [s for s in scored if s["confidence"] >= 70]
        medium = [s for s in scored if 40 <= s["confidence"] < 70]
        low = [s for s in scored if s["confidence"] < 40]

        if as_json:

            def _to_dict(item):
                sym = item["symbol"]
                return {
                    "name": sym.name,
                    "kind": sym.kind,
                    "file": sym.file,
                    "line": sym.line,
                    "confidence": item["confidence"],
                    "note": item["note"],
                    "visibility": sym.visibility,
                }

            payload = {
                "project_root": str(engine.project_root),
                "total_candidates": len(candidates),
                "filtered_structural": filtered_structural_count,
                "high_confidence": [_to_dict(s) for s in high],
                "medium_confidence": [_to_dict(s) for s in medium],
                "low_confidence": [_to_dict(s) for s in low],
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0

        # Text output
        lines = ["## Dead Code Analysis\n"]
        lines.append(
            f"Total {len(candidates)} candidates ({filtered_structural_count} structural elements filtered)"
        )
        if min_confidence > 0:
            lines.append(
                f"Confidence threshold: {min_confidence} (low-confidence items filtered)"
            )
        lines.append("")

        _module_for_file = GraphAnalyzer._module_bucket_for_file

        def _render_tier(title: str, emoji: str, items: list[dict], max_items: int):
            if not items:
                return []
            tier_lines = [f"### {emoji} {title} — {len(items)}"]
            # 按模块分组
            by_module: dict[str, list[dict]] = {}
            for item in items:
                mod = _module_for_file(item["symbol"].file)
                by_module.setdefault(mod, []).append(item)
            tier_lines.append("")
            for mod in sorted(by_module, key=lambda m: -len(by_module[m])):
                mod_items = by_module[mod][
                    : max(3, max_items // max(len(by_module), 1))
                ]
                tier_lines.append(f"**`{mod}/`** ({len(by_module[mod])})")
                for item in mod_items:
                    sym = item["symbol"]
                    tier_lines.append(
                        f"- `{sym.name}` ({sym.kind}) `{sym.file}:{sym.line}` — {item['confidence']}% | {item['note']}"
                    )
                if len(by_module[mod]) > len(mod_items):
                    tier_lines.append(
                        f"  ... {len(by_module[mod]) - len(mod_items)} more"
                    )
            tier_lines.append("")
            return tier_lines

        lines.extend(_render_tier("HIGH (review recommended)", "🔴", high, limit))
        lines.extend(_render_tier("MEDIUM (verify needed)", "🟡", medium, limit))
        lines.extend(_render_tier("LOW (likely active)", "🟢", low, limit))

        # 如果过滤后无结果，给出建议
        if not high and not medium and not low:
            if min_confidence > 0:
                lines.append(
                    f"\n> Using `--min-confidence {min_confidence}` filter returned no results."
                )
                lines.append(
                    f"> Try a lower threshold, e.g.: `--min-confidence {max(0, min_confidence - 20)}`"
                )
            else:
                lines.append("\n> No dead code candidates found.")
                lines.append(
                    "> This may indicate good code quality, or analysis parameters need adjustment."
                )
        else:
            if low:
                lines.append(
                    "> Using `--min-confidence 40` filter low-confidence items."
                )
            lines.append(
                "> Do not delete solely based on this output. Verify with `refs` and business review. Use `--json` for structured output."
            )
            lines.append("")
            lines.append("## Pre-deletion checklist\n")
            lines.append(
                "1. Verify each candidate with `refs --project <project> --symbol <name>` or `query-symbol` before deletion."
            )
            lines.append(
                "2. Check for dynamic references: string-based calls, reflection, macro expansions, test fixtures, config-driven dispatch."
            )
            lines.append(
                "3. Check project-specific rules about code ownership, generated code, or feature flags."
            )
            lines.append("4. Run the full test suite after deletion.")
            lines.append(
                "5. Never delete solely from `orphan` output; treat it as a starting point for investigation."
            )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] orphan failed: {exc}", file=sys.stderr)
        return 1


def run_check(
    project: str,
    types: list[str] | None,
    max_issues: int,
    since_commit: str | None,
    modified_files: list[str] | None,
    resolve_symbols: bool,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
    lsp_max_files: int = 20,
) -> int:
    try:
        project_root = _resolve_project(project)
        normalized_modified_files = None
        if modified_files:
            try:
                normalized_modified_files = _normalize_project_relative_paths(
                    project_root, modified_files, must_exist=False
                )
            except ValueError as exc:
                print(
                    f"[{CLI_NAME}] check failed: unsafe modified file: {exc}",
                    file=sys.stderr,
                )
                return 1
        symbols_map = None
        if resolve_symbols:
            engine = _scan_engine(project_root, 8000)
            symbols_map = engine.graph.symbols

        checker = RepoMapChecker(project_root, max_issues)
        result = checker.check(
            types=types,
            resolve_symbols=resolve_symbols and symbols_map is not None,
            symbols_map=symbols_map,
            since_commit=since_commit,
            modified_files=normalized_modified_files,
            with_lsp=with_lsp,
            lsp_timeout=lsp_timeout,
            lsp_max_files=lsp_max_files,
        )
        print(_format_check_report(result, max_issues))
        status = result.get("status")
        if status == "passed":
            return 0
        if status == "warning":
            return 0
        if status == "unknown":
            return EXIT_NO_RESULTS
        return 1
    except Exception as exc:
        print(f"[{CLI_NAME}] check failed: {exc}", file=sys.stderr)
        return 1


def _format_check_report(result: dict[str, Any], max_issues: int) -> str:
    lines = ["## Compiler/Static Analysis Diagnostics\n"]
    lines.append(f"**Project**: `{result['project_root']}`")
    status = result["status"]
    if status == "passed":
        status_label = "✅ Passed"
    elif status == "warning":
        status_label = "⚠️ Warnings"
    elif status == "unknown":
        status_label = (
            "ℹ️ No diagnostic tools ran"
            if result.get("message")
            else "ℹ️ No supported types detected"
        )
    else:
        status_label = "❌ Errors"
    lines.append(f"**Status**: {status_label}")
    if result.get("message"):
        lines.append(f"**Message**: {result['message']}")
    lines.append(f"**Types**: {', '.join(result.get('types', [])) or 'auto-detected'}")
    lines.append(f"**Time**: {result['timestamp']}\n")

    summary = result.get("summary", {})
    lines.append("### Summary")
    lines.append(f"- Total errors: **{summary.get('total_errors', 0)}** 🔴")
    lines.append(f"- Total warnings: **{summary.get('total_warnings', 0)}** ⚠️")
    lines.append(f"- Files with issues: {summary.get('files_with_errors', 0)}")
    lines.append(
        f"- Tools run: {summary.get('tools_run', 0)} |  Skipped: {summary.get('tools_skipped', 0)}"
    )
    if summary.get("tool_failures", 0):
        lines.append(f"- Tool failures: **{summary.get('tool_failures', 0)}**")
    if summary.get("tools_run", 0) == 0 and summary.get("tools_skipped", 0) > 0:
        lines.append(
            "\n⚠️ No diagnostic tool was available; status is unknown, not passed."
        )
    lines.append("")

    runs = result.get("runs", [])
    if runs:
        lines.append("### Tool Execution Details\n")
        for run in runs:
            status = (
                "⏭️ Skipped"
                if run.get("skipped")
                else (
                    "✅ Passed"
                    if run["exit_code"] == 0 and run["error_count"] == 0
                    else "❌ Failed"
                )
            )
            lines.append(f"**{run['tool']}** {status} ({run['duration_ms']}ms)")
            if run.get("skipped"):
                lines.append(f"  - Reason: {run.get('skip_reason', 'unknown')}")
            else:
                lines.append(f"  - Command: `{run['command']}`")
                if run.get("exit_code", 0) != 0:
                    lines.append(f"  - Exit code: {run['exit_code']}")
                if run.get("tool_failure_reason"):
                    lines.append(f"  - Reason: {run['tool_failure_reason']}")
                    excerpt = run.get("raw_excerpt") or []
                    if excerpt:
                        lines.append(f"  - Output: {str(excerpt[0])[:120]}")
                if run["error_count"] > 0:
                    lines.append(f"  - Errors: **{run['error_count']}**")
                if run["warning_count"] > 0:
                    lines.append(f"  - Warnings: {run['warning_count']}")
                if run.get("truncated"):
                    lines.append(
                        f"  - ⚠️ Output truncated; showing first {max_issues} items"
                    )
            lines.append("")

    errors_by_file = result.get("errors_by_file", {})
    if errors_by_file:
        lines.append("### Issues by File (Top 10)\n")
        for file_path, issues in list(errors_by_file.items())[:10]:
            error_count = sum(1 for issue in issues if issue["severity"] == "error")
            warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
            info_count = sum(1 for issue in issues if issue["severity"] == "info")
            counts = []
            if error_count:
                counts.append(f"{error_count} errors")
            if warning_count:
                counts.append(f"{warning_count} warnings")
            if info_count:
                counts.append(f"{info_count} infos")
            lines.append(f"**{file_path}**: {', '.join(counts)}")
            for issue in issues[:3]:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
                    issue["severity"], "❌"
                )
                confidence_icon = {"exact": "🎯", "line": "📍", "none": ""}.get(
                    issue.get("symbol_confidence", "none"), ""
                )
                symbol_info = (
                    f" {confidence_icon}`{issue['symbol']}`"
                    if issue.get("symbol")
                    else ""
                )
                lines.append(
                    f"  {icon} line{issue['line']}{symbol_info}: [{issue['code']}] {issue['message'][:50]}"
                )
            lines.append("")

    return "\n".join(lines)
