from __future__ import annotations

import sys

from ..handlers import (
    CLI_NAME,
    _resolve_project,
)
from ...toolkit import save_cache, scan_project


def run_cache(project: str, action: str, as_json: bool = False) -> int:
    project_path = _resolve_project(project)
    if action != "save":
        print(f"[{CLI_NAME}] unsupported cache action: {action}", file=sys.stderr)
        return 2
    try:
        symbols, edges = scan_project(project_path)
        cache_path = save_cache(project_path, symbols, edges)
        if as_json:
            from ..handlers import json_envelope

            print(
                json_envelope(
                    "cache",
                    project_path,
                    {
                        "action": action,
                        "cache_path": str(cache_path),
                        "symbols": len(symbols),
                        "edges": len(edges),
                    },
                )
            )
            return 0
        print(
            "✅ Graph baseline saved for a future comparison\n"
            f"- Path: `{cache_path}`\n"
            f"- Symbols: {len(symbols)}\n"
            f"- Edges: {len(edges)}\n"
            "- Use before the target edits; saving after edits cannot prove those edits are safe."
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] cache save failed: {exc}", file=sys.stderr)
        return 1
