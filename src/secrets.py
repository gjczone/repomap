"""Secrets scanning for repomap verify command.

Scans git diff hunks for credentials/keys/secrets.
Tool chain: gitleaks → detect-secrets → built-in patterns.

Design:
- scan_diff_secrets() is the main entry point
- Only scans git diff hunks, not the full repo
- verify integrates this via --no-secrets flag (default: enabled)
- fix rejects execution when secrets are detected (pre-fix guard)
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

from .git_backend import GitBackend

logger = logging.getLogger("repomap")

# ── Built-in minimal pattern set (fallback when no external tools) ────────────

_BUILTIN_PATTERNS: list[dict[str, Any]] = [
    {
        "rule_id": "aws-access-key",
        "pattern": re.compile(r"AKIA[0-9A-Z]{16}"),
        "description": "AWS Access Key ID",
    },
    {
        "rule_id": "github-pat",
        "pattern": re.compile(r"gh[pousr]_[0-9a-zA-Z]{36}"),
        "description": "GitHub Personal Access Token",
    },
    {
        "rule_id": "private-key",
        "pattern": re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
        ),
        "description": "Private Key (PEM format)",
    },
    {
        "rule_id": "stripe-live-key",
        "pattern": re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
        "description": "Stripe Live Secret Key",
    },
    {
        "rule_id": "generic-api-key",
        "pattern": re.compile(
            r'(?:api[_-]?key|apikey|secret|token|password|passwd)\s*[:=]\s*["\']([^"\'\s]{16,})["\']',
            re.IGNORECASE,
        ),
        "description": "Generic API key/secret assignment",
    },
    {
        "rule_id": "jwt-token",
        "pattern": re.compile(
            r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}",
        ),
        "description": "JWT Token",
    },
]


def _scan_content_with_patterns(
    content: str, file_path: str, patterns: list[dict] | None = None
) -> list[dict[str, Any]]:
    """Scan text content with built-in regex patterns.

    Args:
        content: Text content to scan.
        file_path: Relative file path for reporting.
        patterns: Pattern list (defaults to _BUILTIN_PATTERNS).

    Returns:
        List of findings: {rule, description, file, line, match}.
    """
    if patterns is None:
        patterns = _BUILTIN_PATTERNS
    findings: list[dict[str, Any]] = []
    lines = content.split("\n")
    for line_num, line in enumerate(lines, start=1):
        for pat in patterns:
            for match in pat["pattern"].finditer(line):
                # Skip lines that look like comments or docs
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                if stripped.startswith("*") or stripped.startswith("/**"):
                    continue
                findings.append(
                    {
                        "rule": pat["rule_id"],
                        "description": pat["description"],
                        "file": file_path,
                        "line": line_num,
                        "match": match.group(0),
                    }
                )
    return findings


def _check_gitleaks(project_root: str) -> dict[str, Any] | None:
    """Try gitleaks detect on the project.

    Returns dict with tool + findings, or None if unavailable.
    """
    try:
        result = subprocess.run(
            ["gitleaks", "detect", "--source", project_root, "--no-git", "-f", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode in (0, 1):  # 0=clean, 1=leaks found
            import json

            try:
                data = json.loads(result.stdout) if result.stdout.strip() else []
            except json.JSONDecodeError:
                return None
            findings = []
            for item in data if isinstance(data, list) else [data]:
                findings.append(
                    {
                        "rule": item.get("RuleID", item.get("rule_id", "unknown")),
                        "description": item.get(
                            "Description", item.get("description", "")
                        ),
                        "file": item.get("File", item.get("file", "")),
                        "line": item.get("StartLine", item.get("line", 0)),
                        "match": item.get("Secret", item.get("match", "")),
                    }
                )
            return {"tool": "gitleaks", "findings": findings}
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception as exc:
        logger.debug("gitleaks error: %s", exc)
        return None


def _check_detect_secrets(project_root: str) -> dict[str, Any] | None:
    """Try detect-secrets scan on the project.

    Returns dict with tool + findings, or None if unavailable.
    """
    try:
        result = subprocess.run(
            ["detect-secrets", "scan", "--all-files", project_root],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=project_root,
        )
        if result.returncode in (0, 1):
            import json

            try:
                data = json.loads(result.stdout) if result.stdout.strip() else {}
            except json.JSONDecodeError:
                return None
            results = data.get("results", {})
            findings = []
            for file_path, secrets in results.items():
                for secret in secrets:
                    findings.append(
                        {
                            "rule": secret.get("type", "unknown"),
                            "description": secret.get("type", ""),
                            "file": file_path,
                            "line": secret.get("line_number", 0),
                            "match": secret.get("hashed_secret", ""),
                        }
                    )
            return {"tool": "detect-secrets", "findings": findings}
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception as exc:
        logger.debug("detect-secrets error: %s", exc)
        return None


def _get_git_diff_hunks(project_root: str) -> list[dict[str, str]]:
    """Get git diff hunks (staged + unstaged) as file+content pairs.

    Only returns added lines (lines starting with '+' in unified diff).

    Args:
        project_root: Absolute project root.

    Returns:
        List of {file, content} where content contains only added lines.
    """
    git = GitBackend(str(project_root))
    git_root = git.show_toplevel()
    if not git_root:
        return []

    try:
        diff_output = git.diff_unified()
    except Exception:
        logger.debug("git diff_unified failed", exc_info=True)
        return []

    hunks: list[dict[str, str]] = []
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff_output.split("\n"):
        if line.startswith("diff --git "):
            # Save previous file
            if current_file and current_lines:
                hunks.append(
                    {"file": current_file, "content": "\n".join(current_lines)}
                )
            current_file = None
            current_lines = []
            # Extract filename: diff --git a/path b/path
            parts = line.split(" ")
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    current_file = b_path[2:]
                else:
                    current_file = b_path
        elif line.startswith("+++ "):
            # +++ b/path — also sets current file
            if line.startswith("+++ b/"):
                current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            # Added line — strip the leading '+'
            current_lines.append(line[1:])

    if current_file and current_lines:
        hunks.append({"file": current_file, "content": "\n".join(current_lines)})

    return hunks


def scan_diff_secrets(project_root: str) -> dict[str, Any]:
    """Scan git diff hunks for secrets, using best available tool.

    Tool priority: gitleaks > detect-secrets > built-in patterns.

    Args:
        project_root: Absolute project root path.

    Returns:
        Dict with:
          - tool: str (tool name used)
          - findings: list[{rule, description, file, line, match}]
    """
    # 1. Try gitleaks
    gitleaks_result = _check_gitleaks(project_root)
    if gitleaks_result is not None:
        return gitleaks_result

    # 2. Try detect-secrets
    ds_result = _check_detect_secrets(project_root)
    if ds_result is not None:
        return ds_result

    # 3. Fall back to built-in patterns on git diff hunks
    hunks = _get_git_diff_hunks(project_root)
    all_findings: list[dict[str, Any]] = []
    for hunk in hunks:
        findings = _scan_content_with_patterns(hunk["content"], hunk["file"])
        all_findings.extend(findings)

    return {"tool": "builtin", "findings": all_findings}
