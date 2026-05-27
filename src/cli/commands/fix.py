from __future__ import annotations

import glob
import subprocess
import sys

from ..handlers import (
    CLI_NAME,
    _resolve_project,
)
from .verify import run_verify, run_check


def run_fix(project: str, dry_run: bool = False) -> int:
    """Auto-fix: ruff --fix, eslint --fix, etc."""
    try:
        project_root = _resolve_project(project)

        fixes_applied: list[str] = []
        tools_failed: list[str] = []

        # Try ruff
        try:
            result = subprocess.run(
                ["ruff", "check", "--fix", str(project_root)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                fixes_applied.append("ruff --fix")
        except FileNotFoundError:
            tools_failed.append("ruff (not installed)")
        except Exception as exc:
            tools_failed.append(f"ruff (error: {exc})")

        # Try eslint
        try:
            patterns = ["*.js", "*.ts", "*.jsx", "*.tsx"]
            eslint_files: list[str] = []
            for pat in patterns:
                eslint_files.extend(
                    glob.glob(str(project_root) + "/**/" + pat, recursive=True)
                )
            if eslint_files:
                result = subprocess.run(
                    ["eslint", "--fix", "--", *eslint_files],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode == 0:
                    fixes_applied.append("eslint --fix")
        except FileNotFoundError:
            tools_failed.append("eslint (not installed)")
        except Exception as exc:
            tools_failed.append(f"eslint (error: {exc})")

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


def run_ready(project: str) -> int:
    """Quick readiness check: verify --quick + check + ruff format --check."""
    try:
        project_root = _resolve_project(project)

        print("=" * 60)
        print("Ready Check")
        print("=" * 60)

        # 1. Quick verify (risk-only)
        print("\n--- Step 1: verify --quick ---")
        verify_ok = True
        try:
            verify_rc = run_verify(
                project=project_root,
                as_json=False,
                types=None,
                max_issues=50,
                resolve_symbols=True,
                with_lsp=False,
                lsp_timeout=8.0,
                lsp_max_files=20,
                with_diff=False,
                quick=True,
                incremental=False,
            )
            if verify_rc != 0:
                verify_ok = False
        except Exception as exc:
            print(f"  verify skipped: {exc}")
            verify_ok = False

        # 2. Check (compiler/static analysis)
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
                with_lsp=False,
                lsp_timeout=8.0,
                lsp_max_files=20,
            )
            if check_rc != 0:
                check_ok = False
        except Exception as exc:
            print(f"  check skipped: {exc}")
            check_ok = False

        # 3. ruff format --check
        print("\n--- Step 3: ruff format --check ---")
        format_ok: bool | None = True
        try:
            result = subprocess.run(
                ["ruff", "format", "--check", str(project_root)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                print("  Format check passed.")
            else:
                print(
                    f"  Format check failed. Run `ruff format {project_root}` to fix."
                )
                format_ok = False
        except FileNotFoundError:
            print("  ruff not available, skipping format check.")
            format_ok = None
        except Exception:
            print("  ruff error, skipping format check.")
            format_ok = None

        # Summary
        print("\n" + "=" * 60)
        print("Ready Check Summary")
        print("=" * 60)
        all_ok = verify_ok and check_ok and (format_ok is not False)
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
