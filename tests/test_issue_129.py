"""Issue #129 — git_backend 异常分类 + _last_error 完善

测试目标：
  SubprocessBackend: 使用 _SUBPROCESS_EXPECTED 常量区分预期/意外异常
  Pygit2Backend:    使用 _PYGIT2_ERRORS 常量区分预期/意外异常
  GitBackend:       所有委托方法在失败时设置 _last_error
"""

from __future__ import annotations

import inspect
import unittest


class TestPygit2BackendErrorClassification(unittest.TestCase):
    """Pygit2Backend 每个方法都应区分预期异常和意外异常。"""

    _PYGIT2_METHODS = [
        "rev_parse_head",
        "show_toplevel",
        "changed_files",
        "deleted_files",
        "diff_name_only",
        "diff_cached_name_only",
        "status_porcelain",
        "log_name_only",
        "log_commits_grouped",
        "diff_name_only_since",
        "blame_line",
        "log_file_commits",
        "file_authors",
    ]

    def test_all_pygit2_methods_use_pygit2_errors(self) -> None:
        """每个 Pygit2Backend 方法都应引用 _PYGIT2_ERRORS 或 pygit2.GitError。"""
        import src.git_backend as gb

        for method_name in self._PYGIT2_METHODS:
            method = getattr(gb.Pygit2Backend, method_name)
            source = inspect.getsource(method)

            with self.subTest(method=method_name):
                self.assertTrue(
                    "_PYGIT2_ERRORS" in source or "pygit2.GitError" in source,
                    f"{method_name}: 应使用 _PYGIT2_ERRORS 或 pygit2.GitError",
                )
                self.assertIn(
                    "logger.warning",
                    source,
                    f"{method_name}: 预期异常应 logger.warning",
                )
                self.assertIn(
                    "except Exception",
                    source,
                    f"{method_name}: 应有 except Exception 兜底",
                )
                self.assertIn(
                    "logger.error",
                    source,
                    f"{method_name}: 意外异常应 logger.error",
                )


class TestGitBackendLastError(unittest.TestCase):
    """GitBackend 所有委托方法应在异常时设置 self._last_error。"""

    _GITBACKEND_METHODS = [
        "rev_parse_head",
        "show_toplevel",
        "changed_files",
        "deleted_files",
        "diff_name_only",
        "diff_cached_name_only",
        "status_porcelain",
        "log_name_only",
        "log_commits_grouped",
        "diff_name_only_since",
        "blame_line",
        "log_file_commits",
        "file_authors",
    ]

    def test_all_gitbackend_methods_set_last_error(self) -> None:
        """GitBackend 每个委托方法都应在异常时设置 self._last_error。"""
        import src.git_backend as gb

        for method_name in self._GITBACKEND_METHODS:
            method = getattr(gb.GitBackend, method_name)
            source = inspect.getsource(method)

            with self.subTest(method=method_name):
                self.assertIn(
                    "_last_error",
                    source,
                    f"{method_name}: 应在异常时设置 self._last_error",
                )
                self.assertIn(
                    "except",
                    source,
                    f"{method_name}: 应包含 try/except 包裹",
                )

    def test_last_error_initialized(self) -> None:
        """GitBackend.__init__ 应初始化 _last_error 属性。"""
        import src.git_backend as gb

        source = inspect.getsource(gb.GitBackend.__init__)
        self.assertIn("_last_error", source)
        self.assertIn("None", source)


class TestSubprocessBackendErrorClassification(unittest.TestCase):
    """SubprocessBackend 每个方法都应使用 _SUBPROCESS_EXPECTED 常量。"""

    _SUBPROCESS_METHODS = [
        "rev_parse_head",
        "show_toplevel",
        "changed_files",
        "deleted_files",
        "diff_name_only",
        "diff_cached_name_only",
        "status_porcelain",
        "log_name_only",
        "diff_name_only_since",
        "log_commits_grouped",
        "blame_line",
        "log_file_commits",
        "file_authors",
    ]

    def test_all_subprocess_methods_use_expected_errors(self) -> None:
        """每个 SubprocessBackend 方法都应使用 _SUBPROCESS_EXPECTED 常量。"""
        import src.git_backend as gb

        for method_name in self._SUBPROCESS_METHODS:
            method = getattr(gb.SubprocessBackend, method_name)
            source = inspect.getsource(method)

            with self.subTest(method=method_name):
                self.assertTrue(
                    "_SUBPROCESS_EXPECTED" in source or "FileNotFoundError" in source,
                    f"{method_name}: 应使用 _SUBPROCESS_EXPECTED 常量",
                )
                self.assertIn(
                    "logger.warning",
                    source,
                    f"{method_name}: 预期异常应 logger.warning",
                )
                self.assertIn(
                    "logger.error",
                    source,
                    f"{method_name}: 意外异常应 logger.error",
                )
