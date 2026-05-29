import sys
import tempfile
import unittest
from pathlib import Path

from src.toolkit import (
    diff_project,
    save_cache,
    scan_project,
)


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class RepoMapToolkitTests(unittest.TestCase):
    def test_diff_is_stable_for_unchanged_code(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "main.py",
                ("def helper():\n    return 1\n\ndef caller():\n    return helper()\n"),
            )

            symbols, edges = scan_project(project_root)
            save_cache(project_root, symbols, edges)

            result = diff_project(project_root)

            self.assertEqual(result["summary"]["added"], 0)
            self.assertEqual(result["summary"]["removed"], 0)
            self.assertEqual(result["summary"]["edges_added"], 0)
            self.assertEqual(result["summary"]["edges_removed"], 0)

    @unittest.skipIf(
        sys.platform == "win32", "test matching path logic differs on Windows"
    )
    def test_related_tests_are_deduplicated_with_best_confidence(self) -> None:
        from src.core import RepoMapEngine
        from src.topic import find_related_tests

        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "src/foo.ts",
                "export function foo(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "src/foo.test.ts",
                (
                    "import { foo } from './foo';\n"
                    "export function testFoo(): number {\n"
                    "  return foo();\n"
                    "}\n"
                ),
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            tests = find_related_tests(
                ["src/foo.ts", "src/foo.ts"],
                engine.graph,
                engine.file_analysis(),
                project_root,
            )

            matches = [
                item
                for item in tests
                if item.test_file == "src/foo.test.ts"
                and item.target_file == "src/foo.ts"
            ]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].confidence, "high")

    @unittest.skipIf(
        sys.platform == "win32", "test matching path logic differs on Windows"
    )
    def test_related_tests_include_same_directory_when_no_stronger_match_exists(
        self,
    ) -> None:
        from src.core import RepoMapEngine
        from src.topic import find_related_tests

        with tempfile.TemporaryDirectory() as project_root:
            write_file(
                project_root,
                "src/bar.ts",
                "export function bar(): number {\n  return 1;\n}\n",
            )
            write_file(
                project_root,
                "src/bar-extra.test.ts",
                "export function testBarExtra(): number {\n  return 1;\n}\n",
            )

            engine = RepoMapEngine(project_root)
            engine.scan()
            tests = find_related_tests(
                ["src/bar.ts"], engine.graph, engine.file_analysis(), project_root
            )

            matches = [
                item
                for item in tests
                if item.test_file == "src/bar-extra.test.ts"
                and item.target_file == "src/bar.ts"
            ]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].confidence, "medium")
            self.assertEqual(matches[0].reason, "same test directory")


if __name__ == "__main__":
    unittest.main()
