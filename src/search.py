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
from typing import Any

logger = logging.getLogger("repomap.search")

_HAS_BM25 = False
try:
    from rank_bm25 import BM25Okapi

    _HAS_BM25 = True
except ImportError:
    BM25Okapi = None


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|[a-zA-Z]+|\d+", text.lower())
    return tokens


def _symbol_to_document(sym: Any) -> list[str]:
    parts = []
    name = sym.name
    for sub in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
        parts.extend(re.findall(r"[a-zA-Z]+|\d+", sub.lower()))
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
    return parts


class SymbolSearchIndex:
    """BM25 符号搜索索引，在 scan 后按需构建。"""

    def __init__(self, symbols: dict[str, Any]) -> None:
        self._symbol_ids: list[str] = []
        self._documents: list[list[str]] = []
        self._bm25: Any | None = None
        self._built = False

        if not symbols:
            return

        for sym_id, sym in symbols.items():
            doc = _symbol_to_document(sym)
            if doc:
                self._symbol_ids.append(sym_id)
                self._documents.append(doc)

        if self._documents and _HAS_BM25:
            try:
                self._bm25 = BM25Okapi(self._documents)
                self._built = True
            except Exception as exc:
                logger.debug(f"BM25 index build failed: {exc}")

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

        scores = self._bm25.get_scores(tokens)
        ranked = sorted(
            enumerate(scores),
            key=lambda x: -x[1],
        )
        results = []
        for idx, score in ranked:
            if score > 0 and len(results) < top_k:
                results.append((self._symbol_ids[idx], float(score)))
        return results

    def _fallback_search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        query_lower = query.lower()
        query_tokens = set(_tokenize(query))
        results: list[tuple[str, float]] = []
        for i, sym_id in enumerate(self._symbol_ids):
            doc_tokens = set(self._documents[i])
            overlap = len(query_tokens & doc_tokens)
            if overlap > 0:
                score = overlap / max(len(query_tokens), 1)
                results.append((sym_id, score))
        results.sort(key=lambda x: -x[1])
        return results[:top_k]
