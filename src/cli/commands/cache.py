from __future__ import annotations

import sys

from ..handlers import (
    CLI_NAME,
    _resolve_project,
)
from ... import CACHE_DIR
from ...toolkit import prune_cache, save_cache, scan_project


def run_cache(
    project: str,
    action: str,
    as_json: bool = False,
    ttl_days: int = 7,
) -> int:
    project_path = _resolve_project(project)
    if action == "prune":
        return _run_cache_prune(ttl_days, as_json, project_path)
    if action != "save":
        print(f"[{CLI_NAME}] unsupported cache action: {action}", file=sys.stderr)
        return 2
    # Issue #183: save 前自动清理陈旧 session cache，避免磁盘堆积
    try:
        prune_cache(CACHE_DIR, ttl_days=ttl_days)
    except Exception as exc:
        print(
            f"[{CLI_NAME}] cache auto-prune skipped: {exc}",
            file=sys.stderr,
        )
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


def _run_cache_prune(ttl_days: int, as_json: bool, project_path: str) -> int:
    try:
        removed, kept = prune_cache(CACHE_DIR, ttl_days=ttl_days)
    except Exception as exc:
        print(f"[{CLI_NAME}] cache prune failed: {exc}", file=sys.stderr)
        return 1
    if as_json:
        from ..handlers import json_envelope

        print(
            json_envelope(
                "cache",
                project_path,
                {
                    "action": "prune",
                    "ttl_days": ttl_days,
                    "removed": [str(p) for p in removed],
                    "kept": [str(p) for p in kept],
                    "removed_count": len(removed),
                    "kept_count": len(kept),
                },
            )
        )
        return 0
    print(
        f"Cache prune (ttl={ttl_days} days):\n"
        f"- Removed: {len(removed)}\n"
        f"- Kept: {len(kept)}"
    )
    for r in removed[:20]:
        print(f"  - {r}")
    if len(removed) > 20:
        print(f"  … and {len(removed) - 20} more")
    return 0
