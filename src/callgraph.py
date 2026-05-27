"""
多语言精确调用图构建模块。

Python：基于 ast 模块做精确调用图分析
TypeScript/Go/Rust：基于 tree-sitter AST 做精确调用图分析

核心能力：
- 跨文件 import 解析：obj.method() 调用能追踪到定义文件
- 类方法分发：self.method() / this.method() 追踪到类定义中的方法
- Go receiver 解析：func (r *Type) Method() 的方法分发
- Rust impl 块解析：impl Type { fn method() } 的方法分发

设计原则：
- Python 用 ast 模块，TS/Go/Rust 用 tree-sitter AST
- 结果合并到 RepoGraph 的 edge 体系
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

from . import find_child_by_type as _find_child_by_type
from . import find_children_by_type as _find_children_by_type

logger = logging.getLogger("repomap.callgraph")


def _node_text(node: Any) -> str:
    return node.text.decode("utf-8") if getattr(node, "text", None) else ""


def _safe_parse(source: bytes, filename: str = "<unknown>") -> ast.AST | None:
    try:
        return ast.parse(source, filename)
    except SyntaxError:
        logger.debug("Syntax error in %s, skipping call graph analysis", filename)
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


# ═══════════════════════════════════════════════════════════════════════════════
# Python 调用图（基于 ast 模块）
# ═══════════════════════════════════════════════════════════════════════════════


class _PyCallGraphVisitor(ast.NodeVisitor):
    def __init__(self, filepath: str):
        self.info = ModuleInfo(filepath)
        self._current_class: list[str] = []
        self._in_function: bool = False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        cls = ClassInfo(node.name)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cls.methods[item.name] = item.lineno
        self.info.classes[node.name] = cls
        self._current_class.append(node.name)
        self.generic_visit(node)
        self._current_class.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not self._current_class:
            self.info.functions[node.name] = node.lineno
        old_in_func = self._in_function
        self._in_function = True
        self._visit_calls_in_func(node)
        self._in_function = old_in_func

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if not self._current_class:
            self.info.functions[node.name] = node.lineno
        old_in_func = self._in_function
        self._in_function = True
        self._visit_calls_in_func(node)
        self._in_function = old_in_func

    def visit_Call(self, node: ast.Call) -> None:
        if not self._in_function and not self._current_class:
            call_name = self._extract_call_name(node.func)
            if call_name:
                self.info.calls.append((call_name, "", node.lineno))
        self.generic_visit(node)

    def _visit_calls_in_func(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = self._extract_call_name(child.func)
                if call_name:
                    self.info.calls.append(
                        (
                            call_name,
                            self._current_class[-1] if self._current_class else "",
                            child.lineno,
                        )
                    )

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
            current: ast.expr = node
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
    source_map: dict[str, bytes] | None = None,
) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}

    for rel_path in python_files:
        full_path = project_root / rel_path
        if not full_path.exists():
            continue
        try:
            if source_map and rel_path in source_map:
                source = source_map[rel_path]
            else:
                source = full_path.read_bytes()
        except OSError:
            logger.debug(
                "Failed to read %s for call graph analysis", rel_path, exc_info=True
            )
            continue
        tree = _safe_parse(source, rel_path)
        if not tree:
            logger.debug("Python AST parse returned None for %s", rel_path)
            continue
        visitor = _PyCallGraphVisitor(rel_path)
        visitor.visit(tree)
        modules[rel_path] = visitor.info

    return modules


# ═══════════════════════════════════════════════════════════════════════════════
# TypeScript/TSX 调用图（基于 tree-sitter AST）
# ═══════════════════════════════════════════════════════════════════════════════


def _walk_ts_node(
    node: Any, info: ModuleInfo, current_class: list[str | None], depth: int = 0
) -> None:
    if depth > 50 or node.child_count == 0:
        return

    if node.type == "class_declaration":
        name_node = _find_child_by_type(node, "type_identifier") or _find_child_by_type(
            node, "identifier"
        )
        if name_node:
            class_name = _node_text(name_node)
            cls = ClassInfo(class_name)
            body = _find_child_by_type(node, "class_body")
            if body:
                for child in body.children:
                    if child.type == "method_definition":
                        mn = _find_child_by_type(child, "property_identifier")
                        if mn:
                            cls.methods[_node_text(mn)] = child.start_point[0] + 1
            info.classes[class_name] = cls
            old = current_class[0]
            current_class[0] = class_name
            for child in node.children:
                _walk_ts_node(child, info, current_class, depth + 1)
            current_class[0] = old
            return

    if node.type == "function_declaration":
        name_node = _find_child_by_type(node, "identifier")
        if name_node and not current_class[0]:
            info.functions[_node_text(name_node)] = node.start_point[0] + 1

    if node.type == "variable_declarator":
        name_node = _find_child_by_type(node, "identifier")
        val = _find_child_by_type(node, "arrow_function") or _find_child_by_type(
            node, "function_expression"
        )
        if name_node and val and not current_class[0]:
            info.functions[_node_text(name_node)] = node.start_point[0] + 1

    if node.type == "import_statement":
        source_node = _find_child_by_type(node, "string")
        source = _node_text(source_node).strip("\"'") if source_node else ""
        clause = _find_child_by_type(node, "import_clause")
        if clause:
            for child in clause.children:
                if child.type == "identifier":
                    local = _node_text(child)
                    info.imports[local] = source
                elif child.type == "namespace_import":
                    ns_id = _find_child_by_type(child, "identifier")
                    if ns_id:
                        info.imports[_node_text(ns_id)] = source
                elif child.type == "named_imports":
                    for spec in child.children:
                        if spec.type == "import_specifier":
                            alias_node = _find_child_by_type(spec, "alias_identifier")
                            if alias_node:
                                info.imports[_node_text(alias_node)] = source
                            else:
                                ids = _find_children_by_type(spec, "identifier")
                                if len(ids) >= 2:
                                    info.imports[_node_text(ids[1])] = source
                                elif ids:
                                    info.imports[_node_text(ids[0])] = source

    if node.type == "call_expression":
        call_name = _extract_ts_call_name(node)
        if call_name:
            info.calls.append(
                (call_name, current_class[0] or "", node.start_point[0] + 1)
            )

    for child in node.children:
        _walk_ts_node(child, info, current_class, depth + 1)


def _extract_ts_call_name(node: Any) -> str:
    func = node.child_by_field_name("function")
    if not func:
        return ""
    if func.type == "identifier":
        return _node_text(func)
    if func.type == "member_expression":
        prop = func.child_by_field_name("property")
        obj = func.child_by_field_name("object")
        prop_text = _node_text(prop) if prop else ""
        obj_text = _node_text(obj) if obj else ""
        if obj_text and prop_text:
            return f"{obj_text}.{prop_text}"
        return prop_text
    return ""


def analyze_ts_callgraph(
    project_root: Path,
    ts_files: list[str],
    ts_adapter: Any,
    source_map: dict[str, bytes] | None = None,
) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for rel_path in ts_files:
        full_path = project_root / rel_path
        if not full_path.exists():
            continue
        try:
            if source_map and rel_path in source_map:
                source = source_map[rel_path]
            else:
                source = full_path.read_bytes()
        except OSError:
            logger.debug(
                "Failed to read %s for call graph analysis", rel_path, exc_info=True
            )
            continue
        ext = Path(rel_path).suffix.lower()
        lang = "tsx" if ext == ".tsx" else "typescript"
        tree = ts_adapter.parse(source, lang)
        if not tree:
            logger.debug("TypeScript tree-sitter parse returned None for %s", rel_path)
            continue
        info = ModuleInfo(rel_path)
        _walk_ts_node(tree.root_node, info, [None])
        modules[rel_path] = info
    return modules


# ═══════════════════════════════════════════════════════════════════════════════
# Go 调用图（基于 tree-sitter AST）
# ═══════════════════════════════════════════════════════════════════════════════


def _walk_go_node(
    node: Any, info: ModuleInfo, current_receiver: list[str | None], depth: int = 0
) -> None:
    if depth > 50 or node.child_count == 0:
        return

    if node.type == "function_declaration":
        name_node = _find_child_by_type(node, "identifier")
        if name_node:
            info.functions[_node_text(name_node)] = node.start_point[0] + 1

    if node.type == "method_declaration":
        receiver = _find_child_by_type(node, "parameter_list")
        method_name_node = _find_child_by_type(node, "field_identifier")
        if receiver and method_name_node:
            method_name = _node_text(method_name_node)
            recv_type = _extract_go_receiver_type(receiver)
            if recv_type:
                if recv_type not in info.classes:
                    info.classes[recv_type] = ClassInfo(recv_type)
                info.classes[recv_type].methods[method_name] = node.start_point[0] + 1
                current_receiver[0] = recv_type

    if node.type == "import_declaration":
        spec_list = _find_child_by_type(node, "import_spec_list")
        if spec_list:
            for spec in spec_list.children:
                if spec.type == "import_spec":
                    path_node = _find_child_by_type(spec, "interpreted_string_literal")
                    alias_nodes = _find_children_by_type(
                        spec, "identifier"
                    ) + _find_children_by_type(spec, "package_identifier")
                    if path_node:
                        pkg_path = _node_text(path_node).strip('"')
                        pkg_name = (
                            pkg_path.rsplit("/", 1)[-1] if "/" in pkg_path else pkg_path
                        )
                        if alias_nodes:
                            alias = _node_text(alias_nodes[-1])
                            info.imports[alias] = pkg_path
                        else:
                            info.imports[pkg_name] = pkg_path
        else:
            for child in node.children:
                if child.type == "import_spec":
                    path_node = _find_child_by_type(child, "interpreted_string_literal")
                    alias_nodes = _find_children_by_type(
                        child, "identifier"
                    ) + _find_children_by_type(child, "package_identifier")
                    if path_node:
                        pkg_path = _node_text(path_node).strip('"')
                        pkg_name = (
                            pkg_path.rsplit("/", 1)[-1] if "/" in pkg_path else pkg_path
                        )
                        if alias_nodes:
                            alias = _node_text(alias_nodes[-1])
                            info.imports[alias] = pkg_path
                        else:
                            info.imports[pkg_name] = pkg_path

    if node.type == "call_expression":
        call_name = _extract_go_call_name(node)
        if call_name:
            info.calls.append(
                (call_name, current_receiver[0] or "", node.start_point[0] + 1)
            )

    for child in node.children:
        _walk_go_node(child, info, current_receiver, depth + 1)


def _extract_go_receiver_type(param_list: Any) -> str:
    for child in param_list.children:
        if child.type in ("parameter_declaration", "variadic_parameter_declaration"):
            type_info = (
                _find_child_by_type(child, "type_identifier")
                or _find_child_by_type(child, "pointer_type")
                or _find_child_by_type(child, "generic_type")
            )
            if type_info:
                text = _node_text(type_info)
                text = text.lstrip("*")
                if "<" in text:
                    text = text[: text.index("<")]
                return text
    return ""


def _extract_go_call_name(node: Any) -> str:
    func = node.child_by_field_name("function")
    if not func:
        return ""
    if func.type == "identifier":
        return _node_text(func)
    if func.type == "selector_expression":
        field = func.child_by_field_name("field")
        operand = func.child_by_field_name("operand")
        field_text = _node_text(field) if field else ""
        operand_text = _node_text(operand) if operand else ""
        if operand_text and field_text:
            return f"{operand_text}.{field_text}"
        return field_text
    return ""


def analyze_go_callgraph(
    project_root: Path,
    go_files: list[str],
    ts_adapter: Any,
    source_map: dict[str, bytes] | None = None,
) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for rel_path in go_files:
        full_path = project_root / rel_path
        if not full_path.exists():
            continue
        try:
            if source_map and rel_path in source_map:
                source = source_map[rel_path]
            else:
                source = full_path.read_bytes()
        except OSError:
            logger.debug(
                "Failed to read %s for call graph analysis", rel_path, exc_info=True
            )
            continue
        tree = ts_adapter.parse(source, "go")
        if not tree:
            logger.debug("Go tree-sitter parse returned None for %s", rel_path)
            continue
        info = ModuleInfo(rel_path)
        _walk_go_node(tree.root_node, info, [None])
        modules[rel_path] = info
    return modules


# ═══════════════════════════════════════════════════════════════════════════════
# Rust 调用图（基于 tree-sitter AST）
# ═══════════════════════════════════════════════════════════════════════════════


def _walk_rust_node(
    node: Any, info: ModuleInfo, current_impl: list[str | None], depth: int = 0
) -> None:
    if depth > 50 or node.child_count == 0:
        return

    if node.type == "function_item":
        name_node = _find_child_by_type(node, "identifier")
        if name_node and not current_impl[0]:
            info.functions[_node_text(name_node)] = node.start_point[0] + 1

    if node.type == "impl_item":
        type_node = node.child_by_field_name("type")
        if type_node is None:
            type_ids = _find_children_by_type(node, "type_identifier")
            type_node = type_ids[-1] if type_ids else None
        if type_node:
            impl_type = _node_text(type_node)
            if impl_type not in info.classes:
                info.classes[impl_type] = ClassInfo(impl_type)
            old = current_impl[0]
            current_impl[0] = impl_type
            for child in node.children:
                if child.type == "function_item":
                    fn_name_node = _find_child_by_type(child, "identifier")
                    if fn_name_node:
                        fn_name = _node_text(fn_name_node)
                        info.classes[impl_type].methods[fn_name] = (
                            child.start_point[0] + 1
                        )
                elif child.type == "declaration_list":
                    for decl_child in child.children:
                        if decl_child.type == "function_item":
                            fn_name_node = _find_child_by_type(decl_child, "identifier")
                            if fn_name_node:
                                fn_name = _node_text(fn_name_node)
                                info.classes[impl_type].methods[fn_name] = (
                                    decl_child.start_point[0] + 1
                                )
                _walk_rust_node(child, info, current_impl, depth + 1)
            current_impl[0] = old
            return

    if node.type == "use_declaration":
        arg = node.child_by_field_name("argument")
        if arg:
            use_path = _node_text(arg)
            parts = use_path.split("::")
            if parts:
                local = parts[-1]
                if local == "{self}" or local == "self":
                    local = parts[-2] if len(parts) >= 2 else parts[-1]
                info.imports[local] = use_path

    if node.type == "call_expression":
        call_name = _extract_rust_call_name(node)
        if call_name:
            info.calls.append(
                (call_name, current_impl[0] or "", node.start_point[0] + 1)
            )

    for child in node.children:
        _walk_rust_node(child, info, current_impl, depth + 1)


def _extract_rust_call_name(node: Any) -> str:
    func = node.child_by_field_name("function")
    if not func:
        return ""
    if func.type == "identifier":
        return _node_text(func)
    if func.type == "field_expression":
        field = func.child_by_field_name("field")
        value = func.child_by_field_name("value")
        field_text = _node_text(field) if field else ""
        value_text = _node_text(value) if value else ""
        if value_text and field_text:
            return f"{value_text}.{field_text}"
        return field_text
    if func.type == "scoped_identifier":
        name = func.child_by_field_name("name")
        path = func.child_by_field_name("path")
        name_text = _node_text(name) if name else ""
        path_text = _node_text(path) if path else ""
        if path_text and name_text:
            return f"{path_text}::{name_text}"
        return name_text
    return ""


def analyze_rust_callgraph(
    project_root: Path,
    rust_files: list[str],
    ts_adapter: Any,
    source_map: dict[str, bytes] | None = None,
) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for rel_path in rust_files:
        full_path = project_root / rel_path
        if not full_path.exists():
            continue
        try:
            if source_map and rel_path in source_map:
                source = source_map[rel_path]
            else:
                source = full_path.read_bytes()
        except OSError:
            logger.debug(
                "Failed to read %s for call graph analysis", rel_path, exc_info=True
            )
            continue
        tree = ts_adapter.parse(source, "rust")
        if not tree:
            logger.debug("Rust tree-sitter parse returned None for %s", rel_path)
            continue
        info = ModuleInfo(rel_path)
        _walk_rust_node(tree.root_node, info, [None])
        modules[rel_path] = info
    return modules


# ═══════════════════════════════════════════════════════════════════════════════
# 统一边解析（Python + TS + Go + Rust）
# ═══════════════════════════════════════════════════════════════════════════════


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
        module_name = (
            fpath.replace("/", ".").replace(".py", "").replace(".__init__", "")
        )
        module_path_map[module_name] = fpath
        parent = module_name.rsplit(".", 1)[0] if "." in module_name else ""
        if parent:
            module_path_map.setdefault(parent, fpath)

    edges: list[tuple[str, str, str, int, str]] = []

    for fpath, info in modules.items():
        for call_name, in_class, call_line in info.calls:
            resolved = _resolve_call(
                call_name,
                in_class,
                fpath,
                info,
                name_to_file,
                class_name_to_file,
                module_path_map,
            )
            if resolved:
                callee_file, callee_name, callee_line, kind = resolved
                caller_name = (
                    f"{in_class}.{call_name.split('.')[-1]}"
                    if in_class
                    else call_name.split(".")[-1]
                )
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
    sep = "::" if "::" in call_name else "."

    if "::" in call_name:
        parts = call_name.split("::")

    if len(parts) >= 2:
        obj_name = parts[0]
        method_name = parts[1]

        if obj_name in ("self", "this", "Self") and in_class:
            cls_info_list = class_name_to_file.get(in_class, [])
            for cfpath, cinfo in cls_info_list:
                if method_name in cinfo.methods:
                    return (
                        cfpath,
                        f"{in_class}{sep}{method_name}",
                        cinfo.methods[method_name],
                        "method_call",
                    )

        if obj_name == "cls" and in_class:
            cls_info_list = class_name_to_file.get(in_class, [])
            for cfpath, cinfo in cls_info_list:
                if method_name in cinfo.methods:
                    return (
                        cfpath,
                        f"{in_class}{sep}{method_name}",
                        cinfo.methods[method_name],
                        "method_call",
                    )

        class_matches = class_name_to_file.get(obj_name, [])
        for cfpath, cinfo in class_matches:
            if method_name in cinfo.methods:
                return (
                    cfpath,
                    f"{obj_name}{sep}{method_name}",
                    cinfo.methods[method_name],
                    "method_call",
                )

        import_target = caller_info.imports.get(obj_name)
        if import_target:
            func_matches = name_to_file.get(method_name, [])
            for mfpath, mline in func_matches:
                if mfpath != caller_file:
                    return (mfpath, method_name, mline, "import_call")

    func_name = parts[0]
    matches = name_to_file.get(func_name, [])
    if matches:
        for mfpath, mline in matches:
            if mfpath != caller_file:
                return (mfpath, func_name, mline, "call")
        return (matches[0][0], func_name, matches[0][1], "call")

    return None
