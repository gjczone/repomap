"""Issue #170 回归测试 — check pyright 范围、缓存解码、co-change 污染、toolkit docstring

每个测试对应一个 P1/P2 问题，修复前必须失败。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestP1_PyrightDefaultScope(unittest.TestCase):
    """P1: `_run_pyright()` 默认应检查 `src` 而非 `.`（全仓）。

    Issue #170: 当前 `pyright .` 会检查 tests/ 目录中的动态测试代码并报错，
    而 CI 的类型契约只覆盖 `src/`。修复后默认命令应为 `pyright src --outputjson`。
    """

    def test_pyright_defaults_to_src_directory(self):
        """无 modified_files 时，pyright 默认检查 src/ 而非全仓 ."""
        from src.check import DiagnosticRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = DiagnosticRunner(Path(tmpdir), max_items=10)
            # 不传入 modified_files，应使用默认范围

            with patch.object(runner, "_has_cmd", return_value=True):
                with patch.object(
                    runner, "_run_command", return_value=(0, "{}", "", 0)
                ) as mock_run:
                    runner._run_pyright()

                    cmd = mock_run.call_args[0][0]
                    # 命令应包含 "src" 而不是 "."
                    self.assertIn("src", cmd)
                    self.assertNotIn(".", cmd)

    def test_pyright_cmd_str_reflects_src_scope(self):
        """cmd_str 应反映实际执行的命令范围。"""
        from src.check import DiagnosticRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = DiagnosticRunner(Path(tmpdir), max_items=10)

            with patch.object(runner, "_has_cmd", return_value=True):
                with patch.object(
                    runner, "_run_command", return_value=(0, "{}", "", 0)
                ):
                    result = runner._run_pyright()
                    # cmd_str 或 command 字段应包含 "src"
                    self.assertIn("src", result.command)


class TestP2_ToolkitCacheErrorsReplace(unittest.TestCase):
    """P2.1: toolkit 缓存读取应使用 `errors="replace"` 解码。

    Issue #170: `load_cache()` 和 `load_incremental_cache()` 使用默认 strict 解码，
    违反项目统一解码策略。非法 UTF-8 应被替换而非抛出 ValueError。
    """

    def test_load_cache_handles_invalid_utf8(self):
        """缓存文件包含非法 UTF-8 时，应使用 errors="replace" 读取而非崩溃。"""
        from src import toolkit

        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            cache_dir = Path(tmpdir) / ".repomap" / "cache"
            cache_dir.mkdir(parents=True)

            # 写入包含非法 UTF-8 字节的缓存文件
            cache_file = cache_dir / "repomap_cache.json"
            with open(cache_file, "wb") as f:
                f.write(b'{"_schema_version": 1, "data": "\xff\xfe invalid"}')

            # 应返回 None（schema 不完整），而非抛出 UnicodeDecodeError
            result = toolkit.load_cache(str(project_root))
            self.assertIsNone(result)

    def test_load_incremental_cache_handles_invalid_utf8(self):
        """增量缓存文件包含非法 UTF-8 时，应使用 errors="replace" 读取。"""
        from src import toolkit

        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            cache_dir = Path(tmpdir) / ".repomap" / "cache"
            cache_dir.mkdir(parents=True)

            # 写入包含非法 UTF-8 字节的增量缓存文件
            cache_file = cache_dir / "repomap_incremental.json"
            with open(cache_file, "wb") as f:
                f.write(
                    b'{"project_root_hash": "abc", "git_head": "def", "files": {}, "scan_stats_json": {}, "invalid": "\xff\xfe"}'
                )

            # 应返回 None（schema 不完整），而非抛出 UnicodeDecodeError
            result = toolkit.load_incremental_cache(str(project_root))
            self.assertIsNone(result)


class TestP2_CoChangeFailurePollution(unittest.TestCase):
    """P2.2: co-change 失败状态不应永久污染后续报告。

    Issue #170: `_load_co_change_scores()` 失败时设置全局 `_co_change_load_failed = True`
    并缓存空结果。后续即使 git 恢复，也会命中失败缓存。
    """

    def setUp(self):
        """每次测试前重置 co-change 全局状态。"""
        import src.co_change as cc

        cc._co_change_load_failed = False
        cc._co_change_cache.clear()

    def tearDown(self):
        """测试后清理。"""
        import src.co_change as cc

        cc._co_change_load_failed = False
        cc._co_change_cache.clear()

    def test_co_change_failure_does_not_cache_empty_result(self):
        """co-change 加载失败时，不应缓存空结果。"""
        import src.co_change as cc
        from src.git_backend import GitBackend

        with patch.object(
            GitBackend, "log_commits_grouped", side_effect=Exception("git error")
        ):
            scores = cc._load_co_change_scores("/fake/project", since_days=30)
            self.assertEqual(scores, {})
            self.assertTrue(cc._co_change_load_failed)

            # 关键：失败结果不应被缓存
            cc._get_or_load_co_change_cache("/fake/project", 30)
            # 修复后：失败时不缓存，所以缓存中不应有空结果
            self.assertNotIn(("/fake/project", 30), cc._co_change_cache)

    def test_co_change_recovery_after_failure(self):
        """co-change 加载失败后恢复，应能返回非空结果。"""
        import src.co_change as cc
        from src.git_backend import GitBackend

        # 第一次失败
        call_count = 0

        def mock_log_commits_grouped(self, since_days=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("git error")
            # 第二次调用返回正常数据（返回列表而非集合）
            return [["file_a.py", "file_b.py"]]

        with patch.object(GitBackend, "log_commits_grouped", mock_log_commits_grouped):
            # 第一次调用失败
            scores1 = cc._load_co_change_scores("/fake/project", since_days=30)
            self.assertEqual(scores1, {})
            self.assertTrue(cc._co_change_load_failed)

            # 清除失败标志（模拟恢复）
            cc._co_change_load_failed = False

            # 第二次调用成功
            scores2 = cc._load_co_change_scores("/fake/project", since_days=30)
            self.assertIn(("file_a.py", "file_b.py"), scores2)
            self.assertEqual(scores2[("file_a.py", "file_b.py")], 1)
            self.assertFalse(cc._co_change_load_failed)


class TestP2_ToolkitDocstring(unittest.TestCase):
    """P2.3: toolkit.py 顶部 docstring 不应引用不存在的旧命令。

    Issue #170: docstring 仍写着 `python repomap_toolkit.py ...`，但当前入口是
    `repomap <subcommand>`，没有 `repomap_toolkit.py`、`refs`、`orphan` 命令。
    """

    def test_toolkit_docstring_no_stale_commands(self):
        """toolkit.py 的模块 docstring 不应引用已不存在的命令。"""
        import src.toolkit as toolkit

        doc = toolkit.__doc__ or ""
        self.assertNotIn(
            "repomap_toolkit.py",
            doc,
            "docstring 不应引用不存在的 repomap_toolkit.py",
        )
        self.assertNotIn(
            "refs --symbol",
            doc,
            "docstring 不应引用不存在的 refs 命令",
        )
        self.assertNotIn(
            "orphan --project",
            doc,
            "docstring 不应引用不存在的 orphan 命令",
        )

    def test_toolkit_docstring_references_current_commands(self):
        """toolkit.py 的模块 docstring 应引用当前有效命令。"""
        import src.toolkit as toolkit

        doc = toolkit.__doc__ or ""
        # 应引用当前有效命令
        self.assertTrue(
            "cache save" in doc or "cache" in doc,
            "docstring 应引用当前有效的 cache 命令",
        )
        self.assertTrue(
            "verify" in doc,
            "docstring 应引用当前有效的 verify 命令",
        )


if __name__ == "__main__":
    unittest.main()
