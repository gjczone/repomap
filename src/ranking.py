#!/usr/bin/env python3
"""
Repo Map Ranking — PageRank and Analysis Layer
================================================
负责符号排名、调用链查询、文件分析、AI 摘要生成。

提供：
- PageRank 重要性计算
- 调用链追踪（callers/callees）
- 热点文件识别
- 模块摘要和推荐阅读顺序
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections import deque
from pathlib import PurePosixPath
from typing import Any, TYPE_CHECKING

from . import (
    BOILERPLATE_NAMES,
    LOW_SIGNAL_KINDS,
    Edge,
    RepoGraph,
    Symbol,
    call_reference_parts,
    signal_weight_for_symbol,
)
from .topic import is_test_like_file

if TYPE_CHECKING:
    pass

logger = logging.getLogger("repomap")


class GraphAnalyzer:
    """
    图分析器：执行 PageRank 和各种图查询。
    """

    # 引用 __init__.py 中的统一定义，保持与 topic.py 同步
    LOW_SIGNAL_KINDS = LOW_SIGNAL_KINDS
    BOILERPLATE_NAMES = BOILERPLATE_NAMES

    def __init__(self, graph: RepoGraph) -> None:
        self.graph = graph
        self._file_analysis_cache: dict[str, dict[str, Any]] | None = None

    def calculate_pagerank(
        self, damping: float = 0.85, max_iter: int = 50, tol: float = 1e-6
    ) -> None:
        """带收敛检测的 PageRank。"""
        syms = list(self.graph.symbols)
        n = len(syms)
        if n == 0:
            return

        pr = {s: 1.0 / n for s in syms}
        # 预算 outgoing 权重和，过滤出权重>0的节点
        out_w: dict[str, float] = {
            s: sum(e.weight for e in self.graph.outgoing.get(s, [])) for s in syms
        }
        # 只保留有出边的节点，避免除零；过滤 NaN/Inf/负值权重
        active_srcs = {
            s
            for s, w in out_w.items()
            if w > 0 and not math.isnan(w) and not math.isinf(w)
        }
        # incoming: tgt -> [(src, weight)]，只包含有出边的源节点
        inc: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for src, edges in self.graph.outgoing.items():
            if src in active_srcs:
                for e in edges:
                    inc[e.target].append((src, e.weight))

        base = (1 - damping) / n
        for _ in range(max_iter):
            new_pr: dict[str, float] = {}
            for s in syms:
                score = base + sum(
                    damping * pr[src] * w / out_w[src] for src, w in inc[s]
                )
                new_pr[s] = score
            total = sum(new_pr.values()) or 1.0
            for s in syms:
                new_pr[s] /= total
            # 收敛检测
            delta = max(abs(new_pr[s] - pr[s]) for s in syms)
            pr = new_pr
            if delta < tol:
                break

        for s, score in pr.items():
            self.graph.symbols[s].pagerank = score

        # PageRank 更新后清除文件分析缓存，避免返回过期数据
        self._file_analysis_cache = None

    def query_symbol(self, name: str, query_context: str | None = None) -> list[Symbol]:
        """按名称模糊查找符号，按 PageRank 降序返回。

        query_context: 可选的关键词上下文，用于为匹配到的符号做局部 boost。
        当提供时，符号所在文件名命中关键词的会获得额外排序加分。
        """
        nl = name.lower()
        candidates = [s for s in self.graph.symbols.values() if nl in s.name.lower()]
        if not query_context or len(candidates) <= 1:
            return sorted(candidates, key=lambda s: s.pagerank, reverse=True)

        # 局部 boost：文件名或路径命中 query_context 关键词的符号获得加分
        context_keywords = query_context.lower().split()
        return sorted(
            candidates,
            key=lambda s: (
                s.pagerank
                + sum(0.05 for kw in context_keywords if kw in s.file.lower())
            ),
            reverse=True,
        )

    def call_chain(
        self, symbol_id: str, direction: str = "both", max_depth: int = 3
    ) -> dict[str, list[Symbol]]:
        """
        返回指定符号的调用链。
        direction: "callers" | "callees" | "both"
        """
        result: dict[str, list[Symbol]] = {"callers": [], "callees": []}
        if direction in ("callers", "both"):
            result["callers"] = self._bfs(symbol_id, "incoming", max_depth, {"call"})
        if direction in ("callees", "both"):
            result["callees"] = self._bfs(symbol_id, "outgoing", max_depth, {"call"})
        return result

    def _bfs(
        self,
        start: str,
        direction: str,
        max_depth: int,
        allowed_kinds: set[str] | None = None,
    ) -> list[Symbol]:
        """用 deque 实现 BFS，O(n) 复杂度。"""
        visited = {start}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        result: list[Symbol] = []
        edges_map = (
            self.graph.incoming if direction == "incoming" else self.graph.outgoing
        )

        # 防止内存溢出：队列大小限制
        MAX_QUEUE_SIZE = 10000
        MAX_RESULTS = 1000

        while queue:
            # 队列大小安全检查
            if len(queue) > MAX_QUEUE_SIZE:
                logger.warning(
                    f"BFS queue size exceeded limit ({MAX_QUEUE_SIZE}), truncating search"
                )
                break

            cur, depth = queue.popleft()
            if cur != start:
                sym = self.graph.symbols.get(cur)
                if sym:
                    result.append(sym)
                    # 结果数量限制，防止内存溢出
                    if len(result) >= MAX_RESULTS:
                        logger.debug(
                            f"BFS reached max results ({MAX_RESULTS}), truncating"
                        )
                        break
            if depth < max_depth:
                for e in edges_map.get(cur, []):
                    if allowed_kinds is not None and e.kind not in allowed_kinds:
                        continue
                    nxt = e.source if direction == "incoming" else e.target
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append((nxt, depth + 1))
        return result

    def _edge_count(
        self,
        symbol_id: str,
        direction: str,
        allowed_kinds: set[str] | None = None,
    ) -> int:
        edges_map = (
            self.graph.incoming if direction == "incoming" else self.graph.outgoing
        )
        return sum(
            1
            for edge in edges_map.get(symbol_id, [])
            if allowed_kinds is None or edge.kind in allowed_kinds
        )

    def _signal_weight(self, symbol: Symbol) -> float:
        # 委托给 __init__.py 中的统一实现，与 topic.py 保持一致
        return signal_weight_for_symbol(symbol.kind, symbol.name, symbol.visibility)

    def _summary_symbol_score(self, symbol: Symbol) -> float:
        incoming_calls = self._edge_count(symbol.id, "incoming", {"call"})
        outgoing_calls = self._edge_count(symbol.id, "outgoing", {"call"})
        incoming_imports = self._edge_count(symbol.id, "incoming", {"import"})
        visibility_bonus = (
            1.2
            if symbol.visibility == "exported"
            else 0.45
            if symbol.visibility == "public"
            else 0.0
        )
        kind_bonus = (
            1.0
            if symbol.kind == "class"
            else 0.55
            if symbol.kind in {"function", "method"}
            else 0.2
        )
        import_bonus = min(incoming_imports, 4) * 0.15
        centrality_bonus = symbol.pagerank * 40
        return (
            incoming_calls * 4.0
            + outgoing_calls * 1.5
            + import_bonus
            + visibility_bonus
            + kind_bonus
            + centrality_bonus
        ) * self._signal_weight(symbol)

    def hotspots(self, limit: int = 15) -> list[dict]:
        """识别高密度文件，优先看高语义密度而不是标签/配置噪音。"""
        analysis = self.file_analysis()
        counts = sorted(
            analysis.values(),
            key=lambda row: (
                row["is_test_file"],
                -row["semantic_symbol_count"],
                -row["score"],
                row["file"],
            ),
        )
        return [
            {
                "file": row["file"],
                "symbol_count": row["symbol_count"],
                "semantic_symbol_count": round(row["semantic_symbol_count"], 1),
                "risk": (
                    "high"
                    if row["semantic_symbol_count"] >= 12
                    else "medium"
                    if row["semantic_symbol_count"] >= 4
                    else "low"
                ),
            }
            for row in counts[:limit]
        ]

    def entry_points(self) -> list[str]:
        """识别常见的入口文件。支持子目录路径匹配。"""
        candidates = [
            "main.py",
            "app.py",
            "manage.py",
            "run.py",
            "server.py",
            "main.go",
            "cmd/main.go",
            "src/main.rs",
            "src/lib.rs",
            "src/main.ts",
            "src/index.ts",
            "src/main.tsx",
            "src/index.tsx",
            "src/main.js",
            "src/index.js",
            "index.ts",
            "index.js",
            # 支持 monorepo 子目录结构
            "*/src/main.tsx",
            "*/src/main.ts",
            "*/src/index.tsx",
            "*/src/index.ts",
            "*/src/main.js",
            "*/src/index.js",
            "*/main.rs",
            "*/lib.rs",
        ]
        result = []
        for c in candidates:
            if "*" in c:
                # 通配符匹配
                pattern = c.replace("*/", "")
                for f in self.graph.file_symbols:
                    if f.endswith(pattern):
                        result.append(f)
            elif c in self.graph.file_symbols:
                result.append(c)
        return sorted(set(result))

    def file_analysis(self) -> dict[str, dict[str, Any]]:
        """分析每个文件的复杂度和连接性。"""
        if self._file_analysis_cache is not None:
            return self._file_analysis_cache
        analysis: dict[str, dict[str, Any]] = {}

        # 初始化文件分析数据
        for file_path, symbol_ids in self.graph.file_symbols.items():
            symbols = [
                self.graph.symbols[symbol_id]
                for symbol_id in symbol_ids
                if symbol_id in self.graph.symbols
            ]
            ranked_symbols = sorted(
                symbols,
                key=lambda item: (
                    -self._summary_symbol_score(item),
                    item.line,
                    item.name,
                ),
            )
            semantic_symbol_count = sum(
                self._signal_weight(symbol) for symbol in symbols
            )
            semantic_pagerank_sum = sum(
                symbol.pagerank * self._signal_weight(symbol) for symbol in symbols
            )
            weighted_exported_count = sum(
                self._signal_weight(symbol)
                for symbol in symbols
                if symbol.visibility == "exported"
            )
            weighted_public_count = sum(
                self._signal_weight(symbol)
                for symbol in symbols
                if symbol.visibility == "public"
            )
            analysis[file_path] = {
                "file": file_path,
                "symbol_count": len(symbols),
                "semantic_symbol_count": semantic_symbol_count,
                "pagerank_sum": sum(symbol.pagerank for symbol in symbols),
                "semantic_pagerank_sum": semantic_pagerank_sum,
                "implementation_score": sum(
                    self._summary_symbol_score(symbol) for symbol in ranked_symbols[:5]
                ),
                "exported_count": weighted_exported_count,
                "public_count": weighted_public_count,
                "is_test_file": self._is_test_like_file(file_path),
                "call_edges": 0,
                "cross_file_call_edges": 0,
                "import_edges": 0,
                "neighbor_files": set(),
                "top_symbols": [symbol.name for symbol in ranked_symbols[:3]],
            }

        # 统计边关系
        for source_id, edge_list in self.graph.outgoing.items():
            source_symbol = self.graph.symbols.get(source_id)
            if not source_symbol:
                continue
            source_file = source_symbol.file
            source_entry = analysis.setdefault(
                source_file,
                {
                    "file": source_file,
                    "symbol_count": 0,
                    "semantic_symbol_count": 0.0,
                    "pagerank_sum": 0.0,
                    "semantic_pagerank_sum": 0.0,
                    "implementation_score": 0.0,
                    "exported_count": 0,
                    "public_count": 0,
                    "is_test_file": self._is_test_like_file(source_file),
                    "call_edges": 0,
                    "cross_file_call_edges": 0,
                    "import_edges": 0,
                    "neighbor_files": set(),
                    "top_symbols": [],
                },
            )
            for edge in edge_list:
                target_symbol = self.graph.symbols.get(edge.target)
                if not target_symbol:
                    continue
                target_file = target_symbol.file
                if edge.kind == "call":
                    source_entry["call_edges"] += 1
                if source_file != target_file:
                    source_entry["neighbor_files"].add(target_file)
                    analysis.setdefault(
                        target_file,
                        {
                            "file": target_file,
                            "symbol_count": 0,
                            "semantic_symbol_count": 0.0,
                            "pagerank_sum": 0.0,
                            "semantic_pagerank_sum": 0.0,
                            "implementation_score": 0.0,
                            "exported_count": 0,
                            "public_count": 0,
                            "is_test_file": self._is_test_like_file(target_file),
                            "call_edges": 0,
                            "cross_file_call_edges": 0,
                            "import_edges": 0,
                            "neighbor_files": set(),
                            "top_symbols": [],
                        },
                    )["neighbor_files"].add(source_file)
                    if edge.kind == "call":
                        source_entry["cross_file_call_edges"] += 1
                    if edge.kind == "import":
                        source_entry["import_edges"] += 1

        # 计算综合得分
        for data in analysis.values():
            neighbor_count = len(data["neighbor_files"])
            data["neighbor_count"] = neighbor_count
            data["score"] = (
                data["implementation_score"]
                + data["exported_count"] * 0.8
                + data["public_count"] * 0.25
                + data["semantic_symbol_count"] * 0.6
                + neighbor_count * 0.45
                + data["cross_file_call_edges"] * 0.25
                + data["call_edges"] * 0.05
            )
            if data["is_test_file"]:
                data["score"] *= 0.55

        self._file_analysis_cache = analysis
        return analysis

    def module_summary(self, limit: int = 8) -> list[dict[str, Any]]:
        """生成模块级别的摘要。"""
        modules: dict[str, list[dict[str, Any]]] = defaultdict(list)
        analysis = self.file_analysis()
        for file_path, file_data in analysis.items():
            modules[self._module_bucket_for_file(file_path)].append(file_data)

        rows: list[dict[str, Any]] = []
        for module_name, file_rows in modules.items():
            ordered_files = sorted(
                file_rows, key=lambda row: (-row["score"], row["file"])
            )
            representative = ordered_files[0] if ordered_files else None
            rows.append(
                {
                    "module": module_name,
                    "file_count": len(file_rows),
                    "symbol_count": sum(row["symbol_count"] for row in file_rows),
                    "semantic_symbol_count": round(
                        sum(row["semantic_symbol_count"] for row in file_rows), 1
                    ),
                    "pagerank_sum": sum(row["pagerank_sum"] for row in file_rows),
                    "semantic_pagerank_sum": sum(
                        row["semantic_pagerank_sum"] for row in file_rows
                    ),
                    "representative_file": representative["file"]
                    if representative
                    else "",
                    "highlights": representative["top_symbols"][:3]
                    if representative
                    else [],
                }
            )
        rows.sort(
            key=lambda row: (
                -row["semantic_pagerank_sum"],
                -row["semantic_symbol_count"],
                row["module"],
            )
        )
        return rows[:limit]

    def suggested_reading_order(self, limit: int = 8) -> list[dict[str, Any]]:
        """为 AI 生成推荐阅读顺序。"""
        analysis = self.file_analysis()
        suggestions: list[dict[str, Any]] = []
        seen_files: set[str] = set()

        # 首先推荐入口点
        for entry in self.entry_points():
            if entry not in analysis or entry in seen_files:
                continue
            file_data = analysis[entry]
            suggestions.append(
                {
                    "file": entry,
                    "reason": "Entry point, good starting path for understanding",
                    "top_symbols": file_data["top_symbols"][:3],
                    "symbol_count": file_data["symbol_count"],
                    "semantic_symbol_count": round(
                        file_data["semantic_symbol_count"], 1
                    ),
                }
            )
            seen_files.add(entry)
            if len(suggestions) >= limit:
                return suggestions

        # 然后按重要性排序推荐其他文件
        ordered_files = sorted(
            analysis.values(),
            key=lambda row: (row["is_test_file"], -row["score"], row["file"]),
        )
        for file_data in ordered_files:
            file_path = file_data["file"]
            if file_path in seen_files:
                continue
            if file_data["symbol_count"] <= 0:
                continue
            reason_parts: list[str] = []
            if file_data["neighbor_count"] >= 3:
                reason_parts.append("cross-module hub")
            if file_data["exported_count"] >= 2:
                reason_parts.append("large export surface")
            if file_data["semantic_symbol_count"] >= 5:
                reason_parts.append("dense logic")
            if file_data["is_test_file"]:
                reason_parts.append("test verification entry")
            if not reason_parts:
                reason_parts.append("key symbols concentrated")
            suggestions.append(
                {
                    "file": file_path,
                    "reason": ", ".join(reason_parts),
                    "top_symbols": file_data["top_symbols"][:3],
                    "symbol_count": file_data["symbol_count"],
                    "semantic_symbol_count": round(
                        file_data["semantic_symbol_count"], 1
                    ),
                }
            )
            seen_files.add(file_path)
            if len(suggestions) >= limit:
                break
        return suggestions

    def summary_symbols(
        self,
        limit_files: int = 6,
        per_file: int = 4,
        include_tests: bool = False,
    ) -> list[dict[str, Any]]:
        """给 overview 提供更适合阅读的关键实现符号。"""
        analysis = self.file_analysis()
        suggestion_rows = self.suggested_reading_order(
            max(limit_files * 2, limit_files)
        )
        reasons = {row["file"]: row["reason"] for row in suggestion_rows}
        ordered_files = [row["file"] for row in suggestion_rows]
        ordered_files.extend(
            row["file"]
            for row in sorted(
                analysis.values(),
                key=lambda row: (row["is_test_file"], -row["score"], row["file"]),
            )
            if row["file"] not in reasons
        )

        sections: list[dict[str, Any]] = []
        for file_path in ordered_files:
            file_data = analysis.get(file_path)
            if not file_data:
                continue
            if file_data["is_test_file"] and not include_tests:
                continue
            symbols = [
                self.graph.symbols[symbol_id]
                for symbol_id in self.graph.file_symbols.get(file_path, [])
                if symbol_id in self.graph.symbols
            ]
            ranked_symbols = sorted(
                symbols,
                key=lambda item: (
                    -self._summary_symbol_score(item),
                    item.line,
                    item.name,
                ),
            )
            if not ranked_symbols:
                continue
            sections.append(
                {
                    "file": file_path,
                    "reason": reasons.get(file_path, ""),
                    "symbol_count": file_data["symbol_count"],
                    "semantic_symbol_count": round(
                        file_data["semantic_symbol_count"], 1
                    ),
                    "symbols": [
                        {
                            "name": symbol.name,
                            "kind": symbol.kind,
                            "line": symbol.line,
                            "visibility": symbol.visibility,
                            "signature": symbol.signature,
                            "pagerank": symbol.pagerank,
                            "summary_score": round(
                                self._summary_symbol_score(symbol), 2
                            ),
                            "incoming_calls": self._edge_count(
                                symbol.id, "incoming", {"call"}
                            ),
                            "outgoing_calls": self._edge_count(
                                symbol.id, "outgoing", {"call"}
                            ),
                        }
                        for symbol in ranked_symbols[:per_file]
                    ],
                }
            )
            if len(sections) >= limit_files:
                break
        return sections

    @staticmethod
    def _module_bucket_for_file(file_path: str) -> str:
        """将文件路径归类到模块。"""
        parts = [
            part for part in PurePosixPath(file_path).parts if part not in ("", ".")
        ]
        if not parts:
            return "(root)"
        if len(parts) == 1:
            return "(root)"
        if parts[0] in {
            "src",
            "app",
            "apps",
            "packages",
            "services",
            "modules",
            "libs",
            "lib",
            "crates",
        }:
            return "/".join(parts[:2]) if len(parts) > 1 else parts[0]
        return parts[0]

    @staticmethod
    def _is_test_like_file(file_path: str) -> bool:
        """判断是否为测试文件。"""
        return is_test_like_file(file_path)


class EdgeBuilder:
    """
    边构建器：负责从符号和调用信息构建依赖图边。
    """

    IMPORT_WEIGHT = 0.35
    CALL_WEIGHT = 0.50

    # 符号可见性排序权重（用于选每个文件最具代表性的符号建边）
    _VISIBILITY_RANK = {"exported": 3, "public": 2, "private": 1}
    _KIND_RANK = {
        "class": 4,
        "function": 3,
        "method": 3,
        "anonymous_function": 2,
        "struct": 4,
        "interface": 4,
        "trait": 4,
        "enum": 4,
        "module": 3,
    }

    def __init__(self, graph: RepoGraph, resolver: Any) -> None:
        self.graph = graph
        self.resolver = resolver
        self._edge_set: set[tuple[str, str, str]] = set()

    def _top_symbol_ids(self, file: str, max_count: int = 3) -> list[str]:
        """按语义重要性选文件中最具代表性的符号 ID。"""
        ids = self.graph.file_symbols.get(file, [])
        if len(ids) <= max_count:
            return list(ids)
        scored = []
        for sid in ids:
            sym = self.graph.symbols.get(sid)
            if sym is None:
                scored.append((0, sid))
                continue
            vis = self._VISIBILITY_RANK.get(sym.visibility, 0)
            kind = self._KIND_RANK.get(sym.kind, 1)
            scored.append((vis + kind, sid))
        scored.sort(key=lambda x: -x[0])
        return [sid for _, sid in scored[:max_count]]

    def build_edges(self) -> None:
        """构建 import 边和 call 边。"""
        self.resolver.build_indices()

        import_targets_by_file: dict[str, set[str]] = defaultdict(set)
        import_symbol_targets_by_file: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )

        # import 边
        for file, imports in sorted(self.graph.file_imports.items()):
            src_ids = self._top_symbol_ids(file)
            for imp in imports:
                target_files = self.resolver.resolve_import_targets(file, imp)
                if not target_files:
                    continue
                import_targets_by_file[file].update(target_files)
                for target_file in target_files:
                    tgt_ids = self._top_symbol_ids(target_file)
                    for s in src_ids:
                        for t in tgt_ids:
                            self._add_edge(s, t, self.IMPORT_WEIGHT, "import")

            for binding in self.graph.file_import_bindings.get(file, []):
                target_ids = self.resolver.resolve_import_binding_targets(file, binding)
                if not target_ids:
                    continue
                import_symbol_targets_by_file[file][binding.local_name].update(
                    target_ids
                )
                for target_id in sorted(target_ids):
                    import_targets_by_file[file].add(self.graph.symbols[target_id].file)
                    for source_id in src_ids:
                        self._add_edge(
                            source_id, target_id, self.IMPORT_WEIGHT, "import"
                        )

        # call 边
        for file, calls in sorted(self.graph.file_calls.items()):
            for call_ref in calls:
                call_name, call_line, call_kind = call_reference_parts(call_ref)
                caller_id = self.resolver.resolve_calling_symbol(file, call_line)
                if not caller_id:
                    continue
                target_id = self.resolver.resolve_call_target(
                    file=file,
                    call_name=call_name,
                    call_line=call_line,
                    call_kind=call_kind,
                    import_targets_by_file=import_targets_by_file,
                    import_symbol_targets_by_file=import_symbol_targets_by_file,
                )
                if target_id:
                    self._add_edge(caller_id, target_id, self.CALL_WEIGHT, "call")

    def _add_edge(self, src: str, tgt: str, weight: float, kind: str) -> None:
        if src == tgt:
            return
        key = (src, tgt, kind)
        if key in self._edge_set:
            return
        self._edge_set.add(key)
        e = Edge(src, tgt, weight, kind)
        self.graph.outgoing[src].append(e)
        self.graph.incoming[tgt].append(e)


# ═══════════════════════════════════════════════════════════════════════════════
# Community detection via Label Propagation
# ═══════════════════════════════════════════════════════════════════════════════


def detect_file_clusters(
    graph: "RepoGraph", max_iterations: int = 20
) -> dict[str, int]:
    """Detect module clusters from the file import/call graph using label propagation.

    Returns dict mapping file_path -> cluster_id.
    Clusters are numbered 0..N-1 by size (largest first).
    """
    import random

    files = sorted(graph.file_symbols.keys())
    if len(files) < 3:
        return {f: 0 for f in files}

    # Build undirected adjacency with edge weights
    neighbors: dict[str, dict[str, float]] = {f: {} for f in files}
    for f in files:
        # Import edges
        for imported in graph.file_imports.get(f, []):
            if imported in neighbors:
                neighbors[f][imported] = neighbors[f].get(imported, 0) + 1.0
                neighbors[imported][f] = neighbors[imported].get(f, 0) + 1.0
        # Call edges
        for called, *_ in graph.file_calls.get(f, []):
            if called in neighbors:
                neighbors[f][called] = neighbors[f].get(called, 0) + 0.5
                neighbors[called][f] = neighbors[called].get(f, 0) + 0.5

    # Initialize labels by directory structure for meaningful cluster seeds
    from pathlib import PurePosixPath

    dir_labels: dict[str, int] = {}
    next_dir_id = 0
    labels: dict[str, int] = {}
    for f in files:
        # Use top-2 directory levels as initial label
        # Normalize ./file.txt to file.txt for correct classification
        normalized = f.lstrip("./")
        parts = PurePosixPath(normalized).parts
        if len(parts) >= 2:
            d = f"{parts[0]}/{parts[1]}"
        elif len(parts) == 1 and parts[0] != f:
            d = parts[0]
        else:
            d = "__root__"
        if d not in dir_labels:
            dir_labels[d] = next_dir_id
            next_dir_id += 1
        labels[f] = dir_labels[d]

    # Label propagation
    rng = random.Random(42)  # 固定 seed 保证聚类结果确定性
    for _ in range(max_iterations):
        changed = 0
        # Randomize order each iteration for stability
        order = list(files)
        rng.shuffle(order)
        for f in order:
            if not neighbors[f]:
                continue
            # Count neighbor labels weighted by edge weight
            label_counts: dict[int, float] = {}
            for nb, weight in neighbors[f].items():
                lbl = labels.get(nb)
                if lbl is not None:
                    label_counts[lbl] = label_counts.get(lbl, 0) + weight
            if not label_counts:
                continue
            # Most common label; break ties with smallest label ID for determinism
            new_label = max(label_counts.items(), key=lambda item: (item[1], -item[0]))[
                0
            ]
            if labels[f] != new_label:
                labels[f] = new_label
                changed += 1
        if changed == 0:
            break

    # Remap cluster IDs by size (largest first)
    cluster_sizes: dict[int, int] = {}
    for lbl in labels.values():
        cluster_sizes[lbl] = cluster_sizes.get(lbl, 0) + 1
    sorted_clusters = sorted(cluster_sizes.items(), key=lambda x: -x[1])
    remap = {old: new for new, (old, _) in enumerate(sorted_clusters)}
    return {f: remap[lbl] for f, lbl in labels.items()}


def format_cluster_summary(clusters: dict[str, int], top_n: int = 8) -> list[dict]:
    """Format cluster detection results for overview rendering.

    Returns list of dicts with keys: cluster_id, size, files (top representatives),
    top_directory, label (heuristic name).
    """
    from collections import defaultdict
    from pathlib import PurePosixPath

    grouped: dict[int, list[str]] = defaultdict(list)
    for f, cid in clusters.items():
        grouped[cid].append(f)

    results = []
    for cid in sorted(grouped, key=lambda c: -len(grouped[c]))[:top_n]:
        members = grouped[cid]
        # Find common directory prefix
        dirs = [str(PurePosixPath(f).parent) for f in members]
        top_dir = max(set(dirs), key=dirs.count) if dirs else ""
        # Top representative files (non-test, high symbol count)
        reps = [f for f in members if "test" not in f.lower()][:5]
        if not reps:
            reps = members[:5]

        # Heuristic label from common directory
        label = top_dir.split("/")[-1] if "/" in top_dir else top_dir

        results.append(
            {
                "cluster_id": cid,
                "size": len(members),
                "label": label,
                "top_dir": top_dir,
                "representatives": reps,
            }
        )
    return results
