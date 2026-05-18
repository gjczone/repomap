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
from pathlib import Path, PurePosixPath
from typing import Any

from .ai import (
    render_call_chain_report,
    render_file_detail_report,
    render_overview_report,
)
from .gitignore import GitignoreParser, get_gitignore
from .parser import EXT_TO_LANG, QUERIES, TreeSitterAdapter
from .ranking import EdgeBuilder, GraphAnalyzer
from .resolver import ImportResolver
from . import (
    HttpRoute,
    RepoGraph,
    ScanStats,
    Symbol,
)

# ── 日志：统一写 stderr，绝不污染 CLI stdout ────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("repomap")

DEFAULT_MAX_FILE_BYTES = 512 * 1024

# 以下两常量已弃用——实际文件过滤完全委托给 GitignoreParser。
# 保留仅为向后兼容导出。新增忽略规则请修改 src/gitignore.py 的 BUILTIN_IGNORE_PATTERNS。
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

SUPPORTING_FILE_NAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "SKILL.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "vitest.config.ts",
    "vitest.config.js",
    "vite.config.ts",
    "vite.config.js",
    "eslint.config.js",
    "eslint.config.mjs",
    "pytest.ini",
    "tox.ini",
}

SENSITIVE_SUPPORTING_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
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
        self.routes: list = []
        self._gitignore: GitignoreParser | None = None
        self._search_index: Any | None = None

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

    def scan(self, max_files: int = 8000, max_scan_time: float = 300.0,
             incremental: bool = False) -> None:
        """三阶段扫描：提取符号 → 建依赖边 → PageRank。

        Args:
            max_files: 最多扫描文件数
            max_scan_time: 扫描超时时间（秒），默认 300 秒（5 分钟）
            incremental: 尝试增量扫描——只重新解析 git 变更文件
        """
        import time
        start_time = time.time()

        self.scan_state = "invalid"
        if not self.ts.parsers:
            raise RuntimeError(
                "No tree-sitter language bindings detected.\n"
                "Install with: uv sync"
            )

        self.graph = RepoGraph()
        self._cache = {}
        self.scan_stats = ScanStats()
        self.routes = []
        self._search_index = None  # invalidate search index on re-scan
        self._inc_cache_loaded = False

        # 尝试加载增量缓存
        inc_cache = None
        if incremental:
            inc_cache = self._load_incremental_cache_if_valid()
        if inc_cache:
            changed_files, deleted_files = self._git_changed_files()
            all_candidate_files = self._list_files(max_files)
            changed_set = set(changed_files) & set(all_candidate_files)
            unchanged_set = set(inc_cache.files.keys()) - changed_set - set(deleted_files)
            files_to_scan = [f for f in all_candidate_files if f in changed_set]
            stale_cached_files: list[str] = []
            for f in sorted(unchanged_set):
                if f in all_candidate_files:
                    if not self._restore_from_inc_cache(f, inc_cache.files[f]):
                        stale_cached_files.append(f)
            if stale_cached_files:
                stale_set = set(stale_cached_files)
                files_to_scan.extend(f for f in all_candidate_files if f in stale_set and f not in changed_set)
            logger.info(
                f"Incremental scan: {len(files_to_scan)} changed/stale, "
                f"{len(unchanged_set) - len(stale_cached_files)} restored, {len(deleted_files)} deleted"
            )
            self._inc_cache_loaded = True
        else:
            files_to_scan = self._list_files(max_files)
            logger.info(f"Found {len(files_to_scan)} source files")

        try:
            for f in files_to_scan:
                # 超时熔断检查
                elapsed = time.time() - start_time
                if elapsed > max_scan_time:
                    self.scan_stats.timeout_triggered = True
                    logger.warning(f"Scan timeout: ran for  {elapsed:.1f}s, limit {max_scan_time}s")
                    break

                try:
                    self._process_file(f)
                except Exception as e:
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

        # 全量扫描后保存增量基线
        if not self._inc_cache_loaded and self.scan_state == "scanned":
            try:
                from .toolkit import save_incremental_cache
                save_incremental_cache(str(self.project_root), self)
            except Exception as e:
                logger.debug(f"Failed to save incremental cache: {e}")

        sym_count = len(self.graph.symbols)
        edge_count = sum(len(v) for v in self.graph.outgoing.values())

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

    # ── 增量扫描辅助 ─────────────────────────────────────────────────────────

    def _load_incremental_cache_if_valid(self) -> Any | None:
        """加载增量缓存并校验有效性（项目路径 + git HEAD 匹配）。"""
        try:
            from .toolkit import _project_root_cache_key, load_incremental_cache
            cache = load_incremental_cache(str(self.project_root))
            if cache is None or not cache.files:
                return None
            if cache.project_root_hash != _project_root_cache_key(self.project_root):
                logger.debug("Incremental cache stale: project root changed")
                return None
            if cache.git_head:
                from .git_backend import GitBackend
                git = GitBackend(str(self.project_root))
                current_head = git.rev_parse_head()
                if current_head and cache.git_head != current_head:
                    logger.debug("Incremental cache stale: git HEAD changed")
                    return None
            return cache
        except Exception:
            return None

    def _git_changed_files(self) -> tuple[list[str], list[str]]:
        """返回 (modified_files, deleted_files)，相对于项目根目录。"""
        try:
            from .git_backend import GitBackend
            git = GitBackend(str(self.project_root))
            modified = git.changed_files()
            deleted = git.deleted_files()
            return sorted(set(modified)), sorted(set(deleted))
        except Exception:
            return [], []

    def _restore_from_inc_cache(self, file_path: str, entry: Any) -> bool:
        """从增量缓存还原文件解析结果，跳过 tree-sitter 解析。"""
        full = self.project_root / file_path
        if not full.exists():
            return False
        actual_mtime = full.stat().st_mtime
        if abs(actual_mtime - entry.mtime) > 0.001:
            return False

        # 还原符号
        self.graph.file_symbols.setdefault(file_path, [])
        for sym_dict in entry.symbols_json:
            sym = Symbol(
                id=sym_dict["id"], name=sym_dict["name"], kind=sym_dict["kind"],
                file=sym_dict["file"], line=sym_dict["line"],
                end_line=sym_dict.get("end_line", sym_dict["line"]),
                col=sym_dict.get("col", 0),
                visibility=sym_dict.get("visibility", "private"),
                docstring=sym_dict.get("docstring", ""),
                signature=sym_dict.get("signature", ""),
                return_type=sym_dict.get("return_type", ""),
                params=sym_dict.get("params", ""),
                pagerank=sym_dict.get("pagerank", 0.0),
            )
            self.graph.symbols[sym.id] = sym
            self.graph.file_symbols[file_path].append(sym.id)

        # 还原 imports
        self.graph.file_imports[file_path] = list(entry.imports)

        # 还原 import bindings
        from . import JSImportBinding
        self.graph.file_import_bindings[file_path] = [
            JSImportBinding(
                local_name=b["local_name"], imported_name=b["imported_name"],
                module=b["module"], line=b["line"], kind=b.get("kind", "named"),
            )
            for b in entry.import_bindings_json
        ]

        # 还原 exports
        from . import JSExportBinding
        self.graph.file_exports[file_path] = [
            JSExportBinding(
                exported_name=b["exported_name"], source_name=b.get("source_name"),
                module=b.get("module"), line=b["line"], kind=b.get("kind", "local"),
            )
            for b in entry.exports_json
        ]

        # 还原 calls
        self.graph.file_calls[file_path] = [
            (c["name"], c["line"], c.get("kind", "direct"))
            for c in entry.calls_json
        ]

        self.routes.extend(HttpRoute(**r) for r in entry.routes_json)

        # 更新 mtime 缓存
        self._cache[file_path] = entry.mtime
        self.scan_stats.processed_files += 1
        self.scan_stats.skipped_files += 1
        return True

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

    def supporting_files(self, limit: int = 8) -> list[dict[str, Any]]:
        """列出符号图之外也值得先看的文档、脚本和配置文件。"""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for file in self._list_supporting_file_candidates():
            if file in seen:
                continue
            seen.add(file)
            classified = self._classify_supporting_file(file)
            if not classified:
                continue
            priority, role, reason = classified
            rows.append({"file": file, "role": role, "reason": reason, "priority": priority})
        rows.sort(key=lambda row: (row["priority"], row["file"]))
        return [
            {"file": row["file"], "role": row["role"], "reason": row["reason"]}
            for row in rows[:limit]
        ]

    def _list_supporting_file_candidates(self) -> list[str]:
        """快速列出仓库文件，用于轻量支撑文件清单；不读取文件内容。"""
        try:
            result = subprocess.run(
                ["rg", "--files", "--hidden", "-g", "!**/*.min.js"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            candidates = sorted(line for line in result.stdout.strip().split("\n") if line)
        except Exception:
            candidates = sorted(
                str(p.relative_to(self.project_root))
                for p in self.project_root.rglob("*")
                if p.is_file()
            )
        root_context_files = [
            name
            for name in ("AGENTS.md", "CLAUDE.md", "README.md", "SKILL.md")
            if (self.project_root / name).is_file()
        ]
        candidates = sorted(set(root_context_files + candidates))
        return [file for file in candidates if not self._should_skip_supporting_path(file)]

    def _should_skip_supporting_path(self, file: str) -> bool:
        path = Path(file)
        name = path.name
        name_lower = name.lower()
        if self._should_skip_path(file):
            return True
        if name in SENSITIVE_SUPPORTING_FILE_NAMES or name_lower.startswith(".env."):
            return True
        if name_lower.endswith((".pem", ".key", ".p12", ".pfx")):
            return True
        return False

    @staticmethod
    def _classify_supporting_file(file: str) -> tuple[int, str, str] | None:
        path = PurePosixPath(file)
        parts = path.parts
        name = path.name
        name_lower = name.lower()
        suffix = path.suffix.lower()
        depth = len(parts)

        if name in {"AGENTS.md", "CLAUDE.md"}:
            return 0, "agent-context", "Injected project structure, rules, and workflow context"
        if name == "SKILL.md":
            return 1, "skill-doc", "Skill entrypoint, typically the skill repository core"
        if name == "README.md":
            return 2, "readme", "User/project entrypoint"
        if name in {"package.json", "pyproject.toml", "Cargo.toml", "go.mod", "requirements.txt"}:
            return 3, "manifest", "Dependencies, scripts, or package metadata"
        if name.startswith("tsconfig") and suffix == ".json":
            return 4, "tooling-config", "TypeScript compilation config"
        if name_lower.startswith(("vite.config", "vitest.config", "eslint.config")):
            return 4, "tooling-config", "Build, test, or lint configuration"
        if name in {"Makefile", "Dockerfile", "docker-compose.yml", "compose.yml"}:
            return 5, "automation", "Build, container, or automation entrypoint"
        if suffix == ".service":
            return 5, "service", "Service deployment/startup configuration"
        if suffix == ".sh" and (depth <= 2 or (parts and parts[0] in {"scripts", "bin"})):
            return 6, "script", "Startup, verification, or maintenance script"
        if suffix == ".md" and (depth <= 2 or (parts and parts[0] in {"docs", "references"})):
            return 7, "docs", "Supplementary documentation or reference"
        if name in SUPPORTING_FILE_NAMES:
            return 8, "supporting", "Project supporting file"
        return None

    def _should_skip_path(self, file: str) -> bool:
        if self._gitignore is None:
            self._gitignore = get_gitignore(self.project_root)
        return self._gitignore.is_ignored(file)

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

        if lang in ("python", "typescript", "tsx", "go", "rust",
                     "java", "kotlin", "swift", "c_sharp", "cpp"):
            self._enrich_symbol_types(file, tree, lang)

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

    def _enrich_python_call_edges(self) -> None:
        python_files = [
            f for f in self.graph.file_symbols
            if f.endswith(".py") or f.endswith(".pyi")
        ]
        if not python_files:
            return
        try:
            from .callgraph import analyze_python_callgraph, resolve_precise_edges
            modules = analyze_python_callgraph(self.project_root, python_files)
            precise_edges = resolve_precise_edges(modules)
            existing_edges = {
                (e.source, e.target)
                for edges in self.graph.outgoing.values()
                for e in edges
            }
            added = 0
            for caller_file, caller_name, callee_file, callee_line, kind in precise_edges:
                caller_id = self._find_symbol_id(caller_file, caller_name)
                callee_id = self._find_symbol_id_by_line(callee_file, callee_line)
                if caller_id and callee_id and (caller_id, callee_id) not in existing_edges:
                    from . import Edge
                    edge = Edge(source=caller_id, target=callee_id, weight=0.55, kind="call")
                    self.graph.outgoing.setdefault(caller_id, []).append(edge)
                    self.graph.incoming.setdefault(callee_id, []).append(edge)
                    existing_edges.add((caller_id, callee_id))
                    added += 1
            if added:
                logger.debug(f"Python precise call graph added {added} edges")
        except Exception as exc:
            logger.debug(f"Python call graph enrichment failed: {exc}")

    def _enrich_ts_call_edges(self) -> None:
        ts_files = [
            f for f in self.graph.file_symbols
            if f.endswith(".ts") or f.endswith(".tsx")
        ]
        if not ts_files:
            return
        try:
            from .callgraph import analyze_ts_callgraph, resolve_precise_edges
            modules = analyze_ts_callgraph(self.project_root, ts_files, self.ts)
            precise_edges = resolve_precise_edges(modules)
            added = self._add_precise_edges(precise_edges)
            if added:
                logger.debug(f"TypeScript precise call graph added {added} edges")
        except Exception as exc:
            logger.debug(f"TypeScript call graph enrichment failed: {exc}")

    def _enrich_go_call_edges(self) -> None:
        go_files = [
            f for f in self.graph.file_symbols
            if f.endswith(".go")
        ]
        if not go_files:
            return
        try:
            from .callgraph import analyze_go_callgraph, resolve_precise_edges
            modules = analyze_go_callgraph(self.project_root, go_files, self.ts)
            precise_edges = resolve_precise_edges(modules)
            added = self._add_precise_edges(precise_edges)
            if added:
                logger.debug(f"Go precise call graph added {added} edges")
        except Exception as exc:
            logger.debug(f"Go call graph enrichment failed: {exc}")

    def _enrich_rust_call_edges(self) -> None:
        rust_files = [
            f for f in self.graph.file_symbols
            if f.endswith(".rs")
        ]
        if not rust_files:
            return
        try:
            from .callgraph import analyze_rust_callgraph, resolve_precise_edges
            modules = analyze_rust_callgraph(self.project_root, rust_files, self.ts)
            precise_edges = resolve_precise_edges(modules)
            added = self._add_precise_edges(precise_edges)
            if added:
                logger.debug(f"Rust precise call graph added {added} edges")
        except Exception as exc:
            logger.debug(f"Rust call graph enrichment failed: {exc}")

    def _add_precise_edges(
        self,
        precise_edges: list[tuple[str, str, str, int, str]],
    ) -> int:
        existing_edges = {
            (e.source, e.target)
            for edges in self.graph.outgoing.values()
            for e in edges
        }
        added = 0
        for caller_file, caller_name, callee_file, callee_line, kind in precise_edges:
            caller_id = self._find_symbol_id(caller_file, caller_name)
            callee_id = self._find_symbol_id_by_line(callee_file, callee_line)
            if caller_id and callee_id and (caller_id, callee_id) not in existing_edges:
                from . import Edge
                edge = Edge(source=caller_id, target=callee_id, weight=0.55, kind=kind)
                self.graph.outgoing.setdefault(caller_id, []).append(edge)
                self.graph.incoming.setdefault(callee_id, []).append(edge)
                existing_edges.add((caller_id, callee_id))
                added += 1
        return added

    def _find_symbol_id(self, file: str, name: str) -> str | None:
        for sym_id in self.graph.file_symbols.get(file, []):
            sym = self.graph.symbols.get(sym_id)
            if sym and sym.name == name:
                return sym_id
        base_name = name.split(".")[-1]
        for sym_id in self.graph.file_symbols.get(file, []):
            sym = self.graph.symbols.get(sym_id)
            if sym and sym.name == base_name:
                return sym_id
        return None

    def _find_symbol_id_by_line(self, file: str, line: int) -> str | None:
        for sym_id in self.graph.file_symbols.get(file, []):
            sym = self.graph.symbols.get(sym_id)
            if sym and sym.line == line:
                return sym_id
        return None

    def _enrich_symbol_types(self, file: str, tree: Any, lang: str) -> None:
        from .type_inference import extract_types_for_file
        sym_ids = self.graph.file_symbols.get(file, [])
        if not sym_ids:
            return
        extract_types_for_file(tree, lang, sym_ids, self.graph.symbols)

    # ── 构建边 ─────────────────────────────────────────────────────────────────

    def _build_edges(self) -> None:
        self._resolver = ImportResolver(self.project_root, self.graph)
        edge_builder = EdgeBuilder(self.graph, self._resolver)
        edge_builder.build_edges()
        self._enrich_python_call_edges()
        self._enrich_ts_call_edges()
        self._enrich_go_call_edges()
        self._enrich_rust_call_edges()

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

    def file_clusters(self, limit: int = 8) -> list[dict[str, Any]]:
        """Detect module clusters from the file dependency graph."""
        from .ranking import detect_file_clusters, format_cluster_summary
        clusters = detect_file_clusters(self.graph)
        return format_cluster_summary(clusters, top_n=limit)

    def summary_symbols(self, limit_files: int = 6, per_file: int = 4) -> list[dict[str, Any]]:
        """返回适合 overview 展示的关键实现符号。"""
        return self._analyzer.summary_symbols(limit_files, per_file)

    def _scan_summary_lines(self) -> list[str]:
        lines = [
            f"- Files: {self.scan_stats.processed_files}",
            f"- Symbols: {len(self.graph.symbols)}",
            f"- Edges: {sum(len(v) for v in self.graph.outgoing.values())}",
            f"- Filtered paths: {self.scan_stats.filtered_path_files}",
            f"- Filtered large files: {self.scan_stats.filtered_large_files}",
        ]
        if self._resolver and self._resolver.import_configs:
            lines.append(f"- Import configs: {len(self._resolver.import_configs)}")
        # 超时熔断提示
        if self.scan_stats.timeout_triggered:
            lines.append("- ⚠️ Scan timeout triggered: some files were not processed, results incomplete")
        # 失败文件提示（最多显示 3 个）
        if self.scan_stats.failed_files:
            lines.append(f"- Failed files: {len(self.scan_stats.failed_files)}")
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

    def render_file_detail(self, file_path: str, max_symbols: int = 12, max_chars: int = 6000,
                           lsp_symbol_tree: list[Any] | None = None) -> str:
        return render_file_detail_report(self, file_path, max_symbols=max_symbols, max_chars=max_chars,
                                         lsp_symbol_tree=lsp_symbol_tree)

    def search_symbols(self, query: str, top_k: int = 20) -> list[tuple[Any, float]]:
        """BM25 符号搜索，返回 [(Symbol, score), ...]。"""
        if self._search_index is None:
            from .search import SymbolSearchIndex
            self._search_index = SymbolSearchIndex(self.graph.symbols)
        results = self._search_index.search(query, top_k)
        return [(self.graph.symbols[sid], score) for sid, score in results if sid in self.graph.symbols]


# ═══════════════════════════════════════════════════════════════════════════════
# 向后兼容导出
# ═══════════════════════════════════════════════════════════════════════════════

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
