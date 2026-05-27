from __future__ import annotations

from datetime import datetime
import sys

from ... import json_dumps
from ..handlers import (
    CLI_NAME,
    _resolve_project,
)
from ...toolkit import diff_project, save_cache, scan_project


def run_cache(project: str, action: str) -> int:
    project_path = _resolve_project(project)
    if action != "save":
        print(f"[{CLI_NAME}] unsupported cache action: {action}", file=sys.stderr)
        return 2
    try:
        symbols, edges = scan_project(project_path)
        cache_path = save_cache(project_path, symbols, edges)
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


def run_diff(project: str, as_json: bool) -> int:
    result = diff_project(_resolve_project(project))
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    if as_json:
        print(json_dumps(result, ensure_ascii=False, indent=2))
        return 0
    lines = ["## Change Detection\n"]
    lines.append(
        f"**Compare**: {result.get('last_scan', 'unknown')} → {result.get('scan_time', datetime.now().isoformat())}\n"
    )
    summary = result.get("summary", {})
    lines.append(f"- Added symbols: {summary.get('added', 0)}")
    lines.append(f"- Removed symbols: {summary.get('removed', 0)}")
    lines.append(f"- Modified symbols: {summary.get('modified', 0)}")
    lines.append(f"- Added calls: {summary.get('edges_added', 0)}")
    lines.append(f"- Removed calls: {summary.get('edges_removed', 0)}\n")
    if result.get("added_symbols"):
        lines.append("**Added symbols** (Top 10):")
        for item in result["added_symbols"][:10]:
            lines.append(f"  - `{item['name']}` ({item['file']}:{item['line']})")
    if result.get("call_chain_changes", {}).get("new_calls"):
        lines.append("\n**Added calls** (Top 10):")
        for change in result["call_chain_changes"]["new_calls"][:10]:
            src_name = (
                change["from"].split("::")[-2]
                if "::" in change["from"]
                else change["from"]
            )
            tgt_name = (
                change["to"].split("::")[-2] if "::" in change["to"] else change["to"]
            )
            lines.append(f"  - `{src_name}` -[{change['kind']}]-> `{tgt_name}`")
    print("\n".join(lines))
    return 0
