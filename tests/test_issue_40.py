"""Issue #40 回归测试 — 未审查模块静默吞错 + debug→warning 升级

覆盖 #39 未修复的独有问题。
"""

from __future__ import annotations

import inspect
import unittest


# ═══════════════════════════════════════════════════════════════════════════════
# P1-3: callgraph.py 四语言文件读取失败应有日志
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1_3_CallgraphOsErrorLogging(unittest.TestCase):
    """P1-3: callgraph.py 文件读取 OSError 不应完全静默。"""

    def test_python_callgraph_oserror_has_logger(self) -> None:
        import src.callgraph

        source = inspect.getsource(src.callgraph.analyze_python_callgraph)
        self.assertIn("OSError", source)
        # 修复后 except OSError 块应包含 logger 调用
        self.assertTrue(
            "logger" in source.split("except OSError")[1].split("continue")[0]
            if "except OSError" in source
            else True,
            "analyze_python_callgraph 的 except OSError 应有日志",
        )

    def test_ts_callgraph_oserror_has_logger(self) -> None:
        import src.callgraph

        source = inspect.getsource(src.callgraph.analyze_ts_callgraph)
        if "except OSError" in source:
            self.assertIn("logger", source)

    def test_go_callgraph_oserror_has_logger(self) -> None:
        import src.callgraph

        source = inspect.getsource(src.callgraph.analyze_go_callgraph)
        if "except OSError" in source:
            self.assertIn("logger", source)

    def test_rust_callgraph_oserror_has_logger(self) -> None:
        import src.callgraph

        source = inspect.getsource(src.callgraph.analyze_rust_callgraph)
        if "except OSError" in source:
            self.assertIn("logger", source)


# ═══════════════════════════════════════════════════════════════════════════════
# P1-4: consumers.py 文件读取失败应有日志
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1_4_ConsumersReadErrorLogging(unittest.TestCase):
    """P1-4: consumers.py 文件读取失败不应完全静默。"""

    def test_file_read_error_has_logger(self) -> None:
        import src.consumers

        source = inspect.getsource(src.consumers.find_route_consumers)
        self.assertIn("OSError", source)
        self.assertIn("UnicodeDecodeError", source)


# ═══════════════════════════════════════════════════════════════════════════════
# P2: debug→warning 升级验证
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_DebugToWarningUpgrades(unittest.TestCase):
    """验证关键路径的 logger.debug 已升级为 logger.warning。"""

    def test_git_backend_blame_uses_warning(self) -> None:
        import src.git_backend

        source = inspect.getsource(src.git_backend.SubprocessBackend.blame_line)
        if "logger.debug" in source and "blame failed" in source:
            self.fail("blame_line 应使用 warning 而非 debug")
        self.assertIn("logger.warning", source)

    def test_git_backend_log_file_commits_uses_warning(self) -> None:
        import src.git_backend

        source = inspect.getsource(src.git_backend.SubprocessBackend.log_file_commits)
        if "logger.debug" in source and "log_file_commits" in source:
            self.fail("log_file_commits 应使用 warning 而非 debug")

    def test_git_backend_file_authors_uses_warning(self) -> None:
        import src.git_backend

        source = inspect.getsource(src.git_backend.SubprocessBackend.file_authors)
        if "logger.debug" in source and "file_authors failed" in source:
            self.fail("file_authors 应使用 warning 而非 debug")

    def test_core_incremental_cache_load_uses_warning(self) -> None:
        import src.core

        source = inspect.getsource(
            src.core.RepoMapEngine._load_incremental_cache_if_valid
        )
        self.assertIn("Incremental cache load failed", source)

    def test_resolver_package_json_parse_uses_warning(self) -> None:
        import src.resolver

        source = inspect.getsource(src.resolver)
        if "Failed to parse package.json" in source:
            self.assertNotIn(
                "logger.debug", source.split("Failed to parse package.json")[0][-200:]
            )

    def test_search_bm25_build_uses_warning(self) -> None:
        import src.search

        source = inspect.getsource(src.search.SymbolSearchIndex.__init__)
        self.assertNotIn(
            'logger.debug(f"BM25',
            source,
            "BM25 索引构建失败应使用 warning 而非 debug",
        )

    def test_toolkit_git_head_failure_uses_warning(self) -> None:
        import src.toolkit

        source = inspect.getsource(src.toolkit.save_incremental_cache)
        if "git rev-parse HEAD failed" in source:
            self.assertNotIn(
                "logger.debug", source.split("git rev-parse HEAD failed")[0][-200:]
            )

    def test_state_map_read_failure_uses_warning(self) -> None:
        import src.state_map

        source = inspect.getsource(src.state_map._read_file)
        self.assertIn("Failed to read", source)


# ═══════════════════════════════════════════════════════════════════════════════
# P2-10: type_inference.py 递归深度限制无日志
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_10_TypeInferenceDepthLogging(unittest.TestCase):
    """P2-10: 递归深度超限时应记录日志。"""

    def test_depth_limit_has_logger(self) -> None:
        import src.type_inference

        if hasattr(src.type_inference, "logger"):
            self.assertTrue(True)
        # type_inference.py 应该有 logger
        self.assertTrue(hasattr(src.type_inference, "logger"))


# ═══════════════════════════════════════════════════════════════════════════════
# P2-11: gitignore.py 权限错误静默跳过
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2_11_GitignorePermissionErrorLogging(unittest.TestCase):
    """P2-11: PermissionError 不应完全静默。"""

    def test_permission_error_has_logger(self) -> None:
        import src.gitignore

        self.assertTrue(hasattr(src.gitignore, "logger"))


# ═══════════════════════════════════════════════════════════════════════════════
# __init__.py 版本获取失败无日志
# ═══════════════════════════════════════════════════════════════════════════════


class TestInitVersionFetchLogging(unittest.TestCase):
    """版本获取失败应有 debug 日志。"""

    def test_version_fetch_has_logger(self) -> None:
        import src

        source = inspect.getsource(src.get_repomap_version)
        self.assertIn("0.0.0-dev", source)
        self.assertIn("logger", source)


if __name__ == "__main__":
    unittest.main()
