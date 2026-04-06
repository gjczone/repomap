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


if __name__ == "__main__":
    unittest.main()
