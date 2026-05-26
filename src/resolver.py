#!/usr/bin/env python3
"""
Repo Map Resolver — Import and Alias Resolution Layer
=======================================================
负责 import 路径解析、alias 映射、re-export 追踪。

支持：
- tsconfig.json / jsconfig.json paths 和 baseUrl
- package.json exports 字段
- Vite / Webpack 等 bundler 的 alias 配置
- CommonJS require/module.exports
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .gitignore import get_gitignore
from .parser import EXT_TO_LANG
from . import JSImportBinding, PathAliasRule, ProjectImportConfig, RepoGraph, json_loads

logger = logging.getLogger("repomap")

MAX_EXPORT_RESOLVE_DEPTH = 3
CALLABLE_KINDS = {"function", "method", "anonymous_function"}

# Bundler config file names
BUNDLER_CONFIGS = {
    "vite": ["vite.config.js", "vite.config.ts", "vite.config.mjs"],
    "webpack": ["webpack.config.js", "webpack.config.ts"],
    "rollup": ["rollup.config.js", "rollup.config.mjs"],
    "esbuild": [],  # esbuild usually configured in package.json or build scripts
    "turbopack": [],  # Next.js 13+, uses next.config.js
}


class PackageJsonExports:
    """解析 package.json exports 字段，支持条件导出和子路径模式。"""

    def __init__(self, exports: Any) -> None:
        self.raw = exports
        self.mappings: dict[str, str] = {}  # subpath -> resolved path
        self._parse_exports(exports)

    def _parse_exports(
        self, exports: Any, base_path: str = ".", is_nested_condition: bool = False
    ) -> None:
        """解析 exports 字段的各种形式。

        Args:
            exports: package.json 中的 exports 字段值
            base_path: 当前子路径（用于子路径映射）
            is_nested_condition: 是否处于嵌套条件导出中
        """
        if isinstance(exports, str):
            # 简写形式: "exports": "./dist/index.js"
            self.mappings[base_path] = exports
        elif isinstance(exports, dict):
            # 检查是否有条件导出 (import/require/default/types 等)
            condition_keys = {
                "import",
                "require",
                "default",
                "types",
                "node",
                "browser",
                "deno",
            }
            has_conditions = bool(set(exports.keys()) & condition_keys)

            if has_conditions:
                # 选择优先的条件: import > default > require > types
                selected_target = None
                for key in ("import", "default", "require", "types"):
                    if key in exports:
                        selected_target = exports[key]
                        break

                if selected_target is None:
                    # 使用第一个非子路径的可用条件
                    for key, target in exports.items():
                        if not key.startswith("."):
                            selected_target = target
                            break

                # 处理选中的目标
                if isinstance(selected_target, str):
                    self.mappings[base_path] = selected_target
                elif isinstance(selected_target, dict):
                    # 嵌套条件导出，base_path 保持不变
                    self._parse_exports(
                        selected_target, base_path, is_nested_condition=True
                    )
            else:
                # 子路径映射形式
                for key, target in exports.items():
                    subpath = f"{base_path}/{key}" if base_path != "." else key
                    if isinstance(target, str):
                        self.mappings[subpath] = target
                    elif isinstance(target, dict):
                        # 子路径映射中的值可能是条件导出字典
                        self._parse_exports(target, subpath, is_nested_condition=False)

    def resolve(self, import_path: str) -> str | None:
        """
        解析 import 路径到实际文件路径。
        import_path 应该是包内路径，如 "#utils" 或 "./helpers"
        """
        # 直接匹配
        if import_path in self.mappings:
            return self.mappings[import_path]

        # 处理子路径通配符模式，如 "./features/*": "./dist/features/*.js"
        for pattern, target in self.mappings.items():
            if "*" in pattern:
                prefix = pattern.split("*")[0]
                if import_path.startswith(prefix):
                    suffix = import_path[len(prefix) :]
                    if "*" in target:
                        return target.replace("*", suffix, 1)
                    # 目标没有通配符，追加到目录
                    if target.endswith("/"):
                        return f"{target}{suffix}"

        return None


class BundlerAliasConfig:
    """解析各种 bundler 的 alias 配置。

    NOTE: 当前实现仅作为占位符，实际 bundler alias 解析依赖于：
    1. tsconfig.json / jsconfig.json 的 paths 配置（由 ImportResolver 主逻辑处理）
    2. 项目特定的构建配置通常应由用户通过其他方式提供

    正则解析 JS 配置文件极易出错，已移除。如需支持特定 bundler，
    建议通过显式的配置文件映射而非解析代码实现。
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.aliases: dict[str, str] = {}  # alias -> resolved path

    def resolve(self, import_path: str) -> str | None:
        """解析 bundler alias 到实际路径。"""
        for alias, target in self.aliases.items():
            if import_path == alias:
                return target
            if import_path.startswith(f"{alias}/"):
                suffix = import_path[len(alias) + 1 :]
                return (
                    f"{target}/{suffix}"
                    if not target.endswith("/")
                    else f"{target}{suffix}"
                )
        return None


class ImportResolver:
    """
    Import 解析器：处理各种 import 路径解析场景。
    """

    def __init__(self, project_root: Path, graph: RepoGraph) -> None:
        self.project_root = project_root
        self.graph = graph
        self.import_configs: list[ProjectImportConfig] = []
        self.bundler_aliases = BundlerAliasConfig(project_root)
        self.package_exports: dict[
            str, PackageJsonExports
        ] = {}  # package name -> exports
        self._gitignore = get_gitignore(project_root)
        self.package_export_roots: dict[
            str, PurePosixPath
        ] = {}  # package name -> project-relative package root
        self._package_imports: dict[str, str] = {}  # #pattern -> resolved target

        # 构建文件索引: stem -> [files]
        self._file_map: dict[str, list[str]] = defaultdict(list)
        # name -> [sym_id]
        self._name_idx: dict[str, list[str]] = defaultdict(list)
        # sym_id -> file
        self._sym_file: dict[str, str] = {}

        # 导入解析缓存：(source_file, imp) -> target_files，避免同一模块被反复解析
        self._resolve_cache: dict[tuple[str, str], list[str]] = {}

        self._load_import_configs()
        self._load_package_json_exports()
        self._detect_vite_alias()

    def _load_import_configs(self) -> None:
        """加载所有 tsconfig.json / jsconfig.json 配置。"""
        configs: list[ProjectImportConfig] = []
        for config_path in self._discover_import_config_paths():
            data = self._load_jsonc_with_extends(config_path, set())
            compiler_options = (
                data.get("compilerOptions", {}) if isinstance(data, dict) else {}
            )
            base_url = self._resolve_config_relative_path(
                config_path, compiler_options.get("baseUrl")
            )
            alias_rules: list[PathAliasRule] = []
            raw_paths = compiler_options.get("paths", {})
            if isinstance(raw_paths, dict):
                for alias_pattern, targets in raw_paths.items():
                    if not isinstance(alias_pattern, str) or not isinstance(
                        targets, list
                    ):
                        continue
                    resolved_targets = tuple(
                        target
                        for raw_target in targets
                        if isinstance(raw_target, str)
                        for target in [
                            self._resolve_config_relative_path(
                                config_path, raw_target, base_url
                            )
                        ]
                        if target is not None
                    )
                    if resolved_targets:
                        alias_rules.append(
                            PathAliasRule(
                                alias_pattern=alias_pattern,
                                target_patterns=resolved_targets,
                            )
                        )
            try:
                config_dir = config_path.parent.relative_to(
                    self.project_root
                ).as_posix()
            except ValueError:
                continue
            configs.append(
                ProjectImportConfig(
                    config_path=str(config_path.relative_to(self.project_root)),
                    config_dir=config_dir or ".",
                    base_url=base_url,
                    alias_rules=alias_rules,
                )
            )
        configs.sort(
            key=lambda config: (
                -self._path_depth(config.config_dir),
                config.config_path or "",
            )
        )
        self.import_configs = configs

    def _load_package_json_exports(self) -> None:
        """扫描 package.json 并解析 exports 字段。"""
        package_json_path = self.project_root / "package.json"
        if package_json_path.exists():
            try:
                data = json_loads(package_json_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "exports" in data:
                    self._register_package_exports(
                        ".", data["exports"], PurePosixPath(".")
                    )
                    package_name = data.get("name")
                    if isinstance(package_name, str) and package_name:
                        self._register_package_exports(
                            package_name, data["exports"], PurePosixPath(".")
                        )
                # 解析 imports 字段（Node.js # 私有导入）
                if isinstance(data, dict) and "imports" in data:
                    self._parse_package_imports(data["imports"])
            except Exception as e:
                logger.debug(f"Failed to parse package.json: {e}")

        # 解析子包的 package.json（monorepo 场景），跳过依赖和构建目录，避免读取海量无关 package。
        for root, dir_names, file_names in os.walk(self.project_root):
            dir_names[:] = [
                n
                for n in dir_names
                if not self._gitignore.is_ignored(
                    (Path(root).relative_to(self.project_root) / n).as_posix() + "/"
                )
            ]
            if "package.json" not in file_names:
                continue
            sub_package_path = Path(root) / "package.json"
            if sub_package_path == package_json_path:
                continue
            try:
                sub_data = json_loads(sub_package_path.read_text(encoding="utf-8"))
                if not isinstance(sub_data, dict) or "exports" not in sub_data:
                    continue
                rel_path = sub_package_path.parent.relative_to(self.project_root)
                package_root = PurePosixPath(rel_path.as_posix())
                path_package_name = f"./{rel_path.as_posix()}"
                self._register_package_exports(
                    path_package_name, sub_data["exports"], package_root
                )
                package_name = sub_data.get("name")
                if isinstance(package_name, str) and package_name:
                    self._register_package_exports(
                        package_name, sub_data["exports"], package_root
                    )
            except ValueError:
                continue
            except Exception as e:
                logger.debug(f"Failed to parse {sub_package_path}: {e}")

    def _detect_vite_alias(self) -> None:
        """检测 Vite 默认 alias: ~/ → src/。"""
        for cfg_name in ("vite.config.ts", "vite.config.js", "vite.config.mjs"):
            if (self.project_root / cfg_name).exists():
                if "~" not in self.bundler_aliases.aliases:
                    self.bundler_aliases.aliases["~"] = "src"
                break

    def _parse_package_imports(self, imports: Any) -> None:
        """解析 package.json imports 字段（# 私有导入映射）。"""
        if not isinstance(imports, dict):
            return
        for pattern, target in imports.items():
            resolved = None
            if isinstance(target, str):
                resolved = target
            elif isinstance(target, dict):
                for key in ("import", "default", "require", "node", "browser"):
                    if key in target and isinstance(target[key], str):
                        resolved = target[key]
                        break
            if resolved and isinstance(pattern, str):
                self._package_imports[pattern] = resolved

    def _register_package_exports(
        self, package_name: str, exports: Any, package_root: PurePosixPath
    ) -> None:
        self.package_exports[package_name] = PackageJsonExports(exports)
        self.package_export_roots[package_name] = package_root

    def build_indices(self) -> None:
        """构建文件和符号索引，用于快速解析。"""
        self._file_map.clear()
        self._name_idx.clear()
        self._sym_file.clear()

        for file in sorted(self.graph.file_symbols):
            self._file_map[Path(file).stem].append(file)

        for sid, sym in self.graph.symbols.items():
            self._name_idx[sym.name].append(sid)
            self._sym_file[sid] = sym.file

        for symbol_ids in self._name_idx.values():
            symbol_ids.sort(
                key=lambda symbol_id: (
                    self._sym_file[symbol_id],
                    self.graph.symbols[symbol_id].line,
                    self.graph.symbols[symbol_id].name,
                )
            )

    def resolve_import_targets(self, source_file: str, imp: str) -> list[str]:
        """解析 import 路径到目标文件列表。"""
        # 非相对路径走缓存，避免同一模块被不同文件重复解析
        cache_key = (source_file, imp)
        if cached := self._resolve_cache.get(cache_key):
            return cached

        result: list[str]
        # 处理 Node.js # 私有导入
        if imp.startswith("#") and self._package_imports:
            target = self._package_imports.get(imp)
            if target:
                result = self._resolve_package_export_target(".", target)
                if result:
                    self._resolve_cache[cache_key] = result
                    return result

        if imp.startswith("."):
            result = self._resolve_relative(source_file, imp)
        else:
            # 尝试 Java 点号导入 (com.example.Foo → com/example/Foo.java)
            source_ext = Path(source_file).suffix.lower()
            if source_ext == ".java" and "." in imp and not imp.startswith("."):
                java_modules = [part for part in imp.split(".") if part]
                if java_modules:
                    java_path = PurePosixPath(*java_modules)
                    java_matches = self._candidate_files_for_base_path(java_path)
                    if java_matches:
                        self._resolve_cache[cache_key] = java_matches
                        return java_matches

            # 尝试 bundler alias 解析
            bundler_match = self.bundler_aliases.resolve(imp)
            if bundler_match:
                result = self._resolve_relative(source_file, bundler_match)
            else:
                # 尝试 tsconfig/jsconfig alias/baseUrl 解析
                alias_matches = self._resolve_alias_or_baseurl_targets(source_file, imp)
                if alias_matches:
                    result = alias_matches
                else:
                    # 尝试 package.json exports 解析（用于自引用或子包）
                    pkg_result = None
                    for package_name, exports in self.package_exports.items():
                        export_subpath = self._package_export_subpath(package_name, imp)
                        if export_subpath is None:
                            continue
                        resolved = exports.resolve(export_subpath)
                        if resolved:
                            pkg_result = self._resolve_package_export_target(
                                package_name, resolved
                            )
                            break
                    if pkg_result is not None:
                        result = pkg_result
                    else:
                        python_matches = self._resolve_python_dotted_import(
                            source_file, imp
                        )
                        if python_matches:
                            result = python_matches
                        else:
                            # 最后尝试模块名匹配（优先同语言）
                            module_key = Path(imp).stem or imp.split(".")[-1]
                            matches = list(self._file_map.get(module_key, []))
                            if not matches:
                                result = []
                            else:
                                # 优先匹配同扩展名的文件（语言隔离）
                                source_ext = Path(source_file).suffix.lower()
                                same_lang_matches = [
                                    f for f in matches if f.lower().endswith(source_ext)
                                ]
                                if same_lang_matches:
                                    result = same_lang_matches
                                else:
                                    # 次优：匹配相同语言组的文件
                                    source_lang = EXT_TO_LANG.get(source_ext)
                                    if source_lang:
                                        same_group_matches = [
                                            f
                                            for f in matches
                                            if EXT_TO_LANG.get(Path(f).suffix.lower())
                                            == source_lang
                                        ]
                                        if same_group_matches:
                                            result = same_group_matches
                                        else:
                                            # 兜底：返回所有匹配（但限制数量避免爆炸）
                                            result = matches[:3]
                                    else:
                                        result = matches[:3]

        # 缓存非相对路径的解析结果（相对路径取决于源文件位置，不宜缓存）
        if not imp.startswith("."):
            self._resolve_cache[cache_key] = result
        return result

    def _package_export_subpath(self, package_name: str, imp: str) -> str | None:
        if package_name == ".":
            return imp if imp.startswith("#") else None
        if imp == package_name:
            return "."
        prefix = package_name + "/"
        if imp.startswith(prefix):
            return "./" + imp[len(prefix) :]
        return None

    def _resolve_package_export_target(
        self, package_name: str, target: str
    ) -> list[str]:
        package_root = self.package_export_roots.get(package_name, PurePosixPath("."))
        target_path = PurePosixPath(target)
        if target_path.is_absolute():
            return []
        normalized = self._normalize_posix_path(package_root / target_path)
        if normalized is None:
            return []
        return self._candidate_files_for_base_path(normalized)

    def _resolve_relative(self, source_file: str, imp: str) -> list[str]:
        resolved = self._resolve_relative_base(source_file, imp)
        if resolved is None:
            return []
        return self._candidate_files_for_base_path(resolved)

    def _candidate_files_for_base_path(self, resolved: PurePosixPath) -> list[str]:
        matches = []
        resolved_str = str(resolved)
        if resolved_str in self.graph.file_symbols:
            matches.append(resolved_str)
            if resolved.suffix.lower() in EXT_TO_LANG:
                return sorted(set(matches))
        runtime_source_exts = {
            ".js": (".ts", ".tsx"),
            ".jsx": (".tsx",),
            ".mjs": (".mts", ".ts", ".tsx"),
            ".cjs": (".cts", ".ts", ".tsx"),
        }
        for source_ext in runtime_source_exts.get(resolved.suffix.lower(), ()):
            source_file = str(resolved.with_suffix(source_ext))
            if source_file in self.graph.file_symbols:
                matches.append(source_file)
        for ext in EXT_TO_LANG:
            direct = resolved_str + ext
            index_file = str(resolved / f"index{ext}")
            if direct in self.graph.file_symbols:
                matches.append(direct)
            if index_file in self.graph.file_symbols:
                matches.append(index_file)
        init_file = str(resolved / "__init__.py")
        if init_file in self.graph.file_symbols:
            matches.append(init_file)
        # Python namespace package: 无 __init__.py 时也尝试匹配目录下 .py 文件
        elif resolved_str not in self.graph.file_symbols:
            ns_prefix = resolved_str + "/"
            ns_matches = [
                f
                for f in self.graph.file_symbols
                if f.startswith(ns_prefix) and f.endswith(".py")
            ]
            matches.extend(ns_matches[:5])
        return sorted(set(matches))

    def _resolve_python_dotted_import(self, source_file: str, imp: str) -> list[str]:
        if (
            EXT_TO_LANG.get(Path(source_file).suffix.lower()) != "python"
            or "." not in imp
        ):
            return []
        module_path = PurePosixPath(*[part for part in imp.split(".") if part])
        if str(module_path) in ("", "."):
            return []
        return self._candidate_files_for_base_path(module_path)

    def _resolve_alias_or_baseurl_targets(
        self, source_file: str, imp: str
    ) -> list[str]:
        for config in self._candidate_import_configs_for_file(source_file):
            alias_matches: list[str] = []
            for rule in config.alias_rules:
                wildcard_value = self._match_alias_pattern(rule.alias_pattern, imp)
                if wildcard_value is None:
                    continue
                for target_pattern in rule.target_patterns:
                    target_base = self._apply_alias_target(
                        target_pattern, wildcard_value
                    )
                    if target_base is None:
                        continue
                    alias_matches.extend(
                        self._candidate_files_for_base_path(PurePosixPath(target_base))
                    )
            if alias_matches:
                return sorted(set(alias_matches))
            if config.base_url:
                base_path = self._normalize_posix_path(
                    PurePosixPath(config.base_url, imp)
                )
                if base_path is None:
                    continue
                base_matches = self._candidate_files_for_base_path(base_path)
                if base_matches:
                    return base_matches
        return []

    def _candidate_import_configs_for_file(
        self, source_file: str
    ) -> list[ProjectImportConfig]:
        source_parent = self._normalize_posix_path(PurePosixPath(source_file).parent)
        if source_parent is None:
            return []
        ranked: list[tuple[int, ProjectImportConfig]] = []
        for config in self.import_configs:
            config_dir = self._normalize_posix_path(
                PurePosixPath(config.config_dir or ".")
            )
            if config_dir is not None and self._is_subpath(source_parent, config_dir):
                ranked.append((self._path_depth(config.config_dir), config))
        ranked.sort(key=lambda item: (-item[0], item[1].config_path or ""))
        return [config for _, config in ranked]

    @staticmethod
    def _match_alias_pattern(alias_pattern: str, import_path: str) -> str | None:
        if "*" not in alias_pattern:
            return "" if alias_pattern == import_path else None
        prefix, suffix = alias_pattern.split("*", 1)
        if not import_path.startswith(prefix) or not import_path.endswith(suffix):
            return None
        return import_path[
            len(prefix) : len(import_path) - len(suffix) if suffix else None
        ]

    @staticmethod
    def _apply_alias_target(target_pattern: str, wildcard_value: str) -> str | None:
        if "*" in target_pattern:
            return target_pattern.replace("*", wildcard_value, 1)
        if wildcard_value:
            return None
        return target_pattern

    def _resolve_relative_base(
        self, source_file: str, imp: str
    ) -> PurePosixPath | None:
        source_parent = PurePosixPath(source_file).parent
        if "/" in imp:
            return self._normalize_posix_path(PurePosixPath(source_parent, imp))

        leading_dots = len(imp) - len(imp.lstrip("."))
        remainder = imp.lstrip(".").replace(".", "/")
        base = source_parent
        for _ in range(max(leading_dots - 1, 0)):
            base = base.parent
        if remainder:
            base = PurePosixPath(base, remainder)
        return self._normalize_posix_path(base)

    @staticmethod
    def _normalize_posix_path(path: PurePosixPath) -> PurePosixPath | None:
        normalized_parts: list[str] = []
        for part in path.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not normalized_parts:
                    return None
                normalized_parts.pop()
                continue
            normalized_parts.append(part)
        if not normalized_parts:
            return PurePosixPath(".")
        return PurePosixPath(*normalized_parts)

    @staticmethod
    def _path_depth(path_value: str | None) -> int:
        if not path_value or path_value == ".":
            return 0
        return len(
            [part for part in PurePosixPath(path_value).parts if part not in ("", ".")]
        )

    @staticmethod
    def _is_subpath(path: PurePosixPath, maybe_parent: PurePosixPath) -> bool:
        parent_parts = tuple(
            part for part in maybe_parent.parts if part not in ("", ".")
        )
        path_parts = tuple(part for part in path.parts if part not in ("", "."))
        if not parent_parts:
            return True
        if len(parent_parts) > len(path_parts):
            return False
        return path_parts[: len(parent_parts)] == parent_parts

    def resolve_calling_symbol(self, file: str, call_line: int) -> str | None:
        """确定指定行所在的符号（调用者）。"""
        symbol_ids = self.graph.file_symbols.get(file, [])
        containing = [
            self.graph.symbols[symbol_id]
            for symbol_id in symbol_ids
            if symbol_id in self.graph.symbols
            and self.graph.symbols[symbol_id].line
            <= call_line
            <= max(
                self.graph.symbols[symbol_id].end_line,
                self.graph.symbols[symbol_id].line,
            )
        ]
        if not containing:
            return None
        containing.sort(
            key=lambda symbol: (
                max(symbol.end_line, symbol.line) - symbol.line,
                -symbol.line,
                symbol.col,
                symbol.name,
            )
        )
        return containing[0].id

    def resolve_import_binding_targets(
        self,
        file: str,
        binding: JSImportBinding,
    ) -> set[str]:
        """解析 import binding 到目标符号。"""
        if binding.imported_name == "*":
            return set()
        target_files = self.resolve_import_targets(file, binding.module)
        if not target_files:
            return set()

        resolved_ids: set[str] = set()
        for target_file in target_files:
            resolved_ids.update(
                self._resolve_exported_symbols(
                    file=target_file,
                    export_name=binding.imported_name,
                    depth=0,
                    visited=set(),
                )
            )

        if resolved_ids:
            return resolved_ids

        if binding.imported_name != "default":
            direct_candidates = [
                symbol_id
                for symbol_id in self._name_idx.get(binding.imported_name, [])
                if self._sym_file[symbol_id] in target_files
            ]
            if direct_candidates:
                return set(direct_candidates)
        return set()

    def _resolve_exported_symbols(
        self,
        file: str,
        export_name: str,
        depth: int,
        visited: set[tuple[str, str]],
    ) -> set[str]:
        """递归解析 re-export，追踪到实际定义的符号。"""
        if depth >= MAX_EXPORT_RESOLVE_DEPTH:
            return set()
        visit_key = (file, export_name)
        if visit_key in visited:
            return set()
        next_visited = set(visited)
        next_visited.add(visit_key)

        bindings = self.graph.file_exports.get(file, [])
        resolved_ids: set[str] = set()

        for binding in bindings:
            if binding.kind == "wildcard" and binding.module:
                target_files = self.resolve_import_targets(file, binding.module)
                for target_file in target_files:
                    resolved_ids.update(
                        self._resolve_exported_symbols(
                            file=target_file,
                            export_name=export_name,
                            depth=depth + 1,
                            visited=next_visited,
                        )
                    )
                continue

            if binding.exported_name != export_name:
                continue

            if binding.module is None and binding.source_name:
                resolved_ids.update(
                    symbol_id
                    for symbol_id in self._name_idx.get(binding.source_name, [])
                    if self._sym_file[symbol_id] == file
                )
                continue

            if binding.module and binding.source_name:
                target_files = self.resolve_import_targets(file, binding.module)
                for target_file in target_files:
                    resolved_ids.update(
                        self._resolve_exported_symbols(
                            file=target_file,
                            export_name=binding.source_name,
                            depth=depth + 1,
                            visited=next_visited,
                        )
                    )

        if resolved_ids:
            return resolved_ids

        return {
            symbol_id
            for symbol_id in self._name_idx.get(export_name, [])
            if self._sym_file[symbol_id] == file
            and self.graph.symbols[symbol_id].visibility == "exported"
        }

    def resolve_call_target(
        self,
        file: str,
        call_name: str,
        call_line: int,
        call_kind: str,
        import_targets_by_file: dict[str, set[str]],
        import_symbol_targets_by_file: dict[str, dict[str, set[str]]],
    ) -> str | None:
        """解析函数调用到目标符号。"""
        candidates = [
            symbol_id
            for symbol_id in self._name_idx.get(call_name, [])
            if self.graph.symbols[symbol_id].kind in CALLABLE_KINDS
        ]
        if not candidates:
            return None

        imported_targets = [
            symbol_id
            for symbol_id in import_symbol_targets_by_file.get(file, {}).get(
                call_name, set()
            )
            if self.graph.symbols[symbol_id].kind in CALLABLE_KINDS
        ]
        if imported_targets:
            if len(imported_targets) == 1:
                return imported_targets[0]
            return self._pick_best_target(imported_targets, file, call_line)

        imported_files = import_targets_by_file.get(file, set())
        imported = [
            symbol_id
            for symbol_id in candidates
            if self._sym_file[symbol_id] in imported_files
        ]
        if imported:
            return self._pick_best_target(imported, file, call_line)

        if call_kind == "member":
            # 成员调用 (obj.method())：仅在同文件匹配 method 类型的符号，避免误绑到全局函数
            same_file_methods = [
                sid
                for sid in candidates
                if self._sym_file[sid] == file
                and self.graph.symbols[sid].kind == "method"
            ]
            if same_file_methods:
                return self._pick_best_target(same_file_methods, file, call_line)
            return None

        if any(
            binding.local_name == call_name
            for binding in self.graph.file_import_bindings.get(file, [])
        ):
            return None

        same_file = [
            symbol_id for symbol_id in candidates if self._sym_file[symbol_id] == file
        ]
        if same_file:
            return self._pick_best_target(same_file, file, call_line)

        if len(candidates) == 1:
            return candidates[0]

        exported = [
            symbol_id
            for symbol_id in candidates
            if self.graph.symbols[symbol_id].visibility == "exported"
        ]
        if len(exported) == 1:
            return exported[0]
        return None

    def _pick_best_target(
        self, candidates: list[str], file: str, call_line: int
    ) -> str:
        ordered = sorted(
            candidates,
            key=lambda symbol_id: (
                0 if self.graph.symbols[symbol_id].file == file else 1,
                abs(self.graph.symbols[symbol_id].line - call_line),
                0 if self.graph.symbols[symbol_id].visibility == "exported" else 1,
                self.graph.symbols[symbol_id].file,
                self.graph.symbols[symbol_id].line,
                self.graph.symbols[symbol_id].name,
            ),
        )
        return ordered[0]

    def _discover_import_config_paths(self) -> list[Path]:
        found: list[Path] = []
        for root, dir_names, file_names in os.walk(self.project_root):
            dir_names[:] = [
                n
                for n in dir_names
                if not self._gitignore.is_ignored(
                    (Path(root).relative_to(self.project_root) / n).as_posix() + "/"
                )
            ]
            for filename in ("tsconfig.json", "jsconfig.json"):
                if filename in file_names:
                    found.append(Path(root) / filename)
        return sorted(found)

    def _load_jsonc_with_extends(
        self, config_path: Path, visited: set[Path]
    ) -> dict[str, Any]:
        if config_path in visited or not config_path.exists():
            return {}
        next_visited = set(visited)
        next_visited.add(config_path)
        try:
            raw_data = json_loads(
                self._strip_jsonc(config_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError):
            return {}
        if not isinstance(raw_data, dict):
            return {}

        extends_value = raw_data.get("extends")
        if not isinstance(extends_value, str):
            return raw_data
        base_config_path = self._resolve_extends_path(config_path, extends_value)
        if base_config_path is None:
            return raw_data
        base_data = self._load_jsonc_with_extends(base_config_path, next_visited)
        return self._merge_dicts(base_data, raw_data)

    def _resolve_extends_path(
        self, config_path: Path, extends_value: str
    ) -> Path | None:
        candidate = Path(extends_value)
        if not candidate.suffix:
            candidate = candidate.with_suffix(".json")
        if candidate.is_absolute():
            resolved = candidate
        else:
            if not extends_value.startswith("."):
                return None
            resolved = (config_path.parent / candidate).resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            return None
        return resolved

    def _resolve_config_relative_path(
        self, config_path: Path, value: Any, base_url: str | None = None
    ) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        value_path = Path(value)
        if base_url and not value.startswith(".") and not value_path.is_absolute():
            resolved = (self.project_root / base_url / value).resolve()
        else:
            resolved = (config_path.parent / value).resolve()
        try:
            return resolved.relative_to(self.project_root).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = ImportResolver._merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _strip_jsonc(text: str) -> str:
        result: list[str] = []
        in_string = False
        string_delimiter = ""
        escape = False
        in_line_comment = False
        in_block_comment = False
        i = 0
        while i < len(text):
            char = text[i]
            next_char = text[i + 1] if i + 1 < len(text) else ""
            if in_line_comment:
                if char == "\n":
                    in_line_comment = False
                    result.append(char)
                i += 1
                continue
            if in_block_comment:
                if char == "*" and next_char == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_string:
                result.append(char)
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == string_delimiter:
                    in_string = False
                i += 1
                continue
            if char == "/" and next_char == "/":
                in_line_comment = True
                i += 2
                continue
            if char == "/" and next_char == "*":
                in_block_comment = True
                i += 2
                continue
            if char in {'"', "'"}:
                in_string = True
                string_delimiter = char
                result.append(char)
                i += 1
                continue
            result.append(char)
            i += 1
        return re.sub(r",(\s*[}\]])", r"\1", "".join(result))
