from __future__ import annotations

import functools
import logging
from pathlib import Path

logger = logging.getLogger("repomap")

RISK_MARK = {"high": "[high]", "medium": "[medium]", "low": "[low]"}
VISIBILITY_MARK = {
    "exported": "[exported]",
    "public": "[public]",
    "private": "[private]",
}
CONFIDENCE_MARK = {"high": "HIGH", "medium": "MED", "low": "LOW"}


def _truncate_output(output: str, max_chars: int) -> str:
    if max_chars <= 0 or len(output) <= max_chars:
        return output
    truncated = output[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars // 2:
        truncated = truncated[:last_newline]
    else:
        truncated = truncated[:max_chars]
    original_size = len(output)
    truncated_size = len(truncated)
    ratio = int(truncated_size / original_size * 100)
    return (
        truncated
        + f"\n\n[output truncated: {truncated_size}/{original_size} chars ({ratio}%)]"
    )


@functools.lru_cache(maxsize=64)
def _read_file_cached(abs_path: str) -> list[str] | None:
    try:
        return Path(abs_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _read_symbol_source(
    project_root: "str | Path",
    file_path: str,
    line: int,
    end_line: int = 0,
    max_lines: int = 80,
) -> str:
    if max_lines <= 0:
        return ""
    if end_line <= line:
        end_line = line + 5
    abs_path = str(Path(project_root) / file_path)
    all_lines = _read_file_cached(abs_path)
    if all_lines is None:
        return ""
    start_idx = max(0, line - 1)
    end_idx = min(len(all_lines), end_line)
    lines = all_lines[start_idx:end_idx]
    if len(lines) > max_lines:
        lines = list(lines[:max_lines])
        lines.append(
            f"# ... (truncated, showing first {max_lines} of {end_idx - start_idx} lines)"
        )
    return "\n".join(lines)


__all__ = [
    "_get_hot_files",
    "_build_query_reading_order",
    "_rank_symbols_for_file",
    "render_overview_report",
    "render_call_chain_report",
    "render_file_detail_report",
    "render_query_report",
    "render_impact_report",
    "render_verify_report",
    "render_affected_report",
    "render_routes_report",
]

from .overview import _get_hot_files, render_overview_report  # noqa: E402
from .call_chain import render_call_chain_report  # noqa: E402
from .file_detail import render_file_detail_report  # noqa: E402
from .query import (  # noqa: E402
    _build_query_reading_order,
    _rank_symbols_for_file,
    render_query_report,
)
from .impact import render_impact_report  # noqa: E402
from .verify import render_verify_report  # noqa: E402
from .affected import render_affected_report  # noqa: E402
from .routes import render_routes_report  # noqa: E402
