import os
import tempfile
import unittest
from pathlib import Path

from src.gitignore import GitignoreParser


class GitignoreParserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel_path: str, content: str = ""):
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_builtin_skips_node_modules(self):
        self._write("node_modules/foo/index.js")
        self._write("src/main.py")
        p = GitignoreParser(self.root)
        self.assertTrue(p.is_ignored("node_modules/foo/index.js"))
        self.assertFalse(p.is_ignored("src/main.py"))

    def test_builtin_skips_lock_files(self):
        self._write("package-lock.json")
        self._write("Cargo.lock")
        self._write("src/index.ts")
        p = GitignoreParser(self.root)
        self.assertTrue(p.is_ignored("package-lock.json"))
        self.assertTrue(p.is_ignored("Cargo.lock"))
        self.assertFalse(p.is_ignored("src/index.ts"))

    def test_project_gitignore_anchored(self):
        self._write(".gitignore", "/logs/debug.log\n")
        self._write("logs/debug.log")
        self._write("src/logs/debug.log")
        p = GitignoreParser(self.root)
        self.assertTrue(p.is_ignored("logs/debug.log"))
        self.assertFalse(p.is_ignored("src/logs/debug.log"))

    def test_project_gitignore_negation(self):
        self._write(".gitignore", "generated/\n!generated/keep.py\n")
        self._write("generated/foo.py")
        self._write("generated/keep.py")
        p = GitignoreParser(self.root)
        self.assertTrue(p.is_ignored("generated/foo.py"))
        self.assertFalse(p.is_ignored("generated/keep.py"))

    def test_double_star_wildcard(self):
        self._write(".gitignore", "**/test_*.py\n")
        self._write("test_main.py")
        self._write("src/test_utils.py")
        self._write("src/deep/nested/test_thing.py")
        self._write("src/normal.py")
        p = GitignoreParser(self.root)
        self.assertTrue(p.is_ignored("test_main.py"))
        self.assertTrue(p.is_ignored("src/test_utils.py"))
        self.assertTrue(p.is_ignored("src/deep/nested/test_thing.py"))
        self.assertFalse(p.is_ignored("src/normal.py"))

    def test_subdirectory_gitignore(self):
        self._write("src/.gitignore", "*.tmp\n")
        self._write("src/main.py")
        self._write("src/cache.tmp")
        self._write("tests/cache.tmp")
        p = GitignoreParser(self.root)
        self.assertTrue(p.is_ignored("src/cache.tmp"))
        self.assertFalse(p.is_ignored("tests/cache.tmp"))
        self.assertFalse(p.is_ignored("src/main.py"))

    def test_gitignore_cache(self):
        self._write(".gitignore", "*.log\n")
        p = GitignoreParser(self.root)
        self.assertTrue(p.is_ignored("error.log"))
        self.assertTrue(p.is_ignored("error.log"))


if __name__ == "__main__":
    unittest.main()
