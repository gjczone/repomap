"""Tests for issue #50: impact -> verify session linkage."""

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


def _init_git_project(root):
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", "-b", "main"], str(root))
    (root / ".gitignore").write_text(".repomap/\n")
    src = root / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        textwrap.dedent("""
        def greet(name):
            return f"hello {name}"

        def call_greet():
            return greet("world")
    """).lstrip()
    )
    (src / "util.py").write_text(
        textwrap.dedent("""
        def helper():
            return 42
    """).lstrip()
    )
    _run_git(["add", "."], str(root))
    _run_git(["commit", "-q", "-m", "init"], str(root))


_REPO_ROOT = str(Path(__file__).resolve().parents[1])


class ImpactSessionRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        _init_git_project(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_cli(self, args):
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_impact_writes_session_file(self):
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/mod.py",
                "--json",
            ]
        )
        self.assertIn(
            r.returncode, (0, 3), r.stderr
        )  # 0=passed or 3=warning(no_changes/skipped)
        session_path = self.root / ".repomap" / "session.json"
        self.assertTrue(session_path.exists(), "session.json must be written")
        payload = json.loads(session_path.read_text())
        self.assertEqual(payload.get("schema_version"), "1.0")
        self.assertIn("impact", payload)
        self.assertIn("src/mod.py", payload["impact"]["target_files"])
        self.assertIsInstance(payload["impact"]["affected_files"], list)
        self.assertIsInstance(payload["impact"]["key_symbols"], list)
        self.assertIsInstance(payload["impact"]["suggested_tests"], list)
        self.assertIn("created_at", payload)

    def test_verify_reports_missed_affected_files(self):
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/mod.py",
                "--json",
            ]
        )
        self.assertIn(
            r.returncode, (0, 3), r.stderr
        )  # 0=passed or 3=warning(no_changes/skipped)

        session_path = self.root / ".repomap" / "session.json"
        payload = json.loads(session_path.read_text())
        payload["impact"]["affected_files"] = ["src/util.py"]
        session_path.write_text(json.dumps(payload))

        mod_file = self.root / "src" / "mod.py"
        mod_file.write_text(mod_file.read_text() + "\n# changed\n")
        _run_git(["add", "src/mod.py"], str(self.root))

        r = self._run_cli(["verify", "--project", str(self.root), "--json"])
        self.assertIn("impactSession", r.stdout, r.stderr)
        verify_payload = json.loads(r.stdout)
        impact_session = verify_payload["result"].get("impactSession", {})
        self.assertEqual(impact_session.get("status"), "missed")
        self.assertIn("src/util.py", impact_session.get("missedFiles", []))

    def test_verify_skips_when_session_absent(self):
        mod_file = self.root / "src" / "mod.py"
        mod_file.write_text(mod_file.read_text() + "\n# tweak\n")
        _run_git(["add", "src/mod.py"], str(self.root))

        r = self._run_cli(["verify", "--project", str(self.root), "--json"])
        verify_payload = json.loads(r.stdout)
        impact_session = verify_payload["result"].get("impactSession", {})
        self.assertEqual(impact_session.get("status"), "skipped")

    def test_verify_no_changes_reports_status(self):
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/mod.py",
                "--json",
            ]
        )
        self.assertIn(
            r.returncode, (0, 3), r.stderr
        )  # 0=passed or 3=warning(no_changes/skipped)

        r = self._run_cli(["verify", "--project", str(self.root), "--json", "--quick"])
        verify_payload = json.loads(r.stdout)
        impact_session = verify_payload["result"].get("impactSession", {})
        self.assertEqual(impact_session.get("status"), "no_changes")

    def test_verify_passes_when_all_affected_covered(self):
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/mod.py",
                "--json",
            ]
        )
        self.assertIn(
            r.returncode, (0, 3), r.stderr
        )  # 0=passed or 3=warning(no_changes/skipped)
        session_path = self.root / ".repomap" / "session.json"
        payload = json.loads(session_path.read_text())
        payload["impact"]["affected_files"] = ["src/util.py"]
        session_path.write_text(json.dumps(payload))

        (self.root / "src" / "mod.py").write_text(
            (self.root / "src" / "mod.py").read_text() + "\n# target change\n"
        )
        (self.root / "src" / "util.py").write_text(
            (self.root / "src" / "util.py").read_text() + "\n# affected change\n"
        )
        _run_git(["add", "."], str(self.root))

        r = self._run_cli(["verify", "--project", str(self.root), "--json"])
        verify_payload = json.loads(r.stdout)
        impact_session = verify_payload["result"].get("impactSession", {})
        self.assertEqual(impact_session.get("status"), "ok")
        self.assertIn("src/util.py", impact_session.get("coveredFiles", []))


class ImpactSessionPathSafetyTests(unittest.TestCase):
    def test_save_session_rejects_traversal(self):
        from src.cli.handlers import save_impact_session

        with self.assertRaises(ValueError):
            save_impact_session(
                project_root="/tmp/fake-proj",
                target_files=["../escape.py"],
                affected_files=[],
                key_symbols=[],
                suggested_tests=[],
            )


if __name__ == "__main__":
    unittest.main()


class ImpactSessionRobustnessTests(unittest.TestCase):
    """Defensive: malformed session must not crash verify."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        _init_git_project(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_cli(self, args):
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_verify_skips_when_session_has_nonstring_elements(self):
        """A session with non-string list elements must yield status=skipped, not crash."""
        # Run impact to create a valid session, then corrupt it.
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/mod.py",
                "--json",
            ]
        )
        self.assertIn(
            r.returncode, (0, 3), r.stderr
        )  # 0=passed or 3=warning(no_changes/skipped)

        session_path = self.root / ".repomap" / "session.json"
        payload = json.loads(session_path.read_text())
        payload["impact"]["target_files"] = [{"not": "a string"}]
        session_path.write_text(json.dumps(payload))

        # Trigger git diff so verify runs the comparison path.
        mod_file = self.root / "src" / "mod.py"
        mod_file.write_text(mod_file.read_text() + "\n# tweak\n")
        _run_git(["add", "src/mod.py"], str(self.root))

        r = self._run_cli(["verify", "--project", str(self.root), "--json"])
        self.assertIn(
            r.returncode, (0, 3), r.stderr
        )  # 0=passed or 3=warning(no_changes/skipped)
        verify_payload = json.loads(r.stdout)
        impact_session = verify_payload["result"].get("impactSession", {})
        self.assertEqual(
            impact_session.get("status"),
            "skipped",
            f"expected skipped on malformed session, got {impact_session}",
        )
