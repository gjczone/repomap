"""Issue #56 回归测试 — 健壮性修复验证.

测试覆盖:
  B4:  decode errors='replace'
  B6:  _resolve_cache 20000 limit
  B11: LSP did_open 1MiB limit
  B19: rglob symlink check
  #36: scan timeout partial_state
  #36: path traversal blocked
  B4:  static check: all .decode() use errors='replace'
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

# 确保 src 可导入
_SRC_DIR = str(Path(__file__).resolve().parents[1])


class TestB4DecodeErrorsReplace(unittest.TestCase):
    """B4: parser.py 中处理文件内容的 .decode() 调用必须使用 errors='replace'.

    parser.py line 554 使用 errors='ignore' 用于括号嵌套深度检测
    (不处理源文件内容), 属于例外。
    """

    @classmethod
    def setUpClass(cls) -> None:
        parser_path = Path(_SRC_DIR, "src", "parser.py")
        cls.source = parser_path.read_text("utf-8")
        cls.tree = ast.parse(cls.source)

    def _find_decode_calls(self) -> list[ast.Call]:
        """找到所有 .decode() 调用节点."""

        class DecodeVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.calls: list[ast.Call] = []

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Attribute) and node.func.attr == "decode":
                    self.calls.append(node)
                self.generic_visit(node)

        visitor = DecodeVisitor()
        visitor.visit(self.tree)
        return visitor.calls

    def test_at_least_one_decode_call_found(self) -> None:
        """parser.py 应包含至少一个 .decode() 调用."""
        calls = self._find_decode_calls()
        self.assertGreater(
            len(calls), 0, "应在 parser.py 中找到至少一个 .decode() 调用"
        )

    def test_content_decode_calls_use_errors_replace(self) -> None:
        """处理源文件内容的 .decode() 调用必须有 errors='replace'.

        line 554 (errors='ignore') 用于检测括号嵌套深度而非内容处理, 可豁免。
        """
        calls = self._find_decode_calls()

        # line 554 是嵌套深度检测, 不属于内容处理
        EXEMPT_LINES = {554}

        violations: list[tuple[int, str]] = []
        for call in calls:
            if call.lineno in EXEMPT_LINES:
                continue
            has_replace = False
            for kw in call.keywords:
                if kw.arg == "errors":
                    if (
                        isinstance(kw.value, ast.Constant)
                        and kw.value.value == "replace"
                    ):
                        has_replace = True
                        break
            if not has_replace:
                violations.append((call.lineno, ast.unparse(call)))

        self.assertEqual(
            len(violations),
            0,
            f"发现 {len(violations)} 个内容处理 .decode() 调用缺少 errors='replace':\n"
            + "\n".join(f"  行 {ln}: {code}" for ln, code in violations),
        )


class TestB6ResolverCacheLimit(unittest.TestCase):
    """B6: _resolve_cache 上限 20000，超限后清空防止内存泄漏."""

    def test_cache_max_is_20000(self) -> None:
        from src.resolver import ImportResolver
        from src.core import RepoGraph

        graph = RepoGraph()
        resolver = ImportResolver(Path(tempfile.gettempdir()), graph)
        self.assertEqual(
            resolver._resolve_cache_max,
            20000,
            "_resolve_cache_max 应为 20000",
        )

    def test_cache_clears_when_full(self) -> None:
        from src.resolver import ImportResolver
        from src.core import RepoGraph

        graph = RepoGraph()
        resolver = ImportResolver(Path(tempfile.gettempdir()), graph)

        # 填充缓存到上限
        for i in range(20000):
            key = (f"src/file_{i}.py", "os")
            resolver._resolve_cache[key] = ["stdlib/os"]
        self.assertEqual(len(resolver._resolve_cache), 20000)

        # 再添加一个条目应触发清空
        resolver._cache_set(("overflow.py", "os"), ["stdlib/os"])
        self.assertLessEqual(
            len(resolver._resolve_cache),
            20000,
            "缓存应在超过 20000 条后被清空或限制",
        )
        # 清空后至少应包含新条目
        self.assertIn(("overflow.py", "os"), resolver._resolve_cache)

    def test_cache_not_cleared_below_limit(self) -> None:
        from src.resolver import ImportResolver
        from src.core import RepoGraph

        graph = RepoGraph()
        resolver = ImportResolver(Path(tempfile.gettempdir()), graph)

        # 填充到低于上限
        for i in range(10000):
            key = (f"src/file_{i}.py", "os")
            resolver._resolve_cache[key] = ["stdlib/os"]

        # 应该保留已有条目
        resolver._cache_set(("another.py", "os"), ["stdlib/os"])
        self.assertGreater(len(resolver._resolve_cache), 10000)
        self.assertIn(("another.py", "os"), resolver._resolve_cache)


class TestB11LspDidOpenLimit(unittest.TestCase):
    """B11: LSP did_open 跳过大于 1MiB 的文件."""

    def test_max_file_size_constant(self) -> None:
        from src.lsp import StdioLspClient

        self.assertEqual(
            StdioLspClient.MAX_FILE_SIZE,
            1_048_576,
            "MAX_FILE_SIZE 应为 1 MiB (1048576 字节)",
        )

    def test_did_open_skips_large_files(self) -> None:
        from src.lsp import StdioLspClient

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            large_file = tmp / "large.py"

            # 创建一个大于 1MiB 的文件
            content = "x" * (StdioLspClient.MAX_FILE_SIZE + 100)
            large_file.write_text(content, encoding="utf-8")

            # 创建一个 StdioLspClient 的模拟实例
            # 我们不启动真正的 LSP 服务器，只测试 did_open 的大小检查逻辑
            client = object.__new__(StdioLspClient)

            # 验证 did_open 在文件过大时不会调用 send_notification
            call_count = 0

            def mock_send(*_args: object, **_kwargs: object) -> None:
                nonlocal call_count
                call_count += 1

            client.send_notification = mock_send  # type: ignore[attr-defined]
            client.did_open(large_file, "python", content)  # type: ignore[arg-type]

            self.assertEqual(
                call_count,
                0,
                "超过 1MiB 的文件不应触发 didOpen 通知",
            )

    def test_did_open_allows_small_files(self) -> None:
        from src.lsp import StdioLspClient

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            small_file = tmp / "small.py"
            content = "def foo(): pass\n"
            small_file.write_text(content, encoding="utf-8")

            client = object.__new__(StdioLspClient)
            call_count = 0

            def mock_send(*_args: object, **_kwargs: object) -> None:
                nonlocal call_count
                call_count += 1

            client.send_notification = mock_send  # type: ignore[attr-defined]
            client.did_open(small_file, "python", content)  # type: ignore[arg-type]

            self.assertEqual(
                call_count,
                1,
                "小于 1MiB 的文件应正常触发 didOpen 通知",
            )


class TestB19RglobSymlinkCheck(unittest.TestCase):
    """B19: rglob 文件扫描应使用 not p.is_symlink() 跳过符号链接."""

    def test_rglob_includes_is_symlink_guard(self) -> None:
        """验证 _list_files 中的 rglob 使用 is_symlink() 保护.

        不启动完整扫描 (会触发 GitignoreParser 的 os.scandir 遍历),
        而是直接解析源码确认 rglob 调用包含 is_symlink 检查。
        """
        core_path = Path(_SRC_DIR, "src", "core.py")
        source = core_path.read_text("utf-8")

        # 查找所有 rglob("*") 调用，验证紧跟着 is_symlink 检查
        # 模式: for p in ... rglob("*") ... p.is_file() and not p.is_symlink()
        rglob_lines: list[str] = []
        for i, line in enumerate(source.splitlines(), 1):
            if "rglob(" in line and "is_symlink" not in line:
                # 检查后续几行是否有 is_symlink
                subsequent = "\n".join(source.splitlines()[i : i + 5])
                if "is_symlink" not in subsequent:
                    rglob_lines.append(f"line {i}: {line.strip()}")

        self.assertEqual(
            len(rglob_lines),
            0,
            "rglob 调用缺少 is_symlink 检查:\n" + "\n".join(rglob_lines),
        )

    def test_symlink_to_file_skipped_by_engine(self) -> None:
        """指向普通文件的符号链接在扫描时被跳过."""
        from src.core import RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "real.py").write_text("def real(): pass\n", encoding="utf-8")

            try:
                (root / "link.py").symlink_to(root / "real.py")
            except OSError:
                self.skipTest("文件系统不支持符号链接")

            engine = RepoMapEngine(str(root))
            if not engine.ts.parsers:
                self.skipTest("tree-sitter 不可用")

            engine.scan(max_files=100, max_scan_time=5.0)

            scanned = {s.file for s in engine.graph.symbols.values()}
            self.assertIn("real.py", scanned, "真实文件应被扫描")
            self.assertNotIn("link.py", scanned, "符号链接文件应被跳过")


class TestScanTimeoutPartialState(unittest.TestCase):
    """#36: scan 超时后 scan_state 和 timeout_triggered 状态正确."""

    def test_scan_state_partial_after_timeout(self) -> None:
        from src.core import RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir, "app.py")
            py_file.write_text("def foo(): pass\n", encoding="utf-8")

            engine = RepoMapEngine(tmpdir)
            if not engine.ts.parsers:
                self.skipTest("tree-sitter 不可用")

            # max_scan_time=0.0 确保第一个文件处理前就超时
            engine.scan(max_files=8000, max_scan_time=0.0)

            self.assertEqual(
                engine.scan_state,
                "partial",
                "scan 超时后 scan_state 应为 'partial'，不应误导为 'scanned'",
            )

    def test_timeout_triggered_flag(self) -> None:
        from src.core import RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir, "app.py")
            py_file.write_text("def foo(): pass\n", encoding="utf-8")

            engine = RepoMapEngine(tmpdir)
            if not engine.ts.parsers:
                self.skipTest("tree-sitter 不可用")

            engine.scan(max_files=8000, max_scan_time=0.0)

            self.assertTrue(
                engine.scan_stats.timeout_triggered,
                "超时后 timeout_triggered 应为 True",
            )

    def test_no_timeout_keeps_scanned_state(self) -> None:
        from src.core import RepoMapEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir, "app.py")
            py_file.write_text("def foo(): pass\n", encoding="utf-8")

            engine = RepoMapEngine(tmpdir)
            if not engine.ts.parsers:
                self.skipTest("tree-sitter 不可用")

            # 正常超时时间
            engine.scan(max_files=8000, max_scan_time=60.0)

            self.assertEqual(
                engine.scan_state,
                "scanned",
                "未超时时 scan_state 应为 'scanned'",
            )
            self.assertFalse(
                engine.scan_stats.timeout_triggered,
                "未超时时 timeout_triggered 应为 False",
            )


class TestPathTraversalBlocked(unittest.TestCase):
    """#36: _validate_file_path 阻止路径遍历攻击."""

    def test_rejects_parent_traversal(self) -> None:
        from src.git_backend import _validate_file_path

        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir, "project")
            project.mkdir()

            result = _validate_file_path(str(project), "../../etc/passwd")
            self.assertIsNone(result, "../../etc/passwd 应被拒绝")

    def test_rejects_absolute_path(self) -> None:
        from src.git_backend import _validate_file_path

        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir, "project")
            project.mkdir()

            result = _validate_file_path(str(project), "/etc/passwd")
            self.assertIsNone(result, "绝对路径 /etc/passwd 应被拒绝")

    def test_rejects_traversal_with_dotdot(self) -> None:
        from src.git_backend import _validate_file_path

        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir, "project")
            project.mkdir()

            result = _validate_file_path(str(project), "../etc/passwd")
            self.assertIsNone(result, "../etc/passwd 应被拒绝")

    def test_accepts_valid_relative_path(self) -> None:
        from src.git_backend import _validate_file_path

        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir, "project")
            project.mkdir()
            valid_file = project / "src" / "main.py"
            valid_file.parent.mkdir(parents=True)
            valid_file.touch()

            result = _validate_file_path(str(project), "src/main.py")
            self.assertIsNotNone(result, "合法相对路径应被接受")
            self.assertIn("src", str(result))

    def test_rejects_deep_traversal(self) -> None:
        from src.git_backend import _validate_file_path

        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir, "project")
            project.mkdir()

            # 尝试通过多层 ../ 逃逸
            result = _validate_file_path(
                str(project), "../../../root/.ssh/authorized_keys"
            )
            self.assertIsNone(result, "多层 ../ 逃逸应被拒绝")


class TestB4StaticAllDecodeErrorsReplace(unittest.TestCase):
    """B4 static: 所有 src/*.py 中处理任意字节的 .decode() 调用必须使用 errors='replace'.

    跳过以下已知安全场景:
      - 含 try/except UnicodeDecodeError 的调用
      - 解析已知为 UTF-8 的数据 (orjson 输出, LSP JSON-RPC 消息)
      - tree-sitter 节点文本 (保证有效 UTF-8)
      - parser.py:554 嵌套检测 (errors='ignore' 用于计数)
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.src_dir = Path(_SRC_DIR, "src")
        cls.py_files = sorted(cls.src_dir.glob("*.py"))

    @staticmethod
    def _find_decode_calls(source: str) -> list[tuple[int, str]]:
        """解析源文件并返回 (行号, 代码) 的所有 .decode() 调用."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        calls: list[tuple[int, str]] = []

        class DecodeVisitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Attribute) and node.func.attr == "decode":
                    calls.append((node.lineno, ast.unparse(node)))
                self.generic_visit(node)

        DecodeVisitor().visit(tree)
        return calls

    @staticmethod
    def _is_protected_by_try_except(source: str, lineno: int) -> bool:
        """检查指定行号的 decode 调用是否被 try/except UnicodeDecodeError 包裹."""
        lines = source.splitlines()
        if lineno < 1 or lineno > len(lines):
            return False
        # 简单启发式: 在前 12 行内找 try:, 在后 10 行内找 UnicodeDecodeError
        start = max(0, lineno - 13)
        end = min(len(lines), lineno + 10)
        block = "\n".join(lines[start:end])
        return "try:" in block and "UnicodeDecodeError" in block

    def test_content_decode_calls_use_errors_replace(self) -> None:
        """处理任意文件字节的 .decode() 调用必须有 errors='replace'."""
        import re

        # 已知安全场景的模式 (不处理用户文件内容)
        SAFE_PATTERNS = [
            r"\.text\s*\.\s*decode",  # tree-sitter 节点文本 (有效 UTF-8)
            r"body\s*\.\s*decode",  # LSP JSON-RPC 消息体
            r"_orjson\.dumps.*\.decode",  # orjson 序列化输出
        ]

        # 已知例外: (文件名, 行号)
        EXEMPTIONS: set[tuple[str, int]] = {
            ("parser.py", 554),  # 嵌套深度检测, errors='ignore' 正确
        }

        violations: list[tuple[str, int, str]] = []

        for py_file in self.py_files:
            source = py_file.read_text("utf-8")
            for lineno, call_code in self._find_decode_calls(source):
                # 已知例外?
                if (py_file.name, lineno) in EXEMPTIONS:
                    continue
                # 有 errors='replace'? → 通过
                if re.search(r"errors\s*=\s*['\"]replace['\"]", call_code):
                    continue
                # 已知安全模式?
                if any(re.search(pattern, call_code) for pattern in SAFE_PATTERNS):
                    continue
                # 被 try/except UnicodeDecodeError 保护?
                if self._is_protected_by_try_except(source, lineno):
                    continue

                violations.append((py_file.name, lineno, call_code))

        self.assertEqual(
            len(violations),
            0,
            f"发现 {len(violations)} 个未受保护的 .decode() 调用缺少 errors='replace':\n"
            + "\n".join(f"  {fname}:{ln}: {code}" for fname, ln, code in violations),
        )

    def test_at_least_one_decode_call_found(self) -> None:
        """确保测试有意义 — 至少找到一个 .decode() 调用."""
        total = 0
        for py_file in self.py_files:
            source = py_file.read_text("utf-8")
            total += len(self._find_decode_calls(source))

        self.assertGreater(
            total,
            0,
            "应在 src/*.py 中找到至少一个 .decode() 调用，否则测试可能错误",
        )


if __name__ == "__main__":
    unittest.main()
