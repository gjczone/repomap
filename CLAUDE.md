# RepoMap — Skill + CLI for AI-Agent Repository Intelligence

`repomap` is a **skill + CLI tool**. AI agents (Claude Code, Codex, OpenCode) invoke it via the skill definition in `skills/repomap/SKILL.md`. The skill tells the agent *when* to call `repomap`; the CLI binary does the actual work: tree-sitter AST scanning, dependency graph building, PageRank ranking, and structured report generation.

**Distribution**: Pure Python skill+CLI tool. Distributed via skill definition (`skills/repomap/`) and CLI binary (`repomap`). Version managed in `pyproject.toml`.

## Project Snapshot

- **Shape**: Python package (`src/`) with CLI binary
- **Core capability**: tree-sitter AST → symbol extraction → import resolution → call-chain analysis → AI-friendly reports
- **Languages**: Python, JS/TS (including TSX), Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, Lua, HTML, CSS, JSON, YAML, Bash
- **Distribution**: skill (`skills/repomap/`) + CLI binary (`repomap`)
- **No server/daemon**: LSP integration is opt-in, local-only, stdio-based

## Commands

All via `repomap <subcommand> --project <path>`.

| Command | Purpose |
|---|---|
| `overview` | Project map: modules, entry points, reading order, hotspots, key symbols |
| `query --query "keyword"` | Topic/feature discovery with adaptive fallback (never empty) |
| `search --query "text"` | BM25 semantic symbol search with keyword fallback |
| `file-detail --file-path <f>` | Symbols and structure of a known file |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius + edit planning |
| `query-symbol --symbol <name>` | Exact/fuzzy symbol lookup |
| `call-chain --symbol <name>` | Caller/callee context |
| `refs --symbol <name>` | Reference discovery |
| `verify [--quick] [--with-lsp] [--with-diff]` | Post-edit evidence gate with missed-files detection |
| `check` | Compiler/type/lint diagnostics |
| `routes [--json] [--with-consumers]` | HTTP/API route inventory + consumer mapping |
| `state-map --symbol <name>` | Enum/const state values, writers, and readers |
| `orphan [--json]` | Dead-code candidate discovery |
| `hotspots` | Dense-file inventory |
| `cache save` / `diff` | Graph baseline + comparison |
| `lsp setup` | Auto-install LSP servers for detected languages (supports 18 languages) |
| `doctor` | Validate runtime + check LSP availability with `--lsp` |
| `fix [--dry-run]` | Auto-fix: ruff --fix + eslint --fix |
| `ready` | Pre-commit readiness check (verify + check + format) |

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
│   ├── handlers.py         # Shared helpers: constants, scan engine, session cache, symbol resolution
│   └── commands/           # Per-command-group implementations (~2900 lines)
│       ├── overview.py     # run_overview, run_scan, run_hotspots
│       ├── query.py        # run_query, run_search
│       ├── symbol.py       # run_call_chain, run_refs, run_query_symbol, run_file_detail, run_state_map
│       ├── impact.py       # run_impact + edit-planning helpers
│       ├── verify.py       # run_verify, run_check, run_orphan + evidence-gate helpers
│       ├── cache.py        # run_cache, run_diff
│       ├── routes.py       # run_routes
│       ├── fix.py          # run_fix, run_ready
│       ├── doctor.py       # run_doctor, run_lsp_doctor, run_lsp_setup
│       └── build.py        # run_build_binary
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
tests/                     # Test suite
├── test_git_backend.py    # GitBackend unit tests (61 cases)
├── test_callgraph.py      # Call graph unit tests (45 cases)
├── test_type_inference.py # Type inference unit tests (33 cases)
└── ...                    # Other test files
dist/repomap               # Local build output (CI builds Linux x64 only via GitHub Actions)
```

**Dependency flow**: `cli.py` → `core.py` (engine) → `parser.py` (AST) → `resolver.py` (imports) → `ranking.py` (graph) → `ai.py` (reports). Cross-cutting: `__init__.py` (data types), `git_backend.py` (git ops), `callgraph.py` (precise call graph), `type_inference.py` (type extraction), `search.py` (BM25 search), `topic.py` (scoring), `check.py` (diagnostics), `toolkit.py` (cache/git).

## Change Map

- **Parser/AST**: `src/parser.py`, `src/resolver.py` → all symbol/call-chain commands
- **Graph/ranking**: `src/ranking.py` → `overview`, `call-chain`, `query-symbol`, `impact`, `hotspots`
- **Call graph**: `src/callgraph.py` → `call-chain` precise edges (Python ast + TS/Go/Rust tree-sitter)
- **Type inference**: `src/type_inference.py` → `query-symbol` return_type/params (10 languages)
- **Search**: `src/search.py` → `search` command (BM25 + keyword fallback)
- **Git backend**: `src/git_backend.py` → all git operations (pygit2 priority, subprocess fallback)
- **CLI/commands**: `src/cli/cli.py` (argparse + dispatch), `src/cli/handlers.py` (shared helpers), `src/cli/commands/*.py` (run_* implementations) → add subparser in cli.py, implement handler in commands/<group>.py, render via `src/ai.py`
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
| Full | `uv run python -m unittest discover -s tests -v && uv run --with pytest python -m pytest tests/test_git_backend.py tests/test_callgraph.py tests/test_type_inference.py -q` |
| Smoke | `repomap doctor --project . && repomap overview --project . && repomap verify --project . --quick` |

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

The open-source skill (`skills/repomap/SKILL.md`) is distributed to users. The local copy (`~/.agents/skills/repomap/SKILL.md`) must be byte-identical to the open-source version. The skill is a single file — no subdirectories. Neither may include:
- Any references to local file paths (e.g., absolute paths on maintainer's machine)
- Any maintainer-specific workflow or feedback mechanisms

## Release Rule（铁律）

**任何代码修改完成后，必须走完整发布流程。** 不允许只 commit 不发布。修改即发布，发布即完整（版本号 → binary → commit → push → CI → GitHub Release）。

## Post-Change Checklist

After any code change to `src/`, work through these steps. **Every step must complete before moving to the next. When a step depends on an external async process (CI), wait for completion automatically — poll every 60s with `gh run list`, do not ask the user to wait.**

```bash

# 1. Run ALL tests (core unittest + new pytest-based tests)
uv run python -m unittest discover -s tests -v
uv run --with pytest python -m pytest tests/test_git_backend.py tests/test_callgraph.py tests/test_type_inference.py -q

# 2. Rebuild binary
uv run --with pyinstaller python -m src.cli build-binary --output dist

# 3. Smoke test (current project)
repomap doctor --project .
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


# 4.5. Sync local skill directory to ~/.agents/skills/repomap/
#      cp skills/repomap/SKILL.md ~/.agents/skills/repomap/SKILL.md
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

- **No binaries in git**: `dist/repomap` and `npm/platforms/*/repomap*` are gitignored. Build artifacts stay local.
- **npm distribution**: `npm install -g repomap-bin`（仅 Linux x64）。`repomap-bin` 是 wrapper 包（含 `repomap.js` shim），通过 `optionalDependencies` 拉取 `@gjczone/repomap-linux-x64` 二进制包。Windows/macOS 用户需从源码自行构建（见 README）。wrapper 在 CI 中从 linux 平台发布。所有包版本由 CI 从 `pyproject.toml` 自动同步。
- **GitHub Releases**: Text-only bilingual (CN+EN) changelogs. No binary attachments. Created via `gh release create` with `--notes`.
- **Version bump**: When bumping version, update `pyproject.toml`.
- **CI build**: CI builds the Linux x64 binary and runs tests. Auto-wait for CI: poll `gh run list --repo gjczone/repomap --branch main --limit 1` every 60s until `status=completed`; check `conclusion=success` before release.

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

### Version Alignment（铁律）

**`pyproject.toml` 是版本号的唯一真相来源。** 发布时的 commit message、git tag、GitHub Release 和 npm publish 的版本号必须与 `pyproject.toml` 中的 `version` 字段严格一致。

- Bump 版本时，**只改 `pyproject.toml`** 一处，其余全部由 CI 自动同步
- Commit message 中的版本号必须与 `pyproject.toml` 一致
- 创建 GitHub Release 时使用的 tag 名必须与 `pyproject.toml` 一致
- **绝不允许** commit message 写 v2.6.0 但 `pyproject.toml` 是 2.4.6 这种不一致
- 推送前执行 `grep '^version = ' pyproject.toml` 确认当前版本，再据此撰写 commit message

### Commit Message
- Format: `[release]: vX.Y.Z — English summary of primary change`
- Version number in message MUST match `pyproject.toml` version exactly.
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

### CI Breakage Prevention（铁律）

**修改以下内容时必须同步更新 CI 配置，否则 CI 必然失败：**

1. **CLI 参数变更**（如新增/删除必传参数、重命名参数）→ 更新 `.github/workflows/build-binaries.yml` 中 smoke test 的命令行
2. **新增/删除 Python 依赖** → 更新 `.github/workflows/build-binaries.yml` 中 `uv run --with ...` 的参数列表
3. **`install.js` 或 npm `package.json` 变更** → 本地验证 `npm install -g` 后 binary 可执行

**每次提交前必须执行的预检：**
```bash
# 1. 确认 smoke test 命令行与当前 CLI 签名一致
grep "repomap " .github/workflows/build-binaries.yml

# 2. 确认没有遗漏 --project 参数（所有命令除 build-binary 外都需 --project）
grep -E "repomap (doctor|overview|verify|check)" .github/workflows/build-binaries.yml | grep -v "\-\-project"

# 3. 确认 npm 包名正确（wrapper + 3 个平台包 + 所有平台包都是 scoped）
grep '"name"' npm/wrapper/package.json npm/platforms/*/package.json
# 期望: wrapper -> repomap-bin, linux-x64 -> @gjczone/repomap-linux-x64, darwin-arm64 -> @gjczone/repomap-darwin-arm64, windows-x64 -> @gjczone/repomap-windows-x64
```

### npm 包发布规则

- **Wrapper**: `repomap-bin`（`npm/wrapper/`）— `npm install -g repomap-bin` 的入口，含 `repomap.js` shim + `optionalDependencies`
- **Linux x64**: `@gjczone/repomap-linux-x64`（`npm/platforms/linux-x64/`）
- **macOS ARM64**: `@gjczone/repomap-darwin-arm64`（`npm/platforms/darwin-arm64/`）
- **Windows x64**: `@gjczone/repomap-windows-x64`（`npm/platforms/windows-x64/`）
- 三个平台包都是 scoped（需 `--access public`），通过 `os`/`cpu` 字段确保 npm 只安装匹配平台的版本
- Wrapper 通过 `optionalDependencies` 声明对三个平台包的依赖，npm 自动只安装当前平台的那一个
- Wrapper 仅在 linux CI job 中发布一次（不是每个平台都发）
- 所有包的版本号在 CI 中自动从 `pyproject.toml` 同步
- 若某平台 npm publish 因"version already exists"失败，检查上游版本是否冲突；不得依赖 `|| echo` 静默跳过

### Completion Report
After release is fully done, report:
- Git tag and commit hash
- GitHub Release URL
- CI run URL

