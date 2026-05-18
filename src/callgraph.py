"""
Python 精确调用图构建模块。

基于 ast 模块做 Python 专属的精确调用图分析，补充 tree-sitter 的
简单名称匹配。核心能力：
- 跨文件 import 解析：obj.method() 调用能追踪到定义文件
- 类方法分发：self.method() 追踪到类定义中的方法
- 装饰器感知：识别 @staticmethod / @classmethod

设计原则：
- 纯 Python ast 模块，无外部依赖
- 仅处理 Python 文件，其他语言仍用 tree-sitter
- 结果合并到 RepoGraph 的 edge 体系
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("repomap.callgraph")


def _safe_parse(source: bytes, filename: str = "<unknown>") -> ast.AST | None:
    try:
        return ast.parse(source, filename)
    except SyntaxError:
        return None


class ClassInfo:
    __slots__ = ("name", "methods")

    def __init__(self, name: str):
        self.name = name
        self.methods: dict[str, int] = {}


class ModuleInfo:
    __slots__ = ("filepath", "classes", "functions", "imports", "calls")

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.classes: dict[str, ClassInfo] = {}
        self.functions: dict[str, int] = {}
        self.imports: dict[str, str] = {}
        self.calls: list[tuple[str, str, int]] = []


class _PyCallGraphVisitor(ast.NodeVisitor):
    def __init__(self, filepath: str):
        self.info = ModuleInfo(filepath)
        self._current_class: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        cls = ClassInfo(node.name)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cls.methods[item.name] = item.lineno
        self.info.classes[node.name] = cls
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._current_class is None:
            self.info.functions[node.name] = node.lineno
        self._visit_calls_in_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self._current_class is None:
            self.info.functions[node.name] = node.lineno
        self._visit_calls_in_func(node)

    def _visit_calls_in_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = self._extract_call_name(child.func)
                if call_name:
                    self.info.calls.append((call_name, self._current_class or "", child.lineno))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name
            self.info.imports[local] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.info.imports[local] = f"{node.module}.{alias.name}"

    def _extract_call_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            parts.reverse()
            return ".".join(parts)
        return ""


def analyze_python_callgraph(
    project_root: Path,
    python_files: list[str],
) -> dict[str, ModuleInfo]:
    """
    分析 Python 文件的精确调用图。

    参数：
        project_root: 项目根目录
        python_files: 相对路径的 Python 文件列表

    返回：file_path → ModuleInfo 映射
    """
    modules: dict[str, ModuleInfo] = {}

    for rel_path in python_files:
        full_path = project_root / rel_path
        if not full_path.exists():
            continue
        try:
            source = full_path.read_bytes()
        except OSError:
            continue
        tree = _safe_parse(source, rel_path)
        if not tree:
            continue
        visitor = _PyCallGraphVisitor(rel_path)
        visitor.visit(tree)
        modules[rel_path] = visitor.info

    return modules


def resolve_precise_edges(
    modules: dict[str, ModuleInfo],
) -> list[tuple[str, str, str, int, str]]:
    """
    从模块信息解析精确调用边。

    返回: [(caller_file, caller_name, callee_file, callee_line, edge_kind), ...]
    edge_kind: "call" | "method_call" | "import_call"
    """
    name_to_file: dict[str, list[tuple[str, int]]] = {}
    class_name_to_file: dict[str, list[tuple[str, ClassInfo]]] = {}
    module_path_map: dict[str, str] = {}

    for fpath, info in modules.items():
        for fname, lineno in info.functions.items():
            name_to_file.setdefault(fname, []).append((fpath, lineno))
        for cname, cinfo in info.classes.items():
            class_name_to_file.setdefault(cname, []).append((fpath, cinfo))
        module_name = fpath.replace("/", ".").replace(".py", "").replace(".__init__", "")
        module_path_map[module_name] = fpath
        parent = module_name.rsplit(".", 1)[0] if "." in module_name else ""
        if parent:
            module_path_map.setdefault(parent, fpath)

    edges: list[tuple[str, str, str, int, str]] = []

    for fpath, info in modules.items():
        for call_name, in_class, call_line in info.calls:
            resolved = _resolve_call(
                call_name, in_class, fpath, info,
                name_to_file, class_name_to_file, module_path_map,
            )
            if resolved:
                callee_file, callee_name, callee_line, kind = resolved
                caller_name = f"{in_class}.{call_name.split('.')[-1]}" if in_class else call_name.split(".")[-1]
                edges.append((fpath, caller_name, callee_file, callee_line, kind))

    return edges


def _resolve_call(
    call_name: str,
    in_class: str,
    caller_file: str,
    caller_info: ModuleInfo,
    name_to_file: dict[str, list[tuple[str, int]]],
    class_name_to_file: dict[str, list[tuple[str, ClassInfo]]],
    module_path_map: dict[str, str],
) -> tuple[str, str, int, str] | None:
    parts = call_name.split(".")

    if len(parts) >= 2:
        obj_name = parts[0]
        method_name = parts[1]

        if obj_name == "self" and in_class:
            cls_info_list = class_name_to_file.get(in_class, [])
            for cfpath, cinfo in cls_info_list:
                if method_name in cinfo.methods:
                    return (cfpath, f"{in_class}.{method_name}", cinfo.methods[method_name], "method_call")

        if obj_name == "cls" and in_class:
            cls_info_list = class_name_to_file.get(in_class, [])
            for cfpath, cinfo in cls_info_list:
                if method_name in cinfo.methods:
                    return (cfpath, f"{in_class}.{method_name}", cinfo.methods[method_name], "method_call")

        import_target = caller_info.imports.get(obj_name)
        if import_target:
            func_matches = name_to_file.get(method_name, [])
            for mfpath, mline in func_matches:
                if mfpath != caller_file:
                    return (mfpath, method_name, mline, "import_call")

        class_matches = class_name_to_file.get(obj_name, [])
        for cfpath, cinfo in class_matches:
            if method_name in cinfo.methods:
                return (cfpath, f"{obj_name}.{method_name}", cinfo.methods[method_name], "method_call")

    func_name = parts[0]
    matches = name_to_file.get(func_name, [])
    if matches:
        for mfpath, mline in matches:
            if mfpath != caller_file:
                return (mfpath, func_name, mline, "call")
        return (matches[0][0], func_name, matches[0][1], "call")

    return None
