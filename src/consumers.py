"""Route consumer detection — cross-boundary mapping from backend routes to frontend/test consumers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import HttpRoute
    from .core import RepoMapEngine

logger = logging.getLogger("repomap")


@dataclass
class RouteConsumer:
    file: str
    line: int
    context: str  # function name or code snippet
    confidence: str  # "high" | "medium" | "low"
    match_type: str  # "exact_literal" | "normalized_dynamic" | "prefix_concatenation"


# Patterns per language for HTTP client calls with literal route strings
_JS_TS_CONSUMER_PATTERNS: list[tuple[str, int]] = [
    (r"""fetch\s*\(\s*['"`](/[^'"`)]*)['"`]""", 1),
    (r"""fetch\s*\(\s*`(/[^`]*)`""", 1),
    (
        r"""axios\.(?:get|post|put|patch|delete|head|options)\s*\(\s*['"`](/[^'"`)]*)['"`]""",
        1,
    ),
    (r"""axios\.(?:get|post|put|patch|delete|head|options)\s*\(\s*`(/[^`]*)`""", 1),
]

_CONSUMER_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "javascript": _JS_TS_CONSUMER_PATTERNS,
    "typescript": _JS_TS_CONSUMER_PATTERNS,
    "tsx": _JS_TS_CONSUMER_PATTERNS,
    "python": [
        (
            r"""requests\.(?:get|post|put|patch|delete|head|options)\s*\(\s*['"](/[^'"]*)['"]""",
            1,
        ),
        (
            r"""httpx\.(?:get|post|put|patch|delete|head|options)\s*\(\s*['"](/[^'"]*)['"]""",
            1,
        ),
        (
            r"""client\.(?:get|post|put|patch|delete|head|options)\s*\(\s*['"](/[^'"]*)['"]""",
            1,
        ),
        (r"""\.get\s*\(\s*['"](/[^'"]*)['"]""", 1),  # test client
        (r"""\.post\s*\(\s*['"](/[^'"]*)['"]""", 1),
    ],
    "rust": [
        (r"""\.get\s*\(\s*"([^"]*)"\s*\)""", 1),
        (r"""\.post\s*\(\s*"([^"]*)"\s*\)""", 1),
        (r"""\.put\s*\(\s*"([^"]*)"\s*\)""", 1),
        (r"""\.delete\s*\(\s*"([^"]*)"\s*\)""", 1),
        (r"""\.patch\s*\(\s*"([^"]*)"\s*\)""", 1),
        (r"""\.request\s*\(\s*Method::\w+,\s*"([^"]*)""", 1),
    ],
    "go": [
        (r"""http\.Get\s*\(\s*"([^"]*)"\s*\)""", 1),
        (r"""http\.Post\s*\(\s*"([^"]*)""", 1),
        (r"""http\.NewRequest\s*\(\s*"[A-Z]+",\s*"([^"]*)""", 1),
    ],
}

# File extensions per language
_LANG_EXTENSIONS: dict[str, list[str]] = {
    "javascript": [".js", ".mjs", ".cjs"],
    "typescript": [".ts"],
    "tsx": [".tsx"],
    "python": [".py"],
    "rust": [".rs"],
    "go": [".go"],
}


def normalize_route_path(path: str) -> str:
    """Normalize route path for cross-language matching.

    Converts :param (Express/FastAPI) to {param} (Axum standard).
    Removes template string interpolation ${var} for matching.
    """
    # Express/FastAPI :param -> {param}
    norm = re.sub(r":(\w+)", r"{\1}", path)
    return norm


def _route_pattern_for_matching(route_path: str) -> list[tuple[str, str]]:
    """Generate regex patterns to match a route in client code.

    Returns list of (pattern, match_type) tuples.
    """
    norm = normalize_route_path(route_path)
    patterns: list[tuple[str, str]] = []

    # Exact literal match
    escaped = re.escape(norm)
    patterns.append((escaped, "exact_literal"))

    # Dynamic segment -> wildcard for template literal / concatenation matching
    wildcard = re.sub(r"\\\{[^}]+\\\}", r"[^/'\")`]+", escaped)
    if wildcard != escaped:
        patterns.append((wildcard, "normalized_dynamic"))

    # Prefix match (for concatenation like '/path/' + name)
    prefix = norm.rsplit("/{", 1)[0] if "/{" in norm else norm
    if prefix != norm:
        escaped_prefix = re.escape(prefix)
        patterns.append((escaped_prefix, "prefix_concatenation"))

    return patterns


def find_route_consumers(
    engine: "RepoMapEngine",
    routes: list["HttpRoute"],
) -> dict[str, list[RouteConsumer]]:
    """Find source files that consume each route.

    Returns dict keyed by route identity string "METHOD /path".
    """

    consumers: dict[str, list[RouteConsumer]] = {}

    # Build extension -> language map
    ext_to_lang: dict[str, str] = {}
    for lang, exts in _LANG_EXTENSIONS.items():
        for ext in exts:
            ext_to_lang[ext] = lang

    # Scan each source file
    project_root = str(engine.project_root)
    for file_path in sorted(engine.graph.file_symbols.keys()):
        ext = PurePosixPath(file_path).suffix.lower()
        lang = ext_to_lang.get(ext)
        if not lang:
            continue

        patterns = _CONSUMER_PATTERNS.get(lang, [])
        if not patterns:
            continue

        # Read file content (bounded)
        try:
            full_path = f"{project_root}/{file_path}"
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(65536)  # 64KB max
        except (OSError, UnicodeDecodeError):
            logger.debug(
                "Failed to read %s for route consumer detection",
                file_path,
                exc_info=True,
            )
            continue

        lines = content.split("\n")

        for regex, group_idx in patterns:
            for match in re.finditer(regex, content):
                route_literal = match.group(group_idx)
                if not route_literal or not route_literal.startswith("/"):
                    continue
                if len(route_literal) < 2:
                    continue

                # Find line number
                pos = match.start()
                line_num = content[:pos].count("\n") + 1

                # Get context (function name on same or nearby line)
                context = ""
                if line_num <= len(lines):
                    ctx_line = lines[line_num - 1].strip()
                    context = ctx_line[:80]

                # Match against routes
                route_key = ""
                confidence = ""
                match_type = ""

                for route in routes:
                    route_lit = route.path
                    route_norm = normalize_route_path(route_lit)

                    # Exact match
                    if route_literal == route_lit or route_literal == route_norm:
                        confidence = "high"
                        match_type = "exact_literal"
                        route_key = f"{route.method} {route_lit}"
                        break

                    # Normalized dynamic match
                    if not route_key:
                        # Replace dynamic segments in route with wildcards
                        route_regex_str = re.escape(route_norm)
                        route_regex_str = re.sub(
                            r"\\\{[^}]+\\\}", r"[^/]+", route_regex_str
                        )
                        if re.match(f"^{route_regex_str}$", route_literal):
                            confidence = "medium"
                            match_type = "normalized_dynamic"
                            route_key = f"{route.method} {route_lit}"

                    # Prefix match (route literal starts with route path prefix)
                    if not route_key and "{" in route_norm:
                        prefix = route_norm.rsplit("/{", 1)[0]
                        if (
                            route_literal.startswith(prefix)
                            and len(route_literal) > len(prefix) + 1
                        ):
                            confidence = "low"
                            match_type = "prefix_concatenation"
                            route_key = f"{route.method} {route_lit}"

                if not route_key:
                    continue

                consumer = RouteConsumer(
                    file=file_path,
                    line=line_num,
                    context=context,
                    confidence=confidence,
                    match_type=match_type,
                )
                consumers.setdefault(route_key, []).append(consumer)

    return consumers
