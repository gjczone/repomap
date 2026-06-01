"""Issue #143: impact 命令 _affected_severity 性能优化回归测试。

验证 _build_file_severity_index 正确性、与 _affected_severity 等价性，
以及 _impact_type_level target_set hoisting 后行为不变。
"""

from __future__ import annotations

import unittest
from collections import defaultdict

from src import Edge, RepoGraph, Symbol


def _make_symbol(sid: str, name: str, file: str, kind: str = "function") -> Symbol:
    return Symbol(id=sid, name=name, kind=kind, file=file, line=1)


def _make_edge(source: str, target: str, kind: str = "call") -> Edge:
    return Edge(source=source, target=target, weight=0.5, kind=kind)


class MockEngine:
    """最小化的 mock engine，只暴露 graph 和 project_root。"""

    def __init__(self, graph: RepoGraph) -> None:
        self.graph = graph
        self.project_root = "/fake/project"


class FileSeverityIndexCorrectness(unittest.TestCase):
    """验证 _build_file_severity_index 对各种边类型和边界情况的计数正确性。"""

    def setUp(self) -> None:
        self.graph = RepoGraph()
        # file_a: [sym_a]  --calls--> file_b: [sym_b]
        # file_c: [sym_c]  --calls--> file_b: [sym_b]
        # file_b: [sym_b]  --calls--> file_a: [sym_a]
        # file_b: [sym_b]  --imports--> file_c: [sym_c]  (should NOT count)
        self.graph.symbols = {
            "sym_a": _make_symbol("sym_a", "func_a", "file_a.py"),
            "sym_b": _make_symbol("sym_b", "func_b", "file_b.py"),
            "sym_c": _make_symbol("sym_c", "func_c", "file_c.py"),
        }
        self.graph.file_symbols = defaultdict(list)
        self.graph.file_symbols["file_a.py"] = ["sym_a"]
        self.graph.file_symbols["file_b.py"] = ["sym_b"]
        self.graph.file_symbols["file_c.py"] = ["sym_c"]
        self.graph.incoming = defaultdict(list)
        # sym_b 被 sym_a 调用
        self.graph.incoming["sym_b"].append(_make_edge("sym_a", "sym_b", "call"))
        # sym_b 被 sym_c 调用
        self.graph.incoming["sym_c"].append(_make_edge("sym_c", "sym_c", "call"))  # self-call, should not count
        # sym_a 被 sym_b 调用
        self.graph.incoming["sym_a"].append(_make_edge("sym_b", "sym_a", "call"))
        # sym_b 有 import 边（不应计数）
        self.graph.incoming["sym_c"].append(_make_edge("sym_b", "sym_c", "import"))

    def test_basic_index_correctness(self) -> None:
        from src.cli.commands.impact import _build_file_severity_index

        index = _build_file_severity_index(self.graph)
        # file_a: 被 sym_b(from file_b) 调用 → 1
        self.assertEqual(index.get("file_a.py", 0), 1)
        # file_b: 被 sym_a(from file_a) 调用 → 1
        self.assertEqual(index.get("file_b.py", 0), 1)
        # file_c: sym_c 有 self-call(sym_c→sym_c, same file) 和 import 边 → 0
        self.assertEqual(index.get("file_c.py", 0), 0)

    def test_non_call_edges_excluded(self) -> None:
        from src.cli.commands.impact import _build_file_severity_index

        index = _build_file_severity_index(self.graph)
        # import edge from sym_b to sym_c should NOT be counted
        # file_c has one "import" incoming edge and one self-"call" → both excluded
        self.assertEqual(index.get("file_c.py", 0), 0)

    def test_self_referential_calls_excluded(self) -> None:
        from src.cli.commands.impact import _build_file_severity_index

        index = _build_file_severity_index(self.graph)
        # sym_c → sym_c (both in file_c.py) should NOT count
        self.assertEqual(index.get("file_c.py", 0), 0)

    def test_missing_source_symbol(self) -> None:
        from src.cli.commands.impact import _build_file_severity_index

        g = RepoGraph()
        g.symbols = {
            "exists": _make_symbol("exists", "f", "target.py"),
        }
        g.file_symbols["target.py"] = ["exists"]
        g.incoming = defaultdict(list)
        # edge from a symbol not in graph.symbols
        g.incoming["exists"].append(_make_edge("missing", "exists", "call"))

        index = _build_file_severity_index(g)
        # Missing source symbol → not counted (should not crash)
        self.assertEqual(index.get("target.py", 0), 0)

    def test_empty_graph(self) -> None:
        from src.cli.commands.impact import _build_file_severity_index

        index = _build_file_severity_index(RepoGraph())
        self.assertEqual(index, {})

    def test_file_without_external_calls_is_zero(self) -> None:
        from src.cli.commands.impact import _build_file_severity_index

        g = RepoGraph()
        g.symbols = {
            "s1": _make_symbol("s1", "isolated", "lonely.py"),
        }
        g.file_symbols["lonely.py"] = ["s1"]
        g.incoming = defaultdict(list)
        g.incoming["s1"].append(_make_edge("s1", "s1", "call"))  # self-call only

        index = _build_file_severity_index(g)
        self.assertEqual(index.get("lonely.py", 0), 0)


class FileSeverityIndexEquivalence(unittest.TestCase):
    """验证 _build_file_severity_index 与 _affected_severity 对每个文件返回相同值。"""

    def test_equivalence_with_affected_severity(self) -> None:
        from src.cli.commands.impact import (
            _affected_severity,
            _build_file_severity_index,
        )

        g = RepoGraph()
        # 构建一个更复杂的图: 5 个文件，多种边类型
        g.symbols = {}
        for i in range(10):
            fname = f"file_{i//2}.py"
            sid = f"sym_{i}"
            g.symbols[sid] = _make_symbol(sid, f"func_{i}", fname)
            g.file_symbols.setdefault(fname, []).append(sid)

        g.incoming = defaultdict(list)
        # sym_0(from file_0) --calls--> sym_2(from file_1)
        g.incoming["sym_2"].append(_make_edge("sym_0", "sym_2", "call"))
        # sym_0(from file_0) --calls--> sym_4(from file_2)
        g.incoming["sym_4"].append(_make_edge("sym_0", "sym_4", "call"))
        # sym_1(from file_0) --calls--> sym_2(from file_1)
        g.incoming["sym_2"].append(_make_edge("sym_1", "sym_2", "call"))
        # sym_3(from file_1) --calls--> sym_5(from file_2)
        g.incoming["sym_5"].append(_make_edge("sym_3", "sym_5", "call"))
        # sym_1(from file_0) --imports--> sym_6(from file_3)  (should not count)
        g.incoming["sym_6"].append(_make_edge("sym_1", "sym_6", "import"))
        # sym_0(from file_0) --calls--> sym_0(from file_0)  (self, should not count)
        g.incoming["sym_0"].append(_make_edge("sym_0", "sym_0", "call"))

        engine = MockEngine(g)
        index = _build_file_severity_index(g)

        for fname in g.file_symbols:
            old = _affected_severity(fname, engine)  # type: ignore[arg-type]
            new = index.get(fname, 0)
            self.assertEqual(
                old, new,
                f"file={fname}: _affected_severity={old} != index={new}",
            )


class ImpactTypeLevelHoisting(unittest.TestCase):
    """验证 _impact_type_level 在 target_set hoisting 后行为正确。"""

    def test_target_set_used_correctly_after_hoisting(self) -> None:
        from src.cli.commands.impact import _impact_type_level

        g = RepoGraph()
        # target files: file_a.py
        # file_a.py has sym_a with an external caller in file_b.py
        g.symbols = {
            "sym_a": _make_symbol("sym_a", "exported_func", "file_a.py"),
            "sym_b": _make_symbol("sym_b", "caller_func", "file_b.py"),
            "sym_c": _make_symbol("sym_c", "internal_func", "file_a.py"),
        }
        g.file_symbols = defaultdict(list)
        g.file_symbols["file_a.py"] = ["sym_a", "sym_c"]
        g.file_symbols["file_b.py"] = ["sym_b"]
        g.incoming = defaultdict(list)
        # sym_b(from file_b) calls sym_a(from file_a) — external caller
        g.incoming["sym_a"].append(_make_edge("sym_b", "sym_a", "call"))
        # sym_c has no incoming edges

        engine = MockEngine(g)
        results = _impact_type_level(engine, ["file_a.py"])  # type: ignore[arg-type]

        # exported_func (sym_a) should be reported because it has external caller
        self.assertTrue(
            any(r["symbol"] == "exported_func" for r in results),
            "exported_func with external caller should be in results",
        )
        # internal_func (sym_c) should NOT be reported (no incoming edges)
        self.assertFalse(
            any(r["symbol"] == "internal_func" for r in results),
            "internal_func without incoming edges should not be in results",
        )

    def test_multiple_target_files(self) -> None:
        from src.cli.commands.impact import _impact_type_level

        g = RepoGraph()
        g.symbols = {
            "sym_a": _make_symbol("sym_a", "f_a", "file_a.py"),
            "sym_b": _make_symbol("sym_b", "f_b", "file_b.py"),
            "sym_c": _make_symbol("sym_c", "f_c", "file_c.py"),
        }
        g.file_symbols = defaultdict(list)
        g.file_symbols["file_a.py"] = ["sym_a"]
        g.file_symbols["file_b.py"] = ["sym_b"]
        g.file_symbols["file_c.py"] = ["sym_c"]
        g.incoming = defaultdict(list)
        # sym_c calls sym_a (both not in same target set when both are targets)
        g.incoming["sym_a"].append(_make_edge("sym_c", "sym_a", "call"))
        # sym_c calls sym_b (both in target set → should NOT count as external)
        g.incoming["sym_b"].append(_make_edge("sym_c", "sym_b", "call"))

        engine = MockEngine(g)
        results = _impact_type_level(engine, ["file_a.py", "file_b.py"])  # type: ignore[arg-type]

        # f_a (sym_a): caller sym_c is in file_c.py, NOT in target_set → external caller → IN results
        self.assertTrue(
            any(r["symbol"] == "f_a" for r in results),
            "f_a has external caller from file_c.py, should be in results",
        )
        # f_b (sym_b): caller sym_c is in file_c.py, NOT in target_set → external caller → IN results
        self.assertTrue(
            any(r["symbol"] == "f_b" for r in results),
            "f_b has external caller from file_c.py, should be in results",
        )


if __name__ == "__main__":
    unittest.main()
