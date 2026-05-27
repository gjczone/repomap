"""Issue #60 regression tests — JSON output consistency.

P0-1: run_ready --json emits pure JSON (no mixed text+JSON)
P0-2: json_envelope status reflects actual state (not always "ok")
P0-3: json_envelope normalizes project path to absolute
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestJsonEnvelopeNormalization(unittest.TestCase):
    """P0-3: json_envelope MUST normalize project to absolute path."""

    def test_relative_path_becomes_absolute(self) -> None:
        from src.cli.handlers import json_envelope

        result = json_envelope("test", ".", {})
        data = json.loads(result)
        self.assertEqual(data["project"], os.path.abspath("."))

    def test_already_absolute_path_preserved(self) -> None:
        from src.cli.handlers import json_envelope

        result = json_envelope("test", "/abs/path", {})
        data = json.loads(result)
        self.assertEqual(data["project"], "/abs/path")

    def test_home_tilde_expanded(self) -> None:
        from src.cli.handlers import json_envelope

        result = json_envelope("test", "~/test_project", {})
        data = json.loads(result)
        self.assertTrue(data["project"].startswith(str(Path.home())))
        self.assertFalse("~" in data["project"])

    def test_schema_structure(self) -> None:
        from src.cli.handlers import json_envelope

        result = json_envelope("ready", "/tmp/test", {"a": 1}, status="error")
        data = json.loads(result)
        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["command"], "ready")
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["result"], {"a": 1})


class TestRunReadyJsonOutput(unittest.TestCase):
    """P0-1: run_ready --json MUST emit exactly one valid JSON object."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = self.tmpdir.name
        # Create a minimal .py file so the project dir is valid
        (Path(self.project) / "dummy.py").write_text("# test\n")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_json_no_mixed_output_all_pass(self) -> None:
        """When all steps pass, stdout is valid JSON with status=ok."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", return_value=0),
            patch("src.cli.commands.fix.run_check", return_value=0),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = run_ready(self.project, as_json=True)

            output = buf.getvalue().strip()
            # Must be valid JSON
            data = json.loads(output)
            self.assertEqual(data["command"], "ready")
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["result"]["overall"], "READY")
            self.assertEqual(data["result"]["verify"], "PASS")
            self.assertEqual(data["result"]["check"], "PASS")
            self.assertEqual(data["result"]["format"], "PASS")
            self.assertEqual(rc, 0)

    def test_json_no_mixed_output_verify_fails(self) -> None:
        """When verify fails, status=error, overall=NOT READY."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", return_value=1),
            patch("src.cli.commands.fix.run_check", return_value=0),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = run_ready(self.project, as_json=True)

            output = buf.getvalue().strip()
            data = json.loads(output)
            self.assertEqual(data["command"], "ready")
            self.assertEqual(data["status"], "error")
            self.assertEqual(data["result"]["overall"], "NOT READY")
            self.assertEqual(data["result"]["verify"], "FAIL")
            self.assertEqual(data["result"]["check"], "PASS")
            self.assertEqual(data["result"]["format"], "PASS")
            self.assertEqual(rc, 1)

    def test_json_no_mixed_output_check_fails(self) -> None:
        """When check fails, status=error, overall=NOT READY."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", return_value=0),
            patch("src.cli.commands.fix.run_check", return_value=1),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = run_ready(self.project, as_json=True)

            output = buf.getvalue().strip()
            data = json.loads(output)
            self.assertEqual(data["status"], "error")
            self.assertEqual(data["result"]["overall"], "NOT READY")
            self.assertEqual(data["result"]["check"], "FAIL")
            self.assertEqual(rc, 1)

    def test_json_no_mixed_output_format_fails(self) -> None:
        """When ruff format check fails, status=error."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", return_value=0),
            patch("src.cli.commands.fix.run_check", return_value=0),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1)

            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = run_ready(self.project, as_json=True)

            output = buf.getvalue().strip()
            data = json.loads(output)
            self.assertEqual(data["status"], "error")
            self.assertEqual(data["result"]["format"], "FAIL")
            self.assertEqual(data["result"]["overall"], "NOT READY")
            self.assertEqual(rc, 1)

    def test_json_output_is_single_line_json(self) -> None:
        """Output should parse as valid JSON — no text before or after."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", return_value=0),
            patch("src.cli.commands.fix.run_check", return_value=0),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            buf = StringIO()
            with patch("sys.stdout", buf):
                run_ready(self.project, as_json=True)

            output = buf.getvalue().strip()
            # Must be parseable as JSON
            data = json.loads(output)
            self.assertIsInstance(data, dict)
            # Must not contain non-JSON text snippets
            self.assertNotIn("--- Step", output)

    def test_json_project_path_is_absolute(self) -> None:
        """project field in JSON output must be absolute path."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", return_value=0),
            patch("src.cli.commands.fix.run_check", return_value=0),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            buf = StringIO()
            with patch("sys.stdout", buf):
                run_ready(self.project, as_json=True)

            output = buf.getvalue().strip()
            data = json.loads(output)
            self.assertTrue(os.path.isabs(data["project"]))

    def test_json_verify_exception(self) -> None:
        """When run_verify raises, status=error."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", side_effect=RuntimeError("boom")),
            patch("src.cli.commands.fix.run_check", return_value=0),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = run_ready(self.project, as_json=True)

            output = buf.getvalue().strip()
            data = json.loads(output)
            self.assertEqual(data["status"], "error")
            self.assertEqual(data["result"]["verify"], "FAIL")
            self.assertEqual(rc, 1)

    def test_non_json_output_is_clean(self) -> None:
        """When as_json=False, human-readable output is produced."""
        from io import StringIO
        from src.cli.commands.fix import run_ready

        with (
            patch("src.cli.commands.fix.run_verify", return_value=0),
            patch("src.cli.commands.fix.run_check", return_value=0),
            patch("src.cli.commands.fix.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = run_ready(self.project, as_json=False)

            output = buf.getvalue()
            self.assertIn("Ready Check", output)
            self.assertIn("Ready Check Summary", output)
            self.assertIn("READY", output)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
