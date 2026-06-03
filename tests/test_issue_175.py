"""Tests for issue #175: graph_diff vs impactSession consistency (line drift tolerance)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import Symbol, compare_graph_snapshots


def _mk_symbol(
    name: str,
    file: str,
    line: int,
    kind: str = "function",
    signature: str = "",
    visibility: str = "internal",
) -> Symbol:
    """构造最小 Symbol。"""
    return Symbol(
        id=f"{file}::{name}::{line}",
        name=name,
        kind=kind,
        file=file,
        line=line,
        end_line=line + 1,
        signature=signature,
        visibility=visibility,
        pagerank=0.0,
    )


def _mk_prev(sym: Symbol) -> dict:
    """把 Symbol 转成 previous dict 格式。"""
    return {
        "id": sym.id,
        "name": sym.name,
        "file": sym.file,
        "line": sym.line,
        "end_line": sym.end_line,
        "kind": sym.kind,
        "signature": sym.signature,
        "visibility": sym.visibility,
    }


class GraphDiffLineDriftTests(unittest.TestCase):
    """Issue #175: 仅 line 漂移的符号不应出现在 added/removed 列表里。"""

    def test_line_drift_not_reported_as_add_remove(self) -> None:
        """同一符号（同 file/name/kind/signature）仅 line 变化 → 应识别为 modified，非 added+removed。"""
        prev = _mk_symbol("foo", "src/a.py", 10, signature="def foo()")
        curr = _mk_symbol("foo", "src/a.py", 20, signature="def foo()")  # 仅 line 漂移
        result = compare_graph_snapshots(
            current_symbols=[curr],
            current_edges=[],
            previous_symbols=[_mk_prev(prev)],
            previous_edges=[],
        )
        # 不应被误报为 add/remove
        self.assertEqual(result["summary"]["added"], 0)
        self.assertEqual(result["summary"]["removed"], 0)
        # 应归类为 modified（line_change）
        self.assertEqual(result["summary"]["modified"], 1)
        self.assertEqual(result["modified_symbols"][0]["name"], "foo")
        self.assertIn("10 -> 20", result["modified_symbols"][0]["line_change"])

    def test_signature_change_still_reported_as_modified(self) -> None:
        """signature 真实变化的符号应被报为 modified。"""
        prev = _mk_symbol("bar", "src/a.py", 10, signature="def bar(x)")
        curr = _mk_symbol("bar", "src/a.py", 10, signature="def bar(x, y)")
        result = compare_graph_snapshots(
            current_symbols=[curr],
            current_edges=[],
            previous_symbols=[_mk_prev(prev)],
            previous_edges=[],
        )
        self.assertEqual(result["summary"]["added"], 0)
        self.assertEqual(result["summary"]["removed"], 0)
        self.assertEqual(result["summary"]["modified"], 1)
        self.assertTrue(result["modified_symbols"][0]["signature_changed"])

    def test_genuinely_new_and_removed_symbols(self) -> None:
        """真正新增/删除的符号应出现在 added/removed 列表。"""
        prev = _mk_symbol("old_func", "src/a.py", 5)
        curr = _mk_symbol("new_func", "src/a.py", 15)
        result = compare_graph_snapshots(
            current_symbols=[curr],
            current_edges=[],
            previous_symbols=[_mk_prev(prev)],
            previous_edges=[],
        )
        self.assertEqual(result["summary"]["added"], 1)
        self.assertEqual(result["summary"]["removed"], 1)
        self.assertEqual(result["added_symbols"][0]["name"], "new_func")
        self.assertEqual(result["removed_symbols"][0]["name"], "old_func")

    def test_overloaded_symbols_with_line_drift(self) -> None:
        """同 file/name 的多个重载符号（如方法），各自按第 N 个匹配。"""
        prev1 = _mk_symbol(
            "process", "src/a.py", 10, kind="method", signature="def process(self)"
        )
        prev2 = _mk_symbol(
            "process", "src/a.py", 30, kind="method", signature="def process(self, x)"
        )
        curr1 = _mk_symbol(
            "process", "src/a.py", 15, kind="method", signature="def process(self)"
        )
        curr2 = _mk_symbol(
            "process", "src/a.py", 40, kind="method", signature="def process(self, x)"
        )
        result = compare_graph_snapshots(
            current_symbols=[curr1, curr2],
            current_edges=[],
            previous_symbols=[_mk_prev(prev1), _mk_prev(prev2)],
            previous_edges=[],
        )
        self.assertEqual(result["summary"]["added"], 0)
        self.assertEqual(result["summary"]["removed"], 0)
        self.assertEqual(result["summary"]["modified"], 2)


if __name__ == "__main__":
    unittest.main()
