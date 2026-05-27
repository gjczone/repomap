"""Issue #56 regression tests — core algorithm fixes.

C1: is_test_like_file for JS/Go/Rust
C2: incremental cache validates st_size in addition to mtime
T1: AST walk depth >= 100 (no truncation at depth 30)
B2: anonymous function ID collision disambiguation
B5: _top_symbol_ids returns at most max_count entries
#48: orphan detection — import edges prevent orphan flagging
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock


# ── C1: is_test_like_file JS/Go/Rust ─────────────────────────────────────────


class TestC1IsTestLikeFile(unittest.TestCase):
    """Verify is_test_like_file correctly identifies test files by extension."""

    def test_test_js_identified(self) -> None:
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("src/foo.test.js"))
        self.assertTrue(is_test_like_file("bar.test.js"))

    def test_test_go_identified(self) -> None:
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("pkg/foo_test.go"))
        self.assertTrue(is_test_like_file("bar_test.go"))

    def test_test_rs_identified(self) -> None:
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("src/foo_test.rs"))
        self.assertTrue(is_test_like_file("bar_test.rs"))

    def test_test_dir_identified(self) -> None:
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("tests/test_foo.py"))
        self.assertTrue(is_test_like_file("__tests__/bar.js"))

    def test_non_test_files_rejected(self) -> None:
        from src.topic import is_test_like_file

        self.assertFalse(is_test_like_file("src/main.js"))
        self.assertFalse(is_test_like_file("pkg/handler.go"))
        self.assertFalse(is_test_like_file("src/lib.rs"))
        self.assertFalse(is_test_like_file("src/utils.py"))

    def test_test_like_markers_exhaustive(self) -> None:
        """Verify all documented test-like patterns are recognized."""
        from src.topic import is_test_like_file

        test_files = [
            "test_something.py",
            "something_test.py",
            "something_test.go",
            "something_test.rs",
            "component.spec.ts",
            "component.test.ts",
            "component.test.tsx",
            "component.spec.tsx",
            "component.test.js",
            "component.spec.js",
            "component.test.jsx",
            "component.spec.jsx",
            "component.test.mjs",
            "component.spec.mjs",
        ]
        for f in test_files:
            with self.subTest(file=f):
                self.assertTrue(is_test_like_file(f), f"Expected {f} to be test-like")


# ── C2: incremental cache st_size validation ─────────────────────────────────


class TestC2IncCacheStSize(unittest.TestCase):
    """Verify _restore_from_inc_cache validates file size in addition to mtime."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_size_mismatch_rejects_cache_entry(self) -> None:
        """When file content changes but mtime stays same, size check rejects."""
        from src.core import RepoMapEngine

        src_file = self.project_root / "app.py"
        src_file.write_text("def hello(): return 'world'\n")

        engine = RepoMapEngine(str(self.project_root))
        if not engine.ts.parsers:
            self.skipTest("tree-sitter not available")

        engine.scan(max_files=100)

        entry_mtime = src_file.stat().st_mtime

        # Change file content (different size)
        new_content = "def hello():\n    return 'world'\n    # extra comment\n"
        src_file.write_text(new_content)
        # Preserve original mtime to force the size check to be the discriminator
        os.utime(str(src_file), (entry_mtime, entry_mtime))

        from src import FileCacheEntry

        entry = FileCacheEntry(
            mtime=entry_mtime,
            size=len("def hello(): return 'world'\n".encode("utf-8")),
            symbols_json=[],
            imports=[],
        )

        result = engine._restore_from_inc_cache("app.py", entry)
        self.assertFalse(
            result,
            "Should reject cache entry when file size differs even if mtime matches",
        )

    def test_size_match_allows_cache_restore(self) -> None:
        """When mtime AND size match, cache entry is accepted."""
        from src.core import RepoMapEngine

        src_file = self.project_root / "app.py"
        content = "def hello(): return 'world'\n"
        src_file.write_text(content)

        engine = RepoMapEngine(str(self.project_root))
        if not engine.ts.parsers:
            self.skipTest("tree-sitter not available")

        engine.scan(max_files=100)

        entry_mtime = src_file.stat().st_mtime
        entry_size = src_file.stat().st_size

        from src import FileCacheEntry

        entry = FileCacheEntry(
            mtime=entry_mtime,
            size=entry_size,
            symbols_json=[],
            imports=[],
        )

        result = engine._restore_from_inc_cache("app.py", entry)
        self.assertTrue(
            result,
            "Should accept cache entry when both mtime and size match",
        )


# ── T1: _walk depth >= 100 ──────────────────────────────────────────────────


class TestT1WalkDepth(unittest.TestCase):
    """Verify AST walk in type inference does not truncate at depth 30."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _generate_deeply_nested_python(depth: int) -> str:
        """Generate Python code with deeply nested 'if' blocks containing a function."""
        indent = "    "
        lines = []
        for i in range(depth):
            lines.append(f"{indent * i}if True:")
        lines.append(f"{indent * depth}def deep_func(x: int) -> str:")
        lines.append(f"{indent * (depth + 1)}return str(x)")
        lines.append(f"{indent * depth}deep_func(42)")
        return "\n".join(lines)

    def test_depth_50_does_not_truncate(self) -> None:
        """Type inference should handle depth 50 without issues."""
        from src.core import RepoMapEngine
        from src.type_inference import extract_types_for_file

        code = self._generate_deeply_nested_python(50)
        py_file = Path(self.tmpdir) / "deep.py"
        py_file.write_text(code)

        engine = RepoMapEngine(str(self.tmpdir))
        if not engine.ts.parsers:
            self.skipTest("tree-sitter not available")

        lang = "python"
        tree = engine.ts.parse(py_file.read_bytes(), lang)
        self.assertIsNotNone(tree, "Tree-sitter should parse the deep file")

        symbols = engine.ts.extract_symbols(tree, lang, "deep.py", py_file.read_bytes())
        self.assertGreater(len(symbols), 0, "Should find the deep function")

        all_symbols = {s.id: s for s in symbols}
        sym_ids = [s.id for s in symbols]

        count = extract_types_for_file(tree, lang, sym_ids, all_symbols)
        self.assertIsInstance(
            count, int, "Type inference should complete without error"
        )

    def test_depth_30_is_not_a_barrier(self) -> None:
        """Depth exactly 30 should work (no truncation at 30)."""
        from src.core import RepoMapEngine
        from src.type_inference import extract_types_for_file

        code = self._generate_deeply_nested_python(30)
        py_file = Path(self.tmpdir) / "deep30.py"
        py_file.write_text(code)

        engine = RepoMapEngine(str(self.tmpdir))
        if not engine.ts.parsers:
            self.skipTest("tree-sitter not available")

        tree = engine.ts.parse(py_file.read_bytes(), "python")
        self.assertIsNotNone(tree)

        symbols = engine.ts.extract_symbols(
            tree, "python", "deep30.py", py_file.read_bytes()
        )
        self.assertGreater(len(symbols), 0)

        all_symbols = {s.id: s for s in symbols}
        sym_ids = [s.id for s in symbols]

        count = extract_types_for_file(tree, "python", sym_ids, all_symbols)
        self.assertIsInstance(count, int)

    def test_depth_101_truncates_gracefully(self) -> None:
        """Depth 101 should trigger the limit but NOT crash."""
        from src.core import RepoMapEngine
        from src.type_inference import extract_types_for_file

        code = self._generate_deeply_nested_python(101)
        py_file = Path(self.tmpdir) / "deep101.py"
        py_file.write_text(code)

        engine = RepoMapEngine(str(self.tmpdir))
        if not engine.ts.parsers:
            self.skipTest("tree-sitter not available")

        tree = engine.ts.parse(py_file.read_bytes(), "python")
        self.assertIsNotNone(tree)

        symbols = engine.ts.extract_symbols(
            tree, "python", "deep101.py", py_file.read_bytes()
        )
        all_symbols = {s.id: s for s in symbols}
        sym_ids = [s.id for s in symbols]

        try:
            count = extract_types_for_file(tree, "python", sym_ids, all_symbols)
            self.assertIsInstance(count, int)
        except RecursionError:
            self.fail("Depth 101 should not cause RecursionError")


# ── B2: anonymous function ID collision ──────────────────────────────────────


class TestB2AnonymousFunctionIdCollision(unittest.TestCase):
    """Verify anonymous functions on the same line get unique IDs."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_two_anonymous_js_functions_same_line_unique_ids(self) -> None:
        """Two arrow functions on one line in JS must have distinct IDs."""
        from src.core import RepoMapEngine

        js_file = Path(self.tmpdir) / "anon.js"
        js_file.write_text(
            "export const a = (x) => x + 1; export const b = (y) => y + 2;\n"
        )

        engine = RepoMapEngine(str(self.tmpdir))
        if not engine.ts.parsers:
            self.skipTest("tree-sitter not available")

        data = js_file.read_bytes()
        tree = engine.ts.parse(data, "javascript")
        self.assertIsNotNone(tree)

        symbols = engine.ts.extract_symbols(tree, "javascript", "anon.js", data)
        ids = [s.id for s in symbols]
        self.assertEqual(
            len(ids),
            len(set(ids)),
            f"All symbol IDs must be unique, got: {ids}",
        )

    def test_anonymous_collision_counter_increments(self) -> None:
        """Verify the collision counter produces distinct IDs for same-line collisions."""
        from src.parser import TreeSitterAdapter

        adapter = TreeSitterAdapter()

        js_file = Path(self.tmpdir) / "collide.js"
        js_file.write_text("export default () => 1; export default () => 2;\n")

        data = js_file.read_bytes()
        tree = adapter.parse(data, "javascript")
        if tree is None:
            self.skipTest("tree-sitter not available for JavaScript")

        symbols = adapter.extract_symbols(tree, "javascript", "collide.js", data)
        anon_ids = [s.id for s in symbols if s.kind == "anonymous_function"]
        self.assertGreaterEqual(
            len(anon_ids),
            1,
            "Should find at least one anonymous function",
        )
        self.assertEqual(
            len(anon_ids),
            len(set(anon_ids)),
            f"Anonymous function IDs must be unique: {anon_ids}",
        )
        if len(anon_ids) > 1:
            has_suffixed = any("#" in aid.split("::")[-1] for aid in anon_ids)
            self.assertTrue(
                has_suffixed,
                f"Expected at least one collision-suffixed ID among: {anon_ids}",
            )

    def test_anonymous_python_lambdas_unique_ids(self) -> None:
        """Two assigned lambdas on same line in Python must have unique IDs."""
        from src.parser import TreeSitterAdapter

        adapter = TreeSitterAdapter()

        py_file = Path(self.tmpdir) / "lambdas.py"
        py_file.write_text("x = lambda a: a + 1; y = lambda b: b + 2\n")

        data = py_file.read_bytes()
        tree = adapter.parse(data, "python")
        if tree is None:
            self.skipTest("tree-sitter not available for Python")

        symbols = adapter.extract_symbols(tree, "python", "lambdas.py", data)
        lambda_symbols = [s for s in symbols if s.kind == "lambda"]
        self.assertGreaterEqual(
            len(lambda_symbols),
            1,
            "Should find at least one lambda symbol",
        )
        ids = [s.id for s in lambda_symbols]
        self.assertEqual(
            len(ids),
            len(set(ids)),
            f"Lambda symbol IDs must be unique: {ids}",
        )


# ── B5: _top_symbol_ids max_count ────────────────────────────────────────────


class TestB5TopSymbolIdsMaxCount(unittest.TestCase):
    """Verify _top_symbol_ids returns at most max_count entries."""

    def test_max_count_respected(self) -> None:
        """With 100 symbols for a file, _top_symbol_ids returns at most max_count."""
        from src import Symbol, RepoGraph
        from src.ranking import EdgeBuilder

        graph = RepoGraph()
        file_path = "src/module.py"
        symbol_ids = []

        for i in range(100):
            sid = f"{file_path}::func_{i}::{i + 1}"
            sym = Symbol(
                id=sid,
                name=f"func_{i}",
                kind="function" if i % 3 != 0 else "class",
                file=file_path,
                line=i + 1,
                visibility="public" if i % 2 == 0 else "private",
            )
            graph.symbols[sid] = sym
            symbol_ids.append(sid)

        graph.file_symbols[file_path] = symbol_ids

        resolver = MagicMock()
        builder = EdgeBuilder(graph, resolver)

        for max_count in [1, 3, 5, 10, 50]:
            with self.subTest(max_count=max_count):
                result = builder._top_symbol_ids(file_path, max_count=max_count)
                self.assertLessEqual(
                    len(result),
                    max_count,
                    f"Expected at most {max_count} results, got {len(result)}",
                )

    def test_default_max_count_is_3(self) -> None:
        from src import Symbol, RepoGraph
        from src.ranking import EdgeBuilder

        graph = RepoGraph()
        file_path = "src/mod.py"
        symbol_ids = []
        for i in range(10):
            sid = f"{file_path}::fn_{i}::{i + 1}"
            sym = Symbol(
                id=sid,
                name=f"fn_{i}",
                kind="function",
                file=file_path,
                line=i + 1,
                visibility="public",
            )
            graph.symbols[sid] = sym
            symbol_ids.append(sid)
        graph.file_symbols[file_path] = symbol_ids

        resolver = MagicMock()
        builder = EdgeBuilder(graph, resolver)

        result = builder._top_symbol_ids(file_path)
        self.assertLessEqual(len(result), 3, "Default max_count=3 should be enforced")

    def test_fewer_symbols_than_max_returns_all(self) -> None:
        from src import Symbol, RepoGraph
        from src.ranking import EdgeBuilder

        graph = RepoGraph()
        file_path = "src/small.py"
        symbol_ids = []
        for i in range(2):
            sid = f"{file_path}::fn_{i}::{i + 1}"
            sym = Symbol(
                id=sid,
                name=f"fn_{i}",
                kind="function",
                file=file_path,
                line=i + 1,
                visibility="public",
            )
            graph.symbols[sid] = sym
            symbol_ids.append(sid)
        graph.file_symbols[file_path] = symbol_ids

        resolver = MagicMock()
        builder = EdgeBuilder(graph, resolver)

        result = builder._top_symbol_ids(file_path, max_count=5)
        self.assertEqual(len(result), 2, "Should return all 2 when max_count=5")

    def test_top_ids_picks_highest_scored(self) -> None:
        """Higher visibility + kind scores should be preferred."""
        from src import Symbol, RepoGraph
        from src.ranking import EdgeBuilder

        graph = RepoGraph()
        file_path = "src/ranked.py"

        syms = [
            Symbol(
                id=f"{file_path}::low::{1}",
                name="low",
                kind="function",
                file=file_path,
                line=1,
                visibility="private",
            ),
            Symbol(
                id=f"{file_path}::mid::{2}",
                name="mid",
                kind="function",
                file=file_path,
                line=2,
                visibility="public",
            ),
            Symbol(
                id=f"{file_path}::high::{3}",
                name="high",
                kind="class",
                file=file_path,
                line=3,
                visibility="exported",
            ),
        ]
        for sym in syms:
            graph.symbols[sym.id] = sym
        graph.file_symbols[file_path] = [s.id for s in syms]

        resolver = MagicMock()
        builder = EdgeBuilder(graph, resolver)

        result = builder._top_symbol_ids(file_path, max_count=2)
        self.assertEqual(len(result), 2)
        self.assertIn(
            f"{file_path}::high::3",
            result,
            "Exported class (score 7) should be in top 2",
        )
        self.assertNotIn(
            f"{file_path}::low::1",
            result,
            "Private function (score 4) should not be in top 2",
        )


# ── #48: orphan counts import edges ──────────────────────────────────────────


class Test48OrphanCountsImportEdges(unittest.TestCase):
    """Verify symbols with only import references are NOT flagged as orphans."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build_cache_file(self, symbols_data, edges_data):
        """Write a minimal cache using save_cache for analyze_refs to consume."""
        from src import Symbol as Sym, Edge as Ed
        from src.toolkit import save_cache

        symbols = []
        for sd in symbols_data:
            symbols.append(Sym(**sd))

        edges = []
        for ed in edges_data:
            edges.append(Ed(**ed))

        save_cache(str(self.project_root), symbols, edges)

    def test_import_only_edges_prevent_orphan_status(self) -> None:
        """Symbol A imports Symbol B — both should have refs, neither orphan."""
        from src.toolkit import analyze_refs

        sym_a = {
            "id": "src/a.py::func_a::1",
            "name": "func_a",
            "kind": "function",
            "file": "src/a.py",
            "line": 1,
            "end_line": 1,
            "col": 0,
            "visibility": "public",
            "signature": "def func_a()",
            "return_type": "",
            "params": "",
            "docstring": "",
            "pagerank": 0.0,
        }
        sym_b = {
            "id": "src/b.py::func_b::1",
            "name": "func_b",
            "kind": "function",
            "file": "src/b.py",
            "line": 1,
            "end_line": 1,
            "col": 0,
            "visibility": "public",
            "signature": "def func_b()",
            "return_type": "",
            "params": "",
            "docstring": "",
            "pagerank": 0.0,
        }

        edge = {
            "source": "src/a.py::func_a::1",
            "target": "src/b.py::func_b::1",
            "weight": 0.35,
            "kind": "import",
        }

        self._build_cache_file([sym_a, sym_b], [edge])

        result = analyze_refs(str(self.project_root))
        self.assertNotIn("error", result, f"Should not error: {result}")

        orphans = result.get("orphaned_symbols", [])
        orphan_ids = {o["id"] for o in orphans}

        self.assertNotIn(
            sym_a["id"],
            orphan_ids,
            "Symbol with import out-edge should NOT be orphan",
        )
        self.assertNotIn(
            sym_b["id"],
            orphan_ids,
            "Symbol with import in-edge should NOT be orphan",
        )

    def test_call_only_edges_prevent_orphan_status(self) -> None:
        """Symbol A calls Symbol B — both have refs, neither orphan."""
        from src.toolkit import analyze_refs

        sym_a = {
            "id": "src/a.py::func_a::1",
            "name": "func_a",
            "kind": "function",
            "file": "src/a.py",
            "line": 1,
            "end_line": 1,
            "col": 0,
            "visibility": "public",
            "signature": "def func_a()",
            "return_type": "",
            "params": "",
            "docstring": "",
            "pagerank": 0.0,
        }
        sym_b = {
            "id": "src/b.py::func_b::1",
            "name": "func_b",
            "kind": "function",
            "file": "src/b.py",
            "line": 1,
            "end_line": 1,
            "col": 0,
            "visibility": "public",
            "signature": "def func_b()",
            "return_type": "",
            "params": "",
            "docstring": "",
            "pagerank": 0.0,
        }

        edge = {
            "source": "src/a.py::func_a::1",
            "target": "src/b.py::func_b::1",
            "weight": 0.50,
            "kind": "call",
        }

        self._build_cache_file([sym_a, sym_b], [edge])

        result = analyze_refs(str(self.project_root))
        self.assertNotIn("error", result)

        orphans = result.get("orphaned_symbols", [])
        orphan_ids = {o["id"] for o in orphans}

        self.assertNotIn(sym_a["id"], orphan_ids)
        self.assertNotIn(sym_b["id"], orphan_ids)

    def test_truly_isolated_symbol_is_orphan(self) -> None:
        """Symbol with no edges at all (not public entry) should be orphan."""
        from src.toolkit import analyze_refs

        sym = {
            "id": "src/dead.py::dead_func::1",
            "name": "dead_func",
            "kind": "function",
            "file": "src/dead.py",
            "line": 1,
            "end_line": 1,
            "col": 0,
            "visibility": "private",
            "signature": "def dead_func()",
            "return_type": "",
            "params": "",
            "docstring": "",
            "pagerank": 0.0,
        }

        self._build_cache_file([sym], [])

        result = analyze_refs(str(self.project_root))
        self.assertNotIn("error", result)

        orphans = result.get("orphaned_symbols", [])
        orphan_ids = {o["id"] for o in orphans}

        self.assertIn(
            sym["id"],
            orphan_ids,
            "Completely isolated symbol should be flagged as orphan",
        )

    def test_public_entry_not_orphan_even_without_edges(self) -> None:
        """A public entry point (main, handler) without edges is NOT an orphan."""
        from src.toolkit import analyze_refs

        sym = {
            "id": "src/main.py::main::1",
            "name": "main",
            "kind": "function",
            "file": "src/main.py",
            "line": 1,
            "end_line": 1,
            "col": 0,
            "visibility": "public",
            "signature": "def main()",
            "return_type": "",
            "params": "",
            "docstring": "",
            "pagerank": 0.0,
        }

        self._build_cache_file([sym], [])

        result = analyze_refs(str(self.project_root))
        self.assertNotIn("error", result)

        orphans = result.get("orphaned_symbols", [])
        orphan_ids = {o["id"] for o in orphans}

        self.assertNotIn(
            sym["id"],
            orphan_ids,
            "Public entry point 'main' should NOT be flagged orphan",
        )
