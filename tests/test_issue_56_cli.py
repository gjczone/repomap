"""Issue #56 regression tests — CLI/output fixes for #49, #50, #36, #41.

Tests verify:
  - #49: json_envelope() produces {schema_version, command, project, status, result}
  - #50: save_impact_session / load_impact_session roundtrip
  - #36: run_verify returns EXIT_NO_RESULTS (3) when no git changes
  - #41: run_query / run_impact accept --max-files parameter
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── helpers for git-based tests ──────────────────────────────────────────────


def _run_git(args, cwd):
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _init_minimal_git_repo(root: Path) -> None:
    """Create a tiny git repo with one Python file, fully committed."""
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", "-b", "main"], str(root))
    (root / ".gitignore").write_text(".repomap/\n")
    src = root / "src"
    src.mkdir()
    (src / "main.py").write_text(
        textwrap.dedent("""\
        def hello():
            return "world"
    """)
    )
    _run_git(["add", "."], str(root))
    _run_git(["commit", "-q", "-m", "init"], str(root))


# ── #49: json_envelope produces unified envelope ─────────────────────────────


class TestJsonEnvelope(unittest.TestCase):
    """Verify json_envelope() produces the correct {schema_version, command,
    project, status, result} structure for all commands."""

    def test_envelope_structure_default_status(self) -> None:
        from src.cli.handlers import json_envelope

        output = json_envelope("overview", "/tmp/test-proj", {"foo": "bar"})
        parsed = json.loads(output)

        self.assertEqual(parsed["schema_version"], "1.0")
        self.assertEqual(parsed["command"], "overview")
        self.assertEqual(parsed["project"], "/tmp/test-proj")
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["result"], {"foo": "bar"})

        # verify no extra top-level keys
        self.assertCountEqual(
            parsed.keys(),
            {"schema_version", "command", "project", "status", "result"},
        )

    def test_envelope_error_status(self) -> None:
        from src.cli.handlers import json_envelope

        output = json_envelope("impact", "/a/b", {"error": "bad"}, status="error")
        parsed = json.loads(output)

        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["command"], "impact")
        self.assertEqual(parsed["result"], {"error": "bad"})

    def test_envelope_multiple_commands(self) -> None:
        from src.cli.handlers import json_envelope

        for cmd in ("overview", "impact", "verify"):
            output = json_envelope(cmd, ".", {"x": 1})
            parsed = json.loads(output)
            self.assertEqual(parsed["command"], cmd, f"command field should be '{cmd}'")
            self.assertEqual(parsed["schema_version"], "1.0")
            self.assertEqual(parsed["status"], "ok")
            self.assertIn("result", parsed)

    def test_envelope_project_is_string(self) -> None:
        from src.cli.handlers import json_envelope

        # Path objects are converted to string
        output = json_envelope("scan", str(Path("/some/project")), {})
        parsed = json.loads(output)
        self.assertIsInstance(parsed["project"], str)


# ── #50: session write/read/compare ──────────────────────────────────────────


class TestSessionRoundtrip(unittest.TestCase):
    """Verify save_impact_session and load_impact_session roundtrip correctly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_and_load_roundtrip(self) -> None:
        from src.cli.handlers import save_impact_session, load_impact_session

        target = ["src/main.py", "src/util.py"]
        affected = ["tests/test_main.py", "src/helper.py"]
        key_symbols = [
            {"name": "hello", "file": "src/main.py", "line": 1},
            {"name": "helper", "file": "src/helper.py", "line": 5},
        ]
        suggested_tests = ["tests/test_main.py"]

        session_path = save_impact_session(
            self.root, target, affected, key_symbols, suggested_tests
        )

        self.assertTrue(session_path.exists(), "session file must exist")
        self.assertEqual(session_path.parent.name, ".repomap")

        payload = load_impact_session(self.root)
        self.assertIsNotNone(payload, "load_impact_session must return data")
        assert payload is not None

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertIn("created_at", payload)
        self.assertIn("impact", payload)

        impact = payload["impact"]
        self.assertEqual(impact["target_files"], target)
        self.assertEqual(impact["affected_files"], affected)
        self.assertEqual(impact["key_symbols"], key_symbols)
        self.assertEqual(impact["suggested_tests"], suggested_tests)

    def test_load_nonexistent_session(self) -> None:
        from src.cli.handlers import load_impact_session

        empty = Path(self._tmp.name) / "no-such-dir"
        result = load_impact_session(empty)
        self.assertIsNone(result)

    def test_save_rejects_path_traversal(self) -> None:
        from src.cli.handlers import save_impact_session

        with self.assertRaises(ValueError):
            save_impact_session(
                self.root,
                target_files=["../etc/passwd"],
                affected_files=["src/x.py"],
                key_symbols=[],
                suggested_tests=[],
            )


# ── #36: verify WARNING exit code 3 (no git changes) ────────────────────────


class TestVerifyNoChangesExitCode(unittest.TestCase):
    """Verify that run_verify returns EXIT_NO_RESULTS (3) when no git changes
    are detected in a clean repo."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        _init_minimal_git_repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_changes_returns_exit_3(self) -> None:
        from src.cli.handlers import EXIT_NO_RESULTS
        from src.cli.commands.verify import run_verify

        self.assertEqual(EXIT_NO_RESULTS, 3, "EXIT_NO_RESULTS must be 3")

        # Run verify on a clean repo — no uncommitted changes
        exit_code = run_verify(
            project=str(self.root),
            as_json=True,
            types=None,
            max_issues=20,
            resolve_symbols=False,
            with_lsp=False,
            lsp_timeout=30,
            lsp_max_files=20,
            with_diff=False,
            quick=True,
            incremental=False,
            max_chars=16000,
        )
        self.assertEqual(
            exit_code,
            3,
            f"run_verify on clean repo must return EXIT_NO_RESULTS (3), got {exit_code}",
        )


# ── #41: --max-files parameter ───────────────────────────────────────────────


class TestMaxFilesParameter(unittest.TestCase):
    """Verify that run_query and run_impact accept and pass through the
    max_files parameter in their function signatures."""

    def test_run_query_accepts_max_files(self) -> None:
        from inspect import signature
        from src.cli.commands.query import run_query

        sig = signature(run_query)
        params = list(sig.parameters.keys())
        self.assertIn("max_files", params, "run_query must accept max_files parameter")

    def test_run_impact_accepts_max_files(self) -> None:
        from inspect import signature
        from src.cli.commands.impact import run_impact

        sig = signature(run_impact)
        params = list(sig.parameters.keys())
        self.assertIn("max_files", params, "run_impact must accept max_files parameter")


if __name__ == "__main__":
    unittest.main()
