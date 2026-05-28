"""Issue #36 P2 回归测试 — session cache版本校验、重复函数消除、depth上界、verify输出截断

每个测试对应 Issue #36 中的一个 P2 问题，修复前必须失败。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestP2_9_SessionCacheVersionValidation(unittest.TestCase):
    """P2-9: session cache 应随 repomap 版本升级自动失效。"""

    def test_session_payload_includes_repomap_version(self) -> None:
        from src.cli.handlers import _engine_to_session_payload

        engine = MagicMock()
        engine.scan_state = "scanned"
        engine.graph.symbols.values.return_value = []
        engine.graph.outgoing = {}
        engine.graph.file_symbols = {}
        engine.graph.file_imports = {}
        engine.graph.file_calls = {}
        engine.graph.file_import_bindings = {}
        engine.graph.file_exports = {}
        engine.routes = []
        engine.scan_stats = MagicMock()
        engine.scan_stats.listed_source_files = 0
        engine.scan_stats.selected_source_files = 0
        engine.scan_stats.processed_files = 0
        engine.scan_stats.filtered_path_files = 0
        engine.scan_stats.filtered_large_files = 0
        engine.scan_stats.truncated_files = 0
        engine.scan_stats.failed_files = []
        engine.scan_stats.scan_duration_ms = 0
        engine.scan_stats.timeout_triggered = False
        engine.scan_stats.skipped_files = 0

        payload = _engine_to_session_payload("/tmp/test", "fp123", engine)

        self.assertIn(
            "repomap_version",
            payload,
            "session cache payload 必须包含 repomap_version 字段，版本升级时自动失效",
        )

    def test_restore_rejects_version_mismatch(self) -> None:
        from src.cli.handlers import (
            _restore_engine_from_session_payload,
            SESSION_CACHE_VERSION,
        )

        payload = {
            "version": SESSION_CACHE_VERSION,
            "repomap_version": "0.0.1-old",
            "project_root": "/tmp/test",
            "scan_state": "scanned",
            "scan_stats": {
                "listed_source_files": 0,
                "selected_source_files": 0,
                "processed_files": 0,
                "filtered_path_files": 0,
                "filtered_large_files": 0,
                "truncated_files": 0,
                "failed_files": [],
                "scan_duration_ms": 0,
                "timeout_triggered": False,
                "skipped_files": 0,
            },
            "symbols": [],
            "outgoing": {},
            "file_symbols": {},
            "file_imports": {},
            "file_calls": {},
            "file_import_bindings": {},
            "file_exports": {},
            "routes": [],
        }

        with patch("src.cli.handlers.get_repomap_version", return_value="2.6.0"):
            result = _restore_engine_from_session_payload(payload)

        self.assertIsNone(
            result,
            "repomap 版本不匹配时必须丢弃 session cache，防止反序列化错误",
        )


class TestP2_10_ReadMaxFileBytesDedup(unittest.TestCase):
    """P2-10: _read_max_file_bytes 不应重复定义。"""

    def test_handlers_uses_core_static_method(self) -> None:
        import src.cli.handlers as handlers_mod

        self.assertFalse(
            hasattr(handlers_mod, "_read_max_file_bytes"),
            "handlers.py 不应有独立的 _read_max_file_bytes 定义，应引用 core.py 的静态方法",
        )

    def test_handlers_calls_core_method(self) -> None:
        from src.core import RepoMapEngine

        with patch.object(
            RepoMapEngine, "_read_max_file_bytes", return_value=999
        ) as mock_method:
            with tempfile.TemporaryDirectory() as tmpdir:
                Path(tmpdir, "app.py").write_text("def foo(): pass\n")

                from src.cli.handlers import _scan_fingerprint

                _scan_fingerprint(tmpdir, 8000)
                mock_method.assert_called()


class TestP2_11_DepthUpperBound(unittest.TestCase):
    """P2-11: --depth 参数应有上界校验。"""

    def test_call_chain_clamps_excessive_depth(self) -> None:
        from src.cli.commands.symbol import run_call_chain

        engine = MagicMock()
        selected = MagicMock()
        selected.id = "sym1"
        selected.name = "foo"
        selected.kind = "function"
        selected.file = "a.py"
        selected.line = 1
        selected.signature = "def foo()"
        selected.pagerank = 0.5
        engine.call_chain.return_value = {"callers": [], "callees": []}

        with (
            patch("src.cli.commands.symbol._scan_engine", return_value=engine),
            patch(
                "src.cli.commands.symbol._select_symbol_match",
                return_value=(selected, None, "fuzzy"),
            ),
        ):
            exit_code = run_call_chain(
                project="/tmp/test",
                max_files=8000,
                symbol="foo",
                file_path=None,
                direction="both",
                depth=999999,
                max_chars=4000,
                as_json=True,
            )

        _ = exit_code

        call_args = engine.call_chain.call_args
        actual_depth = (
            call_args[0][2]
            if len(call_args[0]) > 2
            else call_args[1].get("depth", 999999)
        )

        self.assertLessEqual(
            actual_depth,
            10,
            "call-chain --depth 超过 10 应被 clamp，防止遍历过深",
        )

    def test_impact_clamps_excessive_depth(self) -> None:
        from src.cli.commands.impact import run_impact

        with (
            patch("src.cli.commands.impact._scan_engine") as mock_scan,
            patch(
                "src.cli.commands.impact._normalize_project_relative_paths",
                return_value=["a.py"],
            ),
            patch("src.cli.commands.impact.find_related_tests", return_value=[]),
            patch("src.cli.commands.impact._assess_risk", return_value=("low", [])),
            patch("src.cli.commands.impact._impact_type_level", return_value=[]),
            patch("src.cli.commands.impact._scan_stats_payload", return_value={}),
        ):
            engine = MagicMock()
            engine.project_root = "/tmp/test"
            engine.graph.symbols = {}
            engine.graph.file_symbols = {}
            engine.graph.incoming = {}
            engine.graph.outgoing = {}
            engine.file_analysis.return_value = {}
            mock_scan.return_value = engine

            with tempfile.TemporaryDirectory() as tmpdir:
                exit_code = run_impact(
                    project=tmpdir,
                    max_files=8000,
                    target_files=["a.py"],
                    max_affected_files=20,
                    as_json=True,
                    with_symbols=False,
                    depth=999999,
                    incremental=False,
                )

        self.assertEqual(
            exit_code,
            0,
            "impact --depth 超过上限应被 clamp 后正常执行",
        )


class TestP2_12_VerifyOutputMaxChars(unittest.TestCase):
    """P2-12: verify 文本输出应有大小限制且可通过 CLI 参数控制。"""

    def test_verify_accepts_max_chars_arg(self) -> None:
        from src.cli.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "verify",
                "--project",
                "/tmp/test",
                "--max-chars",
                "8000",
            ]
        )
        self.assertTrue(
            hasattr(args, "max_chars"),
            "verify 命令应支持 --max-chars 参数",
        )
        self.assertEqual(
            args.max_chars,
            8000,
            "--max-chars 参数值应正确解析",
        )

    def test_verify_passes_max_chars_to_render(self) -> None:
        from src.cli.commands.verify import run_verify

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "app.py").write_text("def foo(): pass\n")

            with (
                patch("src.cli.commands.verify._scan_engine") as mock_scan,
                patch("src.cli.commands.verify.GitBackend") as mock_git_cls,
                patch(
                    "src.cli.commands.verify.render_verify_report",
                    return_value="report",
                ) as mock_render,
                patch(
                    "src.cli.commands.verify._overall_verify_status",
                    return_value="passed",
                ),
            ):
                mock_engine = MagicMock()
                mock_engine.graph.symbols = {}
                mock_engine.graph.file_symbols = {}
                mock_engine.graph.incoming = {}
                mock_engine.graph.outgoing = {}
                mock_engine.scan_state = "scanned"
                mock_scan.return_value = mock_engine

                mock_git = MagicMock()
                mock_git.changed_files.return_value = []
                mock_git.deleted_files.return_value = []
                mock_git_cls.return_value = mock_git

                run_verify(
                    project=tmpdir,
                    as_json=False,
                    types=None,
                    max_issues=50,
                    resolve_symbols=False,
                    
                    lsp_timeout=8.0,
                    lsp_max_files=20,
                    with_diff=False,
                    quick=False,
                    incremental=False,
                    max_chars=8000,
                )

                call_kwargs = mock_render.call_args
                has_max_chars = (
                    "max_chars" in call_kwargs.kwargs if call_kwargs.kwargs else False
                )
                self.assertTrue(
                    has_max_chars,
                    "run_verify 应将 max_chars 传递给 render_verify_report",
                )


if __name__ == "__main__":
    unittest.main()
