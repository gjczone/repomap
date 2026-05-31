# RepoMap — Skill + CLI for AI-Agent Repository Intelligence

> **用 repomap 查 repomap，用 repomap 优化 repomap。** 开发和审查本项目时，必须使用 repomap 自身的命令（`overview`、`impact`、`call-chain`、`verify`、`check`）来理解代码、评估变更影响、发现死代码和验证修改。这是 dogfooding 原则——自己吃自己的狗粮。

`repomap` is a **skill + CLI tool**. AI agents (Claude Code, Codex, OpenCode) invoke it via the skill definition in `skills/repomap/SKILL.md`. The skill tells the agent _when_ to call `repomap`; the CLI binary does the actual work: tree-sitter AST scanning, dependency graph building, PageRank ranking, and structured report generation.

**Distribution**: Pure Python skill+CLI tool. Distributed via skill definition (`skills/repomap/`) and CLI binary (`repomap`). Version managed in `pyproject.toml`.

## Project Snapshot

- **Shape**: Python package (`src/`) with CLI binary
- **Core capability**: tree-sitter AST → symbol extraction → import resolution → call-chain analysis → AI-friendly reports
- **Parsing languages**: Python, JS/TS/TSX, Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, Lua, HTML, CSS, JSON, YAML, Bash
- **Type inference**: Python, TS/TSX, Go, Rust, Java, Kotlin, Swift, C#, C++, PHP (11 languages)
- **Distribution**: skill (`skills/repomap/`) + CLI binary (`repomap`)
- **No server/daemon**: LSP integration is opt-in, local-only, stdio-based

## LLM-First Design

**repomap is a pure LLM-consumed tool. Humans do not interact with it directly.** Every design decision — CLI argument defaults, output format, command naming, hint text, error messages — must optimize for LLM comprehension speed and token efficiency, not human readability.

- `--json` defaults to `True` because LLMs consume structured output more efficiently than prose.
- Command names are chosen for disambiguation in LLM reasoning, not for human CLI conventions.
- All output goes through `json_envelope()` for consistent `{schema_version, command, project, status, result}` structure.
- Hint text (stderr) guides LLM next-step decision; **every hint must reference only existing commands**.

## Commands

All via `repomap <subcommand> [--project <path>]`.

| Command                                | Purpose                                                                  |
| -------------------------------------- | ------------------------------------------------------------------------ |
| `overview`                             | Project map: modules, entry points, reading order, hotspots, key symbols |
| `query --query "keyword"`              | Topic/feature discovery with adaptive fallback (never empty)             |
| `query --symbol <name>`                | Exact/fuzzy symbol lookup + state map for enums + references             |
| `query --search "text"`                | BM25 semantic symbol search with keyword fallback                        |
| `query --file <f>`                     | Symbols and structure of a known file                                    |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius + edit planning; `--compact` concise; `--top-n <N>` |
| `call-chain --symbol <name>`           | Caller/callee context + references                                       |
| `affected --files <f...>`              | CI test discovery: which test files are affected by source changes        |
| `verify [--quick] [--no-diff]`         | Post-edit evidence gate + orphan symbols + graph diff; `--risk-threshold HIGH\|MED\|LOW` |
| `check`                                | Compiler/type/lint diagnostics                                           |
| `routes [--with-consumers]`             | HTTP/API route inventory + consumer mapping                              |
| `cache save`                           | Graph baseline save for diff comparison                                  |
| `lsp setup`                            | Auto-install LSP servers for detected languages                          |
| `doctor [--no-lsp]`                    | Validate runtime + LSP status (default)                                  |
| `fix [--dry-run]`                      | Auto-fix: ruff --fix + eslint --fix                                      |
| `ready`                                | Pre-commit readiness check (verify + check + format)                     |

```bash
# Run from source
uv run repomap --help

# Tests
uv run python -m unittest discover -s tests -v

# Build binary
uv run --with pyinstaller python -m PyInstaller \
  --onefile --name repomap src/cli/__main__.py \
  --hidden-import tree_sitter \
  --hidden-import tree_sitter_python \
  --hidden-import tree_sitter_javascript \
  --hidden-import tree_sitter_typescript \
  --hidden-import tree_sitter_go \
  --hidden-import tree_sitter_rust \
  --hidden-import tree_sitter_html \
  --hidden-import tree_sitter_css \
  --hidden-import tree_sitter_json \
  --hidden-import tree_sitter_c \
  --hidden-import tree_sitter_java \
  --hidden-import tree_sitter_kotlin \
  --hidden-import tree_sitter_swift \
  --hidden-import tree_sitter_cpp \
  --hidden-import tree_sitter_c_sharp \
  --hidden-import tree_sitter_php \
  --hidden-import tree_sitter_ruby \
  --hidden-import repomap_lsp
```

## Architecture

```
src/                    # Python package (flat)
├── __init__.py            # Core data structures: Symbol, Edge, RepoGraph, ScanStats + orjson compat layer
├── cli/                   # CLI entrypoint
│   ├── __init__.py
│   ├── __main__.py        # python -m repomap entry
│   ├── cli.py             # argparse CLI, dispatch, core constants (~780 lines)
│   ├── handlers.py         # Shared helpers: constants, scan engine, session cache, symbol resolution (~710 lines)
│   └── commands/           # Per-command-group implementations (~3400 lines)
│       ├── overview.py     # run_overview, run_scan
│       ├── query.py        # run_query, run_search
│       ├── symbol.py       # run_call_chain, run_query_symbol, run_file_detail
│       ├── impact.py       # run_impact + edit-planning helpers
│       ├── verify.py       # run_verify, run_check + evidence-gate + orphan helpers
│       ├── cache.py        # run_cache, run_diff
│       ├── affected.py     # run_affected — CI test discovery from source changes
│       ├── routes.py       # run_routes
│       ├── fix.py          # run_fix, run_ready
│       └── doctor.py       # run_doctor, run_lsp_doctor, run_lsp_setup
├── hints.py                 # Runtime hints: context-aware next-step suggestions
├── git_backend.py          # GitBackend: unified git operations (pygit2 priority, subprocess fallback)
├── core.py                # RepoMapEngine: scan pipeline, graph build
├── parser.py              # TreeSitterAdapter: AST parsing, import/export bindings
├── resolver.py            # ImportResolver: resolve imports to file paths
├── ranking.py             # EdgeBuilder, GraphAnalyzer: PageRank, call-graph edges
├── callgraph.py           # Multi-language precise call graph (Python ast + TS/Go/Rust tree-sitter)
├── type_inference.py      # Multi-language type annotation extraction (11 languages)
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
tests/                     # Test suite
├── test_git_backend.py    # GitBackend unit tests (61 cases)
├── test_callgraph.py      # Call graph unit tests (45 cases)
├── test_type_inference.py # Type inference unit tests (33 cases)
└── ...                    # Other test files
dist/repomap               # Local build output (CI builds Linux x64 only via GitHub Actions)
```

**Dependency flow**: `cli.py` → `core.py` (engine) → `parser.py` (AST) → `resolver.py` (imports) → `ranking.py` (graph) → `ai.py` (reports). Cross-cutting: `__init__.py` (data types), `git_backend.py` (git ops), `callgraph.py` (precise call graph), `type_inference.py` (type extraction), `search.py` (BM25 search), `topic.py` (scoring), `check.py` (diagnostics), `toolkit.py` (cache/git), `hints.py` (runtime hints).

## Change Map

- **Parser/AST**: `src/parser.py`, `src/resolver.py` → all symbol/call-chain commands
- **Graph/ranking**: `src/ranking.py` → `overview`, `call-chain`, `query --symbol`, `impact`
- **Call graph**: `src/callgraph.py` → `call-chain` precise edges (Python ast + TS/Go/Rust tree-sitter + JSX component detection + Rust trait default methods)
- **Type inference**: `src/type_inference.py` → `query --symbol` return_type/params (11 languages)
- **Search**: `src/search.py` → `query --search` (BM25 + keyword fallback)
- **Git backend**: `src/git_backend.py` → all git operations (pygit2 priority, subprocess fallback)
- **Affected files**: `src/cli/commands/affected.py` → `affected` — CI test discovery from source changes
- **CLI/commands**: `src/cli/cli.py` (argparse + dispatch), `src/cli/handlers.py` (shared helpers), `src/cli/commands/*.py` (run\_\* implementations) → add subparser in cli.py, implement handler in commands/<group>.py, render via `src/ai.py`
- **Hints**: `src/hints.py` → runtime next-step suggestions appended to text output via stderr (not JSON)
- **Reports**: `src/ai.py` → each `render_*` function owns one report type
- **Topic scoring**: `src/topic.py` → `impact`, `verify`, `query` test suggestions
- **Diagnostics**: `src/check.py` → `check`, `verify`
- **Gitignore**: `src/gitignore.py` → file filtering (replaced hardcoded skip lists with pathspec)
- **Cache/diff**: `src/toolkit.py` → `cache save`, `verify` (graph diff)
- **Route consumers**: `src/consumers.py` → `routes --with-consumers`
- **State map**: `src/state_map.py` → integrated into `query --symbol` for enum/const symbols
- **LSP**: `src/lsp.py` → auto-enabled, affects `query --symbol`, `query --file`, `verify`, `check`, `doctor`, `lsp setup`; per-language timeout via `lsp_timeout_for()`
- **Call-graph consistency**: `src/cli/commands/verify.py` → `verify` broken call/import edges
- **Call budget**: `src/hints.py` → `query_budget_hint()` outputs tip when queries exceed threshold
- **JSON output**: `src/cli/handlers.py::json_envelope()` → unified `{schema_version, command, project, status, result}` envelope; all commands support `--json`

## Verification

| Scope          | Command                                                                                                                                                                     |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Parser         | `uv run python -m unittest discover -s tests -p 'test_repomap_parser_ast.py' -v`                                                                                            |
| CLI            | `uv run python -m unittest discover -s tests -p 'test_repomap_cli.py' -v`                                                                                                   |
| Engine         | `uv run python -m unittest discover -s tests -p 'test_repomap_engine.py' -v`                                                                                                |
| Toolkit        | `uv run python -m unittest discover -s tests -p 'test_repomap_toolkit.py' -v`                                                                                               |
| LSP            | `uv run python -m unittest discover -s tests -p 'test_repomap_lsp.py' -v`                                                                                                   |
| Git Backend    | `uv run python -m pytest tests/test_git_backend.py -v`                                                                                                                      |
| Call Graph     | `uv run python -m pytest tests/test_callgraph.py -v`                                                                                                                        |
| Type Inference | `uv run python -m pytest tests/test_type_inference.py -v`                                                                                                                   |
| Binary E2E     | `uv run --with pyinstaller python -m unittest discover -s tests -p 'test_repomap_binary_e2e.py' -v`                                                                         |
| Full           | `uv run python -m unittest discover -s tests -v && uv run --with pytest python -m pytest tests/test_git_backend.py tests/test_callgraph.py tests/test_type_inference.py -q` |
| Smoke          | `repomap doctor --project . && repomap overview --project . && repomap verify --project . --quick`                                                                          |
| Typecheck      | `uv run mypy src/ --ignore-missing-imports --no-error-summary`                                                                                                              |

## README Maintenance

The public README files serve different audiences than this document:

- **README.md** (English, primary): user-facing — what, how to install, how to use. Keep concise, answer "what/why/how" within 10 seconds.
- **README.zh-CN.md** (Chinese): same content and structure — not literal translation but content-equivalent. All sections, commands, tables must exist in both.
- **SKILL.md** (`skills/repomap/SKILL.md`): AI agent operating procedure. Describes _when and how_ to call each command. Distributed to users.

**Rules for README changes**:

- README describes the _product_, not the implementation. No module names, internal architecture, or refactoring details.
- Binary distribution is via CLI binary (`repomap`), not manual download.
- Language support list must match `src/parser.py` and `pyproject.toml`.
- When adding commands, update README.md, README.zh-CN.md, and SKILL.md.
- README does NOT say "For Humans" / "For AI Agents" — present information directly.

<general-project-rules>

# General Project Rules

## Coding Rules
- **Docs sync is blocking**: after any behavior change, API change, command add/rename/delete, or config change, you MUST review these files and update every one that is stale — BEFORE committing:
  - `skills/repomap/SKILL.md` — agent decision procedure
  - `~/.agents/skills/repomap/SKILL.md` — local copy, must be byte-identical to the open-source version
  - `README.md` + `README.zh-CN.md` — user-facing docs
  - `AGENTS.md` (symlink to `CLAUDE.md`) — project rules and architecture
  Stale docs are P1 bugs. Do not defer doc updates to a follow-up PR.

- Source ownership: `src/cli/cli.py` owns all argparse definitions and subcommand dispatch.
- Data structures: `src/__init__.py` is single source of truth for Symbol, Edge, RepoGraph, ScanStats, HttpRoute.
- Rendering: report rendering stays in `src/ai.py`; engine/parser/ranking produce data, `ai.py` formats it.
- Import resolution: goes through `src/resolver.py`; do not hand-roll path resolution.
- LSP: strictly opt-in, local-only. `lsp setup` suggests install commands per detected language but does not execute them without user consent.
- Resource limits: every cache, file read, loop, or collection must have an explicit upper bound (max size, max entries, timeout). Unbounded growth is a memory-leak bug.
- Error visibility: `.decode("utf-8")` must use `errors="replace"`; bare `except:` or `except Exception: pass` must be justified in a comment. Silent error swallowing hides real bugs.
- Comment accuracy: comments describing behavior ("atomic commit", "max_count", "dry-run") must match the actual implementation. A misleading comment is worse than no comment.

## Testing Rules

- Use TDD for bug fixes and behavior changes: write or identify the smallest failing test first, then make the minimal code change.
- Run the focused test before the full suite; when behavior contracts change, update affected assertions, fixtures, mocks, and snapshots.
- Regression tests: for every P0/P1 bug fixed, add or update a test that would have caught it before the fix.
- mypy typecheck is **blocking** in CI (`uv run mypy src/ --ignore-missing-imports --no-error-summary`). PRs that introduce new mypy errors will fail CI. Run mypy locally before committing changes to `src/`.
- **mypy strict 迁移路径**: 当前使用 `--ignore-missing-imports`，远期目标逐步收紧 → `--strict`。阶段：(1) 当前：基本通过 (2) 下一步：移除 `--ignore-missing-imports`，为所有可选依赖添加 `type: ignore[import-untyped]` (3) 远期：启用 `--strict`。
- **ruff type-checking**: ruff 目前仅有 annotation 存在性检查（ANN 规则），不支持类型推断。ruff 比 mypy 快 10-100x 但不替代 mypy。可考虑开启 ANN 规则作为补充。

## Language Integration Test Strategy

**现有语言测试覆盖（8种）**：Python, TypeScript, Go, Rust, Java, Kotlin, Swift, C#, C++

**优先级**：

1. **第一优先级**：Python, Rust, TypeScript — 核心使用场景，必须保证稳定
2. **第二优先级**：Go, Java, C#, C++ — 常见企业语言
3. **第三优先级**：Kotlin, Swift — 可选依赖，跳过测试已通过

**暂不扩展**：不为 C, JavaScript, PHP, Ruby, HTML, CSS, JSON 添加新的集成测试。现有覆盖已足够，待用户量增长后再评估。

**性能基准测试**：暂不需要。当前无超大项目场景，用户量较少。待项目获得较多 star/用户后再考虑添加。

## API / CLI Rules

- `--project` must be absolute when called from AI/Agent contexts.
- All commands should support `--json` for machine-parseable output; use `json_envelope()` from `src/cli/handlers.py` for consistent `{schema_version, command, project, status, result}` format.
- `verify` suggests tests but does not run them. Agents must run tests explicitly.
- Session cache version in `src/cli/cli.py` must be bumped when scan cache semantics change.
- **Local binary only — do not `npm install -g repomap-bin` on developer machines.** The `repomap` command is a symlink to `dist/repomap` (local build). npm package `repomap-bin` is for cloud/CI environments only. During release, verify npm version is published and matches `pyproject.toml` version: `npm view repomap-bin version`.
- **Hints synchronization**: when a command is added, renamed, or removed, `src/hints.py` MUST be updated in the same change. Each hint function must only reference currently valid commands. A stale hint is a P1 bug — LLMs follow them blindly.

## Data & State Rules

- Cache directories are keyed by canonical project path.
- `.gitignore` keeps `docs/` local-only (not in public repo).

## Verification Before Completion

- After any code change to `src/`, run the full test suite: `uv run python -m unittest discover -s tests -v && uv run --with pytest python -m pytest tests/test_git_backend.py tests/test_callgraph.py tests/test_type_inference.py -q`.
- Run `repomap verify --project .` after non-trivial edits; treat `SKIPPED` or `unknown` diagnostics as incomplete evidence.
- Before claiming completion, confirm the exact command and result of the most relevant check for the changed area.

## Project-Specific Rules

- Resolver fall-through is correct: when import binding resolution fails in `src/resolver.py`, falling through to global symbol matching is intentional — do not flag this as a bug (ref: B1 false positive, round 6).
- Swift query warnings: `struct_declaration` is not a valid node type in tree-sitter-swift grammar — the `[WARNING] Query compile failed [swift/class]` log line is expected and can be ignored.
- Git porcelain format: both `"XY path"` (2-char status) and `"X path"` (1-char status) variants appear in real git output — do not simplify `_parse_git_status_porcelain_paths` to expect only one format.
- verify --quick exit code: returning exit code 3 (EXIT_NO_RESULTS) when no git changes are detected is design behavior, not a bug. The WARNING status means "cannot assess risk without changes."
- CI uv.lock variability: `uv.lock` may be auto-modified by CI during `uv run` / `uv pip install`. In the CI smoke test, `verify --quick` may return PASS or WARNING depending on whether `uv.lock` was modified — either outcome is acceptable.

### Code Review Rules (based on 15+ rounds of deep review)

**Review History**:

15+ rounds of deep review across ~130 issues since project inception. The pattern is well-established:

- **Rounds 1–3** (#5, #31, #33): high P0/P1 density — shell injection, silent swallowing, LSP transport bugs, 370+ dead lines
- **Rounds 4–7** (#36–#46): systemic weaknesses surface — cache eviction, resource caps, LLM interaction boundaries
- **Rounds 8+** (#115, #117, #131): single-point P0/P1 rare; findings shift to design coherence (command UX, hints drift, output format consistency, stale documentation)

Representative issues: #5 (first structured review), #31 (shell injection), #33 (7 P0), #46 (LLM boundaries), #115 (callgraph precision), #131 (LLM-first UX).

**When to review**: After every non-trivial code change, before merge. Scope = changed files + files reported by `impact --files`.

**Agent configuration**:

- 1–2 files → 1 agent, 3–10 files → 2–3 agents, 10+ files → 4–5 agents
- Always include an Integrity Audit agent (detects code that looks correct but is actually broken)
- Assign non-overlapping review dimensions (Bug Hunter / Integrity Audit / Anti-Bloat) to reduce false positives
- Each agent prompt must state: scope + direction + output format, in ≤3 sentences

**Priority classification**:

- P0 (fix immediately): security vulnerabilities, data loss, crashes, broken contracts
- P1 (fix now): real bugs, clear functional defects
- P2 (fix if time): code quality issues, small optimizations
- P3 (file follow-up issue): too large for one PR, needs independent design

**Known false-positive patterns (skip during review)**:

- `src/resolver.py` import resolution fall-through → intentional, not a bug (ref: #46 B1 false positive)
- Swift/Kotlin query compile warnings → tree-sitter grammar limitation, expected
- `except Exception` in top-level CLI handlers → intentional crash guard
- `_deprecated_*` prefixed unused variables → kept for backward compatibility
- Pyright `reportAttributeAccessIssue` on dynamic attributes → correct at runtime
- `repomap verify --quick` exit code 3 on no git changes → WARNING is "cannot assess risk without changes" (ref: B1, round 9)
- Swift `struct_declaration` query warnings → tree-sitter-swift grammar bug, not repomap bug
- Git porcelain 1-char + 2-char status formats both appear in real output → don't simplify parser
- `uv.lock` auto-modified by CI → `verify --quick` may return PASS or WARNING; both acceptable

**Systemic weaknesses (high-recurrence areas — check every review)**:

- Silent error swallowing: 15+ historical sites of `except Exception: return []/None/{}` — prevents crashes at the cost of debuggability
- Unbounded caches: gitignore / topic co-change / LSP notifications caches have no eviction
- Missing resource caps: file reads, AST walks, rglob, git history queries lack upper bounds
- Security gaps: shell injection (#31), argument injection, lax path validation concentrated in CLI layer
- **Hints drift**: when commands are added/renamed/removed, `src/hints.py` consistently lags behind — stale hints are P1 bugs because LLMs follow them blindly
- **LSP timeout inflexibility**: single `DEFAULT_LSP_TIMEOUT = 8.0` inadequate for heavy servers (rust-analyzer needs 15-30s)
- **Session cache persistence**: cached `size` field not persisted caused total restore failure (#58); cache format changes need version bump
- **JSON output contract drift**: `--json` output occasionally mixed with text (#60); every command must go through `json_envelope()`

**Fix discipline**:

- Fixes from review rounds introduce new bugs at ~5–10% rate; always run regression tests after fixing
- Fixes touching `resolver`/`lsp`/`git` core paths carry the highest risk — validate extra carefully
- Do not over-engineer a "fix" — if the original code is correct but improvable, file as P3, do not change it now
- Fix P0/P1 items one at a time in priority order; verify each before moving to the next; never batch-fix
- **When deleting/renaming commands**: grep for references in `src/hints.py`, `CLAUDE.md`, `SKILL.md`, `README*.md`, and `.github/workflows/*.yml` before committing
- **Cache format changes**: bump session cache version in `src/cli/cli.py` and test restore from old format

**Diminishing returns**:

- Rounds 1–3: find ~80% of bugs, P0/P1 dense
- Rounds 4–5: find ~15%, systemic weaknesses (not single-point bugs) dominate
- Rounds 6–7: find ~5%, false-positive rate rises to 10–15%
- Rounds 8+: single-point P0/P1 become rare; findings shift to "design coherence" (command naming, LLM UX, output format consistency, stale hints)
- Rounds 12+: mostly P2/P3 code organization and test coverage gaps
- After 4+ rounds with zero P0/P1, shift effort to writing regression tests rather than chasing diminishing P2/P3

## Agent Boundary Discovery

When using `repomap`, AI agents encounter tool boundaries that specs don't cover (language/framework gaps, performance boundaries, output precision issues, workflow friction). File these as GitHub Issues on the repository — do not log them in local files.

## Skill Distribution

The open-source skill (`skills/repomap/SKILL.md`) is distributed to users. The local copy (`~/.agents/skills/repomap/SKILL.md`) must be byte-identical to the open-source version. The skill is a single file — no subdirectories. Neither may include:

- Any references to local file paths (e.g., absolute paths on maintainer's machine)
- Any maintainer-specific workflow or feedback mechanisms

## Release Rule

**Every code change MUST complete the full release pipeline.** No commit without release. Every change ships: version bump → binary rebuild → commit → push → CI → GitHub Release.

## Post-Change Checklist

After any code change to `src/`, work through these steps. **Every step must complete before moving to the next. When a step depends on an external async process (CI), wait for completion automatically — poll every 60s with `gh run list`, do not ask the user to wait.**

```bash

# 1. Run ALL tests (core unittest + new pytest-based tests)
uv run python -m unittest discover -s tests -v
uv run --with pytest python -m pytest tests/test_git_backend.py tests/test_callgraph.py tests/test_type_inference.py -q

# 2. Rebuild binary
uv run --with pyinstaller python -m PyInstaller \
  --onefile --name repomap src/cli/__main__.py \
  --hidden-import tree_sitter \
  --hidden-import tree_sitter_python \
  --hidden-import tree_sitter_javascript \
  --hidden-import tree_sitter_typescript \
  --hidden-import tree_sitter_go \
  --hidden-import tree_sitter_rust \
  --hidden-import tree_sitter_html \
  --hidden-import tree_sitter_css \
  --hidden-import tree_sitter_json \
  --hidden-import tree_sitter_c \
  --hidden-import tree_sitter_java \
  --hidden-import tree_sitter_kotlin \
  --hidden-import tree_sitter_swift \
  --hidden-import tree_sitter_cpp \
  --hidden-import tree_sitter_c_sharp \
  --hidden-import tree_sitter_php \
  --hidden-import tree_sitter_ruby \
  --hidden-import repomap_lsp
# 2.5. Verify binary version matches pyproject.toml (MANDATORY)
#    - If version is wrong, npm will publish a broken package that cannot be overwritten
#    EXPECTED=$(grep '^version = ' pyproject.toml | head -1 | sed 's/version = "\(.*\)"/\1/')
#    ACTUAL=$(./dist/repomap --version | sed 's/repomap //')
#    [ "$EXPECTED" = "$ACTUAL" ] || { echo "VERSION MISMATCH: expected=$EXPECTED actual=$ACTUAL"; exit 1; }

# 3. Smoke test (current project)
repomap doctor --project .
repomap overview --project .

# 3.5. Smoke test with local projects (MANDATORY before release)
#    - Verify: overview, query --file, call-chain, affected, verify --quick
#    - Verify: lsp setup --dry-run detects languages correctly
#    - Verify: lsp doctor finds available servers

# 4. Evaluate: do SKILL.md, README.md, README.zh-CN.md, or CLAUDE.md (AGENTS.md symlink) need updating?
#    - New/changed/removed commands → update command table, docs, and Change Map in all files
#    - Changed behavior → update relevant sections in all files
#    - New limitations discovered → update Boundaries section in SKILL.md
#    See skills/repomap/SKILL.md
#
# 4.5. Sync local skill directory to ~/.agents/skills/repomap/
#      cp skills/repomap/SKILL.md ~/.agents/skills/repomap/SKILL.md

# 6. Bump version in pyproject.toml

# 7. Commit + push → CI auto-builds binary
#    Commit message format: [release]: vX.Y.Z — English summary of primary change

# 8. Wait for CI to complete
#     - Poll CI status: gh run list --repo gjczone/repomap --branch main --limit 1 --json status,conclusion
#     - Wait up to 10 minutes; CI typically takes 3-6 minutes

# 9. Create GitHub Release page:
#     gh release create "v$(grep '^version = ' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')" \
#       --title "v$(grep '^version = ' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')" \
#       --notes "$(cat <<'RELEASE_NOTES'
#       ## What's New
#       ...
#       ---
#       ## 更新内容
#       ...
#       RELEASE_NOTES
#       )"

# 10. Verify npm version matches pyproject.toml
#     - npm version is auto-published by CI; verify it's correct:
#       npm view repomap-bin version
#     - Compare with pyproject.toml version; they MUST match
#     - npm package is for cloud/CI environments only; local dev uses symlink to dist/repomap
```

## Distribution Policies

- **No binaries in git**: `dist/repomap` and `npm/platforms/*/repomap*` are gitignored. Build artifacts stay local.
- **npm distribution**: `npm install -g repomap-bin` (Linux x64 only). `repomap-bin` is the wrapper package (contains `repomap.js` shim), pulls `@gjczone/repomap-linux-x64` binary via `optionalDependencies`. Windows/macOS users build from source (see README). Wrapper is published from the Linux CI job only. All package versions are auto-synced from `pyproject.toml` by CI.
- **GitHub Releases**: Text-only bilingual (EN + CN) changelogs. No binary attachments. Created via `gh release create` with `--notes`. The release page is the public changelog — every release MUST have one.
- **Version bump**: When bumping version, update `pyproject.toml` and nothing else. CI auto-syncs all other version numbers.
- **CI build**: CI builds the Linux x64 binary and runs tests. Auto-wait for CI: poll `gh run list --repo gjczone/repomap --branch main --limit 1` every 60s until `status=completed`; check `conclusion=success` before release.

## Release Automation Rules

When the user asks to release a new version, follow this automated flow. **No manual checkpoints — every step completes before the next begins. If a step requires waiting (CI), poll automatically and report progress.**

### Release Quality Gates

- Treat release as a full contract, not a version bump: local tests, rebuilt binary, CI, GitHub Release page, and npm publish must all be verified.
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

### Version Alignment

**`pyproject.toml` is the single source of truth for version numbers.** The commit message, git tag, GitHub Release tag, and npm publish version MUST all match the `version` field in `pyproject.toml` exactly.

- When bumping, **only change `pyproject.toml`** — everything else is auto-synced by CI
- The version in the commit message MUST match `pyproject.toml`
- The git tag used for GitHub Release MUST match `pyproject.toml`
- **Never** allow a mismatch like commit message says v2.6.0 but `pyproject.toml` says 2.4.6
- Before pushing, run `grep '^version = ' pyproject.toml` to confirm the current version, then write the commit message accordingly

### Commit Message

- Format: `[release]: vX.Y.Z — English summary of primary change`
- Version number in message MUST match `pyproject.toml` version exactly.
- The description should capture the primary change in English (5-10 words).
- Example: `[release]: v2.3.0 — LSP 13 languages + gitignore parsing + search format`
- Type tag `[release]` is always in English; the summary after `—` is also in English.

### Pre-Release Testing

- **MANDATORY**: smoke test with at least 2-3 local projects before any release commit
- Test in projects with different language mixes (pure Python, TS+Python, etc.)
- Key commands to verify: `overview`, `query --file`, `call-chain`, `affected`, `verify --quick`, `lsp doctor`, `lsp setup --dry-run`
- If any command fails, fix before proceeding with the release
- If any test, diagnostic, or verify step is skipped/unknown, document why it is acceptable and which non-skipped evidence covers the same risk.

### GitHub Release Page

Every release MUST create a GitHub Release page. Use `gh release create`:

```bash
VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
gh release create "v$VERSION" \
  --title "v$VERSION" \
  --notes "$(cat <<'RELEASE_NOTES'
## What's New
...

---

## 更新内容
...
RELEASE_NOTES
)"
```

- The git tag name MUST be `v` + the exact version from `pyproject.toml` (e.g. `v2.6.0`)
- **Bilingual, independent sections**: English first, then Chinese. Two complete, independent sections separated by `---`. Do NOT interleave languages within sections.
- **Text-only**: No binary attachments. The release notes are the changelog.
- **Structure**:

  ```
  ## What's New
  (English content — complete paragraphs, no Chinese)

  ### Feature Group 1
  (English description)

  ## Changes
  (English file list)

  ---

  ## 更新内容
  (Chinese content — complete paragraphs, no English)

  ### 功能分组 1
  (Chinese description)

  ## 变更文件
  (Chinese file list)
  ```

- **Never use inline bilingual format** like `## What's New / 更新内容`. Each language section stands alone.

### CI Wait Protocol

1. After `git push`, immediately poll CI status.
2. If CI is `in_progress` or `queued`: wait 60s, poll again. Report "CI running…" to the user.
3. If CI completes but `conclusion=failure`: report the failure and stop.
4. If CI completes and `conclusion=success`: proceed to create the GitHub Release page.

### CI Breakage Prevention

**The following changes MUST be accompanied by CI config updates — otherwise CI WILL fail:**

1. **CLI argument changes** (new/removed required args, renamed args) → update smoke test commands in `.github/workflows/build-binaries.yml`
2. **New/removed Python dependencies** → update `uv run --with ...` argument lists in `.github/workflows/build-binaries.yml`
3. **`install.js` or npm `package.json` changes** → locally verify `npm install -g` produces an executable binary

**Pre-push checks (run before every commit):**

```bash
# 1. Confirm smoke test commands match current CLI signature
grep "repomap " .github/workflows/build-binaries.yml

# 2. Confirm no missing --project args (all commands except build-binary require --project)
grep -E "repomap (doctor|overview|verify|check|affected)" .github/workflows/build-binaries.yml | grep -v "\-\-project"

# 3. Confirm npm package names are correct (wrapper + 3 platform packages, all scoped)
grep '"name"' npm/wrapper/package.json npm/platforms/*/package.json
# Expected: wrapper -> repomap-bin, linux-x64 -> @gjczone/repomap-linux-x64, darwin-arm64 -> @gjczone/repomap-darwin-arm64, windows-x64 -> @gjczone/repomap-windows-x64
```

### npm Publishing Rules

- **Wrapper**: `repomap-bin` (`npm/wrapper/`) — entry point for `npm install -g repomap-bin`, contains `repomap.js` shim + `optionalDependencies`
- **Linux x64**: `@gjczone/repomap-linux-x64` (`npm/platforms/linux-x64/`)
- **macOS ARM64**: `@gjczone/repomap-darwin-arm64` (`npm/platforms/darwin-arm64/`)
- **Windows x64**: `@gjczone/repomap-windows-x64` (`npm/platforms/windows-x64/`)
- All three platform packages are scoped (require `--access public`); `os`/`cpu` fields ensure npm only installs the matching platform
- The wrapper declares all three platform packages via `optionalDependencies` — npm automatically installs only the one matching the current platform
- The wrapper is published only once, from the Linux CI job (not once per platform)
- All package versions are auto-synced from `pyproject.toml` by CI
- If a platform npm publish fails with "version already exists", check for upstream version conflicts; do NOT rely on `|| echo` to silently skip

### Completion Report

After release is fully done, report:

- Git tag and commit hash
- GitHub Release URL
- CI run URL
