"""
多语言类型信息提取模块。

从 tree-sitter AST 提取函数/方法的返回类型和参数类型注解。
- Python / TypeScript / Go / Rust / C# / Swift / Kotlin / C++ / Java：提取已有注解
- Python 无注解代码：可选 pytype 推断（作为后处理 pass）

设计原则：
- 不修改 tree-sitter query 体系，在 extract_symbols 之后独立运行
- 类型信息写入 Symbol.return_type / Symbol.params
- 无注解时字段保持空字符串，不猜测
"""

from __future__ import annotations

import logging
from typing import Any

from . import find_child_by_type as _find_child_by_type
from . import find_children_by_type as _find_children_by_type

logger = logging.getLogger("repomap.type_inference")


def _node_text(node: Any) -> str:
    return node.text.decode("utf-8") if getattr(node, "text", None) else ""


def _extract_python_return_type(def_node: Any) -> str:
    for child in def_node.children:
        if child.type == "block":
            break
        if child.type == "type":
            return _node_text(child).strip()
    return ""


def _extract_python_params(def_node: Any) -> str:
    params_node = _find_child_by_type(def_node, "parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_ts_return_type(def_node: Any) -> str:
    for child in def_node.children:
        if child.type == "type_annotation":
            colon_idx = -1
            for i, c in enumerate(child.children):
                if c.type == ":":
                    colon_idx = i
                    break
            if colon_idx >= 0 and colon_idx + 1 < len(child.children):
                type_node = child.children[colon_idx + 1]
                return _node_text(type_node).strip()
            return _node_text(child).lstrip(":").strip()
    return ""


def _extract_ts_params(def_node: Any) -> str:
    params_node = _find_child_by_type(
        def_node, "formal_parameters"
    ) or _find_child_by_type(def_node, "parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_go_return_type(def_node: Any) -> str:
    result_node = _find_child_by_type(def_node, "type")
    if result_node:
        return _node_text(result_node).strip()
    for child in def_node.children:
        if child.type == "parameter_list":
            continue
        if child.type in (
            "type_identifier",
            "pointer_type",
            "interface_type",
            "array_type",
            "slice_type",
            "map_type",
            "channel_type",
            "function_type",
            "struct_type",
            "generic_type",
        ):
            return _node_text(child).strip()
    param_lists = _find_children_by_type(def_node, "parameter_list")
    if len(param_lists) >= 2:
        result_params: list[str] = []
        for child in param_lists[-1].children:
            if child.type in ("(", ")", ",", "comment"):
                continue
            result_params.append(_node_text(child).strip())
        if result_params:
            return ", ".join(result_params)
    return ""


def _extract_go_params(def_node: Any) -> str:
    param_lists = _find_children_by_type(def_node, "parameter_list")
    if not param_lists:
        return ""
    if def_node.type == "method_declaration":
        # method: index 0 = receiver, index 1 = input params
        params_node = param_lists[1] if len(param_lists) > 1 else param_lists[0]
    else:
        # function_declaration: first param_list is input params
        params_node = param_lists[0]
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_rust_return_type(def_node: Any) -> str:
    found_arrow = False
    for child in def_node.children:
        if child.type == "->":
            found_arrow = True
            continue
        if found_arrow:
            return _node_text(child).strip()
    return ""


def _extract_rust_params(def_node: Any) -> str:
    params_node = _find_child_by_type(def_node, "parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_java_return_type(def_node: Any) -> str:
    for child in def_node.children:
        if child.type == "type_identifier":
            return _node_text(child).strip()
        if child.type in (
            "void_type",
            "integral_type",
            "floating_point_type",
            "boolean_type",
            "generic_type",
        ):
            return _node_text(child).strip()
    return ""


def _extract_java_params(def_node: Any) -> str:
    params_node = _find_child_by_type(def_node, "formal_parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_kotlin_return_type(def_node: Any) -> str:
    for child in def_node.children:
        if child.type == "type":
            return _node_text(child).lstrip(":").strip()
    return ""


def _extract_kotlin_params(def_node: Any) -> str:
    params_node = _find_child_by_type(
        def_node, "function_value_parameters"
    ) or _find_child_by_type(def_node, "parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_swift_return_type(def_node: Any) -> str:
    for child in def_node.children:
        if child.type in ("return_type", "type_annotation"):
            return _node_text(child).lstrip("->").strip()
    return ""


def _extract_swift_params(def_node: Any) -> str:
    params_node = _find_child_by_type(
        def_node, "parameter_list"
    ) or _find_child_by_type(def_node, "parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_c_sharp_return_type(def_node: Any) -> str:
    for child in def_node.children:
        if child.type == "type":
            return _node_text(child).strip()
        if child.type in (
            "void_keyword",
            "int_keyword",
            "string_keyword",
            "bool_keyword",
            "float_keyword",
            "double_keyword",
            "object_keyword",
            "var_keyword",
        ):
            return _node_text(child).strip()
    return ""


def _extract_c_sharp_params(def_node: Any) -> str:
    params_node = _find_child_by_type(
        def_node, "parameter_list"
    ) or _find_child_by_type(def_node, "parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


def _extract_cpp_return_type(def_node: Any) -> str:
    for child in def_node.children:
        if child.type in (
            "type_identifier",
            "primitive_type",
            "sized_type_specifier",
            "auto",
            "pointer_type",
            "reference_type",
            "qualified_identifier",
        ):
            return _node_text(child).strip()
    return ""


def _extract_cpp_params(def_node: Any) -> str:
    params_node = _find_child_by_type(
        def_node, "parameter_list"
    ) or _find_child_by_type(def_node, "parameters")
    if not params_node:
        declarator = _find_child_by_type(
            def_node, "function_declarator"
        ) or _find_child_by_type(def_node, "declarator")
        if declarator:
            params_node = _find_child_by_type(
                declarator, "parameter_list"
            ) or _find_child_by_type(declarator, "parameters")
    if not params_node:
        return ""
    parts: list[str] = []
    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        parts.append(_node_text(child).strip())
    return ", ".join(parts) if parts else ""


_EXTRACTORS: dict[str, tuple[Any, Any]] = {
    "python": (_extract_python_return_type, _extract_python_params),
    "typescript": (_extract_ts_return_type, _extract_ts_params),
    "tsx": (_extract_ts_return_type, _extract_ts_params),
    "go": (_extract_go_return_type, _extract_go_params),
    "rust": (_extract_rust_return_type, _extract_rust_params),
    "java": (_extract_java_return_type, _extract_java_params),
    "kotlin": (_extract_kotlin_return_type, _extract_kotlin_params),
    "swift": (_extract_swift_return_type, _extract_swift_params),
    "c_sharp": (_extract_c_sharp_return_type, _extract_c_sharp_params),
    "cpp": (_extract_cpp_return_type, _extract_cpp_params),
}

_FUNC_NODE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition"}),
    "typescript": frozenset(
        {
            "function_declaration",
            "method_definition",
            "arrow_function",
            "function",
            "generator_function_declaration",
        }
    ),
    "tsx": frozenset(
        {
            "function_declaration",
            "method_definition",
            "arrow_function",
            "function",
            "generator_function_declaration",
        }
    ),
    "go": frozenset({"function_declaration", "method_declaration"}),
    "rust": frozenset({"function_item"}),
    "java": frozenset({"method_declaration", "constructor_declaration"}),
    "kotlin": frozenset({"function_declaration"}),
    "swift": frozenset({"function_declaration"}),
    "c_sharp": frozenset({"method_declaration", "constructor_declaration"}),
    "cpp": frozenset({"function_definition", "declaration"}),
}


def extract_types_for_file(
    tree: Any,
    lang: str,
    sym_ids: list[str],
    all_symbols: dict[str, Any],
) -> int:
    """
    单文件类型提取：遍历 AST，为函数/方法符号填充 return_type 和 params。

    参数：
        tree: tree-sitter 解析结果
        lang: 语言标识
        sym_ids: 该文件的 symbol_id 列表
        all_symbols: 全局 symbol_id → Symbol 映射

    返回：成功填充类型信息的字段数量
    """
    extractors = _EXTRACTORS.get(lang)
    func_types = _FUNC_NODE_TYPES.get(lang)
    if not extractors or not func_types:
        return 0

    extract_rt, extract_params = extractors

    symbol_line_map: dict[int, Any] = {}
    for sym_id in sym_ids:
        sym = all_symbols.get(sym_id)
        if sym and sym.kind in ("function", "method", "lambda"):
            symbol_line_map[sym.line] = sym

    if not symbol_line_map:
        return 0

    enriched = [0]

    def _walk(node: Any, depth: int = 0) -> None:
        if depth > 30:
            logger.debug(
                "Type inference recursion depth limit reached at depth 30 "
                "for node type %r in language %s",
                node.type, lang,
            )
            return
        if node.type in func_types:
            node_start_line = node.start_point[0] + 1
            sym = symbol_line_map.get(node_start_line)
            if sym and not sym.return_type and not sym.params:
                rt = extract_rt(node)
                params = extract_params(node)
                if rt:
                    sym.return_type = rt
                    enriched[0] += 1
                if params:
                    sym.params = params
                    enriched[0] += 1
        for child in node.children:
            _walk(child, depth + 1)

    _walk(tree.root_node)
    return enriched[0]
