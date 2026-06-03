"""Tests for issue #182: BM25 搜索结果必须有细粒度排序（避免 0.5/1.0 二元分布）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _mk_symbol(
    sid: str,
    name: str,
    kind: str = "function",
    pagerank: float = 0.0,
    signature: str = "",
    file: str = "src/m.py",
    line: int = 1,
):
    from src import Symbol

    return Symbol(
        id=sid,
        name=name,
        kind=kind,
        file=file,
        line=line,
        end_line=line + 1,
        pagerank=pagerank,
        signature=signature,
    )


class BM25GranularityTests(unittest.TestCase):
    """Issue #182: 相似 BM25 分数必须有 tie-breaker 区分。"""

    def test_pagerank_tiebreaker_differentiates_equal_bm25(self) -> None:
        """两个符号 BM25 命中相同（都匹配 'websocket handler'），pagerank 高的应排前面。"""
        from src.search import SymbolSearchIndex

        # 必须有足够多的无关符号让 BM25 IDF > 0（corpus 太小 → IDF 负 → 分数 ≤0）
        symbols = {
            "src/a.py::handler_a::1": _mk_symbol(
                "src/a.py::handler_a::1",
                "handler_a",
                signature="def handler_a(websocket, msg)",
                pagerank=0.01,
            ),
            "src/b.py::handler_b::10": _mk_symbol(
                "src/b.py::handler_b::10",
                "handler_b",
                signature="def handler_b(websocket, msg)",
                pagerank=0.5,
            ),
        }
        for i in range(15):
            sid = f"src/x.py::unrelated_{i}::{i+100}"
            symbols[sid] = _mk_symbol(
                sid,
                f"unrelated_{i}",
                signature=f"def unrelated_{i}(foo, bar)",
                pagerank=0.001,
            )
        index = SymbolSearchIndex(symbols)
        results = index.search("websocket handler", top_k=3)

        # 结果应至少包含 2 个 handler（其余无关符号不匹配 'websocket'）
        handler_results = [r for r in results if "handler" in r[0]]
        self.assertEqual(
            len(handler_results),
            2,
            f"应得到 2 个 handler，实际 {handler_results} (全部 {results})",
        )

        # pagerank 更高的 handler_b 应排在 handler_a 之前
        (sid_first, score_first), (sid_second, score_second) = handler_results
        self.assertEqual(sid_first, "src/b.py::handler_b::10")
        self.assertGreater(
            score_first, score_second - 1e-6, "pagerank 应让高 PR 符号靠前"
        )

    def test_scores_have_at_least_three_distinct_values(self) -> None:
        """在中等规模 corpus 上，BM25 应产出至少 3 个不同分数值（非 0.5/1.0 二元）。"""
        from src.search import SymbolSearchIndex

        # 构造 20 个符号：不同数量的 query term 命中 + 不同 pagerank
        symbols = {}
        for i in range(20):
            name = f"ws_handler_{i}" if i % 3 == 0 else f"generic_func_{i}"
            sig = "def websocket handler" if i % 3 == 0 else f"def func_{i}"
            sid = f"src/m.py::{name}::{i}"
            symbols[sid] = _mk_symbol(
                sid,
                name,
                signature=sig,
                pagerank=0.01 * (i + 1),
            )
        index = SymbolSearchIndex(symbols)
        results = index.search("websocket handler", top_k=20)

        # 至少 3 个不同分数值（打破二元分布）
        scores = [round(s, 6) for _, s in results]
        distinct = set(scores)
        self.assertGreaterEqual(
            len(distinct),
            3,
            f"搜索结果应有 >=3 个不同分数，实际 {len(distinct)} 个：{sorted(distinct)}",
        )


if __name__ == "__main__":
    unittest.main()
