"""Tests for issue #180: verify.untestedSymbols 必须携带 truncation 元数据。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class UntestedSymbolsTruncationTests(unittest.TestCase):
    """Issue #180: untestedSymbols 必须包含 total/truncated 元数据，避免 LLM 误以为完整。"""

    def _make_graph(self, num_untested: int) -> "MagicMock":
        """构造含 num_untested 个未测试符号的 RepoGraph mock。"""
        from src import RepoGraph, Symbol, Edge

        g = RepoGraph()
        # 1 个测试文件（用于触发 BFS 但不覆盖 main 符号）
        test_sym = Symbol(
            id="t.py::test_foo::1",
            name="test_foo",
            kind="function",
            file="tests/t.py",
            line=1,
        )
        g.symbols[test_sym.id] = test_sym
        g.file_symbols["tests/t.py"] = [test_sym.id]
        # N 个未测试符号（每个有足够高的 incoming 与 score，应通过默认 filter）
        for i in range(num_untested):
            sid = f"src/m.py::fn{i}::{i + 10}"
            sym = Symbol(
                id=sid,
                name=f"fn{i}",
                kind="function",
                file="src/m.py",
                line=i + 10,
                visibility="public",
            )
            g.symbols[sid] = sym
            g.file_symbols.setdefault("src/m.py", []).append(sid)
            # 给每个符号 5 个 incoming call edges → score = 5*1.0*5.0=25 ≥ 5.0
            for j in range(5):
                caller_id = f"src/o.py::caller_{i}_{j}::{i * 100 + j}"
                caller = Symbol(
                    id=caller_id,
                    name=f"caller_{i}_{j}",
                    kind="function",
                    file="src/o.py",
                    line=1,
                )
                g.symbols[caller_id] = caller
                g.file_symbols.setdefault("src/o.py", []).append(caller_id)
                g.incoming.setdefault(sid, []).append(
                    Edge(source=caller_id, target=sid, weight=1.0, kind="call")
                )
        return g

    def test_untested_symbols_carries_metadata(self) -> None:
        """find_untested_symbols 必须返回 metadata（total_before_filter/truncated/returned）。"""
        from src.topic import find_untested_symbols

        graph = self._make_graph(num_untested=50)
        meta: dict = {}
        result = find_untested_symbols(graph, metadata=meta)

        # 默认 max_results=30，50 个应被截断
        self.assertLessEqual(len(result), 30)
        self.assertEqual(meta.get("total_before_filter"), 50)
        self.assertEqual(meta.get("returned"), len(result))
        self.assertTrue(meta.get("truncated"))

    def test_untested_symbols_no_truncation_when_under_limit(self) -> None:
        """未超限时 truncated=False。"""
        from src.topic import find_untested_symbols

        graph = self._make_graph(num_untested=5)
        meta: dict = {}
        result = find_untested_symbols(graph, metadata=meta)

        self.assertEqual(len(result), 5)
        self.assertEqual(meta.get("total_before_filter"), 5)
        self.assertFalse(meta.get("truncated"))


if __name__ == "__main__":
    unittest.main()
