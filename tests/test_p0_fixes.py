"""P0 bug 回归测试 — issue #33

每个测试对应一个 P0 功能性 Bug，修复前必须失败。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import json_dumps


class P0_1_OrjsonJsonDumps(unittest.TestCase):
    """P1-2: json_dumps wrapper 不应设置 OPT_NON_STR_KEYS（语义错误）。

    orjson 使用显式参数替代 **kwargs，避免静默丢弃不支持的参数。
    原 bug 是错误地设置了 OPT_NON_STR_KEYS（允许非字符串 key，产生非法 JSON）。
    """

    def test_indent_does_not_set_non_str_keys(self) -> None:
        """indent=2 应设置 OPT_INDENT_2 但不设置 OPT_NON_STR_KEYS。"""
        try:
            import orjson  # noqa: F811
        except ImportError:
            self.skipTest("orjson not installed")
            return

        captured_options: list[int] = []
        original_dumps = orjson.dumps

        def spy_dumps(obj: object, option: int = 0) -> bytes:
            captured_options.append(option)
            return original_dumps(obj, option=option)

        with patch.object(orjson, "dumps", side_effect=spy_dumps):
            json_dumps({"name": "中文"}, indent=2)

        self.assertTrue(len(captured_options) > 0, "orjson.dumps 应被调用")
        used_option = captured_options[0]
        # OPT_INDENT_2 应被设置
        self.assertTrue(
            bool(used_option & orjson.OPT_INDENT_2),
            f"indent=2 应设置 OPT_INDENT_2 (option={used_option})",
        )
        # OPT_NON_STR_KEYS 不应被设置 — 它允许 int key，这是错误行为
        self.assertFalse(
            bool(used_option & orjson.OPT_NON_STR_KEYS),
            f"indent=2 不应设置 OPT_NON_STR_KEYS (option={used_option})",
        )


class P0_2_FormatSymbolRefDanglingId(unittest.TestCase):
    """P0-2: _format_symbol_ref 对不存在的 symbol id 不应 KeyError 崩溃。"""

    def test_dangling_symbol_id_returns_placeholder(self) -> None:
        from src.cli.handlers import _format_symbol_ref
        from src.core import RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RepoMapEngine(tmpdir)
            # 不添加任何 symbol，直接查询不存在的 id
            result = _format_symbol_ref(engine, "nonexistent_symbol_id")

            # 应返回占位符而非抛出 KeyError
            self.assertEqual(result["name"], "?")
            self.assertEqual(result["file"], "?")
            self.assertEqual(result["line"], 0)


class P0_3_CoChangeCacheKeyIncludesSinceDays(unittest.TestCase):
    """P0-3: co_change 缓存键必须包含 since_days，不同时间窗口不能复用缓存。"""

    def test_different_since_days_produces_different_cache_entries(self) -> None:
        from src import co_change

        # 清空缓存，确保干净测试
        original_cache = co_change._co_change_cache.copy()
        co_change._co_change_cache.clear()

        try:
            call_log: list[int] = []

            def fake_load(project_root: str, since_days: int = 30) -> dict:
                call_log.append(since_days)
                return {("a.py", "b.py"): since_days}

            with patch.object(co_change, "_load_co_change_scores", side_effect=fake_load):
                # 第一次调用 since_days=30
                score_30 = co_change.get_co_change_score(
                    "/fake/project", "a.py", "b.py", since_days=30
                )
                # 第二次调用 since_days=90 — 必须重新加载
                score_90 = co_change.get_co_change_score(
                    "/fake/project", "a.py", "b.py", since_days=90
                )

            # 两次调用的 since_days 不同，_load_co_change_scores 必须被调用两次
            self.assertEqual(len(call_log), 2, "不同 since_days 应该触发独立的缓存加载")
            self.assertEqual(call_log, [30, 90])
            self.assertEqual(score_30, 30)
            self.assertEqual(score_90, 90)
        finally:
            co_change._co_change_cache.clear()
            co_change._co_change_cache.update(original_cache)


class P0_4_TypescriptFallbackWarning(unittest.TestCase):
    """P0-4: TypeScript 回退到 JavaScript 解析器时必须发出警告。"""

    def test_adapter_has_fallback_langs_tracking(self) -> None:
        """TreeSitterAdapter 必须有 _fallback_langs 属性来跟踪降级语言。"""
        from src.parser import TreeSitterAdapter

        adapter = TreeSitterAdapter()
        self.assertTrue(
            hasattr(adapter, "_fallback_langs"),
            "TreeSitterAdapter 必须有 _fallback_langs 属性来记录哪些语言使用了回退解析器",
        )
        self.assertIsInstance(adapter._fallback_langs, set)


class P0_5_ReadMaxFileBytesMinimum(unittest.TestCase):
    """P0-5: _read_max_file_bytes 最小返回值应为 1，不允许 0。"""

    def test_zero_env_var_returns_minimum_one(self) -> None:
        from src.core import RepoMapEngine

        with patch.dict(os.environ, {"REPOMAP_MAX_FILE_BYTES": "0"}):
            result = RepoMapEngine._read_max_file_bytes()
            self.assertGreaterEqual(
                result, 1, "REPOMAP_MAX_FILE_BYTES=0 时应返回至少 1，防止跳过所有文件"
            )

    def test_negative_env_var_returns_minimum_one(self) -> None:
        from src.core import RepoMapEngine

        with patch.dict(os.environ, {"REPOMAP_MAX_FILE_BYTES": "-100"}):
            result = RepoMapEngine._read_max_file_bytes()
            self.assertGreaterEqual(result, 1)


class P0_6_RestoreEngineInvalidState(unittest.TestCase):
    """P0-6: scan_state 为 'invalid' 的引擎不应被直接丢弃。"""

    def test_invalid_state_engine_is_restored_with_warning(self) -> None:
        from src import SESSION_CACHE_VERSION
        from src.cli.handlers import _restore_engine_from_session_payload

        # 构造一个最小化的有效 payload，scan_state 为 "invalid"
        payload = {
            "version": SESSION_CACHE_VERSION,
            "project_root": "/fake/project",
            "scan_state": "invalid",
            "symbols": [],
            "outgoing": {},
            "file_symbols": {},
            "file_imports": {},
            "file_calls": {},
            "file_import_bindings": {},
            "file_exports": {},
            "scan_stats": {},
            "routes": [],
        }

        result = _restore_engine_from_session_payload(payload)
        # 应返回 engine 而非 None
        self.assertIsNotNone(
            result, "scan_state='invalid' 的引擎不应被丢弃，数据已完整反序列化"
        )


class P0_7_EslintCommandSeparator(unittest.TestCase):
    """P0-7: eslint 命令行必须在文件列表前使用 '--' 分隔符。"""

    def test_eslint_command_includes_separator(self) -> None:
        """验证 eslint 命令包含 '--' 分隔符防止参数注入。"""
        captured_commands: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> MagicMock:
            captured_commands.append(cmd)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一些 .js 文件
            Path(tmpdir, "app.js").write_text("var x = 1;\n")
            Path(tmpdir, "util.js").write_text("var y = 2;\n")

            with patch("subprocess.run", side_effect=fake_run):
                from src.cli.commands.fix import run_fix

                run_fix(tmpdir)

        # 找到 eslint 命令
        eslint_cmds = [cmd for cmd in captured_commands if cmd and cmd[0] == "eslint"]
        if eslint_cmds:
            cmd = eslint_cmds[0]
            # '--' 必须在文件列表之前
            self.assertIn("--", cmd, "eslint 命令必须包含 '--' 分隔符")
            separator_idx = cmd.index("--")
            # '--' 之后应该是文件路径
            files_after_separator = cmd[separator_idx + 1 :]
            self.assertTrue(
                all(
                    f.endswith((".js", ".ts", ".jsx", ".tsx"))
                    for f in files_after_separator
                ),
                "'--' 之后应该都是文件路径",
            )


class P0_8_StderrNoiseRegression(unittest.TestCase):
    """P0-8: stderr 噪音 — parser/nesting/scan 日志不应每次命令都输出到 stderr。

    Issue #144: 每次 repomap 命令都在 stderr 输出 ~4 条 parser unavailable 警告、
    N 条 extreme nesting risk 警告，以及扫描进度 INFO 日志，污染 LLM 上下文窗口。
    """

    def test_basic_config_level_is_warning_not_info(self) -> None:
        """src/core.py: 库代码应使用 NullHandler 而非 basicConfig。

        修复前：basicConfig(level=logging.WARNING) 在模块导入时配置日志
        修复后：NullHandler — 库层不配置，应用层自行决定日志策略

        使用 inspect.getsource 直接检查源码，避免依赖运行时 root logger 状态。
        """
        import inspect

        from src import core as core_module

        source = inspect.getsource(core_module)
        self.assertIn(
            "NullHandler",
            source,
            "src/core.py 应使用 NullHandler（库层不自行配置日志）",
        )
        self.assertNotIn(
            "basicConfig",
            source,
            "src/core.py 不应调用 basicConfig（库代码不配置日志输出）",
        )

    def test_parser_unavailable_logs_as_info_not_warning(self) -> None:
        """parser.py: 'Parser unavailable' 消息应为 logger.info，非 logger.warning。

        修复前：logger.warning → 每次命令都输出 ~4 条噪音
        修复后：logger.info → 仅在显式开启 INFO 级别时可见
        """
        import inspect

        from src import parser as parser_module

        source = inspect.getsource(parser_module)
        # 找到包含 "Parser unavailable" 的行并确认上下文使用 logger.info
        lines = source.split("\n")
        found = False
        for i, line in enumerate(lines):
            if "Parser unavailable" in line:
                found = True
                # 检查前几行是否有 logger.warning（不应存在）
                context_before = "\n".join(lines[max(0, i - 2) : i])
                context = context_before + "\n" + line
                self.assertNotIn(
                    "logger.warning",
                    context,
                    f"Parser unavailable 消息应使用 logger.info 而非 logger.warning:\n{context}",
                )
                self.assertIn(
                    "logger.info",
                    context,
                    f"Parser unavailable 消息应使用 logger.info:\n{context}",
                )
        self.assertTrue(found, "未找到 'Parser unavailable' 日志调用")

    def test_nesting_risk_logs_as_info_not_warning(self) -> None:
        """parser.py: 'Extreme nesting risk' 消息应为 logger.info，非 logger.warning。

        修复前：logger.warning → 每次扫描大型/嵌套文件时输出噪音
        修复后：logger.info → 跳过信息已在 scan_stats 中记录
        """
        import inspect

        from src import parser as parser_module

        source = inspect.getsource(parser_module)
        lines = source.split("\n")
        found = False
        for i, line in enumerate(lines):
            if "Extreme nesting risk" in line:
                found = True
                context_before = "\n".join(lines[max(0, i - 2) : i])
                context = context_before + "\n" + line
                self.assertNotIn(
                    "logger.warning",
                    context,
                    f"Extreme nesting risk 消息应使用 logger.info 而非 logger.warning:\n{context}",
                )
                self.assertIn(
                    "logger.info",
                    context,
                    f"Extreme nesting risk 消息应使用 logger.info:\n{context}",
                )
        self.assertTrue(found, "未找到 'Extreme nesting risk' 日志调用")


if __name__ == "__main__":
    unittest.main()
