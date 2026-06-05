from __future__ import annotations

import logging
import subprocess
import sys

from ..handlers import (
    CLI_NAME,
    DEFAULT_LSP_TIMEOUT,
    _resolve_project,
)
from .verify import run_verify, run_check
from ...formatters import (
    detect_all_formatters,
    run_formatter,
)

logger = logging.getLogger("repomap")


def _run_subprocess(
    args: list[str], timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with capture_output=True, text=True, timeout."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def run_fix(project: str, dry_run: bool = False, as_json: bool = False) -> int:
    """Auto-fix: language-aware formatters (ruff, biome, gofmt, cargo fmt, etc.).

    Uses nearest-wins config detection to pick the right formatter per file.
    Before formatting, runs a secrets scan on changed files — if secrets are
    found, fix is rejected to prevent overwriting secret evidence.
    """
    try:
        project_root = _resolve_project(project)

        fixes_applied: list[str] = []
        tools_failed: list[str] = []
        dry_run_actions: list[str] = []
        files_processed = 0

        # 1. Secrets scan (pre-fix guard) — reject fix if secrets detected
        try:
            from ...secrets import scan_diff_secrets

            secrets_result = scan_diff_secrets(project_root)
            if secrets_result.get("findings"):
                count = len(secrets_result["findings"])
                msg = (
                    f"Secrets detected in {count} location(s) — "
                    f"refusing to format to preserve evidence. "
                    f"Run `repomap verify --project .` to see details."
                )
                if as_json:
                    from ..handlers import json_envelope

                    print(
                        json_envelope(
                            "fix",
                            project_root,
                            {
                                "dry_run": dry_run,
                                "fixes_applied": [],
                                "tools_failed": [],
                                "dry_run_actions": [],
                                "secrets_blocked": True,
                                "error": msg,
                            },
                            status="error",
                        )
                    )
                else:
                    print(f"[{CLI_NAME}] {msg}")
                return 1
        except ImportError:
            pass  # secrets module not available yet (graceful degradation)
        except Exception as exc:
            logger.warning("Secrets pre-scan failed (non-fatal): %s", exc)

        # 2. Detect all applicable formatters
        candidates = detect_all_formatters(project_root, dry_run=dry_run)
        if not candidates:
            if as_json:
                from ..handlers import json_envelope

                print(
                    json_envelope(
                        "fix",
                        project_root,
                        {
                            "dry_run": dry_run,
                            "fixes_applied": [],
                            "tools_failed": [],
                            "dry_run_actions": [],
                            "files_processed": 0,
                        },
                    )
                )
                return 0
            print("No formattable files found or no formatters available.")
            return 0

        # 3. Group by tool and run
        tool_groups: dict[str, list[str]] = {}
        for c in candidates:
            tool = c["tool"]
            tool_groups.setdefault(tool, []).append(c["file"])

        for tool, files in tool_groups.items():
            if dry_run:
                dry_run_actions.append(f"{tool} (would format {len(files)} file(s))")
                files_processed += len(files)
                continue

            # Build batch command — some formatters accept multiple files
            try:
                if tool == "ruff":
                    args = ["ruff", "format", *files]
                elif tool == "biome":
                    args = ["biome", "check", "--apply", *files]
                elif tool == "gofmt":
                    # gofmt -w per file (batch-safe)
                    all_ok = True
                    for f in files:
                        result = run_formatter(["gofmt", "-w", f])
                        if not result.success:
                            all_ok = False
                            tools_failed.append(
                                f"gofmt on {f} (exit {result.exit_code})"
                            )
                    if all_ok:
                        fixes_applied.append(f"gofmt ({len(files)} file(s))")
                        files_processed += len(files)
                    continue
                elif tool == "cargo":
                    # cargo fmt works per project, not per file
                    result = run_formatter(["cargo", "fmt"])
                    if result.success:
                        fixes_applied.append(f"cargo fmt ({len(files)} file(s))")
                        files_processed += len(files)
                    else:
                        tools_failed.append(
                            f"cargo fmt (exit {result.exit_code}): {result.stderr[:100]}"
                        )
                    continue
                elif tool in ("prettier", "eslint"):
                    if tool == "prettier":
                        args = ["prettier", "--write", *files]
                    else:
                        args = ["eslint", "--fix", "--", *files]
                else:
                    # Generic: run per-file
                    all_ok = True
                    for f in files:
                        result = run_formatter([tool, f])
                        if not result.success:
                            all_ok = False
                            tools_failed.append(
                                f"{tool} on {f} (exit {result.exit_code})"
                            )
                    if all_ok:
                        fixes_applied.append(f"{tool} ({len(files)} file(s))")
                        files_processed += len(files)
                    continue

                result = run_formatter(args)
                if result.success:
                    fixes_applied.append(f"{tool} ({len(files)} file(s))")
                    files_processed += len(files)
                else:
                    tools_failed.append(
                        f"{tool} (exit {result.exit_code}): {result.stderr[:100]}"
                    )

            except Exception as exc:
                tools_failed.append(f"{tool} (error: {exc})")

        if as_json:
            from ..handlers import json_envelope

            print(
                json_envelope(
                    "fix",
                    project_root,
                    {
                        "dry_run": dry_run,
                        "fixes_applied": fixes_applied,
                        "tools_failed": tools_failed,
                        "dry_run_actions": dry_run_actions,
                        "files_processed": files_processed,
                    },
                )
            )
            return 0
        if dry_run:
            if dry_run_actions:
                print(f"Dry run — would apply: {', '.join(dry_run_actions)}")
            else:
                print("Dry run — no auto-fixable issues found.")
        else:
            if fixes_applied:
                print(f"Applied: {', '.join(fixes_applied)}")
            if tools_failed:
                print(f"Skipped: {', '.join(tools_failed)}")
            if not fixes_applied and not tools_failed:
                print("No auto-fixable issues found.")
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] fix failed: {exc}", file=sys.stderr)
        return 1


def run_ready(project: str, as_json: bool = False) -> int:
    """Quick readiness check: verify --quick + check + ruff format --check."""
    import contextlib
    import io

    try:
        project_root = _resolve_project(project)

        # 当 as_json 时抑制所有子命令的 stdout 输出
        capture = io.StringIO() if as_json else None
        stdout_ctx = (
            contextlib.redirect_stdout(capture) if as_json else contextlib.nullcontext()
        )

        with stdout_ctx:
            if not as_json:
                print("=" * 60)
                print("Ready Check")
                print("=" * 60)

            # 1. Quick verify (risk-only)
            if not as_json:
                print("\n--- Step 1: verify --quick ---")
            verify_ok = True
            try:
                verify_rc = run_verify(
                    project=project_root,
                    as_json=False,
                    types=None,
                    max_issues=50,
                    resolve_symbols=True,
                    lsp_timeout=DEFAULT_LSP_TIMEOUT,
                    lsp_max_files=20,
                    with_diff=False,
                    quick=True,
                    incremental=False,
                )
                if verify_rc != 0:
                    verify_ok = False
            except Exception as exc:
                if not as_json:
                    print(f"  verify skipped: {exc}")
                verify_ok = False

            # 2. Check (compiler/static analysis)
            if not as_json:
                print("\n--- Step 2: check ---")
            check_ok = True
            try:
                check_rc = run_check(
                    project=project_root,
                    types=None,
                    max_issues=50,
                    since_commit=None,
                    modified_files=None,
                    resolve_symbols=True,
                    lsp_timeout=DEFAULT_LSP_TIMEOUT,
                    lsp_max_files=20,
                )
                if check_rc != 0:
                    check_ok = False
            except Exception as exc:
                if not as_json:
                    print(f"  check skipped: {exc}")
                check_ok = False

            # 3. ruff format --check
            if not as_json:
                print("\n--- Step 3: ruff format --check ---")
            format_ok: bool | None = True
            try:
                result = _run_subprocess(
                    ["ruff", "format", "--check", str(project_root)]
                )
                if result.returncode == 0:
                    if not as_json:
                        print("  Format check passed.")
                else:
                    if not as_json:
                        print(
                            f"  Format check failed. Run `ruff format {project_root}` to fix."
                        )
                    format_ok = False
            except FileNotFoundError:
                if not as_json:
                    print("  ruff not available, skipping format check.")
                format_ok = None
            except Exception as exc:
                logger.warning("ruff format check failed: %s", exc)
                format_ok = None

        # Summary
        all_ok = verify_ok and check_ok and (format_ok is not False)
        if as_json:
            from ..handlers import json_envelope

            print(
                json_envelope(
                    "ready",
                    project_root,
                    {
                        "verify": "PASS" if verify_ok else "FAIL",
                        "check": "PASS" if check_ok else "FAIL",
                        "format": "PASS"
                        if format_ok
                        else ("SKIP" if format_ok is None else "FAIL"),
                        "overall": "READY" if all_ok else "NOT READY",
                    },
                    status="ok" if all_ok else "error",
                )
            )
            return 0 if all_ok else 1
        print("\n" + "=" * 60)
        print("Ready Check Summary")
        print("=" * 60)
        print(f"  verify --quick: {'PASS' if verify_ok else 'FAIL'}")
        print(f"  check:         {'PASS' if check_ok else 'FAIL'}")
        if format_ok is None:
            print("  format:        SKIP (ruff not available)")
        else:
            print(f"  format:        {'PASS' if format_ok else 'FAIL'}")
        print(f"\n  Overall: {'READY' if all_ok else 'NOT READY'}")

        return 0 if all_ok else 1
    except Exception as exc:
        print(f"[{CLI_NAME}] ready failed: {exc}", file=sys.stderr)
        return 1
