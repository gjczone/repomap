from __future__ import annotations

import fnmatch
import sys
from pathlib import PurePosixPath
from typing import Any

from ...core import RepoMapEngine
from ...topic import is_test_like_file
from ..handlers import (
    CLI_NAME,
    _normalize_project_relative_paths,
    _scan_engine,
    _scan_stats_payload,
    json_envelope,
)

_AFFECTED_MAX_DEPTH = 5


def run_affected(
    project: str,
    max_files: int,
    target_files: list[str],
    as_json: bool,
    stdin: bool = False,
    filter_pattern: str | None = None,
    depth: int = 2,
) -> int:
    """Discover affected test files from changed source files."""
    if stdin and not target_files:
        target_files = [
            line.strip() for line in sys.stdin.read().splitlines() if line.strip()
        ]

    if not target_files:
        if as_json:
            print(
                json_envelope(
                    "affected",
                    str(project),
                    {
                        "scan_stats": {},
                        "changed_files": [],
                        "affected_tests": [],
                        "dependency_chain": [],
                    },
                )
            )
            return 0
        print("# Affected Tests\n\nNo changed files specified.")
        return 0

    if depth < 1:
        print(
            f"[{CLI_NAME}] --depth {depth} is invalid, must be >= 1, using 1",
            file=sys.stderr,
        )
        depth = 1
    elif depth > _AFFECTED_MAX_DEPTH:
        print(
            f"[{CLI_NAME}] --depth {depth} exceeds max {_AFFECTED_MAX_DEPTH}, "
            f"clamping to {_AFFECTED_MAX_DEPTH}",
            file=sys.stderr,
        )
        depth = _AFFECTED_MAX_DEPTH

    try:
        engine = _scan_engine(project, max_files)
        target_files = _normalize_project_relative_paths(
            engine.project_root, target_files
        )

        # Collect target file symbols
        target_symbols: set[str] = set()
        for f in target_files:
            for sid in engine.graph.file_symbols.get(f, []):
                target_symbols.add(sid)

        # Find files that directly depend on target files (incoming edges)
        dependent_files: set[str] = set()
        for sid in target_symbols:
            for edge in engine.graph.incoming.get(sid, []):
                caller = engine.graph.symbols.get(edge.source)
                if caller and caller.file not in target_files:
                    dependent_files.add(caller.file)

        # BFS for transitive dependencies
        if depth > 1 and dependent_files:
            frontier = set(dependent_files)
            for _ in range(1, depth):
                next_frontier: set[str] = set()
                for f in frontier:
                    for sid in engine.graph.file_symbols.get(f, []):
                        for edge in engine.graph.incoming.get(sid, []):
                            caller = engine.graph.symbols.get(edge.source)
                            if (
                                caller
                                and caller.file not in target_files
                                and caller.file not in dependent_files
                            ):
                                next_frontier.add(caller.file)
                                dependent_files.add(caller.file)
                if not next_frontier:
                    break
                frontier = next_frontier

        # Filter for test files
        affected_tests: list[tuple[str, str]] = []
        for f in sorted(dependent_files):
            if not is_test_like_file(f):
                continue
            if filter_pattern:
                if not fnmatch.fnmatch(f, filter_pattern):
                    continue
            reason = _find_affected_reason(f, target_files, engine)
            affected_tests.append((f, reason))

        # Build dependency chains
        chains: list[dict[str, Any]] = []
        for src in target_files:
            src_module = _file_to_module_path(src)
            src_chains: list[dict[str, Any]] = []
            for test_f, _reason in affected_tests:
                imports = engine.graph.file_imports.get(test_f, [])
                related = any(
                    src_module in imp or imp.endswith(src_module) for imp in imports
                )
                if related:
                    src_chains.append({"test": test_f, "via": [src, test_f]})
                else:
                    # Check if test references symbols from target
                    test_sids = engine.graph.file_symbols.get(test_f, [])
                    for tsid in test_sids:
                        for edge in engine.graph.outgoing.get(tsid, []):
                            if edge.target in target_symbols:
                                src_chains.append(
                                    {"test": test_f, "via": [src, test_f]}
                                )
                                break
                        else:
                            continue
                        break
            chains.append({"changed": src, "affected_tests": src_chains})

        if as_json:
            result: dict[str, Any] = {
                "scan_stats": _scan_stats_payload(engine),
                "changed_files": target_files,
                "affected_tests": [{"file": f, "reason": r} for f, r in affected_tests],
                "dependency_chain": chains,
            }
            print(json_envelope("affected", str(engine.project_root), result))
            return 0

        from ...reports import render_affected_report

        print(render_affected_report(target_files, affected_tests, chains))
        return 0

    except Exception as exc:
        print(f"[{CLI_NAME}] affected failed: {exc}", file=sys.stderr)
        return 1


def _find_affected_reason(
    test_file: str, target_files: list[str], engine: RepoMapEngine
) -> str:
    """Determine why a test file is affected by the changed files."""
    imports = engine.graph.file_imports.get(test_file, [])
    for t in target_files:
        t_module = _file_to_module_path(t)
        for imp in imports:
            if t_module in imp or imp.endswith(t_module):
                return f"imports {t}"
    # Check symbol-level references
    test_sids = engine.graph.file_symbols.get(test_file, [])
    for t in target_files:
        t_sids = set(engine.graph.file_symbols.get(t, []))
        for tsid in test_sids:
            for edge in engine.graph.outgoing.get(tsid, []):
                if edge.target in t_sids:
                    target_sym = engine.graph.symbols.get(edge.target)
                    sym_name = target_sym.name if target_sym else "?"
                    return f"references {sym_name} from {t}"
    return "depends on changed files"


def _file_to_module_path(file_path: str) -> str:
    """Convert a file path to a module-like path for import matching."""
    p = PurePosixPath(file_path)
    stem_path = str(p.parent / p.stem) if p.suffix else str(p)
    if p.stem == "index":
        stem_path = str(p.parent)
    return stem_path
