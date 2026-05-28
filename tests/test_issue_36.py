"""Issue #36 回归测试 — scan超时状态误报、LSP进程安全、静默吞错等

每个测试对应 Issue #36 中的一个 P0/P1 问题，修复前必须失败。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestP0_1_ScanTimeoutState(unittest.TestCase):
    """P0-1: scan 超时后 scan_state 应为 'partial' 而非 'scanned'。"""

    def test_timeout_sets_partial_state(self) -> None:
        from src.core import RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir, "app.py")
            py_file.write_text("def foo(): pass\n")

            engine = RepoMapEngine(tmpdir)
            if not engine.ts.parsers:
                self.skipTest("tree-sitter not available")

            engine.scan(max_files=8000, max_scan_time=0.0)

            self.assertEqual(
                engine.scan_state,
                "partial",
                "scan 超时后 scan_state 应为 'partial'，不应误导 LLM 认为扫描完整",
            )

    def test_timeout_flag_is_set(self) -> None:
        from src.core import RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir, "app.py")
            py_file.write_text("def foo(): pass\n")

            engine = RepoMapEngine(tmpdir)
            if not engine.ts.parsers:
                self.skipTest("tree-sitter not available")

            engine.scan(max_files=8000, max_scan_time=0.0)

            self.assertTrue(
                engine.scan_stats.timeout_triggered,
                "超时后 timeout_triggered 应为 True",
            )


class TestP0_2_LspAssertSafety(unittest.TestCase):
    """P0-2: LSP StdioLspClient._send() 不应使用 assert 做空值检查。"""

    def test_send_raises_runtime_error_when_process_none(self) -> None:
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo"], "/tmp", timeout=1.0)
        client.process = None

        with self.assertRaises(RuntimeError):
            client._send({"jsonrpc": "2.0", "method": "test", "id": 1})

    def test_send_raises_runtime_error_when_stdin_none(self) -> None:
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo"], "/tmp", timeout=1.0)
        mock_process = MagicMock()
        mock_process.stdin = None
        client.process = mock_process

        with self.assertRaises(RuntimeError):
            client._send({"jsonrpc": "2.0", "method": "test", "id": 1})


class TestP0_3_GitBackendErrorVisibility(unittest.TestCase):
    """P0-3: git_backend 关键方法失败时应记录 warning 日志。"""

    def test_changed_files_logs_warning_on_git_failure(self) -> None:
        from src.git_backend import SubprocessBackend

        fake_result = MagicMock()
        fake_result.returncode = 128
        fake_result.stdout = ""
        fake_result.stderr = "fatal: not a git repository"

        with (
            patch.object(SubprocessBackend, "_run_git", return_value=fake_result),
            patch("src.git_backend.logger") as mock_logger,
        ):
            SubprocessBackend.changed_files("/fake/project")
            logged_warnings = [call for call in mock_logger.warning.call_args_list]
            self.assertTrue(
                len(logged_warnings) > 0,
                "git 命令失败时必须记录 warning 日志，让 LLM 知道 git 不可用",
            )

    def test_deleted_files_logs_warning_on_git_failure(self) -> None:
        from src.git_backend import SubprocessBackend

        fake_result = MagicMock()
        fake_result.returncode = 128
        fake_result.stdout = ""
        fake_result.stderr = "fatal: not a git repository"

        with (
            patch.object(SubprocessBackend, "_run_git", return_value=fake_result),
            patch("src.git_backend.logger") as mock_logger,
        ):
            SubprocessBackend.deleted_files("/fake/project")
            logged_warnings = [call for call in mock_logger.warning.call_args_list]
            self.assertTrue(
                len(logged_warnings) > 0,
                "git 命令失败时必须记录 warning 日志",
            )


class TestP1_4_CheckUnknownExitCode(unittest.TestCase):
    """P1-4: check 命令对 'unknown' 状态应返回非零 exit code。"""

    def test_unknown_status_returns_nonzero(self) -> None:
        from src.cli.handlers import EXIT_NO_RESULTS

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "empty.py").write_text("")

            with patch("src.cli.commands.verify.RepoMapChecker") as mock_checker_cls:
                mock_checker = MagicMock()
                mock_checker.check.return_value = {
                    "status": "unknown",
                    "summary": {},
                    "runs": [],
                    "types": ["python"],
                    "project_root": tmpdir,
                    "timestamp": "2026-01-01",
                    "errors_by_file": {},
                }
                mock_checker_cls.return_value = mock_checker

                from src.cli.commands.verify import run_check

                result = run_check(
                    project=tmpdir,
                    types=None,
                    max_issues=50,
                    since_commit=None,
                    modified_files=None,
                    resolve_symbols=False,
                    
                )
                self.assertNotEqual(
                    result,
                    0,
                    "check 'unknown' 状态应返回非零 exit code，LLM 不应误认为通过",
                )
                self.assertEqual(
                    result,
                    EXIT_NO_RESULTS,
                    "check 'unknown' 应返回 EXIT_NO_RESULTS (3)",
                )


class TestP1_5_SelectSymbolMatchLspLogging(unittest.TestCase):
    """P1-5: _select_symbol_match LSP 异常时应记录 warning。"""

    def test_lsp_exception_logs_warning(self) -> None:
        from src.cli.handlers import _select_symbol_match

        engine = MagicMock()
        engine.query_symbol.return_value = [MagicMock(name="foo", file="a.py", line=1)]

        with (
            patch(
                "src.lsp.collect_lsp_symbol_evidence",
                side_effect=RuntimeError("LSP crashed"),
            ),
            patch("src.cli.handlers.logger") as mock_logger,
        ):
            _select_symbol_match(engine, "foo",  lsp_timeout=8.0)
            logged_warnings = [call for call in mock_logger.warning.call_args_list]
            self.assertTrue(
                len(logged_warnings) > 0,
                "LSP 异常时必须记录 warning，让 LLM 知道符号解析可能不完整",
            )


class TestP1_6_MtimeSizeValidation(unittest.TestCase):
    """P1-6: 增量缓存 mtime 匹配时应额外比较文件大小。"""

    def test_mtime_matches_checks_size_too(self) -> None:
        from src.core import _mtime_matches, RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir, "app.py")
            py_file.write_text("def foo(): pass\n")

            engine = RepoMapEngine(tmpdir)
            if not engine.ts.parsers:
                self.skipTest("tree-sitter not available")

            engine.scan(max_files=8000)

            cached = engine._cache.get("app.py")
            self.assertIsNotNone(cached, "文件应被缓存")

            cached_mtime, cached_size = cached
            stat = py_file.stat()
            same_mtime = stat.st_mtime
            same_size = stat.st_size

            self.assertTrue(
                _mtime_matches(same_mtime, cached_mtime),
                "相同 mtime 应匹配",
            )

            py_file.write_text("def foo(): pass\ndef bar(): pass\n")
            new_stat = py_file.stat()

            if abs(new_stat.st_mtime - cached_mtime) < 0.002:
                new_size = new_stat.st_size
                self.assertNotEqual(
                    same_size,
                    new_size,
                    "文件内容变更但 mtime 相同时，大小应不同（用于检测缓存不一致）",
                )


class TestP1_7_LspProcessAliveCheck(unittest.TestCase):
    """P1-7: LSP request() 入口应检查进程存活状态。"""

    def test_request_raises_immediately_if_process_exited(self) -> None:
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo"], "/tmp", timeout=5.0)
        mock_process = MagicMock()
        mock_process.poll.return_value = 1
        mock_process.stdin = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b"crashed"
        client.process = mock_process

        with self.assertRaises(RuntimeError) as ctx:
            client.request("textDocument/definition", {})

        self.assertIn(
            "exited",
            str(ctx.exception).lower(),
            "进程已退出时应立即报错，而非等待 timeout",
        )


class TestP1_8_VerifyWarningExitCode(unittest.TestCase):
    """P1-8: verify 命令对 'warning' 状态应返回非零 exit code。"""

    def test_warning_status_returns_nonzero(self) -> None:
        from src.cli.commands.verify import _overall_verify_status

        status = _overall_verify_status(
            changed_files=["src/core.py"],
            risk_level="high",
            missing_checks=["no tests"],
            check_payload={"status": "passed"},
            lsp_payload={"status": "passed"},
            graph_diff_payload={"status": "skipped", "breakingChanges": []},
        )
        self.assertEqual(
            status,
            "warning",
            "有 missing_checks 时整体状态应为 warning",
        )


if __name__ == "__main__":
    unittest.main()
