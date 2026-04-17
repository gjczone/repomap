import tempfile
import unittest
from pathlib import Path

from repomap_toolkit import analyze_refs, diff_project, find_orphans, save_cache, scan_project


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
                (
                    "def helper():\n"
                    "    return 1\n\n"
                    "def caller():\n"
                    "    return helper()\n"
                ),
            )

            symbols, edges = scan_project(project_root)
            save_cache(project_root, symbols, edges)

            result = diff_project(project_root)

            self.assertEqual(result["summary"]["added"], 0)
            self.assertEqual(result["summary"]["removed"], 0)
            self.assertEqual(result["summary"]["edges_added"], 0)
            self.assertEqual(result["summary"]["edges_removed"], 0)

    def test_refs_and_orphans_ignore_import_only_edges(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def shared_helper():\n    return 1\n")
            write_file(
                project_root,
                "main.py",
                (
                    "from lib import shared_helper\n\n"
                    "def only_imports():\n"
                    "    return 1\n"
                ),
            )

            symbols, edges = scan_project(project_root)
            save_cache(project_root, symbols, edges)

            refs = analyze_refs(project_root, "shared_helper")
            orphan_names = {item["name"] for item in find_orphans(project_root)}

            self.assertEqual(refs["ref_count"], 0)
            self.assertTrue(refs["is_entry"])
            self.assertTrue(refs["is_leaf"])
            self.assertIn("shared_helper", orphan_names)

    def test_orphans_do_not_hide_get_prefix_functions_without_static_references(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def get_unused():\n    return 1\n")

            symbols, edges = scan_project(project_root)
            save_cache(project_root, symbols, edges)

            orphan_names = {item["name"] for item in find_orphans(project_root)}

            self.assertIn("get_unused", orphan_names)


if __name__ == "__main__":
    unittest.main()
