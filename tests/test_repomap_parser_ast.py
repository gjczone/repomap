import unittest

from repomap_parser import TreeSitterAdapter


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
            [(item.local_name, item.imported_name, item.module, item.kind) for item in bindings],
            [("alias", "real", "./real", "named")],
        )

    def test_export_bindings_ignore_comments_and_capture_namespace_reexport(self) -> None:
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
        content = b".card { color: red; }\n#app { display: grid; }\nmain { margin: 0; }\n"

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


if __name__ == "__main__":
    unittest.main()
