"""
Git 共变更热度分析。

被 overview、verify、impact 共用。
统计项目 git 历史中经常一起修改的文件对，用于识别隐式耦合。
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict, defaultdict
from pathlib import Path

logger = logging.getLogger("repomap")
_co_change_load_failed: bool = False

_co_change_cache: OrderedDict[tuple[str, int], dict[tuple[str, str], int]] = (
    OrderedDict()
)
_co_change_lock = threading.Lock()
_MAX_CO_CHANGE_CACHE = 32  # 最多缓存 32 个项目的共变更数据

# 项目大小限制：超过此大小的项目跳过 co-change 分析
_MAX_PROJECT_FILES = 5000  # 最大文件数
_DEFAULT_SINCE_DAYS = 7  # 默认分析最近7天（而非30天）


def _count_source_files(project_root: str) -> int:
    """快速统计项目中的源文件数量（排除 node_modules 等）。"""
    skip_dirs = {
        "node_modules",
        ".git",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".venv",
        "venv",
    }
    count = 0
    try:
        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            count += len(files)
            if count > _MAX_PROJECT_FILES:
                return count  # 提前返回，避免遍历整个项目
    except Exception:
        pass
    return count


def _get_or_load_co_change_cache(
    project_root: str, since_days: int
) -> dict[tuple[str, str], int]:
    """获取或加载共变更缓存（消除 get_co_change_score 和 get_co_change_neighbors 的重复逻辑）。"""
    cache_key = (project_root, since_days)
    with _co_change_lock:
        cache = _co_change_cache.get(cache_key)
        if cache is not None:
            _co_change_cache.move_to_end(cache_key)
        else:
            cache = _load_co_change_scores(project_root, since_days=since_days)
            # 仅在加载成功时缓存（失败时 _co_change_load_failed 为 True）
            if not _co_change_load_failed:
                if len(_co_change_cache) >= _MAX_CO_CHANGE_CACHE:
                    _co_change_cache.popitem(last=False)
                _co_change_cache[cache_key] = cache
    return cache


def get_co_change_score(
    project_root: str, file_a: str, file_b: str, since_days: int = _DEFAULT_SINCE_DAYS
) -> int:
    """查询两个文件的 git 共变更次数（带缓存，公开接口）。"""
    cache = _get_or_load_co_change_cache(project_root, since_days)
    a, b = sorted([file_a, file_b])
    return cache.get((a, b), 0)


def get_co_change_neighbors(
    project_root: str,
    file_path: str,
    top_n: int = 5,
    since_days: int = _DEFAULT_SINCE_DAYS,
) -> list[tuple[str, int]]:
    """返回与指定文件共变频率最高的文件列表（降序）。

    用途：识别隐式耦合——两个文件在 git 历史中频繁一起修改，
    即使代码上没有显式依赖，也可能存在隐含关联。
    """
    cache = _get_or_load_co_change_cache(project_root, since_days)
    neighbors: dict[str, int] = {}
    for (a, b), count in cache.items():
        if a == file_path:
            neighbors[b] = count
        elif b == file_path:
            neighbors[a] = count
    return sorted(neighbors.items(), key=lambda x: -x[1])[:top_n]


def _load_co_change_scores(
    project_root: str, since_days: int = _DEFAULT_SINCE_DAYS
) -> dict[tuple[str, str], int]:
    """统计项目中文件对的 git 共变更次数。"""
    global _co_change_load_failed
    from .git_backend import GitBackend

    scores: dict[tuple[str, str], int] = defaultdict(int)

    # 大项目跳过 co-change 分析，避免超时
    file_count = _count_source_files(project_root)
    if file_count > _MAX_PROJECT_FILES:
        logger.info(
            "Skipping co-change analysis: project has %d files (limit %d)",
            file_count,
            _MAX_PROJECT_FILES,
        )
        _co_change_load_failed = True
        return dict(scores)

    try:
        git = GitBackend(project_root)
        commit_groups = git.log_commits_grouped(since_days=since_days)
    except Exception:
        logger.warning("Failed to load co-change scores from git", exc_info=True)
        _co_change_load_failed = True
        return dict(scores)

    # 加载成功，清除之前的失败标志
    _co_change_load_failed = False

    # 限制处理的提交数量，避免大型项目超时
    max_commits = 500
    if len(commit_groups) > max_commits:
        logger.info(
            "Limiting co-change analysis to %d commits (had %d)",
            max_commits,
            len(commit_groups),
        )
        commit_groups = commit_groups[:max_commits]

    for commit_files in commit_groups:
        if len(commit_files) > 1:
            # 限制每个提交的文件数量，避免组合爆炸
            if len(commit_files) > 50:
                commit_files = commit_files[:50]
            for i in range(len(commit_files)):
                for j in range(i + 1, len(commit_files)):
                    a, b = sorted([commit_files[i], commit_files[j]])
                    scores[(a, b)] += 1

    return dict(scores)


def co_change_load_failed() -> bool:
    """检查 co-change 分析是否因 git 错误而失败。"""
    return _co_change_load_failed
