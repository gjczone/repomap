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
from collections import defaultdict
from collections import deque
from pathlib import PurePosixPath
from typing import Any, TYPE_CHECKING

from repomap_support import Edge, RepoGraph, Symbol

if TYPE_CHECKING:
    from repomap_core import RepoMapEngine

logger = logging.getLogger("repomap")


class GraphAnalyzer:
    """
    图分析器：执行 PageRank 和各种图查询。
    """

    def __init__(self, graph: RepoGraph) -> None:
        self.graph = graph

    def calculate_pagerank(self, damping: float = 0.85, max_iter: int = 50,
                          tol: float = 1e-6) -> None:
        """带收敛检测的 PageRank。"""
        syms = list(self.graph.symbols)
        n = len(syms)
        if n == 0:
            return

        pr = {s: 1.0 / n for s in syms}
        # 预算 outgoing 权重和，过滤出权重>0的节点
        out_w: dict[str, float] = {
            s: sum(e.weight for e in self.graph.outgoing.get(s, []))
            for s in syms
        }
        # 只保留有出边的节点，避免除零
        active_srcs = {s for s, w in out_w.items() if w > 0}
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
                    damping * pr[src] * w / out_w[src]
                    for src, w in inc[s]
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

    def query_symbol(self, name: str) -> list[Symbol]:
        """按名称模糊查找符号，按 PageRank 降序返回。"""
        nl = name.lower()
        return sorted(
            [s for s in self.graph.symbols.values() if nl in s.name.lower()],
            key=lambda s: s.pagerank, reverse=True,
        )

    def call_chain(self, symbol_id: str, direction: str = "both",
                   max_depth: int = 3) -> dict[str, list[Symbol]]:
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
        edges_map = self.graph.incoming if direction == "incoming" else self.graph.outgoing
        
        # 防止内存溢出：队列大小限制
        MAX_QUEUE_SIZE = 10000
        MAX_RESULTS = 1000

        while queue:
            # 队列大小安全检查
            if len(queue) > MAX_QUEUE_SIZE:
                logger.warning(f"BFS queue size exceeded limit ({MAX_QUEUE_SIZE}), truncating search")
                break
            
            cur, depth = queue.popleft()
            if cur != start:
                sym = self.graph.symbols.get(cur)
                if sym:
                    result.append(sym)
                    # 结果数量限制，防止内存溢出
                    if len(result) >= MAX_RESULTS:
                        logger.debug(f"BFS reached max results ({MAX_RESULTS}), truncating")
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

    def hotspots(self, limit: int = 15) -> list[dict]:
        """识别高密度文件（符号数多）。"""
        counts = sorted(
            ((f, len(s)) for f, s in self.graph.file_symbols.items()),
            key=lambda x: x[1], reverse=True,
        )
        return [
            {"file": f, "symbol_count": c,
             "risk": "high" if c >= 20 else "medium" if c >= 10 else "low"}
            for f, c in counts[:limit]
        ]

    def entry_points(self) -> list[str]:
        """识别常见的入口文件。支持子目录路径匹配。"""
        candidates = [
            "main.py", "app.py", "manage.py", "run.py", "server.py",
            "main.go", "cmd/main.go",
            "src/main.rs", "src/lib.rs",
            "src/main.ts", "src/index.ts", "src/main.tsx", "src/index.tsx",
            "src/main.js", "src/index.js",
            "index.ts", "index.js",
            # 支持 monorepo 子目录结构
            "*/src/main.tsx", "*/src/main.ts", "*/src/index.tsx", "*/src/index.ts",
            "*/src/main.js", "*/src/index.js",
            "*/main.rs", "*/lib.rs",
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
        analysis: dict[str, dict[str, Any]] = {}

        # 初始化文件分析数据
        for file_path, symbol_ids in self.graph.file_symbols.items():
            symbols = [
                self.graph.symbols[symbol_id]
                for symbol_id in symbol_ids
                if symbol_id in self.graph.symbols
            ]
            analysis[file_path] = {
                "file": file_path,
                "symbol_count": len(symbols),
                "pagerank_sum": sum(symbol.pagerank for symbol in symbols),
                "exported_count": sum(1 for symbol in symbols if symbol.visibility == "exported"),
                "public_count": sum(1 for symbol in symbols if symbol.visibility == "public"),
                "is_test_file": self._is_test_like_file(file_path),
                "call_edges": 0,
                "cross_file_call_edges": 0,
                "import_edges": 0,
                "neighbor_files": set(),
                "top_symbols": [
                    symbol.name
                    for symbol in sorted(
                        symbols,
                        key=lambda item: (-item.pagerank, item.line, item.name),
                    )[:3]
                ],
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
                    "pagerank_sum": 0.0,
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
                            "pagerank_sum": 0.0,
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
                data["pagerank_sum"] * 1000
                + data["exported_count"] * 0.8
                + data["public_count"] * 0.3
                + data["symbol_count"] * 0.25
                + neighbor_count * 0.45
                + data["cross_file_call_edges"] * 0.15
            )
            if data["is_test_file"]:
                data["score"] *= 0.55

        return analysis

    def module_summary(self, limit: int = 8) -> list[dict[str, Any]]:
        """生成模块级别的摘要。"""
        modules: dict[str, list[dict[str, Any]]] = defaultdict(list)
        analysis = self.file_analysis()
        for file_path, file_data in analysis.items():
            modules[self._module_bucket_for_file(file_path)].append(file_data)

        rows: list[dict[str, Any]] = []
        for module_name, file_rows in modules.items():
            ordered_files = sorted(file_rows, key=lambda row: (-row["score"], row["file"]))
            representative = ordered_files[0] if ordered_files else None
            rows.append(
                {
                    "module": module_name,
                    "file_count": len(file_rows),
                    "symbol_count": sum(row["symbol_count"] for row in file_rows),
                    "pagerank_sum": sum(row["pagerank_sum"] for row in file_rows),
                    "representative_file": representative["file"] if representative else "",
                    "highlights": representative["top_symbols"][:3] if representative else [],
                }
            )
        rows.sort(key=lambda row: (-row["pagerank_sum"], -row["symbol_count"], row["module"]))
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
            if analysis[entry]["symbol_count"] <= 0:
                continue
            file_data = analysis[entry]
            suggestions.append(
                {
                    "file": entry,
                    "reason": "入口点，适合先建立运行路径",
                    "top_symbols": file_data["top_symbols"][:3],
                    "symbol_count": file_data["symbol_count"],
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
                reason_parts.append("跨模块枢纽")
            if file_data["exported_count"] >= 2:
                reason_parts.append("导出面大")
            if file_data["symbol_count"] >= 8:
                reason_parts.append("逻辑密集")
            if file_data["is_test_file"]:
                reason_parts.append("测试验证入口")
            if not reason_parts:
                reason_parts.append("重要符号集中")
            suggestions.append(
                {
                    "file": file_path,
                    "reason": "，".join(reason_parts),
                    "top_symbols": file_data["top_symbols"][:3],
                    "symbol_count": file_data["symbol_count"],
                }
            )
            seen_files.add(file_path)
            if len(suggestions) >= limit:
                break
        return suggestions

    @staticmethod
    def _module_bucket_for_file(file_path: str) -> str:
        """将文件路径归类到模块。"""
        parts = [part for part in PurePosixPath(file_path).parts if part not in ("", ".")]
        if not parts:
            return "(root)"
        if len(parts) == 1:
            return "(root)"
        if parts[0] in {"src", "app", "apps", "packages", "services", "modules", "libs", "lib"}:
            return "/".join(parts[:2]) if len(parts) > 1 else parts[0]
        return parts[0]

    @staticmethod
    def _is_test_like_file(file_path: str) -> bool:
        """判断是否为测试文件。"""
        path = PurePosixPath(file_path)
        name = path.name.lower()
        if any(part.lower() in {"test", "tests", "__tests__"} for part in path.parts):
            return True
        return name.startswith("test_") or name.endswith("_test.py") or name.endswith(".spec.ts")


class EdgeBuilder:
    """
    边构建器：负责从符号和调用信息构建依赖图边。
    """

    IMPORT_WEIGHT = 0.35
    CALL_WEIGHT = 0.50

    def __init__(self, graph: RepoGraph, resolver: Any) -> None:
        self.graph = graph
        self.resolver = resolver
        self._edge_set: set[tuple[str, str, str]] = set()

    def build_edges(self) -> None:
        """构建 import 边和 call 边。"""
        self.resolver.build_indices()

        import_targets_by_file: dict[str, set[str]] = defaultdict(set)
        import_symbol_targets_by_file: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

        # import 边
        for file, imports in sorted(self.graph.file_imports.items()):
            src_ids = self.graph.file_symbols.get(file, [])[:3]
            for imp in imports:
                target_files = self.resolver.resolve_import_targets(file, imp)
                if not target_files:
                    continue
                import_targets_by_file[file].update(target_files)
                for target_file in target_files:
                    tgt_ids = self.graph.file_symbols.get(target_file, [])[:3]
                    for s in src_ids:
                        for t in tgt_ids:
                            self._add_edge(s, t, self.IMPORT_WEIGHT, "import")

            for binding in self.graph.file_import_bindings.get(file, []):
                target_ids = self.resolver.resolve_import_binding_targets(file, binding)
                if not target_ids:
                    continue
                import_symbol_targets_by_file[file][binding.local_name].update(target_ids)
                for target_id in sorted(target_ids):
                    import_targets_by_file[file].add(self.graph.symbols[target_id].file)
                    for source_id in src_ids:
                        self._add_edge(source_id, target_id, self.IMPORT_WEIGHT, "import")

        # call 边
        for file, calls in sorted(self.graph.file_calls.items()):
            for call_name, call_line in calls:
                caller_id = self.resolver.resolve_calling_symbol(file, call_line)
                if not caller_id:
                    continue
                target_id = self.resolver.resolve_call_target(
                    file=file,
                    call_name=call_name,
                    call_line=call_line,
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
