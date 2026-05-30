"""
Git 操作统一后端 — 优先使用 pygit2（libgit2 绑定），fallback 到 subprocess。

业务目的：消除 20+ 次 subprocess.run(["git", ...]) 的 fork 开销，
将每次 git 操作从 5-50ms 降到 <1ms，整体扫描提速 5-20×。

实现逻辑：
  - Pygit2Backend：基于 pygit2（libgit2 C 库），直接读写 .git 目录
  - SubprocessBackend：原有 subprocess 方式，作为 fallback
  - GitBackend：统一入口，自动选择后端

边界情况：
  - 非 git 仓库：所有方法返回空/None
  - pygit2 未安装：自动 fallback 到 subprocess
  - pygit2 版本不兼容：捕获异常后 fallback
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("repomap.git_backend")


def _format_git_timestamp(timestamp: int) -> str:
    """将 Unix 时间戳格式化为与 git --format=%ad 默认格式一致的字符串。

    例如: "Thu Jan  6 09:32:07 2022 -0500"
    """
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    # 转换为本地时区以匹配 git 默认行为
    local_dt = dt.astimezone()
    return local_dt.strftime("%a %b %d %H:%M:%S %Y %z")


_HAS_PYGIT2 = False
try:
    import pygit2

    _HAS_PYGIT2 = True
except ImportError:
    pygit2 = None  # type: ignore[assignment]


def _validate_file_path(project_root: str, file_path: str) -> str | None:
    """验证并规范化文件路径，防止路径遍历和参数注入。

    返回规范化后的相对路径字符串，无效时返回 None。
    """
    try:
        resolved = (Path(project_root) / file_path).resolve()
        root_resolved = Path(project_root).resolve()
        # Python 3.9+ 推荐用法，避免混合分隔符导致的误判
        if not resolved.is_relative_to(root_resolved):
            logger.warning(f"Path traversal attempt: {file_path}")
            return None
        return str(resolved.relative_to(root_resolved))
    except (ValueError, OSError):
        return None


def _validate_git_ref(ref: str) -> str | None:
    """验证 git ref 格式，返回清理后的 ref 或 None（无效时）。"""
    import re

    # 允许: HEAD, commit hash, branch name, tag, refs/...
    # 禁止: 以 - 开头, 包含 shell 元字符
    if not ref or ref.startswith("-"):
        return None
    # 基本格式检查: 只允许字母数字、/、.、-、_、~、^、:
    if not re.match(r"^[a-zA-Z0-9/.\-_~^:]+$", ref):
        return None
    return ref


class SubprocessBackend:
    """基于 subprocess 的 git 操作后端（原有实现）。"""

    @staticmethod
    def _run_git(
        args: list[str], cwd: str, timeout: int = 10
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    @staticmethod
    def rev_parse_head(project_root: str) -> str | None:
        try:
            r = SubprocessBackend._run_git(
                ["rev-parse", "HEAD"], project_root, timeout=5
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception as exc:
            logger.warning(f"git rev-parse HEAD failed: {exc}")
            return None

    @staticmethod
    def show_toplevel(project_root: str) -> str | None:
        try:
            r = SubprocessBackend._run_git(
                ["rev-parse", "--show-toplevel"], project_root, timeout=5
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception as exc:
            logger.warning(f"git rev-parse --show-toplevel failed: {exc}")
            return None

    @staticmethod
    def changed_files(project_root: str) -> list[str]:
        files: list[str] = []
        try:
            r = SubprocessBackend._run_git(
                ["diff", "--name-only", "HEAD"], project_root
            )
            if r.returncode == 0:
                files.extend(l for l in r.stdout.strip().splitlines() if l)
            elif r.returncode == 128:
                logger.warning(
                    f"git diff failed (rc={r.returncode}): {r.stderr.strip()[:100]}"
                )
        except Exception as exc:
            logger.warning(f"git diff --name-only failed: {exc}")
        try:
            r = SubprocessBackend._run_git(
                ["ls-files", "--others", "--exclude-standard"], project_root
            )
            if r.returncode == 0:
                files.extend(l for l in r.stdout.strip().splitlines() if l)
            elif r.returncode == 128:
                logger.warning(
                    f"git ls-files failed (rc={r.returncode}): {r.stderr.strip()[:100]}"
                )
        except Exception as exc:
            logger.warning(f"git ls-files failed: {exc}")
        return files

    @staticmethod
    def deleted_files(project_root: str) -> list[str]:
        try:
            r = SubprocessBackend._run_git(
                ["diff", "--name-only", "--diff-filter=D", "HEAD"], project_root
            )
            if r.returncode == 0:
                return [l for l in r.stdout.strip().splitlines() if l]
            logger.warning(
                f"git diff --diff-filter=D failed (rc={r.returncode}): {r.stderr.strip()[:100]}"
            )
            return []
        except Exception as exc:
            logger.warning(f"git deleted_files failed: {exc}")
            return []

    @staticmethod
    def diff_name_only(project_root: str, since: str | None = None) -> list[str]:
        try:
            args = ["diff", "--name-only"]
            if since:
                if since.startswith("-"):
                    return []
                if not _validate_git_ref(since):
                    return []
                args += [since, "HEAD"]
            r = SubprocessBackend._run_git(args, project_root)
            return (
                [l for l in r.stdout.strip().splitlines() if l]
                if r.returncode == 0
                else []
            )
        except Exception as exc:
            logger.warning(f"git diff --name-only failed: {exc}")
            return []

    @staticmethod
    def diff_cached_name_only(project_root: str) -> list[str]:
        try:
            r = SubprocessBackend._run_git(
                ["diff", "--cached", "--name-only"], project_root
            )
            return (
                [l for l in r.stdout.strip().splitlines() if l]
                if r.returncode == 0
                else []
            )
        except Exception as exc:
            logger.warning(f"git diff --cached --name-only failed: {exc}")
            return []

    @staticmethod
    def status_porcelain(project_root: str) -> list[str]:
        try:
            r = SubprocessBackend._run_git(["status", "--porcelain"], project_root)
            return (
                [l for l in r.stdout.strip().splitlines() if l]
                if r.returncode == 0
                else []
            )
        except Exception as exc:
            logger.warning(f"git status --porcelain failed: {exc}")
            return []

    @staticmethod
    def log_name_only(project_root: str, since: str = "90.days.ago") -> list[str]:
        try:
            r = SubprocessBackend._run_git(
                [
                    "log",
                    "--name-only",
                    "--pretty=format:",
                    f"--since={since}",
                    "--",
                    ".",
                ],
                project_root,
            )
            # 校验 returncode：git 命令失败时返回空列表，与"无变更文件"区分
            if r.returncode != 0:
                logger.debug(
                    f"git log --name-only failed (rc={r.returncode}): "
                    f"{r.stderr.strip()[:100]}"
                )
                return []
            return [l for l in r.stdout.strip().splitlines() if l]
        except Exception as exc:
            logger.warning(f"git log --name-only failed: {exc}")
            return []

    @staticmethod
    def diff_name_only_since(project_root: str, days: int = 30) -> list[str]:
        try:
            r = SubprocessBackend._run_git(
                ["diff", "--name-only", f"HEAD@{{{days}.days ago}}", "HEAD", "--", "."],
                project_root,
            )
            return (
                [l for l in r.stdout.strip().splitlines() if l]
                if r.returncode == 0
                else []
            )
        except Exception as exc:
            logger.warning(f"git diff --name-only --since failed: {exc}")
            return []

    @staticmethod
    def log_commits_grouped(project_root: str, since_days: int = 30) -> list[list[str]]:
        """返回按 commit 分组的文件列表，用于共变分析。每个子列表是一个 commit 修改的文件。"""
        try:
            r = SubprocessBackend._run_git(
                [
                    "log",
                    "--name-only",
                    "--pretty=format:",
                    f"--since={since_days}.days.ago",
                    "--",
                    ".",
                ],
                project_root,
                timeout=30,
            )
            if r.returncode != 0:
                return []
            groups: list[list[str]] = []
            current: list[str] = []
            for line in r.stdout.split("\n"):
                stripped = line.strip()
                if not stripped:
                    if current:
                        groups.append(current)
                        current = []
                else:
                    current.append(stripped)
            if current:
                groups.append(current)
            return groups
        except Exception as exc:
            logger.warning(f"git log --name-only (grouped) failed: {exc}")
            return []

    @staticmethod
    def blame_line(
        project_root: str, file_path: str, line: int
    ) -> dict[str, str] | None:
        safe_path = _validate_file_path(project_root, file_path)
        if safe_path is None:
            return None
        try:
            r = SubprocessBackend._run_git(
                ["blame", "-L", f"{line},{line}", "-p", safe_path],
                project_root,
                timeout=10,
            )
            if r.returncode != 0:
                return None
            output = r.stdout
            commit_hash = output.split()[0][:8] if output else "unknown"
            return {"commit": commit_hash}
        except Exception as exc:
            logger.warning(f"git blame failed for {file_path}:{line}: {exc}")
            return None

    @staticmethod
    def log_file_commits(
        project_root: str, file_path: str, limit: int = 20
    ) -> list[dict[str, str]]:
        safe_path = _validate_file_path(project_root, file_path)
        if safe_path is None:
            return []
        try:
            r = SubprocessBackend._run_git(
                [
                    "log",
                    "--follow",
                    f"-{limit}",
                    "--format=%H|%an|%ad|%s",
                    "--",
                    safe_path,
                ],
                project_root,
                timeout=10,
            )
            commits = []
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    if "|" in line:
                        parts = line.split("|", 3)
                        if len(parts) >= 4:
                            commits.append(
                                {
                                    "hash": parts[0][:8],
                                    "author": parts[1],
                                    "date": parts[2],
                                    "message": parts[3],
                                }
                            )
            return commits
        except Exception as exc:
            logger.warning(f"log_file_commits failed for {file_path}: {exc}")
            return []

    @staticmethod
    def file_authors(
        project_root: str, file_path: str, since_days: int = 365
    ) -> list[str]:
        safe_path = _validate_file_path(project_root, file_path)
        if safe_path is None:
            return []
        try:
            args = ["shortlog", "-sn"]
            if since_days > 0:
                args.extend(["--since", f"{since_days}.days.ago"])
            args.extend(["--", safe_path])
            r = SubprocessBackend._run_git(args, project_root, timeout=10)
            authors = []
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    parts = line.strip().split("\t", 1)
                    if len(parts) == 2:
                        authors.append(parts[1])
            return authors
        except Exception as exc:
            logger.warning(f"file_authors failed for {file_path}: {exc}")
            return []


class Pygit2Backend:
    """基于 pygit2（libgit2）的 git 操作后端，消除 fork 开销。"""

    @staticmethod
    def _validate_git_ref(ref: str) -> str | None:
        """Backward-compatible alias for the module-level _validate_git_ref."""
        return _validate_git_ref(ref)

    @staticmethod
    def _repo(project_root: str) -> Any | None:
        if not _HAS_PYGIT2:
            return None
        try:
            return pygit2.Repository(project_root)
        except Exception as exc1:
            try:
                tl = Pygit2Backend.show_toplevel(project_root)
                if tl:
                    return pygit2.Repository(tl)
            except Exception:
                logger.debug(
                    "pygit2 _repo fallback also failed; original: %s",
                    repr(exc1),
                    exc_info=True,
                )
            return None

    @staticmethod
    def rev_parse_head(project_root: str) -> str | None:
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return None
        try:
            return str(repo.head.target)
        except Exception:
            logger.debug("pygit2 rev_parse_head failed", exc_info=True)
            return None

    @staticmethod
    def show_toplevel(project_root: str) -> str | None:
        if not _HAS_PYGIT2:
            return None
        try:
            repo = pygit2.discover_repository(project_root)
            if repo:
                return str(Path(repo).parent)
        except Exception:
            logger.warning("pygit2 show_toplevel failed", exc_info=True)
            return None
        return None

    @staticmethod
    def changed_files(project_root: str) -> list[str]:
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            files: list[str] = []
            status = repo.status()
            workdir = repo.workdir
            if workdir is None:
                return []
            for fp, flags in status.items():
                if flags & pygit2.GIT_STATUS_IGNORED:
                    continue
                is_changed = bool(
                    flags
                    & (pygit2.GIT_STATUS_INDEX_MODIFIED | pygit2.GIT_STATUS_WT_MODIFIED)
                )
                is_new_wt = bool(flags & pygit2.GIT_STATUS_WT_NEW)
                is_new_idx = bool(flags & pygit2.GIT_STATUS_INDEX_NEW)
                if is_changed or is_new_wt or is_new_idx:
                    files.append(fp)
            return files
        except Exception:
            logger.warning("pygit2 changed_files failed", exc_info=True)
            return []

    @staticmethod
    def deleted_files(project_root: str) -> list[str]:
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            files: list[str] = []
            status = repo.status()
            for fp, flags in status.items():
                is_deleted = bool(
                    flags
                    & (pygit2.GIT_STATUS_INDEX_DELETED | pygit2.GIT_STATUS_WT_DELETED)
                )
                if is_deleted:
                    files.append(fp)
            return files
        except Exception:
            logger.warning("pygit2 deleted_files failed", exc_info=True)
            return []

    @staticmethod
    def diff_name_only(project_root: str, since: str | None = None) -> list[str]:
        if since and "days.ago" in since:
            return SubprocessBackend.diff_name_only(project_root, since)
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            if since and repo.head.target:
                # 验证 git ref 格式
                validated = _validate_git_ref(
                    since.split("..")[0] if ".." in since else since
                )
                if not validated:
                    return []
                since_commit = repo.revparse_single(validated)
                head = repo.get(repo.head.target)
                diff = repo.diff(since_commit.tree, head.tree)
            else:
                diff = repo.diff_index_to_workdir()
            return list({p.new_file.path for p in diff.deltas if p.new_file.path})
        except Exception:
            logger.warning("pygit2 diff_name_only failed", exc_info=True)
            return []

    @staticmethod
    def diff_cached_name_only(project_root: str) -> list[str]:
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            diff = repo.diff_cached()
            return list({p.new_file.path for p in diff.deltas if p.new_file.path})
        except Exception:
            logger.warning("pygit2 diff_cached_name_only failed", exc_info=True)
            return []

    @staticmethod
    def status_porcelain(project_root: str) -> list[str]:
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            lines: list[str] = []
            status = repo.status()
            for fp, flags in status.items():
                if flags & pygit2.GIT_STATUS_IGNORED:
                    lines.append(f"!! {fp}")
                    continue
                if flags & pygit2.GIT_STATUS_WT_NEW and not (
                    flags
                    & (
                        pygit2.GIT_STATUS_INDEX_NEW
                        | pygit2.GIT_STATUS_INDEX_MODIFIED
                        | pygit2.GIT_STATUS_INDEX_DELETED
                    )
                ):
                    lines.append(f"?? {fp}")
                    continue
                x = " "
                if flags & pygit2.GIT_STATUS_INDEX_NEW:
                    x = "A"
                elif flags & pygit2.GIT_STATUS_INDEX_MODIFIED:
                    x = "M"
                elif flags & pygit2.GIT_STATUS_INDEX_DELETED:
                    x = "D"
                elif flags & pygit2.GIT_STATUS_INDEX_RENAMED:
                    # pygit2 repo.status() 不提供旧路径，只能输出 R NEW_PATH
                    x = "R"
                elif flags & pygit2.GIT_STATUS_INDEX_TYPECHANGE:
                    x = "T"
                y = " "
                if flags & pygit2.GIT_STATUS_WT_MODIFIED:
                    y = "M"
                elif flags & pygit2.GIT_STATUS_WT_DELETED:
                    y = "D"
                elif flags & pygit2.GIT_STATUS_WT_RENAMED:
                    # pygit2 repo.status() 不提供旧路径，只能输出 R NEW_PATH
                    y = "R"
                elif flags & pygit2.GIT_STATUS_WT_TYPECHANGE:
                    y = "T"
                elif flags & pygit2.GIT_STATUS_WT_NEW:
                    y = "?"
                if flags & pygit2.GIT_STATUS_CONFLICTED:
                    x, y = "U", "U"
                lines.append(f"{x}{y} {fp}")
            return lines
        except Exception:
            logger.warning("pygit2 status_porcelain failed", exc_info=True)
            return []

    @staticmethod
    def log_name_only(project_root: str, since: str = "90.days.ago") -> list[str]:
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            files: set[str] = set()
            import time as _time

            now = _time.time()
            import re as _re

            _m = _re.match(r"(\d+)\.days\.ago", since)
            since_days = int(_m.group(1)) if _m else 90
            since_seconds = int(now) - since_days * 86400
            max_commits = 5000
            walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TIME)
            for i, commit in enumerate(walker):
                if i >= max_commits:
                    logger.warning(
                        "log_name_only: hit max_commits=%d limit", max_commits
                    )
                    break
                if commit.commit_time < since_seconds:
                    break
                if not commit.parents:
                    diff = commit.tree.diff_to_tree(swap=True)
                else:
                    diff = repo.diff(commit.parents[0].tree, commit.tree)
                for d in diff.deltas:
                    if d.new_file.path:
                        files.add(d.new_file.path)
            return list(files)
        except Exception:
            logger.warning("pygit2 log_name_only failed", exc_info=True)
            return []

    @staticmethod
    def log_commits_grouped(project_root: str, since_days: int = 30) -> list[list[str]]:
        """返回按 commit 分组的文件列表，用于共变分析。"""
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            import time as _time

            since_seconds = int(_time.time()) - since_days * 86400
            groups: list[list[str]] = []
            max_commits = 5000
            walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TIME)
            for i, commit in enumerate(walker):
                if i >= max_commits:
                    logger.warning(
                        "log_commits_grouped: hit max_commits=%d limit", max_commits
                    )
                    break
                if commit.commit_time < since_seconds:
                    break
                if not commit.parents:
                    diff = commit.tree.diff_to_tree(swap=True)
                else:
                    diff = repo.diff(commit.parents[0].tree, commit.tree)
                commit_files = [d.new_file.path for d in diff.deltas if d.new_file.path]
                if commit_files:
                    groups.append(commit_files)
            return groups
        except Exception:
            logger.warning("pygit2 log_commits_grouped failed", exc_info=True)
            return []

    @staticmethod
    def diff_name_only_since(project_root: str, days: int = 30) -> list[str]:
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            import time as _time

            since_seconds = int(_time.time()) - days * 86400
            files: set[str] = set()
            max_commits = 5000
            walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TIME)
            for i, commit in enumerate(walker):
                if i >= max_commits:
                    logger.warning(
                        "diff_name_only_since: hit max_commits=%d limit", max_commits
                    )
                    break
                if commit.commit_time < since_seconds:
                    break
                if not commit.parents:
                    diff = commit.tree.diff_to_tree(swap=True)
                else:
                    diff = repo.diff(commit.parents[0].tree, commit.tree)
                for d in diff.deltas:
                    if d.new_file.path:
                        files.add(d.new_file.path)
            return list(files)
        except Exception:
            logger.warning("pygit2 diff_name_only_since failed", exc_info=True)
            return []

    @staticmethod
    def blame_line(
        project_root: str, file_path: str, line: int
    ) -> dict[str, str] | None:
        # 路径验证：与 SubprocessBackend 保持一致的安全检查
        safe_path = _validate_file_path(project_root, file_path)
        if safe_path is None:
            return None
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return None
        try:
            blame = repo.blame(safe_path)
            for hunk in blame:
                start = hunk.final_start_line_number
                end = start + hunk.lines_in_hunk - 1
                if start <= line <= end:
                    return {"commit": str(hunk.final_commit_id)[:8]}
        except Exception:
            logger.debug(
                "pygit2 blame_line failed for %s:%d", file_path, line, exc_info=True
            )
            pass
        return None

    @staticmethod
    def log_file_commits(
        project_root: str, file_path: str, limit: int = 20
    ) -> list[dict[str, str]]:
        # 路径验证：与 SubprocessBackend 保持一致的安全检查
        safe_path = _validate_file_path(project_root, file_path)
        if safe_path is None:
            return []
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            commits: list[dict[str, str]] = []
            max_commits = 5000
            walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TIME)
            for i, commit in enumerate(walker):
                if i >= max_commits:
                    logger.warning(
                        "log_file_commits: hit max_commits=%d limit for %s",
                        max_commits,
                        file_path,
                    )
                    break
                if len(commits) >= limit:
                    break
                if not commit.parents:
                    diff = commit.tree.diff_to_tree(swap=True)
                    for d in diff.deltas:
                        if d.new_file.path == safe_path or d.old_file.path == safe_path:
                            commits.append(
                                {
                                    "hash": str(commit.id)[:8],
                                    "author": commit.author.name,
                                    "date": _format_git_timestamp(commit.commit_time),
                                    "message": commit.message.split("\n")[0],
                                }
                            )
                            break
                else:
                    for parent in commit.parents:
                        diff = repo.diff(parent.tree, commit.tree)
                        for d in diff.deltas:
                            if (
                                d.new_file.path == safe_path
                                or d.old_file.path == safe_path
                            ):
                                commits.append(
                                    {
                                        "hash": str(commit.id)[:8],
                                        "author": commit.author.name,
                                        "date": _format_git_timestamp(
                                            commit.commit_time
                                        ),
                                        "message": commit.message.split("\n")[0],
                                    }
                                )
                                break
                        else:
                            continue
                        break
            return commits
        except Exception as exc:
            logger.warning(f"log_file_commits failed for {file_path}: {exc}")
            return []

    @staticmethod
    def file_authors(
        project_root: str, file_path: str, since_days: int = 365
    ) -> list[str]:
        # 路径验证：与 SubprocessBackend 保持一致的安全检查
        safe_path = _validate_file_path(project_root, file_path)
        if safe_path is None:
            return []
        repo = Pygit2Backend._repo(project_root)
        if repo is None:
            return []
        try:
            from datetime import datetime, timezone, timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
            cutoff_timestamp = int(cutoff.timestamp())

            author_counts: dict[str, int] = {}
            max_commits = 5000
            walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TIME)
            for i, commit in enumerate(walker):
                if i >= max_commits:
                    logger.warning(
                        "file_authors: hit max_commits=%d limit", max_commits
                    )
                    break
                # 超过时间限制则停止遍历
                if commit.commit_time < cutoff_timestamp:
                    break
                touched = False
                if not commit.parents:
                    diff = commit.tree.diff_to_tree(swap=True)
                else:
                    diff = repo.diff(commit.parents[0].tree, commit.tree)
                for d in diff.deltas:
                    if d.new_file.path == safe_path or d.old_file.path == safe_path:
                        touched = True
                        break
                if touched:
                    name = commit.author.name
                    author_counts[name] = author_counts.get(name, 0) + 1
            sorted_authors = sorted(
                author_counts.items(), key=lambda x: x[1], reverse=True
            )
            return [a[0] for a in sorted_authors]
        except Exception as exc:
            logger.warning(f"file_authors failed for {file_path}: {exc}")
            return []


class GitBackend:
    """Git 操作统一入口 — 优先 pygit2，fallback 到 subprocess。"""

    def __init__(self, project_root: str) -> None:
        self.project_root = project_root
        if _HAS_PYGIT2:
            try:
                repo = Pygit2Backend._repo(project_root)
                if repo is not None:
                    self._backend: type[Pygit2Backend] | type[SubprocessBackend] = (
                        Pygit2Backend
                    )
                    logger.debug("Using pygit2 backend for %s", project_root)
                    return
            except Exception:
                logger.debug(
                    "pygit2 backend init failed, falling back to subprocess",
                    exc_info=True,
                )
                pass
        self._backend = SubprocessBackend
        logger.debug("Using subprocess backend for %s", project_root)

    def changed_files(self) -> list[str]:
        try:
            return self._backend.changed_files(self.project_root)
        except Exception:
            logger.warning(
                "pygit2 changed_files failed, falling back to subprocess",
                exc_info=True,
            )
            return SubprocessBackend.changed_files(self.project_root)

    @property
    def backend_name(self) -> str:
        return "pygit2" if self._backend is Pygit2Backend else "subprocess"

    def rev_parse_head(self) -> str | None:
        return self._backend.rev_parse_head(self.project_root)

    def show_toplevel(self) -> str | None:
        return self._backend.show_toplevel(self.project_root)

    def diff_name_only(self, since: str | None = None) -> list[str]:
        try:
            return self._backend.diff_name_only(self.project_root, since)
        except Exception:
            logger.warning(
                "pygit2 diff_name_only failed, falling back to subprocess",
                exc_info=True,
            )
            return SubprocessBackend.diff_name_only(self.project_root, since)

    def deleted_files(self) -> list[str]:
        return self._backend.deleted_files(self.project_root)

    def diff_cached_name_only(self) -> list[str]:
        return self._backend.diff_cached_name_only(self.project_root)

    def status_porcelain(self) -> list[str]:
        return self._backend.status_porcelain(self.project_root)

    def log_name_only(self, since: str = "90.days.ago") -> list[str]:
        return self._backend.log_name_only(self.project_root, since)

    def diff_name_only_since(self, days: int = 30) -> list[str]:
        return self._backend.diff_name_only_since(self.project_root, days)

    def blame_line(self, file_path: str, line: int) -> dict[str, str] | None:
        return self._backend.blame_line(self.project_root, file_path, line)

    def log_file_commits(self, file_path: str, limit: int = 20) -> list[dict[str, str]]:
        return self._backend.log_file_commits(self.project_root, file_path, limit)

    def file_authors(self, file_path: str) -> list[str]:
        return self._backend.file_authors(self.project_root, file_path)

    def log_commits_grouped(self, since_days: int = 30) -> list[list[str]]:
        return self._backend.log_commits_grouped(self.project_root, since_days)
