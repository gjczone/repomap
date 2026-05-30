import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core import RepoMapEngine


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@unittest.skipIf(
    sys.platform == "win32", "engine path normalization differs on Windows"
)
class RepoMapEngineTests(unittest.TestCase):
    def test_git_changed_files_uses_project_root_as_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def app():\n    return 1\n")
            subprocess.run(
                ["git", "init"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "repomap@example.com"],
                cwd=project_root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "RepoMap Test"],
                cwd=project_root,
                check=True,
            )
            subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
            write_file(project_root, "main.py", "def app():\n    return 2\n")

            with tempfile.TemporaryDirectory() as outside_cwd:
                previous_cwd = os.getcwd()
                try:
                    os.chdir(outside_cwd)
                    modified, deleted = RepoMapEngine(project_root)._git_changed_files()
                finally:
                    os.chdir(previous_cwd)

            self.assertEqual(modified, ["main.py"])
            self.assertEqual(deleted, [])

    def test_incremental_scan_rescans_clean_file_with_stale_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def app():\n    return 1\n")
            subprocess.run(
                ["git", "init"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "repomap@example.com"],
                cwd=project_root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "RepoMap Test"],
                cwd=project_root,
                check=True,
            )
            subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )

            full = RepoMapEngine(project_root)
            full.scan(max_files=10, incremental=False)
            original_mtime = Path(project_root, "main.py").stat().st_mtime
            os.utime(
                Path(project_root, "main.py"), (original_mtime + 5, original_mtime + 5)
            )

            incremental = RepoMapEngine(project_root)
            incremental.scan(max_files=10, incremental=True)

            self.assertIn("main.py", incremental.graph.file_symbols)
            self.assertTrue(
                any(
                    symbol.name == "app"
                    for symbol in incremental.graph.symbols.values()
                )
            )

    def test_tsx_test_file_is_marked_as_test_file_in_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "src/App.tsx",
                "export function App() {\n  return <div />;\n}\n",
            )
            write_file(
                project_root,
                "src/App.test.tsx",
                "export function renders() {\n  return <div />;\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            analysis = engine.file_analysis()

            self.assertFalse(analysis["src/App.tsx"]["is_test_file"])
            self.assertTrue(analysis["src/App.test.tsx"]["is_test_file"])

    def test_package_exports_scan_skips_dependency_directories(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "package.json", "{}\n")
            write_file(
                project_root,
                "node_modules/pkg/package.json",
                '{"exports":"./index.js"}\n',
            )
            write_file(
                project_root,
                "packages/pkg/package.json",
                '{"exports":"./src/index.js"}\n',
            )
            write_file(
                project_root,
                "packages/pkg/src/index.js",
                "export function helper() { return 1; }\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()

            resolver = engine._resolver
            self.assertIsNotNone(resolver)
            assert resolver is not None
            self.assertIn("./packages/pkg", resolver.package_exports)
            self.assertNotIn("./node_modules/pkg", resolver.package_exports)

    def test_large_files_are_skipped_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "small.py", "def keep_me():\n    return 1\n")
            write_file(project_root, "large.js", "function giant() {}\n" * 5000)

            with patch.dict(
                os.environ, {"REPOMAP_MAX_FILE_BYTES": "1024"}, clear=False
            ):
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

            # 强制 rglob fallback（模拟 rg 不可用），确保测试确定性地覆盖 skip_dirs 逻辑
            with patch("subprocess.run", side_effect=FileNotFoundError):
                engine = RepoMapEngine(project_root)
                engine.scan()

            symbol_files = {symbol.file for symbol in engine.graph.symbols.values()}
            self.assertIn("app.py", symbol_files)
            self.assertNotIn(".venv/lib/python3.11/site-packages/pkg.py", symbol_files)
            # .venv 在 rglob fallback 的 skip_dirs 中直接跳过，不再计入 filtered_path_files
            self.assertEqual(engine.scan_stats.filtered_path_files, 0)

    def test_lockfiles_are_skipped_from_symbol_scan(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "package-lock.json",
                '{ "name": "demo", "packages": {} }\n',
            )
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
            helper = next(
                symbol
                for symbol in engine.query_symbol("shared_helper")
                if symbol.file == "lib.py"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", callers)
            self.assertNotIn("only_imports", callers)

    def test_call_chain_ignores_low_signal_non_callable_targets(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root, "package.json", '{ "name": "demo", "test": "vitest" }\n'
            )
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
            wrapper = next(
                symbol
                for symbol in engine.query_symbol("wrapper")
                if symbol.file == "main.ts"
            )
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
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "main.py"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertNotIn("unrelated", callers)

    def test_member_calls_do_not_fall_back_to_unrelated_global_unique_symbol(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.ts",
                (
                    "export function create(): void {\n"
                    "  session.pty.onData(() => undefined);\n"
                    "}\n"
                ),
            )
            write_file(
                project_root,
                "tests/mock.ts",
                ("export function onData(): void {\n}\n"),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            create = next(
                symbol
                for symbol in engine.query_symbol("create")
                if symbol.file == "main.ts"
            )
            callees = {
                (symbol.name, symbol.file)
                for symbol in engine.call_chain(create.id, "callees", 2)["callees"]
            }

            self.assertNotIn(("onData", "tests/mock.ts"), callees)

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "small.py", "def keep_me():\n    return 1\n")
            write_file(project_root, "large.js", "function giant() {}\n" * 5000)
            write_file(
                project_root,
                ".venv/lib/python3.11/site-packages/pkg.py",
                "def vendored():\n    return 1\n",
            )

            with (
                patch.dict(os.environ, {"REPOMAP_MAX_FILE_BYTES": "1024"}, clear=False),
                patch("subprocess.run", side_effect=FileNotFoundError),
            ):
                engine = RepoMapEngine(project_root)
                engine.scan()

            summary = "\n".join(engine._scan_summary_lines())
            # .venv 在 rglob fallback 的 skip_dirs 中直接跳过，不再计入 Filtered paths
            self.assertIn("- Filtered large files: 1", summary)

    def test_cache_save_then_immediate_diff_is_stable(self) -> None:
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
                    "export function caller(): number {\n"
                    "  return helper();\n"
                    "}\n"
                ),
            )

            from src.toolkit import diff_project, save_cache, scan_project

            symbols, edges = scan_project(project_root)
            save_cache(project_root, symbols, edges)
            diff = diff_project(project_root)

            self.assertEqual(
                diff["summary"],
                {
                    "added": 0,
                    "removed": 0,
                    "modified": 0,
                    "edges_added": 0,
                    "edges_removed": 0,
                },
            )

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
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "helper.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertTrue(
                any(
                    "<anonymous@" in c or "_callback@" in c or "_handler@" in c
                    for c in callers
                ),
                f"Expected a callback-type caller in {callers}",
            )

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
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "lib/internal/helper.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", callers)

    def test_tsconfig_alias_resolves_local_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "tsconfig.json",
                (
                    "{\n"
                    "  // comment should be ignored\n"
                    '  "compilerOptions": {\n'
                    '    "baseUrl": ".",\n'
                    '    "paths": {\n'
                    '      "@/*": ["src/*"]\n'
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
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "src/lib/helper.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", callers)

    def test_nested_tsconfig_alias_prefers_nearest_config(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "tsconfig.json",
                (
                    "{\n"
                    '  "compilerOptions": {\n'
                    '    "baseUrl": ".",\n'
                    '    "paths": {\n'
                    '      "@/*": ["src/*"]\n'
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
            )
            write_file(
                project_root,
                "src/helper.ts",
                "export function helper(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "packages/app/tsconfig.json",
                (
                    "{\n"
                    '  "compilerOptions": {\n'
                    '    "baseUrl": ".",\n'
                    '    "paths": {\n'
                    '      "@/*": ["app-src/*"]\n'
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
                for symbol in engine.call_chain(package_helper.id, "callers", 2)[
                    "callers"
                ]
            }

            root_helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "src/helper.ts"
            )
            root_callers = {
                symbol.name
                for symbol in engine.call_chain(root_helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", package_callers)
            self.assertNotIn("caller", root_callers)

    def test_tsconfig_alias_paths_target_respects_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "tsconfig.json",
                (
                    "{\n"
                    '  "compilerOptions": {\n'
                    '    "baseUrl": "src",\n'
                    '    "paths": {\n'
                    '      "@/*": ["*"]\n'
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
            )
            write_file(
                project_root,
                "src/utils/foo.ts",
                "export function foo(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "src/main.ts",
                (
                    "import { foo } from '@/utils/foo';\n\n"
                    "export function run(): number {\n"
                    "  return foo();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            foo = next(
                symbol
                for symbol in engine.query_symbol("foo")
                if symbol.file == "src/utils/foo.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(foo.id, "callers", 2)["callers"]
            }

            self.assertIn("run", callers)

    def test_explicit_extension_import_resolves_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root, "foo.js", "export function foo() {\n  return 1;\n}\n"
            )
            write_file(
                project_root,
                "main.ts",
                (
                    "import { foo } from './foo.js';\n\n"
                    "export function run(): number {\n"
                    "  return foo();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            foo = next(
                symbol
                for symbol in engine.query_symbol("foo")
                if symbol.file == "foo.js"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(foo.id, "callers", 2)["callers"]
            }

            self.assertIn("run", callers)

    def test_node16_js_runtime_import_resolves_typescript_source(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "foo.ts",
                "export function foo(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "main.ts",
                (
                    "import { foo } from './foo.js';\n\n"
                    "export function run(): number {\n"
                    "  return foo();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            foo = next(
                symbol
                for symbol in engine.query_symbol("foo")
                if symbol.file == "foo.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(foo.id, "callers", 2)["callers"]
            }

            self.assertIn("run", callers)

    def test_python_dotted_import_resolves_package_file(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "pkg/sub.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "main.py",
                ("from pkg.sub import helper\n\ndef run():\n    return helper()\n"),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "pkg/sub.py"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("run", callers)

    def test_relative_import_above_project_root_does_not_remap_inside_project(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "outside.ts",
                "export function outside(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "src/main.ts",
                (
                    "import { outside } from '../../outside';\n\n"
                    "export function run(): number {\n"
                    "  return outside();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            outside = next(
                symbol
                for symbol in engine.query_symbol("outside")
                if symbol.file == "outside.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(outside.id, "callers", 2)["callers"]
            }

            self.assertNotIn("run", callers)

    def test_python_member_call_does_not_fall_back_to_global_function(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "helper.py", "def send():\n    return None\n")
            write_file(
                project_root,
                "main.py",
                ("def run(client):\n    return client.send()\n"),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            send = next(
                symbol
                for symbol in engine.query_symbol("send")
                if symbol.file == "helper.py"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(send.id, "callers", 2)["callers"]
            }

            self.assertNotIn("run", callers)

    def test_python_self_member_call_resolves_same_class_method(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                (
                    "class Runner:\n"
                    "    def helper(self):\n"
                    "        return 1\n\n"
                    "    def run(self):\n"
                    "        return self.helper()\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "main.py"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("run", callers)

    def test_typescript_member_call_does_not_fall_back_to_same_file_function(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.ts",
                (
                    "function save(): void {\n"
                    "}\n\n"
                    "export function run(api: { save(): void }): void {\n"
                    "  api.save();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            save = next(
                symbol
                for symbol in engine.query_symbol("save")
                if symbol.file == "main.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(save.id, "callers", 2)["callers"]
            }

            self.assertNotIn("run", callers)

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
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "lib.js"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", callers)

    def test_default_anonymous_export_resolves_default_import_call_target(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.ts", "export default () => 1;\n")
            write_file(
                project_root,
                "main.ts",
                "import run from './lib';\n\nexport function caller() {\n  return run();\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            default_symbol = next(
                symbol
                for symbol in engine.graph.symbols.values()
                if symbol.file == "lib.ts"
                and ("<anonymous@" in symbol.name or "<default_export" in symbol.name)
            )
            incoming = {
                (edge.source, edge.kind)
                for edge in engine.graph.incoming[default_symbol.id]
            }

            self.assertIn(("main.ts::caller::3", "import"), incoming)

    def test_commonjs_function_export_resolves_require_default(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "lib.js",
                "module.exports = function run() {\n  return 1;\n};\n",
            )
            write_file(
                project_root,
                "main.js",
                "const run = require('./lib');\n\nfunction caller() {\n  return run();\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            default_symbol = next(
                symbol
                for symbol in engine.graph.symbols.values()
                if symbol.file == "lib.js" and symbol.name == "run"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(default_symbol.id, "callers", 2)[
                    "callers"
                ]
            }

            self.assertIn("caller", callers)

    def test_commonjs_named_function_export_resolves_destructured_require(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "lib.js",
                "exports.helper = function helper() {\n  return 1;\n};\n",
            )
            write_file(
                project_root,
                "main.js",
                "const { helper } = require('./lib');\n\nfunction caller() {\n  return helper();\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(
                symbol
                for symbol in engine.graph.symbols.values()
                if symbol.file == "lib.js" and symbol.name == "helper"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", callers)

    def test_package_self_reference_resolves_root_export(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "package.json",
                '{"name":"@scope/app","exports":{".":"./src/index.ts"}}\n',
            )
            write_file(
                project_root,
                "src/index.ts",
                "export function helper() {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "src/consumer.ts",
                "import { helper } from '@scope/app';\n\nexport function caller() {\n  return helper();\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "src/index.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", callers)

    def test_package_self_reference_resolves_subpath_export(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "package.json",
                '{"name":"@scope/app","exports":{"./feature":"./src/feature.ts"}}\n',
            )
            write_file(
                project_root,
                "src/feature.ts",
                "export function helper() {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "src/consumer.ts",
                "import { helper } from '@scope/app/feature';\n\nexport function caller() {\n  return helper();\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "src/feature.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

            self.assertIn("caller", callers)

    def test_monorepo_package_name_exports_resolves_without_root_package_json(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "packages/pkg/package.json",
                '{"name":"@scope/pkg","exports":{".":"./src/index.ts"}}\n',
            )
            write_file(
                project_root,
                "packages/pkg/src/index.ts",
                "export function helper() {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "apps/app/main.ts",
                "import { helper } from '@scope/pkg';\n\nexport function caller() {\n  return helper();\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            helper = next(
                symbol
                for symbol in engine.query_symbol("helper")
                if symbol.file == "packages/pkg/src/index.ts"
            )
            callers = {
                symbol.name
                for symbol in engine.call_chain(helper.id, "callers", 2)["callers"]
            }

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

            write_file(project_root, "README.md", "# Demo\n")
            write_file(
                project_root, "scripts/validate.sh", "#!/usr/bin/env bash\necho ok\n"
            )
            write_file(project_root, ".env", "SECRET=hidden\n")

            engine = RepoMapEngine(project_root)
            engine.scan()
            overview = engine.render_overview()

            self.assertIn("## Recommended Reading Order", overview)
            self.assertIn("## Supporting Files (non-AST)", overview)
            self.assertIn("README.md", overview)
            self.assertIn("scripts/validate.sh", overview.replace("\\", "/"))
            self.assertNotIn(".env", overview)
            self.assertIn("## Module Summary", overview)
            self.assertIn("## Key Implementation Symbols", overview)
            self.assertIn("main.py", overview)

    def test_summary_symbols_prefer_runtime_code_over_markup_noise(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                ("def run():\n    return 1\n\ndef helper():\n    return run()\n"),
            )
            write_file(
                project_root,
                "templates/index.html",
                "<html><body>"
                + "".join("<div><span>x</span></div>" for _ in range(40))
                + "</body></html>",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            if "html" not in engine.ts.parsers:
                self.skipTest(
                    "tree-sitter-html parser unavailable in current interpreter"
                )

            summary = engine.summary_symbols(2, 2)

            self.assertEqual(summary[0]["file"], "main.py")
            self.assertEqual(
                [item["name"] for item in summary[0]["symbols"]], ["run", "helper"]
            )

    def test_hotspots_deprioritize_markup_only_density(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                ("def run():\n    return 1\n\ndef helper():\n    return run()\n"),
            )
            write_file(
                project_root,
                "templates/index.html",
                "<html><body>"
                + "".join("<div><span>x</span></div>" for _ in range(40))
                + "</body></html>",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            if "html" not in engine.ts.parsers:
                self.skipTest(
                    "tree-sitter-html parser unavailable in current interpreter"
                )

            hotspots = engine.hotspots(2)

            self.assertEqual(hotspots[0]["file"], "main.py")
            self.assertGreater(
                hotspots[0]["semantic_symbol_count"],
                hotspots[1]["semantic_symbol_count"],
            )

    def test_reading_order_deprioritizes_markup_noise_when_code_exists(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                ("from service import helper\n\ndef run():\n    return helper()\n"),
            )
            write_file(
                project_root,
                "service.py",
                ("def helper():\n    return build()\n\ndef build():\n    return 1\n"),
            )
            write_file(
                project_root,
                "prototype.html",
                "<html><body>"
                + "".join("<div><span>x</span></div>" for _ in range(80))
                + "</body></html>",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            if "html" not in engine.ts.parsers:
                self.skipTest(
                    "tree-sitter-html parser unavailable in current interpreter"
                )

            reading_order = engine.suggested_reading_order(3)

            self.assertEqual(reading_order[0]["file"], "main.py")
            self.assertEqual(reading_order[1]["file"], "service.py")

    def test_reading_order_prefers_runtime_files_over_tests(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                ("from service import helper\n\ndef run():\n    return helper()\n"),
            )
            write_file(project_root, "service.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "tests/test_main.py",
                ("from main import run\n\ndef test_run():\n    assert run() == 1\n"),
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

            reading_files = {item["file"].replace("\\", "/") for item in reading_order}
            self.assertIn("src/main.tsx", reading_files)
            self.assertIn("src/App.tsx", reading_files)

    def test_git_changed_files_sets_git_failed_on_exception(self) -> None:
        """P1-5: _git_changed_files should set git_failed=True on exception."""
        with tempfile.TemporaryDirectory() as project_root:
            engine = RepoMapEngine(project_root)
            # Mock git_backend to raise exception
            with patch(
                "src.git_backend.GitBackend.changed_files",
                side_effect=Exception("git error"),
            ):
                modified, deleted = engine._git_changed_files()

            self.assertIsNone(modified)
            self.assertIsNone(deleted)
            self.assertTrue(engine.scan_stats.git_failed)

    def test_git_changed_files_clears_git_failed_on_success(self) -> None:
        """P1-5: _git_changed_files should set git_failed=False on success."""
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def app():\n    return 1\n")
            subprocess.run(
                ["git", "init"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "repomap@example.com"],
                cwd=project_root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "RepoMap Test"],
                cwd=project_root,
                check=True,
            )
            subprocess.run(["git", "add", "main.py"], cwd=project_root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )

            engine = RepoMapEngine(project_root)
            modified, deleted = engine._git_changed_files()

            self.assertFalse(engine.scan_stats.git_failed)


class FindUntestedSymbolsTests(unittest.TestCase):
    """P1-1: find_untested_symbols should return all untested symbols when no tests exist."""

    def test_returns_all_untested_when_no_test_files(self) -> None:
        """When project has no test files, all non-low-signal symbols should be untested."""
        from src import RepoGraph, Symbol
        from src.topic import find_untested_symbols

        # Create a graph with no test files
        graph = RepoGraph()
        graph.symbols = {
            "s1": Symbol(id="s1", name="foo", kind="function", file="main.py", line=1),
            "s2": Symbol(id="s2", name="bar", kind="class", file="main.py", line=5),
        }
        graph.file_symbols = {"main.py": ["s1", "s2"]}

        # Should return empty because no incoming calls
        result = find_untested_symbols(graph, min_incoming_calls=0, min_score=0.0)
        self.assertEqual(len(result), 2)

    def test_skips_low_signal_symbols(self) -> None:
        """Low signal symbols should be skipped even when no tests exist."""
        from src import LOW_SIGNAL_KINDS, RepoGraph, Symbol
        from src.topic import find_untested_symbols

        graph = RepoGraph()
        # Use a kind that is in LOW_SIGNAL_KINDS
        low_signal_kind = next(iter(LOW_SIGNAL_KINDS))
        graph.symbols = {
            "s1": Symbol(
                id="s1",
                name="foo",
                kind=low_signal_kind,
                file="main.py",
                line=1,
            ),
            "s2": Symbol(id="s2", name="bar", kind="function", file="main.py", line=5),
        }
        graph.file_symbols = {"main.py": ["s1", "s2"]}

        result = find_untested_symbols(graph, min_incoming_calls=0, min_score=0.0)
        # Only s2 should be returned (s1 is low signal)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "bar")


if __name__ == "__main__":
    unittest.main()
