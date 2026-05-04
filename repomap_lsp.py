from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LspServerSpec:
    language: str
    server_name: str
    command_names: tuple[str, ...]
    args: tuple[str, ...] = ()
    file_suffixes: tuple[str, ...] = ()
    root_markers: tuple[str, ...] = ()
    project_relative_candidates: tuple[str, ...] = ()


@dataclass
class LspServerDetection:
    language: str
    server_name: str
    status: str
    command: list[str] = field(default_factory=list)
    source: str = ""
    workspace_root: str = ""
    reason: str = ""


@dataclass
class LspDiagnostic:
    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    severity: str
    code: str
    message: str
    source: str = "lsp"


@dataclass
class LspLocation:
    file: str
    line: int
    col: int
    end_line: int
    end_col: int


@dataclass
class LspRunResult:
    server: str
    language: str
    status: str
    diagnostics: list[LspDiagnostic] = field(default_factory=list)
    definitions: list[LspLocation] = field(default_factory=list)
    references: list[LspLocation] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    workspace_root: str = ""
    reason: str = ""
    duration_ms: int = 0


LSP_SPECS: tuple[LspServerSpec, ...] = (
    LspServerSpec(
        language="typescript",
        server_name="typescript-language-server",
        command_names=("typescript-language-server",),
        args=("--stdio",),
        file_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
        root_markers=("package.json", "tsconfig.json", "jsconfig.json"),
        project_relative_candidates=("node_modules/.bin/typescript-language-server",),
    ),
    LspServerSpec(
        language="python",
        server_name="pyright-langserver",
        command_names=("pyright-langserver",),
        args=("--stdio",),
        file_suffixes=(".py",),
        root_markers=("pyproject.toml", "setup.py", "setup.cfg", ".venv"),
        project_relative_candidates=(".venv/bin/pyright-langserver",),
    ),
    LspServerSpec(
        language="python",
        server_name="pylsp",
        command_names=("pylsp",),
        file_suffixes=(".py",),
        root_markers=("pyproject.toml", "setup.py", "setup.cfg", ".venv"),
        project_relative_candidates=(".venv/bin/pylsp",),
    ),
    LspServerSpec(
        language="rust",
        server_name="rust-analyzer",
        command_names=("rust-analyzer",),
        file_suffixes=(".rs",),
        root_markers=("Cargo.toml",),
    ),
    LspServerSpec(
        language="go",
        server_name="gopls",
        command_names=("gopls",),
        file_suffixes=(".go",),
        root_markers=("go.mod", "go.work"),
    ),
)


def language_for_file(file_path: str | Path) -> str | None:
    suffix = Path(file_path).suffix.lower()
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return "typescript"
    if suffix == ".py":
        return "python"
    if suffix == ".rs":
        return "rust"
    if suffix == ".go":
        return "go"
    return None


def specs_for_language(language: str) -> list[LspServerSpec]:
    return [spec for spec in LSP_SPECS if spec.language == language]


def detect_project_languages(project_root: str | Path, max_files: int = 2000) -> list[str]:
    root = Path(project_root).resolve()
    languages: set[str] = set()
    skip_dirs = {".git", "node_modules", "dist", "build", ".venv", "venv", "target", "__pycache__"}
    seen = 0
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in skip_dirs]
        for file_name in file_names:
            language = language_for_file(file_name)
            if language:
                languages.add(language)
            seen += 1
            if seen >= max_files:
                return sorted(languages)
    return sorted(languages)


def detect_lsp_workspace_root(project_root: str | Path, file_path: str | Path | None, language: str) -> Path:
    root = Path(project_root).resolve()
    specs = specs_for_language(language)
    markers: tuple[str, ...] = tuple(dict.fromkeys(marker for spec in specs for marker in spec.root_markers))
    if not file_path:
        return root
    path = Path(file_path)
    abs_path = path if path.is_absolute() else root / path
    abs_path = abs_path.resolve()
    current = abs_path if abs_path.is_dir() else abs_path.parent
    while True:
        if current == root or root in current.parents:
            if any((current / marker).exists() for marker in markers):
                return current
            if current == root:
                break
            current = current.parent
            continue
        break
    return root


def _candidate_is_executable(path: Path) -> bool:
    return path.exists() and os.access(path, os.X_OK)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _npm_prefix_bin(command_name: str) -> list[Path]:
    try:
        completed = subprocess.run(
            ["npm", "config", "get", "prefix"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    prefix = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    if not prefix or prefix.lower() == "undefined":
        return []
    prefix_path = Path(prefix).expanduser()
    return [prefix_path / "bin" / command_name]


def _trusted_user_lsp_candidates(command_name: str) -> list[Path]:
    home = Path.home()
    candidates: list[Path] = [
        home / ".local" / "bin" / command_name,
        home / ".npm-global" / "bin" / command_name,
        home / ".cargo" / "bin" / command_name,
        home / "go" / "bin" / command_name,
        home / ".bun" / "bin" / command_name,
        home / ".yarn" / "bin" / command_name,
        home / ".config" / "yarn" / "global" / "node_modules" / ".bin" / command_name,
        home / ".local" / "share" / "pnpm" / command_name,
        home / ".local" / "share" / "nvim" / "mason" / "bin" / command_name,
    ]
    for base in (
        home / ".local" / "share" / "pnpm" / "global",
        home / ".local" / "share" / "pipx" / "venvs",
        home / ".local" / "share" / "uv" / "tools",
    ):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            candidate = child / "node_modules" / ".bin" / command_name
            if candidate.exists():
                candidates.append(candidate)
            candidate = child / "bin" / command_name
            if candidate.exists():
                candidates.append(candidate)
    candidates.extend(_npm_prefix_bin(command_name))
    return _dedupe_paths(candidates)


def detect_lsp_server(project_root: str | Path, language: str, file_path: str | Path | None = None) -> LspServerDetection:
    root = Path(project_root).resolve()
    workspace_root = detect_lsp_workspace_root(root, file_path, language)
    specs = specs_for_language(language)
    if not specs:
        return LspServerDetection(language, "", "missing", workspace_root=str(workspace_root), reason="unsupported language")
    for spec in specs:
        for candidate in spec.project_relative_candidates:
            candidate_path = workspace_root / candidate
            if _candidate_is_executable(candidate_path):
                return LspServerDetection(
                    language=language,
                    server_name=spec.server_name,
                    status="available",
                    command=[str(candidate_path), *spec.args],
                    source="project",
                    workspace_root=str(workspace_root),
                )
        for command_name in spec.command_names:
            resolved = shutil.which(command_name)
            if resolved:
                return LspServerDetection(
                    language=language,
                    server_name=spec.server_name,
                    status="available",
                    command=[resolved, *spec.args],
                    source="path",
                    workspace_root=str(workspace_root),
                )
            for candidate_path in _trusted_user_lsp_candidates(command_name):
                if _candidate_is_executable(candidate_path):
                    return LspServerDetection(
                        language=language,
                        server_name=spec.server_name,
                        status="available",
                        command=[str(candidate_path), *spec.args],
                        source="user",
                        workspace_root=str(workspace_root),
                    )
    return LspServerDetection(
        language=language,
        server_name=specs[0].server_name,
        status="missing",
        workspace_root=str(workspace_root),
        reason="local LSP server executable not found",
    )


def detect_lsp_servers(project_root: str | Path, languages: list[str] | None = None) -> list[LspServerDetection]:
    detected_languages = languages or detect_project_languages(project_root)
    return [detect_lsp_server(project_root, language) for language in detected_languages]


def _path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse
        return Path(unquote(urlparse(uri).path))
    return Path(uri)


def _json_rpc_frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def _read_lsp_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii", errors="replace").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


class StdioLspClient:
    def __init__(self, command: list[str], workspace_root: Path, timeout: float = 8.0):
        self.command = command
        self.workspace_root = workspace_root
        self.timeout = timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader: threading.Thread | None = None

    def __enter__(self) -> "StdioLspClient":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.command,
            cwd=self.workspace_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            try:
                message = _read_lsp_message(self.process.stdout)
            except Exception as exc:
                self._messages.put({"method": "$/repomapReadError", "params": {"message": str(exc)}})
                return
            if message is None:
                return
            self._messages.put(message)

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                message = self._messages.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if message.get("id") == request_id:
                return message
            # 请求期间可能收到 diagnostics 等通知；这里丢弃非目标消息，
            # 避免同一通知被反复放回队列导致请求超时。
            if "id" not in message:
                continue
            time.sleep(0.01)
        raise TimeoutError(f"LSP request timed out: {method}")

    def _send(self, payload: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(_json_rpc_frame(payload))
        self.process.stdin.flush()

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": _path_to_uri(self.workspace_root),
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {},
                        "synchronization": {},
                        "definition": {},
                        "references": {},
                    }
                },
            },
        )
        self.send_notification("initialized", {})

    def did_open(self, file_path: Path, language: str, text: str) -> None:
        self.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": _path_to_uri(file_path),
                    "languageId": _lsp_language_id(language, file_path),
                    "version": 1,
                    "text": text,
                }
            },
        )

    def _position_params(self, file_path: Path, line: int, character: int) -> dict[str, Any]:
        return {
            "textDocument": {"uri": _path_to_uri(file_path)},
            "position": {"line": line, "character": character},
        }

    def definition(self, file_path: Path, line: int, character: int) -> Any:
        response = self.request("textDocument/definition", self._position_params(file_path, line, character))
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    def references(self, file_path: Path, line: int, character: int) -> Any:
        params = self._position_params(file_path, line, character)
        params["context"] = {"includeDeclaration": True}
        response = self.request("textDocument/references", params)
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    def collect_diagnostics(self, file_paths: list[Path], language: str) -> list[dict[str, Any]]:
        expected_uris = {_path_to_uri(path) for path in file_paths}
        diagnostics: list[dict[str, Any]] = []
        deadline = time.time() + self.timeout
        while time.time() < deadline and expected_uris:
            try:
                message = self._messages.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if message.get("method") != "textDocument/publishDiagnostics":
                continue
            params = message.get("params", {})
            uri = params.get("uri", "")
            if uri in expected_uris:
                diagnostics.append(params)
                expected_uris.remove(uri)
        return diagnostics

    def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                try:
                    self.request("shutdown", {})
                except Exception:
                    pass
                try:
                    self.send_notification("exit", {})
                except Exception:
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        finally:
            self.process = None


def _lsp_language_id(language: str, file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if language == "typescript":
        if suffix == ".tsx":
            return "typescriptreact"
        if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
            return "javascriptreact" if suffix == ".jsx" else "javascript"
        return "typescript"
    return language


def _severity_name(value: int | None) -> str:
    return {1: "error", 2: "warning", 3: "info", 4: "info"}.get(value or 3, "info")


def _diagnostic_from_lsp(project_root: Path, params: dict[str, Any], item: dict[str, Any]) -> LspDiagnostic:
    file_path = _uri_to_path(params.get("uri", ""))
    try:
        rel_file = file_path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        rel_file = file_path.as_posix()
    range_row = item.get("range", {})
    start = range_row.get("start", {})
    end = range_row.get("end", {})
    code = item.get("code", "")
    return LspDiagnostic(
        file=rel_file,
        line=int(start.get("line", 0)) + 1,
        col=int(start.get("character", 0)) + 1,
        end_line=int(end.get("line", start.get("line", 0))) + 1,
        end_col=int(end.get("character", start.get("character", 0))) + 1,
        severity=_severity_name(item.get("severity")),
        code=str(code) if code is not None else "",
        message=str(item.get("message", "")),
        source=str(item.get("source", "lsp")),
    )


def collect_lsp_diagnostics(
    project_root: str | Path,
    files: list[str],
    timeout: float = 8.0,
    max_files: int = 20,
) -> list[LspRunResult]:
    root = Path(project_root).resolve()
    normalized_files = [Path(file) for file in files[:max_files]]
    by_language: dict[str, list[Path]] = {}
    for file_path in normalized_files:
        language = language_for_file(file_path)
        if not language:
            continue
        abs_path = file_path if file_path.is_absolute() else root / file_path
        if abs_path.exists() and abs_path.is_file():
            by_language.setdefault(language, []).append(abs_path.resolve())
    results: list[LspRunResult] = []
    for language, abs_files in sorted(by_language.items()):
        detection = detect_lsp_server(root, language, abs_files[0])
        if detection.status != "available":
            results.append(LspRunResult(
                server=detection.server_name or language,
                language=language,
                status="skipped",
                workspace_root=detection.workspace_root,
                reason=detection.reason,
            ))
            continue
        start = time.time()
        try:
            workspace_root = Path(detection.workspace_root)
            with StdioLspClient(detection.command, workspace_root, timeout=timeout) as client:
                client.initialize()
                for abs_file in abs_files:
                    client.did_open(abs_file, language, abs_file.read_text(encoding="utf-8", errors="replace"))
                raw_diagnostics = client.collect_diagnostics(abs_files, language)
            diagnostics = [
                _diagnostic_from_lsp(root, params, item)
                for params in raw_diagnostics
                for item in params.get("diagnostics", [])
            ]
            exit_code_status = "ok"
            results.append(LspRunResult(
                server=detection.server_name,
                language=language,
                status=exit_code_status,
                diagnostics=diagnostics,
                command=detection.command,
                workspace_root=detection.workspace_root,
                duration_ms=int((time.time() - start) * 1000),
            ))
        except TimeoutError as exc:
            results.append(LspRunResult(
                server=detection.server_name,
                language=language,
                status="timeout",
                command=detection.command,
                workspace_root=detection.workspace_root,
                reason=str(exc),
                duration_ms=int((time.time() - start) * 1000),
            ))
        except Exception as exc:
            results.append(LspRunResult(
                server=detection.server_name,
                language=language,
                status="failed",
                command=detection.command,
                workspace_root=detection.workspace_root,
                reason=str(exc),
                duration_ms=int((time.time() - start) * 1000),
            ))
    if not results:
        results.append(LspRunResult(server="lsp", language="unknown", status="skipped", reason="no supported files"))
    return results


def _location_from_lsp(project_root: Path, item: dict[str, Any]) -> LspLocation | None:
    uri = item.get("uri") or item.get("targetUri")
    raw_range = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
    if not uri or not isinstance(raw_range, dict):
        return None
    file_path = _uri_to_path(str(uri))
    try:
        rel_file = file_path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        rel_file = file_path.as_posix()
    start = raw_range.get("start", {})
    end = raw_range.get("end", {})
    return LspLocation(
        file=rel_file,
        line=int(start.get("line", 0)) + 1,
        col=int(start.get("character", 0)) + 1,
        end_line=int(end.get("line", start.get("line", 0))) + 1,
        end_col=int(end.get("character", start.get("character", 0))) + 1,
    )


def _normalize_lsp_locations(project_root: Path, value: Any) -> list[LspLocation]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    locations: list[LspLocation] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        location = _location_from_lsp(project_root, raw_item)
        if location is None:
            continue
        key = (location.file, location.line, location.col, location.end_line, location.end_col)
        if key in seen:
            continue
        seen.add(key)
        locations.append(location)
    return locations


def _symbol_position(project_root: Path, file_path: str, line: int, symbol_name: str) -> tuple[Path, int, int]:
    abs_file = (project_root / file_path).resolve()
    zero_based_line = max(0, line - 1)
    character = 0
    try:
        lines = abs_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if 0 <= zero_based_line < len(lines):
            index = lines[zero_based_line].find(symbol_name)
            if index >= 0:
                character = index
    except OSError:
        pass
    return abs_file, zero_based_line, character


def collect_lsp_symbol_evidence(
    project_root: str | Path,
    file_path: str,
    line: int,
    symbol_name: str,
    timeout: float = 8.0,
) -> LspRunResult:
    root = Path(project_root).resolve()
    language = language_for_file(file_path)
    if not language:
        return LspRunResult(server="lsp", language="unknown", status="skipped", reason="unsupported file type")
    abs_file, line_index, character = _symbol_position(root, file_path, line, symbol_name)
    if not abs_file.exists() or not abs_file.is_file():
        return LspRunResult(server="lsp", language=language, status="skipped", reason="file not found")
    detection = detect_lsp_server(root, language, abs_file)
    if detection.status != "available":
        return LspRunResult(
            server=detection.server_name or language,
            language=language,
            status="skipped",
            workspace_root=detection.workspace_root,
            reason=detection.reason,
        )
    start = time.time()
    try:
        workspace_root = Path(detection.workspace_root)
        with StdioLspClient(detection.command, workspace_root, timeout=timeout) as client:
            client.initialize()
            client.did_open(abs_file, language, abs_file.read_text(encoding="utf-8", errors="replace"))
            definitions = _normalize_lsp_locations(root, client.definition(abs_file, line_index, character))
            references = _normalize_lsp_locations(root, client.references(abs_file, line_index, character))
        return LspRunResult(
            server=detection.server_name,
            language=language,
            status="ok",
            definitions=definitions,
            references=references,
            command=detection.command,
            workspace_root=detection.workspace_root,
            duration_ms=int((time.time() - start) * 1000),
        )
    except TimeoutError as exc:
        return LspRunResult(
            server=detection.server_name,
            language=language,
            status="timeout",
            command=detection.command,
            workspace_root=detection.workspace_root,
            reason=str(exc),
            duration_ms=int((time.time() - start) * 1000),
        )
    except Exception as exc:
        return LspRunResult(
            server=detection.server_name,
            language=language,
            status="failed",
            command=detection.command,
            workspace_root=detection.workspace_root,
            reason=str(exc),
            duration_ms=int((time.time() - start) * 1000),
        )


def detection_to_dict(detection: LspServerDetection) -> dict[str, Any]:
    return {
        "language": detection.language,
        "server": detection.server_name,
        "status": detection.status,
        "command": detection.command,
        "source": detection.source,
        "workspaceRoot": detection.workspace_root,
        "reason": detection.reason,
    }


def run_result_to_dict(result: LspRunResult) -> dict[str, Any]:
    return {
        "server": result.server,
        "language": result.language,
        "status": result.status,
        "command": result.command,
        "workspaceRoot": result.workspace_root,
        "reason": result.reason,
        "durationMs": result.duration_ms,
        "diagnostics": [diagnostic.__dict__ for diagnostic in result.diagnostics],
        "definitions": [location.__dict__ for location in result.definitions],
        "references": [location.__dict__ for location in result.references],
    }
