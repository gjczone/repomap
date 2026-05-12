# RepoMap — Skill + CLI for AI-Agent Repository Intelligence

`repomap` is a **skill + CLI tool**. AI agents (Claude Code, Codex, OpenCode) invoke it via the skill definition in `skills/repomap/SKILL.md`. The skill tells the agent *when* to call `repomap`; the CLI binary does the actual work: tree-sitter AST scanning, dependency graph building, PageRank ranking, and structured report generation.

**Distribution**: npm is the sole distribution channel. All 5 packages (`repomap-mcp-server`, `repomap-bin`, and 3 platform binaries) are published to npm. No PyPI, no GitHub Release binaries, no manual downloads. See [README.md](./README.md) for the user-facing install instructions.

## Project Snapshot

- **Shape**: Python package (`src/`) + prebuilt binaries for Linux/macOS/Windows (CI-built, distributed via npm platform packages)
- **Core capability**: tree-sitter AST → symbol extraction → import resolution → call-chain analysis → AI-friendly reports
- **Languages**: Python, JS/TS (including TSX), Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON
- **Distribution**: MCP (`npx repomap-mcp-server`) / npm (`repomap-bin`) / skill (`skills/repomap/` → `~/.claude/skills/repomap/`) + binary (`~/.local/bin/repomap`)
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
│   ├── cli.py             # argparse CLI, dispatch, core constants (~410 lines)
│   └── handlers.py        # All run_* command implementations + shared helpers (~2450 lines)
├── core.py                # RepoMapEngine: scan pipeline, graph build, skip lists
├── parser.py              # TreeSitterAdapter: AST parsing, import/export bindings
├── resolver.py            # ImportResolver: resolve imports to file paths
├── ranking.py             # EdgeBuilder, GraphAnalyzer: PageRank, call-graph edges
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
mcp/                       # MCP server (TypeScript)
├── src/                   # MCP server source (index.ts, repomap.ts, tools.ts)
├── repomap-bin/           # Binary finder + npm wrapper package
└── package.json           # MCP server package metadata
tests/                     # Test suite
dist/repomap               # Local build output (CI builds Linux/macOS/Windows via GitHub Actions)
```

**Dependency flow**: `cli.py` → `core.py` (engine) → `parser.py` (AST) → `resolver.py` (imports) → `ranking.py` (graph) → `ai.py` (reports). Cross-cutting: `__init__.py` (data types), `topic.py` (scoring), `check.py` (diagnostics), `toolkit.py` (cache/git).

## Change Map

- **Parser/AST**: `src/parser.py`, `src/resolver.py` → all symbol/call-chain commands
- **Graph/ranking**: `src/ranking.py` → `overview`, `call-chain`, `query-symbol`, `impact`, `hotspots`
- **CLI/commands**: `src/cli/cli.py` (argparse + dispatch) + `src/cli/handlers.py` (run_* implementations) → add subparser in cli.py, implement handler in handlers.py, render via `src/ai.py`
- **Reports**: `src/ai.py` → each `render_*` function owns one report type
- **Topic scoring**: `src/topic.py` → `impact`, `verify`, `query` test suggestions
- **Diagnostics**: `src/check.py` → `check`, `verify`
- **Cache/diff**: `src/toolkit.py` → `cache save`, `diff`, `verify --with-diff`
- **Route consumers**: `src/consumers.py` → `routes --with-consumers`
- **State map**: `src/state_map.py` → `state-map --symbol/--query`
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
- Binary distribution is via npm platform packages (`repomap-bin-linux-x64`, etc.), not manual download.
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
- `.gitignore` keeps `docs/` local-only (not in public repo).

## Agent Boundary Discovery

When using `repomap`, AI agents encounter tool boundaries that specs don't cover. These discoveries are logged in [`docs/BOUNDARIES.md`](docs/BOUNDARIES.md) for continuous improvement.

**What to log**: language/framework gaps, performance boundaries, output precision issues, workflow friction, edge cases.

**When to log**: after any `repomap` command that fails or produces unexpected results; when output requires post-processing; when a real coding task exposes a capability gap.

**Format**: each entry uses `[ ]` (pending) or `[x]` (resolved) checkbox format with discovery scenario, current behavior, expected behavior, and impact on agent workflow.

**Workflow**: use repomap normally → hit a boundary → find alternative approach → log the discovery → after fix is implemented, verify and mark `[x]`.

The SKILL.md `## Optimization Feedback` section is the authoritative procedure for what to capture and how to format entries.

## Skill Distribution

The open-source skill (`skills/repomap/SKILL.md`) is distributed to users and must NOT include:
- `## Optimization Feedback` section — local maintainer use only
- Any references to local file paths (e.g., absolute paths on maintainer's machine)
- Any maintainer-specific workflow or feedback mechanisms

The local skill (`~/.agents/skills/repomap/SKILL.md`) includes the full `## Optimization Feedback` section for continuous improvement based on real-world usage.

## Post-Change Checklist

After any code change to `src/` or `mcp/`, work through these steps. **Every step must complete before moving to the next. When a step depends on an external async process (CI), wait for completion automatically — poll every 60s with `gh run list`, do not ask the user to wait.**

```bash
# 1. Run tests
uv run python -m unittest discover -s tests -v

# 2. Rebuild binary
uv run --with pyinstaller python -m src.cli build-binary --output dist

# 3. Copy to platform package + update PATH
cp dist/repomap mcp/repomap-bin/platforms/repomap-bin-linux-x64/repomap
# Binary at ~/.local/bin/repomap must be symlinked to dist/repomap

# 4. Smoke test
repomap doctor
repomap overview --project .

# 5. Evaluate: does SKILL.md need updating?
#    - New commands or changed options → update Command selection table
#    - Changed behavior → update Boundaries section
#    - New limitations discovered → update Boundaries section
#    See skills/repomap/SKILL.md

# 6. Evaluate: do ~/.A1/ai/AGENTS.md or ~/.claude/CLAUDE.md need updating?
#    - New repomap commands → update Section 8.1 command lists
#    - Changed distribution method → update availability description

# 7. Rebuild MCP if TypeScript changed
cd mcp && npm run build && cd ..

# 8. Bump version in all 7 locations (see Distribution Policies)

# 9. Commit + push → CI auto-publishes platform packages
#    Commit message format: [release]: vX.Y.Z — 简短中文描述

# 10. Wait for CI to complete, then publish repomap-bin + repomap-mcp-server locally
#     - Poll CI status: gh run list --repo gjczone/repomap --branch main --limit 1 --json status,conclusion
#     - Wait up to 10 minutes; CI typically takes 3-6 minutes
#     - When CI succeeds: cd mcp/repomap-bin && npm publish && cd /home/guojiancheng/.A1/repomap
#     - Then: cd mcp && npm publish && cd /home/guojiancheng/.A1/repomap (only if MCP source changed)
#     - Verify all 5 packages: for pkg in repomap-bin repomap-mcp-server repomap-bin-linux-x64 repomap-bin-darwin-arm64 repomap-bin-windows-x64; do npm view "$pkg" version; done
#     ⚠️  npm publish changes cwd into the package directory; after publishing,
#     cd back to /home/guojiancheng/.A1/repomap before running cp or repomap commands.

# 11. Sync skill to ~/.agents/skills/repomap/ + append Optimization Feedback
cp -r skills/repomap/references/* ~/.agents/skills/repomap/references/
cp -r skills/repomap/scripts/* ~/.agents/skills/repomap/scripts/
cp skills/repomap/SKILL.md ~/.agents/skills/repomap/SKILL.md
# Manually append ## Optimization Feedback to local copy
diff -r skills/repomap/references/ ~/.agents/skills/repomap/references/

# 12. Create GitHub Release — bilingual: English section first, Chinese section second, separated by ---
```

## MCP Server

`mcp/` is a TypeScript MCP (Model Context Protocol) server that exposes repomap commands as MCP tools for Claude Code, Cursor, VS Code, and other MCP-compatible clients.

### Structure

```
mcp/
├── src/index.ts        # MCP server entrypoint
├── src/repomap.ts      # Binary invocation wrapper
├── src/tools.ts        # MCP tool definitions (overview, query, impact, verify, etc.)
├── repomap-bin/        # Binary finder + npm binary package
│   ├── run.js          # CLI wrapper (resolves binary via dist/ -> npm -> PATH)
│   ├── index.js        # Programmatic API for getBinaryPath()
│   └── package.json    # npm package metadata + optionalDependencies
├── package.json        # MCP server package (depends on repomap-bin)
└── tsconfig.json
```

### Binary resolution order

`repomap-bin/run.js` searches for the repomap binary in this order:
1. `../../dist/repomap` — local repo build (development)
2. `node_modules/<platform-package>/repomap` — resolved via `createRequire` (handles hoisted, npx, yarn, pnpm)
3. `repomap` on PATH — system install

### Building the MCP server

```bash
cd mcp
npm install
npm run build     # compiles TypeScript → dist/
```

### Testing MCP tools locally

```bash
cd mcp
npm run build
node dist/index.js   # starts MCP server on stdio
```

### Publishing to npm

```bash
cd mcp
npm version patch   # or minor/major
npm publish         # publishes repomap-mcp-server + repomap-bin to npm
```

### MCP version sync

When the CLI binary is updated (`src/` changes, binary rebuilt):
- Bump `mcp/package.json` version to match
- Bump `mcp/repomap-bin/package.json` version to match
- Rebuild MCP: `cd mcp && npm run build`
- No GitHub Release needed — npm publishes directly

## Distribution Policies

- **npm only**: All distribution is via npm. No PyPI, no GitHub Release binaries, no manual binary downloads.
- **No binaries in git**: `dist/repomap` and `mcp/repomap-bin/platforms/*/repomap` are gitignored. Build artifacts stay local.
- **GitHub Releases**: Text-only bilingual (CN+EN) changelogs. No binary attachments. Created via `gh release create` with `--notes`.
- **Version bump**: When bumping version, update ALL 7 locations in one commit:
  - `pyproject.toml`
  - `mcp/package.json` + `mcp/repomap-bin/package.json`
  - `mcp/repomap-bin/platforms/repomap-bin-linux-x64/package.json`
  - `mcp/repomap-bin/platforms/repomap-bin-darwin-arm64/package.json`
  - `mcp/repomap-bin/platforms/repomap-bin-windows-x64/package.json`
  - `mcp/src/index.ts` (hardcoded version string in `McpServer` constructor)
- **CI publish**: CI builds platform binaries on ubuntu/macos/windows, publishes to npm if version doesn't already exist. `repomap-bin` wrapper and `repomap-mcp-server` must be published locally after CI succeeds.
  - Auto-wait for CI: poll `gh run list --repo gjczone/repomap --branch main --limit 1` every 60s until `status=completed`; check `conclusion=success` before publishing locally.
  - After local publish, verify all 5 packages match the new version via `npm view <pkg> version`.

## Release Automation Rules

When the user asks to release a new version, follow this automated flow. **No manual checkpoints — every step completes before the next begins. If a step requires waiting (CI), poll automatically and report progress.**

### Version Decision
- **docs/README changes only**: patch bump (x.y.Z)
- **New feature or behavior enhancement**: minor bump (x.Y.z)
- **Breaking changes or major rework**: major bump (X.y.z)
- When in doubt, ask the user; default to minor for feature work.

### Commit Message
- Format: `[release]: vX.Y.Z — 简短中文描述`
- The description should capture the primary change in 5-8 Chinese characters.

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
3. If CI completes but `conclusion=failure`: report the failure, do NOT publish locally.
4. If CI completes and `conclusion=success`: proceed to local npm publish.

### npm Publish Verification
After local publish, run this verification command:
```bash
for pkg in repomap-bin repomap-mcp-server repomap-bin-linux-x64 repomap-bin-darwin-arm64 repomap-bin-windows-x64; do
  ver=$(npm view "$pkg" version 2>/dev/null || echo "N/A")
  echo "$pkg: $ver"
done
```
All 5 packages MUST show the same new version. Report any mismatch.

### Completion Report
After release is fully done, report:
- Git tag and commit hash
- GitHub Release URL
- All 5 npm package versions
- CI run URL

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
