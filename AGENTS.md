# RepoMap — Skill + CLI for AI-Agent Repository Intelligence

`repomap` is a **skill + CLI tool**. AI agents (Claude Code, Codex, OpenCode) invoke it via the skill definition in `skills/repomap/SKILL.md`. The skill tells the agent *when* to call `repomap`; the CLI binary does the actual work: tree-sitter AST scanning, dependency graph building, PageRank ranking, and structured report generation.

**Distribution**: users clone the skill directory + download the prebuilt binary. No server, no daemon, no MCP. See [README.md](./README.md) for the user-facing description.

## Project Snapshot

- **Shape**: Python package (`src/`) + prebuilt binaries for Linux/macOS/Windows (via GitHub Releases)
- **Core capability**: tree-sitter AST → symbol extraction → import resolution → call-chain analysis → AI-friendly reports
- **Languages**: Python, JS/TS (including TSX), Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON
- **Distribution**: `skills/repomap/` → `~/.claude/skills/repomap/`; binary → `~/.local/bin/repomap`
- **No server/daemon**: LSP integration is opt-in, local-only, stdio-based

## Commands

All via `repomap <subcommand> --project <path>`.

| Command | Purpose |
|---|---|
| `overview` | Project map: modules, entry points, reading order, hotspots, key symbols |
| `query --query "keyword"` | Topic/feature discovery by business words |
| `file-detail --file-path <f>` | Symbols and structure of a known file |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius + edit planning |
| `query-symbol --symbol <name>` | Exact/fuzzy symbol lookup |
| `call-chain --symbol <name>` | Caller/callee context |
| `refs --symbol <name>` | Reference discovery |
| `verify [--quick] [--with-lsp] [--with-diff]` | Post-edit evidence gate |
| `check` | Compiler/type/lint diagnostics |
| `routes [--json]` | HTTP/API route inventory |
| `orphan [--json]` | Dead-code candidate discovery |
| `hotspots` | Dense-file inventory |
| `cache save` / `diff` | Graph baseline + comparison |
| `lsp doctor` | Inspect local LSP availability |

```bash
# Run from source
uv run repomap --help

# Tests
uv run python -m unittest discover -s tests -v

# Build binary
uv run --with pyinstaller python -m src.cli build-binary --output dist
```
## Architecture

```
src/                    # Python package (flat)
├── __init__.py            # Core data structures: Symbol, Edge, RepoGraph, ScanStats
├── cli/                   # CLI entrypoint
│   ├── __init__.py
│   ├── __main__.py        # python -m repomap entry
│   └── cli.py             # argparse CLI, all subcommand dispatch
├── core.py                # RepoMapEngine: scan pipeline, graph build, skip lists
├── parser.py              # TreeSitterAdapter: AST parsing, import/export bindings
├── resolver.py            # ImportResolver: resolve imports to file paths
├── ranking.py             # EdgeBuilder, GraphAnalyzer: PageRank, call-graph edges
├── topic.py               # Topic scoring, test matching, file role classification
├── check.py               # RepoMapChecker: diagnostics (eslint, tsc, ruff, go vet, ...)
├── toolkit.py             # Cache/diff/git helper logic
├── ai.py                  # Markdown report rendering (overview, impact, verify, query)
└── lsp.py                 # Optional local LSP integration (stdio, on-demand)
skills/repomap/            # AI agent skill definition
├── SKILL.md               # Agent decision procedure
└── references/            # Command map, prompt examples, authoring checklist
tests/                     # Test suite
dist/repomap               # Local build output (CI builds Linux/macOS/Windows via GitHub Actions)
```

**Dependency flow**: `cli.py` → `core.py` (engine) → `parser.py` (AST) → `resolver.py` (imports) → `ranking.py` (graph) → `ai.py` (reports). Cross-cutting: `__init__.py` (data types), `topic.py` (scoring), `check.py` (diagnostics), `toolkit.py` (cache/git).

## Change Map

- **Parser/AST**: `src/parser.py`, `src/resolver.py` → all symbol/call-chain commands
- **Graph/ranking**: `src/ranking.py` → `overview`, `call-chain`, `query-symbol`, `impact`, `hotspots`
- **CLI/commands**: `src/cli/cli.py` → add subparser + wire to engine + renderer in `src/ai.py`
- **Reports**: `src/ai.py` → each `render_*` function owns one report type
- **Topic scoring**: `src/topic.py` → `impact`, `verify`, `query` test suggestions
- **Diagnostics**: `src/check.py` → `check`, `verify`
- **Cache/diff**: `src/toolkit.py` → `cache save`, `diff`, `verify --with-diff`
- **LSP**: `src/lsp.py` → opt-in, affects `diagnostics`, `query-symbol --with-lsp`, etc.

## Verification

| Scope | Command |
|---|---|
| Parser | `uv run python -m unittest discover -s tests -p 'test_repomap_parser_ast.py' -v` |
| CLI | `uv run python -m unittest discover -s tests -p 'test_repomap_cli.py' -v` |
| Engine | `uv run python -m unittest discover -s tests -p 'test_repomap_engine.py' -v` |
| Toolkit | `uv run python -m unittest discover -s tests -p 'test_repomap_toolkit.py' -v` |
| LSP | `uv run python -m unittest discover -s tests -p 'test_repomap_lsp.py' -v` |
| Binary E2E | `uv run --with pyinstaller python -m unittest discover -s tests -p 'test_repomap_binary_e2e.py' -v` |
| Full | `uv run python -m unittest discover -s tests -v` |
| Smoke | `repomap doctor && repomap overview --project . && repomap verify --project . --quick` |

## README Maintenance

The public README files serve different audiences than this document:

- **README.md** (English, primary): user-facing — what, how to install, how to use. Keep concise, answer "what/why/how" within 10 seconds.
- **README.zh-CN.md** (Chinese): same content and structure — not literal translation but content-equivalent. All sections, commands, tables must exist in both.
- **SKILL.md** (`skills/repomap/SKILL.md`): AI agent operating procedure. Describes *when and how* to call each command. Distributed to users.

**Rules for README changes**:
- README describes the *product*, not the implementation. No module names, internal architecture, or refactoring details.
- Binary URLs must point to GitHub Releases: `repomap-linux`, `repomap-macos`, `repomap.exe`.
- Language support list must match `src/parser.py` and `pyproject.toml`.
- When adding commands, update README.md, README.zh-CN.md, and SKILL.md.
- README does NOT say "For Humans" / "For AI Agents" — present information directly.

## Project Rules

- `src/cli/cli.py` owns all argparse definitions and subcommand dispatch.
- `src/__init__.py` is single source of truth for data structures (Symbol, Edge, RepoGraph, ScanStats, HttpRoute).
- Report rendering stays in `src/ai.py`; engine/parser/ranking produce data, `ai.py` formats it.
- Import resolution goes through `src/resolver.py`; do not hand-roll path resolution.
- Session cache version in `cli.py` must be bumped when scan cache semantics change.
- `--project` must be absolute when called from AI/Agent contexts.
- LSP is strictly opt-in, local-only. Never auto-install servers, never run `npx`/`pnpx`/`bunx`.
- `verify` suggests tests but does not run them. Agents must run tests explicitly.
- Cache directories are keyed by canonical project path.
- `.gitignore` keeps `AGENTS.md`, `CLAUDE.md`, and `docs/` local-only (not in public repo).

## Skill Distribution

The open-source skill (`skills/repomap/SKILL.md`) is distributed to users and must NOT include:
- `## Optimization Feedback` section — local maintainer use only
- Any references to local file paths (e.g., absolute paths on maintainer's machine)
- Any maintainer-specific workflow or feedback mechanisms

The local skill (`~/.agents/skills/repomap/SKILL.md`) includes the full `## Optimization Feedback` section for continuous improvement based on real-world usage.

## Binary Build Workflow

After any code change to `src/`, rebuild the binary and verify PATH is fresh:

```bash
# 1. Build binary
uv run --with pyinstaller python -m src.cli build-binary --output dist

# 2. Verify the new binary is in PATH and works
which repomap
repomap doctor
repomap overview --project . --quick
```

The built binary at `dist/repomap` must be the one in PATH (symlinked or copied to `~/.local/bin/repomap`). After rebuild, run `repomap doctor` then `repomap overview --project . --quick` to smoke-test.

## Skill Sync Rules

After modifying repomap source code or skill files, sync between skill directories:

| Path | Role |
|------|------|
| `skills/repomap/` | Source of truth — edit here first |
| `~/.agents/skills/repomap/` | Local deployment — sync from source |

**Sync procedure**:

```bash
# Sync references and scripts (must be byte-identical)
cp -r skills/repomap/references/* ~/.agents/skills/repomap/references/
cp -r skills/repomap/scripts/* ~/.agents/skills/repomap/scripts/

# Sync SKILL.md then add Optimization Feedback section locally
cp skills/repomap/SKILL.md ~/.agents/skills/repomap/SKILL.md
# Manually append ## Optimization Feedback to local copy
```

**Consistency rules**:
- `references/` and `scripts/`: byte-identical at all times
- `SKILL.md` in `~/.agents/skills/repomap/`: open-source version **plus** `## Optimization Feedback` appended — no other differences
- After changes, verify with `diff -r skills/repomap/references/ ~/.agents/skills/repomap/references/`
