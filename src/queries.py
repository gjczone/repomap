#!/usr/bin/env python3
"""
Tree-sitter 查询定义 — 内嵌查询模式，无需外部 .scm 文件。

此模块仅包含语言查询字典，供 parser.py 和其他模块使用。
"""

from __future__ import annotations

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
