from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core import RepoMapEngine


def render_routes_report(engine: "RepoMapEngine", consumers: dict | None = None) -> str:
    routes = engine.list_routes()
    if not routes:
        return "No HTTP routes detected."
    if consumers:
        return _format_route_lines_with_consumers(routes, consumers)
    return _format_route_table(routes)


def _format_route_lines_with_consumers(routes: list, consumers: dict) -> str:
    lines: list[str] = []
    lines.append("## API Routes with Consumers\n")
    for r in sorted(routes, key=lambda r: (r.file, r.line)):
        route_key = f"{r.method} {r.path}"
        lines.append(f"\n### {r.method} `{r.path}`\n")
        lines.append(
            f"- **Handler**: `{r.handler}` — `{r.file}:{r.line}` ({r.framework})"
        )
        route_consumers = consumers.get(route_key, [])
        if route_consumers:
            lines.append("- **Consumers**:")
            for c in route_consumers:
                conf = {"high": "HIGH", "medium": "MED", "low": "LOW"}.get(
                    c.confidence, c.confidence
                )
                lines.append(f"  - `{c.file}:{c.line}` [{conf}: {c.match_type}]")
                if c.context:
                    lines.append(f"    > {c.context}")
        else:
            lines.append("- **Consumers**: none detected")
    return "\n".join(lines)


def _render_route_section(engine: "RepoMapEngine") -> list[str]:
    routes = engine.list_routes()
    if not routes:
        return []
    return _format_route_lines(routes, compact=True)


def _format_route_lines(routes: list, compact: bool = False) -> list[str]:
    lines = ["## API Routes\n"]
    routes_sorted = sorted(routes, key=lambda r: (r.file, r.line))

    if compact and len(routes) > 12:
        by_file: dict[str, list] = {}
        for r in routes_sorted:
            by_file.setdefault(r.file, []).append(r)
        for file, file_routes in list(by_file.items())[:6]:
            methods = Counter(r.method for r in file_routes)
            method_str = " ".join(
                f"{m}x{methods[m]}"
                for m in ("GET", "POST", "PUT", "DELETE", "PATCH")
                if methods[m]
            )
            lines.append(f"- `{file}` — {len(file_routes)} routes ({method_str})")
        if len(by_file) > 6:
            lines.append(f"- ... {len(by_file) - 6} more files with routes")
    else:
        if len(routes_sorted) > 20 and compact:
            lines.append(f"> （{len(routes)} routes total, showing top 20）\n")
        lines.append("| Method | Path | Handler | File | Framework |")
        lines.append("|--------|------|---------|------|-----------|")
        for r in routes_sorted[:20]:
            lines.append(
                f"| {r.method} | `{r.path}` | `{r.handler}` | `{r.file}:{r.line}` | {r.framework} |"
            )
        if len(routes_sorted) > 20 and compact:
            lines.append(f"\n... {len(routes_sorted) - 20} more routes")
    lines.append("")
    return lines


def _format_route_table(routes: list) -> str:
    lines = _format_route_lines(routes, compact=False)
    return "\n".join(lines)
