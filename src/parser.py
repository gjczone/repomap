#!/usr/bin/env python3
"""
Repo Map Parser — Tree-sitter Analysis Layer
==============================================
负责代码解析、符号提取、import/export 绑定提取。

此模块独立于引擎层，可被单独使用进行代码分析。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from . import JSImportBinding, JSExportBinding, Symbol

logger = logging.getLogger("repomap")

# ═══════════════════════════════════════════════════════════════════════════════
# Tree-sitter Queries（内嵌，无需外部 .scm 文件）
# ═══════════════════════════════════════════════════════════════════════════════

QUERIES: dict[str, dict[str, str]] = {
    "python": {
        "function": """
            (function_definition name: (identifier) @name) @definition.function
            (decorated_definition (function_definition name: (identifier) @name)) @definition.function
            (class_definition body: (block (function_definition name: (identifier) @name))) @definition.method
            (assignment left: (identifier) @name right: (lambda)) @definition.lambda
        """,
        "class": """
            (class_definition name: (identifier) @name) @definition.class
            (decorated_definition (class_definition name: (identifier) @name)) @definition.class
        """,
        "import": """
            (import_statement name: (dotted_name) @name)
            (import_statement name: (aliased_import name: (dotted_name) @name))
            (import_from_statement module_name: (dotted_name) @name)
            (import_from_statement module_name: (relative_import) @name)
        """,
        "call": """
            (call function: (identifier) @name) @reference.call
            (call function: (attribute attribute: (identifier) @name)) @reference.call
        """,
        "http_route": """
            ;; FastAPI: @app.get("/path") / Flask: @app.route("/path", methods=["GET"])
            ;; Blueprint: @bp.route("/path") / @blueprint.get("/path")
            (decorated_definition
              (decorator
                (call
                  function: (attribute
                    object: (identifier) @_obj
                    attribute: (identifier) @method)
                  arguments: (argument_list (string) @path
                    (keyword_argument
                      name: (identifier) @_kw_name
                      value: (list (string) @_methods))?)))
              definition: (function_definition name: (identifier) @handler))
            (#match? @_obj "^(app|router|api|bp|blueprint|routes|endpoints)$")
            (#match? @method "^(get|post|put|delete|patch|head|options|route)$")
        """,
    },
    "javascript": {
        "function": """
            (function_declaration name: (identifier) @name) @definition.function
            (variable_declarator name: (identifier) @name value: (arrow_function)) @definition.function
            (variable_declarator name: (identifier) @name value: (function_expression)) @definition.function
            (method_definition name: (property_identifier) @name) @definition.method
        """,
        "anonymous_function": """
            (arrow_function) @definition.anonymous_function
            (function_expression) @definition.anonymous_function
        """,
        "class": """
            (class_declaration name: (identifier) @name) @definition.class
        """,
        "import": """
            (import_statement source: (string) @source)
            (import_specifier name: (identifier) @name)
            (import_clause (identifier) @name)
        """,
        "call": """
            (call_expression function: (identifier) @name) @reference.call
            (call_expression function: (member_expression property: (property_identifier) @name)) @reference.call
        """,
        "http_route": """
            ;; Express: app.get("/path", handler) / router.post("/path", handler)
            (call_expression
              function: (member_expression
                object: (identifier) @_router
                property: (property_identifier) @method)
              arguments: (arguments
                (string) @path
                .
                [(identifier) @handler (arrow_function) @handler (function_expression) @handler]))
            (#match? @_router "^(app|router|api|server|routes)$")
            (#match? @method "^(get|post|put|delete|patch|use|all)$")
        """,
    },
    # TypeScript：使用专用绑定时节点名不同；回退到 JS parser 时 TS 特有语法会报 ERROR，
    # 此处只保留两个 parser 都支持的通用模式
    "typescript": {
        "function": """
            (function_declaration name: (identifier) @name) @definition.function
            (variable_declarator name: (identifier) @name value: (arrow_function)) @definition.function
            (method_definition name: (property_identifier) @name) @definition.method
        """,
        "anonymous_function": """
            (arrow_function) @definition.anonymous_function
            (function_expression) @definition.anonymous_function
        """,
        "class": """
            (class_declaration name: (_) @name) @definition.class
        """,
        "import": """
            (import_statement source: (string) @source)
            (import_specifier name: (identifier) @name)
            (import_clause (identifier) @name)
        """,
        "call": """
            (call_expression function: (identifier) @name) @reference.call
            (call_expression function: (member_expression property: (property_identifier) @name)) @reference.call
        """,
        "http_route": """
            ;; Express: app.get("/path", handler) / router.post("/path", handler)
            (call_expression
              function: (member_expression
                object: (identifier) @_router
                property: (property_identifier) @method)
              arguments: (arguments
                (string) @path
                .
                [(identifier) @handler (arrow_function) @handler (function_expression) @handler]))
            (#match? @_router "^(app|router|api|server|routes)$")
            (#match? @method "^(get|post|put|delete|patch|use|all)$")
        """,
        "http_route_nestjs": """
            ;; NestJS: @Controller("prefix") class + @Get("path") method
            (decorator
              (call_expression
                function: (identifier) @method
                arguments: (arguments (string) @path)))
            (#match? @method "^(Get|Post|Put|Delete|Patch|Head|Options|All)$")
        """,
    },
    "go": {
        "function": """
            (function_declaration name: (identifier) @name) @definition.function
            (method_declaration name: (field_identifier) @name) @definition.method
        """,
        "class": """
            (type_spec name: (type_identifier) @name type: (struct_type)) @definition.struct
            (type_spec name: (type_identifier) @name type: (interface_type)) @definition.interface
        """,
        "import": """
            (import_spec path: (interpreted_string_literal) @path)
        """,
        "call": """
            (call_expression function: (identifier) @name) @reference.call
            (call_expression function: (selector_expression field: (field_identifier) @name)) @reference.call
        """,
        "http_route": """
            ;; gin: r.GET("/path", handler), echo: e.GET("/path", handler)
            ;; chi: r.Get("/path", handler), net/http: mux.HandleFunc("/path", handler)
            (call_expression
              function: (selector_expression
                operand: (identifier) @_router
                field: (field_identifier) @method)
              arguments: (argument_list
                (interpreted_string_literal) @path
                .
                (identifier) @handler))
            (#match? @method "^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|Get|Post|Put|Delete|Patch|Head|Options)$")
        """,
    },
    "rust": {
        "function": """
            (function_item name: (identifier) @name) @definition.function
            (function_signature_item name: (identifier) @name) @definition.trait_method
        """,
        "class": """
            (struct_item name: (type_identifier) @name) @definition.struct
            (enum_item name: (type_identifier) @name) @definition.enum
            (trait_item name: (type_identifier) @name) @definition.trait
            (impl_item type: (type_identifier) @name) @definition.impl
            (type_item name: (type_identifier) @name) @definition.type
            (mod_item name: (identifier) @name) @definition.module
        """,
        "import": """
            ; 捕获完整 scoped_identifier（支持多段 use a::b::C）
            (use_declaration
                argument: (scoped_identifier) @full_path)
            ; 捕获 use crate::module::{A, B} 中的 scoped_use_list
            (use_declaration
                argument: (scoped_use_list) @full_path)
            ; 捕获 extern crate name;
            (extern_crate_declaration name: (identifier) @name)
            ; 捕获 use module;
            (use_declaration argument: (identifier) @name)
        """,
        "call": """
            (call_expression function: (identifier) @name) @reference.call
            (call_expression function: (field_expression field: (field_identifier) @name)) @reference.call
            (call_expression function: (scoped_identifier name: (identifier) @name)) @reference.call
        """,
        "http_route": """
            ;; Axum: .route("/path", get(handler))
            (call_expression
              function: (field_expression
                field: (field_identifier) @_method_name)
              arguments: (arguments
                (string_literal) @path
                (call_expression
                  function: (identifier) @http_method
                  arguments: (arguments (identifier) @handler))))
            (#eq? @_method_name "route")
            (#match? @http_method "^(get|post|put|delete|patch|head|options)$")
        """,
    },
    "c": {
        "function": """
            (function_definition
              declarator: (function_declarator
                declarator: (identifier) @name)) @definition.function
        """,
        "class": """
            (struct_specifier name: (type_identifier) @name) @definition.struct
            (union_specifier name: (type_identifier) @name) @definition.union
            (enum_specifier name: (type_identifier) @name) @definition.enum
        """,
        "import": """
            (preproc_include path: (_) @path)
        """,
        "call": """
            (call_expression function: (identifier) @name) @reference.call
        """,
    },
    "java": {
        "function": """
            (method_declaration name: (identifier) @name) @definition.method
            (constructor_declaration name: (identifier) @name) @definition.method
        """,
        "class": """
            (class_declaration name: (identifier) @name) @definition.class
            (interface_declaration name: (identifier) @name) @definition.interface
            (enum_declaration name: (identifier) @name) @definition.enum
        """,
        "import": """
            (import_declaration (scoped_identifier) @name)
            (import_declaration (identifier) @name)
        """,
        "call": """
            (method_invocation name: (identifier) @name) @reference.call
        """,
        "http_route": """
            ;; Spring Boot: @GetMapping("/path") on a controller method
            ;; Simplified form: @GetMapping("/path") — value is direct string_literal
            (method_declaration
              (modifiers
                (annotation
                  name: (identifier) @method
                  arguments: (annotation_argument_list
                    (string_literal) @path)))
              name: (identifier) @handler)
            (#match? @method "^(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)$")
        """,
        "http_route_explicit": """
            ;; Spring Boot: @RequestMapping(value="/path", method=GET) — element_value_pair form
            (method_declaration
              (modifiers
                (annotation
                  name: (identifier) @method
                  arguments: (annotation_argument_list
                    (element_value_pair
                      value: (string_literal) @path))))
              name: (identifier) @handler)
            (#match? @method "^(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)$")
        """,
    },
    "kotlin": {
        "function": (
            "(function_declaration name: (identifier) @name) @definition.function"
        ),
        "class": (
            "(class_declaration name: (identifier) @name) @definition.class\n"
            "(object_declaration name: (identifier) @name) @definition.class"
        ),
        "import": "(import (qualified_identifier) @name)",
        "call": (
            "(call_expression (identifier) @name) @reference.call\n"
            "(call_expression (navigation_expression (identifier) @name)) @reference.call"
        ),
    },
    "swift": {
        "function": (
            "(function_declaration name: (simple_identifier) @name) @definition.function"
        ),
        "class": (
            "(class_declaration name: (type_identifier) @name) @definition.class\n"
            "(class_declaration name: (type_identifier) @name) @definition.struct\n"
            "(class_declaration name: (type_identifier) @name) @definition.enum\n"
            "(protocol_declaration name: (type_identifier) @name) @definition.protocol"
        ),
        "import": "(import_declaration (identifier) @name)",
        "call": (
            "(call_expression (simple_identifier) @name) @reference.call\n"
            "(call_expression (navigation_expression (simple_identifier) @name)) @reference.call"
        ),
    },
    "cpp": {
        "function": """
            (function_definition
              declarator: (function_declarator
                declarator: [(identifier) (qualified_identifier)] @name)) @definition.function
        """,
        "class": """
            (class_specifier name: (type_identifier) @name) @definition.class
            (struct_specifier name: (type_identifier) @name) @definition.struct
            (enum_specifier name: (type_identifier) @name) @definition.enum
        """,
        "import": """
            (preproc_include path: (_) @path)
        """,
        "call": """
            (call_expression function: [(identifier) (qualified_identifier)] @name) @reference.call
        """,
    },
    "c_sharp": {
        "function": """
            (method_declaration name: (identifier) @name) @definition.method
            (local_function_statement name: (identifier) @name) @definition.function
        """,
        "class": """
            (class_declaration name: (identifier) @name) @definition.class
            (interface_declaration name: (identifier) @name) @definition.interface
            (struct_declaration name: (identifier) @name) @definition.struct
            (enum_declaration name: (identifier) @name) @definition.enum
        """,
        "import": """
            (using_directive name: [(identifier) (qualified_name)] @name)
        """,
        "call": """
            (invocation_expression function: (identifier) @name) @reference.call
            (invocation_expression function: (member_access_expression name: (identifier) @name)) @reference.call
        """,
    },
    "php": {
        "function": """
            (function_definition name: (name) @name) @definition.function
            (method_declaration name: (name) @name) @definition.method
        """,
        "class": """
            (class_declaration name: (name) @name) @definition.class
            (interface_declaration name: (name) @name) @definition.interface
            (trait_declaration name: (name) @name) @definition.trait
            (enum_declaration name: (name) @name) @definition.enum
        """,
        "import": """
            (namespace_use_declaration (qualified_name) @name)
        """,
        "call": """
            (function_call_expression function: (name) @name) @reference.call
            (member_call_expression name: (name) @name) @reference.call
        """,
    },
    "ruby": {
        "function": """
            (method name: (identifier) @name) @definition.method
            (singleton_method name: (identifier) @name) @definition.method
        """,
        "class": """
            (class name: (constant) @name) @definition.class
            (module name: (constant) @name) @definition.module
        """,
        "import": """
            (call method: (identifier) @_method arguments: (argument_list (string) @path))
            (#match? @_method "^(require|require_relative|load)$")
        """,
        "call": """
            (call method: (identifier) @name) @reference.call
        """,
    },
    "html": {},
    "css": {},
    "json": {},
}
QUERIES["tsx"] = QUERIES["typescript"]

EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".json": "json",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".cs": "c_sharp",
    ".php": "php",
    ".phtml": "php",
    ".rb": "ruby",
}

# ═══════════════════════════════════════════════════════════════════════════════
# Tree-sitter 适配层
# ═══════════════════════════════════════════════════════════════════════════════


class TreeSitterAdapter:
    """
    封装 tree-sitter 多语言解析。
    ‑ 兼容 tree-sitter 0.20 ~ 0.25+（捕获结果格式差异）
    ‑ 懒加载语言绑定，未安装的静默跳过
    """

    def __init__(self) -> None:
        self.parsers: dict[str, Any] = {}
        # lang -> query_type -> compiled Query
        self._queries: dict[str, dict[str, Any]] = {}
        self._query_error_logged: dict[str, bool] = {}  # 按语言独立跟踪查询失败日志
        self._fallback_langs: set[str] = set()  # 使用回退解析器的语言
        self._init_parsers()

    # ── 初始化 ─────────────────────────────────────────────────────────────────

    def _init_parsers(self) -> None:
        """加载各语言 parser，并预编译 queries。"""
        bindings = {
            "python": ("tree_sitter_python", "language"),
            "javascript": ("tree_sitter_javascript", "language"),
            "go": ("tree_sitter_go", "language"),
            "rust": ("tree_sitter_rust", "language"),
            "html": ("tree_sitter_html", "language"),
            "css": ("tree_sitter_css", "language"),
            "json": ("tree_sitter_json", "language"),
            "c": ("tree_sitter_c", "language"),
            "java": ("tree_sitter_java", "language"),
            "kotlin": ("tree_sitter_kotlin", "language"),
            "swift": ("tree_sitter_swift", "language"),
            "cpp": ("tree_sitter_cpp", "language"),
            "c_sharp": ("tree_sitter_c_sharp", "language"),
            "php": ("tree_sitter_php", "language"),
            "ruby": ("tree_sitter_ruby", "language"),
        }

        # 动态导入，失败则跳过
        for lang, (module, attr) in bindings.items():
            try:
                mod = __import__(module)
                lang_fn = getattr(mod, attr)
                from tree_sitter import Language, Parser  # type: ignore

                self.parsers[lang] = Parser(Language(lang_fn()))
                logger.debug(f"Parser loaded: {lang}")
            except Exception as e:
                logger.warning(f"Parser unavailable [{lang}]: {e}")

        # TypeScript / TSX：优先专用绑定，TypeScript 回退到 JavaScript parser，TSX 不回退以避免误解析 JSX。
        try:
            from tree_sitter_typescript import language_typescript, language_tsx  # type: ignore
            from tree_sitter import Language, Parser  # type: ignore

            self.parsers["typescript"] = Parser(Language(language_typescript()))
            self.parsers["tsx"] = Parser(Language(language_tsx()))
            logger.debug("Parser loaded: typescript (dedicated)")
            logger.debug("Parser loaded: tsx (dedicated)")
        except Exception:
            logger.debug(
                "TypeScript+TSX parser load failed, trying TypeScript-only",
                exc_info=True,
            )
            try:
                from tree_sitter_typescript import language_typescript  # type: ignore
                from tree_sitter import Language, Parser  # type: ignore

                self.parsers["typescript"] = Parser(Language(language_typescript()))
                logger.debug("Parser loaded: typescript (dedicated)")
            except Exception:
                if "javascript" in self.parsers:
                    self.parsers["typescript"] = self.parsers["javascript"]
                    self._fallback_langs.add("typescript")
                    logger.warning(
                        "TypeScript parser unavailable, falling back to JavaScript parser. "
                        "TS-specific syntax (types, interfaces, enums) will not be extracted."
                    )

        # 预编译 queries —— 只对已加载的语言
        self._precompile_queries()

    def _precompile_queries(self) -> None:
        """预编译所有 tree-sitter queries，失败时记录警告。

        要求 tree-sitter >= 0.21（支持 Query 类）
        """
        try:
            from tree_sitter import Query  # type: ignore
        except ImportError:
            logger.warning(
                "tree-sitter Query class not available (requires >=0.21), queries disabled"
            )
            return

        for lang, patterns in QUERIES.items():
            if lang not in self.parsers:
                continue
            self._queries[lang] = {}
            parser = self.parsers[lang]
            for qtype, src in patterns.items():
                try:
                    q = Query(parser.language, src)
                    self._queries[lang][qtype] = q
                except Exception as e:
                    logger.warning(f"Query compile failed [{lang}/{qtype}]: {e}")

    # ── 公开接口 ────────────────────────────────────────────────────────────────

    def parse(self, content: bytes, lang: str) -> Any | None:
        parser = self.parsers.get(lang)
        if not parser:
            return None

        # 内容大小限制（防止内存溢出）
        MAX_PARSE_SIZE = 10 * 1024 * 1024  # 10MB
        if len(content) > MAX_PARSE_SIZE:
            logger.warning(
                f"File too large for parsing ({len(content)} bytes > {MAX_PARSE_SIZE}), skipping"
            )
            return None

        # 检测异常内容模式（可能导致解析器崩溃）
        try:
            # 使用 bytes 级别计数快速评估嵌套深度，避免逐字符 Python 循环
            # 仅扫描前 256KB 作为启发式检查，大文件只需判断是否需要跳过
            scan_bytes = content[: 256 * 1024]
            open_count = (
                scan_bytes.count(b"(") + scan_bytes.count(b"{") + scan_bytes.count(b"[")
            )
            close_count = (
                scan_bytes.count(b")") + scan_bytes.count(b"}") + scan_bytes.count(b"]")
            )
            # 括号总数不平衡或过多时视为极端嵌套风险
            if (
                abs(open_count - close_count) > 100
                or max(open_count, close_count) > 1000
            ):
                logger.warning(
                    f"Extreme nesting risk detected ({open_count} open, {close_count} close brackets in first 256KB), "
                    f"skipping file to prevent parser crash"
                )
                return None
        except (UnicodeDecodeError, ValueError):
            logger.debug(
                "Nesting depth check failed (encoding), proceeding to parse",
                exc_info=True,
            )

        try:
            return parser.parse(content)
        except RecursionError:
            logger.warning(f"Parser recursion limit exceeded for {lang}, skipping file")
            return None
        except MemoryError:
            logger.warning(f"Parser out of memory for {lang}, skipping file")
            return None
        except Exception as e:
            logger.warning(f"Parse error [{lang}]: {e}")
            return None

    def extract_symbols(
        self, tree: Any, lang: str, file: str, content: bytes
    ) -> list[Symbol]:
        """从 AST 提取函数 / 类等符号定义。"""
        if lang == "html":
            return self._extract_html_symbols(tree, file)
        if lang == "css":
            return self._extract_css_symbols(tree, file)
        if lang == "json":
            return self._extract_json_symbols(tree, file)

        symbols_by_id: dict[str, Symbol] = {}
        root = tree.root_node

        for qtype in ("function", "class"):
            query = self._queries.get(lang, {}).get(qtype)
            if not query:
                continue

            captures = self._run_query(query, root, lang)
            name_nodes: list[Any] = []
            def_nodes: list[tuple[Any, str]] = []

            for cap_name, node in captures:
                if cap_name == "name":
                    name_nodes.append(node)
                elif "definition" in cap_name or "export" in cap_name:
                    def_nodes.append((node, cap_name))

            names_processed = 0
            matches_found = 0
            for name_node in name_nodes:
                if names_processed >= 5000:
                    break
                names_processed += 1
                matching_defs = []
                def_searched = 0
                for def_node, def_cap in def_nodes:
                    if def_searched >= 500:
                        break
                    def_searched += 1
                    if self._within(name_node, def_node):
                        matching_defs.append((def_node, def_cap))
                        matches_found += 1
                        if matches_found >= 5000:
                            break
                matching_defs.sort(
                    key=lambda item: (
                        (
                            item[0].end_point[0] - item[0].start_point[0],
                            item[0].end_point[1] - item[0].start_point[1],
                        ),
                        item[0].start_point[0],
                        item[0].start_point[1],
                    )
                )
                for def_node, def_cap in matching_defs:
                    kind = def_cap.split(".")[-1] if "." in def_cap else def_cap
                    vis = "exported" if "export" in def_cap else "public"
                    name = self._text(name_node)
                    if not name:
                        break
                    if (
                        lang == "python"
                        and kind == "function"
                        and self._is_python_class_member(def_node)
                    ):
                        kind = "method"
                    # Python: _ 前缀视为 private
                    if (
                        lang == "python"
                        and name.startswith("_")
                        and not name.startswith("__")
                    ):
                        vis = "private"
                    sym_id = f"{file}::{name}::{name_node.start_point[0] + 1}"
                    symbols_by_id[sym_id] = Symbol(
                        id=sym_id,
                        name=name,
                        kind=kind,
                        file=file,
                        line=name_node.start_point[0] + 1,
                        end_line=def_node.end_point[0] + 1,
                        col=name_node.start_point[1],
                        visibility=vis,
                        docstring=self._docstring(def_node, lang),
                        signature=self._signature(def_node, lang),
                    )
                    break

        for symbol in self._extract_exported_function_expression_symbols(
            tree, lang, file
        ):
            symbols_by_id.setdefault(symbol.id, symbol)

        for symbol in self._extract_object_literal_method_symbols(tree, lang, file):
            symbols_by_id.setdefault(symbol.id, symbol)

        for symbol in self._extract_anonymous_symbols(tree, lang, file):
            symbols_by_id.setdefault(symbol.id, symbol)

        return sorted(
            symbols_by_id.values(),
            key=lambda symbol: (
                symbol.file,
                symbol.line,
                symbol.end_line,
                symbol.col,
                symbol.name,
                symbol.kind,
            ),
        )

    def _extract_exported_function_expression_symbols(
        self, tree: Any, lang: str, file: str
    ) -> list[Symbol]:
        if lang not in ("javascript", "typescript", "tsx"):
            return []
        symbols_by_id: dict[str, Symbol] = {}
        for node in self._walk_tree(tree.root_node):
            if node.type not in {"function_expression", "arrow_function"}:
                continue
            if not self._is_exported_anonymous_expression(node):
                continue
            explicit_name = self._declaration_primary_name(node)
            if explicit_name:
                name = explicit_name
            elif self._is_export_default(node):
                name = self._export_default_name(node)
            else:
                name = self._anonymous_symbol_name(node)
            line = node.start_point[0] + 1
            symbol_id = f"{file}::{name}::{line}"
            symbols_by_id[symbol_id] = Symbol(
                id=symbol_id,
                name=name,
                kind="anonymous_function",
                file=file,
                line=line,
                end_line=node.end_point[0] + 1,
                col=node.start_point[1],
                visibility="private",
                signature=self._signature(node, lang),
            )
        return sorted(
            symbols_by_id.values(),
            key=lambda symbol: (symbol.file, symbol.line, symbol.col, symbol.name),
        )

    @staticmethod
    def _is_python_class_member(node: Any) -> bool:
        current = getattr(node, "parent", None)
        while current is not None:
            if current.type == "class_definition":
                return True
            if current.type == "function_definition":
                return False
            current = getattr(current, "parent", None)
        return False

    def _is_export_default(self, node: Any) -> bool:
        current = getattr(node, "parent", None)
        depth = 0
        while current is not None and depth < 4:
            if current.type == "export_statement":
                return self._first_child_of_type(current, "default") is not None
            current = getattr(current, "parent", None)
            depth += 1
        return False

    def _extract_object_literal_method_symbols(
        self, tree: Any, lang: str, file: str
    ) -> list[Symbol]:
        if lang not in ("javascript", "typescript", "tsx"):
            return []
        symbols_by_id: dict[str, Symbol] = {}
        for node in self._walk_tree(tree.root_node):
            if node.type != "pair":
                continue
            value_node = node.child_by_field_name("value")
            if value_node is None or value_node.type not in {
                "arrow_function",
                "function_expression",
            }:
                continue
            key_node = node.child_by_field_name("key")
            if key_node is None:
                for child in node.children:
                    if child.type in {"property_identifier", "identifier", "string"}:
                        key_node = child
                        break
            if key_node is None:
                continue
            name = self._identifier_text(key_node) or (
                self._string_literal_value(key_node)
                if key_node and key_node.type == "string"
                else ""
            )
            if not name:
                continue
            line = key_node.start_point[0] + 1
            symbol_id = f"{file}::{name}::{line}"
            symbols_by_id[symbol_id] = Symbol(
                id=symbol_id,
                name=name,
                kind="method",
                file=file,
                line=line,
                end_line=value_node.end_point[0] + 1,
                col=key_node.start_point[1],
                visibility="public",
                signature=self._signature(value_node, lang),
            )
        return sorted(
            symbols_by_id.values(),
            key=lambda symbol: (symbol.file, symbol.line, symbol.col, symbol.name),
        )

    def _extract_anonymous_symbols(
        self, tree: Any, lang: str, file: str
    ) -> list[Symbol]:
        if lang not in ("javascript", "typescript", "tsx"):
            return []

        anonymous_symbols: dict[str, Symbol] = {}
        for node in self._walk_tree(tree.root_node):
            if node.type not in {"arrow_function", "function_expression"}:
                continue
            if self._has_named_owner(
                node
            ) and not self._is_exported_anonymous_expression(node):
                continue
            if node.end_point[0] <= node.start_point[
                0
            ] and not self._is_exported_anonymous_expression(node):
                continue

            explicit_name = self._declaration_primary_name(node)
            if explicit_name is not None and not self._is_exported_anonymous_expression(
                node
            ):
                continue
            line = node.start_point[0] + 1

            # 尝试从上下文推断更有意义的名字
            name = explicit_name or self._contextual_anonymous_name(node)

            symbol_id = f"{file}::{name}::{line}"
            # 碰撞消歧义：同一行多个匿名函数时，追加序号避免ID覆盖
            if symbol_id in anonymous_symbols:
                count = 2
                while f"{symbol_id}#{count}" in anonymous_symbols:
                    count += 1
                symbol_id = f"{symbol_id}#{count}"
            anonymous_symbols[symbol_id] = Symbol(
                id=symbol_id,
                name=name,
                kind="anonymous_function",
                file=file,
                line=line,
                end_line=node.end_point[0] + 1,
                col=node.start_point[1],
                visibility="private",
                signature=self._signature(node, lang),
            )

        return list(anonymous_symbols.values())

    def _contextual_anonymous_name(self, node: Any) -> str:
        """从父节点上下文推断匿名函数名（JSX handler / Hook callback 等）。"""
        parent = getattr(node, "parent", None)
        if parent is None:
            return self._anonymous_symbol_name(node)

        # JSX 属性: onClick={() => ...} → onClick_handler@L24
        if parent.type == "jsx_expression":
            grandparent = getattr(parent, "parent", None)
            if grandparent is not None and grandparent.type == "jsx_attribute":
                prop_name = ""
                for child in grandparent.children:
                    if child.type == "property_identifier":
                        prop_name = self._text(child)
                        break
                if prop_name:
                    return f"<{prop_name}_handler@{node.start_point[0] + 1}>"

        # 调用参数: useEffect(() => ...) → useEffect_callback@L24
        if parent.type == "arguments":
            grandparent = getattr(parent, "parent", None)
            if grandparent is not None and grandparent.type == "call_expression":
                func_node = grandparent.child_by_field_name("function")
                if func_node is not None:
                    func_name = self._text(func_node)
                    if func_name and len(func_name) <= 40:
                        return f"<{func_name}_callback@{node.start_point[0] + 1}>"

        # 数组方法回调: arr.map(() => ...) → map_callback@L24
        if parent.type == "arguments":
            grandparent = getattr(parent, "parent", None)
            if grandparent is not None and grandparent.type == "call_expression":
                func_node = grandparent.child_by_field_name("function")
                if func_node is not None and func_node.type == "member_expression":
                    prop_node = func_node.child_by_field_name("property")
                    if prop_node is not None:
                        method_name = self._text(prop_node)
                        if method_name in {
                            "map",
                            "filter",
                            "reduce",
                            "forEach",
                            "find",
                            "some",
                            "every",
                            "sort",
                            "flatMap",
                        }:
                            return f"<{method_name}_callback@{node.start_point[0] + 1}>"

        return self._anonymous_symbol_name(node)

    def _is_exported_anonymous_expression(self, node: Any) -> bool:
        current = getattr(node, "parent", None)
        depth = 0
        while current is not None and depth < 4:
            if (
                current.type == "export_statement"
                and self._first_child_of_type(current, "default") is not None
            ):
                return True
            if current.type == "assignment_expression":
                left_node = current.child_by_field_name("left")
                if (
                    left_node is not None
                    and self._commonjs_export_target(left_node) is not None
                ):
                    return True
            current = getattr(current, "parent", None)
            depth += 1
        return False

    def _has_named_owner(self, node: Any) -> bool:
        current = getattr(node, "parent", None)
        depth = 0
        while current is not None and depth < 4:
            if current.type in {"function_declaration", "method_definition"}:
                return True
            if current.type == "pair":
                value_node = current.child_by_field_name("value")
                key_node = current.child_by_field_name("key")
                if value_node is node and key_node is not None:
                    return True
            if current.type == "variable_declarator":
                for child in current.children:
                    if child.type == "identifier":
                        return True
            current = getattr(current, "parent", None)
            depth += 1
        return False

    def extract_imports(self, tree: Any, lang: str) -> list[tuple[str, int]]:
        query = self._queries.get(lang, {}).get("import")
        if not query:
            return []
        results = set()

        # 对于Rust，需要特殊处理：捕获完整路径文本
        if lang == "rust":
            for cap_name, node in self._run_query(query, tree.root_node, lang):
                text = self._text(node)
                line = node.start_point[0] + 1
                if cap_name == "full_path":
                    # 对于 scoped_use_list，提取路径前缀
                    if node.type == "scoped_use_list":
                        path_node = node.child_by_field_name("path")
                        if path_node:
                            results.add((self._text(path_node), line))
                    else:
                        # 对于 scoped_identifier，使用完整文本
                        results.add((text, line))
                elif cap_name == "name":
                    results.add((text, line))
        else:
            for cap_name, node in self._run_query(query, tree.root_node, lang):
                if lang in ("javascript", "typescript", "tsx") and cap_name != "source":
                    continue
                text = self._text(node).strip("\"'")
                if text:
                    results.add((text, node.start_point[0] + 1))
        return sorted(results, key=lambda item: (item[1], item[0]))

    @staticmethod
    def _call_reference_kind(node: Any) -> str:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.type in {"call_expression", "call"}:
                function_node = parent.child_by_field_name("function")
                if function_node is not None and function_node.type in {
                    "member_expression",
                    "field_expression",
                    "selector_expression",
                    "attribute",
                }:
                    return "member"
                return "direct"
            parent = getattr(parent, "parent", None)
        return "direct"

    def _extract_html_symbols(self, tree: Any, file: str) -> list[Symbol]:
        symbols_by_id: dict[str, Symbol] = {}
        seen_names: dict[tuple[str, int], int] = {}
        for node in self._walk_tree(tree.root_node):
            if node.type != "element":
                continue
            start_tag = self._first_child_of_type(node, "start_tag")
            if start_tag is None:
                continue
            tag_name = None
            for child in start_tag.children:
                if child.type == "tag_name":
                    tag_name = self._text(child)
                    break
            if not tag_name:
                continue
            line = node.start_point[0] + 1
            visible_name = f"<{tag_name}>"
            key = (visible_name, line)
            seen_names[key] = seen_names.get(key, 0) + 1
            if seen_names[key] > 1:
                visible_name = f"{visible_name}#{seen_names[key]}"
            symbol_id = f"{file}::{visible_name}::{line}"
            symbols_by_id[symbol_id] = Symbol(
                id=symbol_id,
                name=visible_name,
                kind="element",
                file=file,
                line=line,
                end_line=node.end_point[0] + 1,
                col=node.start_point[1],
                visibility="public",
                signature=visible_name,
            )
        return sorted(
            symbols_by_id.values(),
            key=lambda symbol: (symbol.file, symbol.line, symbol.col, symbol.name),
        )

    def _extract_css_symbols(self, tree: Any, file: str) -> list[Symbol]:
        symbols_by_id: dict[str, Symbol] = {}
        seen_names: dict[tuple[str, int], int] = {}
        selector_types = {
            "class_selector",
            "id_selector",
            "tag_name",
            "nesting_selector",
        }
        for node in self._walk_tree(tree.root_node):
            if node.type not in selector_types:
                continue
            raw_name = self._text(node).strip()
            if not raw_name:
                continue
            line = node.start_point[0] + 1
            kind = "selector"
            if raw_name.startswith("."):
                kind = "class_selector"
            elif raw_name.startswith("#"):
                kind = "id_selector"
            key = (raw_name, line)
            seen_names[key] = seen_names.get(key, 0) + 1
            visible_name = (
                raw_name if seen_names[key] == 1 else f"{raw_name}#{seen_names[key]}"
            )
            symbol_id = f"{file}::{visible_name}::{line}"
            symbols_by_id[symbol_id] = Symbol(
                id=symbol_id,
                name=visible_name,
                kind=kind,
                file=file,
                line=line,
                end_line=node.end_point[0] + 1,
                col=node.start_point[1],
                visibility="public",
                signature=raw_name,
            )
        return sorted(
            symbols_by_id.values(),
            key=lambda symbol: (symbol.file, symbol.line, symbol.col, symbol.name),
        )

    def _extract_json_symbols(self, tree: Any, file: str) -> list[Symbol]:
        symbols_by_id: dict[str, Symbol] = {}
        seen_names: dict[tuple[str, int], int] = {}
        for node in self._walk_tree(tree.root_node):
            if node.type != "pair":
                continue
            key_node = node.child_by_field_name("key")
            if key_node is None:
                continue
            key_name = self._string_literal_value(key_node)
            if not key_name:
                continue
            line = node.start_point[0] + 1
            key = (key_name, line)
            seen_names[key] = seen_names.get(key, 0) + 1
            visible_name = (
                key_name if seen_names[key] == 1 else f"{key_name}#{seen_names[key]}"
            )
            symbol_id = f"{file}::{visible_name}::{line}"
            symbols_by_id[symbol_id] = Symbol(
                id=symbol_id,
                name=visible_name,
                kind="json_key",
                file=file,
                line=line,
                end_line=node.end_point[0] + 1,
                col=node.start_point[1],
                visibility="public",
                signature=f'"{key_name}"',
            )
        return sorted(
            symbols_by_id.values(),
            key=lambda symbol: (symbol.file, symbol.line, symbol.col, symbol.name),
        )

    def extract_js_ts_import_bindings(
        self,
        content: bytes,
        lang: str,
        tree: Any | None = None,
    ) -> list[JSImportBinding]:
        """提取 JS/TS import 绑定信息。"""
        if lang not in ("javascript", "typescript", "tsx"):
            return []
        parsed_tree = tree or self.parse(content, lang)
        if not parsed_tree:
            return []
        bindings: dict[tuple[str, str, str, int, str], JSImportBinding] = {}
        for node in parsed_tree.root_node.children:
            if node.type == "import_statement":
                self._collect_es_import_bindings(node, bindings)
        for node in self._walk_tree(parsed_tree.root_node):
            if node.type == "variable_declarator":
                self._collect_commonjs_import_bindings(node, bindings)
        return sorted(
            bindings.values(),
            key=lambda item: (
                item.line,
                item.module,
                item.local_name,
                item.imported_name,
                item.kind,
            ),
        )

    def extract_js_ts_export_bindings(
        self,
        content: bytes,
        lang: str,
        tree: Any | None = None,
    ) -> list[JSExportBinding]:
        """提取 JS/TS export 绑定信息。"""
        if lang not in ("javascript", "typescript", "tsx"):
            return []
        parsed_tree = tree or self.parse(content, lang)
        if not parsed_tree:
            return []
        bindings: dict[
            tuple[str, str | None, str | None, int, str], JSExportBinding
        ] = {}

        def add_binding(
            exported_name: str,
            source_name: str | None,
            module: str | None,
            line: int,
            kind: str,
        ) -> None:
            key = (exported_name, source_name, module, line, kind)
            bindings[key] = JSExportBinding(
                exported_name=exported_name,
                source_name=source_name,
                module=module,
                line=line,
                kind=kind,
            )

        for node in parsed_tree.root_node.children:
            if node.type == "export_statement":
                self._collect_es_export_bindings(node, add_binding)
        for node in self._walk_tree(parsed_tree.root_node):
            if node.type == "assignment_expression":
                self._collect_commonjs_export_bindings(node, add_binding)
        return sorted(
            bindings.values(),
            key=lambda item: (
                item.line,
                item.exported_name,
                item.source_name or "",
                item.module or "",
                item.kind,
            ),
        )

    def _collect_es_import_bindings(
        self,
        node: Any,
        bindings: dict[tuple[str, str, str, int, str], JSImportBinding],
    ) -> None:
        module = self._module_literal_from_statement(node)
        if not module:
            return
        line = node.start_point[0] + 1
        import_clause = self._first_child_of_type(node, "import_clause")
        if not import_clause:
            return
        for child in import_clause.children:
            if child.type == "identifier":
                self._add_import_binding(
                    bindings,
                    child.text.decode("utf-8", errors="replace"),
                    "default",
                    module,
                    line,
                    "default",
                )
            elif child.type == "named_imports":
                for specifier in child.children:
                    if specifier.type != "import_specifier":
                        continue
                    source_node = specifier.child_by_field_name("name")
                    alias_node = specifier.child_by_field_name("alias")
                    source_name = self._identifier_text(source_node)
                    local_name = self._identifier_text(alias_node) or source_name
                    if source_name and local_name:
                        self._add_import_binding(
                            bindings, local_name, source_name, module, line, "named"
                        )
            elif child.type == "namespace_import":
                local_name = self._last_identifier(child)
                if local_name:
                    self._add_import_binding(
                        bindings, local_name, "*", module, line, "namespace"
                    )

    def _collect_commonjs_import_bindings(
        self,
        node: Any,
        bindings: dict[tuple[str, str, str, int, str], JSImportBinding],
    ) -> None:
        value_node = node.child_by_field_name("value")
        module = self._require_call_module(value_node)
        if not module:
            return
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        line = node.start_point[0] + 1
        if name_node.type == "identifier":
            self._add_import_binding(
                bindings,
                name_node.text.decode("utf-8", errors="replace"),
                "default",
                module,
                line,
                "default",
            )
            return
        if name_node.type != "object_pattern":
            return
        for child in name_node.children:
            if child.type in {"shorthand_property_identifier_pattern", "identifier"}:
                name = child.text.decode("utf-8", errors="replace")
                self._add_import_binding(bindings, name, name, module, line, "named")
            elif child.type == "pair_pattern":
                source_name = self._identifier_text(child.child_by_field_name("key"))
                local_name = self._identifier_text(child.child_by_field_name("value"))
                if source_name and local_name:
                    self._add_import_binding(
                        bindings, local_name, source_name, module, line, "named"
                    )
            elif child.type == "rest_pattern":
                # 处理 const { a, ...rest } = require('foo') 中的 ...rest
                rest_id = child.child_by_field_name("argument") or (
                    child.children[0] if child.children else None
                )
                if rest_id and rest_id.type == "identifier":
                    name = rest_id.text.decode("utf-8", errors="replace")
                    self._add_import_binding(
                        bindings, name, name, module, line, "named"
                    )

    def _collect_es_export_bindings(
        self,
        node: Any,
        add_binding: Any,
    ) -> None:
        line = node.start_point[0] + 1
        module = self._module_literal_from_statement(node)
        has_default = self._first_child_of_type(node, "default") is not None
        namespace_export = self._first_child_of_type(node, "namespace_export")
        export_clause = self._first_child_of_type(node, "export_clause")
        declaration = self._export_declaration_node(node)

        if namespace_export is not None and module:
            exported_name = self._last_identifier(namespace_export)
            if exported_name:
                add_binding(exported_name, "*", module, line, "namespace")
            return

        if self._first_child_of_type(node, "*") is not None and module:
            add_binding("*", "*", module, line, "wildcard")
            return

        if export_clause is not None:
            kind = "reexport" if module else "local"
            for specifier in export_clause.children:
                if specifier.type != "export_specifier":
                    continue
                source_name = self._identifier_text(
                    specifier.child_by_field_name("name")
                )
                exported_name = (
                    self._identifier_text(specifier.child_by_field_name("alias"))
                    or source_name
                )
                if source_name and exported_name:
                    add_binding(exported_name, source_name, module, line, kind)
            return

        if has_default:
            source_name = self._export_default_source_name(node, declaration)
            if source_name:
                add_binding("default", source_name, None, line, "local")
            return

        for exported_name in self._exported_names_from_declaration(declaration):
            add_binding(exported_name, exported_name, None, line, "local")

    def _collect_commonjs_export_bindings(
        self,
        node: Any,
        add_binding: Any,
    ) -> None:
        target_node = node.child_by_field_name("left")
        value_node = node.child_by_field_name("right")
        if (
            target_node is None
            or value_node is None
            or target_node.type != "member_expression"
        ):
            return
        export_target = self._commonjs_export_target(target_node)
        if export_target is None:
            return
        line = node.start_point[0] + 1
        if export_target == "default":
            if value_node.type == "object":
                for child in value_node.children:
                    if child.type == "shorthand_property_identifier":
                        name = child.text.decode("utf-8", errors="replace")
                        add_binding(name, name, None, line, "local")
                    elif child.type == "pair":
                        exported_name = self._identifier_text(
                            child.child_by_field_name("key")
                        )
                        source_name = self._identifier_text(
                            child.child_by_field_name("value")
                        ) or self._expression_binding_name(
                            child.child_by_field_name("value")
                        )
                        if exported_name and source_name:
                            add_binding(exported_name, source_name, None, line, "local")
                return
            source_name = self._expression_binding_name(value_node)
            if source_name:
                add_binding("default", source_name, None, line, "local")
            return
        source_name = self._expression_binding_name(value_node)
        if source_name:
            add_binding(export_target, source_name, None, line, "local")

    def _add_import_binding(
        self,
        bindings: dict[tuple[str, str, str, int, str], JSImportBinding],
        local_name: str,
        imported_name: str,
        module: str,
        line: int,
        kind: str,
    ) -> None:
        key = (local_name, imported_name, module, line, kind)
        bindings[key] = JSImportBinding(local_name, imported_name, module, line, kind)

    def _module_literal_from_statement(self, node: Any) -> str | None:
        for child in node.children:
            if child.type == "string":
                return self._string_literal_value(child)
        return None

    def _require_call_module(self, node: Any | None) -> str | None:
        if node is None or node.type != "call_expression":
            return None
        function_node = node.child_by_field_name("function")
        arguments_node = node.child_by_field_name("arguments")
        if function_node is None or function_node.type != "identifier":
            return None
        if (
            function_node.text.decode("utf-8", errors="replace") != "require"
            or arguments_node is None
        ):
            return None
        for child in arguments_node.children:
            if child.type == "string":
                return self._string_literal_value(child)
        return None

    def _commonjs_export_target(self, node: Any) -> str | None:
        object_node = node.child_by_field_name("object")
        property_node = node.child_by_field_name("property")
        if object_node is None or property_node is None:
            return None
        if (
            object_node.type == "identifier"
            and object_node.text.decode("utf-8", errors="replace") == "exports"
        ):
            return property_node.text.decode("utf-8", errors="replace")
        if object_node.type == "member_expression":
            inner_object = object_node.child_by_field_name("object")
            inner_property = object_node.child_by_field_name("property")
            if (
                inner_object is not None
                and inner_property is not None
                and inner_object.type == "identifier"
                and inner_object.text.decode("utf-8", errors="replace") == "module"
                and inner_property.type == "property_identifier"
                and inner_property.text.decode("utf-8", errors="replace") == "exports"
            ):
                return property_node.text.decode("utf-8", errors="replace")
        if (
            object_node.type == "identifier"
            and property_node.type == "property_identifier"
            and object_node.text.decode("utf-8", errors="replace") == "module"
            and property_node.text.decode("utf-8", errors="replace") == "exports"
        ):
            return "default"
        return None

    def _export_declaration_node(self, node: Any) -> Any | None:
        for child in node.children:
            if child.type in {
                "function_declaration",
                "class_declaration",
                "lexical_declaration",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
            }:
                return child
        return None

    def _export_default_source_name(
        self, node: Any, declaration: Any | None
    ) -> str | None:
        if declaration is not None:
            return self._declaration_primary_name(declaration)
        for child in node.children:
            if child.type in {"export", "default", ";"}:
                continue
            source_name = self._expression_binding_name(child)
            if source_name:
                return source_name
        return None

    def _exported_names_from_declaration(self, declaration: Any | None) -> list[str]:
        if declaration is None:
            return []
        if declaration.type == "lexical_declaration":
            names: list[str] = []
            for child in declaration.children:
                if child.type != "variable_declarator":
                    continue
                name_node = child.child_by_field_name("name")
                if name_node is not None and name_node.type == "identifier":
                    names.append(name_node.text.decode("utf-8", errors="replace"))
            return names
        primary_name = self._declaration_primary_name(declaration)
        return [primary_name] if primary_name else []

    def _declaration_primary_name(self, declaration: Any) -> str | None:
        for field_name in ("name",):
            target = declaration.child_by_field_name(field_name)
            if target is not None:
                return target.text.decode("utf-8", errors="replace")
        for child in declaration.children:
            if child.type in {"identifier", "type_identifier"}:
                return child.text.decode("utf-8", errors="replace")
        return None

    def _expression_binding_name(self, node: Any | None) -> str | None:
        if node is None:
            return None
        if node.type in {"identifier", "property_identifier", "type_identifier"}:
            return node.text.decode("utf-8", errors="replace")
        if node.type in {
            "function_declaration",
            "class_declaration",
            "function_expression",
        }:
            return self._declaration_primary_name(node) or self._anonymous_symbol_name(
                node
            )
        if node.type == "arrow_function":
            return self._anonymous_symbol_name(node)
        return None

    @staticmethod
    def _anonymous_symbol_name(node: Any) -> str:
        return f"<anonymous@{node.start_point[0] + 1}>"

    @staticmethod
    def _export_default_name(node: Any) -> str:
        """为 export default 无名字的函数/类生成可读名。"""
        line = node.start_point[0] + 1
        kind = node.type.replace("_expression", "").replace("_declaration", "")
        return f"<default_export_{kind}@{line}>"

    def _string_literal_value(self, node: Any) -> str:
        return self._text(node).strip("\"'`")

    def _first_child_of_type(self, node: Any, node_type: str) -> Any | None:
        for child in node.children:
            if child.type == node_type:
                return child
        return None

    def _last_identifier(self, node: Any) -> str | None:
        identifiers = [
            child.text.decode("utf-8", errors="replace")
            for child in node.children
            if child.type in {"identifier", "property_identifier", "type_identifier"}
        ]
        return identifiers[-1] if identifiers else None

    def _identifier_text(self, node: Any | None) -> str | None:
        if node is None:
            return None
        if node.type in {
            "identifier",
            "property_identifier",
            "type_identifier",
            "shorthand_property_identifier",
            "shorthand_property_identifier_pattern",
        }:
            return node.text.decode("utf-8", errors="replace")
        return None

    def _walk_tree(self, root: Any, max_nodes: int = 500_000) -> list[Any]:
        """前序 DFS 遍历 AST 节点，限制最大节点数防止 OOM。"""
        nodes = [root]
        result: list[Any] = []
        while nodes and len(result) < max_nodes:
            current = nodes.pop()
            result.append(current)
            nodes.extend(reversed(current.children))
        return result

    def extract_calls(self, tree: Any, lang: str) -> list[tuple[str, int, str]]:
        query = self._queries.get(lang, {}).get("call")
        if not query:
            return []
        results = []
        for cap_name, node in self._run_query(query, tree.root_node, lang):
            if cap_name != "name":
                continue
            name = self._text(node)
            if name:
                results.append(
                    (name, node.start_point[0] + 1, self._call_reference_kind(node))
                )
        return sorted(set(results), key=lambda item: (item[1], item[0], item[2]))

    def extract_http_routes(self, tree: Any, lang: str, file: str) -> list[Any]:
        """从 AST 中提取 HTTP 路由定义。

        支持框架：FastAPI (Python), Express (JS/TS), Axum (Rust)。
        route inventory 只输出严格匹配的生产路由定义，避免把测试 DSL、日志、
        Array/Option 等普通调用误判为 HTTP route。
        """
        from . import HttpRoute

        if self._should_skip_route_file(file):
            return []

        queries = self._queries.get(lang, {})
        route_query = queries.get("http_route")
        explicit_query = queries.get("http_route_explicit")
        if not route_query and not explicit_query:
            return []

        routes: list[HttpRoute] = []
        for q in (route_query, explicit_query):
            if q is None:
                continue
            for captures in self._run_query_matches(q, tree.root_node, lang):
                route = self._http_route_from_captures(captures, lang, file)
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

    def _http_route_from_captures(
        self, captures: dict[str, list[Any]], lang: str, file: str
    ) -> Any | None:
        from . import HttpRoute

        path_node = self._first_capture(captures, "path")
        handler_node = self._first_capture(captures, "handler")
        if path_node is None or handler_node is None:
            return None

        method_node = self._first_capture(captures, "method") or self._first_capture(
            captures, "http_method"
        )
        method = (self._text(method_node) if method_node is not None else "").lower()
        if not method:
            return None

        if lang == "python":
            obj = self._text(self._first_capture(captures, "_obj"))
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
            # Flask: @app.route("/path", methods=["GET"]) — method comes from a list, not the attribute name
            if method == "route":
                # 尝试从 methods= 参数提取 HTTP method
                methods_node = self._first_capture(captures, "_methods")
                if methods_node is not None:
                    method_text = self._text(methods_node).strip("\"'")
                    method = method_text.lower()
                else:
                    method = "get"  # Flask route 默认为 GET
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
                "flask"
                if method == "route" or obj in {"bp", "blueprint"}
                else "fastapi"
            )
        elif lang in ("javascript", "typescript", "tsx"):
            # NestJS decorator-based routes (checked before Express since
            # NestJS queries capture no _router and Express check would reject)
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
                path = self._string_literal_value(path_node)
                if not path:
                    return None
                handler_name = self._route_handler_name(handler_node)
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
            router = self._text(self._first_capture(captures, "_router"))
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
            framework = "express"
        elif lang == "go":
            router = self._text(self._first_capture(captures, "_router"))
            if method in {"some", "ok", "err", "unwrap", "map", "filter"}:
                return None
            framework = "go-http"
        elif lang == "rust":
            method_name = self._text(self._first_capture(captures, "_method_name"))
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
            framework = "axum"
        elif lang == "java":
            # Spring Boot: @GetMapping("/path") on a method
            if method in {"some", "ok", "err", "unwrap", "map", "filter"}:
                return None
            framework = "spring"
        else:
            return None

        path = self._string_literal_value(path_node)
        if not path:
            return None
        handler_name = self._route_handler_name(handler_node)
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
        self, query: Any, root: Any, lang: str = "unknown"
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
                        normalized[cap_name] = (
                            nodes if isinstance(nodes, list) else [nodes]
                        )
                    results.append(normalized)
                if results:
                    return results
        except Exception as e:
            logger.debug(f"Query match run error [{lang}]: {e}")

        # 兼容旧 runtime：只能拿到 capture 列表时，按捕获起始行粗分组后再严格校验。
        captures_by_line: dict[int, dict[str, list[Any]]] = {}
        for cap_name, node in self._run_query(query, root, lang):
            line = node.start_point[0]
            captures_by_line.setdefault(line, {}).setdefault(cap_name, []).append(node)
        return [captures for _, captures in sorted(captures_by_line.items())]

    @staticmethod
    def _first_capture(captures: dict[str, list[Any]], name: str) -> Any | None:
        nodes = captures.get(name) or []
        return nodes[0] if nodes else None

    @staticmethod
    def _should_skip_route_file(file: str) -> bool:
        normalized = file.replace("\\", "/")
        parts = {part.lower() for part in normalized.split("/")}
        if parts & {"e2e", "tests", "__tests__"}:
            return True
        name = normalized.rsplit("/", 1)[-1].lower()
        return bool(
            re.search(
                r"(_test\.rs|\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts))$", name
            )
        )

    def _route_handler_name(self, node: Any) -> str:
        explicit = self._identifier_text(node)
        if explicit:
            return explicit
        if node.type in {"arrow_function", "function_expression", "lambda"}:
            return self._anonymous_symbol_name(node)
        return self._text(node)

    # ── 内部辅助 ────────────────────────────────────────────────────────────────

    def _run_query(
        self, query: Any, root: Any, lang: str = "unknown"
    ) -> list[tuple[str, Any]]:
        """
        执行 tree-sitter query 并返回统一格式 list[(cap_name, Node)]

        要求 tree-sitter >= 0.22（使用 QueryCursor）
        """
        try:
            from tree_sitter import QueryCursor  # type: ignore

            cursor = QueryCursor(query)
            raw = cursor.captures(root)

            pairs: list[tuple[str, Any]] = []
            # tree-sitter >= 0.23.0 保证 captures() 返回 dict[cap_name, list[Node]]
            for cap_name, nodes in raw.items():
                node_list = nodes if isinstance(nodes, list) else [nodes]
                for n in node_list:
                    pairs.append((cap_name, n))
            return pairs
        except Exception as e:
            if not self._query_error_logged.get(lang, False):
                logger.warning(f"Query run error [{lang}] (first occurrence): {e}")
                self._query_error_logged[lang] = True
            else:
                logger.debug(f"Query run error [{lang}]: {e}")
            return []

    @staticmethod
    def _within(child: Any, parent: Any) -> bool:
        return (
            child.start_point >= parent.start_point
            and child.end_point <= parent.end_point
        )

    @staticmethod
    def _text(node: Any) -> str:
        return (
            node.text.decode("utf-8", errors="replace")
            if getattr(node, "text", None)
            else ""
        )

    def _docstring(self, node: Any, lang: str) -> str:
        if not node:
            return ""
        try:
            if lang == "python":
                for child in node.children:
                    if child.type == "expression_statement":
                        for sub in child.children:
                            if sub.type == "string":
                                return self._text(sub).strip("\"'` \n")
            elif lang in ("javascript", "typescript", "go", "rust", "java", "c_sharp"):
                prev = getattr(node, "prev_sibling", None)
                if prev and "comment" in prev.type:
                    return self._text(prev).lstrip("/* \n").rstrip("*/ \n")
        except Exception as exc:
            logger.debug(f"Docstring extraction error: {exc}")
        return ""

    def _signature(self, node: Any, lang: str) -> str:
        if not node:
            return ""
        try:
            # 对于 decorated_definition，找到 function_definition 子节点
            target_node = node
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type == "function_definition":
                        target_node = child
                        break
            # 仅提取首行，避免读取整个函数体再 split
            node_bytes = getattr(target_node, "text", b"") or b""
            newline_pos = node_bytes.find(b"\n")
            first_line_bytes = (
                node_bytes[:newline_pos] if newline_pos >= 0 else node_bytes[:500]
            )
            first_line = first_line_bytes.decode("utf-8", errors="replace")
            patterns = {
                "python": r"(?:async\s+)?def\s+\w+\s*\([^)]*\)(?:\s*->\s*[^:]+)?",
                "javascript": r"(?:async\s+)?(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)",
                "typescript": r"(?:async\s+)?(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)(?:\s*:\s*\S+)?\s*=>)",
                "rust": r"(?:pub\s+)?(?:async\s+)?fn\s+\w+(?:<[^>]*>)?\s*\([^)]*\)(?:\s*->\s*[^{]+)?",
                "go": r"func\s+(?:\([^)]+\)\s+)?\w+\s*\([^)]*\)(?:\s*\([^)]*\))?(?:\s*[^{]+)?",
                "java": r"\w+\s*\([^)]*\)",
                "c_sharp": r"\w+\s*\([^)]*\)",
            }
            pat = patterns.get(lang, "")
            if pat:
                m = re.search(pat, first_line)
                if m:
                    return m.group(0).strip()
        except Exception as exc:
            logger.debug(f"Signature extraction error: {exc}")
        return ""
