import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class RepoMapCliTests(unittest.TestCase):
    def test_script_entrypoint_runs_without_package_context(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "repomap_cli/__main__.py", "doctor"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("tree-sitter parsers", result.stdout)

    def test_help_lists_former_mcp_commands_and_excludes_mcp(self) -> None:
        from repomap_cli import main

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["--help"])

        self.assertEqual(exit_code, 0)
        help_text = stdout.getvalue()
        for command in (
            "scan",
            "overview",
            "call-chain",
            "query-symbol",
            "file-detail",
            "hotspots",
            "cache",
            "diff",
            "git-history",
            "refs",
            "orphan",
            "check",
            "doctor",
            "build-binary",
        ):
            self.assertIn(command, help_text)
        self.assertNotIn("mcp", help_text)

    def test_overview_and_query_symbol_run_without_stateful_scan_server(self) -> None:
        from repomap_cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "main.py",
                "from lib import helper\n\ndef caller():\n    return helper()\n",
            )

            overview_stdout = io.StringIO()
            with redirect_stdout(overview_stdout), redirect_stderr(io.StringIO()):
                overview_code = main(["overview", "--project", project_root])

            symbol_stdout = io.StringIO()
            with redirect_stdout(symbol_stdout), redirect_stderr(io.StringIO()):
                symbol_code = main(["query-symbol", "--project", project_root, "--symbol", "helper"])

            chain_stdout = io.StringIO()
            with redirect_stdout(chain_stdout), redirect_stderr(io.StringIO()):
                chain_code = main(["call-chain", "--project", project_root, "--symbol", "helper"])

            self.assertEqual(overview_code, 0)
            self.assertEqual(symbol_code, 0)
            self.assertEqual(chain_code, 0)
            self.assertIn("# 项目地图", overview_stdout.getvalue())
            self.assertIn("helper", symbol_stdout.getvalue())
            self.assertIn("caller", chain_stdout.getvalue())

    def test_cache_save_and_diff_follow_standalone_cli_semantics(self) -> None:
        from repomap_cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def keep():\n    return 1\n")

            cache_stdout = io.StringIO()
            with redirect_stdout(cache_stdout), redirect_stderr(io.StringIO()):
                cache_code = main(["cache", "save", "--project", project_root])

            write_file(
                project_root,
                "main.py",
                "def keep():\n    return 1\n\ndef added():\n    return keep()\n",
            )

            diff_stdout = io.StringIO()
            with redirect_stdout(diff_stdout), redirect_stderr(io.StringIO()):
                diff_code = main(["diff", "--project", project_root])

            self.assertEqual(cache_code, 0)
            self.assertEqual(diff_code, 0)
            self.assertIn("缓存已保存", cache_stdout.getvalue())
            self.assertIn("新增符号: 1", diff_stdout.getvalue())

    def test_build_binary_invokes_pyinstaller_onefile_for_repomap_binary(self) -> None:
        from repomap_cli import main

        with tempfile.TemporaryDirectory() as output_dir:
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("repomap_cli.cli.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(["build-binary", "--output", output_dir])

            self.assertEqual(exit_code, 0)
            command = run_mock.call_args.args[0]
            self.assertIn("--onefile", command)
            self.assertIn("--name", command)
            self.assertIn("repomap", command)


if __name__ == "__main__":
    unittest.main()
