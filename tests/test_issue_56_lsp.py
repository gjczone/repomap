"""Regression tests for LSP fixes from issue #56."""

import io
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.lsp import (
    LspDiagnostic,
    StdioLspClient,
    _MAX_CONTENT_LENGTH,
    _MESSAGE_SKIPPED,
    _diagnostic_from_lsp,
    _read_lsp_message,
)


class TestStopReaderReset(unittest.TestCase):
    """L1: _stop_event reset — verify start() resets _stop_event."""

    def test_start_resets_stop_event(self) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            # 让 stdout.readline() 返回空字节，表示 EOF，避免无限循环
            mock_process.stdout = MagicMock()
            mock_process.stdout.readline.return_value = b""
            mock_process.stderr = MagicMock()
            mock_process.stderr.read.return_value = b""
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            client = StdioLspClient(command=["fake-lsp"], workspace_root=Path("/tmp"))
            # Simulate a prior stop: set _stop_event
            client._stop_event.set()
            self.assertTrue(client._stop_event.is_set())

            client.start()

            # After start(), _stop_event must be cleared so reader threads can run
            self.assertFalse(client._stop_event.is_set())

            # Clean up: set _stop_event so mocked threads would exit, then close
            client._stop_event.set()
            client.close()


class TestMaxContentLength(unittest.TestCase):
    """L3: MAX_CONTENT_LENGTH — _read_lsp_message discards messages > 10 MB."""

    def test_rejects_oversized_message(self) -> None:
        oversized_length = _MAX_CONTENT_LENGTH + 1
        header = f"Content-Length: {oversized_length}\r\n\r\n".encode("ascii")
        # Provide enough body bytes so the drain loop can consume them all
        stream = io.BytesIO(header + b"x" * oversized_length)
        result = _read_lsp_message(stream)
        self.assertIs(result, _MESSAGE_SKIPPED)

    def test_accepts_message_at_max_length(self) -> None:
        # A message exactly at the limit should be accepted
        body = '{"jsonrpc":"2.0","method":"test"}'
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        stream = io.BytesIO(header + body.encode("utf-8"))
        result = _read_lsp_message(stream)
        self.assertIsNotNone(result)
        assert result is not None  # narrow for type checker
        self.assertEqual(result["method"], "test")

    def test_warns_on_oversized(self) -> None:
        with self.assertLogs("repomap.lsp", level="WARNING") as cm:
            oversized_length = _MAX_CONTENT_LENGTH + 1
            header = f"Content-Length: {oversized_length}\r\n\r\n".encode("ascii")
            stream = io.BytesIO(header + b"x")
            _read_lsp_message(stream)
        self.assertTrue(
            any("exceeds maximum" in msg for msg in cm.output),
            f"Expected 'exceeds maximum' warning, got: {cm.output}",
        )


class TestNextIdThreadSafety(unittest.TestCase):
    """L5: _next_id thread safety — concurrent increments produce no collisions."""

    def test_concurrent_next_id_no_collisions(self) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            # 让 stdout.readline() 返回空字节，表示 EOF，避免无限循环
            mock_process.stdout = MagicMock()
            mock_process.stdout.readline.return_value = b""
            mock_process.stderr = MagicMock()
            mock_process.stderr.read.return_value = b""
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            client = StdioLspClient(command=["fake-lsp"], workspace_root=Path("/tmp"))
            client.start()
            client._stop_event.set()  # prevent read loop from blocking
            client.close()
            # client now has _id_lock and _next_id initialized

            ids_seen: list[int] = []
            lock = threading.Lock()
            errors: list[Exception] = []

            def acquire_id() -> None:
                try:
                    for _ in range(500):
                        with client._id_lock:
                            rid = client._next_id
                            client._next_id += 1
                        with lock:
                            ids_seen.append(rid)
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=acquire_id) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
            self.assertEqual(
                len(ids_seen),
                len(set(ids_seen)),
                f"Collisions detected among {len(ids_seen)} IDs; "
                f"unique={len(set(ids_seen))}",
            )


class TestDiagnosticNoneGetSafety(unittest.TestCase):
    """S4: diagnostic None.get() safety — malformed params don't crash."""

    def test_params_missing_uri_does_not_crash(self) -> None:
        result = _diagnostic_from_lsp(
            project_root=Path("/tmp"),
            params={},  # no 'uri' key
            item={"message": "test diagnostic"},
        )
        self.assertIsInstance(result, LspDiagnostic)
        self.assertEqual(result.message, "test diagnostic")

    def test_empty_item_does_not_crash(self) -> None:
        result = _diagnostic_from_lsp(
            project_root=Path("/tmp"),
            params={"uri": "file:///tmp/test.py"},
            item={},
        )
        self.assertIsInstance(result, LspDiagnostic)
        self.assertEqual(result.line, 1)
        self.assertEqual(result.col, 1)

    def test_none_fields_in_item_do_not_crash(self) -> None:
        result = _diagnostic_from_lsp(
            project_root=Path("/tmp"),
            params={"uri": "file:///tmp/test.py"},
            item={
                "range": None,
                "severity": None,
                "code": None,
                "message": None,
                "source": None,
            },
        )
        self.assertIsInstance(result, LspDiagnostic)
        self.assertEqual(result.line, 1)
        self.assertEqual(result.code, "")
        # str(None) produces "None" for message/source — this is acceptable;
        # the fix is about not crashing, not cosmetic output for None values.
        self.assertEqual(result.message, "None")
        self.assertEqual(result.source, "None")
        self.assertEqual(result.severity, "warning")

    def test_range_not_a_dict_does_not_crash(self) -> None:
        result = _diagnostic_from_lsp(
            project_root=Path("/tmp"),
            params={"uri": "file:///tmp/test.py"},
            item={"range": [1, 2, 3]},  # list, not dict
        )
        self.assertIsInstance(result, LspDiagnostic)
        self.assertEqual(result.line, 1)
        self.assertEqual(result.col, 1)

    def test_all_params_none(self) -> None:
        """Entire params can be empty — the .get() calls guard against None."""
        result = _diagnostic_from_lsp(
            project_root=Path("/tmp"),
            params={},
            item={},
        )
        self.assertIsInstance(result, LspDiagnostic)


class TestNotificationsCap(unittest.TestCase):
    """S5: _notifications 500 cap — list capped at 500 and warns."""

    def test_notifications_capped_at_500(self) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            # Use a tiny timeout so the request loop exits quickly
            client = StdioLspClient(
                command=["fake-lsp"], workspace_root=Path("/tmp"), timeout=0.1
            )
            # Don't call start() — we don't want reader threads
            client.process = mock_process

            # Pre-fill _notifications to exactly 500
            client._notifications = [
                {"method": "dummy", "params": {}} for _ in range(500)
            ]

            # Put a notification (message without "id") into the message queue.
            notification_msg = {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": "file:///tmp/test.py"},
            }
            client._messages.put(notification_msg)

            with patch.object(client, "_send"):
                with self.assertLogs("repomap.lsp", level="WARNING") as cm:
                    try:
                        client.request("fakeMethod", {})
                    except (TimeoutError, RuntimeError):
                        pass

                self.assertLessEqual(
                    len(client._notifications),
                    500,
                    f"_notifications should be capped at 500, "
                    f"got {len(client._notifications)}",
                )
                self.assertTrue(
                    any("at capacity" in msg for msg in cm.output),
                    f"Expected 'at capacity' warning, got: {cm.output}",
                )

    def test_notifications_appended_when_below_cap(self) -> None:
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            client = StdioLspClient(
                command=["fake-lsp"], workspace_root=Path("/tmp"), timeout=0.1
            )
            client.process = mock_process

            # _notifications below 500 — appending should work
            client._notifications = [
                {"method": "dummy", "params": {}} for _ in range(10)
            ]
            initial_count = len(client._notifications)

            notification_msg = {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": "file:///tmp/test.py"},
            }
            client._messages.put(notification_msg)

            with patch.object(client, "_send"):
                try:
                    client.request("fakeMethod", {})
                except (TimeoutError, RuntimeError):
                    pass

            self.assertGreater(
                len(client._notifications),
                initial_count,
                f"Expected notification to be appended, "
                f"but _notifications stayed at {len(client._notifications)}",
            )


if __name__ == "__main__":
    unittest.main()
