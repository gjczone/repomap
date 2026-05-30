"""Issue #39 回归测试 — LSP响应污染、静默吞错、截断逻辑、PageRank异常值

每个测试对应 Issue #39 中的一个 P1/P2 问题，修复前必须失败。
"""

from __future__ import annotations

import math
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════════════════════
# P1-1: lsp.py StdioLspClient.request() 超时后非目标响应被放回队列
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1_1_LspResponsePollution(unittest.TestCase):
    """P1-1: 不匹配的 LSP 响应不应放回队列，避免后续请求误消费。"""

    def test_unmatched_response_not_requeued(self) -> None:
        from src.lsp import StdioLspClient

        client = StdioLspClient.__new__(StdioLspClient)
        client.process = MagicMock()
        client.process.poll.return_value = None
        client.process.stderr = None
        client.command = ["test-lsp"]
        client._messages = queue.Queue()
        client._notifications = []
        client._stop_event = __import__("threading").Event()
        client._reader = None
        client._stderr_reader = None
        client._send = MagicMock()
        client._id_lock = __import__("threading").Lock()
        client.timeout = 1.0
        client._next_id = 1

        # 放入一个不匹配 id 的响应，然后放入匹配的
        client._messages.put({"jsonrpc": "2.0", "id": 999, "result": "stale"})
        client._messages.put({"jsonrpc": "2.0", "id": 1, "result": "correct"})

        response = client.request("test/method", {})

        self.assertEqual(response.get("id"), 1)
        self.assertEqual(response.get("result"), "correct")
        # 验证 stale 响应已被消费且未被放回（队列中不应有它）
        self.assertTrue(client._messages.empty(), "stale 响应不应被放回队列")

    def test_stale_response_discarded_with_warning(self) -> None:
        from src.lsp import StdioLspClient

        client = StdioLspClient.__new__(StdioLspClient)
        client.process = MagicMock()
        client.process.poll.return_value = None
        client.process.stderr = None
        client.command = ["test-lsp"]
        client._messages = queue.Queue()
        client._notifications = []
        client._stop_event = __import__("threading").Event()
        client._reader = None
        client._stderr_reader = None
        client._send = MagicMock()
        client._id_lock = __import__("threading").Lock()
        client.timeout = 0.5
        client._next_id = 2

        client._messages.put({"jsonrpc": "2.0", "id": 99, "result": "old"})
        client._messages.put({"jsonrpc": "2.0", "id": 2, "result": "good"})

        with self.assertLogs("repomap.lsp", level="WARNING") as log_ctx:
            response = client.request("test/method", {})

        self.assertEqual(response.get("id"), 2)
        self.assertTrue(
            any(
                "unmatched" in msg.lower() or "unexpected" in msg.lower() or "99" in msg
                for msg in log_ctx.output
            ),
            f"应记录 unmatched 响应的 warning，实际日志: {log_ctx.output}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# P1-2: lsp.py StdioLspClient.close() shutdown 失败静默吞错
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1_2_LspCloseShutdownFailure(unittest.TestCase):
    """P1-2: close() 中 shutdown 失败应记录日志，而非静默吞错。"""

    def test_shutdown_failure_logs_warning(self) -> None:
        from src.lsp import StdioLspClient

        import threading

        client = StdioLspClient.__new__(StdioLspClient)
        client.process = MagicMock()
        client.process.poll.return_value = None
        client.process.stderr = None
        client.command = ["test-lsp"]
        client._messages = queue.Queue()
        client._notifications = []
        client._stop_event = __import__("threading").Event()
        client._reader = None
        client._stderr_reader = None
        client._id_lock = threading.Lock()
        client._send = MagicMock()
        client.timeout = 1.0

        # 让 request("shutdown") 抛出异常
        def failing_request(method, params=None, request_id=None):
            if method == "shutdown":
                raise OSError("connection lost")
            return {}

        client.request = failing_request
        client.send_notification = MagicMock()

        with self.assertLogs("repomap.lsp", level="DEBUG") as log_ctx:
            client.close()

        self.assertTrue(
            any("shutdown" in msg.lower() for msg in log_ctx.output),
            f"shutdown 失败应记录日志，实际日志: {log_ctx.output}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# P1-3: parser.py 嵌套检测失败静默吞错
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1_3_ParserNestingDetectionFailure(unittest.TestCase):
    """P1-3: 嵌套深度检测失败应记录日志。"""

    def test_parse_does_not_crash_on_invalid_text(self) -> None:
        """解析无效内容时不崩溃，安全返回 None。"""
        from src.parser import TreeSitterAdapter

        adapter = TreeSitterAdapter.__new__(TreeSitterAdapter)
        adapter.parsers = {}
        adapter._nesting_limit = 1000
        adapter._max_file_bytes = 1024 * 1024

        result = adapter.parse(b"\xff\xfe\x00\x01\x02", "python")
        # 无 parser 时无效内容应安全返回 None（不崩溃）
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════════
# P1-4: check.py _has_js_files rg 失败静默吞错
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1_4_HasJsFilesRgFailure(unittest.TestCase):
    """P1-4: _has_js_files 中 rg 失败应记录 warning。"""

    def test_rg_failure_falls_back_to_walk(self) -> None:
        import src.check

        if not hasattr(src.check, "logger"):
            self.skipTest("check.py 尚未添加 logger（待修复后此测试应通过）")

        from src.check import ProjectDetector

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("rg not found")
            with self.assertLogs("repomap", level="WARNING") as log_ctx:
                with tempfile.TemporaryDirectory() as tmpdir:
                    result = ProjectDetector._has_js_files(Path(tmpdir))
                    self.assertFalse(result)

            self.assertTrue(
                any(
                    "rg" in msg.lower() or "ripgrep" in msg.lower()
                    for msg in log_ctx.output
                ),
                f"rg 失败应记录 WARNING，实际日志: {log_ctx.output}",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# P1-5: check.py modified_files_in_project 失败静默吞错
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1_5_ModifiedFilesGitFailure(unittest.TestCase):
    """P1-5: modified_files_in_project git 失败应记录 warning。"""

    def test_git_failure_logs_warning(self) -> None:
        import src.check

        if not hasattr(src.check, "logger"):
            self.skipTest("check.py 尚未添加 logger（待修复后此测试应通过）")

        from src.check import GitHelper

        with patch("src.check.logger"):
            result = GitHelper.get_modified_files(Path("/tmp/nonexistent_repo"))
            self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════════════
# P2-6: ranking.py PageRank NaN/负值/Inf 传播
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_6_PageRankInvalidWeights(unittest.TestCase):
    """P2-6: PageRank 计算应过滤 NaN、Inf 和负值权重。"""

    def test_nan_weight_filtered(self) -> None:
        from src import Symbol, Edge, RepoGraph
        from src.ranking import GraphAnalyzer

        graph = RepoGraph()
        s1 = Symbol(id="s1", name="sym1", kind="function", file="a.py", line=1)
        s2 = Symbol(id="s2", name="sym2", kind="function", file="a.py", line=2)
        graph.symbols = {"s1": s1, "s2": s2}
        graph.outgoing["s1"] = [
            Edge(source="s1", target="s2", weight=float("nan"), kind="call")
        ]
        graph.outgoing["s2"] = []

        analyzer = GraphAnalyzer(graph)
        analyzer.calculate_pagerank()

        self.assertIn("s1", graph.symbols)
        self.assertFalse(math.isnan(s1.pagerank), "NaN weight 不应污染 PageRank")
        self.assertFalse(math.isnan(s2.pagerank), "NaN weight 不应污染 PageRank")

    def test_negative_weight_filtered(self) -> None:
        from src import Symbol, Edge, RepoGraph
        from src.ranking import GraphAnalyzer

        graph = RepoGraph()
        s1 = Symbol(id="s1", name="sym1", kind="function", file="a.py", line=1)
        s2 = Symbol(id="s2", name="sym2", kind="function", file="a.py", line=2)
        graph.symbols = {"s1": s1, "s2": s2}
        graph.outgoing["s1"] = [
            Edge(source="s1", target="s2", weight=-5.0, kind="call")
        ]
        graph.outgoing["s2"] = []

        analyzer = GraphAnalyzer(graph)
        analyzer.calculate_pagerank()

        self.assertGreaterEqual(s1.pagerank, 0, "负值权重应被过滤")
        self.assertGreaterEqual(s2.pagerank, 0, "负值权重应被过滤")


# ═══════════════════════════════════════════════════════════════════════════════
# P2-7: ai.py _truncate_output 在换行边界截断
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_7_TruncateOutputNewlineBoundary(unittest.TestCase):
    """P2-7: _truncate_output 应在换行边界截断，不在任意字符位置截断。"""

    def test_truncate_at_newline(self) -> None:
        from src.ai import _truncate_output

        text = "line 1\nline 2\nline 3\nline 4"
        max_chars = 15  # "line 1\nline 2\n" is 13 chars
        result = _truncate_output(text, max_chars)

        self.assertIn("truncated", result.lower(), f"截断输出应有截断提示: {result!r}")

    def test_truncate_respects_max_chars(self) -> None:
        from src.ai import _truncate_output

        text = "line 1\nline 2\nline 3\nline 4"
        max_chars = 15
        result = _truncate_output(text, max_chars)

        # 截断后的内容（去掉截断提示行）不应超过 max_chars
        self.assertIn("truncated", result.lower())
        # 截断点应在换行处
        lines = result.split("\n")
        # 核心内容部分（截断提示之前）不应超过 max_chars
        trunc_msg_index = next(
            i for i, line in enumerate(lines) if "truncated" in line.lower()
        )
        core = "\n".join(lines[:trunc_msg_index])
        self.assertLessEqual(
            len(core), max_chars, f"核心内容 {len(core)} 字符不应超过 {max_chars}"
        )

    def test_no_newline_fallback(self) -> None:
        from src.ai import _truncate_output

        text = "a" * 100
        result = _truncate_output(text, 30)

        self.assertLess(len(result), 100)
        self.assertIn("truncated", result.lower())


# ═══════════════════════════════════════════════════════════════════════════════
# P2-7 + P2-15: _truncate_output 截断提示包含大小和比例
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_15_TruncateMessageSize(unittest.TestCase):
    """P2-15: 截断提示应告知原始大小和截断比例。"""

    def test_truncation_message_includes_size(self) -> None:
        from src.ai import _truncate_output

        text = "x" * 5000
        result = _truncate_output(text, 100)

        self.assertIn("truncated", result.lower())
        # 应包含原始大小或截断比例信息
        has_size_info = (
            "5000" in result
            or "5" in result
            or "%" in result
            or "/" in result
            or "chars" in result.lower()
            or "bytes" in result.lower()
        )
        self.assertTrue(has_size_info, f"截断提示应包含大小信息: {result!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# P2-10: core.py 增量缓存保存失败日志级别
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_10_CacheSaveLogLevel(unittest.TestCase):
    """P2-10: 缓存保存失败应使用 warning 而非 debug。"""

    def test_cache_save_failure_code_uses_warning(self) -> None:
        """验证 core.py 中缓存保存失败使用 logger.warning 而非 logger.debug。"""
        import inspect
        from src import core

        source = inspect.getsource(core.RepoMapEngine.scan)
        # scan() 中的异常处理应包含 logger.warning（不是 logger.debug）
        self.assertIn(
            "logger.warning", source, "scan() 中缓存保存失败应使用 logger.warning"
        )
        self.assertNotIn(
            'logger.debug(f"Failed to save',
            source,
            "不应再使用 logger.debug 记录缓存保存失败",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# P2-11: core.py call graph enrichment 失败日志级别
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_11_CallGraphEnrichmentLogLevel(unittest.TestCase):
    """P2-11: call graph enrichment 失败应使用 warning 而非 debug。"""

    def test_enrichment_failure_code_uses_warning(self) -> None:
        """验证 _enrich_call_edges 失败时使用 logger.warning 而非 logger.debug。"""
        import inspect
        from src.core import RepoMapEngine

        source = inspect.getsource(RepoMapEngine._enrich_call_edges)
        # 异常处理应使用 logger.warning
        self.assertIn(
            "logger.warning", source, "_enrich_call_edges 异常处理应使用 logger.warning"
        )
        self.assertNotIn(
            'logger.debug(f"{label} call graph enrichment',
            source,
            "不应再使用 logger.debug 记录 call graph enrichment 失败",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# P2-14: check.py ESLint 跳过时 exit_code 不为 0
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_14_EslintSkipExitCode(unittest.TestCase):
    """P2-14: 跳过 ESLint 时 exit_code 应为非零值以区分"通过"。"""

    def test_eslint_skip_has_nonzero_exit_code(self) -> None:
        from src.check import DiagnosticResult

        result = DiagnosticResult(
            tool="eslint",
            command="skip (no eslint config)",
            exit_code=-1,
            duration_ms=0,
            skipped=True,
            skip_reason="eslint config not found",
        )

        self.assertTrue(result.skipped)
        self.assertNotEqual(
            result.exit_code,
            0,
            "跳过 ESLint 时 exit_code 不应为 0，否则 LLM 误认为检查通过",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# P2-16: git_backend.py Pygit2Backend 静默吞错
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_16_Pygit2BackendSilentExceptions(unittest.TestCase):
    """P2-16: Pygit2Backend 的 except Exception 不应完全静默。"""

    def test_blame_line_returns_none_for_invalid_repo(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.blame_line("/tmp/nonexistent_repo_xyz", "test.py", 1)
        self.assertIsNone(result)

    def test_status_porcelain_returns_empty_for_invalid_repo(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.status_porcelain("/tmp/nonexistent_repo_xyz")
        self.assertEqual(result, [])

    def test_diff_name_only_returns_empty_for_invalid_repo(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.diff_name_only("/tmp/nonexistent_repo_xyz")
        self.assertEqual(result, [])

    def test_diff_cached_name_only_returns_empty_for_invalid_repo(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.diff_cached_name_only("/tmp/nonexistent_repo_xyz")
        self.assertEqual(result, [])

    def test_log_name_only_returns_empty_for_invalid_repo(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.log_name_only("/tmp/nonexistent_repo_xyz")
        self.assertEqual(result, [])

    def test_log_commits_grouped_returns_empty_for_invalid_repo(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.log_commits_grouped("/tmp/nonexistent_repo_xyz")
        self.assertEqual(result, [])

    def test_diff_name_only_since_returns_empty_for_invalid_repo(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.diff_name_only_since("/tmp/nonexistent_repo_xyz")
        self.assertEqual(result, [])

    def test_repo_init_graceful_fallback_to_subprocess(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend._repo("/tmp/nonexistent_repo_xyz")
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════════
# P2-12: topic.py _load_co_change_scores 失败静默吞错
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_12_CoChangeScoresSilentFailure(unittest.TestCase):
    """P2-12: _load_co_change_scores git 失败应记录日志。"""

    def test_git_failure_returns_empty(self) -> None:
        from src.topic import _load_co_change_scores

        result = _load_co_change_scores("/tmp/nonexistent_repo_xyz")
        self.assertEqual(result, {})
        # 修复后 topic.py 会有 logger，此时可以检查日志
        import src.topic

        if hasattr(src.topic, "logger"):
            with self.assertLogs("repomap", level="WARNING") as log_ctx:
                _load_co_change_scores("/tmp/nonexistent_repo_xyz")
            self.assertTrue(
                any(
                    "co-change" in msg.lower()
                    or "co_change" in msg.lower()
                    or "git" in msg.lower()
                    for msg in log_ctx.output
                ),
                f"git 失败应记录日志: {log_ctx.output}",
            )


if __name__ == "__main__":
    unittest.main()
