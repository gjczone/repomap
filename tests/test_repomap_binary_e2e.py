import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def write_file(root: str, relative_path: str, content: str) -> None:
    path = Path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class RepoMapBinaryE2ETests(unittest.TestCase):
    repo_root: Path
    temp_dir: tempfile.TemporaryDirectory[str]
    output_dir: Path
    binary_path: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.output_dir = Path(cls.temp_dir.name)

        build = subprocess.run(
            [sys.executable, "-m", "src.cli", "build-binary", "--output", str(cls.output_dir)],
            cwd=cls.repo_root,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            text=True,
            check=False,
        )
        if build.returncode != 0:
            raise AssertionError(f"binary build failed:\nSTDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}")

        binary_name = "repomap.exe" if os.name == "nt" else "repomap"
        cls.binary_path = cls.output_dir / binary_name
        if not cls.binary_path.exists():
            raise AssertionError(f"expected binary missing: {cls.binary_path}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_binary_doctor_runs(self) -> None:
        result = subprocess.run(
            [str(self.binary_path), "doctor"],
            cwd=self.repo_root,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("tree-sitter parsers", result.stdout)

    def test_binary_query_symbol_runs_on_real_project(self) -> None:
        with tempfile.TemporaryDirectory() as project_root:
            write_file(project_root, "lib.py", "def helper():\n    return 1\n")
            write_file(
                project_root,
                "main.py",
                "from lib import helper\n\ndef caller():\n    return helper()\n",
            )

            result = subprocess.run(
                [str(self.binary_path), "query-symbol", "--project", project_root, "--symbol", "helper"],
                cwd=self.repo_root,
                capture_output=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("helper", result.stdout)
            self.assertIn("lib.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
