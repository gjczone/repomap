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


from repomap_check import RepoMapChecker


class RepoMapCliTests(unittest.TestCase):
    def test_check_marks_nonzero_tool_exit_as_failed_even_without_parsed_issues(self) -> None:
        from repomap_cli import main

        def fake_check(self, types=None, resolve_symbols=True, symbols_map=None, since_commit=None, modified_files=None):
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

    def test_parse_git_status_porcelain_paths_preserves_unstaged_leading_space(self) -> None:
        from repomap_cli.cli import _parse_git_status_porcelain_paths

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
            [sys.executable, "repomap_cli/__main__.py", "doctor"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("tree-sitter parsers", result.stdout)
        self.assertIn("only required for build-binary", result.stdout)

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

    def test_orphan_reports_unreferenced_get_prefix_function(self) -> None:
        from repomap_cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def get_unused():\n    return 1\n")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["orphan", "--project", project_root])

            self.assertEqual(exit_code, 0)
            self.assertIn("get_unused", stdout.getvalue())

    def test_call_chain_requires_file_path_when_symbol_is_ambiguous(self) -> None:
        from repomap_cli import main

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
        from repomap_cli import main

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
        from repomap_cli import main

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
        from repomap_cli import main

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

    def test_overview_json_returns_machine_readable_summary(self) -> None:
        from repomap_cli import main

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def helper():\n    return 1\n")
            write_file(project_root, "main.py", "from lib import helper\n\ndef caller():\n    return helper()\n")

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
            self.assertGreaterEqual(payload["scan_stats"]["symbol_count"], 2)
            self.assertLessEqual(len(payload["hotspots"]), 8)
            self.assertLessEqual(len(payload["reading_order"]), 6)
            self.assertLessEqual(len(payload["modules"]), 6)
            self.assertLessEqual(len(payload["summary_symbols"]), 4)

    def test_file_detail_defaults_to_compact_symbol_list(self) -> None:
        from repomap_cli import main

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
        from repomap_cli import main

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
        from repomap_cli import main

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
        import repomap_cli.cli as cli_mod
        from repomap_cli import main

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

    def test_session_cache_reuses_scan_across_memory_cache_reset(self) -> None:
        import repomap_cli.cli as cli_mod
        from repomap_cli import main
        from repomap_support import get_session_cache_path

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
        import repomap_cli.cli as cli_mod
        from repomap_cli import main
        from repomap_support import get_session_cache_path

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
        import repomap_cli.cli as cli_mod
        from repomap_cli import main

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
        import repomap_cli.cli as cli_mod
        from repomap_cli import main

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
        from repomap_cli import main

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
        from repomap_cli import main

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
        from repomap_cli import main

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
        from repomap_cli import main

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
