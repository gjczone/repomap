"""Multi-language formatter dispatch for repomap fix command.

Provides language-aware formatter detection, config file discovery,
and subprocess execution. Used by fix.py to replace the hardcoded
ruff + eslint with a config-gated nearest-wins dispatcher.

Design:
- FORMATTER_MAP defines available formatters per file extension
- detect_formatter() picks the right formatter for a file
- find_nearest_config() walks up from file to project root
- run_formatter() executes the formatter subprocess
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("repomap")


# ── Data types ───────────────────────────────────────────────────────────────


@dataclass
class FormatterResult:
    """Result of running a formatter command."""

    success: bool = False
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False
    tool: str = ""
    args: list[str] = field(default_factory=list)


# ── Formatter map — per-language configuration ───────────────────────────────

FORMATTER_MAP: list[dict[str, Any]] = [
    {
        "extensions": [".py"],
        "primary": {
            "tool": "ruff",
            "args_format": ["ruff", "format", "{file}"],
            "check_args_format": ["ruff", "format", "--check", "{file}"],
        },
        "fallback": {
            "tool": "black",
            "args_format": ["black", "{file}"],
            "check_args_format": ["black", "--check", "{file}"],
            "config_files": ["pyproject.toml"],  # must have [tool.black] section
        },
    },
    {
        "extensions": [".js", ".ts", ".jsx", ".tsx"],
        "primary": {
            "tool": "biome",
            "args_format": ["biome", "check", "--apply", "{file}"],
            "check_args_format": ["biome", "check", "{file}"],
            "config_files": ["biome.json", "biome.jsonc"],
        },
        "fallback": {
            "tool": "prettier",
            "args_format": ["prettier", "--write", "{file}"],
            "check_args_format": ["prettier", "--check", "{file}"],
            "config_files": [
                ".prettierrc",
                ".prettierrc.json",
                ".prettierrc.yaml",
                ".prettierrc.yml",
                ".prettierrc.js",
                ".prettierrc.mjs",
                ".prettierrc.cjs",
                "prettier.config.js",
                "prettier.config.mjs",
                "prettier.config.cjs",
            ],
        },
        "second_fallback": {
            "tool": "eslint",
            "args_format": ["eslint", "--fix", "--", "{file}"],
            "check_args_format": ["eslint", "--", "{file}"],
        },
    },
    {
        "extensions": [".go"],
        "primary": {
            "tool": "gofmt",
            "args_format": ["gofmt", "-w", "{file}"],
            "check_args_format": ["gofmt", "-d", "{file}"],
        },
    },
    {
        "extensions": [".rs"],
        "primary": {
            "tool": "cargo",
            "args_format": ["cargo", "fmt", "--", "{file}"],
            "check_args_format": ["cargo", "fmt", "--check", "--", "{file}"],
            "config_files": ["Cargo.toml"],
        },
        "fallback": None,
    },
]


def _ext_to_entry(ext: str) -> dict[str, Any] | None:
    """Find the FORMATTER_MAP entry for a file extension."""
    ext_lower = ext.lower()
    for entry in FORMATTER_MAP:
        if ext_lower in entry.get("extensions", []):
            return entry
    return None


def find_nearest_config(
    file_path: str, project_root: str, config_names: list[str]
) -> str | None:
    """Walk up from file_path to project_root, return first matching config file.

    Args:
        file_path: Absolute path to the file being formatted.
        project_root: Absolute project root — stop searching at this directory.
        config_names: List of config file names to look for (e.g., ['biome.json']).

    Returns:
        Absolute path to the first config found, or None.
    """
    current = os.path.dirname(os.path.abspath(file_path))
    project_root_abs = os.path.abspath(project_root)

    while current.startswith(project_root_abs):
        for name in config_names:
            candidate = os.path.join(current, name)
            if os.path.isfile(candidate):
                return candidate
        # Stop when we reach the project root
        if current == project_root_abs:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _check_black_config(config_path: str) -> bool:
    """Check if pyproject.toml has [tool.black] section (for black fallback)."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        return "[tool.black]" in content
    except OSError:
        return False


def detect_formatter(file_path: str, project_root: str) -> list[str] | None:
    """Detect the appropriate formatter command for a file.

    Uses nearest-wins config detection. For each formatter in the chain
    (primary → fallback → second_fallback), checks if the required config
    files exist. Returns the first usable formatter's args, or None if
    no formatter is available.

    Args:
        file_path: Absolute path to the file.
        project_root: Absolute project root.

    Returns:
        List of command arguments (tool + flags), or None if no formatter applies.
    """
    ext = Path(file_path).suffix
    entry = _ext_to_entry(ext)
    if entry is None:
        return None

    candidates: list[dict[str, Any]] = []
    if "primary" in entry and entry["primary"] is not None:
        candidates.append(entry["primary"])
    if "fallback" in entry and entry["fallback"] is not None:
        candidates.append(entry["fallback"])
    if "second_fallback" in entry and entry.get("second_fallback") is not None:
        candidates.append(entry["second_fallback"])

    for candidate in candidates:
        tool = candidate.get("tool", "")
        config_files = candidate.get("config_files", [])
        args_format = candidate.get("args_format", [])

        if config_files:
            # Check if config exists
            config_path = find_nearest_config(file_path, project_root, config_files)
            if config_path is None:
                continue
            # Special case: pyproject.toml for black requires [tool.black]
            if tool == "black" and config_path.endswith("pyproject.toml"):
                if not _check_black_config(config_path):
                    continue

        # Format args with the file path
        return [arg.replace("{file}", file_path) for arg in args_format]

    return None


def run_formatter(args: list[str], dry_run: bool = False) -> FormatterResult:
    """Run a formatter command, returning structured result.

    Args:
        args: Formatter command and arguments (e.g., ['ruff', 'format', 'file.py']).
        dry_run: If True, only report what would be done without executing.

    Returns:
        FormatterResult with success, exit_code, stdout, stderr, dry_run.
    """
    tool = args[0] if args else "unknown"
    result = FormatterResult(tool=tool, args=list(args), dry_run=dry_run)

    if dry_run:
        result.success = True
        result.exit_code = 0
        return result

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
        )
        result.exit_code = proc.returncode
        result.stdout = proc.stdout
        result.stderr = proc.stderr
        result.success = proc.returncode == 0
    except FileNotFoundError:
        result.success = False
        result.exit_code = -1
        result.stderr = f"tool '{tool}' not found"
    except subprocess.TimeoutExpired:
        result.success = False
        result.exit_code = -1
        result.stderr = f"tool '{tool}' timed out after 120s"
    except Exception as exc:
        result.success = False
        result.exit_code = -1
        result.stderr = f"unexpected error: {exc}"

    return result


def detect_all_formatters(
    project_root: str,
    dry_run: bool = False,
    max_files: int = 500,
) -> list[dict[str, Any]]:
    """Scan project for files and detect applicable formatters.

    Returns a list of {file, formatter, args, dry_run_plan} dicts.
    Used by fix command to batch-format all files in a project.

    Args:
        project_root: Absolute project root.
        dry_run: If True, report plans without executing.
        max_files: Maximum files to process (default 500).
    """
    results: list[dict[str, Any]] = []
    skip_dirs = {
        ".git",
        "__pycache__",
        "node_modules",
        "venv",
        ".venv",
        "target",
        "dist",
        "build",
        "coverage",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
    valid_exts: set[str] = set()
    for entry in FORMATTER_MAP:
        valid_exts.update(entry.get("extensions", []))

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            if len(results) >= max_files:
                return results
            ext = Path(fname).suffix.lower()
            if ext not in valid_exts:
                continue
            file_path = os.path.join(dirpath, fname)
            formatter_args = detect_formatter(file_path, project_root)
            if formatter_args is None:
                continue
            results.append(
                {
                    "file": file_path,
                    "tool": formatter_args[0],
                    "args": formatter_args,
                    "dry_run_plan": f"would run: {' '.join(formatter_args)}",
                }
            )
    return results
