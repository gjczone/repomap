"""Tests for issue #179: check LSP skip 应在顶层显示为 warning，不应隐藏。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class CheckLspSkipVisibilityTests(unittest.TestCase):
    """Issue #179: check 未指定 --modified-file 时，LSP skip 必须在顶层明确可见。"""

    def test_lsp_skip_reason_visible_in_top_level(self) -> None:
        """当 LSP 因无文件被 skip 时，skip 原因应出现在顶层（而非只藏在 runs 里）。"""
        from src.check import RepoMapChecker

        with tempfile.TemporaryDirectory() as project_root:
            (Path(project_root) / "main.py").write_text("print('hi')\n")
            checker = RepoMapChecker(project_root, max_items=20)
            from src.check import DiagnosticRunner

            # 模拟：python 类型已知，外部诊断工具返回空，只剩 LSP（因无文件被 skip）
            with patch.object(DiagnosticRunner, "run_all", return_value=[]):
                report = checker.check(
                    types=["python"],
                    modified_files=None,
                    lsp_timeout=5.0,
                    lsp_max_files=10,
                )

        # 必须有一个顶层字段明确告知 LSP 被跳过
        has_skip_warnings = (
            isinstance(report.get("skip_warnings"), list)
            and len(report["skip_warnings"]) > 0
            and any("lsp" in w.lower() for w in report["skip_warnings"])
        )
        has_message_hint = (
            isinstance(report.get("message"), str)
            and "lsp" in report["message"].lower()
            and (
                "skip" in report["message"].lower()
                or "no " in report["message"].lower()
            )
        )
        self.assertTrue(
            has_skip_warnings or has_message_hint,
            f"顶层应明确提示 LSP 被跳过；实际 keys: {list(report.keys())}, "
            f"message={report.get('message')!r}, skip_warnings={report.get('skip_warnings')!r}",
        )


if __name__ == "__main__":
    unittest.main()
