from __future__ import annotations

import importlib.util as importlib_util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..handlers import (
    CLI_NAME,
    PACKAGE_ROOT,
    PROJECT_ROOT,
    PYINSTALLER_BINDINGS,
    EXIT_SUCCESS,
    EXIT_ERROR,
)


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
