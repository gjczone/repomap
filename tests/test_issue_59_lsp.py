"""Regression tests for LSP fixes from issue #59."""

import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.lsp import (
    StdioLspClient,
    _MAX_CONTENT_LENGTH,
    _MESSAGE_SKIPPED,
    _STREAM_EOF,
    _read_lsp_message,
)


class TestStreamEOF(unittest.TestCase):
    """_read_lsp_message returns _STREAM_EOF on stream exhaustion."""

    def test_empty_stream_returns_stream_eof(self) -> None:
        stream = io.BytesIO(b"")
        result = _read_lsp_message(stream)
        self.assertIs(result, _STREAM_EOF)

    def test_incomplete_header_returns_stream_eof(self) -> None:
        stream = io.BytesIO(b"Content-Length: 42")
        result = _read_lsp_message(stream)
        self.assertIs(result, _STREAM_EOF)

    def test_body_read_short_returns_stream_eof(self) -> None:
        header = b"Content-Length: 10\r\n\r\n"
        stream = io.BytesIO(header + b"short")
        result = _read_lsp_message(stream)
        self.assertIs(result, _STREAM_EOF)


class TestMessageSkipped(unittest.TestCase):
    """_read_lsp_message returns _MESSAGE_SKIPPED for oversized messages."""

    def test_oversized_returns_message_skipped_not_none(self) -> None:
        oversized_length = _MAX_CONTENT_LENGTH + 1
        header = f"Content-Length: {oversized_length}\r\n\r\n".encode("ascii")
        stream = io.BytesIO(header + b"x" * oversized_length)
        result = _read_lsp_message(stream)
        self.assertIs(result, _MESSAGE_SKIPPED)
        self.assertIsNot(result, None)

    def test_warns_on_oversized(self) -> None:
        with self.assertLogs("repomap.lsp", level="WARNING") as cm:
            oversized_length = _MAX_CONTENT_LENGTH + 1
            header = f"Content-Length: {oversized_length}\r\n\r\n".encode("ascii")
            stream = io.BytesIO(header + b"x" * oversized_length)
            _read_lsp_message(stream)
        self.assertTrue(
            any("exceeds maximum" in msg for msg in cm.output),
            f"Expected 'exceeds maximum' warning, got: {cm.output}",
        )


class TestStreamRecoveryAfterOversized(unittest.TestCase):
    """After draining an oversized body, the stream can read the next message."""

    def test_valid_message_after_oversized(self) -> None:
        oversized_length = _MAX_CONTENT_LENGTH + 1
        oversized_header = f"Content-Length: {oversized_length}\r\n\r\n".encode("ascii")
        oversized_body = b"x" * oversized_length

        valid_body = b'{"jsonrpc":"2.0","method":"hello"}'
        valid_header = f"Content-Length: {len(valid_body)}\r\n\r\n".encode("ascii")

        combined = oversized_header + oversized_body + valid_header + valid_body
        stream = io.BytesIO(combined)

        result1 = _read_lsp_message(stream)
        self.assertIs(
            result1,
            _MESSAGE_SKIPPED,
            f"Expected _MESSAGE_SKIPPED, got {type(result1).__name__}",
        )

        result2 = _read_lsp_message(stream)
        self.assertIsNot(result2, _STREAM_EOF)
        self.assertIsNot(result2, _MESSAGE_SKIPPED)
        self.assertIsInstance(result2, dict)
        self.assertEqual(result2["method"], "hello")


class TestValidMessage(unittest.TestCase):
    """_read_lsp_message returns parsed dict for valid LSP messages."""

    def test_valid_message_returns_dict(self) -> None:
        body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        stream = io.BytesIO(header + body)
        result = _read_lsp_message(stream)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["id"], 1)

    def test_zero_or_negative_length_returns_stream_eof(self) -> None:
        for length in (0, -1):
            with self.subTest(length=length):
                header = f"Content-Length: {length}\r\n\r\n".encode("ascii")
                stream = io.BytesIO(header)
                result = _read_lsp_message(stream)
                self.assertIs(result, _STREAM_EOF)

    def test_malformed_content_length_returns_stream_eof(self) -> None:
        stream = io.BytesIO(b"Content-Length: not-a-number\r\n\r\n")
        result = _read_lsp_message(stream)
        self.assertIs(result, _STREAM_EOF)


class TestReadLoopSentinel(unittest.TestCase):
    """_read_loop distinguishes _STREAM_EOF from _MESSAGE_SKIPPED."""

    def test_read_loop_exits_on_stream_eof(self) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            client = StdioLspClient(command=["fake-lsp"], workspace_root=Path("/tmp"))
            client.process = mock_process
            client._stop_reader = False

            with patch("src.lsp._read_lsp_message", return_value=_STREAM_EOF):
                client._read_loop()

            self.assertTrue(client._messages.empty())

    def test_read_loop_continues_on_message_skipped(self) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            client = StdioLspClient(command=["fake-lsp"], workspace_root=Path("/tmp"))
            client.process = mock_process
            client._stop_reader = False

            call_count = [0]

            def mock_read(_stream: object) -> object:
                call_count[0] += 1
                if call_count[0] == 1:
                    return _MESSAGE_SKIPPED
                return _STREAM_EOF

            with patch("src.lsp._read_lsp_message", side_effect=mock_read):
                client._read_loop()

            self.assertEqual(call_count[0], 2)
            self.assertTrue(client._messages.empty())


class TestOpenedFilesTracking(unittest.TestCase):
    """did_open tracks file paths; definition/references/hover guard on them."""

    def setUp(self) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            self.client = StdioLspClient(
                command=["fake-lsp"], workspace_root=Path("/tmp")
            )
            self.client.process = mock_process

    def test_did_open_tracks_file_path(self) -> None:
        file_path = Path("/tmp/test.py")
        with patch.object(self.client, "send_notification"):
            self.client.did_open(file_path, "python", "print('hello')")

        self.assertIn(str(file_path.resolve()), self.client._opened_files)

    def test_did_open_large_file_not_tracked(self) -> None:
        large_text = "x" * (self.client.MAX_FILE_SIZE + 1)
        file_path = Path("/tmp/large.py")

        with patch.object(self.client, "send_notification") as mock_send:
            self.client.did_open(file_path, "python", large_text)
            mock_send.assert_not_called()

        self.assertNotIn(str(file_path.resolve()), self.client._opened_files)

    def test_definition_returns_none_for_unopened_file(self) -> None:
        result = self.client.definition(Path("/tmp/not_opened.py"), 0, 0)
        self.assertIsNone(result)

    def test_definition_sends_request_for_opened_file(self) -> None:
        file_path = Path("/tmp/opened.py")
        self.client._opened_files.add(str(file_path.resolve()))

        with patch.object(self.client, "request") as mock_request:
            mock_request.return_value = {"result": ["some_location"]}
            result = self.client.definition(file_path, 5, 10)
            self.assertEqual(result, ["some_location"])

            mock_request.assert_called_once()
            args, _ = mock_request.call_args
            self.assertEqual(args[0], "textDocument/definition")

    def test_references_returns_none_for_unopened_file(self) -> None:
        result = self.client.references(Path("/tmp/not_opened.py"), 0, 0)
        self.assertIsNone(result)

    def test_references_sends_request_for_opened_file(self) -> None:
        file_path = Path("/tmp/opened.py")
        self.client._opened_files.add(str(file_path.resolve()))

        with patch.object(self.client, "request") as mock_request:
            mock_request.return_value = {"result": ["ref1", "ref2"]}
            result = self.client.references(file_path, 5, 10)
            self.assertEqual(result, ["ref1", "ref2"])

            mock_request.assert_called_once()
            args, _ = mock_request.call_args
            self.assertEqual(args[0], "textDocument/references")

    def test_hover_returns_none_for_unopened_file(self) -> None:
        result = self.client.hover(Path("/tmp/not_opened.py"), 0, 0)
        self.assertIsNone(result)

    def test_hover_sends_request_for_opened_file(self) -> None:
        file_path = Path("/tmp/opened.py")
        self.client._opened_files.add(str(file_path.resolve()))

        with patch.object(self.client, "request") as mock_request:
            mock_request.return_value = {"result": "some type info"}
            result = self.client.hover(file_path, 5, 10)
            self.assertEqual(result, "some type info")

            mock_request.assert_called_once()
            args, _ = mock_request.call_args
            self.assertEqual(args[0], "textDocument/hover")

    def test_multiple_did_open_accumulates_files(self) -> None:
        with patch.object(self.client, "send_notification"):
            self.client.did_open(Path("/tmp/a.py"), "python", "x = 1")
            self.client.did_open(Path("/tmp/b.py"), "python", "y = 2")

        self.assertIn(str(Path("/tmp/a.py").resolve()), self.client._opened_files)
        self.assertIn(str(Path("/tmp/b.py").resolve()), self.client._opened_files)
        self.assertEqual(len(self.client._opened_files), 2)


if __name__ == "__main__":
    unittest.main()
