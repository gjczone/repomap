"""
Git 共变更热度分析。

被 overview、verify、impact 共用。
统计项目 git 历史中经常一起修改的文件对，用于识别隐式耦合。
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict, defaultdict

logger = logging.getLogger("repomap")
_co_change_load_failed: bool = False

_co_change_cache: OrderedDict[tuple[str, int], dict[tuple[str, str], int]] = (
    OrderedDict()
)
_co_change_lock = threading.Lock()
_MAX_CO_CHANGE_CACHE = 32  # 最多缓存 32 个项目的共变更数据


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
    project_root: str, file_a: str, file_b: str, since_days: int = 30
) -> int:
    """查询两个文件的 git 共变更次数（带缓存，公开接口）。"""
    cache = _get_or_load_co_change_cache(project_root, since_days)
    a, b = sorted([file_a, file_b])
    return cache.get((a, b), 0)


def get_co_change_neighbors(
    project_root: str,
    file_path: str,
    top_n: int = 5,
    since_days: int = 30,
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
    project_root: str, since_days: int = 30
) -> dict[tuple[str, str], int]:
    """统计项目中文件对的 git 共变更次数。"""
    global _co_change_load_failed
    from .git_backend import GitBackend

    scores: dict[tuple[str, str], int] = defaultdict(int)
    try:
        git = GitBackend(project_root)
        commit_groups = git.log_commits_grouped(since_days=since_days)
    except Exception:
        logger.warning("Failed to load co-change scores from git", exc_info=True)
        _co_change_load_failed = True
        return dict(scores)

    # 加载成功，清除之前的失败标志
    _co_change_load_failed = False

    for commit_files in commit_groups:
        if len(commit_files) > 1:
            for i in range(len(commit_files)):
                for j in range(i + 1, len(commit_files)):
                    a, b = sorted([commit_files[i], commit_files[j]])
                    scores[(a, b)] += 1

    return dict(scores)


def co_change_load_failed() -> bool:
    """检查 co-change 分析是否因 git 错误而失败。"""
    return _co_change_load_failed
