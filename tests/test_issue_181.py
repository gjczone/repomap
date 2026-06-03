"""Tests for issue #181: affected 不应产生跨语言 dependency_chain。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _run_git(args, cwd):
    import os

    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def _init_mixed_project(root: Path) -> None:
    """TS + Rust 混合项目。"""
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", "-b", "main"], str(root))
    (root / ".gitignore").write_text(".repomap/\n")

    # TS 端
    ts_src = root / "src"
    ts_src.mkdir()
    (ts_src / "client.ts").write_text(
        textwrap.dedent(
            """\
            export function fetchWithAuth(url: string) {
                return fetch(url);
            }
            """
        )
    )
    ts_tests = root / "tests"
    ts_tests.mkdir()
    (ts_tests / "client.test.ts").write_text(
        textwrap.dedent(
            """\
            import { fetchWithAuth } from "../src/client";
            test("fetch", () => {
                expect(fetchWithAuth("x")).toBeDefined();
            });
            """
        )
    )

    # Rust 端
    rs_src = root / "backend" / "src"
    rs_src.mkdir(parents=True)
    (rs_src / "app.rs").write_text(
        textwrap.dedent(
            """\
            pub fn fetch_with_auth(url: &str) -> bool {
                !url.is_empty()
            }
            """
        )
    )
    rs_tests = root / "backend" / "tests"
    rs_tests.mkdir(parents=True)
    (rs_tests / "app_test.rs").write_text(
        textwrap.dedent(
            """\
            use crate::fetch_with_auth;
            #[test]
            fn test_fetch_with_auth() {
                assert!(fetch_with_auth("x"));
            }
            """
        )
    )
    (root / "backend" / "src" / "lib.rs").write_text("pub mod app;\n")
    (root / "backend" / "Cargo.toml").write_text(
        '[package]\nname = "backend"\nversion = "0.1.0"\nedition = "2021"\n'
    )

    _run_git(["add", "."], str(root))
    _run_git(["commit", "-q", "-m", "init"], str(root))


class AffectedCrossLanguageTests(unittest.TestCase):
    """Issue #181: 跨语言 edges 不应产生 dependency chain。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        _init_mixed_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_cli(self, args):
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_ts_file_does_not_affect_rust_tests(self) -> None:
        """仅传 TS 文件时，Rust tests 不应出现在 affected_tests。"""
        r = self._run_cli(
            [
                "affected",
                "--project",
                str(self.root),
                "--files",
                "src/client.ts",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]

        # 所有 affected_tests 必须是 TS 测试（排除 .rs）
        for t in result["affected_tests"]:
            self.assertFalse(
                t["file"].endswith(".rs"),
                f"TS changed file 不应影响 Rust 测试，但得到 {t['file']}",
            )
        # 所有 dependency_chain.via 不应包含 .rs
        for chain in result["dependency_chain"]:
            for item in chain["affected_tests"]:
                for via_file in item["via"]:
                    (
                        self.assertFalse(
                            via_file.endswith(".rs"),
                            f"dependency_chain via 不应包含 Rust 文件：{via_file}",
                        ),
                    )

    def test_rust_file_does_not_affect_ts_tests(self) -> None:
        """仅传 Rust 文件时，TS tests 不应出现在 affected_tests。"""
        r = self._run_cli(
            [
                "affected",
                "--project",
                str(self.root),
                "--files",
                "backend/src/app.rs",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]

        for t in result["affected_tests"]:
            self.assertFalse(
                t["file"].endswith(".ts") or t["file"].endswith(".tsx"),
                f"Rust changed file 不应影响 TS 测试，但得到 {t['file']}",
            )

    def test_cross_language_edge_not_traversed(self) -> None:
        """当图中存在跨语言 phantom edge 时，affected 不应遍历到另一语言的测试。"""
        from src import RepoGraph, Symbol, Edge
        from src.cli.commands import affected as affected_mod

        # 构造最小 graph：TS client.ts → (phantom) → Rust app.rs → Rust test
        g = RepoGraph()
        ts_sym = Symbol(
            id="src/client.ts::fetchWithAuth::1",
            name="fetchWithAuth",
            kind="function",
            file="src/client.ts",
            line=1,
        )
        rs_sym = Symbol(
            id="backend/src/app.rs::fetch_with_auth::1",
            name="fetch_with_auth",
            kind="function",
            file="backend/src/app.rs",
            line=1,
        )
        rs_test_sym = Symbol(
            id="backend/tests/app_test.rs::test_fetch::1",
            name="test_fetch",
            kind="function",
            file="backend/tests/app_test.rs",
            line=1,
        )
        for sym in (ts_sym, rs_sym, rs_test_sym):
            g.symbols[sym.id] = sym
        g.file_symbols["src/client.ts"] = [ts_sym.id]
        g.file_symbols["backend/src/app.rs"] = [rs_sym.id]
        g.file_symbols["backend/tests/app_test.rs"] = [rs_test_sym.id]
        # phantom cross-language edge：TS → Rust（不应存在但 tree-sitter 误识别）
        cross_edge = Edge(source=rs_sym.id, target=ts_sym.id, weight=1.0, kind="call")
        g.incoming.setdefault(ts_sym.id, []).append(cross_edge)
        # Rust test → Rust function edge（合法）
        rust_edge = Edge(
            source=rs_test_sym.id, target=rs_sym.id, weight=1.0, kind="call"
        )
        g.incoming.setdefault(rs_sym.id, []).append(rust_edge)

        # Mock _scan_engine 返回此 graph
        class FakeEngine:
            def __init__(self_inner, root):
                self_inner.project_root = root
                self_inner.graph = g

            def file_analysis(self_inner):
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "client.ts").write_text(
                "export function fetchWithAuth() {}\n"
            )
            (root / "backend" / "src").mkdir(parents=True)
            (root / "backend" / "src" / "app.rs").write_text(
                "pub fn fetch_with_auth() {}\n"
            )
            (root / "backend" / "tests").mkdir(parents=True)
            (root / "backend" / "tests" / "app_test.rs").write_text(
                "#[test] fn t() {}\n"
            )

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with (
                patch.object(
                    affected_mod, "_scan_engine", return_value=FakeEngine(root)
                ),
                patch.object(affected_mod, "_scan_stats_payload", return_value={}),
                redirect_stdout(buf),
            ):
                rc = affected_mod.run_affected(
                    project=str(root),
                    max_files=100,
                    target_files=["src/client.ts"],
                    as_json=True,
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            result = payload["result"]

            # 关键断言：Rust tests 不应出现在 affected_tests
            for t in result["affected_tests"]:
                (
                    self.assertFalse(
                        t["file"].endswith(".rs"),
                        f"跨语言 phantom edge 不应让 Rust test 出现在 TS changed 的 affected 中：{t['file']}",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
