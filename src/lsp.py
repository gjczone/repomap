from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import select
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import json_dumps, json_loads

logger = logging.getLogger("repomap.lsp")

_MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB max LSP message body
_MAX_LSP_FILE_SIZE = 1_048_576  # 1 MiB，超过此大小的文件不送 LSP

# 按语言区分的 LSP 超时（秒），重型服务器需要更长启动/响应时间
_LSP_TIMEOUT_BY_LANGUAGE: dict[str, float] = {
    "typescript": 15.0,  # TypeScript 项目通常较大，需要更多时间初始化
    "python": 12.0,  # pyright 在大型项目上也需要更多时间
    "rust": 20.0,
    "java": 15.0,
    "kotlin": 15.0,
    "swift": 15.0,
    "cpp": 10.0,
    "c": 10.0,
    "csharp": 10.0,
}
DEFAULT_LSP_TIMEOUT = 8.0


def lsp_timeout_for(language: str) -> float:
    """返回适合语言的 LSP 超时值。"""
    return _LSP_TIMEOUT_BY_LANGUAGE.get(language, DEFAULT_LSP_TIMEOUT)


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


@dataclass
class LspSymbolInfo:
    """LSP documentSymbol 返回的符号树节点。"""

    name: str
    kind: int  # LSP SymbolKind 整数
    kind_name: str  # 可读名称："class", "function", "method", ...
    file: str
    line: int
    end_line: int = 0
    col: int = 0
    end_col: int = 0
    detail: str = ""
    children: list["LspSymbolInfo"] = field(default_factory=list)


@dataclass
class LspHoverInfo:
    """LSP hover 返回的符号类型/文档信息。"""

    file: str
    line: int
    col: int
    contents: str = ""  # 纯文本格式的 hover 内容


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


# 从 LSP_SPECS 推导 suffix → language 映射，避免手动维护重复列表。
_SUFFIX_TO_LANGUAGE: dict[str, str] = {}
for _spec in LSP_SPECS:
    for _suffix in _spec.file_suffixes:
        _SUFFIX_TO_LANGUAGE[_suffix] = _spec.language


def language_for_file(file_path: str | Path) -> str | None:
    return _SUFFIX_TO_LANGUAGE.get(Path(file_path).suffix.lower())


def specs_for_language(language: str) -> list[LspServerSpec]:
    return [spec for spec in LSP_SPECS if spec.language == language]


def detect_project_languages(
    project_root: str | Path, max_files: int = 2000
) -> list[str]:
    root = Path(project_root).resolve()
    languages: set[str] = set()
    skip_dirs = {
        ".git",
        "node_modules",
        "dist",
        "build",
        ".venv",
        "venv",
        "target",
        "__pycache__",
    }
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


def detect_lsp_workspace_root(
    project_root: str | Path, file_path: str | Path | None, language: str
) -> Path:
    root = Path(project_root).resolve()
    specs = specs_for_language(language)
    markers: tuple[str, ...] = tuple(
        dict.fromkeys(marker for spec in specs for marker in spec.root_markers)
    )
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
    """去重路径列表，使用 resolve() 解析 symlink 以正确去重。"""
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
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
    prefix = (
        completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    )
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


def detect_lsp_server(
    project_root: str | Path, language: str, file_path: str | Path | None = None
) -> LspServerDetection:
    root = Path(project_root).resolve()
    workspace_root = detect_lsp_workspace_root(root, file_path, language)
    specs = specs_for_language(language)
    if not specs:
        return LspServerDetection(
            language,
            "",
            "missing",
            workspace_root=str(workspace_root),
            reason="unsupported language",
        )
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


def detect_lsp_servers(
    project_root: str | Path, languages: list[str] | None = None
) -> list[LspServerDetection]:
    detected_languages = languages or detect_project_languages(project_root)
    return [
        detect_lsp_server(project_root, language) for language in detected_languages
    ]


def _path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse

        return Path(unquote(urlparse(uri).path))
    return Path(uri)


def _json_rpc_frame(payload: dict[str, Any]) -> bytes:
    body = json_dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


# 哨兵对象：区分流 EOF 和消息丢弃
_STREAM_EOF = object()  # 流正常关闭
_MESSAGE_SKIPPED = object()  # 消息被丢弃但流仍健康
_MAX_DISCARD_BYTES = 20 * 1024 * 1024  # 20MB: 丢弃路径的上限保护


def _read_lsp_message(stream: Any) -> Any:
    """Read a single LSP message from the stream. Returns dict, _STREAM_EOF, or _MESSAGE_SKIPPED."""
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return _STREAM_EOF
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii", errors="replace").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    try:
        length = int(headers.get("content-length", "0"))
    except (ValueError, TypeError):
        return _STREAM_EOF
    if length <= 0:
        return _STREAM_EOF
    if length > _MAX_CONTENT_LENGTH:
        logger.warning(
            "LSP message Content-Length %d exceeds maximum %d, discarding",
            length,
            _MAX_CONTENT_LENGTH,
        )
        # 消费 body 字节，防止级联失败，但有上限保护
        remaining = min(length, _MAX_DISCARD_BYTES)
        while remaining > 0:
            chunk = stream.read(min(remaining, 65536))
            if not chunk:
                return _STREAM_EOF
            remaining -= len(chunk)
        if length > _MAX_DISCARD_BYTES:
            logger.error(
                "LSP message too large (%d bytes), disconnecting",
                length,
            )
            return _STREAM_EOF
        return _MESSAGE_SKIPPED
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            return _STREAM_EOF
        body += chunk
    return json_loads(body.decode("utf-8", errors="replace"))


class StdioLspClient:
    def __init__(self, command: list[str], workspace_root: Path, timeout: float = 8.0):
        self.command = command
        self.workspace_root = workspace_root
        self.timeout = timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10000)
        self._notifications: list[dict[str, Any]] = []
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.server_capabilities: dict[str, Any] = {}
        self._opened_files: set[str] = set()  # 已成功 did_open 的文件路径

    def __enter__(self) -> "StdioLspClient":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def start(self) -> None:
        self._stop_event.clear()
        self.process = subprocess.Popen(
            self.command,
            cwd=self.workspace_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        started: list[threading.Thread] = []
        try:
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            started.append(self._reader)
            self._stderr_reader = threading.Thread(
                target=self._drain_stderr, daemon=True
            )
            self._stderr_reader.start()
            started.append(self._stderr_reader)
        except Exception:
            for t in started:
                if t.is_alive():
                    self._stop_event.set()
            self.process.kill()
            self.process.wait()
            # 关闭管道 FD，防止泄漏
            for stream in (
                self.process.stdin,
                self.process.stdout,
                self.process.stderr,
            ):
                if stream:
                    try:
                        stream.close()
                    except Exception:
                        pass
            raise

    def _read_loop(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                fd = self.process.stdout.fileno()
                ready, _, _ = select.select([fd], [], [], 30.0)
                if not ready:
                    logger.warning("LSP stdout idle for 30s, checking stop event")
                    if self._stop_event.is_set():
                        return
                    continue
                message = _read_lsp_message(self.process.stdout)
            except Exception as exc:
                if self._stop_event.is_set():
                    return  # close() 触发的异常，正常退出
                consecutive_errors += 1
                if consecutive_errors >= 10:
                    logger.error(
                        "LSP reader thread exiting after %d consecutive errors",
                        consecutive_errors,
                    )
                    return
                logger.warning(
                    "LSP message read error (%d/10): %s", consecutive_errors, exc
                )
                continue
            consecutive_errors = 0  # reset on successful read or non-error path
            if message is _STREAM_EOF:
                return  # 流关闭，正常退出
            if message is _MESSAGE_SKIPPED:
                continue  # 消息丢弃，继续读
            if message is None:
                return  # 保持兼容（理论上不应到达）
            try:
                self._messages.put(message, timeout=1.0)
            except queue.Full:
                logger.warning(
                    "LSP message queue full (%d), dropping oldest message",
                    self._messages.maxsize,
                )
                try:
                    self._messages.get_nowait()
                    self._messages.put_nowait(message)
                except queue.Empty:
                    self._messages.put_nowait(message)
                except queue.Full:
                    pass  # 极端情况：丢弃消息

    def _drain_stderr(self) -> None:
        """Continuously drain stderr to prevent pipe buffer deadlock."""
        if self.process is None or self.process.stderr is None:
            return
        while not self._stop_event.is_set():
            try:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    break
            except Exception:
                if self._stop_event.is_set():
                    return  # close() 触发的异常，正常退出
                logger.warning("Stderr drain failed unexpectedly", exc_info=True)
                break

    def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.process is not None and self.process.poll() is not None:
            exit_code = self.process.poll()
            stderr_tail = ""
            try:
                if self.process.stderr is not None:
                    remaining = self.process.stderr.read()
                    if remaining:
                        stderr_tail = remaining.decode(
                            "utf-8", errors="replace"
                        ).strip()[-200:]
            except Exception:
                logger.debug(
                    "Failed to read LSP stderr for error diagnostics", exc_info=True
                )
            detail = f"exit code {exit_code}"
            if stderr_tail:
                detail += f", stderr: {stderr_tail}"
            raise RuntimeError(
                f"LSP server {self.command[0]!r} already exited before request '{method}' ({detail})"
            )
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            # 进程崩溃时立即退出，避免等满整个超时窗口
            if self.process is not None and self.process.poll() is not None:
                break
            try:
                message = self._messages.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if message.get("id") == request_id:
                return message
            # 请求期间可能收到 diagnostics 等通知；缓冲到 _notifications 列表
            if "id" not in message:
                if (
                    len(self._notifications) < 500
                ):  # 防止异常 LSP 服务器导致内存无限增长
                    self._notifications.append(message)
                else:
                    logger.warning(
                        "_notifications list at capacity (%d), new notifications will be dropped",
                        len(self._notifications),
                    )
                continue
            # 非目标响应：LSP 单请求模式下不应出现，丢弃并记录
            logger.warning(
                "Discarding unmatched LSP response id=%s (expected %s) for method %r",
                message.get("id"),
                request_id,
                method,
            )
            time.sleep(0.01)
        # 超时后检查进程是否已退出，给出更精确的诊断
        if self.process is not None:
            exit_code = self.process.poll()
            if exit_code is not None:
                stderr_tail = ""
                try:
                    if self.process.stderr is not None:
                        remaining = self.process.stderr.read()
                        if remaining:
                            stderr_tail = remaining.decode(
                                "utf-8", errors="replace"
                            ).strip()[-200:]
                except Exception:
                    logger.debug(
                        "Failed to read LSP stderr for timeout diagnostics",
                        exc_info=True,
                    )
                detail = f"exit code {exit_code}"
                if stderr_tail:
                    detail += f", stderr: {stderr_tail}"
                raise RuntimeError(
                    f"LSP server {self.command[0]!r} exited during request ({detail})"
                )
        raise TimeoutError(f"LSP request timed out: {method}")

    def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP client not initialized: process or stdin is None")
        try:
            self.process.stdin.write(_json_rpc_frame(payload))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            # TOCTOU 竞态：进程可能在 poll() 返回存活后、stdin.write() 执行前崩溃
            exit_code = self.process.poll()
            stderr_tail = ""
            try:
                if self.process.stderr is not None:
                    remaining = self.process.stderr.read()
                    if remaining:
                        stderr_tail = remaining.decode(
                            "utf-8", errors="replace"
                        ).strip()[-200:]
            except Exception:
                logger.debug(
                    "Failed to read LSP stderr for error diagnostics", exc_info=True
                )
            detail = f"exit code {exit_code}"
            if stderr_tail:
                detail += f", stderr: {stderr_tail}"
            raise RuntimeError(
                f"LSP server {self.command[0]!r} crashed during send ({detail}): {exc}"
            ) from exc

    def initialize(self) -> None:
        response = self.request(
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
                        "hover": {},
                        "documentSymbol": {
                            "hierarchicalDocumentSymbolSupport": True,
                        },
                    }
                },
            },
        )
        self.server_capabilities = response.get("result", {}).get("capabilities", {})
        self.send_notification("initialized", {})

    MAX_FILE_SIZE = _MAX_LSP_FILE_SIZE  # 向后兼容

    def did_open(self, file_path: Path, language: str, text: str) -> None:
        if len(text.encode("utf-8", errors="replace")) > _MAX_LSP_FILE_SIZE:
            logger.warning(
                "Skipping LSP diagnostics for large file %s (%d bytes)",
                file_path,
                len(text.encode("utf-8", errors="replace")),
            )
            return
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
        self._opened_files.add(str(file_path.resolve()))

    def _position_params(
        self, file_path: Path, line: int, character: int
    ) -> dict[str, Any]:
        return {
            "textDocument": {"uri": _path_to_uri(file_path)},
            "position": {"line": line, "character": character},
        }

    def definition(self, file_path: Path, line: int, character: int) -> Any:
        if str(file_path.resolve()) not in self._opened_files:
            return None
        response = self.request(
            "textDocument/definition", self._position_params(file_path, line, character)
        )
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    def references(self, file_path: Path, line: int, character: int) -> Any:
        if str(file_path.resolve()) not in self._opened_files:
            return None
        params = self._position_params(file_path, line, character)
        params["context"] = {"includeDeclaration": True}
        response = self.request("textDocument/references", params)
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    def hover(self, file_path: Path, line: int, character: int) -> Any:
        if str(file_path.resolve()) not in self._opened_files:
            return None
        response = self.request(
            "textDocument/hover", self._position_params(file_path, line, character)
        )
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    def document_symbols(self, file_path: Path) -> Any:
        if str(file_path.resolve()) not in self._opened_files:
            return None
        response = self.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": _path_to_uri(file_path)}},
        )
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    def collect_diagnostics(
        self, file_paths: list[Path], language: str
    ) -> list[dict[str, Any]]:
        # 过滤掉未打开的文件
        opened_paths = [
            path for path in file_paths if str(path.resolve()) in self._opened_files
        ]
        if not opened_paths:
            return []

        expected_uris = {_path_to_uri(path) for path in opened_paths}
        diagnostics: list[dict[str, Any]] = []
        # 先检查 _notifications 列表中在 request() 期间缓存的诊断
        remaining_notifications: list[dict[str, Any]] = []
        for msg in self._notifications:
            if msg.get("method") != "textDocument/publishDiagnostics":
                remaining_notifications.append(msg)
                continue
            params = msg.get("params", {})
            uri = params.get("uri", "")
            if uri in expected_uris:
                diagnostics.append(params)
                expected_uris.discard(uri)
            else:
                remaining_notifications.append(msg)
        self._notifications = remaining_notifications

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
        process = self.process
        try:
            if process.poll() is None:
                try:
                    self.request("shutdown", {})
                except Exception:
                    logger.debug("LSP shutdown request failed", exc_info=True)
                try:
                    self.send_notification("exit", {})
                except Exception:
                    logger.debug("LSP exit notification failed", exc_info=True)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        logger.debug(
                            "LSP process kill failed (already dead)", exc_info=True
                        )
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        logger.debug("LSP process did not terminate after kill")
        finally:
            self._stop_event.set()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is None:
                    continue
                try:
                    stream.close()
                except Exception:
                    logger.debug(
                        "Stream close failed during LSP cleanup", exc_info=True
                    )
            if self._reader is not None and self._reader.is_alive():
                self._reader.join(timeout=3)
            if self._stderr_reader is not None and self._stderr_reader.is_alive():
                self._stderr_reader.join(timeout=3)
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
    if value is None or value == 0:
        return "warning"
    return {1: "error", 2: "warning", 3: "info", 4: "info"}.get(value, "warning")


def _diagnostic_from_lsp(
    project_root: Path, params: dict[str, Any], item: dict[str, Any]
) -> LspDiagnostic:
    file_path = _uri_to_path(params.get("uri", ""))
    try:
        rel_file = file_path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        rel_file = file_path.as_posix()
    range_row = item.get("range") or {}
    start = (range_row.get("start") or {}) if isinstance(range_row, dict) else {}
    end = (range_row.get("end") or {}) if isinstance(range_row, dict) else {}
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
            results.append(
                LspRunResult(
                    server=detection.server_name or language,
                    language=language,
                    status="skipped",
                    workspace_root=detection.workspace_root,
                    reason=detection.reason,
                )
            )
            continue
        start = time.time()
        try:
            workspace_root = Path(detection.workspace_root)
            with StdioLspClient(
                detection.command, workspace_root, timeout=timeout
            ) as client:
                client.initialize()
                for abs_file in abs_files:
                    client.did_open(
                        abs_file,
                        language,
                        abs_file.read_text(encoding="utf-8", errors="replace"),
                    )
                raw_diagnostics = client.collect_diagnostics(abs_files, language)
            diagnostics = [
                _diagnostic_from_lsp(root, params, item)
                for params in raw_diagnostics
                for item in params.get("diagnostics", [])
            ]
            exit_code_status = "ok"
            results.append(
                LspRunResult(
                    server=detection.server_name,
                    language=language,
                    status=exit_code_status,
                    diagnostics=diagnostics,
                    command=detection.command,
                    workspace_root=detection.workspace_root,
                    duration_ms=int((time.time() - start) * 1000),
                )
            )
        except TimeoutError as exc:
            results.append(
                LspRunResult(
                    server=detection.server_name,
                    language=language,
                    status="timeout",
                    command=detection.command,
                    workspace_root=detection.workspace_root,
                    reason=str(exc),
                    duration_ms=int((time.time() - start) * 1000),
                )
            )
        except Exception as exc:
            results.append(
                LspRunResult(
                    server=detection.server_name,
                    language=language,
                    status="failed",
                    command=detection.command,
                    workspace_root=detection.workspace_root,
                    reason=str(exc),
                    duration_ms=int((time.time() - start) * 1000),
                )
            )
    if not results:
        results.append(
            LspRunResult(
                server="lsp",
                language="unknown",
                status="skipped",
                reason="no supported files",
            )
        )
    return results


def _location_from_lsp(project_root: Path, item: dict[str, Any]) -> LspLocation | None:
    uri = item.get("uri") or item.get("targetUri")
    raw_range = (
        item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
    )
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
        key = (
            location.file,
            location.line,
            location.col,
            location.end_line,
            location.end_col,
        )
        if key in seen:
            continue
        seen.add(key)
        locations.append(location)
    return locations


def _symbol_position(
    project_root: Path,
    file_path: str,
    line: int,
    symbol_name: str,
    *,
    file_text: str | None = None,
) -> tuple[Path, int, int]:
    abs_file = (project_root / file_path).resolve()
    zero_based_line = max(0, line - 1)
    character = 0
    try:
        if file_text is not None:
            text = file_text
        else:
            if abs_file.stat().st_size > _MAX_LSP_FILE_SIZE:
                return abs_file, zero_based_line, character
            text = abs_file.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if 0 <= zero_based_line < len(lines):
            index = lines[zero_based_line].find(symbol_name)
            if index >= 0:
                character = index
    except OSError:
        logger.warning(
            "Symbol position fallback: cannot read file %s", abs_file, exc_info=True
        )
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
        return LspRunResult(
            server="lsp",
            language="unknown",
            status="skipped",
            reason="unsupported file type",
        )
    abs_file = (root / file_path).resolve()
    if not abs_file.exists() or not abs_file.is_file():
        return LspRunResult(
            server="lsp", language=language, status="skipped", reason="file not found"
        )
    # 读取文件一次，同时用于 _symbol_position 和 did_open
    try:
        file_text = abs_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return LspRunResult(
            server="lsp", language=language, status="skipped", reason="file read error"
        )
    abs_file, line_index, character = _symbol_position(
        root, file_path, line, symbol_name, file_text=file_text
    )
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
        with StdioLspClient(
            detection.command, workspace_root, timeout=timeout
        ) as client:
            client.initialize()
            client.did_open(abs_file, language, file_text)
            definitions = _normalize_lsp_locations(
                root, client.definition(abs_file, line_index, character)
            )
            references = _normalize_lsp_locations(
                root, client.references(abs_file, line_index, character)
            )
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


# ── LSP SymbolKind 整数 → 可读名称 ───────────────────────────────────────────
_LSP_SYMBOL_KIND_NAMES: dict[int, str] = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type parameter",
}


def _parse_lsp_symbol_tree(project_root: Path, raw: Any) -> list[LspSymbolInfo]:
    """递归解析 LSP documentSymbol 返回的嵌套符号结构。

    兼容两种 LSP 格式：
    - DocumentSymbol: range + selectionRange 在顶层，children 嵌套
    - SymbolInformation: location.range 含位置信息
    """
    if not isinstance(raw, list):
        return []
    result: list[LspSymbolInfo] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        kind = item.get("kind", 0)
        # 位置解析：优先使用顶层 range（DocumentSymbol），
        # 否则从 location.range 获取（SymbolInformation）
        range_data = item.get("range")
        if not range_data:
            range_data = item.get("location", {}).get("range", {})
        start = range_data.get("start", {})
        end = range_data.get("end", {})
        children = _parse_lsp_symbol_tree(project_root, item.get("children", []))
        line = int(start.get("line", 0)) + 1
        end_line = int(end.get("line", start.get("line", 0))) + 1
        result.append(
            LspSymbolInfo(
                name=name,
                kind=kind,
                kind_name=_LSP_SYMBOL_KIND_NAMES.get(kind, f"kind{kind}"),
                file="",
                line=line,
                end_line=end_line,
                col=int(start.get("character", 0)) + 1,
                end_col=int(end.get("character", start.get("character", 0))) + 1,
                detail=item.get("detail", ""),
                children=children,
            )
        )
    return result


def _parse_hover_response(raw: Any) -> str:
    """将 LSP hover 返回值转为纯文本。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        # MarkupContent: {"kind": "markdown", "value": "..."}
        value = raw.get("value")
        if isinstance(value, str) and value:
            return value
        # contents 可能是 MarkedString 或 MarkedString[]
        contents = raw.get("contents")
        if contents is None:
            return str(raw)
        if isinstance(contents, str):
            return contents
        if isinstance(contents, dict):
            return contents.get("value", str(contents))
        if isinstance(contents, list):
            parts = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("value", str(item)))
            return "\n".join(parts)
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("value", str(item)))
        return "\n".join(parts)
    return str(raw)


def collect_lsp_symbol_tree(
    project_root: str | Path,
    file_path: str,
    timeout: float | None = None,
) -> list[LspSymbolInfo]:
    root = Path(project_root).resolve()
    language = language_for_file(file_path)
    if not language:
        return []
    abs_file = (root / file_path).resolve()
    if not abs_file.exists() or not abs_file.is_file():
        return []
    detection = detect_lsp_server(root, language, abs_file)
    if detection.status != "available":
        return []
    effective_timeout: float = (
        timeout if timeout is not None else lsp_timeout_for(language)
    )
    try:
        workspace_root = Path(detection.workspace_root)
        with StdioLspClient(
            detection.command, workspace_root, timeout=effective_timeout
        ) as client:
            client.initialize()
            client.did_open(
                abs_file,
                language,
                abs_file.read_text(encoding="utf-8", errors="replace"),
            )
            raw = client.document_symbols(abs_file)
            # Issue #177: typescript-language-server（及少数重型 LSP）在 indexing 完成前
            # 对 document_symbols 可能返回空数组。首次空时短暂等待后重试一次。
            _SYMBOL_TREE_RETRY_DELAY = 1.5
            if not raw:
                time.sleep(_SYMBOL_TREE_RETRY_DELAY)
                try:
                    raw = client.document_symbols(abs_file)
                except Exception:
                    logger.debug("LSP retry document_symbols failed", exc_info=True)
        tree = _parse_lsp_symbol_tree(root, raw)
        # 回填 file 字段
        for node in _walk_symbol_tree(tree):
            node.file = file_path
        return tree
    except Exception as exc:
        logger.warning(f"LSP symbol collection failed for {file_path}: {exc}")
        return []


def _walk_symbol_tree(nodes: list[LspSymbolInfo]) -> "list[LspSymbolInfo]":
    """展开符号树为扁平列表（DFS）。"""
    result: list[LspSymbolInfo] = []
    for node in nodes:
        result.append(node)
        result.extend(_walk_symbol_tree(node.children))
    return result


def collect_lsp_full_evidence(
    project_root: str | Path,
    file_path: str,
    line: int,
    symbol_name: str,
    timeout: float | None = None,
) -> tuple[LspRunResult, LspHoverInfo | None]:
    """Collect definition + references + hover in a single LSP session.

    Returns (run_result, hover_info). The LSP client is created once and reused
    for all three requests, avoiding 2-3 separate process starts.
    """
    root = Path(project_root).resolve()
    language = language_for_file(file_path)
    if not language:
        empty = LspRunResult(
            server="lsp",
            language="unknown",
            status="skipped",
            reason="unsupported file type",
        )
        return empty, None
    abs_file = (root / file_path).resolve()
    if not abs_file.exists() or not abs_file.is_file():
        empty = LspRunResult(
            server="lsp",
            language=language,
            status="skipped",
            reason="file not found",
        )
        return empty, None
    try:
        file_text = abs_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        empty = LspRunResult(
            server="lsp",
            language=language,
            status="skipped",
            reason="file read error",
        )
        return empty, None
    abs_file, line_index, character = _symbol_position(
        root, file_path, line, symbol_name, file_text=file_text
    )
    detection = detect_lsp_server(root, language, abs_file)
    if detection.status != "available":
        empty = LspRunResult(
            server=detection.server_name or language,
            language=language,
            status="skipped",
            workspace_root=detection.workspace_root,
            reason=detection.reason,
        )
        return empty, None
    effective_timeout: float = (
        timeout if timeout is not None else lsp_timeout_for(language)
    )
    start = time.time()
    hover_info: LspHoverInfo | None = None
    try:
        workspace_root = Path(detection.workspace_root)
        with StdioLspClient(
            detection.command, workspace_root, timeout=effective_timeout
        ) as client:
            client.initialize()
            client.did_open(abs_file, language, file_text)
            definitions = _normalize_lsp_locations(
                root, client.definition(abs_file, line_index, character)
            )
            references = _normalize_lsp_locations(
                root, client.references(abs_file, line_index, character)
            )
            # hover in the same session — no extra process start
            raw_hover: Any = None
            try:
                raw_hover = client.hover(abs_file, line_index, character)
            except Exception:
                logger.warning(
                    "LSP hover failed for %s:%d (definition OK)",
                    file_path,
                    line,
                    exc_info=True,
                )

            # Issue #176: rust-analyzer 在 indexing 完成前对 definition/hover 可能返回空。
            # 当首次三个查询全部空（definition+references+hover）时，短暂等待后重试一次。
            # 仅针对 rust，避免对其他语言引入延迟。
            _RUST_RETRY_DELAY = 1.5
            hover_empty = raw_hover is None or _parse_hover_response(raw_hover) == ""
            first_pass_empty = not definitions and not references and hover_empty
            if language == "rust" and first_pass_empty:
                time.sleep(_RUST_RETRY_DELAY)
                try:
                    definitions = _normalize_lsp_locations(
                        root, client.definition(abs_file, line_index, character)
                    )
                except Exception:
                    logger.debug("Rust LSP retry definition failed", exc_info=True)
                try:
                    references = _normalize_lsp_locations(
                        root, client.references(abs_file, line_index, character)
                    )
                except Exception:
                    logger.debug("Rust LSP retry references failed", exc_info=True)
                try:
                    raw_hover = client.hover(abs_file, line_index, character)
                except Exception:
                    logger.debug("Rust LSP retry hover failed", exc_info=True)

            if raw_hover is not None:
                hover_info = LspHoverInfo(
                    file=file_path,
                    line=line,
                    col=character + 1,
                    contents=_parse_hover_response(raw_hover),
                )
        result = LspRunResult(
            server=detection.server_name,
            language=language,
            status="ok",
            definitions=definitions,
            references=references,
            command=detection.command,
            workspace_root=detection.workspace_root,
            duration_ms=int((time.time() - start) * 1000),
        )
        return result, hover_info
    except TimeoutError as exc:
        result = LspRunResult(
            server=detection.server_name,
            language=language,
            status="timeout",
            command=detection.command,
            workspace_root=detection.workspace_root,
            reason=str(exc),
            duration_ms=int((time.time() - start) * 1000),
        )
        return result, None
    except Exception as exc:
        result = LspRunResult(
            server=detection.server_name,
            language=language,
            status="failed",
            command=detection.command,
            workspace_root=detection.workspace_root,
            reason=str(exc),
            duration_ms=int((time.time() - start) * 1000),
        )
        return result, None


LSP_INSTALL_STRATEGIES: dict[str, dict[str, str]] = {
    "python": {"tool": "uv", "cmd": "uv pip install pyright", "detect": ".venv"},
    "typescript": {
        "tool": "npm",
        "cmd": "npm install -g typescript-language-server typescript",
        "detect": "package.json",
    },
    "rust": {
        "tool": "rustup",
        "cmd": "rustup component add rust-analyzer",
        "detect": "Cargo.toml",
    },
    "go": {
        "tool": "go",
        "cmd": "go install golang.org/x/tools/gopls@latest",
        "detect": "go.mod",
    },
}


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
