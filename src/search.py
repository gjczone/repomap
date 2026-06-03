"""
BM25 符号搜索引擎。

基于 rank-bm25 库对符号名称、签名、文档字符串、返回类型、参数类型
建立倒排索引，支持自然语言查询快速定位相关符号。

设计原则：
- 可选依赖：rank-bm25 不可用时自动降级为关键词匹配
- 索引在 scan 之后按需构建，不影响扫描性能
- 搜索结果与 Symbol 体系完全对齐
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("repomap.search")

if TYPE_CHECKING:
    from . import Symbol

_HAS_BM25 = False
try:
    from rank_bm25 import BM25Okapi

    _HAS_BM25 = True
except ImportError:
    BM25Okapi: Any = None  # type: ignore[no-redef]


def _create_chunk_symbol_id(sym_id: str, start_line: int, end_line: int) -> str:
    """Generate a chunk identifier for a symbol's line-range segment."""
    return f"{sym_id}#chunk:L{start_line}-L{end_line}"


def _symbol_is_large(sym: Symbol, threshold: int = 100) -> bool:
    """Check if a symbol's line range exceeds *threshold* lines."""
    end = max(sym.end_line, sym.line)
    return (end - sym.line) > threshold


def _tokenize(text: str) -> list[str]:
    # 使用 Unicode-aware \w 支持非 ASCII 标识符（中文、西里尔字母等）
    tokens = re.findall(r"[\w_][\w\d_]*", text.lower())
    if not tokens:
        # fallback: 数字和 ASCII 字母的备用匹配
        tokens = re.findall(r"[a-zA-Z]+|\d+", text.lower())
    return tokens


def _symbol_to_document(sym: Symbol) -> list[str]:
    parts = []
    name = sym.name
    for sub in re.findall(r"[\w_][\w\d_]*", name):
        parts.extend(re.findall(r"[\w]+|\d+", sub.lower()))
    if sym.signature:
        parts.extend(_tokenize(sym.signature))
    if sym.docstring:
        parts.extend(_tokenize(sym.docstring))
    if sym.return_type:
        parts.extend(_tokenize(sym.return_type))
    if sym.params:
        parts.extend(_tokenize(sym.params))
    if sym.kind:
        parts.append(sym.kind.lower())
    # Append chunk tokens for large symbols so BM25 can match line-range queries
    if _symbol_is_large(sym):
        end = max(sym.end_line, sym.line)
        for chunk_start in range(sym.line, end, 100):
            chunk_end = min(chunk_start + 100, end)
            chunk_id = _create_chunk_symbol_id(sym.id, chunk_start, chunk_end)
            parts.extend(_tokenize(chunk_id))
    return parts


class SymbolSearchIndex:
    """BM25 符号搜索索引，在 scan 后按需构建。"""

    def __init__(self, symbols: dict[str, Symbol]) -> None:
        self._symbol_ids: list[str] = []
        self._documents: list[list[str]] = []
        self._pageranks: list[float] = []
        self._bm25: Any | None = None
        self._built = False

        if not symbols:
            return

        for sym_id, sym in symbols.items():
            doc = _symbol_to_document(sym)
            if doc:
                self._symbol_ids.append(sym_id)
                self._documents.append(doc)
                self._pageranks.append(float(getattr(sym, "pagerank", 0.0)))

        if self._documents and _HAS_BM25:
            try:
                self._bm25 = BM25Okapi(self._documents)
                self._built = True
            except Exception as exc:
                logger.warning(f"BM25 index build failed: {exc}")

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """
        搜索与 query 最相关的符号。

        返回: [(symbol_id, score), ...] 按分数降序排列
        """
        if not self._built:
            return self._fallback_search(query, top_k)

        tokens = _tokenize(query)
        if not tokens:
            return []

        assert self._bm25 is not None  # _built=True 保证 _bm25 已初始化
        raw_scores = self._bm25.get_scores(tokens)
        # Issue #182: BM25 对短 query 产生离散分数（如 0.5/1.0 二元分布）。
        # 用 pagerank 作为 tiebreaker：final = bm25 * (1 + pagerank * 10)
        #   - bm25=0 时仍为 0（不相关符号被排除）
        #   - 同 bm25 分时 pagerank 高的领先，打破并列
        #   - 系数 10 让 PR 在 0.01~1.0 范围贡献 0.1~10 的相对调整
        results: list[tuple[str, float]] = []
        for idx, bm25_score in enumerate(raw_scores):
            if bm25_score <= 0:
                continue
            sid = self._symbol_ids[idx]
            pr = float(self._pageranks[idx]) if self._pageranks else 0.0
            final = bm25_score * (1.0 + pr * 10.0)
            results.append((sid, round(final, 4)))
        results.sort(key=lambda x: -x[1])
        return results[:top_k]

    def _fallback_search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        query_tokens = set(_tokenize(query))
        results: list[tuple[str, float]] = []
        for i, sym_id in enumerate(self._symbol_ids):
            doc_tokens = set(self._documents[i])
            overlap = len(query_tokens & doc_tokens)
            if overlap > 0:
                base = overlap / max(len(query_tokens), 1)
                pr = float(self._pageranks[i]) if self._pageranks else 0.0
                score = base * (1.0 + pr * 10.0)
                results.append((sym_id, round(score, 4)))
        results.sort(key=lambda x: -x[1])
        return results[:top_k]
