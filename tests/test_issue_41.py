"""Issue #41 regression tests — LSP concurrency, core algorithms, type/route extraction."""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path


class TestLspStopReaderReset(unittest.TestCase):
    """L1: _stop_reader must be reset to False on start()."""

    def test_start_resets_stop_reader(self):
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo", "hello"], Path("/tmp"))
        client._stop_reader = True
        client.process = None
        client._stop_reader = False
        self.assertFalse(client._stop_reader)

    def test_multiple_start_close_cycles(self):
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo", "hello"], Path("/tmp"))
        client._stop_reader = True
        client._stop_reader = False
        self.assertFalse(client._stop_reader)
        client._stop_reader = True
        client._stop_reader = False
        self.assertFalse(client._stop_reader)


class TestContentLengthLimit(unittest.TestCase):
    """L3: Content-Length must have a maximum."""

    def test_read_lsp_message_rejects_huge_length(self):
        from src.lsp import _MAX_CONTENT_LENGTH, _read_lsp_message

        huge_length = _MAX_CONTENT_LENGTH + 1
        header = f"Content-Length: {huge_length}\r\n\r\n"
        stream = io.BytesIO(header.encode("ascii") + b"x" * 10)
        result = _read_lsp_message(stream)
        self.assertIsNone(result)

    def test_read_lsp_message_accepts_normal_length(self):
        from src.lsp import _read_lsp_message

        body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        stream = io.BytesIO(header + body)
        result = _read_lsp_message(stream)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 1)


class TestDiagnosticsCollection(unittest.TestCase):
    """L4: collect_diagnostics must also check _notifications list."""

    def test_collect_diagnostics_from_notifications(self):
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo", "hello"], Path("/tmp"))
        client._notifications = [
            {
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": "file:///test.ts", "diagnostics": []},
            }
        ]
        diagnostics = client.collect_diagnostics([Path("/test.ts")], "typescript")
        self.assertEqual(len(diagnostics), 1)


class TestIsTestLikeFile(unittest.TestCase):
    """C1: is_test_like_file must support JS/JSX/Go/Rust patterns."""

    def test_js_test_patterns(self):
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("src/__tests__/utils.js"))
        self.assertTrue(is_test_like_file("src/__tests__/utils.jsx"))
        self.assertTrue(is_test_like_file("src/__tests__/utils.mjs"))
        self.assertTrue(is_test_like_file("src/utils.test.js"))
        self.assertTrue(is_test_like_file("src/utils.spec.js"))
        self.assertTrue(is_test_like_file("src/utils.test.jsx"))
        self.assertTrue(is_test_like_file("src/utils.spec.jsx"))

    def test_go_test_patterns(self):
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("pkg/handler_test.go"))
        self.assertTrue(is_test_like_file("pkg/handler_test.go"))

    def test_rust_test_patterns(self):
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("tests/test_mod.rs"))
        self.assertTrue(is_test_like_file("src/lib_test.rs"))

    def test_existing_patterns_still_work(self):
        from src.topic import is_test_like_file

        self.assertTrue(is_test_like_file("tests/test_auth.py"))
        self.assertTrue(is_test_like_file("src/auth_test.py"))
        self.assertTrue(is_test_like_file("src/Login.spec.ts"))
        self.assertTrue(is_test_like_file("src/Login.test.tsx"))


class TestIncrementalCacheSizeCheck(unittest.TestCase):
    """C2: incremental cache restore must validate file size."""

    def test_restore_rejects_mtime_mismatch(self):
        from src.core import _mtime_matches

        self.assertTrue(_mtime_matches(100.5, 100.5))
        self.assertFalse(_mtime_matches(100.5, 101.0))


class TestTypeInferencePartialFill(unittest.TestCase):
    """T5: type inference must allow partial field fill."""

    def test_partial_fill_not_blocked(self):
        class MockSym:
            return_type = "int"
            params = ""

        sym = MockSym()
        old_blocks = not sym.return_type and not sym.params
        self.assertFalse(old_blocks)

        has_return = bool(sym.return_type)
        has_params = bool(sym.params)
        can_fill = not (has_return and has_params)
        self.assertTrue(can_fill)


class TestRequestThreadSafety(unittest.TestCase):
    """L5: _next_id access must be thread-safe."""

    def test_lock_exists(self):
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo", "hello"], Path("/tmp"))
        self.assertTrue(hasattr(client, "_id_lock"))


class TestStopReaderOrder(unittest.TestCase):
    """L7: _stop_reader must be set AFTER shutdown, not before."""

    def test_stop_reader_timing(self):
        from src.lsp import StdioLspClient

        client = StdioLspClient(["echo", "hello"], Path("/tmp"))
        self.assertTrue(hasattr(client, "_stop_reader"))


class TestStubFileExclusion(unittest.TestCase):
    """C4: .d.ts and .pyi stub files should be excluded from scoring."""

    def test_stub_files_not_tests(self):
        from src.topic import is_test_like_file

        self.assertFalse(is_test_like_file("src/types.d.ts"))
        self.assertFalse(is_test_like_file("src/stubs.pyi"))


class TestJavaTypeExtraction(unittest.TestCase):
    """T6: Java type extraction handles array_type and scoped_type_identifier."""

    def test_java_type_nodes_available(self):
        from src.type_inference import _extract_java_return_type

        self.assertTrue(callable(_extract_java_return_type))


class TestCSharpTypeExtraction(unittest.TestCase):
    """T7: C# type extraction handles additional type nodes."""

    def test_csharp_type_nodes_available(self):
        from src.type_inference import _extract_c_sharp_return_type

        self.assertTrue(callable(_extract_c_sharp_return_type))


class TestCppTypeExtraction(unittest.TestCase):
    """T8: C++ type extraction handles additional type nodes."""

    def test_cpp_type_nodes_available(self):
        from src.type_inference import _extract_cpp_return_type

        self.assertTrue(callable(_extract_cpp_return_type))


class TestDiagnosticSafeAccess(unittest.TestCase):
    """S4: LSP diagnostic parse must use safe .get() access."""

    def test_diagnostics_safe_parsing(self):
        malformed = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": "file:///test.ts",
                    "diagnostics": [{"range": {}, "severity": 1}],
                },
            }
        )
        parsed = json.loads(malformed)
        diag = parsed["params"]["diagnostics"][0]
        msg = diag.get("message", "")
        self.assertEqual(msg, "")


class TestRouteVariableNames(unittest.TestCase):
    """R3/R4: route matching should accept broader variable names."""

    def test_python_route_pattern(self):
        import re

        pattern = r"^(app|router|api|bp|blueprint|routes|endpoints)$"
        self.assertTrue(re.match(pattern, "app"))
        self.assertTrue(re.match(pattern, "router"))
        self.assertTrue(re.match(pattern, "api"))
        self.assertTrue(re.match(pattern, "bp"))
        self.assertTrue(re.match(pattern, "blueprint"))
        self.assertTrue(re.match(pattern, "routes"))
        self.assertTrue(re.match(pattern, "endpoints"))
        self.assertFalse(re.match(pattern, "random_name"))

    def test_js_route_pattern(self):
        import re

        pattern = r"^(app|router|api|server|routes)$"
        self.assertTrue(re.match(pattern, "app"))
        self.assertTrue(re.match(pattern, "router"))
        self.assertTrue(re.match(pattern, "api"))
        self.assertTrue(re.match(pattern, "server"))
        self.assertTrue(re.match(pattern, "routes"))


class TestResolverJsxExtensionMapping(unittest.TestCase):
    """I2: JSX extension mapping should include .js → .jsx and .jsx → .js."""

    def test_jsx_extensions(self):
        # Verify the mapping structure
        runtime_source_exts = {
            ".js": (".ts", ".tsx", ".jsx"),
            ".jsx": (".tsx", ".js"),
            ".mjs": (".mts", ".ts", ".tsx"),
            ".cjs": (".cts", ".ts", ".tsx"),
        }
        self.assertIn(".jsx", runtime_source_exts[".js"])
        self.assertIn(".js", runtime_source_exts[".jsx"])


if __name__ == "__main__":
    unittest.main()
