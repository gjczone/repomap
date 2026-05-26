#!/usr/bin/env python3
"""
RepoMap Check — 编译器/静态分析诊断模块

自动检测项目类型并运行对应诊断工具，将结构化错误信息与符号图结合，
帮助 AI 在修改代码后快速发现问题并定位到具体符号。

支持：TypeScript (tsc)、Rust (cargo check)、Python (mypy/ruff)、Go (go vet/build)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import json_dumps, json_loads


@dataclass
class DiagnosticIssue:
    """单个诊断问题"""

    tool: str
    file: str
    line: int
    col: int
    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    symbol: str | None = None  # 关联的符号名称（通过符号图解析）
    symbol_confidence: str = "none"  # 符号关联置信度: "exact" | "line" | "none"
    callers: list[str] = field(default_factory=list)  # 调用该符号的函数列表
    suggested_fix: str | None = None  # 建议的修复代码（如有）


@dataclass
class DiagnosticResult:
    """诊断结果"""

    tool: str
    command: str
    exit_code: int
    duration_ms: int
    skipped: bool = False
    skip_reason: str = ""
    errors: list[DiagnosticIssue] = field(default_factory=list)
    warnings: list[DiagnosticIssue] = field(default_factory=list)
    truncated: bool = False
    raw_excerpt: list[str] = field(default_factory=list)


class ProjectDetector:
    """检测项目类型"""

    @staticmethod
    def detect(project_root: Path) -> list[str]:
        """检测项目包含的语言类型列表"""
        types = set()

        # TypeScript
        if list(project_root.glob("tsconfig*.json")):
            types.add("typescript")

        # Rust
        if (project_root / "Cargo.toml").exists():
            types.add("rust")

        # Python
        if any(
            (project_root / f).exists()
            for f in ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"]
        ):
            types.add("python")

        # Go
        if (project_root / "go.mod").exists():
            types.add("go")

        # JavaScript (只有 TypeScript 不存在时才单独检测)
        if "typescript" not in types:
            if ProjectDetector._has_js_files(project_root):
                types.add("javascript")

        return sorted(types)

    @staticmethod
    def _has_js_files(project_root: Path) -> bool:
        """检查是否有 JS 文件（排除 node_modules）"""
        try:
            result = subprocess.run(
                [
                    "rg",
                    "--files",
                    "-g",
                    "!node_modules/**",
                    "-g",
                    "!dist/**",
                    "-g",
                    "!build/**",
                ],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n")[:100]:
                    if re.search(r"\.(mjs|cjs|js|jsx)$", line):
                        return True
        except Exception:
            pass

        # fallback: 简单遍历，剪掉依赖和构建目录，避免只因 node_modules 内文件误判为 JS 项目
        skip_dirs = {
            "node_modules",
            "dist",
            "build",
            ".git",
            ".venv",
            "venv",
            "__pycache__",
        }
        for root, dir_names, file_names in os.walk(project_root):
            dir_names[:] = [name for name in dir_names if name not in skip_dirs]
            if any(
                Path(file_name).suffix.lower() in {".js", ".jsx", ".mjs", ".cjs"}
                for file_name in file_names
            ):
                return True
        return False


class GitHelper:
    """Git 辅助工具"""

    @staticmethod
    def get_modified_files(
        project_root: Path, since_commit: str | None = None
    ) -> list[str]:
        """获取变更的文件列表"""
        try:
            from .git_backend import GitBackend

            git = GitBackend(str(project_root))
            files: set[str] = set()
            files.update(git.diff_cached_name_only())
            files.update(git.diff_name_only())
            if since_commit:
                files.update(git.diff_name_only(since=since_commit))
            return sorted(files)
        except Exception:
            return []


class DiagnosticRunner:
    """运行诊断工具"""

    def __init__(
        self,
        project_root: Path,
        max_items: int = 100,
        modified_files: list[str] | None = None,
    ):
        self.project_root = project_root.resolve()
        self.max_items = max_items
        self.modified_files = {
            normalized
            for file_path in (modified_files or [])
            if (normalized := self._normalize_safe_path(file_path)) is not None
        }  # 增量检查的文件列表统一为项目内相对路径

    def _normalize_safe_path(self, file_path: str) -> str | None:
        """将工具或 CLI 传入的文件路径归一为项目内相对路径；非法路径返回 None。"""
        if not file_path or file_path.startswith("-"):
            return None
        dangerous_chars = [";", "&", "|", "`", "$", "(", ")", "<", ">", "\\", "\x00"]
        if any(c in file_path for c in dangerous_chars):
            return None
        input_path = Path(file_path).expanduser()
        abs_path = (
            input_path.resolve()
            if input_path.is_absolute()
            else (self.project_root / input_path).resolve()
        )
        try:
            rel_path = abs_path.relative_to(self.project_root).as_posix()
        except ValueError:
            return None
        if rel_path in ("", ".") or any(part == ".." for part in Path(rel_path).parts):
            return None
        return rel_path

    def _safe_modified_files(self, suffixes: tuple[str, ...]) -> list[str]:
        return sorted(f for f in self.modified_files if f.endswith(suffixes))

    def run_all(self, types: list[str]) -> list[DiagnosticResult]:
        """运行所有适用的诊断工具"""
        results = []

        if "typescript" in types:
            results.append(self._run_tsc())
            # TypeScript 项目也运行 ESLint（如果有配置）
            if self._has_eslint_config():
                results.append(self._run_eslint())

        if "javascript" in types and "typescript" not in types:
            if self._has_eslint_config():
                results.append(self._run_eslint())
            else:
                results.append(
                    DiagnosticResult(
                        tool="eslint",
                        command="skip (no eslint config)",
                        exit_code=0,
                        duration_ms=0,
                        skipped=True,
                        skip_reason="eslint config not found",
                    )
                )

        if "rust" in types:
            results.append(self._run_cargo_check())

        if "python" in types:
            results.append(self._run_mypy())
            results.append(self._run_ruff())

        if "go" in types:
            results.append(self._run_go_vet())
            results.append(self._run_go_build())

        return results

    def _is_safe_path(self, file_path: str) -> bool:
        """检查文件路径是否安全（防止路径遍历和命令注入）"""
        return self._normalize_safe_path(file_path) is not None

    def _should_check_file(self, file_path: str) -> bool:
        """检查文件是否在增量检查列表中"""
        normalized = self._normalize_safe_path(file_path)
        if normalized is None:
            return False
        if not self.modified_files:
            return True  # 没有指定则检查全部
        return normalized in self.modified_files

    def _has_eslint_config(self) -> bool:
        """检查是否有 ESLint 配置"""
        config_files = [
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
        ]
        return any((self.project_root / f).exists() for f in config_files)

    def _has_cmd(self, cmd: str) -> bool:
        """检查命令是否存在"""
        return shutil.which(cmd) is not None

    def _now_ms(self) -> int:
        """获取当前毫秒时间戳"""
        import time

        return int(time.time() * 1000)

    def _run_command(self, cmd: list[str], tool_name: str) -> tuple[int, str, int]:
        """运行命令并返回 (exit_code, stdout, duration_ms)"""
        start = self._now_ms()
        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            duration = self._now_ms() - start
            output = result.stdout + result.stderr
            return result.returncode, output, duration
        except subprocess.TimeoutExpired:
            return -1, "Timeout after 120s", self._now_ms() - start
        except Exception as e:
            return -1, str(e), self._now_ms() - start

    def _run_tsc(self) -> DiagnosticResult:
        """运行 TypeScript 编译器检查"""
        tool = "tsc"
        cmd_str = "tsc --noEmit --pretty false"

        if not self._has_cmd("tsc"):
            return DiagnosticResult(
                tool=tool,
                command=cmd_str,
                exit_code=0,
                duration_ms=0,
                skipped=True,
                skip_reason="tsc not found",
            )

        cmd = ["tsc", "--noEmit", "--pretty", "false"]

        exit_code, output, duration = self._run_command(cmd, tool)
        errors, warnings = self._parse_tsc_output(output)

        return DiagnosticResult(
            tool=tool,
            command=" ".join(cmd),
            exit_code=exit_code,
            duration_ms=duration,
            errors=errors[: self.max_items],
            warnings=warnings[: self.max_items],
            truncated=len(errors) > self.max_items or len(warnings) > self.max_items,
            raw_excerpt=output.split("\n")[:30],
        )

    def _parse_tsc_output(
        self, output: str
    ) -> tuple[list[DiagnosticIssue], list[DiagnosticIssue]]:
        """解析 tsc 输出"""
        errors, warnings = [], []
        # 匹配: file.ts(42,8): error TS2345: message
        pattern = re.compile(
            r"^(.+)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$"
        )

        for line in output.split("\n"):
            match = pattern.match(line.strip())
            if match:
                file_path = match.group(1)
                # 增量检查过滤
                if not self._should_check_file(file_path):
                    continue

                issue = DiagnosticIssue(
                    tool="tsc",
                    file=file_path,
                    line=int(match.group(2)),
                    col=int(match.group(3)),
                    severity=match.group(4),
                    code=match.group(5),
                    message=match.group(6),
                )
                if issue.severity == "error":
                    errors.append(issue)
                else:
                    warnings.append(issue)

        return errors, warnings

    def _run_eslint(self) -> DiagnosticResult:
        """运行 ESLint"""
        tool = "eslint"
        cmd_str = "eslint . --ext .js,.jsx,.mjs,.cjs,.ts,.tsx --format json"

        if not self._has_cmd("eslint"):
            return DiagnosticResult(
                tool=tool,
                command=cmd_str,
                exit_code=0,
                duration_ms=0,
                skipped=True,
                skip_reason="eslint not found",
            )

        # 增量检查：只检查指定文件
        if self.modified_files:
            target_files = self._safe_modified_files(
                (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
            )
            if not target_files:
                return DiagnosticResult(
                    tool=tool,
                    command="skip (no matching files)",
                    exit_code=0,
                    duration_ms=0,
                    skipped=True,
                    skip_reason="no modified JS/TS files",
                )
            cmd = ["eslint", "--format", "json", "--"] + target_files
        else:
            cmd = [
                "eslint",
                ".",
                "--ext",
                ".js,.jsx,.mjs,.cjs,.ts,.tsx",
                "--format",
                "json",
            ]

        exit_code, output, duration = self._run_command(cmd, tool)
        errors, warnings = self._parse_eslint_output(output)

        return DiagnosticResult(
            tool=tool,
            command=" ".join(cmd[:6]) + "..." if len(cmd) > 6 else " ".join(cmd),
            exit_code=exit_code,
            duration_ms=duration,
            errors=errors[: self.max_items],
            warnings=warnings[: self.max_items],
            truncated=len(errors) > self.max_items or len(warnings) > self.max_items,
            raw_excerpt=output.split("\n")[:20],
        )

    def _parse_eslint_output(
        self, output: str
    ) -> tuple[list[DiagnosticIssue], list[DiagnosticIssue]]:
        """解析 ESLint JSON 输出"""
        errors, warnings = [], []
        try:
            data = json_loads(output) if output.strip() else []
            for record in data:
                file_path = record.get("filePath", "")
                for msg in record.get("messages", []):
                    severity_num = msg.get("severity", 0)
                    if severity_num == 2:
                        severity = "error"
                    elif severity_num == 1:
                        severity = "warning"
                    else:
                        severity = "info"

                    # 尝试获取修复建议
                    suggested_fix = None
                    fix_data = msg.get("fix")
                    if fix_data:
                        fix_text = fix_data.get("text", "")
                        if fix_text:
                            suggested_fix = fix_text[:200]  # 限制长度

                    issue = DiagnosticIssue(
                        tool="eslint",
                        file=file_path,
                        line=msg.get("line", 0),
                        col=msg.get("column", 0),
                        severity=severity,
                        code=msg.get("ruleId") or "eslint",
                        message=msg.get("message", ""),
                        suggested_fix=suggested_fix,
                    )
                    if severity == "error":
                        errors.append(issue)
                    elif severity == "warning":
                        warnings.append(issue)
        except ValueError:
            pass

        return errors, warnings

    def _run_cargo_check(self) -> DiagnosticResult:
        """运行 cargo check"""
        tool = "cargo-check"
        cmd_str = "cargo check --message-format json"

        if not self._has_cmd("cargo"):
            return DiagnosticResult(
                tool=tool,
                command=cmd_str,
                exit_code=0,
                duration_ms=0,
                skipped=True,
                skip_reason="cargo not found",
            )

        # 显示进度提示
        print(
            f"[{tool}] Running cargo check (may take a minute for large projects)...",
            file=sys.stderr,
        )

        cmd = ["cargo", "check", "--message-format", "json"]
        exit_code, output, duration = self._run_command(cmd, tool)
        errors, warnings = self._parse_cargo_output(output)

        # 显示完成提示
        print(f"[{tool}] Completed in {duration}ms", file=sys.stderr)

        return DiagnosticResult(
            tool=tool,
            command=" ".join(cmd),
            exit_code=exit_code,
            duration_ms=duration,
            errors=errors[: self.max_items],
            warnings=warnings[: self.max_items],
            truncated=len(errors) > self.max_items or len(warnings) > self.max_items,
            raw_excerpt=output.split("\n")[:30],
        )

    def _parse_cargo_output(
        self, output: str
    ) -> tuple[list[DiagnosticIssue], list[DiagnosticIssue]]:
        """解析 cargo JSON 输出"""
        errors, warnings = [], []

        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json_loads(line)
                if obj.get("reason") != "compiler-message":
                    continue
                msg = obj.get("message", {})
                level = msg.get("level", "")
                if level not in ("error", "warning"):
                    continue

                spans = msg.get("spans", [])
                primary = next(
                    (s for s in spans if s.get("is_primary")),
                    spans[0] if spans else {},
                )

                file_path = primary.get("file_name", "")
                # 增量检查过滤
                if not self._should_check_file(file_path):
                    continue

                # 尝试提取修复建议
                suggested_fix = None
                children = msg.get("children", [])
                for child in children:
                    if child.get("level") == "help":
                        suggested_fix = child.get("message", "")[:200]
                        break

                issue = DiagnosticIssue(
                    tool="cargo",
                    file=file_path,
                    line=primary.get("line_start", 0),
                    col=primary.get("column_start", 0),
                    severity=level,
                    code=(msg.get("code") or {}).get("code", ""),
                    message=msg.get("message", ""),
                    suggested_fix=suggested_fix,
                )
                if level == "error":
                    errors.append(issue)
                else:
                    warnings.append(issue)
            except ValueError:
                continue

        return errors, warnings

    def _run_mypy(self) -> DiagnosticResult:
        """运行 mypy 类型检查"""
        tool = "mypy"
        cmd_str = "mypy . --show-error-codes --ignore-missing-imports"

        if not self._has_cmd("mypy") and not self._has_cmd("dmypy"):
            return DiagnosticResult(
                tool=tool,
                command=cmd_str,
                exit_code=0,
                duration_ms=0,
                skipped=True,
                skip_reason="mypy/dmypy not found",
            )

        # 增量检查：只检查指定文件
        if self.modified_files:
            target_files = self._safe_modified_files((".py",))
            if not target_files:
                return DiagnosticResult(
                    tool=tool,
                    command="skip (no matching files)",
                    exit_code=0,
                    duration_ms=0,
                    skipped=True,
                    skip_reason="no modified Python files",
                )
        else:
            target_files = ["."]

        # 优先使用 dmypy daemon 模式（更快）
        use_daemon = (
            os.getenv("USE_DAEMON_MYPY", "1") == "1"
            and self._has_cmd("dmypy")
            and target_files == ["."]
        )

        if use_daemon:
            cmd = [
                "dmypy",
                "run",
                "--",
                "--show-error-codes",
                "--hide-error-context",
                "--no-color-output",
                "--ignore-missing-imports",
            ] + target_files
        else:
            cmd = [
                "mypy",
                "--show-error-codes",
                "--hide-error-context",
                "--no-color-output",
                "--ignore-missing-imports",
                "--",
            ] + target_files

        exit_code, output, duration = self._run_command(cmd, tool)
        errors, warnings = self._parse_mypy_output(output)

        return DiagnosticResult(
            tool=tool,
            command=" ".join(cmd) if not use_daemon else "dmypy run ...",
            exit_code=exit_code,
            duration_ms=duration,
            errors=errors[: self.max_items],
            warnings=warnings[: self.max_items],
            truncated=len(errors) > self.max_items or len(warnings) > self.max_items,
            raw_excerpt=output.split("\n")[:30],
        )

    def _parse_mypy_output(
        self, output: str
    ) -> tuple[list[DiagnosticIssue], list[DiagnosticIssue]]:
        """解析 mypy 输出"""
        errors, warnings = [], []
        # 匹配: file.py:42: error: message [code]
        pattern = re.compile(r"^(.+\.py):(\d+):\s*(error|warning|note):\s+(.+)$")

        for line in output.split("\n"):
            match = pattern.match(line)
            if match:
                msg = match.group(4)
                code = "mypy"
                code_match = re.search(r"\[([^\]]+)\]\s*$", msg)
                if code_match:
                    code = code_match.group(1)

                severity = match.group(3)
                if severity == "note":
                    severity = "info"

                issue = DiagnosticIssue(
                    tool="mypy",
                    file=match.group(1),
                    line=int(match.group(2)),
                    col=0,
                    severity=severity,
                    code=code,
                    message=msg,
                )
                if severity == "error":
                    errors.append(issue)
                else:
                    warnings.append(issue)

        return errors, warnings

    def _run_ruff(self) -> DiagnosticResult:
        """运行 ruff lint"""
        tool = "ruff"
        cmd_str = "ruff check . --output-format json"

        if not self._has_cmd("ruff"):
            return DiagnosticResult(
                tool=tool,
                command=cmd_str,
                exit_code=0,
                duration_ms=0,
                skipped=True,
                skip_reason="ruff not found",
            )

        # 增量检查：只检查指定文件
        if self.modified_files:
            target_files = self._safe_modified_files((".py",))
            if not target_files:
                return DiagnosticResult(
                    tool=tool,
                    command="skip (no matching files)",
                    exit_code=0,
                    duration_ms=0,
                    skipped=True,
                    skip_reason="no modified Python files",
                )
            cmd = ["ruff", "check", "--output-format", "json", "--"] + target_files
        else:
            cmd = ["ruff", "check", ".", "--output-format", "json"]

        exit_code, output, duration = self._run_command(cmd, tool)
        errors, warnings = self._parse_ruff_output(output)

        return DiagnosticResult(
            tool=tool,
            command=" ".join(cmd[:5]) + "..." if len(cmd) > 5 else " ".join(cmd),
            exit_code=exit_code,
            duration_ms=duration,
            errors=errors[: self.max_items],
            warnings=warnings[: self.max_items],
            truncated=len(errors) > self.max_items,
            raw_excerpt=output.split("\n")[:20],
        )

    def _parse_ruff_output(
        self, output: str
    ) -> tuple[list[DiagnosticIssue], list[DiagnosticIssue]]:
        """解析 ruff JSON 输出，尝试获取修复建议"""
        errors = []
        try:
            data = json_loads(output) if output.strip() else []
            for item in data:
                loc = item.get("location", {})

                # 尝试获取修复建议
                suggested_fix = None
                fix_data = item.get("fix")
                if fix_data:
                    fix_content = fix_data.get("content", "")
                    if fix_content:
                        suggested_fix = fix_content[:200]

                issue = DiagnosticIssue(
                    tool="ruff",
                    file=item.get("filename", ""),
                    line=loc.get("row", 0),
                    col=loc.get("column", 0),
                    severity="error",
                    code=item.get("code", "ruff"),
                    message=item.get("message", ""),
                    suggested_fix=suggested_fix,
                )
                errors.append(issue)
        except ValueError:
            pass

        return errors, []

    def _run_go_vet(self) -> DiagnosticResult:
        """运行 go vet"""
        tool = "go-vet"
        cmd_str = "go vet ./..."

        if not self._has_cmd("go"):
            return DiagnosticResult(
                tool=tool,
                command=cmd_str,
                exit_code=0,
                duration_ms=0,
                skipped=True,
                skip_reason="go not found",
            )

        # 增量检查：只检查指定文件
        if self.modified_files:
            target_files = self._safe_modified_files((".go",))
            if not target_files:
                return DiagnosticResult(
                    tool=tool,
                    command="skip (no matching files)",
                    exit_code=0,
                    duration_ms=0,
                    skipped=True,
                    skip_reason="no modified Go files",
                )
            cmd = ["go", "vet", "./..."]
        else:
            cmd = ["go", "vet", "./..."]

        exit_code, output, duration = self._run_command(cmd, tool)
        errors, _ = self._parse_go_output(output)

        return DiagnosticResult(
            tool=tool,
            command=" ".join(cmd),
            exit_code=exit_code,
            duration_ms=duration,
            errors=errors[: self.max_items],
            truncated=len(errors) > self.max_items,
            raw_excerpt=output.split("\n")[:30],
        )

    def _run_go_build(self) -> DiagnosticResult:
        """运行 go build"""
        tool = "go-build"
        cmd_str = "go build ./..."

        if not self._has_cmd("go"):
            return DiagnosticResult(
                tool=tool,
                command=cmd_str,
                exit_code=0,
                duration_ms=0,
                skipped=True,
                skip_reason="go not found",
            )

        # 增量检查：只检查指定文件
        if self.modified_files:
            target_files = self._safe_modified_files((".go",))
            if not target_files:
                return DiagnosticResult(
                    tool=tool,
                    command="skip (no matching files)",
                    exit_code=0,
                    duration_ms=0,
                    skipped=True,
                    skip_reason="no modified Go files",
                )
            # go build 需要包路径，这里简化处理，检查整个项目但过滤错误
            cmd = ["go", "build", "./..."]
        else:
            cmd = ["go", "build", "./..."]

        exit_code, output, duration = self._run_command(cmd, tool)
        errors, _ = self._parse_go_output(output)

        # 增量检查：过滤错误
        if self.modified_files:
            errors = [e for e in errors if self._should_check_file(e.file)]

        return DiagnosticResult(
            tool=tool,
            command=" ".join(cmd),
            exit_code=exit_code,
            duration_ms=duration,
            errors=errors[: self.max_items],
            truncated=len(errors) > self.max_items,
            raw_excerpt=output.split("\n")[:30],
        )

    def _parse_go_output(
        self, output: str
    ) -> tuple[list[DiagnosticIssue], list[DiagnosticIssue]]:
        """解析 go vet/build 输出"""
        errors = []
        # 匹配: file.go:42:8: message
        pattern = re.compile(r"^(.+\.go):(\d+):(\d+):\s+(.+)$")

        for line in output.split("\n"):
            match = pattern.match(line)
            if match:
                issue = DiagnosticIssue(
                    tool="go",
                    file=match.group(1),
                    line=int(match.group(2)),
                    col=int(match.group(3)),
                    severity="error",
                    code="go",
                    message=match.group(4),
                )
                errors.append(issue)

        return errors, []


class RepoMapChecker:
    """RepoMap 诊断检查器主类"""

    def __init__(self, project_root: str | Path, max_items: int = 100):
        self.project_root = Path(project_root).resolve()
        self.max_items = max_items
        self.detector = ProjectDetector()
        # runner 延迟初始化，以便传入 modified_files

    def check(
        self,
        types: list[str] | None = None,
        resolve_symbols: bool = True,
        symbols_map: dict[str, Any] | None = None,
        since_commit: str | None = None,
        modified_files: list[str] | None = None,
        with_lsp: bool = False,
        lsp_timeout: float = 8.0,
        lsp_max_files: int = 20,
        graph: Any = None,
    ) -> dict[str, Any]:
        """
        运行诊断检查

        Args:
            types: 指定要检查的语言类型，None 则自动检测
            resolve_symbols: 是否将错误位置解析为符号名称
            symbols_map: 符号图，用于解析错误位置到符号
            since_commit: 检查自某 commit 以来的变更（如 "HEAD~1"）
            modified_files: 显式指定要检查的文件列表（与 since_commit 互斥）
            graph: 可选的 RepoGraph，用于为诊断问题附加上下文调用者信息

        Returns:
            结构化的诊断报告
        """
        # 处理增量检查参数
        target_files = modified_files
        if since_commit and not modified_files:
            target_files = GitHelper.get_modified_files(self.project_root, since_commit)

        detected_types = types or self.detector.detect(self.project_root)

        if not detected_types:
            return {
                "timestamp": self._get_timestamp(),
                "project_root": str(self.project_root),
                "status": "unknown",
                "message": "No supported project type detected",
                "types": [],
                "runs": [],
                "summary": {
                    "total_errors": 0,
                    "total_warnings": 0,
                    "files_with_errors": 0,
                },
                "errors_by_file": {},
                "incremental": {
                    "enabled": target_files is not None,
                    "files_checked": target_files or [],
                },
            }

        # 初始化 runner，传入增量检查文件列表
        self.runner = DiagnosticRunner(self.project_root, self.max_items, target_files)

        # 运行所有诊断工具
        results = self.runner.run_all(detected_types)
        if with_lsp:
            results.extend(
                self._run_lsp_diagnostics(target_files, lsp_timeout, lsp_max_files)
            )

        # 解析符号关联
        if resolve_symbols and symbols_map:
            self._resolve_symbols(results, symbols_map, graph=graph)

        # 构建报告
        report = self._build_report(results, detected_types, target_files)
        return report

    def _run_lsp_diagnostics(
        self,
        target_files: list[str] | None,
        lsp_timeout: float,
        lsp_max_files: int,
    ) -> list[DiagnosticResult]:
        if not target_files:
            return [
                DiagnosticResult(
                    tool="lsp",
                    command="repomap lsp diagnostics",
                    exit_code=0,
                    duration_ms=0,
                    skipped=True,
                    skip_reason="no explicit files; pass --modified-file or use diagnostics --files",
                )
            ]
        from .lsp import collect_lsp_diagnostics

        diagnostic_results: list[DiagnosticResult] = []
        for run in collect_lsp_diagnostics(
            self.project_root,
            target_files,
            timeout=lsp_timeout,
            max_files=lsp_max_files,
        ):
            issues = [
                DiagnosticIssue(
                    tool=f"lsp:{run.server}",
                    file=item.file,
                    line=item.line,
                    col=item.col,
                    severity=item.severity,
                    code=item.code,
                    message=item.message,
                )
                for item in run.diagnostics
            ]
            errors = [issue for issue in issues if issue.severity == "error"]
            warnings = [issue for issue in issues if issue.severity != "error"]
            skipped = run.status == "skipped"
            exit_code = 1 if run.status in {"failed", "timeout"} else 0
            diagnostic_results.append(
                DiagnosticResult(
                    tool=f"lsp:{run.server}",
                    command=" ".join(run.command)
                    if run.command
                    else "repomap lsp diagnostics",
                    exit_code=exit_code,
                    duration_ms=run.duration_ms,
                    skipped=skipped,
                    skip_reason=run.reason if skipped else "",
                    errors=errors,
                    warnings=warnings,
                    raw_excerpt=[run.reason] if run.reason and not skipped else [],
                )
            )
        return diagnostic_results

    def _get_timestamp(self) -> str:
        """获取 ISO 格式时间戳"""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _resolve_symbols(
        self,
        results: list[DiagnosticResult],
        symbols_map: dict[str, Any],
        graph: Any = None,
    ) -> None:
        """将错误位置解析为符号名称，并计算置信度；可选附加上下文调用者。"""
        # 构建文件 -> 符号列表 的映射，包含行号范围
        file_symbols: dict[str, list[tuple[str, int, int, str]]] = {}
        for symbol_id, symbol in symbols_map.items():

            def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
                if hasattr(obj, attr):
                    return getattr(obj, attr, default)
                elif isinstance(obj, dict):
                    return obj.get(attr, default)
                return default

            file_path = _get_attr(symbol, "file", "")
            line = _get_attr(symbol, "line", 0)
            end_line = _get_attr(symbol, "end_line", line)
            name = _get_attr(symbol, "name", "")
            if file_path:
                if file_path not in file_symbols:
                    file_symbols[file_path] = []
                file_symbols[file_path].append((name, line, end_line, symbol_id))

        # 为每个 issue 查找对应符号
        for result in results:
            for issue in result.errors + result.warnings:
                file_key = issue.file
                if file_key.startswith("./"):
                    file_key = file_key[2:]

                candidates = file_symbols.get(file_key, [])
                best_match = None
                best_match_id = None
                best_confidence = "none"

                for name, sym_line, sym_end_line, sym_id in candidates:
                    if sym_line <= issue.line <= max(sym_end_line, sym_line + 50):
                        if issue.line == sym_line:
                            confidence = "exact"
                        else:
                            confidence = "line"

                        if confidence == "exact" or best_confidence == "none":
                            best_match = name
                            best_match_id = sym_id
                            best_confidence = confidence
                            if confidence == "exact":
                                break

                if best_match:
                    issue.symbol = best_match
                    issue.symbol_confidence = best_confidence
                    # 附加上下文调用者信息
                    if graph is not None and best_match_id:
                        issue.callers = [
                            graph.symbols[e.source].name
                            for e in graph.incoming.get(best_match_id, [])
                            if e.kind == "call" and e.source in graph.symbols
                        ][:5]

    def _build_report(
        self,
        results: list[DiagnosticResult],
        types: list[str],
        modified_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """构建最终报告"""
        total_errors = sum(len(r.errors) for r in results)
        total_warnings = sum(len(r.warnings) for r in results)

        # 按文件分组错误
        errors_by_file: dict[str, list[dict]] = {}
        for result in results:
            for issue in result.errors + result.warnings:
                file_key = issue.file or "unknown"
                if file_key not in errors_by_file:
                    errors_by_file[file_key] = []
                errors_by_file[file_key].append(
                    {
                        "tool": issue.tool,
                        "line": issue.line,
                        "col": issue.col,
                        "severity": issue.severity,
                        "code": issue.code,
                        "message": issue.message,
                        "symbol": issue.symbol,
                        "symbol_confidence": issue.symbol_confidence,
                        "callers": issue.callers,
                        "suggested_fix": issue.suggested_fix,
                    }
                )

        # 构建 runs 详情
        runs = []
        for r in results:
            run_data = {
                "tool": r.tool,
                "command": r.command,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "skipped": r.skipped,
                "error_count": len(r.errors),
                "warning_count": len(r.warnings),
                "truncated": r.truncated,
            }
            if r.skip_reason:
                run_data["skip_reason"] = r.skip_reason
            if not r.skipped and r.exit_code != 0 and not r.errors and not r.warnings:
                run_data["tool_failure_reason"] = (
                    "Tool exited non-zero but no structured errors parsed"
                )
            if not r.skipped:
                run_data["raw_excerpt"] = list(r.raw_excerpt[:10])
                run_data["errors"] = [
                    {
                        "file": e.file,
                        "line": e.line,
                        "col": e.col,
                        "code": e.code,
                        "message": e.message,
                        "symbol": e.symbol,
                        "symbol_confidence": e.symbol_confidence,
                        "callers": e.callers,
                        "suggested_fix": e.suggested_fix,
                    }
                    for e in r.errors[:20]
                ]
            runs.append(run_data)

        tool_failures = [r for r in results if not r.skipped and r.exit_code != 0]
        tools_run = len([r for r in results if not r.skipped])
        tools_skipped = len([r for r in results if r.skipped])
        message = ""
        if total_errors > 0 or tool_failures:
            status = "failed"
        elif total_warnings > 0:
            status = "warning"
        elif tools_run == 0 and tools_skipped > 0:
            status = "unknown"
            message = "Project type detected but no diagnostic tools ran"
        else:
            status = "passed"

        return {
            "timestamp": self._get_timestamp(),
            "project_root": str(self.project_root),
            "status": status,
            "message": message,
            "types": types,
            "incremental": {
                "enabled": modified_files is not None,
                "files_checked": modified_files or [],
                "files_count": len(modified_files) if modified_files else 0,
            },
            "runs": runs,
            "summary": {
                "total_errors": total_errors,
                "total_warnings": total_warnings,
                "files_with_errors": len(errors_by_file),
                "tools_run": tools_run,
                "tools_skipped": tools_skipped,
                "tool_failures": len(tool_failures),
            },
            "errors_by_file": dict(
                sorted(errors_by_file.items(), key=lambda x: len(x[1]), reverse=True)[
                    :20
                ]
            ),
        }


def check_project(
    project_root: str,
    types: list[str] | None = None,
    max_items: int = 100,
    symbols_map: dict[str, Any] | None = None,
    since_commit: str | None = None,
    modified_files: list[str] | None = None,
    with_lsp: bool = False,
    lsp_timeout: float = 8.0,
    lsp_max_files: int = 20,
) -> dict[str, Any]:
    """
    便捷函数：检查项目诊断

    Args:
        project_root: 项目根目录路径
        types: 指定语言类型，None 则自动检测
        max_items: 每种工具最多返回的问题数
        symbols_map: 可选的符号图，用于关联错误到符号
        since_commit: 检查自某 commit 以来的变更
        modified_files: 显式指定要检查的文件列表

    Returns:
        诊断报告字典
    """
    checker = RepoMapChecker(project_root, max_items)
    return checker.check(
        types=types,
        resolve_symbols=symbols_map is not None,
        symbols_map=symbols_map,
        since_commit=since_commit,
        modified_files=modified_files,
        with_lsp=with_lsp,
        lsp_timeout=lsp_timeout,
        lsp_max_files=lsp_max_files,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python repomap_check.py <project_root> [types...]")
        print("       python repomap_check.py <project_root> --since HEAD~1")
        print("       python repomap_check.py <project_root> --files file1.py file2.py")
        print("Example: python repomap_check.py ./my-project typescript")
        sys.exit(1)

    root = sys.argv[1]

    # 解析参数
    types = None
    since_commit = None
    modified_files = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--since" and i + 1 < len(sys.argv):
            since_commit = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--files":
            modified_files = []
            i += 1
            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                modified_files.append(sys.argv[i])
                i += 1
        else:
            if types is None:
                types = []
            types.append(sys.argv[i])
            i += 1

    result = check_project(
        root,
        types=types if types else None,
        since_commit=since_commit,
        modified_files=modified_files,
    )
    print(json_dumps(result, indent=2, ensure_ascii=False))
