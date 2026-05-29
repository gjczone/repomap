from __future__ import annotations

import importlib.util as importlib_util
import sys
from pathlib import Path

from ..handlers import (
    CLI_NAME,
    _resolve_project,
)

from ... import get_repomap_version


def run_lsp_doctor(project: str, as_json: bool = False) -> int:
    try:
        project_root = _resolve_project(project)
        from ...lsp import detect_lsp_servers, detection_to_dict

        detections = detect_lsp_servers(project_root)
        payload = {
            "lspClient": "available",
            "bundledServers": [],
            "servers": [detection_to_dict(item) for item in detections],
        }
        if as_json:
            from ..handlers import json_envelope

            print(json_envelope("lsp doctor", project_root, payload))
            return 0
        lines = ["## LSP Doctor\n"]
        lines.append(f"Project: `{project_root}`")
        lines.append("LSP client: available")
        lines.append("Bundled LSP servers: none")
        if not detections:
            lines.append("\nNo supported source files detected.")
        else:
            lines.append("\n| Language | Server | Status | Source | Workspace |")
            lines.append("|---|---|---|---|---|")
            for item in detections:
                status = (
                    "available"
                    if item.status == "available"
                    else f"missing ({item.reason or 'not found'})"
                )
                lines.append(
                    f"| {item.language} | {item.server_name or '-'} | {status} | {item.source or '-'} | `{item.workspace_root or project_root}` |"
                )
        lines.append(
            "\n> repomap checks project-local executables, PATH, and trusted user tool bins such as npm/pnpm/yarn/bun/pipx/uv/mason/cargo/go directories; it does not install or bundle servers."
        )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] lsp doctor failed: {exc}", file=sys.stderr)
        return 1


def run_lsp_setup(project: str, languages: list[str] | None, dry_run: bool) -> int:
    try:
        project_root = _resolve_project(project)
        from ...lsp import detect_lsp_server, detect_lsp_servers, LSP_INSTALL_STRATEGIES

        if languages:
            detections = [detect_lsp_server(project_root, lang) for lang in languages]
        else:
            detections = detect_lsp_servers(project_root)

        missing = [d for d in detections if d.status != "available"]
        available = [d for d in detections if d.status == "available"]

        print(f"Project: {project_root}")
        print(f"Detected languages: {len(detections)}")
        print()

        if available:
            print("Already available:")
            for d in available:
                print(f"  {d.language}: {d.server_name} ({d.source})")

        if not missing:
            print("\nAll LSP servers are already available.")
            return 0

        print(
            f"\n{'Would install' if dry_run else 'Installing'} {len(missing)} server(s):"
        )
        print()
        for d in missing:
            strategy = LSP_INSTALL_STRATEGIES.get(d.language, {})
            tool = strategy.get("tool", "unknown")
            cmd = strategy.get("cmd", "manual install")
            print(f"  [{d.language}] {d.server_name}")
            print(f"    Tool: {tool}")
            print(f"    Command: {cmd}")
            print()

        if dry_run:
            print("Dry run — no changes made. Remove --dry-run to execute.")
            return 0

        print("Installation not yet automated. Run the commands above manually.")
        print("Tip: repomap cannot auto-install LSP servers without your consent.")
        print("      Use the commands listed above, then re-run `repomap lsp doctor`.")
        print("      (returning 0 — this is expected behavior, not an error)")
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] lsp setup failed: {exc}", file=sys.stderr)
        return 1


def _module_origin(module_name: str) -> str:
    spec = importlib_util.find_spec(module_name)
    if spec is None:
        return "not found"
    return spec.origin or "built-in"


def run_doctor(project: str, show_lsp: bool = False, as_json: bool = False) -> int:
    from ...parser import TreeSitterAdapter

    if project:
        project_root = _resolve_project(project)
    else:
        project_root = str(Path.cwd())

    adapter = TreeSitterAdapter()
    parsers = sorted(adapter.parsers)
    pyinstaller_spec = importlib_util.find_spec("PyInstaller")

    # 收集诊断信息
    result: dict = {
        "parsers": parsers,
        "tsx_available": "tsx" in adapter.parsers,
        "tree_sitter_origin": _module_origin("tree_sitter"),
        "lsp_client": "available",
        "pyinstaller": pyinstaller_spec is not None,
    }

    if not parsers:
        if as_json:
            from ..handlers import json_envelope

            result["error"] = "tree-sitter bindings are missing"
            print(json_envelope("doctor", project_root, result, status="error"))
            return 1
        print("tree-sitter bindings are missing", file=sys.stderr)
        return 1
    if "tsx" not in adapter.parsers:
        if as_json:
            from ..handlers import json_envelope

            result["error"] = "TSX parser unavailable"
            print(json_envelope("doctor", project_root, result, status="error"))
            return 1
        print("TSX parser: unavailable", file=sys.stderr)
        return 1

    repomap_cli_origin = _module_origin("repomap_cli")
    if repomap_cli_origin != "not found":
        result["repomap_cli_origin"] = repomap_cli_origin
    result["repomap_version"] = get_repomap_version()

    if show_lsp:
        from ...lsp import detect_lsp_servers
        from ...lsp import LSP_INSTALL_STRATEGIES

        detections = detect_lsp_servers(project_root)
        available = [d for d in detections if d.status == "available"]
        missing = [d for d in detections if d.status != "available"]
        result["lsp_servers"] = [
            {"language": d.language, "server": d.server_name, "source": d.source}
            for d in available
        ]
        result["lsp_missing"] = [
            {
                "language": d.language,
                "server": d.server_name,
                "install": LSP_INSTALL_STRATEGIES.get(d.language, {}).get(
                    "cmd", "manual"
                ),
            }
            for d in missing
        ]
    else:
        result["lsp_hint"] = "run `repomap doctor --lsp` to check"

    if as_json:
        from ..handlers import json_envelope

        print(json_envelope("doctor", project_root, result))
        return 0

    # 文本输出（原有逻辑）
    if parsers:
        print(f"tree-sitter parsers: {', '.join(parsers)}")
    if repomap_cli_origin != "not found":
        print(f"repomap_cli: {repomap_cli_origin} (dev only)")
    print(f"tree_sitter: {_module_origin('tree_sitter')}")
    print("LSP client: available")

    if show_lsp:
        available = result.get("lsp_servers", [])
        missing = result.get("lsp_missing", [])
        print(f"\nLSP servers (project: {project_root}):")
        for d in available:
            print(f"  {d['language']}: {d['server']} ({d['source']})")
        if missing:
            print(f"\nMissing ({len(missing)}):")
            for d in missing:
                print(f"  {d['language']}: {d['server']} — install: {d['install']}")
        else:
            print("\nAll LSP servers available.")
        print("\nTip: run `repomap lsp setup --dry-run` to preview auto-install.")
    else:
        print("LSP servers: run `repomap doctor --lsp` to check")
    if pyinstaller_spec is not None:
        print("PyInstaller: available")
    else:
        print(
            "PyInstaller: not installed in current runtime, only required for build-binary"
        )
    return 0
