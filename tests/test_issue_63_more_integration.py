"""
issue63# 更多集成测试。

测试类型推断、import解析、LRU缓存等功能的端到端行为。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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


class TestTypeInferenceE2E:
    """类型推断端到端测试。"""

    def test_typescript_function_types_preserved(self, project_root):
        """验证 TypeScript 函数的类型信息在完整扫描后保留。"""
        write_file(
            project_root,
            "main.ts",
            "function greet(name: string): string {\n  return `Hello, ${name}`;\n}\n",
        )

        subprocess.run(["git", "add", "main.ts"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        engine = RepoMapEngine(project_root)
        engine.scan()

        # 查找 greet 符号
        greet_symbol = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "greet":
                greet_symbol = sym
                break

        assert greet_symbol is not None, "greet 符号应该存在"
        assert greet_symbol.return_type == "string", (
            f"return_type 应该是 'string'，但得到 '{greet_symbol.return_type}'"
        )
        assert "name: string" in greet_symbol.params, (
            f"params 应该包含 'name: string'，但得到 '{greet_symbol.params}'"
        )

    def test_go_function_types_preserved(self, project_root):
        """验证 Go 函数的类型信息在完整扫描后保留。"""
        write_file(
            project_root,
            "main.go",
            'package main\n\nfunc add(a int, b int) int {\n  return a + b\n}\n',
        )

        subprocess.run(["git", "add", "main.go"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        engine = RepoMapEngine(project_root)
        engine.scan()

        # 查找 add 符号
        add_symbol = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "add":
                add_symbol = sym
                break

        assert add_symbol is not None, "add 符号应该存在"
        assert add_symbol.return_type == "int", (
            f"return_type 应该是 'int'，但得到 '{add_symbol.return_type}'"
        )
        assert "a int" in add_symbol.params, (
            f"params 应该包含 'a int'，但得到 '{add_symbol.params}'"
        )
        assert "b int" in add_symbol.params, (
            f"params 应该包含 'b int'，但得到 '{add_symbol.params}'"
        )

    def test_class_method_types_preserved(self, project_root):
        """验证类方法的类型信息在完整扫描后保留。"""
        write_file(
            project_root,
            "main.py",
            "class Calculator:\n    def add(self, x: int, y: int) -> int:\n        return x + y\n",
        )

        subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        engine = RepoMapEngine(project_root)
        engine.scan()

        # 查找 add 方法
        add_symbol = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "add":
                add_symbol = sym
                break

        assert add_symbol is not None, "add 方法应该存在"
        assert add_symbol.return_type == "int", (
            f"return_type 应该是 'int'，但得到 '{add_symbol.return_type}'"
        )
        assert "x: int" in add_symbol.params, (
            f"params 应该包含 'x: int'，但得到 '{add_symbol.params}'"
        )
        assert "y: int" in add_symbol.params, (
            f"params 应该包含 'y: int'，但得到 '{add_symbol.params}'"
        )


class TestImportResolutionE2E:
    """Import 解析端到端测试。"""

    def test_relative_import_resolves_to_file(self, project_root):
        """验证相对 import 能正确解析到文件。"""
        write_file(
            project_root,
            "utils/helper.py",
            "def helper():\n    return 42\n",
        )
        write_file(
            project_root,
            "main.py",
            "from utils.helper import helper\n\ndef main():\n    return helper()\n",
        )

        subprocess.run(["git", "add", "."], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        engine = RepoMapEngine(project_root)
        engine.scan()

        # 验证 import 关系被正确记录
        assert "main.py" in engine.graph.file_imports, "main.py 应该有 import 记录"
        imports = engine.graph.file_imports["main.py"]
        assert "utils.helper" in imports, (
            f"imports 应该包含 'utils.helper'，但得到 {imports}"
        )

    def test_multiple_imports_from_same_module(self, project_root):
        """验证从同一模块导入多个符号。"""
        write_file(
            project_root,
            "utils.py",
            "def func_a():\n    return 1\n\ndef func_b():\n    return 2\n",
        )
        write_file(
            project_root,
            "main.py",
            "from utils import func_a, func_b\n\ndef main():\n    return func_a() + func_b()\n",
        )

        subprocess.run(["git", "add", "."], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        engine = RepoMapEngine(project_root)
        engine.scan()

        # 验证两个函数都被找到
        func_a = None
        func_b = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "func_a":
                func_a = sym
            elif sym.name == "func_b":
                func_b = sym

        assert func_a is not None, "func_a 符号应该存在"
        assert func_b is not None, "func_b 符号应该存在"


class TestLRUCacheBehavior:
    """LRU 缓存行为测试。"""

    def test_cache_hit_returns_same_result(self, project_root):
        """验证缓存命中返回相同结果。"""
        write_file(
            project_root,
            "main.py",
            "def hello():\n    return 'world'\n",
        )

        subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        # 第一次扫描
        engine1 = RepoMapEngine(project_root)
        engine1.scan()
        symbols1 = dict(engine1.graph.symbols)

        # 第二次扫描（应该使用缓存）
        engine2 = RepoMapEngine(project_root)
        engine2.scan()
        symbols2 = dict(engine2.graph.symbols)

        # 验证结果相同
        assert len(symbols1) == len(symbols2), "两次扫描的符号数量应该相同"
        for sym_id, sym1 in symbols1.items():
            assert sym_id in symbols2, f"符号 {sym_id} 应该在第二次扫描中存在"
            sym2 = symbols2[sym_id]
            assert sym1.name == sym2.name, f"符号 {sym_id} 的名称应该相同"
            assert sym1.return_type == sym2.return_type, (
                f"符号 {sym_id} 的 return_type 应该相同"
            )


class TestCallGraphIntegration:
    """调用图集成测试。"""

    def test_function_call_detected(self, project_root):
        """验证函数调用被正确检测。"""
        write_file(
            project_root,
            "main.py",
            "def helper():\n    return 42\n\ndef main():\n    return helper()\n",
        )

        subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        engine = RepoMapEngine(project_root)
        engine.scan()

        # 查找 main 和 helper 符号
        main_symbol = None
        helper_symbol = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "main":
                main_symbol = sym
            elif sym.name == "helper":
                helper_symbol = sym

        assert main_symbol is not None, "main 符号应该存在"
        assert helper_symbol is not None, "helper 符号应该存在"

        # 验证调用关系
        main_id = main_symbol.id
        helper_id = helper_symbol.id

        # 检查 outgoing 边
        outgoing = engine.graph.outgoing.get(main_id, [])
        call_targets = [e.target for e in outgoing if e.kind == "call"]
        assert helper_id in call_targets, (
            f"main 应该调用 helper，但调用目标是 {call_targets}"
        )

    def test_cross_file_call_detected(self, project_root):
        """验证跨文件函数调用被正确检测。"""
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

        subprocess.run(["git", "add", "."], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )

        engine = RepoMapEngine(project_root)
        engine.scan()

        # 查找 main 和 helper 符号
        main_symbol = None
        helper_symbol = None
        for sym_id, sym in engine.graph.symbols.items():
            if sym.name == "main":
                main_symbol = sym
            elif sym.name == "helper":
                helper_symbol = sym

        assert main_symbol is not None, "main 符号应该存在"
        assert helper_symbol is not None, "helper 符号应该存在"

        # 验证调用关系
        main_id = main_symbol.id
        helper_id = helper_symbol.id

        # 检查 outgoing 边
        outgoing = engine.graph.outgoing.get(main_id, [])
        call_targets = [e.target for e in outgoing if e.kind == "call"]
        assert helper_id in call_targets, (
            f"main 应该调用 helper，但调用目标是 {call_targets}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
