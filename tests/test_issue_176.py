"""Tests for issue #176: Rust LSP hover 空结果的重试机制。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RustLspRetryTests(unittest.TestCase):
    """Issue #176: rust-analyzer 首次返回空结果时应重试一次。"""

    def test_rust_lsp_retries_on_empty_first_result(self) -> None:
        """首次 definition/hover 都返回空时，应对 rust 文件重试一次，第二次非空则采用。"""
        from src.lsp import collect_lsp_full_evidence

        # 模拟 LSP client
        fake_client = MagicMock()
        fake_client.initialize.return_value = None
        fake_client.did_open.return_value = None

        # 第一次返回空，第二次返回有效数据
        empty_def: list[dict] = []
        real_def = [
            {
                "uri": "file:///tmp/proj/src/a.rs",
                "range": {
                    "start": {"line": 4, "character": 0},
                    "end": {"line": 4, "character": 5},
                },
            }
        ]
        fake_client.definition = MagicMock(side_effect=[empty_def, real_def])
        fake_client.references = MagicMock(side_effect=[[], []])
        fake_client.hover = MagicMock(
            side_effect=[
                None,
                {"contents": "```rust\nfn build_router() -> Router\n```"},
            ]
        )

        # Mock StdioLspClient context manager 和 detect_lsp_server
        class FakeCtx:
            def __enter__(self_inner):
                return fake_client

            def __exit__(self_inner, *args):
                return False

        with (
            patch("src.lsp.StdioLspClient", return_value=FakeCtx()),
            patch("src.lsp.detect_lsp_server") as mock_detect,
            patch("src.lsp.language_for_file", return_value="rust"),
            patch("src.lsp.time.sleep"),
        ):
            det = MagicMock()
            det.status = "available"
            det.server_name = "rust-analyzer"
            det.workspace_root = "/tmp/proj"
            det.command = ["rust-analyzer"]
            det.reason = ""
            mock_detect.return_value = det

            # 创建一个假的 rust 文件
            import tempfile

            td = tempfile.TemporaryDirectory()
            root = Path(td.name)
            src_dir = root / "src"
            src_dir.mkdir()
            target = src_dir / "a.rs"
            target.write_text("pub fn build_router() {}\n")

            result, hover = collect_lsp_full_evidence(
                str(root), "src/a.rs", 1, "build_router", timeout=30
            )

        # 第二次成功的 hover 应被采用
        self.assertIsNotNone(hover, "hover 应被重试后获得")
        self.assertIn("build_router", hover.contents)
        # definition 应调用 2 次（首次空 + 重试）
        self.assertEqual(fake_client.definition.call_count, 2)

    def test_non_rust_does_not_retry(self) -> None:
        """非 rust 文件首次返回空时不应重试（避免其他语言延迟）。"""
        from src.lsp import collect_lsp_full_evidence

        fake_client = MagicMock()
        fake_client.initialize.return_value = None
        fake_client.did_open.return_value = None
        fake_client.definition = MagicMock(return_value=[])
        fake_client.references = MagicMock(return_value=[])
        fake_client.hover = MagicMock(return_value=None)

        class FakeCtx:
            def __enter__(self_inner):
                return fake_client

            def __exit__(self_inner, *args):
                return False

        with (
            patch("src.lsp.StdioLspClient", return_value=FakeCtx()),
            patch("src.lsp.detect_lsp_server") as mock_detect,
            patch("src.lsp.language_for_file", return_value="python"),
        ):
            det = MagicMock()
            det.status = "available"
            det.server_name = "pyright"
            det.workspace_root = "/tmp/proj"
            det.command = ["pyright-langserver"]
            det.reason = ""
            mock_detect.return_value = det

            import tempfile

            td = tempfile.TemporaryDirectory()
            root = Path(td.name)
            (root / "a.py").write_text("def build_router(): pass\n")

            result, hover = collect_lsp_full_evidence(
                str(root), "a.py", 1, "build_router", timeout=30
            )

        # 非 rust 只查一次
        self.assertEqual(fake_client.definition.call_count, 1)


if __name__ == "__main__":
    unittest.main()
