"""Issue #58 regression tests — incremental cache size field persistence.

Bug: FileCacheEntry.size was never saved to/loaded from disk,
and _restore_from_inc_cache stored a bare float (entry.mtime)
instead of a (mtime, size) tuple, causing ValueError on unpack.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import FileCacheEntry, IncrementalCache
from src.toolkit import (
    _inc_cache_to_dict,
    load_incremental_cache,
    save_incremental_cache,
)
from src.core import RepoMapEngine


# ── Helpers ───────────────────────────────────────────────────────────────────


def write_file(root: str, relative_path: str, content: str) -> Path:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── Test: _inc_cache_to_dict round-trips size correctly ───────────────────────


class TestIncCacheSizeRoundTrip(unittest.TestCase):
    """Verify size is serialized and deserialized correctly."""

    def test_size_serialized_in_dict(self) -> None:
        """_inc_cache_to_dict must include 'size' key."""
        entry = FileCacheEntry(
            mtime=1234567890.0,
            size=42,
            symbols_json=[{"name": "foo", "kind": "function", "line": 1}],
        )
        cache = IncrementalCache(
            project_root_hash="abc123",
            git_head="deadbeef",
            files={"test.py": entry},
            scan_stats_json={"processed_files": 1},
        )
        result = _inc_cache_to_dict(cache)
        self.assertIn("test.py", result["files"])
        file_entry = result["files"]["test.py"]
        self.assertEqual(file_entry["size"], 42)
        self.assertEqual(file_entry["mtime"], 1234567890.0)

    def test_size_loaded_from_json(self) -> None:
        """load_incremental_cache must restore size from cache file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_data = {
                "project_root_hash": "abc123",
                "git_head": "deadbeef",
                "files": {
                    "test.py": {
                        "mtime": 1234567890.0,
                        "size": 99,
                        "symbols_json": [],
                        "imports": [],
                        "import_bindings_json": [],
                        "exports_json": [],
                        "calls_json": [],
                        "routes_json": [],
                    }
                },
                "scan_stats_json": {},
            }
            cache_path = Path(tmpdir, ".repomap", "incremental.json")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

            # Monkey-patch get_incremental_cache_path to return our test path
            import src.toolkit as tmod

            def _fake_path(_unused: str) -> Path:
                return cache_path

            orig = tmod.get_incremental_cache_path
            tmod.get_incremental_cache_path = _fake_path
            try:
                loaded = load_incremental_cache(tmpdir)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                entry = loaded.files.get("test.py")
                self.assertIsNotNone(entry)
                assert entry is not None
                self.assertEqual(entry.size, 99)
                self.assertEqual(entry.mtime, 1234567890.0)
            finally:
                tmod.get_incremental_cache_path = orig

    def test_size_zero_loaded_correctly(self) -> None:
        """Empty file (size=0) must be handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_data = {
                "project_root_hash": "empty",
                "git_head": "",
                "files": {
                    "empty.py": {
                        "mtime": 1000000000.0,
                        "size": 0,
                        "symbols_json": [],
                        "imports": [],
                        "import_bindings_json": [],
                        "exports_json": [],
                        "calls_json": [],
                        "routes_json": [],
                    }
                },
                "scan_stats_json": {},
            }
            cache_path = Path(tmpdir, ".repomap", "incremental.json")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

            import src.toolkit as tmod

            def _fake_path(_unused: str) -> Path:
                return cache_path

            orig = tmod.get_incremental_cache_path
            tmod.get_incremental_cache_path = _fake_path
            try:
                loaded = load_incremental_cache(tmpdir)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                entry = loaded.files.get("empty.py")
                self.assertIsNotNone(entry)
                assert entry is not None
                self.assertEqual(entry.size, 0)
            finally:
                tmod.get_incremental_cache_path = orig

    def test_missing_size_defaults_to_zero(self) -> None:
        """Backward compat: old cache files without 'size' default to 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Old-format cache: no "size" field
            cache_data = {
                "project_root_hash": "old",
                "git_head": "",
                "files": {
                    "old.py": {
                        "mtime": 2000000000.0,
                        "symbols_json": [],
                        "imports": [],
                        "import_bindings_json": [],
                        "exports_json": [],
                        "calls_json": [],
                        "routes_json": [],
                    }
                },
                "scan_stats_json": {},
            }
            cache_path = Path(tmpdir, ".repomap", "incremental.json")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

            import src.toolkit as tmod

            def _fake_path(_unused: str) -> Path:
                return cache_path

            orig = tmod.get_incremental_cache_path
            tmod.get_incremental_cache_path = _fake_path
            try:
                loaded = load_incremental_cache(tmpdir)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                entry = loaded.files.get("old.py")
                self.assertIsNotNone(entry)
                assert entry is not None
                self.assertEqual(entry.size, 0)  # defaults to 0
            finally:
                tmod.get_incremental_cache_path = orig


# ── Test: _restore_from_inc_cache stores (mtime, size) tuple ──────────────────


class TestRestoreFromIncCache(unittest.TestCase):
    """Verify _restore_from_inc_cache stores correct tuple in _cache."""

    def test_cache_stores_tuple_not_bare_float(self) -> None:
        """After restore, _cache[fp] must be (mtime, size), not a bare float."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RepoMapEngine(tmpdir)
            file_path = "test.py"

            # Create a real file so _restore_from_inc_cache doesn't bail early
            real_path = Path(tmpdir, file_path)
            real_path.write_text("pass", encoding="utf-8")
            real_stat = real_path.stat()

            entry = FileCacheEntry(
                mtime=real_stat.st_mtime,
                size=real_stat.st_size,
            )

            # Pre-condition: graph must have the file registered
            engine.graph.file_symbols[file_path] = []
            engine.graph.file_imports[file_path] = []
            engine.graph.file_import_bindings[file_path] = []
            engine.graph.file_calls[file_path] = []
            engine.graph.symbols = {}

            result = engine._restore_from_inc_cache(file_path, entry)
            self.assertTrue(result, "_restore_from_inc_cache should return True")

            cached = engine._cache.get(file_path)
            self.assertIsNotNone(cached, f"_cache[{file_path!r}] must be set")
            assert cached is not None  # type: narrow
            self.assertIsInstance(cached, tuple, "_cache value must be a tuple")
            mtime, size = cached
            self.assertEqual(mtime, real_stat.st_mtime, "first element is mtime")
            self.assertEqual(size, real_stat.st_size, "second element is size")

    def test_cache_tuple_unpacks_in_process_file(self) -> None:
        """Verify the (mtime, size) tuple unpacks without ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RepoMapEngine(tmpdir)

            file_path = "real.py"
            real_path = write_file(tmpdir, file_path, "def hello():\n    return 1\n")

            # Pre-populate _cache with a valid tuple
            stat = real_path.stat()
            engine._cache[file_path] = (stat.st_mtime, stat.st_size)

            # Verify we can unpack it (this is what _process_file does)
            cached = engine._cache[file_path]
            cached_mtime, cached_size = cached
            self.assertEqual(cached_mtime, stat.st_mtime)
            self.assertEqual(cached_size, stat.st_size)


# ── Test: save + load with real file sizes ────────────────────────────────────


class TestSaveLoadWithRealFiles(unittest.TestCase):
    """End-to-end: save incremental cache, load it, verify sizes."""

    def test_real_file_size_round_trips(self) -> None:
        """Write a real file, scan it, save cache, reload, verify size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            content = "def foo():\n    return 42\n"
            write_file(tmpdir, "mod.py", content)

            engine = RepoMapEngine(tmpdir)

            # Register the file and its symbols in the engine graph
            engine.graph.file_symbols["mod.py"] = []
            engine.graph.file_imports["mod.py"] = []
            engine.graph.file_import_bindings["mod.py"] = []
            engine.graph.file_calls["mod.py"] = []
            engine.graph.symbols = {}

            # Save incremental cache
            cache_path = save_incremental_cache(tmpdir, engine)
            self.assertTrue(cache_path.exists())

            # Check the raw JSON on disk has "size"
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            mod_entry = raw["files"].get("mod.py")
            self.assertIsNotNone(mod_entry, "mod.py must be in cache")
            self.assertIn("size", mod_entry, "size key must be present in JSON")
            self.assertEqual(mod_entry["size"], len(content))

            # Load via load_incremental_cache
            loaded = load_incremental_cache(tmpdir)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            entry = loaded.files.get("mod.py")
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry.size, len(content))


if __name__ == "__main__":
    unittest.main()
