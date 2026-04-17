import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repomap_core import RepoMapEngine


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class RepoMapEngineTests(unittest.TestCase):
    def test_large_files_are_skipped_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "small.py", "def keep_me():\n    return 1\n")
            write_file(project_root, "large.js", "function giant() {}\n" * 5000)

            with patch.dict(os.environ, {"REPOMAP_MAX_FILE_BYTES": "1024"}, clear=False):
                engine = RepoMapEngine(project_root)
                engine.scan()

            self.assertIn("small.py", engine.graph.file_symbols)
            self.assertNotIn("large.js", engine.graph.file_symbols)
            self.assertEqual(engine.scan_stats.filtered_large_files, 1)

    def test_environment_package_directories_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "app.py", "def app():\n    return 1\n")
            write_file(
                project_root,
                ".venv/lib/python3.11/site-packages/pkg.py",
                "def vendored():\n    return 1\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()

            symbol_files = {symbol.file for symbol in engine.graph.symbols.values()}
            self.assertIn("app.py", symbol_files)
            self.assertNotIn(".venv/lib/python3.11/site-packages/pkg.py", symbol_files)
            self.assertEqual(engine.scan_stats.filtered_path_files, 1)

    def test_lockfiles_are_skipped_from_symbol_scan(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "package-lock.json", '{ "name": "demo", "packages": {} }\n')
            write_file(project_root, "main.py", "def app():\n    return 1\n")

            engine = RepoMapEngine(project_root)
            engine.scan()

            self.assertNotIn("package-lock.json", engine.graph.file_symbols)
            self.assertIn("main.py", engine.graph.file_symbols)
            self.assertEqual(engine.scan_stats.filtered_path_files, 1)

    def test_call_chain_uses_call_edges_only(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def shared_helper():\n    return 1\n")
            write_file(
                project_root,
                "main.py",
                (
                    "from lib import shared_helper\n\n"
                    "def caller():\n"
                    "    return shared_helper()\n\n"
                    "def only_imports():\n"
                    "    return 1\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(symbol for symbol in engine.query_symbol("shared_helper") if symbol.file == "lib.py")
            callers = {symbol.name for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]}

            self.assertIn("caller", callers)
            self.assertNotIn("only_imports", callers)

    def test_call_chain_ignores_low_signal_non_callable_targets(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "package.json", '{ "name": "demo", "test": "vitest" }\n')
            write_file(
                project_root,
                "main.ts",
                (
                    "export function wrapper(value: string): boolean {\n"
                    "  return /x/.test(value);\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            wrapper = next(symbol for symbol in engine.query_symbol("wrapper") if symbol.file == "main.ts")
            callees = engine.call_chain(wrapper.id, "callees", 2)["callees"]

            self.assertEqual(callees, [])

    def test_top_level_calls_are_not_assigned_to_nearest_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                (
                    "def helper():\n"
                    "    return 1\n\n"
                    "def unrelated():\n"
                    "    return 2\n\n"
                    "helper()\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(symbol for symbol in engine.query_symbol("helper") if symbol.file == "main.py")
            callers = {symbol.name for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]}

            self.assertNotIn("unrelated", callers)

    def test_scan_summary_reports_filter_counts(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "small.py", "def keep_me():\n    return 1\n")
            write_file(project_root, "large.js", "function giant() {}\n" * 5000)
            write_file(
                project_root,
                ".venv/lib/python3.11/site-packages/pkg.py",
                "def vendored():\n    return 1\n",
            )

            with patch.dict(os.environ, {"REPOMAP_MAX_FILE_BYTES": "1024"}, clear=False):
                engine = RepoMapEngine(project_root)
                engine.scan()

            summary = "\n".join(engine._scan_summary_lines())
            self.assertIn("- 过滤路径: 1", summary)
            self.assertIn("- 过滤大文件: 1", summary)

    def test_anonymous_typescript_callback_becomes_caller_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "helper.ts",
                "export function helper(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "main.ts",
                (
                    "import { helper } from './helper';\n\n"
                    "router.post('/x', async () => {\n"
                    "  return helper();\n"
                    "});\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(symbol for symbol in engine.query_symbol("helper") if symbol.file == "helper.ts")
            callers = {symbol.name for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]}

            self.assertIn("<anonymous@3>", callers)

    def test_two_hop_typescript_barrel_reexport_resolves_call_target(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "lib/internal/helper.ts",
                "export function helper(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "lib/internal/index.ts",
                "export { helper } from './helper';\n",
            )
            write_file(
                project_root,
                "lib/index.ts",
                "export { helper } from './internal';\n",
            )
            write_file(
                project_root,
                "main.ts",
                (
                    "import { helper } from './lib';\n\n"
                    "export function caller(): number {\n"
                    "  return helper();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(symbol for symbol in engine.query_symbol("helper") if symbol.file == "lib/internal/helper.ts")
            callers = {symbol.name for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]}

            self.assertIn("caller", callers)

    def test_tsconfig_alias_resolves_local_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "tsconfig.json",
                (
                    "{\n"
                    "  // comment should be ignored\n"
                    "  \"compilerOptions\": {\n"
                    "    \"baseUrl\": \".\",\n"
                    "    \"paths\": {\n"
                    "      \"@/*\": [\"src/*\"]\n"
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
            )
            write_file(
                project_root,
                "src/lib/helper.ts",
                "export function helper(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "main.ts",
                (
                    "import { helper } from '@/lib/helper';\n\n"
                    "export function caller(): number {\n"
                    "  return helper();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(symbol for symbol in engine.query_symbol("helper") if symbol.file == "src/lib/helper.ts")
            callers = {symbol.name for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]}

            self.assertIn("caller", callers)

    def test_nested_tsconfig_alias_prefers_nearest_config(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "tsconfig.json",
                (
                    "{\n"
                    "  \"compilerOptions\": {\n"
                    "    \"baseUrl\": \".\",\n"
                    "    \"paths\": {\n"
                    "      \"@/*\": [\"src/*\"]\n"
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
            )
            write_file(project_root, "src/helper.ts", "export function helper(): number {\n  return 1;\n}\n")
            write_file(
                project_root,
                "packages/app/tsconfig.json",
                (
                    "{\n"
                    "  \"compilerOptions\": {\n"
                    "    \"baseUrl\": \".\",\n"
                    "    \"paths\": {\n"
                    "      \"@/*\": [\"app-src/*\"]\n"
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
            )
            write_file(
                project_root,
                "packages/app/app-src/helper.ts",
                "export function helper(): number {\n  return 2;\n}\n",
            )
            write_file(
                project_root,
                "packages/app/main.ts",
                (
                    "import { helper } from '@/helper';\n\n"
                    "export function caller(): number {\n"
                    "  return helper();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()

            package_helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "packages/app/app-src/helper.ts"
            )
            package_callers = {
                symbol.name
                for symbol in engine.call_chain(package_helper.id, "callers", 2)["callers"]
            }

            root_helper = next(symbol for symbol in engine.query_symbol("helper") if symbol.file == "src/helper.ts")
            root_callers = {symbol.name for symbol in engine.call_chain(root_helper.id, "callers", 2)["callers"]}

            self.assertIn("caller", package_callers)
            self.assertNotIn("caller", root_callers)

    def test_commonjs_require_and_module_exports_resolve_local_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "lib.js",
                (
                    "function helper() {\n"
                    "  return 1;\n"
                    "}\n\n"
                    "module.exports = { helper };\n"
                ),
            )
            write_file(
                project_root,
                "main.js",
                (
                    "const { helper } = require('./lib');\n\n"
                    "function caller() {\n"
                    "  return helper();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(symbol for symbol in engine.query_symbol("helper") if symbol.file == "lib.js")
            callers = {symbol.name for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]}

            self.assertIn("caller", callers)

    def test_overview_includes_reading_order_and_module_summary(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                (
                    "from services.user import helper\n\n"
                    "def run():\n"
                    "    return helper()\n"
                ),
            )
            write_file(
                project_root,
                "services/user.py",
                (
                    "def helper():\n"
                    "    return build_profile()\n\n"
                    "def build_profile():\n"
                    "    return 1\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            overview = engine.render_overview()

            self.assertIn("## 推荐阅读顺序", overview)
            self.assertIn("## 模块摘要", overview)
            self.assertIn("## 关键实现符号", overview)
            self.assertIn("main.py", overview)

    def test_summary_symbols_prefer_runtime_code_over_markup_noise(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                (
                    "def run():\n"
                    "    return 1\n\n"
                    "def helper():\n"
                    "    return run()\n"
                ),
            )
            write_file(
                project_root,
                "templates/index.html",
                "<html><body>" + "".join("<div><span>x</span></div>" for _ in range(40)) + "</body></html>",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            if "html" not in engine.ts.parsers:
                self.skipTest("tree-sitter-html parser unavailable in current interpreter")

            summary = engine.summary_symbols(2, 2)

            self.assertEqual(summary[0]["file"], "main.py")
            self.assertEqual([item["name"] for item in summary[0]["symbols"]], ["run", "helper"])

    def test_hotspots_deprioritize_markup_only_density(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                (
                    "def run():\n"
                    "    return 1\n\n"
                    "def helper():\n"
                    "    return run()\n"
                ),
            )
            write_file(
                project_root,
                "templates/index.html",
                "<html><body>" + "".join("<div><span>x</span></div>" for _ in range(40)) + "</body></html>",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            if "html" not in engine.ts.parsers:
                self.skipTest("tree-sitter-html parser unavailable in current interpreter")

            hotspots = engine.hotspots(2)

            self.assertEqual(hotspots[0]["file"], "main.py")
            self.assertGreater(hotspots[0]["semantic_symbol_count"], hotspots[1]["semantic_symbol_count"])

    def test_reading_order_deprioritizes_markup_noise_when_code_exists(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                (
                    "from service import helper\n\n"
                    "def run():\n"
                    "    return helper()\n"
                ),
            )
            write_file(
                project_root,
                "service.py",
                (
                    "def helper():\n"
                    "    return build()\n\n"
                    "def build():\n"
                    "    return 1\n"
                ),
            )
            write_file(
                project_root,
                "prototype.html",
                "<html><body>" + "".join("<div><span>x</span></div>" for _ in range(80)) + "</body></html>",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            if "html" not in engine.ts.parsers:
                self.skipTest("tree-sitter-html parser unavailable in current interpreter")

            reading_order = engine.suggested_reading_order(3)

            self.assertEqual(reading_order[0]["file"], "main.py")
            self.assertEqual(reading_order[1]["file"], "service.py")

    def test_reading_order_prefers_runtime_files_over_tests(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                (
                    "from service import helper\n\n"
                    "def run():\n"
                    "    return helper()\n"
                ),
            )
            write_file(project_root, "service.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "tests/test_main.py",
                (
                    "from main import run\n\n"
                    "def test_run():\n"
                    "    assert run() == 1\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            reading_order = engine.suggested_reading_order(3)

            self.assertEqual(reading_order[0]["file"], "main.py")
            self.assertNotEqual(reading_order[0]["file"], "tests/test_main.py")

    def test_reading_order_keeps_entry_file_without_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "src/main.tsx",
                (
                    "import React from 'react';\n"
                    "import ReactDOM from 'react-dom/client';\n"
                    "import { App } from './App';\n"
                    "ReactDOM.createRoot(document.getElementById('root')!).render(<App />);\n"
                ),
            )
            write_file(
                project_root,
                "src/App.tsx",
                "export function App() {\n  return <div>app</div>;\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            reading_order = engine.suggested_reading_order(3)

            self.assertEqual(reading_order[0]["file"], "src/main.tsx")


if __name__ == "__main__":
    unittest.main()
