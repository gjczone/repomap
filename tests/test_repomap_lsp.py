import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.lsp import (
    _json_rpc_frame,
    _read_lsp_message,
    _npm_prefix_bin,
    _trusted_user_lsp_candidates,
    collect_lsp_diagnostics,
    detect_lsp_server,
    detect_lsp_workspace_root,
)


def write_file(root: str | Path, relative_path: str, content: str) -> Path:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class RepoMapLspTests(unittest.TestCase):
    def test_json_rpc_frame_round_trip(self) -> None:
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        import io

        stream = io.BytesIO(_json_rpc_frame(payload))

        self.assertEqual(_read_lsp_message(stream), payload)

    def test_trusted_user_lsp_candidates_cover_common_tool_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as home_dir:
            home = Path(home_dir)
            command_name = "pyright-langserver"
            for relative in (
                ".local/bin/pyright-langserver",
                ".npm-global/bin/pyright-langserver",
                ".cargo/bin/pyright-langserver",
                "go/bin/pyright-langserver",
                ".bun/bin/pyright-langserver",
                ".yarn/bin/pyright-langserver",
                ".config/yarn/global/node_modules/.bin/pyright-langserver",
                ".local/share/pnpm/pyright-langserver",
                ".local/share/nvim/mason/bin/pyright-langserver",
                ".local/share/pnpm/global/5/node_modules/.bin/pyright-langserver",
                ".local/share/pipx/venvs/pyright/bin/pyright-langserver",
                ".local/share/uv/tools/pyright/bin/pyright-langserver",
            ):
                write_file(home, relative, "#!/bin/sh\nexit 0\n")

            with patch("pathlib.Path.home", return_value=home):
                with patch("src.lsp._npm_prefix_bin", return_value=[home / ".npm-global" / "bin" / command_name]):
                    candidates = _trusted_user_lsp_candidates(command_name)

            candidate_set = {path.relative_to(home).as_posix() for path in candidates if home in path.parents}
            self.assertIn(".local/bin/pyright-langserver", candidate_set)
            self.assertIn(".local/share/nvim/mason/bin/pyright-langserver", candidate_set)
            self.assertIn(".local/share/pnpm/global/5/node_modules/.bin/pyright-langserver", candidate_set)
            self.assertIn(".local/share/pipx/venvs/pyright/bin/pyright-langserver", candidate_set)
            self.assertIn(".local/share/uv/tools/pyright/bin/pyright-langserver", candidate_set)
            self.assertEqual(len(candidates), len({str(path) for path in candidates}))

    def test_npm_prefix_bin_uses_safe_config_lookup(self) -> None:
        completed = Mock(returncode=0, stdout="/tmp/npm-prefix\n")
        with patch("subprocess.run", return_value=completed) as run_mock:
            candidates = _npm_prefix_bin("typescript-language-server")

        self.assertEqual(candidates, [Path("/tmp/npm-prefix/bin/typescript-language-server")])
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0], ["npm", "config", "get", "prefix"])
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 2)

    def test_npm_prefix_bin_ignores_failures(self) -> None:
        with patch("subprocess.run", side_effect=TimeoutError("slow npm")):
            self.assertEqual(_npm_prefix_bin("pyright-langserver"), [])
        with patch("subprocess.run", return_value=Mock(returncode=1, stdout="")):
            self.assertEqual(_npm_prefix_bin("pyright-langserver"), [])

    def test_path_lsp_server_is_preferred_over_user_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "print('hi')\n")
            user_server = Path(project_root, "user-bin/pyright-langserver")
            user_server.parent.mkdir(parents=True)
            user_server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            user_server.chmod(user_server.stat().st_mode | stat.S_IXUSR)

            with patch("shutil.which", return_value="/usr/bin/pyright-langserver"):
                with patch("src.lsp._trusted_user_lsp_candidates", return_value=[user_server]):
                    detection = detect_lsp_server(project_root, "python", "main.py")

            self.assertEqual(detection.status, "available")
            self.assertEqual(detection.source, "path")
            self.assertEqual(detection.command[0], "/usr/bin/pyright-langserver")

    def test_workspace_root_uses_nearest_project_marker(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "package.json", "{}")
            write_file(project_root, "apps/web/package.json", "{}")
            write_file(project_root, "apps/web/src/App.tsx", "export function App() { return null }\n")

            root = detect_lsp_workspace_root(project_root, "apps/web/src/App.tsx", "typescript")

            self.assertEqual(root, Path(project_root, "apps/web").resolve())

    def test_project_local_lsp_server_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            server = Path(project_root, "node_modules/.bin/typescript-language-server")
            server.parent.mkdir(parents=True)
            server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            server.chmod(server.stat().st_mode | stat.S_IXUSR)
            write_file(project_root, "package.json", "{}")
            write_file(project_root, "src/App.tsx", "export function App() { return null }\n")

            with patch("shutil.which", return_value=None):
                with patch("src.lsp._trusted_user_lsp_candidates", return_value=[]):
                    detection = detect_lsp_server(project_root, "typescript", "src/App.tsx")

            self.assertEqual(detection.status, "available")
            self.assertEqual(detection.source, "project")
            self.assertEqual(detection.command[0], str(server.resolve()))

    def test_user_local_lsp_server_is_detected_when_not_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "print('hi')\n")
            user_server = Path(project_root, "user-bin/pyright-langserver")
            user_server.parent.mkdir(parents=True)
            user_server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            user_server.chmod(user_server.stat().st_mode | stat.S_IXUSR)

            with patch("shutil.which", return_value=None):
                with patch("src.lsp._trusted_user_lsp_candidates", return_value=[user_server]):
                    detection = detect_lsp_server(project_root, "python", "main.py")

            self.assertEqual(detection.status, "available")
            self.assertEqual(detection.source, "user")
            self.assertEqual(detection.command[0], str(user_server))

    def test_missing_lsp_server_is_reported_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "print('hi')\n")
            with patch("shutil.which", return_value=None):
                with patch("src.lsp._trusted_user_lsp_candidates", return_value=[]):
                    result = collect_lsp_diagnostics(project_root, ["main.py"])

            self.assertEqual(result[0].status, "skipped")
            self.assertIn("not found", result[0].reason)

    def test_fake_lsp_server_returns_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            server = write_file(
                project_root,
                "node_modules/.bin/typescript-language-server",
                """#!/usr/bin/env python3
import json
import sys


def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b'\\r\\n', b'\\n'):
            break
        key, value = line.decode('ascii').split(':', 1)
        headers[key.lower()] = value.strip()
    body = sys.stdin.buffer.read(int(headers.get('content-length', '0')))
    return json.loads(body.decode('utf-8'))


def send(payload):
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    sys.stdout.buffer.write(b'Content-Length: ' + str(len(body)).encode('ascii') + b'\\r\\n\\r\\n' + body)
    sys.stdout.buffer.flush()

while True:
    msg = read_msg()
    if msg is None:
        break
    method = msg.get('method')
    if method == 'initialize':
        send({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'capabilities': {}}})
    elif method == 'textDocument/didOpen':
        uri = msg['params']['textDocument']['uri']
        send({'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics', 'params': {
            'uri': uri,
            'diagnostics': [{
                'range': {'start': {'line': 0, 'character': 7}, 'end': {'line': 0, 'character': 10}},
                'severity': 1,
                'code': 'demo',
                'source': 'fake-lsp',
                'message': 'fake diagnostic'
            }]
        }})
    elif method == 'shutdown':
        send({'jsonrpc': '2.0', 'id': msg['id'], 'result': None})
    elif method == 'exit':
        break
""",
            )
            server.chmod(server.stat().st_mode | stat.S_IXUSR)
            write_file(project_root, "package.json", "{}")
            write_file(project_root, "src/app.ts", "const x = 1;\n")

            result = collect_lsp_diagnostics(project_root, ["src/app.ts"], timeout=3)

            self.assertEqual(result[0].status, "ok")
            self.assertEqual(len(result[0].diagnostics), 1)
            self.assertEqual(result[0].diagnostics[0].file, "src/app.ts")
            self.assertEqual(result[0].diagnostics[0].message, "fake diagnostic")
    def test_fake_lsp_server_returns_definition_and_references(self) -> None:
        from src.lsp import collect_lsp_symbol_evidence

        with tempfile.TemporaryDirectory() as project_root:
            server = write_file(
                project_root,
                "node_modules/.bin/typescript-language-server",
                """#!/usr/bin/env python3
import json
import sys


def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b'\\r\\n', b'\\n'):
            break
        key, value = line.decode('ascii').split(':', 1)
        headers[key.lower()] = value.strip()
    body = sys.stdin.buffer.read(int(headers.get('content-length', '0')))
    return json.loads(body.decode('utf-8'))


def send(payload):
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    sys.stdout.buffer.write(b'Content-Length: ' + str(len(body)).encode('ascii') + b'\\r\\n\\r\\n' + body)
    sys.stdout.buffer.flush()

while True:
    msg = read_msg()
    if msg is None:
        break
    method = msg.get('method')
    if method == 'initialize':
        send({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'capabilities': {'definitionProvider': True, 'referencesProvider': True}}})
    elif method == 'textDocument/definition':
        uri = msg['params']['textDocument']['uri']
        send({'jsonrpc': '2.0', 'id': msg['id'], 'result': {
            'uri': uri,
            'range': {'start': {'line': 0, 'character': 16}, 'end': {'line': 0, 'character': 22}}
        }})
    elif method == 'textDocument/references':
        uri = msg['params']['textDocument']['uri']
        send({'jsonrpc': '2.0', 'id': msg['id'], 'result': [
            {'uri': uri, 'range': {'start': {'line': 0, 'character': 16}, 'end': {'line': 0, 'character': 22}}},
            {'uri': uri, 'range': {'start': {'line': 2, 'character': 0}, 'end': {'line': 2, 'character': 6}}}
        ]})
    elif method == 'shutdown':
        send({'jsonrpc': '2.0', 'id': msg['id'], 'result': None})
    elif method == 'exit':
        break
""",
            )
            server.chmod(server.stat().st_mode | stat.S_IXUSR)
            write_file(project_root, "package.json", "{}")
            write_file(project_root, "src/app.ts", "export function helper() { return 1; }\n\nhelper();\n")

            result = collect_lsp_symbol_evidence(project_root, "src/app.ts", 1, "helper", timeout=3)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.definitions[0].file, "src/app.ts")
            self.assertEqual(result.definitions[0].line, 1)
            self.assertEqual(len(result.references), 2)
            self.assertEqual(result.references[1].line, 3)


if __name__ == "__main__":
    unittest.main()
