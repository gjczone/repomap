"""Lightweight state-map: structural map of state values, writers, and readers.

Supports Python (Enum), TypeScript (enum / string union / const object),
Rust (enum), and Go (typed constants).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import RepoMapEngine


@dataclass
class StateValue:
    name: str
    file: str
    line: int


@dataclass
class StateDefinition:
    symbol_name: str
    file: str
    line: int
    kind: str  # "enum" | "string_union" | "const_object" | "go_const_block"
    values: list[StateValue] = field(default_factory=list)
    writers: list[StateValue] = field(default_factory=list)
    readers: list[StateValue] = field(default_factory=list)


def _read_file(project_root: str, file_path: str) -> str | None:
    try:
        with open(f"{project_root}/{file_path}", "r", encoding="utf-8", errors="replace") as f:
            return f.read(131072)
    except (OSError, UnicodeDecodeError):
        return None


def find_state_definitions(
    engine: "RepoMapEngine",
    query: str | None = None,
    symbol: str | None = None,
) -> list[StateDefinition]:
    """Find enum/const state definitions.

    Matches by query keywords or specific symbol name.
    """
    results: list[StateDefinition] = []
    project_root = str(engine.project_root)

    query_terms = query.lower().split() if query else []
    for sid, sym in engine.graph.symbols.items():
        # Filter: only enum-like kinds
        if sym.kind not in ("enum", "type", "class"):
            continue

        # Match by symbol name if specified
        if symbol:
            if sym.name != symbol:
                continue
        elif query_terms:
            # Match by query keywords against symbol name
            sym_tokens = sym.name.lower().replace("_", " ").split()
            if not any(qt in sym.name.lower() for qt in query_terms):
                if not any(qt in t for qt in query_terms for t in sym_tokens):
                    continue

        content = _read_file(project_root, sym.file)
        if not content:
            continue

        lines = content.split("\n")
        defn = StateDefinition(
            symbol_name=sym.name,
            file=sym.file,
            line=sym.line,
            kind=sym.kind,
        )

        # Extract values and usages based on language
        ext = PurePosixPath(sym.file).suffix.lower()
        if ext == ".rs":
            _scan_rust_state(defn, sym, content, lines, engine)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            _scan_ts_state(defn, sym, content, lines, engine)
        elif ext == ".py":
            _scan_python_state(defn, sym, content, lines, engine)
        elif ext == ".go":
            _scan_go_state(defn, sym, content, lines, engine)

        if defn.values:
            results.append(defn)

    return results


def _scan_rust_state(defn: StateDefinition, sym, content: str, lines: list[str], engine):
    """Extract Rust enum variants and match/write sites."""
    # Find enum variants with line numbers by scanning from symbol line
    in_enum = False
    brace_depth = 0
    for i in range(sym.line - 1, len(lines)):
        line = lines[i]
        if not in_enum:
            if "{" in line:
                in_enum = True
                brace_depth = 1
                # Check same line for variant
                after_brace = line.split("{", 1)[1] if "{" in line else ""
                if after_brace.strip():
                    m = re.match(r"^\s*(\w+)\s*(?:=|,)", after_brace.strip())
                    if m and m.group(1) not in ("pub", "use", "where", "impl"):
                        defn.values.append(StateValue(name=m.group(1), file=defn.file, line=i + 1))
                continue
            continue
        for ch in line:
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    break
        if in_enum and brace_depth == 0:
            break
        if brace_depth == 1:
            m = re.match(r"^\s*(\w+)\s*(?:=|,)", line)
            if m and m.group(1) not in ("pub", "use", "where", "impl"):
                defn.values.append(StateValue(name=m.group(1), file=defn.file, line=i + 1))

    variant_names = {v.name for v in defn.values}
    for i, line in enumerate(lines, 1):
        for vname in variant_names:
            pattern = rf"{sym.name}::{vname}\b"
            if re.search(pattern, line):
                defn.writers.append(StateValue(name=f"{sym.name}::{vname}", file=defn.file, line=i))
    for i, line in enumerate(lines, 1):
        if f"{sym.name}::" in line:
            defn.readers.append(StateValue(name=line.strip()[:80], file=defn.file, line=i))


def _scan_ts_state(defn: StateDefinition, sym, content: str, lines: list[str], engine):
    """Extract TS enum/const object values with line numbers."""
    if sym.kind == "enum":
        in_body = False
        brace_depth = 0
        for i in range(sym.line - 1, len(lines)):
            line = lines[i]
            if not in_body:
                if "{" in line:
                    in_body = True
                    brace_depth = 1
                continue
            for ch in line:
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        return
            if brace_depth == 1:
                m = re.match(r"^\s*(\w+)\s*[=,]", line)
                if m:
                    defn.values.append(StateValue(name=m.group(1), file=defn.file, line=i + 1))

    if sym.kind == "type":
        for i in range(sym.line - 1, min(sym.line + 20, len(lines))):
            line = lines[i]
            for m in re.finditer(r"(\w+)\s*:\s*['\"]([^'\"]+)['\"]", line):
                defn.values.append(StateValue(name=m.group(2), file=defn.file, line=i + 1))


def _scan_python_state(defn: StateDefinition, sym, content: str, lines: list[str], engine):
    """Extract Python Enum values with line numbers."""
    if sym.kind != "class":
        return

    # Scan lines from symbol position for Enum assignments
    in_class = False
    for i in range(sym.line - 1, min(sym.line + 80, len(lines))):
        line = lines[i]
        stripped = line.strip()
        if not in_class:
            if stripped.endswith(":"):
                in_class = True
            continue
        # Exit on dedent (non-empty, non-indented, non-comment line)
        if stripped and not line[0].isspace() and not stripped.startswith("#"):
            break
        m = re.match(r"^\s*(\w+)\s*=\s*(?:auto\(\)|\d+|'[^']*'|\"[^\"]*\")", line)
        if m and not m.group(1).startswith("_"):
            defn.values.append(StateValue(name=m.group(1), file=defn.file, line=i + 1))

    # Readers/writers
    variant_names = {v.name for v in defn.values}
    for i, line in enumerate(lines, 1):
        for vname in variant_names:
            if f"{sym.name}.{vname}" in line:
                if "=" in line or "return" in line:
                    defn.writers.append(StateValue(name=f"{sym.name}.{vname}", file=defn.file, line=i))
                else:
                    defn.readers.append(StateValue(name=f"{sym.name}.{vname}", file=defn.file, line=i))


def _scan_go_state(defn: StateDefinition, sym, content: str, lines: list[str], engine):
    """Extract Go typed constants."""
    # Go: type X string; const (...) block
    for i, line in enumerate(lines[sym.line - 1:], sym.line):
        stripped = line.strip()
        if stripped.startswith("const ("):
            # Read until )
            j = i
            while j < len(lines) and ")" not in lines[j]:
                ct = lines[j].strip()
                if "=" in ct or not ct.startswith("//"):
                    name = ct.split()[0] if ct and not ct.startswith("//") else ""
                    if name and name.isidentifier():
                        defn.values.append(StateValue(name=name, file=defn.file, line=j + 1))
                j += 1
            break
