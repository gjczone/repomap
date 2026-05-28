from __future__ import annotations

import importlib.util as importlib_util
import re
import subprocess
import sys
from pathlib import Path

from ..handlers import (
    CLI_NAME,
    PACKAGE_ROOT,
    PROJECT_ROOT,
    PYINSTALLER_BINDINGS,
)


def _read_version_from_pyproject() -> str:
    """从 pyproject.toml 读取版本号。"""
    pyproject = PROJECT_ROOT.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0-dev"


def _write_version_file(version: str) -> Path:
    """在 src/_version.py 写入版本号，供 PyInstaller 二进制读取。"""
    version_file = PROJECT_ROOT / "_version.py"
    version_file.write_text(
        f'VERSION = "{version}"\n',
        encoding="utf-8",
    )
    return version_file


def _pyinstaller_command(output_dir: Path, name: str) -> list[str]:
    build_root = output_dir / ".pyinstaller"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(output_dir),
        "--workpath",
        str(build_root / "build"),
        "--specpath",
        str(build_root / "spec"),
    ]
    for module_name in PYINSTALLER_BINDINGS:
        # 可选 parser 未安装时仍允许构建；已安装的动态模块显式加入 hidden-import，避免二进制漏包。
        if importlib_util.find_spec(module_name) is None:
            continue
        command.extend(["--hidden-import", module_name])
    command.append(str(PACKAGE_ROOT / "__main__.py"))
    return command


def run_build_binary(output: str, name: str) -> int:
    output_dir = Path(output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # 构建前写入版本号文件，让 PyInstaller 二进制也能显示正确版本
    version = _read_version_from_pyproject()
    version_file = _write_version_file(version)
    try:
        result = subprocess.run(
            _pyinstaller_command(output_dir, name), cwd=str(PROJECT_ROOT), check=False
        )
        if result.returncode != 0:
            print(
                f"[{CLI_NAME}] build failed with exit code {result.returncode}",
                file=sys.stderr,
            )
            return result.returncode or 1
        print(f"binary ready: {output_dir / name}")
        return 0
    finally:
        # 清理生成的版本号文件，避免污染源码目录
        version_file.unlink(missing_ok=True)
