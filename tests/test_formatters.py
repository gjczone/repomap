"""Tests for src/formatters.py — multi-language formatter dispatch."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add src/ to path for test execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestDetectFormatter(unittest.TestCase):
    """Tests for detect_formatter() — returns formatter args per file type."""

    def setUp(self):
        from src.formatters import detect_formatter

        self.detect_formatter = detect_formatter

    def test_python_file_returns_ruff(self) -> None:
        """Python files should default to ruff format."""
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "src/main.py", "print('hello')\n")
            result = self.detect_formatter(
                os.path.join(tmp, "src/main.py"), tmp
            )
        self.assertIsNotNone(result)
        self.assertIn("ruff", result[0])

    def test_js_file_without_config_returns_prettier(self) -> None:
        """JS files without biome.json should fallback to prettier."""
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "src/app.js", "console.log('ok')\n")
            result = self.detect_formatter(
                os.path.join(tmp, "src/app.js"), tmp
            )
        self.assertIsNotNone(result)
        # Fallback should be prettier (if installed) or eslint
        # Without any config, prettier is checked first as fallback
        self.assertIn(result[0], ["prettier", "eslint"])

    def test_go_file_returns_gofmt(self) -> None:
        """Go files should use gofmt -w."""
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "main.go", 'package main\nfunc main() {}\n')
            result = self.detect_formatter(
                os.path.join(tmp, "main.go"), tmp
            )
        self.assertIsNotNone(result)
        self.assertEqual("gofmt", result[0])

    def test_rust_file_with_cargo_toml_returns_cargo_fmt(self) -> None:
        """Rust files with Cargo.toml should use cargo fmt."""
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "Cargo.toml", "[package]\nname = \"test\"\n")
            write_file(tmp, "src/main.rs", "fn main() {}\n")
            result = self.detect_formatter(
                os.path.join(tmp, "src/main.rs"), tmp
            )
        self.assertIsNotNone(result)
        self.assertIn("cargo", result[0])

    def test_rust_file_without_cargo_toml_skips(self) -> None:
        """Rust files without Cargo.toml should return None (skip)."""
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "main.rs", "fn main() {}\n")
            result = self.detect_formatter(
                os.path.join(tmp, "main.rs"), tmp
            )
        self.assertIsNone(result)

    def test_unknown_extension_returns_none(self) -> None:
        """Unknown file extensions should return None."""
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "test.sh", "echo hello\n")
            result = self.detect_formatter(
                os.path.join(tmp, "test.sh"), tmp
            )
        self.assertIsNone(result)

    def test_tsx_file_returns_biome_or_fallback(self) -> None:
        """TSX files should work with biome or fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "src/App.tsx", "export const App = () => <div/>;\n")
            result = self.detect_formatter(
                os.path.join(tmp, "src/App.tsx"), tmp
            )
        self.assertIsNotNone(result)
        self.assertIn(result[0], ["biome", "prettier", "eslint"])


class TestFindNearestConfig(unittest.TestCase):
    """Tests for find_nearest_config() — config file discovery."""

    def setUp(self):
        from src.formatters import find_nearest_config

        self.find_nearest_config = find_nearest_config

    def test_finds_config_in_same_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "biome.json", "{}")
            write_file(tmp, "src/app.ts", "const x = 1;\n")
            result = self.find_nearest_config(
                os.path.join(tmp, "src/app.ts"), tmp, ["biome.json"]
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("biome.json"))

    def test_finds_config_in_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "biome.json", "{}")
            write_file(tmp, "deep/nested/src/app.ts", "const x = 1;\n")
            result = self.find_nearest_config(
                os.path.join(tmp, "deep/nested/src/app.ts"),
                tmp,
                ["biome.json"],
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("biome.json"))

    def test_returns_none_when_no_config_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "src/app.ts", "const x = 1;\n")
            result = self.find_nearest_config(
                os.path.join(tmp, "src/app.ts"), tmp, ["biome.json"]
            )
        self.assertIsNone(result)

    def test_does_not_search_beyond_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "sub")
            os.makedirs(sub)
            write_file(tmp, "biome.json", "{}")  # at project root
            write_file(sub, "app.ts", "const x = 1;\n")
            # Search from sub/ with project_root=sub should NOT find biome.json at parent
            result = self.find_nearest_config(
                os.path.join(sub, "app.ts"), sub, ["biome.json"]
            )
        self.assertIsNone(result)

    def test_finds_first_matching_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, ".prettierrc", "{}")
            write_file(tmp, "biome.json", "{}")
            write_file(tmp, "src/app.ts", "const x = 1;\n")
            # Should find prettier first since it's checked before biome
            result = self.find_nearest_config(
                os.path.join(tmp, "src/app.ts"),
                tmp,
                [".prettierrc", "biome.json"],
            )
        self.assertIsNotNone(result)

    def test_multiple_config_names_finds_nearest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "biome.json", "{}")
            write_file(tmp, "src/app.ts", "const x = 1;\n")
            result = self.find_nearest_config(
                os.path.join(tmp, "src/app.ts"),
                tmp,
                [".prettierrc", ".prettierrc.json", "biome.json"],
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("biome.json"))


class TestFormatterMapping(unittest.TestCase):
    """Tests for the FORMATTER_MAP data structure."""

    def test_formatter_map_covers_python_js_ts_go_rust(self) -> None:
        from src.formatters import FORMATTER_MAP

        covered_exts = set()
        for entry in FORMATTER_MAP:
            covered_exts.update(entry.get("extensions", []))
        for ext in [".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"]:
            self.assertIn(ext, covered_exts, f"Missing extension: {ext}")


class TestRunFormatter(unittest.TestCase):
    """Tests for run_formatter() — subprocess execution."""

    def setUp(self):
        from src.formatters import run_formatter, FormatterResult

        self.run_formatter = run_formatter
        self.FormatterResult = FormatterResult

    @patch("subprocess.run")
    def test_successful_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = self.run_formatter(["echo", "test"], dry_run=False)
        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)

    @patch("subprocess.run")
    def test_failed_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="error", stderr="error detail"
        )
        result = self.run_formatter(["nonexistent_tool"], dry_run=False)
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)

    @patch("subprocess.run")
    def test_dry_run_does_not_execute(self, mock_run: MagicMock) -> None:
        result = self.run_formatter(["rm", "-rf", "/"], dry_run=True)
        mock_run.assert_not_called()
        self.assertTrue(result.success)
        self.assertTrue(result.dry_run)

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_tool_not_found(self, mock_run: MagicMock) -> None:
        result = self.run_formatter(["nonexistent_tool"], dry_run=False)
        self.assertFalse(result.success)
        self.assertIn("not found", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
