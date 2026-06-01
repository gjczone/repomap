from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from ..topic import TestMatch, classify_file_role
from . import _truncate_output


def _render_verify_header(lines: list[str], status: str) -> None:
    status_label = {"passed": "PASS", "warning": "WARNING", "failed": "FAILED"}.get(
        status, status.upper()
    )
    lines.append("## Overall Status\n")
    lines.append(f"**{status_label}**")
    if status == "passed":
        lines.append(
            "- Evidence looks sufficient for final handoff, assuming required project tests were actually run when needed."
        )
    elif status == "warning":
        lines.append(
            "- Do not claim full confidence yet; review the warnings and missing evidence below."
        )
    else:
        lines.append(
            "- Do not claim completion; at least one verification source failed."
        )
    lines.append("")


def _render_verify_changed_files(lines: list[str], result: dict[str, Any]) -> None:
    changed_files = result.get("changedFiles", [])
    lines.append("## Changed Files\n")
    if changed_files:
        for file_path in changed_files[:30]:
            lines.append(f"- `{file_path}` ({classify_file_role(file_path)})")
        if len(changed_files) > 30:
            lines.append(f"- ... {len(changed_files) - 30} more")
    else:
        status = result.get("status", "")
        if status == "warning":
            lines.append(
                "- No git changes detected — verify cannot assess risk without changes."
            )
        else:
            lines.append("- No changed files detected in the project.")
    lines.append("")


def _render_verify_risk(lines: list[str], result: dict[str, Any]) -> None:
    risk = result.get("risk", {})
    lines.append("## Risk Summary\n")
    lines.append(f"- Level: **{str(risk.get('level', 'unknown')).upper()}**")
    for reason in risk.get("reasons", []):
        lines.append(f"- {reason}")
    for missing in risk.get("missingChecks", []):
        lines.append(f"- Missing evidence: {missing}")
    lines.append("")


def _render_verify_tests(lines: list[str], result: dict[str, Any]) -> None:
    tests = result.get("tests", [])
    if tests:
        lines.append("## Suggested Tests\n")
        for command in _test_commands_for_files(
            [
                TestMatch(
                    test_file=item.get("testFile", ""),
                    target_file=item.get("targetFile", ""),
                    confidence=item.get("confidence", ""),
                    reason=item.get("reason", ""),
                )
                for item in tests
            ]
        ):
            lines.append(f"- `{command}`")
        lines.append("")
    else:
        lines.append("## Suggested Tests\n")
        lines.append("- No test files matched for changed files.")
        changed_files = result.get("changedFiles", [])
        if changed_files:
            test_hints: list[str] = []
            for f in changed_files[:3]:
                if f.endswith(".py"):
                    test_hints.append("python -m pytest")
                    break
                elif f.endswith((".ts", ".tsx", ".js", ".jsx")):
                    test_hints.append("npx vitest run")
                    break
                elif f.endswith(".go"):
                    test_hints.append("go test ./...")
                    break
                elif f.endswith(".rs"):
                    test_hints.append("cargo test")
                    break
            if test_hints:
                lines.append(f"- Suggestion: run `{test_hints[0]}`")
            else:
                lines.append("- Suggestion: run the project test suite.")
        lines.append("")


def _render_verify_untested(lines: list[str], result: dict[str, Any]) -> None:
    untested = result.get("untestedSymbols", [])
    if not untested:
        return
    lines.append("## Test Coverage Gaps\n")
    lines.append(
        "> Symbols below lack test coverage. Review carefully before modifying.\n"
    )
    lines.append("| Symbol | Kind | File | Callers | Risk |")
    lines.append("|--------|------|------|:------:|:----:|")
    for item in untested[:15]:
        risk_label = (
            "HIGH"
            if item["risk_score"] >= 10
            else "MEDIUM"
            if item["risk_score"] >= 5
            else "LOW"
        )
        lines.append(
            f"| `{item['symbol']}` | {item['kind']} | `{item['file']}:{item['line']}` "
            f"| {item['incoming_calls']} | {risk_label} |"
        )
    lines.append("")


def _render_verify_check(lines: list[str], result: dict[str, Any]) -> None:
    check = result.get("check", {})
    lines.append("## Check Result\n")
    raw_status = check.get("status", "skipped")
    if raw_status == "unknown":
        raw_status = "skipped"
    lines.append(f"- Status: **{str(raw_status).upper()}**")
    summary = check.get("summary", {})
    if summary:
        lines.append(
            f"- Errors: {summary.get('total_errors', 0)} | Warnings: {summary.get('total_warnings', 0)} | Tool failures: {summary.get('tool_failures', 0)}"
        )
    for run in check.get("runs", [])[:8]:
        marker = "skipped" if run.get("skipped") else f"exit={run.get('exit_code')}"
        lines.append(f"- {run.get('tool')}: {marker}")
    lines.append("")


def _render_verify_lsp(lines: list[str], result: dict[str, Any]) -> None:
    lsp = result.get("lsp", {})
    lines.append("## LSP Diagnostics\n")
    lines.append(f"- Status: **{str(lsp.get('status', 'skipped')).upper()}**")
    if lsp.get("reason"):
        lines.append(f"- Reason: {lsp['reason']}")
    lsp_summary = lsp.get("summary", {})
    if lsp_summary:
        lines.append(
            f"- Errors: {lsp_summary.get('totalErrors', 0)} | Warnings: {lsp_summary.get('totalWarnings', 0)} | Failed runs: {lsp_summary.get('failedRuns', 0)} | Skipped runs: {lsp_summary.get('skippedRuns', 0)}"
        )
    lines.append("")


def _test_commands_for_files(tests: list[TestMatch]) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for t in tests:
        if t.test_file not in seen:
            seen.add(t.test_file)
            if t.test_file.endswith((".ts", ".tsx", ".js", ".jsx")):
                commands.append(f"npx vitest run {t.test_file}")
            elif t.test_file.endswith(".py"):
                commands.append(f"python -m pytest {t.test_file} -v")
            elif t.test_file.endswith(".go"):
                commands.append(f"go test ./{PurePosixPath(t.test_file).parent}")
            elif t.test_file.endswith(".rs"):
                commands.append(f"cargo test -- {t.test_file}")
            else:
                commands.append(f"# run tests in {t.test_file}")
    return commands[:10]


def render_verify_report(payload: dict[str, Any], max_chars: int = 10000) -> str:
    result = payload.get("result", {})
    status = result.get("status", "unknown")
    lines: list[str] = ["# Verify Report\n"]

    _render_verify_header(lines, status)
    _render_verify_changed_files(lines, result)
    _render_verify_risk(lines, result)

    _render_verify_tests(lines, result)
    _render_verify_untested(lines, result)
    _render_verify_check(lines, result)
    _render_verify_lsp(lines, result)

    tests = result.get("tests", [])
    lsp = result.get("lsp", {})
    graph_diff = result.get("graphDiff", {})
    breaking_changes = graph_diff.get("breakingChanges", [])
    if breaking_changes:
        lines.append("## Breaking Changes\n")
        for bc in breaking_changes[:10]:
            risk_icon = {"HIGH": "[HIGH]", "MEDIUM": "[MEDIUM]", "LOW": "[LOW]"}
            lines.append(
                f"- {risk_icon.get(bc.get('risk', 'LOW'), '[LOW]')} "
                f"**{bc['name']}** `({bc.get('kind', '')})` in `{bc['file']}` "
                f"[{bc.get('risk', 'LOW')}]"
            )
            if bc.get("new_signature") and bc.get("old_signature") != bc.get(
                "new_signature"
            ):
                lines.append(f"  - Old: `{bc.get('old_signature', '')}`")
                lines.append(f"  - New: `{bc.get('new_signature', '')}`")
            if bc.get("affected_caller_count", 0) > 0:
                lines.append(f"  - {bc['affected_caller_count']} callers affected")
        lines.append("")

    contract_risks = result.get("contractRisks", [])
    if contract_risks:
        lines.append("## Contract Risk Warnings\n")
        for cr in contract_risks:
            level = cr.get("level", "MED")
            lines.append(f"- {level}: {cr['message']}")
        lines.append("")

    lines.append("## Graph Diff\n")
    lines.append(f"- Status: **{str(graph_diff.get('status', 'skipped')).upper()}**")
    if graph_diff.get("reason"):
        lines.append(f"- Reason: {graph_diff['reason']}")
    if graph_diff.get("summary"):
        summary = graph_diff["summary"]
        lines.append(
            f"- Symbols +{summary.get('added', 0)} / -{summary.get('removed', 0)} / modified {summary.get('modified', 0)}; edges +{summary.get('edges_added', 0)} / -{summary.get('edges_removed', 0)}"
        )
    lines.append("")

    impact_session = result.get("impactSession", {})
    if impact_session:
        imp_status = impact_session.get("status", "skipped")
        if imp_status == "skipped":
            pass
        else:
            lines.append("## Impact Session Check\n")
            age = impact_session.get("sessionAgeSeconds")
            if age is not None and age > 300:
                lines.append("**EXPIRED**")
                lines.append(
                    f"- Impact session expired ({age}s ago), run `repomap impact` first"
                )
            else:
                imp_status_label = {
                    "ok": "PASS",
                    "missed": "WARNING",
                    "no_changes": "NO_CHANGES",
                }.get(imp_status, imp_status.upper())
                lines.append(f"**{imp_status_label}**")
                if age is not None:
                    lines.append(f"- Session age: {age}s")
                missed = impact_session.get("missedFiles", [])
                unexpected = impact_session.get("unexpectedFiles", [])
                covered = impact_session.get("coveredFiles", [])
                if imp_status == "ok":
                    if covered:
                        lines.append(
                            "- All impact-expected affected files are present in the git diff."
                        )
                    else:
                        lines.append(
                            "- Impact predicted no affected files (only targets); coverage check is vacuous."
                        )
                elif imp_status == "missed":
                    lines.append(
                        "- The following impact-expected files are NOT in the git diff (may need review):"
                    )
                    for f in missed[:20]:
                        lines.append(f"  - `{f}`")
                elif imp_status == "no_changes":
                    lines.append(
                        "- No git changes detected; cannot compare against impact expectations."
                    )
                if covered:
                    lines.append(
                        f"- Covered by diff: {', '.join('`' + f + '`' for f in covered[:10])}"
                    )
                if unexpected:
                    lines.append(
                        "- Files in git diff but NOT expected by impact (review if intentional):"
                    )
                    for f in unexpected[:10]:
                        lines.append(f"  - `{f}`")
            lines.append("")

    lines.append("## Final Evidence Checklist\n")

    if status != "passed":
        lines.append("- [ ] Address failed/warning sections above.")
    if tests and status != "passed":
        lines.append("- [ ] Run suggested tests separately.")
    if lsp.get("status") == "skipped":
        lines.append(
            "- [ ] LSP evidence was skipped; ensure an LSP server is installed or use `lsp setup`."
        )
    if graph_diff.get("enabled") and graph_diff.get("status") == "skipped":
        lines.append(
            "- [ ] Graph diff was skipped; use `--with-diff` after `cache save` for contract change evidence."
        )
    if status == "passed":
        lines.append("- [x] No unresolved verification gaps reported by this command.")
    return _truncate_output("\n".join(lines), max_chars)
