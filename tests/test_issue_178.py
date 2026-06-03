"""Tests for issue #178: query --query no-match 时不应 fallback 到 hotspots。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _run_git(args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def _init_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", "-b", "main"], str(root))
    (root / ".gitignore").write_text(".repomap/\n")
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "user.py").write_text(
        textwrap.dedent(
            """\
            class User:
                def login(self):
                    pass

                def logout(self):
                    pass
            """
        )
    )
    (src / "order.py").write_text(
        textwrap.dedent(
            """\
            def create_order():
                pass
            """
        )
    )
    _run_git(["add", "."], str(root))
    _run_git(["commit", "-q", "-m", "init"], str(root))


_REPO_ROOT = str(Path(__file__).resolve().parents[1])


class QueryTopicNoFallbackTests(unittest.TestCase):
    """Issue #178: query --query 无匹配时不应返回 hotspots。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        _init_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_cli(self, args):
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_no_match_returns_empty_without_hotspots(self) -> None:
        """查询完全不相关的关键字（如 'zstd compression'）→ 应返回空结果，无 hotspots。"""
        r = self._run_cli(
            [
                "query",
                "--project",
                str(self.root),
                "--query",
                "zstd compression",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]

        # 不应包含不相关的 hotspots
        self.assertEqual(
            result["matchedFiles"],
            0,
            f"无匹配时 matchedFiles 应为 0，实际 {result['matchedFiles']}",
        )
        self.assertEqual(result["coreFiles"], [])
        self.assertEqual(result["supportingFiles"], [])
        # 必须有明确提示
        self.assertIn("message", result)
        self.assertIn("no match", result["message"].lower())

    def test_real_match_still_works(self) -> None:
        """真实匹配的关键字应正常返回结果，不被破坏。"""
        r = self._run_cli(
            [
                "query",
                "--project",
                str(self.root),
                "--query",
                "login user",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]

        # 应能找到与 login/user 相关的文件
        self.assertGreaterEqual(result["matchedFiles"], 1)
        all_paths = [f["path"] for f in result["coreFiles"] + result["supportingFiles"]]
        self.assertTrue(
            any("user" in p for p in all_paths),
            f"应包含 user.py，实际 {all_paths}",
        )


if __name__ == "__main__":
    unittest.main()
