from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.git_backend import GitBackend, Pygit2Backend, SubprocessBackend, _HAS_PYGIT2


def _mock_completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.returncode = returncode
    return cp


# ── TestGitBackend ──────────────────────────────────────────────────


class TestGitBackend:
    def test_selects_pygit2_when_available_and_repo_ok(self) -> None:
        with (
            patch("src.git_backend._HAS_PYGIT2", True),
            patch.object(Pygit2Backend, "_repo", return_value=MagicMock()),
        ):
            gb = GitBackend("/fake/project")
            assert gb.backend_name == "pygit2"

    def test_selects_subprocess_when_pygit2_unavailable(self) -> None:
        with patch("src.git_backend._HAS_PYGIT2", False):
            gb = GitBackend("/fake/project")
            assert gb.backend_name == "subprocess"

    def test_selects_subprocess_when_pygit2_repo_fails(self) -> None:
        with (
            patch("src.git_backend._HAS_PYGIT2", True),
            patch.object(Pygit2Backend, "_repo", return_value=None),
        ):
            gb = GitBackend("/fake/project")
            assert gb.backend_name == "subprocess"

    def test_selects_subprocess_when_pygit2_raises(self) -> None:
        with (
            patch("src.git_backend._HAS_PYGIT2", True),
            patch.object(Pygit2Backend, "_repo", side_effect=Exception("boom")),
        ):
            gb = GitBackend("/fake/project")
            assert gb.backend_name == "subprocess"

    def test_delegates_rev_parse_head(self) -> None:
        with (
            patch("src.git_backend._HAS_PYGIT2", False),
            patch.object(
                SubprocessBackend, "rev_parse_head", return_value="abc12345"
            ) as m,
        ):
            gb = GitBackend("/fake")
            assert gb.rev_parse_head() == "abc12345"
            m.assert_called_once_with("/fake")

    def test_delegates_status_porcelain(self) -> None:
        with (
            patch("src.git_backend._HAS_PYGIT2", False),
            patch.object(
                SubprocessBackend, "status_porcelain", return_value=["M file.py"]
            ) as m,
        ):
            gb = GitBackend("/fake")
            assert gb.status_porcelain() == ["M file.py"]
            m.assert_called_once_with("/fake")


# ── TestSubprocessBackend ───────────────────────────────────────────


class TestSubprocessBackend:
    ROOT = "/fake/project"

    # rev_parse_head

    def test_rev_parse_head_success(self) -> None:
        with patch.object(
            SubprocessBackend,
            "_run_git",
            return_value=_mock_completed("a1b2c3d4e5f6\n"),
        ):
            assert SubprocessBackend.rev_parse_head(self.ROOT) == "a1b2c3d4e5f6"

    def test_rev_parse_head_failure(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(returncode=128)
        ):
            assert SubprocessBackend.rev_parse_head(self.ROOT) is None

    def test_rev_parse_head_exception(self) -> None:
        with patch.object(SubprocessBackend, "_run_git", side_effect=Exception("err")):
            assert SubprocessBackend.rev_parse_head(self.ROOT) is None

    # show_toplevel

    def test_show_toplevel_success(self) -> None:
        with patch.object(
            SubprocessBackend,
            "_run_git",
            return_value=_mock_completed("/home/user/repo\n"),
        ):
            assert SubprocessBackend.show_toplevel(self.ROOT) == "/home/user/repo"

    def test_show_toplevel_failure(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(returncode=128)
        ):
            assert SubprocessBackend.show_toplevel(self.ROOT) is None

    # changed_files

    def test_changed_files_diff_and_untracked(self) -> None:
        diff_result = _mock_completed("src/a.py\nsrc/b.py\n")
        ls_result = _mock_completed("src/c.py\n")
        with patch.object(
            SubprocessBackend, "_run_git", side_effect=[diff_result, ls_result]
        ):
            files = SubprocessBackend.changed_files(self.ROOT)
        assert files == ["src/a.py", "src/b.py", "src/c.py"]

    def test_changed_files_empty(self) -> None:
        r = _mock_completed("")
        with patch.object(SubprocessBackend, "_run_git", side_effect=[r, r]):
            assert SubprocessBackend.changed_files(self.ROOT) == []

    def test_changed_files_first_fails_second_ok(self) -> None:
        fail = _mock_completed(returncode=128)
        ok = _mock_completed("new.py\n")
        with patch.object(SubprocessBackend, "_run_git", side_effect=[fail, ok]):
            assert SubprocessBackend.changed_files(self.ROOT) == ["new.py"]

    # deleted_files

    def test_deleted_files(self) -> None:
        with patch.object(
            SubprocessBackend,
            "_run_git",
            return_value=_mock_completed("old.py\ngone.py\n"),
        ):
            assert SubprocessBackend.deleted_files(self.ROOT) == ["old.py", "gone.py"]

    def test_deleted_files_none(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("")
        ):
            assert SubprocessBackend.deleted_files(self.ROOT) == []

    # diff_name_only

    def test_diff_name_only_without_since(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("a.py\nb.py\n")
        ):
            assert SubprocessBackend.diff_name_only(self.ROOT) == ["a.py", "b.py"]

    def test_diff_name_only_with_since(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("c.py\n")
        ) as m:
            result = SubprocessBackend.diff_name_only(self.ROOT, since="v1.0")
            assert result == ["c.py"]
            m.assert_called_once_with(
                ["diff", "--name-only", "v1.0", "HEAD"], self.ROOT
            )

    def test_diff_name_only_failure(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(returncode=128)
        ):
            assert SubprocessBackend.diff_name_only(self.ROOT) == []

    # diff_cached_name_only

    def test_diff_cached_name_only(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("staged.py\n")
        ):
            assert SubprocessBackend.diff_cached_name_only(self.ROOT) == ["staged.py"]

    def test_diff_cached_name_only_empty(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("")
        ):
            assert SubprocessBackend.diff_cached_name_only(self.ROOT) == []

    # status_porcelain

    def test_status_porcelain_various_codes(self) -> None:
        output = "M  modified.py\nA  added.py\n D deleted.py\n?? untracked.py\n!! ignored.py\n"
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(output)
        ):
            lines = SubprocessBackend.status_porcelain(self.ROOT)
        assert "M  modified.py" in lines
        assert "A  added.py" in lines
        assert " D deleted.py" in lines
        assert "?? untracked.py" in lines
        assert "!! ignored.py" in lines

    def test_status_porcelain_empty(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("")
        ):
            assert SubprocessBackend.status_porcelain(self.ROOT) == []

    # log_name_only

    def test_log_name_only_default_since(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("a.py\nb.py\n")
        ) as m:
            result = SubprocessBackend.log_name_only(self.ROOT)
            assert "a.py" in result
            m.assert_called_once_with(
                [
                    "log",
                    "--name-only",
                    "--pretty=format:",
                    "--since=90.days.ago",
                    "--",
                    ".",
                ],
                self.ROOT,
            )

    def test_log_name_only_custom_since(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("c.py\n")
        ) as m:
            result = SubprocessBackend.log_name_only(self.ROOT, since="30.days.ago")
            assert result == ["c.py"]
            m.assert_called_once_with(
                [
                    "log",
                    "--name-only",
                    "--pretty=format:",
                    "--since=30.days.ago",
                    "--",
                    ".",
                ],
                self.ROOT,
            )

    # blame_line

    def test_blame_line_success(self) -> None:
        with patch.object(
            SubprocessBackend,
            "_run_git",
            return_value=_mock_completed("a1b2c3d4 some rest\n"),
        ):
            result = SubprocessBackend.blame_line(self.ROOT, "src/main.py", 10)
        assert result is not None
        assert result["commit"] == "a1b2c3d4"
        assert len(result["commit"]) == 8

    def test_blame_line_failure_returns_none(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(returncode=128)
        ):
            assert SubprocessBackend.blame_line(self.ROOT, "missing.py", 1) is None

    def test_blame_line_exception_returns_none(self) -> None:
        with patch.object(SubprocessBackend, "_run_git", side_effect=Exception("boom")):
            assert SubprocessBackend.blame_line(self.ROOT, "x.py", 1) is None

    # log_file_commits

    def test_log_file_commits_parsing(self) -> None:
        output = "a1b2c3d4e5f6|Alice|2024-01-01|feat: add feature\nb2c3d4e5f6g7|Bob|2024-01-02|fix: bug\n"
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(output)
        ):
            commits = SubprocessBackend.log_file_commits(self.ROOT, "src/main.py")
        assert len(commits) == 2
        assert commits[0]["hash"] == "a1b2c3d4"
        assert commits[0]["author"] == "Alice"
        assert commits[0]["message"] == "feat: add feature"
        assert commits[1]["hash"] == "b2c3d4e5"
        assert commits[1]["author"] == "Bob"

    def test_log_file_commits_empty(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("")
        ):
            assert SubprocessBackend.log_file_commits(self.ROOT, "none.py") == []

    # file_authors

    def test_file_authors_parsing(self) -> None:
        output = "  5\tAlice\n  3\tBob\n"
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(output)
        ):
            authors = SubprocessBackend.file_authors(self.ROOT, "src/main.py")
        assert authors == ["Alice", "Bob"]

    def test_file_authors_empty(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("")
        ):
            assert SubprocessBackend.file_authors(self.ROOT, "none.py") == []

    # log_commits_grouped

    def test_log_commits_grouped_by_empty_lines(self) -> None:
        output = "a.py\nb.py\n\nc.py\n"
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(output)
        ):
            groups = SubprocessBackend.log_commits_grouped(self.ROOT)
        assert groups == [["a.py", "b.py"], ["c.py"]]

    def test_log_commits_grouped_single_commit(self) -> None:
        output = "a.py\nb.py\n"
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(output)
        ):
            groups = SubprocessBackend.log_commits_grouped(self.ROOT)
        assert groups == [["a.py", "b.py"]]

    def test_log_commits_grouped_empty(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("")
        ):
            assert SubprocessBackend.log_commits_grouped(self.ROOT) == []

    # diff_name_only_since

    def test_diff_name_only_since_with_days(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed("a.py\nb.py\n")
        ) as m:
            result = SubprocessBackend.diff_name_only_since(self.ROOT, days=7)
            assert result == ["a.py", "b.py"]
            m.assert_called_once_with(
                ["diff", "--name-only", "HEAD@{7.days ago}", "HEAD", "--", "."],
                self.ROOT,
            )

    def test_diff_name_only_since_failure(self) -> None:
        with patch.object(
            SubprocessBackend, "_run_git", return_value=_mock_completed(returncode=128)
        ):
            assert SubprocessBackend.diff_name_only_since(self.ROOT) == []


# ── TestPygit2Backend ───────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_PYGIT2, reason="pygit2 未安装")
class TestPygit2Backend:
    ROOT = "/fake/project"

    def _make_mock_repo(self) -> MagicMock:
        return MagicMock()

    # _repo

    def test_repo_success(self) -> None:
        mock_repo = self._make_mock_repo()
        with (
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.Repository", return_value=mock_repo),
        ):
            assert Pygit2Backend._repo(self.ROOT) is mock_repo

    def test_repo_failure_returns_none(self) -> None:
        with (
            patch("src.git_backend._HAS_PYGIT2", True),
            patch(
                "src.git_backend.pygit2.Repository", side_effect=Exception("no repo")
            ),
        ):
            assert Pygit2Backend._repo(self.ROOT) is None

    def test_repo_no_pygit2_returns_none(self) -> None:
        with patch("src.git_backend._HAS_PYGIT2", False):
            assert Pygit2Backend._repo(self.ROOT) is None

    def test_repo_fallback_discover_repository(self) -> None:
        mock_repo = self._make_mock_repo()
        with (
            patch("src.git_backend._HAS_PYGIT2", True),
            patch(
                "src.git_backend.pygit2.Repository",
                side_effect=[Exception("not found"), mock_repo],
            ),
            patch.object(
                Pygit2Backend, "show_toplevel", return_value="/discovered/repo"
            ),
        ):
            result = Pygit2Backend._repo(self.ROOT)
            assert result is mock_repo

    def test_repo_fallback_discover_fails(self) -> None:
        with (
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.Repository", side_effect=Exception("no")),
            patch.object(Pygit2Backend, "show_toplevel", return_value=None),
        ):
            assert Pygit2Backend._repo(self.ROOT) is None

    # rev_parse_head

    def test_rev_parse_head_success(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.head.target = "a1b2c3d4e5f67890"
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            assert Pygit2Backend.rev_parse_head(self.ROOT) == "a1b2c3d4e5f67890"

    def test_rev_parse_head_no_repo(self) -> None:
        with patch.object(Pygit2Backend, "_repo", return_value=None):
            assert Pygit2Backend.rev_parse_head(self.ROOT) is None

    def test_rev_parse_head_exception(self) -> None:
        mock_repo = self._make_mock_repo()
        type(mock_repo.head).target = property(
            lambda self_: (_ for _ in ()).throw(Exception("no head"))
        )
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            assert Pygit2Backend.rev_parse_head(self.ROOT) is None

    # status_porcelain — 各状态标志组合

    def test_status_porcelain_index_new(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.status.return_value = {"new.py": 1}
        with (
            patch.object(Pygit2Backend, "_repo", return_value=mock_repo),
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_NEW", 1),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_MODIFIED", 256),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_NEW", 128),
            patch("src.git_backend.pygit2.GIT_STATUS_IGNORED", 16384),
            patch("src.git_backend.pygit2.GIT_STATUS_CONFLICTED", 32768),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_MODIFIED", 2),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_DELETED", 4),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_RENAMED", 8),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_TYPECHANGE", 16),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_DELETED", 512),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_RENAMED", 1024),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_TYPECHANGE", 2048),
        ):
            lines = Pygit2Backend.status_porcelain(self.ROOT)
        assert any("A " in l and "new.py" in l for l in lines)

    def test_status_porcelain_wt_modified(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.status.return_value = {"mod.py": 256}
        with (
            patch.object(Pygit2Backend, "_repo", return_value=mock_repo),
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_NEW", 1),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_MODIFIED", 2),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_DELETED", 4),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_RENAMED", 8),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_TYPECHANGE", 16),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_MODIFIED", 256),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_DELETED", 512),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_RENAMED", 1024),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_TYPECHANGE", 2048),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_NEW", 128),
            patch("src.git_backend.pygit2.GIT_STATUS_IGNORED", 16384),
            patch("src.git_backend.pygit2.GIT_STATUS_CONFLICTED", 32768),
        ):
            lines = Pygit2Backend.status_porcelain(self.ROOT)
        assert any(" M" in l and "mod.py" in l for l in lines)

    def test_status_porcelain_wt_new_untracked(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.status.return_value = {"untracked.py": 128}
        with (
            patch.object(Pygit2Backend, "_repo", return_value=mock_repo),
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_NEW", 1),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_MODIFIED", 2),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_DELETED", 4),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_RENAMED", 8),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_TYPECHANGE", 16),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_MODIFIED", 256),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_DELETED", 512),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_RENAMED", 1024),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_TYPECHANGE", 2048),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_NEW", 128),
            patch("src.git_backend.pygit2.GIT_STATUS_IGNORED", 16384),
            patch("src.git_backend.pygit2.GIT_STATUS_CONFLICTED", 32768),
        ):
            lines = Pygit2Backend.status_porcelain(self.ROOT)
        assert any("?? " in l and "untracked.py" in l for l in lines)

    def test_status_porcelain_ignored(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.status.return_value = {"build/": 16384}
        with (
            patch.object(Pygit2Backend, "_repo", return_value=mock_repo),
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_NEW", 1),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_MODIFIED", 2),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_DELETED", 4),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_RENAMED", 8),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_TYPECHANGE", 16),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_MODIFIED", 256),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_DELETED", 512),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_RENAMED", 1024),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_TYPECHANGE", 2048),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_NEW", 128),
            patch("src.git_backend.pygit2.GIT_STATUS_IGNORED", 16384),
            patch("src.git_backend.pygit2.GIT_STATUS_CONFLICTED", 32768),
        ):
            lines = Pygit2Backend.status_porcelain(self.ROOT)
        assert any("!! " in l and "build/" in l for l in lines)

    def test_status_porcelain_conflicted(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.status.return_value = {"conflict.py": 32768}
        with (
            patch.object(Pygit2Backend, "_repo", return_value=mock_repo),
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_NEW", 1),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_MODIFIED", 2),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_DELETED", 4),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_RENAMED", 8),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_TYPECHANGE", 16),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_MODIFIED", 256),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_DELETED", 512),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_RENAMED", 1024),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_TYPECHANGE", 2048),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_NEW", 128),
            patch("src.git_backend.pygit2.GIT_STATUS_IGNORED", 16384),
            patch("src.git_backend.pygit2.GIT_STATUS_CONFLICTED", 32768),
        ):
            lines = Pygit2Backend.status_porcelain(self.ROOT)
        assert any("UU" in l and "conflict.py" in l for l in lines)

    def test_status_porcelain_index_renamed(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.status.return_value = {"renamed_new.py": 8}
        with (
            patch.object(Pygit2Backend, "_repo", return_value=mock_repo),
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_NEW", 1),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_MODIFIED", 2),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_DELETED", 4),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_RENAMED", 8),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_TYPECHANGE", 16),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_MODIFIED", 256),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_DELETED", 512),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_RENAMED", 1024),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_TYPECHANGE", 2048),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_NEW", 128),
            patch("src.git_backend.pygit2.GIT_STATUS_IGNORED", 16384),
            patch("src.git_backend.pygit2.GIT_STATUS_CONFLICTED", 32768),
        ):
            lines = Pygit2Backend.status_porcelain(self.ROOT)
        assert any("R " in l and "renamed_new.py" in l for l in lines)

    def test_status_porcelain_wt_renamed(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.status.return_value = {"wt_renamed.py": 1024}
        with (
            patch.object(Pygit2Backend, "_repo", return_value=mock_repo),
            patch("src.git_backend._HAS_PYGIT2", True),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_NEW", 1),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_MODIFIED", 2),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_DELETED", 4),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_RENAMED", 8),
            patch("src.git_backend.pygit2.GIT_STATUS_INDEX_TYPECHANGE", 16),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_MODIFIED", 256),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_DELETED", 512),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_RENAMED", 1024),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_TYPECHANGE", 2048),
            patch("src.git_backend.pygit2.GIT_STATUS_WT_NEW", 128),
            patch("src.git_backend.pygit2.GIT_STATUS_IGNORED", 16384),
            patch("src.git_backend.pygit2.GIT_STATUS_CONFLICTED", 32768),
        ):
            lines = Pygit2Backend.status_porcelain(self.ROOT)
        assert any(" R" in l and "wt_renamed.py" in l for l in lines)

    def test_status_porcelain_no_repo(self) -> None:
        with patch.object(Pygit2Backend, "_repo", return_value=None):
            assert Pygit2Backend.status_porcelain(self.ROOT) == []

    # blame_line — 使用 repo.blame() API

    def test_blame_line_success(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_hunk = MagicMock()
        mock_hunk.final_start_line_number = 10
        mock_hunk.lines_in_hunk = 3
        mock_hunk.final_commit_id = "deadbeef12345678"
        mock_repo.blame.return_value = [mock_hunk]
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            result = Pygit2Backend.blame_line(self.ROOT, "src/main.py", 11)
        assert result is not None
        assert result["commit"] == "deadbeef"
        assert len(result["commit"]) == 8

    def test_blame_line_line_not_in_hunk(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_hunk = MagicMock()
        mock_hunk.final_start_line_number = 1
        mock_hunk.lines_in_hunk = 5
        mock_hunk.final_commit_id = "abc1234567890"
        mock_repo.blame.return_value = [mock_hunk]
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            result = Pygit2Backend.blame_line(self.ROOT, "src/main.py", 100)
        assert result is None

    def test_blame_line_no_repo(self) -> None:
        with patch.object(Pygit2Backend, "_repo", return_value=None):
            assert Pygit2Backend.blame_line(self.ROOT, "x.py", 1) is None

    def test_blame_line_exception(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_repo.blame.side_effect = Exception("blame failed")
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            assert Pygit2Backend.blame_line(self.ROOT, "x.py", 1) is None

    def test_blame_line_hunk_boundary(self) -> None:
        mock_repo = self._make_mock_repo()
        mock_hunk = MagicMock()
        mock_hunk.final_start_line_number = 5
        mock_hunk.lines_in_hunk = 10
        mock_hunk.final_commit_id = "cafebabecafebabe"
        mock_repo.blame.return_value = [mock_hunk]
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            result_start = Pygit2Backend.blame_line(self.ROOT, "x.py", 5)
            result_end = Pygit2Backend.blame_line(self.ROOT, "x.py", 14)
        assert result_start is not None and result_start["commit"] == "cafebabe"
        assert result_end is not None and result_end["commit"] == "cafebabe"


# ── 契约一致性测试 ─────────────────────────────────────────────────


class TestContractConsistency:
    ROOT = "/fake/project"

    def test_blame_line_subprocess_returns_8char_hash(self) -> None:
        with patch.object(
            SubprocessBackend,
            "_run_git",
            return_value=_mock_completed("a1b2c3d4e5f6 rest\n"),
        ):
            result = SubprocessBackend.blame_line(self.ROOT, "x.py", 1)
        assert result is not None
        assert len(result["commit"]) == 8

    def test_blame_line_pygit2_returns_8char_hash(self) -> None:
        mock_repo = MagicMock()
        mock_hunk = MagicMock()
        mock_hunk.final_start_line_number = 1
        mock_hunk.lines_in_hunk = 1
        mock_hunk.final_commit_id = "a1b2c3d4e5f67890"
        mock_repo.blame.return_value = [mock_hunk]
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            result = Pygit2Backend.blame_line(self.ROOT, "x.py", 1)
        assert result is not None
        assert len(result["commit"]) == 8

    def test_blame_line_both_backends_same_key(self) -> None:
        with patch.object(
            SubprocessBackend,
            "_run_git",
            return_value=_mock_completed("a1b2c3d4 rest\n"),
        ):
            sub_result = SubprocessBackend.blame_line(self.ROOT, "x.py", 1)
        mock_repo = MagicMock()
        mock_hunk = MagicMock()
        mock_hunk.final_start_line_number = 1
        mock_hunk.lines_in_hunk = 1
        mock_hunk.final_commit_id = "a1b2c3d4e5f67890"
        mock_repo.blame.return_value = [mock_hunk]
        with patch.object(Pygit2Backend, "_repo", return_value=mock_repo):
            pygit_result = Pygit2Backend.blame_line(self.ROOT, "x.py", 1)
        assert sub_result is not None and pygit_result is not None
        assert set(sub_result.keys()) == set(pygit_result.keys())
        assert "commit" in sub_result and "commit" in pygit_result


class TestGitRefValidation:
    """P2-13: git ref format validation."""

    def test_validate_git_ref_accepts_valid_refs(self) -> None:
        """Valid git refs should be accepted."""
        assert Pygit2Backend._validate_git_ref("HEAD") == "HEAD"
        assert Pygit2Backend._validate_git_ref("main") == "main"
        assert Pygit2Backend._validate_git_ref("v1.0.0") == "v1.0.0"
        assert Pygit2Backend._validate_git_ref("abc123") == "abc123"
        assert Pygit2Backend._validate_git_ref("refs/heads/main") == "refs/heads/main"
        assert Pygit2Backend._validate_git_ref("HEAD~3") == "HEAD~3"
        assert Pygit2Backend._validate_git_ref("HEAD^2") == "HEAD^2"

    def test_validate_git_ref_rejects_dangerous_refs(self) -> None:
        """Dangerous git refs should be rejected."""
        assert Pygit2Backend._validate_git_ref("") is None
        assert Pygit2Backend._validate_git_ref("-f") is None
        assert Pygit2Backend._validate_git_ref("--force") is None
        assert Pygit2Backend._validate_git_ref("HEAD; rm -rf /") is None
        assert Pygit2Backend._validate_git_ref("HEAD$(cmd)") is None
        assert Pygit2Backend._validate_git_ref("HEAD`cmd`") is None
