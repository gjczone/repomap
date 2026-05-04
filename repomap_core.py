#!/usr/bin/env python3
"""
Repo Map Core — Tree-sitter Analysis Engine (Coordinator Layer)
================================================================
给 RepoMap CLI 提供扫描、解析、图构建和 AI overview 能力。

目标：AI 在逐文件阅读代码之前，先通过这个工具建立
  "项目地图"——了解业务模块划分、核心函数调用关系、
  高密度文件分布、入口点等，从而更高效地定位和理解代码。

安装 & 运行（CLI 模式）：
    uv run python -m repomap_cli overview --project /path/to/your/project

本地调试（直接打印 repo map）：
    python -m repomap_cli overview --project /path/to/your/project
    python -m repomap_cli call-chain --project /path/to/your/project --symbol MyClassName
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from repomap_ai import (
    render_call_chain_report,
    render_file_detail_report,
    render_overview_report,
)
from repomap_parser import EXT_TO_LANG, TreeSitterAdapter
from repomap_ranking import EdgeBuilder, GraphAnalyzer
from repomap_resolver import ImportResolver
from repomap_support import (
    RepoGraph,
    ScanStats,
    compare_graph_snapshots,
    edge_identity_from_edge,
    get_cache_paths,
    serialize_edge,
    serialize_symbol,
)

# ── 日志：统一写 stderr，绝不污染 CLI stdout ────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("repomap")

DEFAULT_MAX_FILE_BYTES = 512 * 1024

SKIP_DIR_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".next",
    ".nox",
    ".nuxt",
    ".parcel-cache",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".tox",
    ".turbo",
    ".venv",
    ".vscode",
    ".yarn",
    "__pypackages__",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "ENV",
    "node_modules",
    "site-packages",
    "target",
    "venv",
    # 第三方库目录
    "monaco-editor",
    "monaco",
    "vendor",
    "third_party",
    "third-party",
    "libs",
    "external",
}

SKIP_FILE_NAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "bun.lock",
    "bun.lockb",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 核心引擎（协调层）
# ═══════════════════════════════════════════════════════════════════════════════


class RepoMapEngine:
    """
    项目地图引擎：扫描代码库 → 构建符号依赖图 → PageRank → 输出 AI 友好摘要。

    给 AI 提供的"项目地图"信息包括：
      1. 模块/文件分布（哪些文件密度高，可能是核心业务）
      2. 入口点（main/app/index 等）
      3. 重要符号（PageRank 高 = 被很多地方调用/导入）
      4. 调用链（某函数被谁调、调了谁）
    """

    IMPORT_WEIGHT = 0.35
    CALL_WEIGHT = 0.50

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root).resolve()
        self.ts = TreeSitterAdapter()
        self.graph = RepoGraph()
        # file -> mtime 增量缓存（只存 mtime，不存 tree 对象以避免内存泄漏）
        self._cache: dict[str, float] = {}
        self.scan_state = "idle"
        self.max_file_bytes = self._read_max_file_bytes()
        self.scan_stats = ScanStats()
        # 子组件
        self._resolver: ImportResolver | None = None
        self._analyzer = GraphAnalyzer(self.graph)
        # 路由提取结果
        self.routes: list = []

    @staticmethod
    def _read_max_file_bytes() -> int:
        raw = os.getenv("REPOMAP_MAX_FILE_BYTES", str(DEFAULT_MAX_FILE_BYTES))
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_MAX_FILE_BYTES
        return max(0, value)

    # ═══════════════════════════════════════════════════════════════════════════
    # 扫描主流程
    # ═══════════════════════════════════════════════════════════════════════════

    def scan(self, max_files: int = 8000, max_scan_time: float = 300.0) -> None:
        """三阶段扫描：提取符号 → 建依赖边 → PageRank。
        
        Args:
            max_files: 最多扫描文件数
            max_scan_time: 扫描超时时间（秒），默认 300 秒（5 分钟）
        """
        import time
        start_time = time.time()
        
        self.scan_state = "invalid"
        if not self.ts.parsers:
            raise RuntimeError(
                "未检测到任何 tree-sitter 语言绑定。\n"
                "请安装：pip install tree-sitter tree-sitter-python tree-sitter-javascript ..."
            )

        self.graph = RepoGraph()
        self._cache = {}
        self.scan_stats = ScanStats()
        self.routes = []
        # _analyzer 延迟到 graph 构建完成后初始化

        files = self._list_files(max_files)
        logger.info(f"Found {len(files)} source files")

        try:
            for f in files:
                # 超时熔断检查
                elapsed = time.time() - start_time
                if elapsed > max_scan_time:
                    self.scan_stats.timeout_triggered = True
                    logger.warning(f"扫描超时熔断：已运行 {elapsed:.1f}s，超过 {max_scan_time}s 限制")
                    break
                
                try:
                    self._process_file(f)
                except Exception as e:
                    # 记录失败的文件（最多记录 5 个）
                    if len(self.scan_stats.failed_files) < 5:
                        self.scan_stats.failed_files.append(f"{f}: {type(e).__name__}: {str(e)[:50]}")
                    logger.warning(f"Failed to process file {f}: {e}")

            self._build_edges()
            self._analyzer = GraphAnalyzer(self.graph)
            self._calculate_pagerank()
            self.scan_state = "scanned"
        except Exception:
            self.scan_state = "invalid"
            raise
        finally:
            self.scan_stats.scan_duration_ms = int((time.time() - start_time) * 1000)

        sym_count = len(self.graph.symbols)
        edge_count = sum(len(v) for v in self.graph.outgoing.values())
        
        # 构建扫描摘要日志
        summary_parts = [f"Scan complete — {sym_count} symbols, {edge_count} edges, {self.scan_stats.scan_duration_ms}ms"]
        if self.scan_stats.skipped_files:
            summary_parts.append(f", {self.scan_stats.skipped_files} skipped (unchanged)")
        if self.scan_stats.failed_files:
            summary_parts.append(f", {len(self.scan_stats.failed_files)} failed files")
        if self.scan_stats.timeout_triggered:
            summary_parts.append(", timeout triggered")
        
        if self.scan_stats.failed_files or self.scan_stats.timeout_triggered:
            logger.warning("".join(summary_parts))
        else:
            logger.info("".join(summary_parts))

    def is_scanned(self) -> bool:
        return self.scan_state == "scanned"

    # ── 文件处理 ───────────────────────────────────────────────────────────────

    def _list_files(self, max_files: int) -> list[str]:
        """用 ripgrep 快速列文件，fallback 到 pathlib。"""
        rg_cmd = ["rg", "--files", "--hidden", "-g", "!**/*.min.js"]
        for ext in sorted(EXT_TO_LANG):
            rg_cmd.extend(["-g", f"**/*{ext}"])
        try:
            result = subprocess.run(
                rg_cmd, cwd=self.project_root,
                capture_output=True, text=True, timeout=30,
            )
            candidates = sorted(
                line for line in result.stdout.strip().split("\n")
                if line
                and Path(line).suffix.lower() in EXT_TO_LANG
            )
        except Exception:
            # fallback：一次遍历过滤扩展名
            valid_exts = set(EXT_TO_LANG)
            candidates = sorted(
                str(p.relative_to(self.project_root))
                for p in self.project_root.rglob("*")
                if p.is_file()
                and p.suffix.lower() in valid_exts
            )

        filtered_files: list[str] = []
        for file in candidates:
            if self._should_skip_path(file):
                self.scan_stats.filtered_path_files += 1
                continue
            filtered_files.append(file)

        self.scan_stats.listed_source_files = len(candidates)
        if len(filtered_files) > max_files:
            self.scan_stats.truncated_files = len(filtered_files) - max_files
        selected_files = filtered_files[:max_files]
        self.scan_stats.selected_source_files = len(selected_files)
        return selected_files

    def _should_skip_path(self, file: str) -> bool:
        path = Path(file)
        if path.name.endswith(".min.js"):
            return True
        if path.name in SKIP_FILE_NAMES:
            return True
        return any(part in SKIP_DIR_NAMES for part in path.parts)

    def _should_skip_large_file(self, path: Path) -> bool:
        if os.getenv("REPOMAP_SCAN_LARGE_FILES", "0") == "1":
            return False
        try:
            return path.stat().st_size > self.max_file_bytes
        except OSError:
            return True

    def _process_file(self, file: str) -> None:
        path = self.project_root / file
        if not path.exists():
            return
        if self._should_skip_large_file(path):
            self.scan_stats.filtered_large_files += 1
            logger.debug(f"Skip oversized file: {file}")
            return

        mtime = path.stat().st_mtime
        cached_mtime = self._cache.get(file)
        if cached_mtime == mtime:
            self.scan_stats.skipped_files += 1
            return  # 未变更，复用缓存

        ext = Path(file).suffix.lower()
        lang = EXT_TO_LANG.get(ext)
        if not lang or lang not in self.ts.parsers:
            return

        content = path.read_bytes()
        tree = self.ts.parse(content, lang)
        if not tree:
            return

        symbols = self.ts.extract_symbols(tree, lang, file, content)
        self.graph.file_symbols.setdefault(file, [])
        for sym in symbols:
            self.graph.symbols[sym.id] = sym
            self.graph.file_symbols[file].append(sym.id)

        imports = self.ts.extract_imports(tree, lang)
        import_bindings = self.ts.extract_js_ts_import_bindings(content, lang, tree=tree)
        import_modules = {module for module, _ in imports}
        import_modules.update(binding.module for binding in import_bindings if binding.module)
        self.graph.file_imports[file] = sorted(import_modules)
        self.graph.file_import_bindings[file] = import_bindings
        self.graph.file_exports[file] = self.ts.extract_js_ts_export_bindings(content, lang, tree=tree)
        self._mark_exported_symbols(file)

        self.graph.file_calls[file] = self.ts.extract_calls(tree, lang)

        # 提取 HTTP 路由（Python/JS/TS/Rust）
        routes = self.ts.extract_http_routes(tree, lang, file)
        if routes:
            self.routes.extend(routes)

        # 立即释放 tree 对象以避免内存泄漏，只缓存 mtime
        del tree
        self._cache[file] = mtime
        self.scan_stats.processed_files += 1

        # 清理已消失文件的缓存
        stale = [k for k in list(self._cache) if not (self.project_root / k).exists()]
        for k in stale:
            del self._cache[k]

    def _mark_exported_symbols(self, file: str) -> None:
        exported_names = {
            binding.source_name
            for binding in self.graph.file_exports.get(file, [])
            if binding.module is None and binding.source_name and binding.source_name != "*"
        }
        if not exported_names:
            return
        for symbol_id in self.graph.file_symbols.get(file, []):
            symbol = self.graph.symbols.get(symbol_id)
            if symbol and symbol.name in exported_names:
                symbol.visibility = "exported"

    # ── 构建边 ─────────────────────────────────────────────────────────────────

    def _build_edges(self) -> None:
        self._resolver = ImportResolver(self.project_root, self.graph)
        edge_builder = EdgeBuilder(self.graph, self._resolver)
        edge_builder.build_edges()

    # ── PageRank ───────────────────────────────────────────────────────────────

    def _calculate_pagerank(self, damping: float = 0.85, max_iter: int = 50,
                             tol: float = 1e-6) -> None:
        self._analyzer.calculate_pagerank(damping, max_iter, tol)

    # ═══════════════════════════════════════════════════════════════════════════
    # 查询接口（委托给 analyzer）
    # ═══════════════════════════════════════════════════════════════════════════

    def query_symbol(self, name: str) -> list[Any]:
        """按名称模糊查找符号，按 PageRank 降序返回。"""
        return self._analyzer.query_symbol(name)

    def call_chain(self, symbol_id: str, direction: str = "both",
                   max_depth: int = 3) -> dict[str, list[Any]]:
        """
        返回指定符号的调用链。
        direction: "callers" | "callees" | "both"
        """
        if direction not in ("callers", "callees", "both"):
            raise ValueError("direction must be 'callers', 'callees', or 'both'")
        return self._analyzer.call_chain(symbol_id, direction, max_depth)

    def hotspots(self, limit: int = 15) -> list[dict]:
        """识别高密度文件。"""
        return self._analyzer.hotspots(limit)

    def entry_points(self) -> list[str]:
        """识别入口文件。"""
        return self._analyzer.entry_points()

    def file_analysis(self) -> dict[str, dict[str, Any]]:
        """分析每个文件的复杂度和连接性。"""
        return self._analyzer.file_analysis()

    def module_summary(self, limit: int = 8) -> list[dict[str, Any]]:
        """生成模块级别的摘要。"""
        return self._analyzer.module_summary(limit)

    def suggested_reading_order(self, limit: int = 8) -> list[dict[str, Any]]:
        """为 AI 生成推荐阅读顺序。"""
        return self._analyzer.suggested_reading_order(limit)

    def list_routes(self) -> list:
        """返回提取到的 HTTP 路由列表。"""
        return self.routes

    def summary_symbols(self, limit_files: int = 6, per_file: int = 4) -> list[dict[str, Any]]:
        """返回适合 overview 展示的关键实现符号。"""
        return self._analyzer.summary_symbols(limit_files, per_file)

    def _scan_summary_lines(self) -> list[str]:
        lines = [
            f"- 文件数: {self.scan_stats.processed_files}",
            f"- 符号数: {len(self.graph.symbols)}",
            f"- 依赖边: {sum(len(v) for v in self.graph.outgoing.values())}",
            f"- 过滤路径: {self.scan_stats.filtered_path_files}",
            f"- 过滤大文件: {self.scan_stats.filtered_large_files}",
        ]
        if self._resolver and self._resolver.import_configs:
            lines.append(f"- 解析配置: {len(self._resolver.import_configs)}")
        # 超时熔断提示
        if self.scan_stats.timeout_triggered:
            lines.append(f"- ⚠️ 扫描超时熔断: 部分文件未处理，结果不完整")
        # 失败文件提示（最多显示 3 个）
        if self.scan_stats.failed_files:
            lines.append(f"- 处理失败: {len(self.scan_stats.failed_files)} 个文件")
            for ff in self.scan_stats.failed_files[:3]:
                lines.append(f"  - {ff}")
        return lines

    # ═══════════════════════════════════════════════════════════════════════════
    # AI 输出格式（委托给 repomap_ai）
    # ═══════════════════════════════════════════════════════════════════════════

    def render_overview(self, max_chars: int = 16000, with_heat: bool = False,
                        with_co_change: bool = False, granularity: str = "auto") -> str:
        return render_overview_report(self, max_chars, with_heat=with_heat,
                                      with_co_change=with_co_change,
                                      granularity=granularity)

    def render_call_chain(self, symbol_name: str, max_depth: int = 3) -> str:
        return render_call_chain_report(self, symbol_name, max_depth)

    def render_file_detail(self, file_path: str, max_symbols: int = 12, max_chars: int = 6000) -> str:
        return render_file_detail_report(self, file_path, max_symbols=max_symbols, max_chars=max_chars)


# ═══════════════════════════════════════════════════════════════════════════════
# 向后兼容导出
# ═══════════════════════════════════════════════════════════════════════════════

# 从 parser 模块导出常量以保持兼容性
from repomap_parser import QUERIES

__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "EXT_TO_LANG",
    "QUERIES",
    "RepoMapEngine",
    "SKIP_DIR_NAMES",
    "SKIP_FILE_NAMES",
    "TreeSitterAdapter",
    "logger",
]
