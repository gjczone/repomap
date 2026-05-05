# RepoMap CLI — AI-Agent Repository Intelligence Layer

A standalone Python CLI that reads a code project and produces a structured "project map": important files, function call chains, change impact analysis, related tests, risk assessment, and optional local LSP diagnostics. Primary users are AI coding agents.

## Project Snapshot

- **Runtime shape**: Single Python CLI binary (PyInstaller) or `uv run` Python command. Stateless per invocation.
- **Core capability**: tree-sitter AST graph → symbol extraction → import resolution → call-chain analysis → AI-friendly reports.
- **Supported languages**: Python, JS/TS (including TSX), Go, Rust, HTML, CSS, JSON. Optional: YAML, Markdown, Swift, Kotlin, PHP, Ruby.
- **No server/daemon**: LSP integration is opt-in, local-only, stdio-based. No MCP server, no background processes.
- **Primary user flow**: `overview` (first look) → `query` (topic search) → `file-detail` / `impact --with-symbols` (pre-edit) → code changes → `verify` (post-edit evidence gate).

## Commands

All commands use `repomap <subcommand>`. Pass `--project <path>` to target a specific project (recommended); omit to scan CWD.

| Command | Purpose |
|---|---|
| `overview` | Project map: modules, entry points, reading order, key symbols |
| `query --query "keyword"` | Topic/feature discovery by business words |
| `file-detail --file-path <f>` | Symbols and structure of a known file |
| `impact --files <f...> [--with-symbols]` | Pre-edit blast radius + optional edit planning |
| `query-symbol --symbol <name>` | Exact/fuzzy symbol lookup |
| `call-chain --symbol <name>` | Caller/callee context for a symbol |
| `refs --symbol <name>` | Reference discovery (add `--with-lsp` for exact evidence) |
| `verify [--quick] [--with-lsp] [--with-diff]` | Post-edit evidence gate |
| `check` | Compiler/type/lint diagnostics |
| `routes [--json]` | HTTP/API route inventory |
| `orphan [--json]` | Dead-code candidate discovery |
| `hotspots` | Dense-file complexity/churn inventory |
| `cache save` | Save graph baseline before edits (for later `diff`) |
| `diff` | Graph-only comparison against saved baseline |
| `lsp doctor` | Inspect local LSP server availability |

### Key Runtime Commands

```bash
# Run from source
uv run python -m repomap_cli --help
uv run repomap --help

# Run tests (runtime only)
uv run python -m unittest discover -s tests -v

# Run tests (including binary build)
uv run --with pyinstaller python -m unittest discover -s tests -v

# Build binary
uv run --with pyinstaller python -m repomap_cli build-binary --output dist

# Smoke check
repomap doctor
```

## Architecture

```text
repomap_cli/
  __init__.py       # re-exports main
  __main__.py       # python -m repomap_cli entry
  cli.py            # argparse CLI, all subcommand dispatch
repomap_core.py     # RepoMapEngine: scan pipeline, graph build, skip dirs/files
repomap_parser.py   # TreeSitterAdapter: AST parsing, import/export bindings
repomap_resolver.py # ImportResolver: resolve imports to file paths
repomap_ranking.py  # EdgeBuilder, GraphAnalyzer: PageRank, call-graph edges
repomap_topic.py    # Topic scoring, test matching, file role classification
repomap_check.py    # RepoMapChecker: diagnostics (eslint, tsc, ruff, go vet, etc.)
repomap_toolkit.py  # Cache/diff/git helper logic
repomap_ai.py       # Markdown report rendering (overview, impact, verify, query)
repomap_support.py  # Core data structures: Symbol, Edge, RepoGraph, ScanStats
repomap_lsp.py      # Optional local LSP integration (stdio, on-demand)
```

**Dependency flow**: `cli.py` → `repomap_core.py` (engine) → `repomap_parser.py` (AST) → `repomap_resolver.py` (imports) → `repomap_ranking.py` (graph) → `repomap_ai.py` (reports). Cross-cutting: `repomap_support.py` (data), `repomap_topic.py` (scoring), `repomap_check.py` (diagnostics), `repomap_toolkit.py` (cache/git).

## Core Flows

1. **Project discovery**: `cli.py:build_parser()` → `repomap_core.py:RepoMapEngine.scan_project()` → tree-sitter parse → graph build → `repomap_ai.py:render_overview_report()`
2. **Pre-edit planning**: `cli.py` → `impact` handler → `repomap_core.py` scan → `repomap_topic.py:find_related_tests()` + symbol ranking → `repomap_ai.py:render_impact_report()`
3. **Post-edit verification**: `cli.py` → `verify` handler → git changed files + `repomap_core.py` scan + `repomap_check.py` diagnostics + optional LSP → `repomap_ai.py:render_verify_report()`
4. **Topic search**: `cli.py` → `query` handler → `repomap_core.py` scan → `repomap_topic.py:topic_score()` keyword matching → `repomap_ai.py:render_query_report()`
5. **Call chain**: `cli.py` → `repomap_core.py` scan → `repomap_ranking.py:GraphAnalyzer` caller/callee traversal → `repomap_ai.py:render_call_chain_report()`

## Change Map

- **Parser/AST changes**: inspect `repomap_parser.py`, `repomap_resolver.py`; affects all commands that produce symbols or call-chain edges.
- **Graph/ranking changes**: inspect `repomap_ranking.py`; affects `overview`, `call-chain`, `query-symbol`, `impact`, `hotspots`.
- **CLI/command changes**: inspect `repomap_cli/cli.py`; add argparse subparser + wire to engine + add renderer in `repomap_ai.py`.
- **Report format changes**: inspect `repomap_ai.py`; each `render_*` function owns one report type.
- **Test matching / topic scoring**: inspect `repomap_topic.py`; affects `impact`, `verify`, `query` test suggestions.
- **Diagnostics / check changes**: inspect `repomap_check.py`; affects `check`, `verify`.
- **Cache/diff behavior**: inspect `repomap_toolkit.py`, `repomap_support.py:get_cache_paths()`; affects `cache save`, `diff`, `verify --with-diff`.
- **LSP integration**: inspect `repomap_lsp.py`; opt-in, affects `diagnostics`, `query-symbol --with-lsp`, `refs --with-lsp`, `verify --with-lsp`.

## Verification Matrix

| Change type | Command |
|---|---|
| Parser/AST | `uv run python -m unittest discover -s tests -p 'test_repomap_parser_ast.py' -v` |
| CLI commands | `uv run python -m unittest discover -s tests -p 'test_repomap_cli.py' -v` |
| Engine/graph | `uv run python -m unittest discover -s tests -p 'test_repomap_engine.py' -v` |
| Toolkit/cache | `uv run python -m unittest discover -s tests -p 'test_repomap_toolkit.py' -v` |
| LSP | `uv run python -m unittest discover -s tests -p 'test_repomap_lsp.py' -v` |
| Binary E2E | `uv run --with pyinstaller python -m unittest discover -s tests -p 'test_repomap_binary_e2e.py' -v` |
| Full runtime | `uv run python -m unittest discover -s tests -v` |
| Full + binary | `uv run --with pyinstaller python -m unittest discover -s tests -v` |
| Smoke check | `repomap doctor && repomap overview --project . && repomap verify --project . --quick` |

## First Places to Inspect

- `repomap_cli/cli.py` — all command definitions and dispatch
- `repomap_core.py` — scan pipeline, graph construction, skip lists
- `repomap_parser.py` — tree-sitter AST parsing, language support
- `repomap_ai.py` — all report rendering
- `repomap_support.py` — core data structures (Symbol, Edge, RepoGraph)

## Key Directories

- `repomap_cli/` — CLI package (entrypoint, argparse, dispatch)
- `tests/` — unit tests and binary E2E tests
- `docs/` — acceptance checklist, AI smoke check guide, delivery reports
- `dist/` — built Linux binary

## Important Files

- `pyproject.toml` — project metadata, dependencies, entry points
- `repomap_support.py` — canonical data structures shared across all modules
- `repomap_parser.py` — EXT_TO_LANG mapping, language grammar selection
- `repomap_core.py` — SKIP_DIR_NAMES, SKIP_FILE_NAMES, DEFAULT_MAX_FILE_BYTES

<general-project-rules>
# General Project Rules

## Coding Rules
- `cli.py` owns all argparse definitions and subcommand dispatch; do not add argparse logic in engine or parser modules.
- `repomap_support.py` is the single source of truth for shared data structures (Symbol, Edge, RepoGraph, ScanStats, HttpRoute); define new types there, not inline in other modules.
- Keep report rendering in `repomap_ai.py`; engine/parser/ranking modules produce data, `repomap_ai.py` formats it.
- Import resolution goes through `repomap_resolver.py`; do not hand-roll path resolution in CLI or engine code.
- Session cache version (`SESSION_CACHE_VERSION` in `cli.py`) must be bumped when scan cache semantics change.

## Testing Rules
- Run focused tests for touched modules: `uv run python -m unittest discover -s tests -p 'test_repomap_<module>.py' -v`.
- Binary E2E tests (`test_repomap_binary_e2e.py`) actually build the executable; run with `--with pyinstaller`.
- Tests use `tempfile.TemporaryDirectory` for isolated project fixtures; do not depend on real project state.
- The `write_file` helper in tests creates files inside temp dirs; use it rather than writing to real paths.

## Project-Specific Rules
- Do not remove or rename `SKIP_DIR_NAMES` / `SKIP_FILE_NAMES` in `repomap_core.py` without updating all scan paths; these control what gets excluded from analysis.
- `--project` argument must always be passed as an absolute path when called from AI/Agent contexts to avoid scanning the wrong directory (e.g., home dir).
- LSP integration is strictly opt-in and local-only: never auto-install servers, never run `npx`/`pnpx`/`bunx`, never create background daemons.
- `verify` does not automatically run project tests; it suggests them. Agents must run tests explicitly when the change requires it.
- `routes` intentionally filters test/e2e/spec DSL noise; do not add test mock routes to the production route inventory.
- Cache directories are keyed by canonical project path; relative and absolute references to the same project share a cache.
</general-project-rules>
