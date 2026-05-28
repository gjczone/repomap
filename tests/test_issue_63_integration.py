"""
issue63# 集成测试。

测试跨模块契约完整性、错误恢复路径、近期修复回归等问题。
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import RepoMapEngine


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def project_root(tmp_path):
    """创建一个临时的 git 项目目录。"""
    root = str(tmp_path)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=root,
        check=True,
    )
    return root


class TestP0TypeInferenceIntegration:
    """P0-1: 类型推断在完整扫描流程中的集成测试。"""

    def test_python_function_return_type_preserved_after_scan(self, project_root):
        """验证 Python 函数的 return_type 在完整扫描后不为空。"""
        # 创建一个带有类型注解的 Python 文件
        write_file(
            project_root,
            "main.py",
            'def hello() -> str:\n    return "world"\n',
        )

        # 添加到 git
        subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        # 运行完整扫描
        engine = RepoMapEngine(project_root)
        engine.scan()

        # 查找 hello 符号
        hello_symbol = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "hello":
                hello_symbol = sym
                break

        # 验证 return_type 不为空
        assert hello_symbol is not None, "hello 符号应该存在"
        assert hello_symbol.return_type == "str", (
            f"return_type 应该是 'str'，但得到 '{hello_symbol.return_type}'"
        )

    def test_python_function_params_preserved_after_scan(self, project_root):
        """验证 Python 函数的 params 在完整扫描后不为空。"""
        # 创建一个带有参数类型注解的 Python 文件
        write_file(
            project_root,
            "main.py",
            "def add(x: int, y: int) -> int:\n    return x + y\n",
        )

        # 添加到 git
        subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        # 运行完整扫描
        engine = RepoMapEngine(project_root)
        engine.scan()

        # 查找 add 符号
        add_symbol = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "add":
                add_symbol = sym
                break

        # 验证 params 不为空
        assert add_symbol is not None, "add 符号应该存在"
        assert "x: int" in add_symbol.params, (
            f"params 应该包含 'x: int'，但得到 '{add_symbol.params}'"
        )
        assert "y: int" in add_symbol.params, (
            f"params 应该包含 'y: int'，但得到 '{add_symbol.params}'"
        )


class TestP1ImportEdgeBuilding:
    """P1-1: import 边构建的集成测试。"""

    def test_import_edge_creates_neighbor_relationship(self, project_root):
        """验证 import 边能正确创建邻居关系。"""
        # 创建两个有 import 关系的文件
        write_file(
            project_root,
            "utils.py",
            "def helper():\n    return 42\n",
        )
        write_file(
            project_root,
            "main.py",
            "from utils import helper\n\ndef main():\n    return helper()\n",
        )

        # 添加到 git
        subprocess.run(["git", "add", "."], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        # 运行完整扫描
        engine = RepoMapEngine(project_root)
        engine.scan()

        # 验证 import 关系被正确记录
        assert "main.py" in engine.graph.file_imports, "main.py 应该有 import 记录"
        imports = engine.graph.file_imports["main.py"]
        assert "utils" in imports, f"imports 应该包含 'utils'，但得到 {imports}"


class TestP1CacheSaveRaceCondition:
    """P1-2: 缓存保存竞态条件的测试。"""

    def test_cache_save_handles_deleted_file(self, project_root):
        """验证缓存保存能处理文件在扫描和保存之间被删除的情况。"""
        # 创建一个文件
        write_file(
            project_root,
            "main.py",
            "def hello():\n    return 'world'\n",
        )

        # 添加到 git
        subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        # 运行完整扫描
        engine = RepoMapEngine(project_root)
        engine.scan()

        # 模拟文件在扫描后被删除
        import os

        os.remove(Path(project_root) / "main.py")

        # 尝试保存缓存，应该不会崩溃
        try:
            from src.toolkit import save_incremental_cache

            save_incremental_cache(project_root, engine)
        except FileNotFoundError:
            pytest.fail(
                "save_incremental_cache 应该处理文件删除，而不是抛出 FileNotFoundError"
            )


class TestP2LspOpenedFilesCheck:
    """P2-2 & P2-3: LSP 文件检查的测试。"""

    def test_collect_diagnostics_skips_unopened_files(self, project_root):
        """验证 collect_diagnostics 跳过未打开的文件。"""
        # 创建一个文件
        write_file(
            project_root,
            "main.py",
            "def hello():\n    return 'world'\n",
        )

        # 添加到 git
        subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        # 直接测试 collect_diagnostics 的逻辑
        from src.lsp import StdioLspClient
        import queue

        # 创建一个模拟的 StdioLspClient 实例
        client = object.__new__(StdioLspClient)
        client._opened_files = set()  # 没有打开的文件
        client._notifications = []
        client._messages = queue.Queue()
        client.timeout = 5.0

        # 应该跳过未打开的文件
        result = client.collect_diagnostics([Path(project_root) / "main.py"], "python")
        assert result == [], "应该返回空列表，因为文件未打开"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
