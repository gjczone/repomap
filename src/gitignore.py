from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

import pathspec

# 模块级缓存：project_root → GitignoreParser（LRU 淘汰，最多 128 个条目）
_cache: OrderedDict[str, "GitignoreParser"] = OrderedDict()
_MAX_CACHE_SIZE = 128


def get_gitignore(
    project_root: str | Path, extra_patterns: list[str] | None = None
) -> "GitignoreParser":
    root = str(Path(project_root).resolve())
    key = f"{root}\x00{tuple(extra_patterns or [])}"
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    if len(_cache) >= _MAX_CACHE_SIZE:
        _cache.popitem(last=False)
    _cache[key] = GitignoreParser(root, extra_patterns=extra_patterns)
    return _cache[key]


BUILTIN_IGNORE_PATTERNS = [
    # 版本控制
    ".git/",
    ".hg/",
    ".svn/",
    # 依赖目录
    "node_modules/",
    ".venv/",
    "venv/",
    "env/",
    "ENV/",
    "site-packages/",
    "__pypackages__/",
    ".yarn/",
    ".pnpm-store/",
    # Python 编译产物
    "__pycache__/",
    "*.py[cod]",
    # 缓存
    ".cache/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".nox/",
    ".tox/",
    ".next/",
    ".nuxt/",
    ".svelte-kit/",
    ".turbo/",
    ".parcel-cache/",
    # IDE
    ".idea/",
    ".vscode/",
    # 构建产物
    "build/",
    "coverage/",
    "dist/",
    "target/",
    # 第三方库目录
    "monaco-editor/",
    "monaco/",
    "vendor/",
    "third_party/",
    "third-party/",
    "libs/",
    "external/",
    # lock 文件
    "package-lock.json",
    "npm-shrinkwrap.json",
    "bun.lock",
    "bun.lockb",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    # minified
    "*.min.js",
]


class GitignoreParser:
    """递归收集项目 .gitignore 文件，统一为 pathspec 匹配器。

    内置规则（BUILTIN_IGNORE_PATTERNS）始终生效；项目 .gitignore 规则
    按所在目录的层级叠加，子目录规则覆盖父目录。
    """

    def __init__(
        self, project_root: str | Path, extra_patterns: list[str] | None = None
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._specs: list[tuple[Path, pathspec.PathSpec]] = []
        self._base_spec: pathspec.PathSpec = pathspec.PathSpec.from_lines(
            "gitignore", list(BUILTIN_IGNORE_PATTERNS) + (extra_patterns or [])
        )
        self._walk_and_load()

    def _walk_and_load(self) -> None:
        """递归遍历项目目录，加载所有 .gitignore 文件。

        使用 os.scandir 替代 os.walk：在进入子目录之前检查 ignore spec，
        避免遍历 node_modules 等大型忽略目录的内容。
        """
        self._scan_dir(self.project_root, Path("."))

    def _scan_dir(self, root_path: Path, rel_path: Path) -> None:
        try:
            entries = list(os.scandir(root_path))
        except PermissionError:
            return

        dirs = []
        for entry in entries:
            if entry.is_dir():
                dirs.append(entry)
            elif entry.name == ".gitignore" and entry.is_file():
                self._load_gitignore(Path(entry.path), rel_path)

        for d in dirs:
            child_rel = rel_path / d.name
            if self._match_spec(self._base_spec, child_rel.as_posix()):
                continue
            if d.name == ".git":
                continue
            self._scan_dir(Path(d.path), child_rel)

    def _load_gitignore(self, path: Path, rel_dir: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if not lines:
            return
        spec = pathspec.PathSpec.from_lines("gitignore", lines)
        self._specs.append((rel_dir, spec))

    def is_ignored(self, file_path: str | Path) -> bool:
        """判断一个项目相对路径是否应被忽略。"""
        path = Path(file_path)
        path_str = path.as_posix()
        # 1) 内置 base spec
        if self._match_spec(self._base_spec, path_str):
            return True
        # 2) 各层级 .gitignore（只应用祖先目录的规则）
        for rel_dir, spec in self._specs:
            if not self._is_ancestor(rel_dir, path):
                continue
            spec_path = (
                path.relative_to(rel_dir).as_posix()
                if rel_dir.as_posix() != "."
                else path_str
            )
            if self._match_spec(spec, spec_path):
                return True
        return False

    @staticmethod
    def _match_spec(spec: pathspec.PathSpec, path: str) -> bool:
        return spec.match_file(path)

    @staticmethod
    def _is_ancestor(rel_dir: Path, file_path: Path) -> bool:
        if rel_dir.as_posix() == ".":
            return True
        dir_parts = rel_dir.parts
        file_parts = file_path.parts
        if len(dir_parts) > len(file_parts):
            return False
        return file_parts[: len(dir_parts)] == dir_parts
