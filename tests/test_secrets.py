"""Tests for src/secrets.py — credentials/secret scanning."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestBuiltinPatterns(unittest.TestCase):
    """Tests for built-in secret detection patterns."""

    def setUp(self):
        from src.secrets import _BUILTIN_PATTERNS, _scan_content_with_patterns

        self.patterns = _BUILTIN_PATTERNS
        self.scan_content = _scan_content_with_patterns

    def test_detects_aws_access_key(self) -> None:
        """Should detect AKIA... patterns."""
        # Construct test key to avoid push-protection false positive
        prefix = "AKIA"
        key = prefix + "0" * 16
        findings = self.scan_content(key, "/test/.env")
        self.assertGreaterEqual(len(findings), 1)

    def test_detects_github_pat(self) -> None:
        """Should detect GitHub PAT patterns."""
        # Construct test token to avoid GitHub push-protection false positive
        prefix = "ghp"
        token = prefix + "_" + "0" * 36
        findings = self.scan_content(token, "/test/config.py")
        self.assertGreaterEqual(len(findings), 1)

    def test_detects_private_key_header(self) -> None:
        """Should detect BEGIN PRIVATE KEY patterns."""
        content = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ...\n-----END PRIVATE KEY-----"
        findings = self.scan_content(content, "/test/key.pem")
        self.assertGreaterEqual(len(findings), 1)

    def test_detects_stripe_live_key(self) -> None:
        """Should detect sk_live_ patterns."""
        # Construct test key to avoid GitHub push-protection false positive
        prefix = "sk"
        key = prefix + "_live_" + "0" * 24
        findings = self.scan_content(
            f"STRIPE_KEY={key}", "/test/.env"
        )
        self.assertGreaterEqual(len(findings), 1)

    def test_no_false_positive_on_placeholder(self) -> None:
        """Should not flag short placeholder values (under 16 chars)."""
        findings = self.scan_content(
            'API_KEY = "placeholder"', "/test/config.py"
        )
        self.assertEqual(len(findings), 0)

    def test_no_false_positive_on_comment(self) -> None:
        """Should not flag explanatory comments."""
        findings = self.scan_content(
            "# GitHub PAT format: ghp_xxxxxxxxx", "/test/docs.md"
        )
        self.assertEqual(len(findings), 0)


class TestSecretsScanDiff(unittest.TestCase):
    """Tests for scan_diff_secrets — git diff based scanning."""

    def setUp(self):
        from src.secrets import scan_diff_secrets

        self.scan_diff_secrets = scan_diff_secrets

    @patch("src.secrets._check_gitleaks")
    @patch("src.secrets._check_detect_secrets")
    @patch("src.secrets._get_git_diff_hunks")
    def test_tries_gitleaks_first(
        self, mock_diff: MagicMock, mock_ds: MagicMock, mock_gl: MagicMock
    ) -> None:
        """Should try gitleaks first, return its results if available."""
        mock_gl.return_value = {
            "tool": "gitleaks",
            "findings": [{"rule": "aws-access-key", "file": "test.py", "line": 5}],
        }
        mock_diff.return_value = [{"file": "test.py", "content": "AKIA..."}]
        mock_ds.return_value = {"tool": "detect-secrets", "findings": []}

        result = self.scan_diff_secrets("/fake/project")
        self.assertEqual(result["tool"], "gitleaks")
        self.assertEqual(len(result["findings"]), 1)

    @patch("src.secrets._check_gitleaks")
    @patch("src.secrets._check_detect_secrets")
    @patch("src.secrets._get_git_diff_hunks")
    def test_falls_back_to_detect_secrets(
        self, mock_diff: MagicMock, mock_ds: MagicMock, mock_gl: MagicMock
    ) -> None:
        """Should fall back to detect-secrets if gitleaks unavailable."""
        mock_gl.return_value = None  # gitleaks not available
        mock_ds.return_value = {
            "tool": "detect-secrets",
            "findings": [{"rule": "aws-access-key", "file": "test.py", "line": 5}],
        }
        mock_diff.return_value = [{"file": "test.py", "content": "AKIA..."}]

        result = self.scan_diff_secrets("/fake/project")
        self.assertEqual(result["tool"], "detect-secrets")

    @patch("src.secrets._check_gitleaks")
    @patch("src.secrets._check_detect_secrets")
    @patch("src.secrets._get_git_diff_hunks")
    def test_falls_back_to_builtin(
        self, mock_diff: MagicMock, mock_ds: MagicMock, mock_gl: MagicMock
    ) -> None:
        """Should use built-in patterns when no external tools available."""
        mock_gl.return_value = None
        mock_ds.return_value = None
        mock_diff.return_value = [
            {"file": "test.py", "content": "AKIA" + "0" * 16}
        ]

        result = self.scan_diff_secrets("/fake/project")
        self.assertEqual(result["tool"], "builtin")
        self.assertGreaterEqual(len(result["findings"]), 1)

    @patch("src.secrets._check_gitleaks")
    @patch("src.secrets._check_detect_secrets")
    @patch("src.secrets._get_git_diff_hunks")
    def test_clean_diff_returns_empty(
        self, mock_diff: MagicMock, mock_ds: MagicMock, mock_gl: MagicMock
    ) -> None:
        """Should return empty findings for clean diff."""
        mock_gl.return_value = None
        mock_ds.return_value = None
        mock_diff.return_value = [{"file": "test.py", "content": "print('hello')"}]

        result = self.scan_diff_secrets("/fake/project")
        self.assertEqual(len(result["findings"]), 0)

    def test_empty_diff_returns_empty(self) -> None:
        """Should handle empty diff gracefully."""
        with patch("src.secrets._get_git_diff_hunks", return_value=[]):
            with patch("src.secrets._check_gitleaks", return_value=None):
                with patch("src.secrets._check_detect_secrets", return_value=None):
                    result = self.scan_diff_secrets("/fake/project")
        self.assertEqual(len(result["findings"]), 0)
        self.assertEqual(result["tool"], "builtin")


class TestGitDiffHunks(unittest.TestCase):
    """Tests for _get_git_diff_hunks — git diff parsing."""

    def setUp(self):
        from src.secrets import _get_git_diff_hunks

        self.get_hunks = _get_git_diff_hunks

    @patch("src.secrets.GitBackend")
    def test_returns_added_lines_from_diff(self, mock_git_class: MagicMock) -> None:
        """Should extract only added lines from git diff."""
        mock_git = MagicMock()
        mock_git.show_toplevel.return_value = "/fake/project"
        mock_git.diff_unified.return_value = (
            "diff --git a/test.py b/test.py\n"
            "--- a/test.py\n"
            "+++ b/test.py\n"
            "@@ -1,3 +1,4 @@\n"
            " unchanged line\n"
            "+added line with " + "AKIA" + "0" * 16 + "\n"
            " unchanged line 2\n"
        )
        mock_git_class.return_value = mock_git

        hunks = self.get_hunks("/fake/project")
        self.assertGreaterEqual(len(hunks), 1)
        self.assertIn("AKIA0000000000000000", hunks[0]["content"])


class TestVerifySecretsSection(unittest.TestCase):
    """Integration test: secrets section in verify JSON payload."""

    def test_verify_json_includes_secrets_section(self) -> None:
        """verify --json output should include a 'secrets' section."""
        from src.cli.commands.verify import run_verify

        def fake_git_toplevel(self):
            return project_root

        def fake_git_status(self):
            return [" M main.py"]

        def fake_check(self, **kwargs):
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "project_root": "/tmp/repo",
                "status": "passed",
                "types": ["python"],
                "runs": [],
                "summary": {
                    "total_errors": 0,
                    "total_warnings": 0,
                    "files_with_errors": 0,
                    "tools_run": 0,
                    "tools_skipped": 0,
                    "tool_failures": 0,
                },
                "errors_by_file": {},
            }

        def fake_lsp(project_root, changed_files, timeout, max_files):
            return {"enabled": True, "status": "skipped", "runs": [], "summary": {}}

        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "main.py", "def target():\n    return 1\n")
            import io, json

            stdout = io.StringIO()
            with (
                patch(
                    "src.git_backend.GitBackend.show_toplevel",
                    fake_git_toplevel,
                ),
                patch(
                    "src.git_backend.GitBackend.status_porcelain",
                    fake_git_status,
                ),
                patch(
                    "src.cli.commands.verify._run_check_payload",
                    fake_check,
                ),
                patch(
                    "src.cli.commands.verify._verify_lsp_payload",
                    fake_lsp,
                ),
                patch("sys.stdout", stdout),
            ):
                rc = run_verify(
                    project=project_root,
                    as_json=True,
                    quick=True,
                )

            stdout.seek(0)
            payload = json.loads(stdout.getvalue())
            result = payload.get("result", payload)
            self.assertIn("secrets", result)


if __name__ == "__main__":
    unittest.main()
