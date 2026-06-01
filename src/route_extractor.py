#!/usr/bin/env python3
"""
HTTP 路由提取 — 从 AST 中检测和提取 HTTP 路由定义。

支持框架：FastAPI (Python), Express (JS/TS), NestJS (TS), Gin/Echo/Chi (Go),
Axum (Rust), Spring Boot (Java)。

所有函数接收 adapter（TreeSitterAdapter 实例）作为第一个参数，
通过 duck typing 调用 adapter 的工具方法。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("repomap")


def extract_http_routes(adapter: Any, tree: Any, lang: str, file: str) -> list[Any]:
    """从 AST 中提取 HTTP 路由定义。

    支持框架：FastAPI (Python), Express (JS/TS), Axum (Rust)。
    route inventory 只输出严格匹配的生产路由定义，避免把测试 DSL、日志、
    Array/Option 等普通调用误判为 HTTP route。
    """
    from . import HttpRoute

    if _should_skip_route_file(file):
        return []

    queries = adapter._queries.get(lang, {})
    route_query = queries.get("http_route")
    explicit_query = queries.get("http_route_explicit")
    if not route_query and not explicit_query:
        return []

    routes: list[HttpRoute] = []
    for q in (route_query, explicit_query):
        if q is None:
            continue
        for captures in _run_query_matches(adapter, q, tree.root_node, lang):
            route = _http_route_from_captures(adapter, captures, lang, file)
            if route is not None:
                routes.append(route)

    return sorted(
        routes,
        key=lambda route: (
            route.file,
            route.line,
            route.method,
            route.path,
            route.handler,
        ),
    )


def _validate_http_route(
    adapter: Any,
    lang: str,
    captures: dict[str, list[Any]],
    method: str,
    path_node: Any,
    handler_node: Any,
    file: str,
) -> Any | None:
    """统一分发到各语言的路由验证器。"""
    if lang == "python":
        return _validate_python_route(adapter, captures, method)
    if lang in ("javascript", "typescript", "tsx"):
        return _validate_js_route(
            adapter, captures, method, path_node, handler_node, file
        )
    if lang == "go":
        return _validate_go_route(captures, method)
    if lang == "rust":
        return _validate_rust_route(adapter, captures, method)
    if lang == "java":
        return _validate_java_route(captures, method)
    return None


def _validate_python_route(
    adapter: Any, captures: dict[str, list[Any]], method: str
) -> tuple[str, str] | None:
    """验证 Python (Flask/FastAPI) 路由并返回 (method, framework)。"""
    obj = adapter._text(_first_capture(captures, "_obj"))
    if obj not in {
        "app",
        "router",
        "api",
        "bp",
        "blueprint",
        "routes",
        "endpoints",
    } or method not in {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "route",
    }:
        return None
    if method == "route":
        methods_node = _first_capture(captures, "_methods")
        if methods_node is not None:
            method_text = adapter._text(methods_node).strip("\"'")
            method = method_text.lower()
        else:
            method = "get"
    if method not in {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
    }:
        return None
    framework = (
        "flask" if method == "route" or obj in {"bp", "blueprint"} else "fastapi"
    )
    return method, framework


def _validate_js_route(
    adapter: Any,
    captures: dict[str, list[Any]],
    method: str,
    path_node: Any,
    handler_node: Any,
    file: str,
) -> Any | None:
    """验证 JS/TS (Express/NestJS) 路由。"""
    from . import HttpRoute

    router_capture = _first_capture(captures, "_router")
    if router_capture is None:
        nestjs_methods = {
            "get",
            "post",
            "put",
            "delete",
            "patch",
            "head",
            "options",
            "all",
        }
        if method in nestjs_methods:
            path = adapter._string_literal_value(path_node)
            if not path:
                return None
            handler_name = _route_handler_name(adapter, handler_node)
            if not handler_name:
                return None
            return HttpRoute(
                method=method.upper(),
                path=path,
                handler=handler_name,
                file=file,
                line=path_node.start_point[0] + 1,
                framework="nestjs",
            )
    if router_capture is None:
        return None
    router = adapter._text(router_capture)
    if router not in {
        "app",
        "router",
        "api",
        "server",
        "routes",
    } or method not in {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "use",
        "all",
    }:
        return None
    if method in {
        "describe",
        "test",
        "it",
        "expect",
        "log",
        "some",
        "map",
        "filter",
        "find",
        "reduce",
        "foreach",
    }:
        return None
    return method, "express"


def _validate_go_route(
    captures: dict[str, list[Any]], method: str
) -> tuple[str, str] | None:
    """验证 Go HTTP 路由。"""
    if method in {"some", "ok", "err", "unwrap", "map", "filter"}:
        return None
    return method, "go-http"


def _validate_rust_route(
    adapter: Any, captures: dict[str, list[Any]], method: str
) -> tuple[str, str] | None:
    """验证 Rust (Axum) 路由。"""
    method_name = adapter._text(_first_capture(captures, "_method_name"))
    if method_name != "route" or method not in {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
    }:
        return None
    if method in {"some", "ok", "err", "is_some", "unwrap", "map", "filter"}:
        return None
    return method, "axum"


def _validate_java_route(
    captures: dict[str, list[Any]], method: str
) -> tuple[str, str] | None:
    """验证 Java (Spring) 路由。"""
    if method in {"some", "ok", "err", "unwrap", "map", "filter"}:
        return None
    return method, "spring"


def _http_route_from_captures(
    adapter: Any, captures: dict[str, list[Any]], lang: str, file: str
) -> Any | None:
    from . import HttpRoute

    path_node = _first_capture(captures, "path")
    handler_node = _first_capture(captures, "handler")
    if path_node is None or handler_node is None:
        return None

    method_node = _first_capture(captures, "method") or _first_capture(
        captures, "http_method"
    )
    method = (adapter._text(method_node) if method_node is not None else "").lower()
    if not method:
        return None

    result = _validate_http_route(
        adapter, lang, captures, method, path_node, handler_node, file
    )
    if result is None:
        return None
    if isinstance(result, HttpRoute):
        return result
    method, framework = result

    path = adapter._string_literal_value(path_node)
    if not path:
        return None
    handler_name = _route_handler_name(adapter, handler_node)
    if not handler_name:
        return None

    return HttpRoute(
        method=method.upper(),
        path=path,
        handler=handler_name,
        file=file,
        line=handler_node.start_point[0] + 1,
        framework=framework,
    )


def _run_query_matches(
    adapter: Any, query: Any, root: Any, lang: str = "unknown"
) -> list[dict[str, list[Any]]]:
    """按 tree-sitter match 返回 captures，避免跨匹配错位拼接 route。"""
    try:
        from tree_sitter import QueryCursor  # type: ignore

        cursor = QueryCursor(query)
        if hasattr(cursor, "matches"):
            raw_matches = cursor.matches(root)
            results: list[dict[str, list[Any]]] = []
            for item in raw_matches:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                _, captures = item
                if not isinstance(captures, dict) or not captures:
                    continue
                normalized: dict[str, list[Any]] = {}
                for cap_name, nodes in captures.items():
                    normalized[cap_name] = nodes if isinstance(nodes, list) else [nodes]
                results.append(normalized)
            if results:
                return results
    except Exception as e:
        logger.debug(f"Query match run error [{lang}]: {e}")

    # 兼容旧 runtime：只能拿到 capture 列表时，按捕获起始行粗分组后再严格校验。
    captures_by_line: dict[int, dict[str, list[Any]]] = {}
    for cap_name, node in adapter._run_query(query, root, lang):
        line = node.start_point[0]
        captures_by_line.setdefault(line, {}).setdefault(cap_name, []).append(node)
    return [captures for _, captures in sorted(captures_by_line.items())]


def _first_capture(captures: dict[str, list[Any]], name: str) -> Any | None:
    nodes = captures.get(name) or []
    return nodes[0] if nodes else None


def _should_skip_route_file(file: str) -> bool:
    normalized = file.replace("\\", "/")
    parts = {part.lower() for part in normalized.split("/")}
    if parts & {"e2e", "tests", "__tests__"}:
        return True
    name = normalized.rsplit("/", 1)[-1].lower()
    return bool(
        re.search(r"(_test\.rs|\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts))$", name)
    )


def _route_handler_name(adapter: Any, node: Any) -> str:
    explicit = adapter._identifier_text(node)
    if explicit:
        return explicit
    if node.type in {"arrow_function", "function_expression", "lambda"}:
        return adapter._anonymous_symbol_name(node)
    return adapter._text(node)
