# RepoMap — Skill + CLI for AI-Agent Repository Intelligence

`repomap` is a **skill + CLI tool**. AI agents (Claude Code, Codex, OpenCode) invoke it via the skill definition in `skills/repomap/SKILL.md`. The skill tells the agent *when* to call `repomap`; the CLI binary does the actual work: tree-sitter AST scanning, dependency graph building, PageRank ranking, and structured report generation.

**Distribution**: Pure Python skill+CLI tool. Distributed via skill definition (`skills/repomap/`) and CLI binary (`repomap`). Version managed in `pyproject.toml`.

## Project Snapshot

- **Shape**: Python package (`src/`) with CLI binary
- **Core capability**: tree-sitter AST → symbol extraction → import resolution → call-chain analysis → AI-friendly reports
- **Languages**: Python, JS/TS (including TSX), Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON
- **Distribution**: skill (`skills/repomap/`) + CLI binary (`repomap`)
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
| `routes [--json] [--with-consumers]` | HTTP/API route inventory + consumer mapping |
| `state-map --symbol <name>` | Enum/const state values, writers, and readers |
| `orphan [--json]` | Dead-code candidate discovery |
| `hotspots` | Dense-file inventory |
| `cache save` / `diff` | Graph baseline + comparison |
| `lsp setup` | Auto-install LSP servers for detected languages (supports 13 languages) |
| `doctor` | Validate runtime + check LSP availability with `--lsp` |

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
├── __init__.py            # Core data structures: Symbol, Edge, RepoGraph, ScanStats + orjson compat layer
├── cli/                   # CLI entrypoint
│   ├── __init__.py
│   ├── __main__.py        # python -m repomap entry
│   ├── cli.py             # argparse CLI, dispatch, core constants (~410 lines)
│   └── handlers.py        # All run_* command implementations + shared helpers (~2450 lines)
├── gitignore.py            # GitignoreParser: pathspec-based file filtering
├── git_backend.py          # GitBackend: unified git operations (pygit2 priority, subprocess fallback)
├── core.py                # RepoMapEngine: scan pipeline, graph build
├── parser.py              # TreeSitterAdapter: AST parsing, import/export bindings
├── resolver.py            # ImportResolver: resolve imports to file paths
├── ranking.py             # EdgeBuilder, GraphAnalyzer: PageRank, call-graph edges
├── callgraph.py           # Multi-language precise call graph (Python ast + TS/Go/Rust tree-sitter)
├── type_inference.py      # Multi-language type annotation extraction (10 languages)
├── search.py              # BM25 symbol search index (rank-bm25 with keyword fallback)
├── topic.py               # Topic scoring, test matching, file role classification
├── check.py               # RepoMapChecker: diagnostics (eslint, tsc, ruff, go vet, ...)
├── toolkit.py             # Cache/diff/git helper logic
├── ai.py                  # Markdown report rendering (overview, impact, verify, query)
├── consumers.py            # HTTP route consumer detection (frontend/test → API mapping)
├── state_map.py            # Enum/const state definition discovery and analysis
└── lsp.py                 # Optional local LSP integration (stdio, on-demand)
skills/repomap/            # AI agent skill definition
├── SKILL.md               # Agent decision procedure
└── references/            # Command map, prompt examples, authoring checklist
tests/                     # Test suite
├── test_git_backend.py    # GitBackend unit tests (61 cases)
├── test_callgraph.py      # Call graph unit tests (45 cases)
├── test_type_inference.py # Type inference unit tests (33 cases)
└── ...                    # Other test files
dist/repomap               # Local build output (CI builds Linux/macOS/Windows via GitHub Actions)
```

**Dependency flow**: `cli.py` → `core.py` (engine) → `parser.py` (AST) → `resolver.py` (imports) → `ranking.py` (graph) → `ai.py` (reports). Cross-cutting: `__init__.py` (data types), `git_backend.py` (git ops), `callgraph.py` (precise call graph), `type_inference.py` (type extraction), `search.py` (BM25 search), `topic.py` (scoring), `check.py` (diagnostics), `toolkit.py` (cache/git).

## Change Map

- **Parser/AST**: `src/parser.py`, `src/resolver.py` → all symbol/call-chain commands
- **Graph/ranking**: `src/ranking.py` → `overview`, `call-chain`, `query-symbol`, `impact`, `hotspots`
- **Call graph**: `src/callgraph.py` → `call-chain` precise edges (Python ast + TS/Go/Rust tree-sitter)
- **Type inference**: `src/type_inference.py` → `query-symbol` return_type/params (10 languages)
- **Search**: `src/search.py` → `search` command (BM25 + keyword fallback)
- **Git backend**: `src/git_backend.py` → all git operations (pygit2 priority, subprocess fallback)
- **CLI/commands**: `src/cli/cli.py` (argparse + dispatch) + `src/cli/handlers.py` (run_* implementations) → add subparser in cli.py, implement handler in handlers.py, render via `src/ai.py`
- **Reports**: `src/ai.py` → each `render_*` function owns one report type
- **Topic scoring**: `src/topic.py` → `impact`, `verify`, `query` test suggestions
- **Diagnostics**: `src/check.py` → `check`, `verify`
- **Gitignore**: `src/gitignore.py` → file filtering (replaced hardcoded skip lists with pathspec)
- **Cache/diff**: `src/toolkit.py` → `cache save`, `diff`, `verify --with-diff`
- **Route consumers**: `src/consumers.py` → `routes --with-consumers`
- **State map**: `src/state_map.py` → `state-map --symbol/--query`
- **LSP**: `src/lsp.py` → opt-in, affects `query-symbol --with-lsp`, `file-detail --with-lsp`, `verify --with-lsp`, `check --with-lsp`, `doctor --lsp`, `lsp setup`

## Verification

| Scope | Command |
|---|---|
| Parser | `uv run python -m unittest discover -s tests -p 'test_repomap_parser_ast.py' -v` |
| CLI | `uv run python -m unittest discover -s tests -p 'test_repomap_cli.py' -v` |
| Engine | `uv run python -m unittest discover -s tests -p 'test_repomap_engine.py' -v` |
| Toolkit | `uv run python -m unittest discover -s tests -p 'test_repomap_toolkit.py' -v` |
| LSP | `uv run python -m unittest discover -s tests -p 'test_repomap_lsp.py' -v` |
| Git Backend | `uv run python -m pytest tests/test_git_backend.py -v` |
| Call Graph | `uv run python -m pytest tests/test_callgraph.py -v` |
| Type Inference | `uv run python -m pytest tests/test_type_inference.py -v` |
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
- Binary distribution is via CLI binary (`repomap`), not manual download.
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
- LSP is strictly opt-in, local-only. `lsp setup` suggests install commands per detected language but does not execute them without user consent.
- `verify` suggests tests but does not run them. Agents must run tests explicitly.
- Cache directories are keyed by canonical project path.
- `.gitignore` keeps `docs/` local-only (not in public repo).

## Agent Boundary Discovery

When using `repomap`, AI agents encounter tool boundaries that specs don't cover (language/framework gaps, performance boundaries, output precision issues, workflow friction). File these as GitHub Issues on the repository — do not log them in local files.

## Skill Distribution

The open-source skill (`skills/repomap/SKILL.md`) is distributed to users. The local copy (`~/.agents/skills/repomap/SKILL.md`) must be byte-identical to the open-source version. Neither may include:
- Any references to local file paths (e.g., absolute paths on maintainer's machine)
- Any maintainer-specific workflow or feedback mechanisms

## Release Rule（铁律）

**任何代码修改完成后，必须走完整发布流程。** 不允许只 commit 不发布。修改即发布，发布即完整（版本号 → binary → commit → push → CI → GitHub Release）。

## Post-Change Checklist

After any code change to `src/`, work through these steps. **Every step must complete before moving to the next. When a step depends on an external async process (CI), wait for completion automatically — poll every 60s with `gh run list`, do not ask the user to wait.**

```bash
# 1. Run ALL tests
uv run python -m unittest discover -s tests -v

# 2. Rebuild binary
uv run --with pyinstaller python -m src.cli build-binary --output dist

# 3. Smoke test (current project)
repomap doctor
repomap overview --project .

# 3.5. Smoke test with local projects (MANDATORY before release)
#    - Test in at least 2-3 local projects of different languages/frameworks
#    - Verify: overview, query, file-detail --with-lsp, call-chain, refs, verify --quick
#    - Verify: lsp setup --dry-run detects languages correctly
#    - Verify: doctor --lsp finds available servers

# 4. Evaluate: does SKILL.md need updating?
#    - New commands or changed options → update Command selection table
#    - Changed behavior → update Boundaries section
#    - New limitations discovered → update Boundaries section
#    See skills/repomap/SKILL.md

# 5. Evaluate: do ~/.A1/ai/AGENTS.md or ~/.claude/CLAUDE.md need updating?
#    - New repomap commands → update Section 8.1 command lists
#    - Changed distribution method → update availability description

# 6. Bump version in pyproject.toml

# 7. Commit + push → CI auto-builds binary
#    Commit message format: [release]: vX.Y.Z — English summary of primary change

# 8. Wait for CI to complete
#     - Poll CI status: gh run list --repo gjczone/repomap --branch main --limit 1 --json status,conclusion
#     - Wait up to 10 minutes; CI typically takes 3-6 minutes

# 9. Create GitHub Release — bilingual: English section first, Chinese section second, separated by ---
```

## Distribution Policies

- **No binaries in git**: `dist/repomap` is gitignored. Build artifacts stay local.
- **GitHub Releases**: Text-only bilingual (CN+EN) changelogs. No binary attachments. Created via `gh release create` with `--notes`.
- **Version bump**: When bumping version, update `pyproject.toml`.
- **CI build**: CI builds the binary and runs tests. Auto-wait for CI: poll `gh run list --repo gjczone/repomap --branch main --limit 1` every 60s until `status=completed`; check `conclusion=success` before release.

## Release Automation Rules

When the user asks to release a new version, follow this automated flow. **No manual checkpoints — every step completes before the next begins. If a step requires waiting (CI), poll automatically and report progress.**

### Release Quality Gates
- Treat release as a full contract, not a version bump: local tests, rebuilt binary, CI, and GitHub Release must all be verified.
- Keep one version chain: Python package version in `pyproject.toml` is the single source of truth.
- Update the source of truth when behavior or distribution changes: workflow files, README files, CLAUDE.md, and SKILL.md must not describe conflicting release paths.
- Treat `skipped`, `unknown`, and missing diagnostics as incomplete evidence. Explain why they are expected and cover the gap with real tests, binary smoke tests, or CI evidence.
- For bug fixes, add or update a regression test that would have failed before the fix unless the change is documentation-only.
- Before push or release, confirm clean git status, target remote, branch, tag target commit, and CI target branch.

### Version Decision
- **docs/README changes only**: patch bump (x.y.Z)
- **New feature or behavior enhancement**: minor bump (x.Y.z)
- **Breaking changes or major rework**: major bump (X.y.z)
- When in doubt, ask the user; default to minor for feature work.

### Commit Message
- Format: `[release]: vX.Y.Z — English summary of primary change`
- The description should capture the primary change in English (5-10 words).
- Example: `[release]: v2.3.0 — LSP 13 languages + gitignore parsing + search format`
- Type tag `[release]` is always in English; the summary after `—` is also in English.

### Pre-Release Testing
- **MANDATORY**: smoke test with at least 2-3 local projects before any release commit
- Test in projects with different language mixes (pure Python, TS+Python, etc.)
- Key commands to verify: `overview`, `query`, `file-detail --with-lsp`, `call-chain`, `refs`, `verify --quick`, `doctor --lsp`, `lsp setup --dry-run`
- If any command fails, fix before proceeding with the release
- If any test, diagnostic, or verify step is skipped/unknown, document why it is acceptable and which non-skipped evidence covers the same risk.

### GitHub Release Format
- **Bilingual, independent sections**: English first, then Chinese. Two complete, independent sections separated by `---`. Do NOT interleave languages within sections.
- **Text-only**: No binary attachments. The release notes are the changelog.
- **Structure**:
  ```
  ## What's New
  (English content — complete paragraphs, no Chinese)

  ### Feature Group 1
  (English description)

  ### Feature Group 2
  (English description)

  ## Changes
  (English file list)

  ---

  ## 更新内容
  (中文内容 — 完整段落，无英文)

  ### 功能分组 1
  (中文描述)

  ### 功能分组 2
  (中文描述)

  ## 变更文件
  (中文文件列表)
  ```
- **Never use inline bilingual format** like `## What's New / 更新内容` or `English text / 中文文本`. Each language section stands alone.

### CI Wait Protocol
1. After `git push`, immediately poll CI status.
2. If CI is `in_progress` or `queued`: wait 60s, poll again. Report "CI 运行中…" to the user.
3. If CI completes but `conclusion=failure`: report the failure and stop.
4. If CI completes and `conclusion=success`: proceed to GitHub Release.

### Completion Report
After release is fully done, report:
- Git tag and commit hash
- GitHub Release URL
- CI run URL

