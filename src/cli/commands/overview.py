from __future__ import annotations

import sys
from typing import Any

from ... import json_dumps
from ... import (
    DEFAULT_OVERVIEW_JSON_HOTSPOTS,
    DEFAULT_OVERVIEW_JSON_MODULES,
    DEFAULT_OVERVIEW_JSON_READING_ORDER,
    DEFAULT_OVERVIEW_JSON_SUMMARY_FILES,
    DEFAULT_OVERVIEW_JSON_SUPPORTING_FILES,
    DEFAULT_OVERVIEW_JSON_SYMBOLS_PER_FILE,
)
from ...ai import (
    _build_query_reading_order,
    _get_hot_files,
    _rank_symbols_for_file,
    _truncate_output,
)
from ...core import RepoMapEngine
from ..handlers import (
    CLI_NAME,
    EXIT_SUCCESS,
    EXIT_ERROR,
    _resolve_project,
    _scan_engine,
    _scan_stats_payload,
)
from ...ranking import GraphAnalyzer
from ...topic import classify_file_role


def run_scan(project: str, max_files: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        hot = engine.hotspots(5)
        entry_points = engine.entry_points()
        lines = [
            f"✅ Scan complete — `{engine.project_root}`\n",
            *engine._scan_summary_lines(),
            f"- Entry points: {', '.join(entry_points) or 'None detected'}",
            "\n**High-Density Files (Top 5)**:",
        ]
        if engine.scan_stats.truncated_files:
            lines.insert(
                6, f"- max_files truncated: {engine.scan_stats.truncated_files}"
            )
        for item in hot:
            lines.append(
                f"  - `{item['file']}` — {item['symbol_count']} symbols ({item['risk']} risk)"
            )
        lines.append(
            "\n> Next: run `repomap overview --project <path>` for a full project map."
        )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] scan failed: {exc}", file=sys.stderr)
        return 1


def run_overview(
    project: str,
    max_files: int,
    max_chars: int,
    as_json: bool,
    with_heat: bool = False,
    with_co_change: bool = False,
    granularity: str = "auto",
    co_change_days: int = 30,
) -> int:
    try:
        engine = _scan_engine(project, max_files)

        if as_json:
            payload = {
                "project_root": str(engine.project_root),
                "scan_stats": _scan_stats_payload(engine),
                "entry_points": engine.entry_points(),
                "hotspots": engine.hotspots(DEFAULT_OVERVIEW_JSON_HOTSPOTS),
                "reading_order": engine.suggested_reading_order(
                    DEFAULT_OVERVIEW_JSON_READING_ORDER
                ),
                "modules": engine.module_summary(DEFAULT_OVERVIEW_JSON_MODULES),
                "summary_symbols": engine.summary_symbols(
                    DEFAULT_OVERVIEW_JSON_SUMMARY_FILES,
                    DEFAULT_OVERVIEW_JSON_SYMBOLS_PER_FILE,
                ),
                "supporting_files": engine.supporting_files(
                    DEFAULT_OVERVIEW_JSON_SUPPORTING_FILES
                ),
                "hot_files": list(_get_hot_files(str(engine.project_root)))
                if with_heat
                else [],
            }
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(
            engine.render_overview(
                max_chars,
                with_heat=with_heat,
                with_co_change=with_co_change,
                granularity=granularity,
                co_change_days=co_change_days,
            )
        )
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] overview failed: {exc}", file=sys.stderr)
        return 1


def run_hotspots(project: str, max_files: int, limit: int) -> int:
    try:
        engine = _scan_engine(project, max_files)
        hotspots = engine.hotspots(limit)
        risk_mark = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        lines = ["## High-Density Files (by symbol count)\n"]
        for index, item in enumerate(hotspots, 1):
            lines.append(
                f"{index}. {risk_mark[item['risk']]} `{item['file']}` — **{item['symbol_count']}** symbols"
            )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] hotspots failed: {exc}", file=sys.stderr)
        return 1
