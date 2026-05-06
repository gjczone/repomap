import io
import json
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


from src.check import RepoMapChecker


class RepoMapCliTests(unittest.TestCase):
    def test_javascript_without_eslint_config_skips_eslint(self) -> None:
        from src.check import RepoMapChecker

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "ui_evaluate.js", "console.log('ok')\n")
            report = RepoMapChecker(project_root, max_items=10).check(types=["javascript"], resolve_symbols=False)

        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["summary"]["tools_run"], 0)
        self.assertEqual(report["summary"]["tools_skipped"], 1)
        self.assertEqual(report["runs"][0]["tool"], "eslint")
        self.assertEqual(report["runs"][0]["skip_reason"], "eslint config not found")

    def test_verify_json_outputs_post_edit_evidence(self) -> None:
        from src.cli import main

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{project_root}\n", stderr="")
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M main.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_check(self, **kwargs):
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "project_root": "/tmp/repo",
                "status": "passed",
                "types": ["python"],
                "incremental": {"enabled": True, "files_checked": kwargs.get("modified_files") or [], "files_count": 1},
                "runs": [],
                "summary": {"total_errors": 0, "total_warnings": 0, "files_with_errors": 0, "tools_run": 0, "tools_skipped": 0, "tool_failures": 0},
                "errors_by_file": {},
            }

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def target():\n    return 1\n")
            stdout = io.StringIO()
            with patch("src.cli.cli.subprocess.run", side_effect=fake_run):
                with patch.object(RepoMapChecker, "check", fake_check):
                    with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                        exit_code = main(["verify", "--project", project_root, "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual(payload["command"], "verify")
        self.assertEqual(result["changedFiles"], ["main.py"])
        self.assertEqual(result["check"]["status"], "passed")
        self.assertIn(result["status"], {"passed", "warning"})

    def test_verify_returns_nonzero_when_check_fails(self) -> None:
        from src.cli import main

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{project_root}\n", stderr="")
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M main.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_check(self, **kwargs):
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "project_root": "/tmp/repo",
                "status": "failed",
                "types": ["python"],
                "incremental": {"enabled": True, "files_checked": ["main.py"], "files_count": 1},
                "runs": [{"tool": "pytest", "exit_code": 1, "skipped": False, "error_count": 1, "warning_count": 0}],
                "summary": {"total_errors": 1, "total_warnings": 0, "files_with_errors": 1, "tools_run": 1, "tools_skipped": 0, "tool_failures": 1},
                "errors_by_file": {"main.py": []},
            }

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def target():\n    return 1\n")
            stdout = io.StringIO()
            with patch("src.cli.cli.subprocess.run", side_effect=fake_run):
                with patch.object(RepoMapChecker, "check", fake_check):
                    with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                        exit_code = main(["verify", "--project", project_root, "--json"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["result"]["status"], "failed")

    def test_verify_with_diff_without_cache_is_nonfatal(self) -> None:
        from src.cli import main

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{project_root}\n", stderr="")
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M main.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_check(self, **kwargs):
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "project_root": "/tmp/repo",
                "status": "passed",
                "types": ["python"],
                "incremental": {"enabled": True, "files_checked": ["main.py"], "files_count": 1},
                "runs": [],
                "summary": {"total_errors": 0, "total_warnings": 0, "files_with_errors": 0, "tools_run": 0, "tools_skipped": 0, "tool_failures": 0},
                "errors_by_file": {},
            }

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def target():\n    return 1\n")
            stdout = io.StringIO()
            with patch("src.cli.cli.subprocess.run", side_effect=fake_run):
                with patch("src.cli.cli.diff_project", return_value={"error": "没有缓存，请先运行 cache --save"}):
                    with patch.object(RepoMapChecker, "check", fake_check):
                        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                            exit_code = main(["verify", "--project", project_root, "--with-diff", "--json"])

        self.assertEqual(exit_code, 0)
        graph_diff = json.loads(stdout.getvalue())["result"]["graphDiff"]
        self.assertTrue(graph_diff["enabled"])
        self.assertEqual(graph_diff["status"], "skipped")

    def test_check_marks_nonzero_tool_exit_as_failed_even_without_parsed_issues(self) -> None:
        from src.cli import main

        def fake_check(self, types=None, resolve_symbols=True, symbols_map=None, since_commit=None, modified_files=None, **kwargs):
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "project_root": "/tmp/demo",
                "status": "failed",
                "types": ["javascript"],
                "incremental": {"enabled": False, "files_checked": [], "files_count": 0},
                "runs": [
                    {
                        "tool": "eslint",
                        "command": "npx eslint . --format json",
                        "exit_code": 2,
                        "duration_ms": 12,
                        "skipped": False,
                        "error_count": 0,
                        "warning_count": 0,
                        "truncated": False,
                        "tool_failure_reason": "工具退出码非 0，但未解析到结构化错误",
                        "raw_excerpt": ["ESLint couldn't find an eslint.config file"],
                    }
                ],
                "summary": {
                    "total_errors": 0,
                    "total_warnings": 0,
                    "files_with_errors": 0,
                    "tools_run": 1,
                    "tools_skipped": 0,
                    "tool_failures": 1,
                },
                "errors_by_file": {},
            }

        stdout = io.StringIO()
        with patch.object(RepoMapChecker, "check", fake_check):
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["check", "--project", ".", "--no-symbols"])

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("❌ 有错误", output)
        self.assertIn("退出码: 2", output)
        self.assertIn("未解析到结构化错误", output)

    def test_lsp_doctor_reports_missing_servers_without_failing(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "print('hi')\n")
            stdout = io.StringIO()
            with patch("shutil.which", return_value=None):
                with patch("src.lsp._trusted_user_lsp_candidates", return_value=[]):
                    with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                        exit_code = main(["lsp", "doctor", "--project", project_root, "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["command"], "lsp doctor")
            self.assertEqual(payload["servers"][0]["status"], "missing")

    def test_diagnostics_lsp_json_outputs_skipped_without_server(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "print('hi')\n")
            stdout = io.StringIO()
            with patch("shutil.which", return_value=None):
                with patch("src.lsp._trusted_user_lsp_candidates", return_value=[]):
                    with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                        exit_code = main(["diagnostics", "--project", project_root, "--source", "lsp", "--files", "main.py", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["runs"][0]["status"], "skipped")

    def test_check_with_lsp_passes_options_to_checker(self) -> None:
        from src.cli import main

        captured = {}

        def fake_check(self, **kwargs):
            captured.update(kwargs)
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "project_root": "/tmp/demo",
                "status": "passed",
                "types": ["python"],
                "incremental": {"enabled": True, "files_checked": ["main.py"], "files_count": 1},
                "runs": [],
                "summary": {"total_errors": 0, "total_warnings": 0, "files_with_errors": 0, "tools_run": 0, "tools_skipped": 0, "tool_failures": 0},
                "errors_by_file": {},
            }

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "print('hi')\n")
            with patch.object(RepoMapChecker, "check", fake_check):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    exit_code = main(["check", "--project", project_root, "--types", "python", "--no-symbols", "--modified-file", "main.py", "--with-lsp", "--lsp-timeout", "1.5", "--lsp-max-files", "3"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(captured["with_lsp"])
        self.assertEqual(captured["lsp_timeout"], 1.5)
        self.assertEqual(captured["lsp_max_files"], 3)

    def test_invalid_project_path_fails_clearly(self) -> None:
        from src.cli import main

        missing = str(Path(tempfile.gettempdir()) / "repomap-missing-project-for-test")
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            exit_code = main(["overview", "--project", missing])

        self.assertEqual(exit_code, 1)
        self.assertIn("project path is not a directory", stderr.getvalue())

    def test_impact_normalizes_dot_relative_and_absolute_files(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def run():\n    return 1\n")

            dot_stdout = io.StringIO()
            with redirect_stdout(dot_stdout), redirect_stderr(io.StringIO()):
                dot_code = main(["impact", "--project", project_root, "--files", "./main.py", "--json"])

            abs_stdout = io.StringIO()
            abs_path = str(Path(project_root, "main.py"))
            with redirect_stdout(abs_stdout), redirect_stderr(io.StringIO()):
                abs_code = main(["impact", "--project", project_root, "--files", abs_path, "--json"])

            self.assertEqual(dot_code, 0)
            self.assertEqual(abs_code, 0)
            self.assertEqual(json.loads(dot_stdout.getvalue())["result"]["inputFiles"], ["main.py"])
            self.assertEqual(json.loads(abs_stdout.getvalue())["result"]["inputFiles"], ["main.py"])

    def test_impact_with_symbols_outputs_edit_plan_fields(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def target():\n    return 1\n")
            write_file(project_root, "caller.py", "from main import target\n\ndef run():\n    return target()\n")
            write_file(project_root, "test_main.py", "from main import target\n\ndef test_target():\n    assert target() == 1\n")

            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["impact", "--project", project_root, "--files", "main.py", "--with-symbols", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            result = payload["result"]
            self.assertEqual(result["inputFiles"], ["main.py"])
            self.assertTrue(any(item["name"] == "target" for item in result["keySymbols"]))
            self.assertEqual(result["readNext"][0]["file"], "main.py")
            self.assertEqual(result["readNext"][0]["role"], "target")
            self.assertIn("lspHint", result)
            self.assertIn("available", result["lspHint"])

    def test_impact_without_symbols_keeps_compatible_json_fields(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def target():\n    return 1\n")
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["impact", "--project", project_root, "--files", "main.py", "--json"])

            self.assertEqual(exit_code, 0)
            result = json.loads(stdout.getvalue())["result"]
            self.assertIn("affectedFiles", result)
            self.assertIn("tests", result)
            self.assertIn("riskLevel", result)
            self.assertEqual(result["keySymbols"], [])
            self.assertEqual(result["lspHint"], {})

    def test_impact_rejects_outside_file_path(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def run():\n    return 1\n")
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = main(["impact", "--project", project_root, "--files", "../outside.py"])

            self.assertEqual(exit_code, 1)
            self.assertIn("outside project", stderr.getvalue())

    def test_query_paths_and_exclude_match_path_segments(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "src/main.py", "def target():\n    return 1\n")
            write_file(project_root, "src2/main.py", "def target():\n    return 2\n")

            paths_stdout = io.StringIO()
            with redirect_stdout(paths_stdout), redirect_stderr(io.StringIO()):
                paths_code = main(["query", "--project", project_root, "--query", "target", "--paths", "src", "--json"])

            exclude_stdout = io.StringIO()
            with redirect_stdout(exclude_stdout), redirect_stderr(io.StringIO()):
                exclude_code = main(["query", "--project", project_root, "--query", "target", "--exclude", "src", "--json"])

            self.assertEqual(paths_code, 0)
            self.assertEqual(exclude_code, 0)
            paths_payload = json.loads(paths_stdout.getvalue())
            exclude_payload = json.loads(exclude_stdout.getvalue())
            self.assertTrue(any(row["path"] == "src/main.py" for row in paths_payload["result"]["coreFiles"] + paths_payload["result"]["supportingFiles"]))
            self.assertFalse(any(row["path"] == "src2/main.py" for row in paths_payload["result"]["coreFiles"] + paths_payload["result"]["supportingFiles"]))
            self.assertTrue(any(row["path"] == "src2/main.py" for row in exclude_payload["result"]["coreFiles"] + exclude_payload["result"]["supportingFiles"]))
            self.assertFalse(any(row["path"] == "src/main.py" for row in exclude_payload["result"]["coreFiles"] + exclude_payload["result"]["supportingFiles"]))

    def test_check_rejects_unsafe_modified_file_paths(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "pyproject.toml", "[project]\nname = \"demo\"\n")
            write_file(project_root, "main.py", "def run():\n    return 1\n")

            for unsafe in ("../outside.py", "-bad.py"):
                stderr = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    exit_code = main(["check", "--project", project_root, "--types", "python", "--no-symbols", "--modified-file", unsafe])

                self.assertEqual(exit_code, 1)
                self.assertIn("unsafe modified file", stderr.getvalue())

    def test_diagnostic_commands_put_modified_files_after_double_dash(self) -> None:
        from src.check import DiagnosticRunner

        with tempfile.TemporaryDirectory() as project_root:
            runner = DiagnosticRunner(Path(project_root), modified_files=["src/main.py"])
            with patch.object(runner, "_has_cmd", return_value=True), patch.object(runner, "_run_command", return_value=(0, "[]", 1)) as run_mock:
                runner._run_ruff()

            command = run_mock.call_args.args[0]
            self.assertEqual(command[:5], ["ruff", "check", "--output-format", "json", "--"])
            self.assertIn("src/main.py", command)

    def test_removed_low_value_commands_are_not_public(self) -> None:
        from src.cli import main

        help_stdout = io.StringIO()
        with redirect_stdout(help_stdout), redirect_stderr(io.StringIO()):
            help_code = main(["--help"])

        self.assertEqual(help_code, 0)
        self.assertNotIn("diff-risk", help_stdout.getvalue())

        cache_stdout = io.StringIO()
        with redirect_stdout(cache_stdout), redirect_stderr(io.StringIO()):
            cache_help_code = main(["cache", "--help"])

        self.assertEqual(cache_help_code, 0)
        cache_help = cache_stdout.getvalue()
        self.assertIn("save", cache_help)
        self.assertNotIn("load", cache_help)

    def test_focused_commands_remain_public_with_clear_value(self) -> None:
        from src.cli import main

        help_stdout = io.StringIO()
        with redirect_stdout(help_stdout), redirect_stderr(io.StringIO()):
            exit_code = main(["--help"])

        self.assertEqual(exit_code, 0)
        help_text = help_stdout.getvalue()
        self.assertIn("routes", help_text)
        self.assertIn("diagnostics", help_text)
        self.assertIn("verify", help_text)

        verify_stdout = io.StringIO()
        with redirect_stdout(verify_stdout), redirect_stderr(io.StringIO()):
            verify_code = main(["verify", "--help"])

        self.assertEqual(verify_code, 0)
        self.assertIn("--quick", verify_stdout.getvalue())


    def test_routes_help_includes_json_output(self) -> None:
        from src.cli import main

        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            exit_code = main(["routes", "--help"])

        self.assertEqual(exit_code, 0)
        self.assertIn("--json", stdout.getvalue())

    def test_routes_filters_test_dsl_noise(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "e2e/analysis.spec.ts",
                "import { test } from '@playwright/test';\n"
                "test.describe('/analysis', () => {\n"
                "  console.log('/health');\n"
                "  items.some('/items', handler);\n"
                "});\n",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["routes", "--project", project_root])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("未检测到 HTTP 路由定义", output)
        self.assertNotIn("DESCRIBE", output)
        self.assertNotIn("LOG", output)
        self.assertNotIn("SOME", output)

    def test_routes_json_outputs_machine_readable_routes(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "src/routes.ts", "router.get('/items', handler);\n")
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["routes", "--project", project_root, "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["command"], "routes")
        self.assertIn("scanStats", payload)
        self.assertEqual(len(payload["routes"]), 1)
        route = payload["routes"][0]
        self.assertEqual(route["method"], "GET")
        self.assertEqual(route["path"], "/items")
        self.assertEqual(route["framework"], "express")
        self.assertEqual(route["file"], "src/routes.ts")
        self.assertEqual(route["line"], 1)

    def test_js_detector_fallback_skips_dependency_directories(self) -> None:
        from src.check import ProjectDetector

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "node_modules/pkg/index.js", "module.exports = {};\n")
            with patch("src.check.subprocess.run", side_effect=FileNotFoundError()):
                self.assertFalse(ProjectDetector._has_js_files(Path(project_root)))

        from src.cli.cli import _parse_git_status_porcelain_paths

        self.assertEqual(
            _parse_git_status_porcelain_paths(
                " M todo.md\n"
                "M  src/app.ts\n"
                "?? new file.ts\n"
                "R  old.ts -> src/new.ts\n"
            ),
            ["todo.md", "src/app.ts", "new file.ts", "src/new.ts"],
        )

    def test_script_entrypoint_runs_without_package_context(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "src/cli/__main__.py", "doctor"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("tree-sitter parsers", result.stdout)
        self.assertIn("tsx", result.stdout)
        self.assertIn("repomap_cli:", result.stdout)
        self.assertIn("repomap_parser:", result.stdout)
        self.assertRegex(result.stdout, r"PyInstaller: (available|not installed in current runtime, only required for build-binary)")

    def test_help_lists_former_mcp_commands_and_excludes_mcp(self) -> None:
        from src.cli import main

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
        from src.cli import main

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

    def test_query_symbol_with_lsp_appends_evidence(self) -> None:
        from src.cli import main

        fake_run = {
            "server": "fake-lsp",
            "language": "python",
            "status": "ok",
            "command": ["fake-lsp"],
            "workspaceRoot": "/tmp/demo",
            "reason": "",
            "durationMs": 1,
            "diagnostics": [],
            "definitions": [{"file": "lib.py", "line": 1, "col": 5, "end_line": 1, "end_col": 11}],
            "references": [{"file": "main.py", "line": 4, "col": 12, "end_line": 4, "end_col": 18}],
        }

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def helper():\n    return 1\n")
            write_file(project_root, "main.py", "from lib import helper\n\ndef caller():\n    return helper()\n")
            stdout = io.StringIO()
            with patch("src.cli.cli._collect_lsp_evidence_for_symbol", return_value=fake_run):
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    exit_code = main(["query-symbol", "--project", project_root, "--symbol", "helper", "--with-lsp"])

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("### LSP evidence", output)
            self.assertIn("Definitions: 1", output)
            self.assertIn("References: 1", output)

    def test_refs_with_lsp_json_includes_evidence(self) -> None:
        from src.cli import main

        fake_run = {
            "server": "fake-lsp",
            "language": "python",
            "status": "ok",
            "command": ["fake-lsp"],
            "workspaceRoot": "/tmp/demo",
            "reason": "",
            "durationMs": 1,
            "diagnostics": [],
            "definitions": [{"file": "lib.py", "line": 1, "col": 5, "end_line": 1, "end_col": 11}],
            "references": [{"file": "main.py", "line": 4, "col": 12, "end_line": 4, "end_col": 18}],
        }

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def helper():\n    return 1\n")
            write_file(project_root, "main.py", "from lib import helper\n\ndef caller():\n    return helper()\n")
            stdout = io.StringIO()
            with patch("src.cli.cli._collect_lsp_evidence_for_symbol", return_value=fake_run):
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    exit_code = main([
                        "refs", "--project", project_root, "--symbol", "helper",
                        "--file-path", "lib.py", "--with-lsp", "--json",
                    ])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["lsp"]["status"], "ok")
            self.assertEqual(payload["lsp"]["references"][0]["file"], "main.py")

    def test_cache_save_and_diff_follow_standalone_cli_semantics(self) -> None:
        from src.cli import main

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
            self.assertIn("Graph baseline saved", cache_stdout.getvalue())
            self.assertIn("新增符号: 1", diff_stdout.getvalue())

    def test_build_binary_invokes_pyinstaller_onefile_for_repomap_binary(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as output_dir:
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("src.cli.cli.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(["build-binary", "--output", output_dir])

            self.assertEqual(exit_code, 0)
            command = run_mock.call_args.args[0]
            self.assertIn("--onefile", command)
            self.assertIn("--name", command)
            self.assertIn("repomap", command)

    def test_orphan_reports_unreferenced_get_prefix_function(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def get_unused():\n    return 1\n")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["orphan", "--project", project_root])

            self.assertEqual(exit_code, 0)
            self.assertIn("get_unused", stdout.getvalue())

    def test_call_chain_requires_file_path_when_symbol_is_ambiguous(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "b.py",
                "def helper():\n    return 2\n\ndef caller():\n    return helper()\n",
            )

            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = main(["call-chain", "--project", project_root, "--symbol", "helper"])

            self.assertEqual(exit_code, 1)
            self.assertIn("--file-path", stderr.getvalue())
            self.assertIn("a.py:1", stderr.getvalue())
            self.assertIn("b.py:1", stderr.getvalue())

    def test_call_chain_can_disambiguate_with_file_path(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "b.py",
                "def helper():\n    return 2\n\ndef caller():\n    return helper()\n",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    ["call-chain", "--project", project_root, "--symbol", "helper", "--file-path", "b.py"]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("caller", stdout.getvalue())
            self.assertIn("b.py:1", stdout.getvalue())

    def test_query_symbol_groups_exact_and_fuzzy_matches(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(project_root, "b.py", "def helper_extra():\n    return 2\n")
            write_file(project_root, "c.py", "def helper():\n    return 3\n")

            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["query-symbol", "--project", project_root, "--symbol", "helper"])

            text = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("## 精确匹配 `helper` (2)", text)
            self.assertIn("## 模糊匹配 (1)", text)
            self.assertIn("建议加 `--file-path`", text)
            self.assertIn("helper_extra", text)

    def test_query_symbol_can_filter_by_file_path(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(project_root, "b.py", "def helper():\n    return 2\n")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["query-symbol", "--project", project_root, "--symbol", "helper", "--file-path", "b.py"])

            text = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("已按文件过滤: `b.py`", text)
            self.assertIn("## 精确匹配 `helper` (1)", text)
            self.assertIn("`b.py:1`", text)
            self.assertNotIn("`a.py:1`", text)

    def test_overview_git_co_change_requires_explicit_flag(self) -> None:
        import src.ai
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def run():\n    return 1\n")

            def fake_co_change_section(engine):
                src.ai.get_co_change_neighbors(str(engine.project_root), "main.py")
                return ["## 隐式耦合（Git 共变）\n"]

            with patch.object(src.ai, "get_co_change_neighbors", return_value=[("other.py", 2)]) as co_change_mock:
                with patch.object(src.ai, "_render_co_change_section", side_effect=fake_co_change_section):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        default_code = main(["overview", "--project", project_root])
                    self.assertEqual(default_code, 0)
                    self.assertEqual(co_change_mock.call_count, 0)

                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        enabled_code = main(["overview", "--project", project_root, "--with-co-change"])
                    self.assertEqual(enabled_code, 0)
                    self.assertGreater(co_change_mock.call_count, 0)

    def test_overview_json_returns_machine_readable_summary(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def helper():\n    return 1\n")
            write_file(project_root, "main.py", "from lib import helper\n\ndef caller():\n    return helper()\n")
            write_file(project_root, "README.md", "# Demo\n")
            write_file(project_root, "scripts/check.sh", "#!/usr/bin/env bash\necho ok\n")
            write_file(project_root, ".env", "SECRET=hidden\n")

            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["overview", "--project", project_root, "--json"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["project_root"], project_root)
            self.assertIn("scan_stats", payload)
            self.assertIn("entry_points", payload)
            self.assertIn("hotspots", payload)
            self.assertIn("reading_order", payload)
            self.assertIn("modules", payload)
            self.assertIn("summary_symbols", payload)
            self.assertIn("supporting_files", payload)
            self.assertGreaterEqual(payload["scan_stats"]["symbol_count"], 2)
            self.assertLessEqual(len(payload["hotspots"]), 8)
            self.assertLessEqual(len(payload["reading_order"]), 6)
            self.assertLessEqual(len(payload["modules"]), 6)
            self.assertLessEqual(len(payload["summary_symbols"]), 4)
            supporting_paths = {item["file"] for item in payload["supporting_files"]}
            self.assertIn("README.md", supporting_paths)
            self.assertIn("scripts/check.sh", supporting_paths)
            self.assertNotIn(".env", supporting_paths)

    def test_file_detail_defaults_to_compact_symbol_list(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                "\n\n".join(f"def helper_{index}():\n    return {index}" for index in range(15)) + "\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["file-detail", "--project", project_root, "--file-path", "main.py"])

            text = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("默认仅展开前 12 个符号", text)
            self.assertIn("helper_11", text)
            self.assertNotIn("helper_12", text)

    def test_file_detail_max_chars_truncates_output(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                "\n\n".join(
                    f"def helper_{index}(first_argument, second_argument, third_argument):\n    return first_argument + second_argument + third_argument + {index}"
                    for index in range(12)
                ) + "\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(
                    ["file-detail", "--project", project_root, "--file-path", "main.py", "--max-chars", "220"]
                )

            text = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("已截断", text)
            self.assertLessEqual(len(text), 260)

    def test_call_chain_json_returns_selected_symbol_and_edges(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def helper():\n    return 1\n")
            write_file(project_root, "main.py", "from lib import helper\n\ndef caller():\n    return helper()\n")

            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["call-chain", "--project", project_root, "--symbol", "helper", "--json"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["symbol"]["name"], "helper")
            self.assertEqual(payload["direction"], "both")
            self.assertTrue(any(item["name"] == "caller" for item in payload["callers"]))

    def test_scan_cache_reuses_engine_for_identical_project_state(self) -> None:
        import src.cli.cli as cli_mod
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def helper():\n    return 1\n")
            cli_mod._SCAN_CACHE.clear()

            original_scan = cli_mod.RepoMapEngine.scan
            with patch.object(cli_mod.RepoMapEngine, "scan", autospec=True, wraps=original_scan) as scan_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code1 = main(["overview", "--project", project_root])
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code2 = main(["query-symbol", "--project", project_root, "--symbol", "helper"])

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(scan_mock.call_count, 1)

    def test_default_project_resolves_to_current_working_directory(self) -> None:
        import src.cli.cli as cli_mod

        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as project_root:
            try:
                os.chdir(project_root)
                resolved = cli_mod._resolve_project(None)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(resolved, str(Path(project_root).resolve()))

    def test_default_project_warns_when_current_working_directory_is_home(self) -> None:
        import src.cli.cli as cli_mod

        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as home_dir:
            try:
                os.chdir(home_dir)
                stderr = io.StringIO()
                with patch.object(cli_mod.Path, "home", return_value=Path(home_dir).resolve()):
                    with redirect_stderr(stderr):
                        resolved = cli_mod._resolve_project(None)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(resolved, str(Path(home_dir).resolve()))
        self.assertIn("warning: default project root is your home directory", stderr.getvalue())

    def test_cache_paths_canonicalize_project_path(self) -> None:
        from src import get_session_cache_path

        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_root:
            project = Path(temp_root, "demo")
            project.mkdir()
            try:
                os.chdir(temp_root)
                relative_cache = get_session_cache_path("demo")
                absolute_cache = get_session_cache_path(str(project))
            finally:
                os.chdir(old_cwd)

        self.assertEqual(relative_cache, absolute_cache)
        self.assertEqual(relative_cache.name, "session_scan.json")

    def test_cache_paths_isolate_same_name_projects(self) -> None:
        from src import get_cache_paths, get_session_cache_path

        with tempfile.TemporaryDirectory() as temp_root:
            project_a = Path(temp_root, "a", "demo")
            project_b = Path(temp_root, "b", "demo")
            project_a.mkdir(parents=True)
            project_b.mkdir(parents=True)

            session_a = get_session_cache_path(str(project_a))
            session_b = get_session_cache_path(str(project_b))
            cache_paths_a = get_cache_paths(str(project_a))

        self.assertNotEqual(session_a, session_b)
        self.assertEqual(session_a.name, "session_scan.json")
        self.assertEqual(session_a.parent, cache_paths_a[0].parent)
        self.assertEqual([path.name for path in cache_paths_a], ["symbols.json", "git.json", "last_snapshot.json"])

    def test_session_cache_rejects_mismatched_project_root_payload(self) -> None:
        import src.cli.cli as cli_mod
        from src.cli import main
        from src import get_session_cache_path

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def helper():\n    return 1\n")
            cli_mod._SCAN_CACHE.clear()
            session_cache = get_session_cache_path(project_root)
            if session_cache.exists():
                session_cache.unlink()

            original_scan = cli_mod.RepoMapEngine.scan
            with patch.object(cli_mod.RepoMapEngine, "scan", autospec=True, wraps=original_scan) as scan_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code1 = main(["overview", "--project", project_root])

                payload = json.loads(session_cache.read_text(encoding="utf-8"))
                payload["project_root"] = str(Path(project_root).parent / "other-project")
                session_cache.write_text(json.dumps(payload), encoding="utf-8")

                cli_mod._SCAN_CACHE.clear()
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code2 = main(["query-symbol", "--project", project_root, "--symbol", "helper"])

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(scan_mock.call_count, 2)

    def test_session_cache_reuses_scan_across_memory_cache_reset(self) -> None:
        import src.cli.cli as cli_mod
        from src.cli import main
        from src import get_session_cache_path

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def helper():\n    return 1\n")
            cli_mod._SCAN_CACHE.clear()
            session_cache = get_session_cache_path(project_root)
            if session_cache.exists():
                session_cache.unlink()

            original_scan = cli_mod.RepoMapEngine.scan
            with patch.object(cli_mod.RepoMapEngine, "scan", autospec=True, wraps=original_scan) as scan_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code1 = main(["overview", "--project", project_root])
                cli_mod._SCAN_CACHE.clear()
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code2 = main(["query-symbol", "--project", project_root, "--symbol", "helper"])

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertTrue(session_cache.exists())
            self.assertEqual(scan_mock.call_count, 1)

    def test_session_cache_preserves_zero_symbol_entry_files(self) -> None:
        import src.cli.cli as cli_mod
        from src.cli import main
        from src import get_session_cache_path

        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "src/main.tsx",
                (
                    "import React from 'react';\n"
                    "import ReactDOM from 'react-dom/client';\n"
                    "import { App } from './App';\n"
                    "ReactDOM.createRoot(document.getElementById('root')!).render(<App />);\n"
                ),
            )
            write_file(
                project_root,
                "src/App.tsx",
                "export function App() {\n  return <div>app</div>;\n}\n",
            )
            cli_mod._SCAN_CACHE.clear()
            session_cache = get_session_cache_path(project_root)
            if session_cache.exists():
                session_cache.unlink()

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code1 = main(["overview", "--project", project_root])

            cli_mod._SCAN_CACHE.clear()
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code2 = main(["overview", "--project", project_root, "--json"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertTrue(session_cache.exists())
            self.assertIn("src/main.tsx", payload["entry_points"])
            self.assertEqual(payload["reading_order"][0]["file"], "src/main.tsx")

    def test_scan_cache_invalidates_after_source_change(self) -> None:
        import src.cli.cli as cli_mod
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def helper():\n    return 1\n")
            cli_mod._SCAN_CACHE.clear()

            original_scan = cli_mod.RepoMapEngine.scan
            with patch.object(cli_mod.RepoMapEngine, "scan", autospec=True, wraps=original_scan) as scan_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code1 = main(["overview", "--project", project_root])
                write_file(project_root, "main.py", "def helper():\n    return 1\n\ndef added():\n    return helper()\n")
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code2 = main(["query-symbol", "--project", project_root, "--symbol", "added"])

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(scan_mock.call_count, 2)

    def test_session_cache_invalidates_after_source_change(self) -> None:
        import src.cli.cli as cli_mod
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def helper():\n    return 1\n")
            cli_mod._SCAN_CACHE.clear()

            original_scan = cli_mod.RepoMapEngine.scan
            with patch.object(cli_mod.RepoMapEngine, "scan", autospec=True, wraps=original_scan) as scan_mock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code1 = main(["overview", "--project", project_root])
                cli_mod._SCAN_CACHE.clear()
                write_file(project_root, "main.py", "def helper():\n    return 1\n\ndef changed():\n    return helper()\n")
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code2 = main(["overview", "--project", project_root])

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(scan_mock.call_count, 2)

    def test_refs_requires_file_path_when_symbol_is_ambiguous(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "b.py",
                "def helper():\n    return 2\n\ndef caller():\n    return helper()\n",
            )

            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = main(["refs", "--project", project_root, "--symbol", "helper"])

            self.assertEqual(exit_code, 1)
            self.assertIn("--file-path", stderr.getvalue())
            self.assertIn("a.py:1", stderr.getvalue())
            self.assertIn("b.py:1", stderr.getvalue())

    def test_refs_can_disambiguate_with_file_path(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "b.py",
                "def helper():\n    return 2\n\ndef caller():\n    return helper()\n",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    ["refs", "--project", project_root, "--symbol", "helper", "--file-path", "b.py"]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("caller", stdout.getvalue())
            self.assertIn("被引用次数: 1", stdout.getvalue())

    def test_git_history_requires_file_path_when_symbol_is_ambiguous(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(project_root, "b.py", "def helper():\n    return 2\n")

            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = main(["git-history", "--project", project_root, "--symbol", "helper"])

            self.assertEqual(exit_code, 1)
            self.assertIn("--file-path", stderr.getvalue())
            self.assertIn("a.py:1", stderr.getvalue())
            self.assertIn("b.py:1", stderr.getvalue())

    def test_git_history_can_disambiguate_with_file_path(self) -> None:
        from src.cli import main

        with tempfile.TemporaryDirectory() as project_root:
            subprocess.run(["git", "init"], cwd=project_root, capture_output=True, text=True, check=False)
            subprocess.run(["git", "config", "user.name", "RepoMap Test"], cwd=project_root, capture_output=True, text=True, check=False)
            subprocess.run(["git", "config", "user.email", "repomap@example.com"], cwd=project_root, capture_output=True, text=True, check=False)

            write_file(project_root, "a.py", "def helper():\n    return 1\n")
            write_file(project_root, "b.py", "def helper():\n    return 2\n")
            subprocess.run(["git", "add", "a.py", "b.py"], cwd=project_root, capture_output=True, text=True, check=False)
            subprocess.run(["git", "commit", "-m", "add helpers"], cwd=project_root, capture_output=True, text=True, check=False)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    ["git-history", "--project", project_root, "--symbol", "helper", "--file-path", "b.py"]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("b.py:1", stdout.getvalue())
            self.assertIn("最近提交", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
