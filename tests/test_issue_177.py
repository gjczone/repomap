"""Tests for issue #177: TypeScript lsp_symbol_tree 首次空结果重试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TypescriptLspSymbolTreeRetryTests(unittest.TestCase):
    """Issue #177: typescript-language-server 首次返回空 symbol tree 时应重试一次。"""

    def test_ts_symbol_tree_retries_on_empty_first_result(self) -> None:
        """首次 document_symbols 返回空时应重试一次，第二次非空则采用。"""
        from src.lsp import collect_lsp_symbol_tree

        fake_client = MagicMock()
        fake_client.initialize.return_value = None
        fake_client.did_open.return_value = None

        # 第一次返回空，第二次返回有效数据
        real_symbols = [
            {
                "name": "fetchWithAuth",
                "kind": 12,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 5, "character": 0},
                },
                "selectionRange": {
                    "start": {"line": 0, "character": 16},
                    "end": {"line": 0, "character": 29},
                },
                "children": [],
            }
        ]
        fake_client.document_symbols = MagicMock(side_effect=[[], real_symbols])

        class FakeCtx:
            def __enter__(self_inner):
                return fake_client

            def __exit__(self_inner, *args):
                return False

        with (
            patch("src.lsp.StdioLspClient", return_value=FakeCtx()),
            patch("src.lsp.detect_lsp_server") as mock_detect,
            patch("src.lsp.language_for_file", return_value="typescript"),
            patch("src.lsp.time.sleep"),
        ):
            det = MagicMock()
            det.status = "available"
            det.server_name = "typescript-language-server"
            det.workspace_root = "/tmp/proj"
            det.command = ["typescript-language-server"]
            det.reason = ""
            mock_detect.return_value = det

            import tempfile

            td = tempfile.TemporaryDirectory()
            root = Path(td.name)
            (root / "a.ts").write_text("export function fetchWithAuth() {}\n")

            result = collect_lsp_symbol_tree(str(root), "a.ts", timeout=30)

        # 第二次成功的结果应被采用
        self.assertEqual(len(result), 1, f"应得到 1 个顶层符号，实际 {result}")
        self.assertEqual(result[0].name, "fetchWithAuth")
        # document_symbols 应调用 2 次（首次空 + 重试）
        self.assertEqual(fake_client.document_symbols.call_count, 2)

    def test_symbol_tree_non_empty_first_pass_no_retry(self) -> None:
        """首次已有结果时不应重试（任何语言）。"""
        from src.lsp import collect_lsp_symbol_tree

        fake_client = MagicMock()
        fake_client.initialize.return_value = None
        fake_client.did_open.return_value = None

        real_symbols = [
            {
                "name": "foo",
                "kind": 12,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 1, "character": 0},
                },
                "selectionRange": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 3},
                },
                "children": [],
            }
        ]
        fake_client.document_symbols = MagicMock(return_value=real_symbols)

        class FakeCtx:
            def __enter__(self_inner):
                return fake_client

            def __exit__(self_inner, *args):
                return False

        with (
            patch("src.lsp.StdioLspClient", return_value=FakeCtx()),
            patch("src.lsp.detect_lsp_server") as mock_detect,
            patch("src.lsp.language_for_file", return_value="typescript"),
            patch("src.lsp.time.sleep") as mock_sleep,
        ):
            det = MagicMock()
            det.status = "available"
            det.server_name = "typescript-language-server"
            det.workspace_root = "/tmp/proj"
            det.command = ["typescript-language-server"]
            det.reason = ""
            mock_detect.return_value = det

            import tempfile

            td = tempfile.TemporaryDirectory()
            root = Path(td.name)
            (root / "a.ts").write_text("function foo() {}\n")

            result = collect_lsp_symbol_tree(str(root), "a.ts", timeout=30)

        self.assertEqual(len(result), 1)
        # 仅调用一次（无重试）
        self.assertEqual(fake_client.document_symbols.call_count, 1)
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
