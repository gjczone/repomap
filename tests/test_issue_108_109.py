"""Tests for issue #108 (impact --compact) and #109 (verify --risk-threshold)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _run_git(args, cwd):
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _init_git_project(root):
    """Create a small multi-file project with cross-file references."""
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", "-b", "main"], str(root))
    (root / ".gitignore").write_text(".repomap/\n")

    src = root / "src"
    src.mkdir()

    # Core module with exported symbols referenced by many other files
    (src / "core.py").write_text(
        textwrap.dedent("""\
        class DataProcessor:
            \"\"\"Core data processor used by many modules.\"\"\"
            def process(self, data):
                return data

        def validate(data):
            return bool(data)

        def transform(data, rules):
            return data

        def parse_config(path):
            return {}

        def load_defaults():
            return {}
        """).lstrip()
    )

    # Multiple consumer files that reference core symbols
    for i in range(8):
        (src / f"consumer_{i}.py").write_text(
            textwrap.dedent(f"""\
            from .core import DataProcessor, validate, transform

            def run_consumer_{i}():
                p = DataProcessor()
                data = p.process({{}})
                if validate(data):
                    return transform(data, [])
                return None
            """).lstrip()
        )

    # Util module
    (src / "util.py").write_text(
        textwrap.dedent("""\
        def helper():
            return 42
        """).lstrip()
    )

    (src / "__init__.py").write_text("")

    # 添加 4 个测试文件引用 consumer 模块，确保 Suggested Tests 有内容（issue #173）
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    # test_core.py 与 core.py 同名 → 触发策略1文件名匹配（high confidence）
    test_core_content = (
        "from src.core import DataProcessor, validate\n"
        "\n"
        "def test_data_processor():\n"
        "    p = DataProcessor()\n"
        "    assert p.process({}) == {}\n"
        "\n"
        "def test_validate():\n"
        "    assert validate('x') is True\n"
    )
    (tests_dir / "test_core.py").write_text(test_core_content)
    # 其他 3 个 test 文件通过 import core 符号触发策略3
    for i in range(3):
        content = (
            f"from src.core import DataProcessor, validate\n"
            f"from src.consumer_{i} import run_consumer_{i}\n"
            f"\n"
            f"def test_consumer_{i}_uses_core():\n"
            f"    p = DataProcessor()\n"
            f"    assert validate(p.process({{}})) is True\n"
            f"    assert run_consumer_{i}() is not None\n"
        )
        (tests_dir / f"test_consumer_{i}.py").write_text(content)

    _run_git(["add", "."], str(root))
    _run_git(["commit", "-q", "-m", "init"], str(root))


class ImpactCompactModeTests(unittest.TestCase):
    """Issue #108: impact --compact limits output verbosity."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        _init_git_project(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_cli(self, args):
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_compact_json_limits_affected_files(self):
        """--compact JSON output should limit affectedFiles to top-N."""
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--compact",
                "--top-n",
                "3",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]

        # Must have affectedFilesCount summary
        self.assertIn("affectedFilesCount", result)
        self.assertIsInstance(result["affectedFilesCount"], int)

        # affected_files should be limited to top-N
        self.assertLessEqual(len(result["affected_files"]), 3)

    def test_compact_json_includes_count_even_with_many_files(self):
        """--compact JSON: affectedFilesCount should reflect total, not truncated count."""
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--compact",
                "--top-n",
                "2",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]

        # affectedFilesCount should be >= len(affected_files) (total vs truncated)
        self.assertGreaterEqual(
            result["affectedFilesCount"], len(result["affected_files"])
        )

    def test_compact_text_mode_shorter_than_full(self):
        """--compact text output should be shorter than full output."""
        r_full = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--no-json",
            ]
        )
        r_compact = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--compact",
                "--no-json",
            ]
        )
        self.assertEqual(r_full.returncode, 0, r_full.stderr)
        self.assertEqual(r_compact.returncode, 0, r_compact.stderr)
        # Compact output should be meaningfully shorter
        self.assertLess(len(r_compact.stdout), len(r_full.stdout))

    def test_no_compact_has_no_affected_files_count(self):
        """Without --compact, JSON should NOT have affectedFilesCount."""
        r = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]
        self.assertNotIn("affectedFilesCount", result)

    def test_compact_json_truncates_tests_and_read_next(self):
        """Issue #173: --compact JSON must truncate tests, read_next, type_impacts, impact_radius."""
        r_compact = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--compact",
                "--top-n",
                "2",
                "--json",
            ]
        )
        r_full = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--json",
            ]
        )
        self.assertEqual(r_compact.returncode, 0, r_compact.stderr)
        self.assertEqual(r_full.returncode, 0, r_full.stderr)
        compact = json.loads(r_compact.stdout)["result"]
        full = json.loads(r_full.stdout)["result"]

        # 项目有 8 个 consumer 文件引用 core，full 模式 tests/read_next 应 >=4
        self.assertGreaterEqual(len(full["tests"]), 4)
        self.assertGreaterEqual(len(full["read_next"]), 4)

        # compact 模式下：tests/read_next 被严格截断（不超过 3）
        self.assertLessEqual(len(compact["tests"]), 3)
        self.assertLessEqual(len(compact["read_next"]), 3)

        # type_impacts 不应以完整数组形式出现（compact 下替换为 summary）
        self.assertNotIn("type_impacts", compact)

        # impact_radius 不应出现（compact 不需要多跳展开细节）
        self.assertNotIn("impact_radius", compact)

        # compact 应提供测试总数摘要
        self.assertIn("suggestedTestsCount", compact)
        self.assertEqual(compact["suggestedTestsCount"], len(full["tests"]))

    def test_compact_text_shorter_with_many_tests(self):
        """Issue #173: --compact text should meaningfully truncate Suggested Tests."""
        r_full = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--no-json",
            ]
        )
        r_compact = self._run_cli(
            [
                "impact",
                "--project",
                str(self.root),
                "--files",
                "src/core.py",
                "--compact",
                "--no-json",
            ]
        )
        self.assertEqual(r_full.returncode, 0, r_full.stderr)
        self.assertEqual(r_compact.returncode, 0, r_compact.stderr)
        # compact 必须比 full 短（Suggested Tests 也被截断）
        self.assertLess(
            len(r_compact.stdout),
            len(r_full.stdout),
            "compact text should be shorter than full output",
        )
        # compact 的 Suggested Tests 数量应少于 full（如果有的话）
        full_test_lines = [
            l for l in r_full.stdout.splitlines() if l.startswith("- `tests/")
        ]
        compact_test_lines = [
            l for l in r_compact.stdout.splitlines() if l.startswith("- `tests/")
        ]
        if len(full_test_lines) >= 4:
            self.assertLessEqual(len(compact_test_lines), 3)


class VerifyRiskThresholdTests(unittest.TestCase):
    """Issue #109: verify --risk-threshold filters contractRisks."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        _init_git_project(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_cli(self, args):
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_verify_has_contract_risks_by_default(self):
        """Default verify should report contractRisks when exported symbols change."""
        # Modify core.py to create a git diff
        core = self.root / "src" / "core.py"
        core.write_text(core.read_text() + "\n# modified\n")
        _run_git(["add", "src/core.py"], str(self.root))

        r = self._run_cli(["verify", "--project", str(self.root), "--json", "--quick"])
        self.assertIn(r.returncode, (0, 3), r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]
        risks = result.get("contractRisks", [])
        # Should have some risks from exported symbols
        self.assertIsInstance(risks, list)

    def test_risk_threshold_high_filters_med(self):
        """--risk-threshold HIGH should filter out MED and LOW risks."""
        core = self.root / "src" / "core.py"
        core.write_text(core.read_text() + "\n# modified\n")
        _run_git(["add", "src/core.py"], str(self.root))

        r = self._run_cli(
            [
                "verify",
                "--project",
                str(self.root),
                "--json",
                "--quick",
                "--risk-threshold",
                "HIGH",
            ]
        )
        self.assertIn(r.returncode, (0, 3), r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]
        risks = result.get("contractRisks", [])
        # All remaining risks should be HIGH
        for risk in risks:
            self.assertEqual(
                risk["level"],
                "HIGH",
                f"Expected only HIGH risks with threshold=HIGH, got {risk}",
            )

    def test_grouped_type_warnings(self):
        """Multiple type warnings should be grouped into a single summary."""
        core = self.root / "src" / "core.py"
        core.write_text(core.read_text() + "\n# modified\n")
        _run_git(["add", "src/core.py"], str(self.root))

        r = self._run_cli(["verify", "--project", str(self.root), "--json", "--quick"])
        self.assertIn(r.returncode, (0, 3), r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]
        risks = result.get("contractRisks", [])

        # Count how many individual type warnings there are
        type_risks = [
            cr
            for cr in risks
            if "Type `" in cr.get("message", "")
            or "types changed" in cr.get("message", "").lower()
        ]
        # If there are multiple types, they should be grouped (not N individual warnings)
        # A grouped message should contain a count
        if len(type_risks) > 0:
            # At least one risk should mention a count if grouping is working
            has_grouped = any(
                "type" in cr.get("message", "").lower()
                and any(c.isdigit() for c in cr.get("message", ""))
                for cr in type_risks
            )
            self.assertTrue(
                has_grouped,
                f"Expected grouped type warnings, got individual: {type_risks}",
            )

    def test_contract_risks_have_confidence_field(self):
        """contractRisks items should have a 'confidence' field distinguishing export vs internal."""
        core = self.root / "src" / "core.py"
        core.write_text(core.read_text() + "\n# modified\n")
        _run_git(["add", "src/core.py"], str(self.root))

        r = self._run_cli(["verify", "--project", str(self.root), "--json", "--quick"])
        self.assertIn(r.returncode, (0, 3), r.stderr)
        payload = json.loads(r.stdout)
        result = payload["result"]
        risks = result.get("contractRisks", [])

        for risk in risks:
            self.assertIn(
                "confidence",
                risk,
                f"contractRisks item missing 'confidence' field: {risk}",
            )
            self.assertIn(
                risk["confidence"],
                ("export", "internal", "config"),
                f"Unexpected confidence value: {risk['confidence']}",
            )


if __name__ == "__main__":
    unittest.main()
