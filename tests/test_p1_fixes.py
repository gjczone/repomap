"""P1 高优先级问题回归测试 — issue #33

每个测试对应一个 P1 问题，修复前必须失败。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestP1_2_UnknownCheckStatus(unittest.TestCase):
    """P1-2: check 结果不应出现 UNKNOWN，应映射为 SKIPPED/PASSED/FAILED。

    Issue #146: 修复后 check.py 不再返回 "unknown"，改为 "skipped"。
    verify 渲染也将 "unknown" 映射为 "SKIPPED"。
    """

    def test_skipped_check_with_passed_lsp_is_passed(self) -> None:
        """check skipped + LSP passed → 整体 passed（skipped 不是问题）。"""
        from src.cli.commands.verify import _overall_verify_status

        status = _overall_verify_status(
            changed_files=["src/core.py"],
            risk_level="high",
            missing_checks=[],
            check_payload={"status": "skipped"},
            lsp_payload={"status": "passed"},
            graph_diff_payload={"status": "skipped", "breakingChanges": []},
        )
        self.assertEqual(status, "passed")

    def test_skipped_check_with_failed_lsp_is_failed(self) -> None:
        """check skipped + LSP failed → 整体 failed（LSP 错误是实锤）。"""
        from src.cli.commands.verify import _overall_verify_status

        status = _overall_verify_status(
            changed_files=["src/core.py"],
            risk_level="high",
            missing_checks=[],
            check_payload={"status": "skipped"},
            lsp_payload={"status": "failed"},
            graph_diff_payload={"status": "skipped", "breakingChanges": []},
        )
        self.assertEqual(status, "failed")

    def test_rendered_status_maps_unknown_to_skipped(self) -> None:
        """ai.py 渲染时，check status 'unknown' 应显示为 SKIPPED。"""
        from src.ai import (
            render_verify_report,
        )

        # 构造一个最小 verify payload，check 状态为 "unknown"（边界防护）
        payload = {
            "result": {
                "scanStats": {
                    "listed_source_files": 0,
                    "selected_source_files": 0,
                    "processed_files": 0,
                    "filtered_path_files": 0,
                    "filtered_large_files": 0,
                    "truncated_files": 0,
                    "failed_files": [],
                    "scan_duration_ms": 0,
                    "timeout_triggered": False,
                    "git_failed": False,
                    "symbol_count": 0,
                    "edge_count": 0,
                },
                "status": "passed",
                "changedFiles": [],
                "risk": {"level": "low", "reasons": [], "missingChecks": []},
                "affectedFiles": [],
                "tests": [],
                "untestedSymbols": [],
                "orphanSymbols": [],
                "check": {"status": "unknown", "summary": {}, "runs": [], "errorsByFile": {}},
                "lsp": {
                    "enabled": False,
                    "status": "skipped",
                    "runs": [],
                    "summary": {},
                    "reason": "",
                },
                "graphDiff": {
                    "enabled": False,
                    "status": "skipped",
                    "summary": {},
                    "breakingChanges": [],
                },
                "contractRisks": [],
                "callGraphConsistency": None,
            }
        }
        report = render_verify_report(payload)
        self.assertIn("SKIPPED", report)
        self.assertNotIn("UNKNOWN", report)


class TestP1_5_LogNameOnlyReturncode(unittest.TestCase):
    """P1-5: SubprocessBackend.log_name_only 应检查 returncode。"""

    def test_nonzero_returncode_returns_empty(self) -> None:
        """git log 失败时应返回空列表，而非解析错误输出。"""
        from src.git_backend import SubprocessBackend

        fake_result = MagicMock()
        fake_result.returncode = 128
        fake_result.stdout = "fatal: not a git repository"
        fake_result.stderr = "fatal: not a git repository"

        with patch.object(SubprocessBackend, "_run_git", return_value=fake_result):
            result = SubprocessBackend.log_name_only("/fake/project")
            self.assertEqual(result, [])

    def test_zero_returncode_returns_files(self) -> None:
        """git log 成功时应返回文件列表。"""
        from src.git_backend import SubprocessBackend

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "src/core.py\nsrc/check.py\n"

        with patch.object(SubprocessBackend, "_run_git", return_value=fake_result):
            result = SubprocessBackend.log_name_only("/fake/project")
            self.assertEqual(result, ["src/core.py", "src/check.py"])


class TestP1_7_Pygit2BackendPathValidation(unittest.TestCase):
    """P1-7: Pygit2Backend 的 blame/log/authors 方法应验证路径安全。"""

    def test_blame_line_rejects_path_traversal(self) -> None:
        """路径遍历攻击应被拦截。"""
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.blame_line("/tmp/project", "../../etc/passwd", 1)
        self.assertIsNone(result)

    def test_log_file_commits_rejects_path_traversal(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.log_file_commits("/tmp/project", "../../etc/passwd")
        self.assertEqual(result, [])

    def test_file_authors_rejects_path_traversal(self) -> None:
        from src.git_backend import Pygit2Backend

        result = Pygit2Backend.file_authors("/tmp/project", "../../etc/passwd")
        self.assertEqual(result, [])


class TestP1_8_LowSignalKindsUnified(unittest.TestCase):
    """P1-8: LOW_SIGNAL_KINDS 应在 __init__.py 统一定义。"""

    def test_shared_constant_is_frozenset(self) -> None:
        from src import LOW_SIGNAL_KINDS

        self.assertIsInstance(LOW_SIGNAL_KINDS, frozenset)
        self.assertIn("element", LOW_SIGNAL_KINDS)
        self.assertIn("json_key", LOW_SIGNAL_KINDS)

    def test_ranking_uses_shared_constant(self) -> None:
        # LOW_SIGNAL_KINDS 仅在需要它的模块中导入（如 topic.py），
        # 不再通过 ranking.py 的类属性重新导出。
        from src import LOW_SIGNAL_KINDS as shared
        from src.topic import LOW_SIGNAL_KINDS as topic_copy

        self.assertIs(topic_copy, shared)

    def test_topic_uses_shared_constant(self) -> None:
        from src import LOW_SIGNAL_KINDS as shared
        from src import topic

        self.assertIs(topic.LOW_SIGNAL_KINDS, shared)


class TestP1_9_SignalWeightUnified(unittest.TestCase):
    """P1-9: signal_weight 应使用统一实现。"""

    def test_shared_function_exists(self) -> None:
        from src import signal_weight_for_symbol

        self.assertEqual(signal_weight_for_symbol("element", "x", "private"), 0.002)
        self.assertEqual(signal_weight_for_symbol("class", "__init__", "public"), 0.35)
        self.assertEqual(
            signal_weight_for_symbol("function", "_helper", "private"), 0.85
        )
        self.assertEqual(signal_weight_for_symbol("class", "MyClass", "exported"), 1.0)


class TestP1_10_FindChildByTypeUnified(unittest.TestCase):
    """P1-10: _find_child_by_type 应在 __init__.py 统一定义。"""

    def test_shared_function_exists(self) -> None:
        from src import find_child_by_type, find_children_by_type

        node = MagicMock()
        child1 = MagicMock(type="identifier")
        child2 = MagicMock(type="parameters")
        child3 = MagicMock(type="identifier")
        node.children = [child1, child2, child3]

        self.assertIs(find_child_by_type(node, "identifier"), child1)
        self.assertIsNone(find_child_by_type(node, "nonexistent"))
        self.assertEqual(find_children_by_type(node, "identifier"), [child1, child3])

    def test_type_inference_uses_shared(self) -> None:
        """type_inference.py 的 _find_child_by_type 应来自共享模块。"""
        from src import find_child_by_type as shared
        from src.type_inference import _find_child_by_type

        self.assertIs(_find_child_by_type, shared)

    def test_callgraph_uses_shared(self) -> None:
        """callgraph.py 的 _find_child_by_type 应来自共享模块。"""
        from src import find_child_by_type as shared
        from src.callgraph import _find_child_by_type

        self.assertIs(_find_child_by_type, shared)


class TestP1_11_BundlerConfigsRemoved(unittest.TestCase):
    """P1-11: BUNDLER_CONFIGS 死代码应已删除。"""

    def test_bundler_configs_not_in_resolver(self) -> None:
        import src.resolver as resolver

        self.assertFalse(hasattr(resolver, "BUNDLER_CONFIGS"))


if __name__ == "__main__":
    unittest.main()
