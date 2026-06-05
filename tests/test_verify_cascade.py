"""Tests for verify cascade impact output."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestVerifyCascade(unittest.TestCase):
    """Tests for _verify_cascade helper function."""

    def setUp(self):
        from src.cli.commands.verify import _verify_cascade

        self._verify_cascade = _verify_cascade

    def test_cascade_returns_empty_for_no_changes(self) -> None:
        """Cascade with no changed files should return empty result."""
        result = self._verify_cascade([], MagicMock(), depth=2, top_n=10)
        self.assertEqual(result["cascadeDepth"], 2)
        self.assertEqual(result["topN"], 10)
        self.assertEqual(len(result["calls"]), 0)

    def test_cascade_returns_structure(self) -> None:
        """Cascade output should have correct structure."""
        mock_engine = MagicMock()
        mock_engine.graph = MagicMock()
        mock_engine.graph.file_symbols = {}
        mock_engine.graph.symbols = {}
        mock_engine.graph.incoming = {}
        mock_engine.graph.outgoing = {}

        result = self._verify_cascade(
            ["src/main.py"], mock_engine, depth=2, top_n=10
        )
        self.assertIn("cascadeDepth", result)
        self.assertIn("topN", result)
        self.assertIn("calls", result)
        self.assertEqual(result["cascadeDepth"], 2)
        self.assertEqual(result["topN"], 10)
        self.assertEqual(len(result["calls"]), 0)

    def test_cascade_depth_clamped(self) -> None:
        """Cascade depth should be clamped to valid range."""
        mock_engine = MagicMock()
        mock_engine.graph = MagicMock()
        mock_engine.graph.file_symbols = {}
        mock_engine.graph.symbols = {}
        mock_engine.graph.incoming = {}
        mock_engine.graph.outgoing = {}

        # Depth 0 should be clamped to 1
        result = self._verify_cascade(
            ["src/main.py"], mock_engine, depth=0, top_n=10
        )
        self.assertGreaterEqual(result["cascadeDepth"], 1)

        # Depth > 5 should be clamped to 5
        result = self._verify_cascade(
            ["src/main.py"], mock_engine, depth=10, top_n=10
        )
        self.assertLessEqual(result["cascadeDepth"], 5)

    def test_cascade_finds_callers_of_changed_symbols(self) -> None:
        """Cascade should find callers of symbols in changed files."""
        mock_engine = MagicMock()

        # Mock symbols and graph structure
        sid_main = "main.py::func_a"
        sid_caller = "caller.py::func_b"
        sid_caller2 = "caller2.py::func_c"

        mock_engine.graph.file_symbols = {
            "src/main.py": [sid_main],
            "src/caller.py": [sid_caller],
            "src/caller2.py": [sid_caller2],
        }

        mock_engine.graph.symbols = {
            sid_main: MagicMock(
                name="func_a",
                kind="function",
                file="src/main.py",
                line=10,
                visibility="public",
            ),
            sid_caller: MagicMock(
                name="func_b",
                kind="function",
                file="src/caller.py",
                line=5,
                visibility="public",
            ),
            sid_caller2: MagicMock(
                name="func_c",
                kind="function",
                file="src/caller2.py",
                line=3,
                visibility="private",
            ),
        }

        # incoming edges: func_a has callers
        mock_edge1 = MagicMock()
        mock_edge1.source = sid_caller
        mock_edge1.target = sid_main
        mock_edge1.kind = "call"

        mock_edge2 = MagicMock()
        mock_edge2.source = sid_caller2
        mock_edge2.target = sid_main
        mock_edge2.kind = "call"

        mock_engine.graph.incoming = {
            sid_main: [mock_edge1, mock_edge2],
            sid_caller: [],
            sid_caller2: [],
        }
        mock_engine.graph.outgoing = {
            sid_main: [],
            sid_caller: [mock_edge1],
            sid_caller2: [mock_edge2],
        }

        result = self._verify_cascade(
            ["src/main.py"], mock_engine, depth=2, top_n=10
        )
        self.assertGreaterEqual(len(result["calls"]), 1)
        # Should contain caller entries
        caller_files = {c["callerFile"] for c in result["calls"]}
        self.assertIn("src/caller.py", caller_files)


class TestVerifyCascadeInPayload(unittest.TestCase):
    """Integration test: cascade section in verify JSON payload."""

    def test_verify_json_includes_cascade_section(self) -> None:
        """verify --json output should include a 'cascade' section."""
        from src.cli.commands.verify import run_verify

        def fake_git_toplevel(self):
            return project_root

        def fake_git_status(self):
            return [" M main.py"]

        def fake_check(self, **kwargs):
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "project_root": "/tmp/repo",
                "status": "passed",
                "types": ["python"],
                "runs": [],
                "summary": {
                    "total_errors": 0,
                    "total_warnings": 0,
                    "files_with_errors": 0,
                    "tools_run": 0,
                    "tools_skipped": 0,
                    "tool_failures": 0,
                },
                "errors_by_file": {},
            }

        def fake_lsp(project_root, changed_files, timeout, max_files):
            return {"enabled": True, "status": "skipped", "runs": [], "summary": {}}

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def target():\n    return 1\n")
            import io

            stdout = io.StringIO()
            with (
                patch(
                    "src.git_backend.GitBackend.show_toplevel",
                    fake_git_toplevel,
                ),
                patch(
                    "src.git_backend.GitBackend.status_porcelain",
                    fake_git_status,
                ),
                patch(
                    "src.cli.commands.verify._run_check_payload",
                    fake_check,
                ),
                patch(
                    "src.cli.commands.verify._verify_lsp_payload",
                    fake_lsp,
                ),
                patch("sys.stdout", stdout),
            ):
                rc = run_verify(
                    project=project_root,
                    as_json=True,
                    quick=True,
                )

            stdout.seek(0)
            payload = json.loads(stdout.getvalue())
            result = payload.get("result", payload)
            self.assertIn("cascade", result)
            cascade = result["cascade"]
            self.assertIn("cascadeDepth", cascade)
            self.assertIn("calls", cascade)


class TestVerifyCLICascadeArgs(unittest.TestCase):
    """Test that --no-cascade and --cascade-depth are accepted by CLI."""

    def test_no_cascade_arg_accepted(self) -> None:
        """--no-cascade should be a valid CLI argument."""
        from src.cli.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["verify", "--project", "/tmp/test", "--no-cascade", "--json"]
        )
        self.assertTrue(args.no_cascade)

    def test_cascade_depth_arg_accepted(self) -> None:
        """--cascade-depth should be a valid CLI argument."""
        from src.cli.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["verify", "--project", "/tmp/test", "--cascade-depth", "3", "--json"]
        )
        self.assertEqual(args.cascade_depth, 3)

    def test_cascade_depth_default(self) -> None:
        """--cascade-depth should default to 2."""
        from src.cli.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["verify", "--project", "/tmp/test", "--json"]
        )
        self.assertEqual(args.cascade_depth, 2)


if __name__ == "__main__":
    unittest.main()
