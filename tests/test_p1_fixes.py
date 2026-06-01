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
                "check": {
                    "status": "unknown",
                    "summary": {},
                    "runs": [],
                    "errorsByFile": {},
                },
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


class TestP1_12_QueryMatchedLinesRemoved(unittest.TestCase):
    """P1-12: query --query 输出中 Matched Lines 区域应移除，减少 token 消耗。

    Issue #148: Matched Lines 输出源代码片段，消耗 ~40% token 但 LLM
    已从 Core Files/Key Symbols 表格获取足够信息。需要源码时 LLM 会
    使用 query --file 或 read 工具。
    """

    def test_render_query_report_has_no_matched_lines(self) -> None:
        """render_query_report 输出不应包含 'Matched Lines' 标题。"""
        from src.ai import render_query_report
        from src.core import RepoMapEngine
        from src.topic import FileMatch

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RepoMapEngine(tmpdir)
            fm = FileMatch(
                path="src/core.py",
                role="core",
                score=95.0,
                reasons=["keyword match"],
            )
            report = render_query_report(
                engine=engine,
                query="test",
                file_matches=[fm],
                tests=[],
                max_files=10,
                max_symbols=10,
                max_chars=12000,
                context_lines=2,
            )
            self.assertNotIn(
                "Matched Lines",
                report,
                "query 文本输出不应包含 'Matched Lines' 区域（源代码片段由 LLM 按需获取）",
            )


class TestP1_13_CallChainCallerGrouping(unittest.TestCase):
    """P1-13: call-chain caller 应按实现/测试分组。

    Issue #147: Callers 列表中实现代码和测试代码混在一起，
    LLM 需要看到实现 caller 优先展示，测试 caller 只展示总数 + top-3。
    """

    def test_callers_grouped_by_impl_and_test(self) -> None:
        """call-chain 输出应将 caller 分为 Implementation Callers 和 Test Callers。"""
        from unittest.mock import MagicMock

        from src.ai import render_call_chain_report
        from src import Symbol

        engine = MagicMock()
        engine.project_root = "/fake/project"

        # 构造 symbol（被调用的函数）
        target = Symbol(
            id="target",
            name="scan",
            kind="function",
            file="src/core.py",
            line=100,
            pagerank=0.05,
            signature="def scan():",
        )
        engine.query_symbol.return_value = [target]

        # 构造 callers: 15 个实现 callers + 10 个测试 callers
        impl_callers = [
            Symbol(
                id=f"caller_impl_{i}",
                name=f"impl_func_{i}",
                kind="function",
                file=f"src/module_{i}.py",
                line=10 * i,
            )
            for i in range(15)
        ]
        test_callers = [
            Symbol(
                id=f"caller_test_{i}",
                name=f"test_func_{i}",
                kind="function",
                file=f"tests/test_module_{i}.py",
                line=20 * i,
            )
            for i in range(10)
        ]

        engine.call_chain.return_value = {
            "callers": impl_callers + test_callers,
            "callees": [],
        }
        engine.confidence_for.return_value = 1.0

        report = render_call_chain_report(engine, "scan")

        self.assertIn("Implementation Callers", report)
        self.assertIn("Test Callers", report)
        self.assertIn("15", report)  # impl count
        self.assertIn("10", report)  # test count


class TestP1_14_RouteConsumerExcludesOwnFile(unittest.TestCase):
    """P1-14: route consumer 检测应排除路由定义文件自身。

    Issue #145: routes --with-consumers 将 decorator 行也作为 consumer，
    实际需要的是前端/测试代码中的 API 调用。
    """

    def test_route_handler_file_not_reported_as_consumer(self) -> None:
        """路由 handler 文件自身不应被列为 consumer（那是路由定义）。"""
        import tempfile
        from pathlib import Path

        from src.consumers import find_route_consumers
        from src import HttpRoute

        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            # 创建后端路由文件（包含 FastAPI route decorator）
            backend_file = project_root / "backend" / "routes.py"
            backend_file.parent.mkdir(parents=True, exist_ok=True)
            backend_file.write_text(
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
                '@router.get("/api/users")\n'
                "def get_users():\n"
                "    return []\n"
            )
            # 创建前端文件（包含 fetch 调用）
            frontend_file = project_root / "frontend" / "api.ts"
            frontend_file.parent.mkdir(parents=True, exist_ok=True)
            frontend_file.write_text("const resp = await fetch('/api/users')\n")

            # 初始化 git repo（engine 需要）
            import subprocess

            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(["git", "add", "-A"], cwd=tmpdir, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init", "--no-gpg-sign"],
                cwd=tmpdir,
                capture_output=True,
            )

            from src.core import RepoMapEngine

            engine = RepoMapEngine(str(tmpdir))
            engine.scan(max_files=200)

            # 手动构造 route（模拟 parser 提取的结果）
            routes = [
                HttpRoute(
                    method="GET",
                    path="/api/users",
                    handler="get_users",
                    file="backend/routes.py",
                    line=3,
                    framework="fastapi",
                )
            ]
            consumers = find_route_consumers(engine, routes)

            key = "GET /api/users"
            self.assertIn(key, consumers, f"应找到路由 {key} 的 consumer")

            consumer_files = [c.file for c in consumers.get(key, [])]
            # 后端路由文件自身不应被列为 consumer
            self.assertNotIn(
                "backend/routes.py",
                consumer_files,
                "路由 handler 文件不应被列为 consumer（那是路由定义，不是调用者）",
            )
            # 前端文件应被列为 consumer
            self.assertIn(
                "frontend/api.ts",
                consumer_files,
                "前端 fetch 调用应被检测",
            )


class TestP2_15_ImpactSessionNoise(unittest.TestCase):
    """Issue #150: Impact Session Check 在无 session/过期时不输出噪音"""

    def _make_payload(self, impact_session: dict) -> dict:
        return {
            "result": {
                "status": "passed",
                "changedFiles": ["src/ai.py"],
                "risk": {"level": "low", "reasons": [], "missingChecks": []},
                "check": {
                    "status": "passed",
                    "summary": {"tools_run": 0, "tools_skipped": 0},
                },
                "graphDiff": {"status": "skipped"},
                "impactSession": impact_session,
            }
        }

    def test_skipped_session_not_rendered(self):
        """status=skipped 时不应出现 Impact Session Check section"""
        from src.ai import render_verify_report

        output = render_verify_report(
            self._make_payload(
                {
                    "status": "skipped",
                    "reason": "no session file",
                    "missedFiles": [],
                    "unexpectedFiles": [],
                    "coveredFiles": [],
                    "sessionAgeSeconds": None,
                }
            )
        )
        self.assertNotIn(
            "Impact Session Check",
            output,
            "skipped session 不应渲染 Impact Session Check section",
        )

    def test_expired_session_shows_oneliner(self):
        """session 过期（>300s）时只显示一行提示，不列举文件"""
        from src.ai import render_verify_report

        output = render_verify_report(
            self._make_payload(
                {
                    "status": "missed",
                    "reason": None,
                    "missedFiles": ["src/core.py", "src/parser.py", "src/cli/cli.py"],
                    "unexpectedFiles": [],
                    "coveredFiles": [],
                    "sessionAgeSeconds": 360,
                }
            )
        )
        self.assertIn("expired", output.lower(), "过期 session 应包含 expired 提示")
        self.assertNotIn("src/core.py", output, "过期 session 不应列举文件")

    def test_valid_session_shows_details(self):
        """有效 session 正常展示"""
        from src.ai import render_verify_report

        output = render_verify_report(
            self._make_payload(
                {
                    "status": "ok",
                    "reason": None,
                    "missedFiles": [],
                    "unexpectedFiles": [],
                    "coveredFiles": ["src/ai.py"],
                    "sessionAgeSeconds": 30,
                }
            )
        )
        self.assertIn(
            "Impact Session Check",
            output,
            "有效 session 应渲染 Impact Session Check section",
        )


class TestP2_16_CheckImplDetailsRemoved(unittest.TestCase):
    """Issue #151: check 输出中不应包含实现细节（command line、exit code、duration_ms）"""

    def test_no_impl_details_in_check_output(self):
        """_format_check_report 输出不应包含 Command/Exit code/duration_ms"""
        from src.cli.commands.verify import _format_check_report

        result = {
            "project_root": "/tmp/test",
            "status": "failed",
            "message": None,
            "types": ["python"],
            "timestamp": "2026-01-01T00:00:00Z",
            "summary": {
                "total_errors": 3,
                "total_warnings": 2,
                "files_with_errors": 1,
                "tools_run": 1,
                "tools_skipped": 0,
                "tool_failures": 0,
            },
            "runs": [
                {
                    "tool": "ruff",
                    "command": "ruff check . --output-format=json",
                    "exit_code": 1,
                    "duration_ms": 1234,
                    "error_count": 3,
                    "warning_count": 2,
                    "skipped": False,
                    "truncated": False,
                }
            ],
        }
        output = _format_check_report(result, max_issues=20)

        # 不应包含实现细节
        self.assertNotIn("Command:", output, "不应包含 Command 行")
        self.assertNotIn("Exit code:", output, "不应包含 Exit code 行")
        self.assertNotIn("1234ms", output, "不应包含 duration_ms")
        self.assertNotIn("ruff check", output, "不应包含命令行")

        # 应保留有价值信息
        self.assertIn("ruff", output, "应包含工具名")
        self.assertIn("Failed", output, "应包含状态")
        self.assertIn("Errors: **3**", output, "应包含错误数")
        self.assertIn("Warnings: 2", output, "应包含警告数")


if __name__ == "__main__":
    unittest.main()
