import unittest

from src.parser import TreeSitterAdapter


class RepoMapParserAstTests(unittest.TestCase):
    def test_import_bindings_ignore_comments_and_strings(self) -> None:
        adapter = TreeSitterAdapter()
        content = (
            b"// import { Ghost } from './ghost';\n"
            b"const banner = \"import { Fake } from './fake'\";\n"
            b"import { real as alias } from './real';\n"
        )

        bindings = adapter.extract_js_ts_import_bindings(content, "typescript")

        self.assertEqual(
            [
                (item.local_name, item.imported_name, item.module, item.kind)
                for item in bindings
            ],
            [("alias", "real", "./real", "named")],
        )

    def test_export_bindings_ignore_comments_and_capture_namespace_reexport(
        self,
    ) -> None:
        adapter = TreeSitterAdapter()
        content = (
            b"// export { Ghost } from './ghost';\n"
            b"const banner = \"export { Fake } from './fake'\";\n"
            b"export { real as alias } from './real';\n"
            b"export * as utils from './utils';\n"
        )

        bindings = adapter.extract_js_ts_export_bindings(content, "typescript")

        self.assertEqual(
            [
                (item.exported_name, item.source_name, item.module, item.kind)
                for item in bindings
            ],
            [
                ("alias", "real", "./real", "reexport"),
                ("utils", "*", "./utils", "namespace"),
            ],
        )

    def test_extract_html_symbols_returns_element_tags(self) -> None:
        adapter = TreeSitterAdapter()
        if "html" not in adapter.parsers:
            self.skipTest("tree-sitter-html parser unavailable in current interpreter")
        content = b"<html><body><main><section></section></main></body></html>"

        tree = adapter.parse(content, "html")
        assert tree is not None
        symbols = adapter.extract_symbols(tree, "html", "index.html", content)

        names = [item.name for item in symbols]
        self.assertIn("<html>", names)
        self.assertIn("<body>", names)
        self.assertIn("<main>", names)
        self.assertIn("<section>", names)

    def test_extract_css_symbols_returns_selectors(self) -> None:
        adapter = TreeSitterAdapter()
        if "css" not in adapter.parsers:
            self.skipTest("tree-sitter-css parser unavailable in current interpreter")
        content = (
            b".card { color: red; }\n#app { display: grid; }\nmain { margin: 0; }\n"
        )

        tree = adapter.parse(content, "css")
        assert tree is not None
        symbols = adapter.extract_symbols(tree, "css", "styles.css", content)

        names = [item.name for item in symbols]
        self.assertIn(".card", names)
        self.assertIn("#app", names)
        self.assertIn("main", names)

    def test_extract_json_symbols_returns_object_keys(self) -> None:
        adapter = TreeSitterAdapter()
        if "json" not in adapter.parsers:
            self.skipTest("tree-sitter-json parser unavailable in current interpreter")
        content = b'{ "name": "demo", "nested": { "enabled": true } }'

        tree = adapter.parse(content, "json")
        assert tree is not None
        symbols = adapter.extract_symbols(tree, "json", "config.json", content)

        names = [item.name for item in symbols]
        self.assertIn("name", names)
        self.assertIn("nested", names)
        self.assertIn("enabled", names)

    def test_typescript_object_literal_arrow_properties_become_named_symbols(
        self,
    ) -> None:
        adapter = TreeSitterAdapter()
        content = (
            b"export const api = {\n"
            b"  getMetadata: (signal) => fetchApi('/api/metadata', signal),\n"
            b"  getKpi: async () => fetchApi('/api/kpi'),\n"
            b"};\n"
        )
        tree = adapter.parse(content, "typescript")
        assert tree is not None

        symbols = adapter.extract_symbols(tree, "typescript", "api.ts", content)

        by_name = {item.name: item for item in symbols}
        self.assertIn("getMetadata", by_name)
        self.assertIn("getKpi", by_name)
        self.assertNotIn("<anonymous@2>", by_name)
        self.assertEqual(by_name["getMetadata"].kind, "method")

    def test_tsx_parser_handles_jsx_without_losing_component_symbol(self) -> None:
        adapter = TreeSitterAdapter()
        if "tsx" not in adapter.parsers:
            self.skipTest("tree-sitter TSX parser unavailable in current interpreter")
        content = (
            b"import { helper } from './helper';\n"
            b"export function App() {\n"
            b'  return <div data-testid="app">{helper()}</div>;\n'
            b"}\n"
        )
        tree = adapter.parse(content, "tsx")
        self.assertIsNotNone(tree)

        symbols = adapter.extract_symbols(tree, "tsx", "App.tsx", content)
        calls = adapter.extract_calls(tree, "tsx")

        imports = adapter.extract_imports(tree, "tsx")

        self.assertIn("App", {item.name for item in symbols})
        self.assertIn("helper", {name for name, _, _ in calls})
        self.assertEqual(imports, [("./helper", 1)])

    def test_tsx_http_route_uses_express_framework(self) -> None:
        adapter = TreeSitterAdapter()
        if "tsx" not in adapter.parsers:
            self.skipTest("tree-sitter TSX parser unavailable in current interpreter")
        content = b"router.get('/items', handler);\n"
        tree = adapter.parse(content, "tsx")
        assert tree is not None

        routes = adapter.extract_http_routes(tree, "tsx", "routes.tsx")

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].framework, "express")
        self.assertEqual(routes[0].path, "/items")

    def test_typescript_test_calls_are_not_http_routes(self) -> None:
        adapter = TreeSitterAdapter()
        content = (
            b"import { test, expect } from '@playwright/test';\n"
            b"test.describe('/analysis', () => {\n"
            b"  console.log('/health', value);\n"
            b"  items.some('/items', handler);\n"
            b"});\n"
        )
        tree = adapter.parse(content, "typescript")
        assert tree is not None

        routes = adapter.extract_http_routes(
            tree, "typescript", "bi-frontend/e2e/analysis.spec.ts"
        )

        self.assertEqual(routes, [])

    def test_typescript_non_router_member_calls_are_not_http_routes(self) -> None:
        adapter = TreeSitterAdapter()
        content = (
            b"console.log('/health');\n"
            b"items.some('/items', handler);\n"
            b"client.get('/api/customer', handler);\n"
        )
        tree = adapter.parse(content, "typescript")
        assert tree is not None

        routes = adapter.extract_http_routes(tree, "typescript", "src/logger.ts")

        self.assertEqual(routes, [])

    def test_rust_test_helpers_are_not_http_routes(self) -> None:
        adapter = TreeSitterAdapter()
        if "rust" not in adapter.parsers:
            self.skipTest("tree-sitter Rust parser unavailable in current interpreter")
        content = (
            b"#[test]\n"
            b"fn analyzer_test() {\n"
            b'    let config = Some("customer");\n'
            b'    let other = Option::Some("/api/customer");\n'
            b"    assert!(config.is_some());\n"
            b"}\n"
        )
        tree = adapter.parse(content, "rust")
        assert tree is not None

        routes = adapter.extract_http_routes(
            tree, "rust", "bi-backend/src/services/analyzer_test.rs"
        )

        self.assertEqual(routes, [])

    def test_rust_axum_route_is_extracted(self) -> None:
        adapter = TreeSitterAdapter()
        if "rust" not in adapter.parsers:
            self.skipTest("tree-sitter Rust parser unavailable in current interpreter")
        content = (
            b"use axum::{routing::get, Router};\n"
            b"fn handler() {}\n"
            b"fn app() -> Router {\n"
            b'    Router::new().route("/items", get(handler))\n'
            b"}\n"
        )
        tree = adapter.parse(content, "rust")
        assert tree is not None

        routes = adapter.extract_http_routes(tree, "rust", "src/main.rs")

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].framework, "axum")
        self.assertEqual(routes[0].method, "GET")
        self.assertEqual(routes[0].path, "/items")

    def test_default_anonymous_export_binding_points_to_anonymous_symbol(self) -> None:
        adapter = TreeSitterAdapter()
        content = b"export default () => helper();\n"

        bindings = adapter.extract_js_ts_export_bindings(content, "typescript")

        self.assertIn(
            ("default", "<anonymous@1>", None, "local"),
            [
                (item.exported_name, item.source_name, item.module, item.kind)
                for item in bindings
            ],
        )

    def test_extract_calls_returns_only_call_names(self) -> None:
        adapter = TreeSitterAdapter()
        content = (
            b"function foo() { return 1; }\n"
            b"const obj = { bar: () => 2 };\n"
            b"foo();\n"
            b"obj.bar();\n"
        )
        tree = adapter.parse(content, "typescript")
        assert tree is not None

        calls = adapter.extract_calls(tree, "typescript")

        call_names = [item[0] for item in calls]
        self.assertIn("foo", call_names)
        self.assertIn("bar", call_names)
        self.assertNotIn("foo()", call_names)
        self.assertNotIn("obj.bar()", call_names)

    def test_nested_function_end_lines_are_stable_when_parent_is_also_captured(
        self,
    ) -> None:
        adapter = TreeSitterAdapter()
        content = (
            b"export function AuthProvider() {\n"
            b"  useEffect(() => {\n"
            b"    const initAuth = async () => {\n"
            b"      await checkAuthStatus();\n"
            b"    };\n"
            b"    void initAuth();\n"
            b"  }, []);\n"
            b"}\n"
        )
        tree = adapter.parse(content, "typescript")
        self.assertIsNotNone(tree)

        symbols = adapter.extract_symbols(tree, "typescript", "auth.tsx", content)
        by_name = {item.name: item for item in symbols}

        self.assertEqual(by_name["initAuth"].line, 3)
        self.assertEqual(by_name["initAuth"].end_line, 5)


if __name__ == "__main__":
    unittest.main()
