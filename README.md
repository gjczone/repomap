# RepoMap CLI — 给 AI 代码助手用的项目地图

`repomap` 是一个命令行工具，它会读取一个代码项目，整理出“项目地图”：哪些文件重要、某个功能可能在哪、一个文件被谁使用、改它可能影响什么、应该优先看哪些测试、当前改动风险有多高。

如果把一个代码项目想成一栋很大的楼，普通搜索相当于在楼里喊关键词；`repomap` 更像是先拿到楼层图、房间用途、通道关系和安全出口。它的核心价值不是替代程序员或测试，而是让 AI 代码助手在动手改代码前少猜一点、少乱翻文件、少漏掉影响范围。

## 给完全不了解 repomap 的人

### 它解决什么问题？

AI 代码助手经常会遇到三个问题：

1. **不知道从哪里开始看**：项目文件很多，只靠 `grep` / `find` 容易读一堆不相关文件。
2. **不知道改动会影响谁**：改一个文件或函数前，不清楚谁在调用它、它又依赖谁。
3. **不知道该验证什么**：改完之后，不知道应该跑哪些测试、看哪些风险。

`repomap` 的作用就是把这些问题变成更结构化的提示：

- 先看项目总览：`overview`
- 按业务词找代码：`query`
- 看某个文件里有什么：`file-detail`
- 改文件前看影响和风险：`impact --with-symbols`
- 改完后做交付前证据汇总：`verify`（快速模式 `verify --quick` 跳过编译器/LSP，只看变更风险）
- 做质量检查：`check`
- 有本地语言服务器时，再额外用 LSP 做更精确的诊断或引用证据

### 谁应该用？

- **AI 代码助手 / Agent**：这是主要使用者。它可以先用 `repomap` 建立上下文，再决定读哪些文件和怎么改。
- **人类开发者**：也可以用它快速理解陌生项目、改动影响、测试建议。
- **非技术负责人**：不需要理解所有命令，只要知道它能帮助 AI 在改代码前做“地图和风险评估”。

### 它不会做什么？

`repomap` 的边界很明确：

- 不会自动改代码。
- 不会替代真实测试。
- 不会自动安装工具或语言服务器。
- 不会启动后台常驻服务。
- 不依赖 IDE、插件、MCP server。
- LSP 能力是可选的，只在明确加 `--with-lsp` 或相关命令时使用。

### 最常用的一条工作流

当 AI 准备修改一个已知文件时，推荐流程是：

> 不传 `--project` 时，`repomap` 会扫描当前工作目录；传了 `--project` 时，会扫描指定目录。AI/Agent 调用时建议始终传绝对项目路径，避免上层工具从 home 目录启动子进程导致误扫。

```bash
repomap file-detail --project /path/to/project --file-path src/foo.ts
repomap impact --project /path/to/project --files src/foo.ts --with-symbols
# 修改代码后
repomap verify --project /path/to/project
```

通俗解释：

1. 先看这个文件里面有哪些关键结构。
2. 再看改它会影响哪些文件、哪些符号、哪些测试，以及风险高不高。
3. 改完后用 `verify` 汇总变更、风险、建议测试、诊断结果和可选 LSP / graph diff 证据。

### 为什么 `impact --with-symbols` 很重要？

普通 `impact` 只回答“这个文件改动可能影响哪些文件”。加上 `--with-symbols` 后，它会更像一个编辑前计划器，额外告诉 AI：

- 目标文件里的关键函数 / 类 / 方法
- 建议下一步阅读哪些文件，按优先级排序
- 哪些测试可能相关
- 改动风险说明
- 本机是否有可用的 LSP，可以进一步做精确诊断或引用检查

这能让 AI 在写代码前先形成计划，而不是边猜边改。

## 项目定位

`repomap` is an **AI-agent repository intelligence layer** for CLI/TUI coding workflows. It gives agents an IDE-like project map without requiring an IDE, MCP server, plugin, daemon, or bundled language servers.

The goal is to stop agents from understanding projects only through `grep`, `find`, and raw file reads. `repomap` provides compact, high-value guidance before and during work: what to read, what a file or symbol affects, which tests matter, what changed, where the risk is, and when optional local LSP evidence can improve confidence.

AST graph and repository structure are the default source of truth. Local LSP diagnostics, definitions, and references are opt-in evidence when explicitly requested.

This project replaces the old MCP protocol surface with direct CLI commands so skills can call `repomap` as a normal binary or Python command, without starting an MCP server.

## What Changed

- Former MCP tools are now direct CLI subcommands.
- The CLI is optimized for AI-agent workflows: first-look overview, focused code discovery, edit planning, post-edit risk review, diagnostics, and optional local LSP evidence.
- JS/TS `import` / `export` bindings are parsed from tree-sitter AST instead of regex.
- Binary delivery is treated as a first-class artifact.
- The runtime no longer depends on `repomap_server.py` or `MCPServer`.

## Former MCP -> CLI Mapping

| Former MCP Tool | CLI Command |
|---|---|
| `repomap_scan` | `repomap scan --project <path>` |
| `repomap_overview` | `repomap overview --project <path>` |
| `repomap_call_chain` | `repomap call-chain --project <path> --symbol <name>` |
| `repomap_query_symbol` | `repomap query-symbol --project <path> --symbol <name>` |
| `repomap_file_detail` | `repomap file-detail --project <path> --file-path <file>` |
| `repomap_hotspots` | `repomap hotspots --project <path>` |
| `repomap_cache` | `repomap cache save --project <path>` |
| `repomap_diff` | `repomap diff --project <path>` |
| `repomap_git_history` | `repomap git-history --project <path> --symbol <name>` |
| `repomap_refs` | `repomap refs --project <path> [--symbol <name>]` |
| `repomap_orphan` | `repomap orphan --project <path> [--json] [--min-confidence N]` |
| `repomap_check` | `repomap check --project <path>` |
| *(new)* | `repomap query --project <path> --query <keyword>` |
| *(new)* | `repomap impact --project <path> --files <file...> [--with-symbols]` |
| *(new)* | `repomap verify --project <path> [--quick] [--with-lsp] [--with-diff]` |
| *(new)* | `repomap routes --project <path> [--json]` |
| *(new)* | `repomap diagnostics --project <path> --source lsp --files <file...>` |
| *(new)* | `repomap lsp doctor --project <path>` |

## Command Semantics

This section is for technical readers who need exact behavior. If you only want the practical workflow, read **给完全不了解 repomap 的人** and **AI Agent Workflow** first.

The old MCP server kept an in-memory scan state between tool calls. This CLI is intentionally stateless.

- If `--project` is omitted, commands use the current working directory. If `--project` is provided, commands use that explicit directory.
- Commands that need a symbol graph scan the target project during that invocation.
- `cache save` stores a graph baseline in `~/.cache/repomap/` before target edits. `diff` and `verify --with-diff` read that saved baseline later; there is no public `cache load` action.
- Cache directories are keyed by the canonical project path so relative and absolute references to the same project share a cache while same-name projects in different directories stay isolated.
- Session scan cache restores only when both the source fingerprint and saved `project_root` match the current project.
- `check` can resolve symbols without a long-lived server by scanning internally.
- `check` treats any non-skipped underlying tool with a non-zero exit code as a failed report, even if no structured issue can be parsed.
- `check --modified-file` accepts only paths inside the project and passes incremental file operands after `--` where supported, so file names cannot be interpreted as tool options.
- `query --paths/--exclude`, `impact --files`, and `file-detail --file-path` normalize `./...` and absolute in-project paths to project-relative paths; outside-project paths fail clearly.
- `impact --with-symbols` turns file-level impact into an edit-planning report with key symbols, ordered read-next guidance, and lightweight local LSP availability hints; it only detects LSP availability and does not start servers.
- `query --paths` and `query --exclude` match path segments (`src` matches `src/a.py` but not `src2/a.py`).
- `verify --quick` preserves porcelain status spacing and reports staged, unstaged, untracked, and rename paths without truncation; it fails clearly when `git rev-parse` or `git status --porcelain` fails.
- `verify` aggregates post-edit evidence from git changed files, risk analysis, `check`, optional LSP diagnostics, and optional graph diff; it does not run project tests automatically and treats missing cache baseline as a non-fatal skipped graph diff. Use `--quick` to skip compiler/LSP checks for a faster change-risk-only report.
- Member calls such as `obj.method()` avoid unrelated fallback targets unless same-file or import evidence exists.
- TS/JS config aliases respect `baseUrl` when resolving non-relative `paths` targets.
- Python dotted imports such as `from pkg.sub import helper` resolve to package paths like `pkg/sub.py` or `pkg/sub/__init__.py`.
- Unresolvable imported names are not silently rebound to same-named global symbols, which avoids misleading call-chain edges.
- JS/TS object literal API methods such as `export const api = { getMetadata: () => ... }` are emitted as named method symbols.
- `overview` is primarily a source-symbol graph. It also lists a small `支撑文件（非符号图）` inventory for key docs, manifests, scripts, and service/config files such as `AGENTS.md`, `CLAUDE.md`, `README.md`, `SKILL.md`, `package.json`, `scripts/*.sh`, and `*.service`. This inventory does not parse or summarize those files and does not replace injected agent context.
- `overview --with-heat` can mark recently changed files, and `overview --with-co-change` explicitly enables the heavier Git co-change section; default `overview` does not run Git history scans.
- `.tsx` files use the dedicated TSX tree-sitter grammar, and `doctor` reports parser availability plus module load paths.
- Anonymous default exports and CommonJS function exports are bound to stable anonymous symbols so default import / require call chains can resolve them.
- Package self-reference imports using `package.json` `name` + `exports` resolve relative to the package root, including monorepo sub-packages.
- LSP integration is opt-in and local-only: `repomap lsp doctor` detects locally installed LSP servers, `diagnostics --source lsp --files ...` starts them on demand through stdio, `check --with-lsp --modified-file ...` merges their diagnostics, and `query-symbol --with-lsp` / `refs --with-lsp` can add definition/reference evidence for one selected symbol. Detection checks project-local executables, `PATH`, and trusted user tool directories used by npm/pnpm/yarn/bun/pipx/uv/mason/cargo/go. `repomap` does not depend on plugin/MCP, does not run `npx`/`pnpx`/`bunx`, does not install servers, does not bundle servers, and does not keep a background daemon.

This makes the CLI predictable for skills and shell automation.

## AI Agent Workflow

Use `repomap` wherever repository intelligence can reduce uncertainty, save context, or prevent risky edits — not only before reading files.

### First touch / unfamiliar repository

```bash
repomap overview --project /path/to/project
repomap query --project /path/to/project --query "feature or domain keyword"
repomap file-detail --project /path/to/project --file-path src/foo.ts
```

Use this when the agent needs a project map, likely entry points, core files, related tests, and a compact reading order before raw file reads.

### Locate a feature, bug, or symbol

```bash
repomap query --project /path/to/project --query "auth token refresh"
repomap query-symbol --project /path/to/project --symbol refreshToken
repomap refs --project /path/to/project --symbol refreshToken
repomap call-chain --project /path/to/project --symbol refreshToken
```

Use `--file-path` to disambiguate repeated symbol names. Add `--with-lsp` only when exact local definition/reference evidence is worth the LSP startup cost.

### Plan an edit to known files

```bash
repomap file-detail --project /path/to/project --file-path src/foo.ts
repomap impact --project /path/to/project --files src/foo.ts --with-symbols
```

Use this before non-trivial edits. `--with-symbols` adds key symbols, an ordered read-next list, related tests, risk notes, and lightweight LSP availability hints before changing code.

What to look for:

- **Key Symbols**: important functions/classes/methods inside the target files. If you plan to change one of these, treat the change as behavior-sensitive.
- **Read Next**: a prioritized list of files to inspect next. Start with `target`, then high-confidence affected files, then related tests.
- **Risk Level / Risk Notes**: business-facing warning signs such as broad structural impact, sensitive domains, or config/build changes.
- **LSP hint**: only says whether local LSP evidence is available. It does not start a server unless you later run an LSP command.

### Plan a behavior change to a known symbol

```bash
repomap query-symbol --project /path/to/project --symbol helper --file-path src/foo.ts
repomap call-chain --project /path/to/project --symbol helper --file-path src/foo.ts
repomap refs --project /path/to/project --symbol helper --file-path src/foo.ts --with-lsp
```

Use this before changing function/class/method behavior so the agent knows callers, callees, and reference evidence.

### When you only know a topic

If you do not know the file name yet, start with `query`:

```bash
repomap query --project /path/to/project --query "login token refresh"
```

Then inspect the top file:

```bash
repomap file-detail --project /path/to/project --file-path src/auth/session.ts
```

### When you already know the file

Use `impact --with-symbols` before non-trivial edits:

```bash
repomap impact --project /path/to/project --files src/auth/session.ts --with-symbols
```

This is the best default pre-edit command because it combines local file symbols, affected files, related tests, risk, and LSP availability hints in one report.

### Validate after edits

```bash
repomap verify --project /path/to/project
repomap verify --project /path/to/project --with-lsp
repomap verify --project /path/to/project --with-diff
```

Use this before final handoff. `verify` summarizes changed files, risk, suggested tests, `check` results, optional focused LSP diagnostics, and optional graph diff evidence in one report. It does not run your suggested tests automatically; run or account for them separately when needed.

## Reading Value Policy

`repomap` now distinguishes between:

- graph centrality: raw PageRank and dependency connectivity
- reading value: symbols and files that are more useful for understanding or modifying behavior

In practice this means:

- `overview` prioritizes key implementation symbols instead of dumping raw PageRank leaders
- text output defaults are now sized for AI workflows instead of terminal-dump completeness
- `hotspots` uses effective symbol density, so HTML tags, CSS selectors, and JSON keys do not drown real code
- lockfiles such as `package-lock.json` are skipped from symbol scan because they distort repo understanding without helping navigation
- entry files can still appear in reading order even when they contain little or no extractable symbol structure, including after CLI session-cache restore
- `call-chain` ignores low-signal non-callable targets such as JSON keys, so object/config noise does not leak into runtime call graphs
- `file-detail` now defaults to a compact symbol slice, and `overview/query-symbol/call-chain/file-detail` all support explicit text caps
- raw PageRank is still available in `query-symbol`, `file-detail`, and the graph itself when you need centrality

## AST Accuracy Upgrade

JS/TS import/export bindings are now extracted from tree-sitter AST nodes instead of raw regex matching.

Benefits:

- Ignores fake hits inside comments and strings
- Captures `export * as utils from './utils'`
- Preserves named/default/namespace import structure
- Preserves CommonJS `require()` and `module.exports` AST handling

Current AST-backed coverage includes:

- ES module default imports
- ES module named imports and aliases
- ES module namespace imports
- Re-exports and local exports
- `export *`
- `export * as <name>`
- CommonJS destructured `require`
- CommonJS `module.exports = { ... }`
- CommonJS `exports.name = value`
- JS/TS object literal function properties: `getMetadata: () => ...`, `getKpi: async () => ...`, and `getItem: function () { ... }`

### Local LSP Diagnostics (opt-in)

```bash
repomap lsp doctor --project /path/to/project
repomap diagnostics --project /path/to/project --source lsp --files src/foo.ts
repomap check --project /path/to/project --with-lsp --modified-file src/foo.ts
repomap query-symbol --project /path/to/project --symbol helper --with-lsp
repomap refs --project /path/to/project --symbol helper --with-lsp
```

`repomap` only detects or starts LSP servers already installed on the machine or in the project, such as `typescript-language-server`, `pyright-langserver`, `pylsp`, `rust-analyzer`, or `gopls`. Detection checks project-local executables, `PATH`, and trusted user tool directories used by npm/pnpm/yarn/bun/pipx/uv/mason/cargo/go. It does not use plugin/MCP, does not run `npx`/`pnpx`/`bunx`, does not install servers, does not bundle servers in the binary, and does not run a background daemon. Missing servers are reported as skipped, not as a core `repomap` failure. Symbol-level LSP evidence is intentionally limited to explicit `--with-lsp` requests and a selected symbol, so normal graph commands remain fast and deterministic.

## Installation

### Python Command

```bash
cd /home/guojiancheng/.A1/ai/cli-created/cli/repomap
uv run python -m repomap_cli --help
```

### Script Entry Point

```bash
cd /home/guojiancheng/.A1/ai/cli-created/cli/repomap
uv run repomap --help
```

### Put The Binary On PATH

`/home/guojiancheng/.local/bin` is already on this machine's `PATH`, so the recommended setup is:

```bash
ln -sf /home/guojiancheng/.A1/ai/cli-created/cli/repomap/dist/repomap /home/guojiancheng/.local/bin/repomap
repomap --help
```

If you rebuild the binary later, the symlink still points at the newest file in `dist/repomap`.

For a future AI assistant, use:

- [For AI: Repomap Smoke Check](/home/guojiancheng/.A1/ai/cli-created/cli/repomap/docs/for-ai-smoke-check.md)
- [Repomap Acceptance Checklist](/home/guojiancheng/.A1/ai/cli-created/cli/repomap/docs/acceptance-checklist.md)

## Binary Location

Current Linux binary:

`/home/guojiancheng/.A1/ai/cli-created/cli/repomap/dist/repomap`

Current PATH entry:

`/home/guojiancheng/.local/bin/repomap`

## Quick Start

### Self Check

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli doctor
```

### Project Map

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli overview --project /path/to/project
```

### Call Chain

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli call-chain --project /path/to/project --symbol helper --depth 3
```

### Symbol Query

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli query-symbol --project /path/to/project --symbol helper
```

### Cache + Diff

Use `cache save` before the target edits when you know you will want a later graph-only comparison. After edits, use `diff` for the advanced graph comparison, or `verify --with-diff` for final evidence.

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli cache save --project /path/to/project

# after the target edits
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli diff --project /path/to/project
```

### Impact Analysis / Edit Plan

Use this before changing a known file:

```bash
repomap impact --project /path/to/project --files src/foo.ts --with-symbols
```

Plain-language meaning:

- “I may change this file; tell me what else might matter before I edit.”

The report includes:

- input files: the files you asked about
- edit plan: a short suggested order for reviewing evidence
- key symbols: important functions/classes/methods in those files
- read-next list: target files, affected files, and related tests in a useful order
- likely affected files: other files connected through call/import evidence
- suggested tests: test files likely worth running
- risk level and notes: low/medium/high plus reasons
- LSP hint: whether local language-server evidence is available for a deeper check

If you do not need the extra planning sections, this shorter form still works:

```bash
repomap impact --project /path/to/project --files src/foo.ts
```

JSON output for automation:

```bash
repomap impact --project /path/to/project --files src/foo.ts --with-symbols --json
```

Important: `--with-symbols` only detects whether LSP help is available. It does not start an LSP server. To actually use LSP evidence, run focused commands such as `diagnostics --source lsp`, `query-symbol --with-lsp`, or `refs --with-lsp`.

### Verify / Post-edit Evidence Gate

Use this after edits and before final handoff:

```bash
repomap verify --project /path/to/project
```

Plain-language meaning:

- “I changed code; collect the evidence I need before saying the work is done.”

The report includes:

- changed files from Git
- risk level and missing-check warnings
- affected files and suggested tests
- `check` result from project diagnostics
- optional LSP diagnostics when `--with-lsp` is enabled
- optional graph diff when `--with-diff` is enabled and a cache baseline exists
- a final checklist that tells the AI what evidence is still missing

Useful variants:

```bash
repomap verify --project /path/to/project --json
repomap verify --project /path/to/project --with-lsp
repomap verify --project /path/to/project --with-diff
```

Important: `verify` does not automatically run your project test suite. It suggests likely tests and reports diagnostics; the agent still needs to run or explicitly account for real tests when the change requires them.

### Diagnostics

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli check --project /path/to/project
```

### Topic Search (new)

```bash
uv run python -m repomap_cli query --project /path/to/project --query "auth"
```

AI-friendly keyword-based code discovery. Finds relevant files by path, filename, and symbol name matching — without needing to know exact symbol names. Output includes reading order, core/supporting files, related tests, and key symbols. Supports `--json`, `--paths <dirs>`, `--exclude <dirs>`, `--no-tests`.

### Impact Analysis (new)

```bash
uv run python -m repomap_cli impact --project /path/to/project --files src/foo.ts --with-symbols
```

File-level change impact: shows who references your symbols, who your symbols call, related tests, and a three-layer risk assessment (structural + domain + change-type). Add `--with-symbols` for edit planning: key symbols in target files, ordered read-next guidance, and local LSP availability hints. Supports `--json`.

### Verify / Post-edit Evidence Gate (new)

```bash
uv run python -m repomap_cli verify --project /path/to/project
```

Post-edit evidence gate: detects changed files, summarizes risk and suggested tests, runs `check`, and can include focused LSP diagnostics with `--with-lsp` or graph diff with `--with-diff`. Supports `--json`.

### Change Risk / Quick Verify

```bash
repomap verify --project /path/to/project --quick
uv run python -m repomap_cli verify --project /path/to/project --quick
```

Pre-commit safety check: detects all changed files (staged, unstaged, untracked, renamed), runs impact analysis on them, suggests de-duplicated tests to run, flags missing test coverage, and gives a risk level — without running compiler/LSP checks. Supports `--json`. For the full gate including diagnostics, use `verify` without `--quick`.

## Command Value Assessment

Use the public commands by value tier, not by historical availability.

### Primary workflow commands

These should be the default choices for most agent work:

- `overview` — first-look project map. It prioritizes source graph reading order, module summaries, entry points, API routes, hotspots, key implementation symbols, and a small non-AST supporting-file inventory.
- `query` — topic / feature discovery when you know business words but not exact files or symbols.
- `file-detail` — focused inspection when the target file is already known.
- `impact --with-symbols` — default pre-edit planning command for known files; combines key symbols, affected files, read-next guidance, likely tests, risk, and LSP availability hints.
- `query-symbol` — exact/fuzzy symbol lookup.
- `call-chain` — caller/callee context before changing behavior.
- `refs` — reference discovery, with optional `--with-lsp` for local exact evidence.
- `verify` — default post-edit evidence gate.
- `verify --quick` — risk-only post-edit check for current Git changes; replaces the old public `diff-risk` command.
- `check` — lower-level compiler/type/lint diagnostics.
- `orphan` — dead-code candidate discovery.

### Focused secondary commands

Keep these, but use them only when the question is narrow:

- `routes` — direct HTTP/API route inventory with optional `--json` machine-readable output. Prefer this over generic `overview` when the task is “show routes/endpoints”; route inventory filters common test/e2e/spec DSL noise.
- `diagnostics` — focused diagnostics for explicit files, usually `diagnostics --source lsp --files ...`.
- `lsp doctor` — inspect local LSP availability; does not install or start project-wide daemons.
- `hotspots` — dense-file inventory when complexity/churn triage is the explicit goal.
- `git-history` — local history or ownership context; not part of the default first-pass workflow.
- `diff` — advanced graph-only comparison against a baseline saved before the target edits.

### Low-level baseline command

- `cache save` — prepare a graph baseline before target edits. It is intentionally narrow and only exposes `save`; graph comparison reads the baseline through `diff` or `verify --with-diff`.

### Removed public command surface

- `diff-risk` is no longer public. Use `verify --quick` for current-change risk analysis.
- `cache load` is no longer public. Baseline reads are internal to `diff` and `verify --with-diff`.

## Product Roadmap For Agent Workflows

This roadmap records where `repomap` should evolve next. Items below P0 are product plans, not all implemented features.

### P0 — Align docs and skill with the product goal

Status: current documentation work.

- Position `repomap` as a CLI/TUI AI-agent repository intelligence layer.
- Make usage explicit across the whole workflow: discovery, edit planning, symbol tracing, post-edit validation, diagnostics, and final evidence.
- Keep LSP local-only and opt-in.

### P1 — Make `impact` an edit-planning command

Status: implemented for file-level impact through `--with-symbols`.

Current shape:

```bash
repomap impact --project /path/to/project --files src/foo.ts --with-symbols
```

This answers what agents need before editing:

- target files
- key symbols
- incoming references through affected files
- outgoing dependencies / calls through affected files
- related tests
- risk reasons
- suggested read-next order
- whether local LSP evidence is available or recommended

Future expansion may add symbol-level impact:

```bash
repomap impact --project /path/to/project --symbol helper --file-path src/foo.ts
```

### P2 — Add a post-edit evidence gate

Status: implemented as `verify`.

```bash
repomap verify --project /path/to/project
repomap verify --project /path/to/project --with-lsp
repomap verify --project /path/to/project --with-diff
```

This does not replace real tests. It tells agents what changed, what is risky, what diagnostics say, which tests are suggested, and what evidence is still missing.

### P3 — Make LSP evidence easier to choose, still opt-in

Keep LSP startup explicit, but make recommendations smarter:

- `lsp doctor` can explain which commands can benefit from available servers.
- `impact` can suggest `refs --with-lsp` for risky symbols.
- `verify` can add focused LSP diagnostics through `--with-lsp` for changed files.

Do not auto-install servers, run `npx`/`pnpx`/`bunx`, create a daemon, or make LSP mandatory.

### P4 — Improve `query` / `overview` feature slices

Status: partially implemented for `overview` through the lightweight `支撑文件（非符号图）` inventory.

Agents need the smallest useful reading set, not a long list. Current `overview` now separates source-symbol graph output from non-AST supporting files such as injected context docs, README/SKILL files, manifests, scripts, and service/config files. Future work should keep improving:

- core files vs supporting files
- tests and config files
- entry points
- key symbols
- why each file matters
- suggested read order
- stable, compact defaults for large repos

### P5 — Measure whether repomap improves agent work

Add evaluation and smoke scenarios that compare workflows with and without `repomap`:

- number of file reads / grep searches needed to locate code
- whether edit impact and related tests were identified before changes
- whether final reports cite real verification evidence
- whether LSP evidence reduced wrong reference assumptions

## Binary Build

### Local Linux Build

```bash
uv run --with pyinstaller,tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli build-binary --output dist

./dist/repomap doctor
./dist/repomap overview --project /path/to/project
```

### Binary E2E

The test suite includes binary runtime E2E coverage:

```bash
uv run --with pyinstaller,tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m unittest discover -s tests -p 'test_repomap_binary_e2e.py' -v
```

This test really builds the executable, then runs the built binary.

## New Computer Migration

If you move to a new machine, there are two supported paths.

### Option A: Copy Source + Rebuild Binary

Recommended.

Copy:

- `/home/guojiancheng/.A1/ai/cli-created/cli/repomap`
- `/home/guojiancheng/.agents/skills/repomap`

Then on the new machine:

```bash
cd /path/to/cli-created/cli/repomap

uv run --with pyinstaller,tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli build-binary --output dist

mkdir -p ~/.local/bin
ln -sf /path/to/cli-created/cli/repomap/dist/repomap ~/.local/bin/repomap
repomap doctor
```

Why this is preferred:

- avoids OS / architecture mismatch
- gives you a fresh binary against the new machine
- keeps source and binary aligned

### Option B: Copy Built Binary Directly

Only use this when the new machine is compatible with the current binary:

- same OS family
- same CPU architecture
- compatible libc/runtime expectations

For the current binary that means:

- safe target: another Linux x86_64 machine with compatible runtime
- not safe target: Windows
- not safe target: macOS
- risky target: very different Linux distro/runtime stack

If you choose this path:

```bash
mkdir -p ~/.local/bin
cp /path/from/old-machine/repomap ~/.local/bin/repomap
chmod +x ~/.local/bin/repomap
repomap doctor
```

### Skill Migration On New Computer

Copy skill directory:

`/home/guojiancheng/.agents/skills/repomap`

After copying:

1. verify `repomap` is on `PATH`
2. run `repomap doctor`
3. run the skill validator:

```bash
/home/guojiancheng/.agents/skills/repomap/scripts/validate-skill.sh /home/guojiancheng/.agents/skills/repomap
```

If you change the install path on the new machine, update the skill reference text if needed.

## Maintenance / Update Strategy

You do **not** need a fixed frequent release cadence unless one of these happens:

- the CLI starts missing real symbol relationships in your repositories
- a new framework or import/export pattern appears often in your codebase
- tree-sitter bindings or parser behavior change
- `check` starts lagging behind the languages/toolchains you use
- you add new high-value commands for recurring workflows

Recommended practical cadence:

- after any false-positive / false-negative that materially hurts workflow: update soon
- after adding a new language pattern or repository style: update soon
- otherwise do a light smoke check every 1-2 months

Good smoke check:

```bash
repomap doctor
repomap overview --project /some/repo
repomap query --project /some/repo --query main
repomap impact --project /some/repo --files src/main.ts --with-symbols
repomap verify --project /some/repo
```

There is also an AI-ready smoke-check guide here:

- [For AI: Repomap Smoke Check](/home/guojiancheng/.A1/ai/cli-created/cli/repomap/docs/for-ai-smoke-check.md)

Short version:

- no need for weekly churn
- yes, it should evolve when your repositories or parser patterns evolve

## Cross-Platform Build Flow

Local Linux can only produce the Linux binary directly. Windows and macOS binaries must be built on:

- native host
- GitHub Actions matrix runner
- another CI system with native target runners

Included workflow:

- `.github/workflows/build-binaries.yml`

Targets:

- Ubuntu Linux -> `dist/repomap`
- Windows -> `dist/repomap.exe`
- macOS -> `dist/repomap`

The workflow runs:

1. full test suite
2. binary build
3. binary smoke test
4. artifact upload

### Windows Notes

Current status:

- not a current delivery target for your day-to-day workflow
- fully documented so the path is ready later

What changes on Windows:

- output file is `repomap.exe`
- smoke test runs through PowerShell
- PATH installation normally targets `%USERPROFILE%\\AppData\\Local\\Microsoft\\WindowsApps` or another user bin directory

Recommended Windows release path:

1. let GitHub Actions build `repomap.exe` on `windows-latest`
2. download the artifact
3. place it in a user-level PATH directory
4. validate with `repomap.exe doctor`

Important limitations:

- do not claim Windows binary support from Linux local build
- if future users need signed binaries, Authenticode signing must be added separately

### macOS Notes

Current status:

- not a current delivery target for your day-to-day workflow
- documented for future rollout

What changes on macOS:

- output file is still `repomap`
- build must run on a native macOS runner
- distribution may require codesign and notarization depending on trust requirements

Recommended macOS release path:

1. let GitHub Actions build on `macos-latest`
2. smoke test with `./dist/repomap doctor`
3. if distributing outside internal use, add Apple signing/notarization later

Important limitations:

- Linux cannot truthfully produce a final macOS binary
- unsigned binaries may trigger Gatekeeper warnings on end-user machines

## Skill Integration

Future skill usage should call the `repomap` skill first, and that skill should execute the CLI directly.

For natural-language examples that help an AI choose the right command, see:

- `/home/guojiancheng/.agents/skills/repomap/references/prompt-examples.md`

Examples:

```bash
repomap overview --project /repo
repomap routes --project /repo --json
repomap diagnostics --project /repo --source lsp --files src/foo.ts
repomap cache save --project /repo      # before target edits, if graph diff evidence is needed
repomap verify --project /repo --quick  # after edits, risk-only
repomap verify --project /repo --with-diff
```

Recommended pattern:

- use `overview` when first entering a codebase
- use `query-symbol` or `file-detail` for pinpoint navigation
- use `query` (topic search) when you know the feature area but not the symbol names
- use `impact --with-symbols` before modifying known files to assess change blast radius, key symbols, read-next order, tests, risk, and LSP availability
- use `verify` after edits as the default final evidence gate; use `verify --quick` for change-risk only (skips compiler/LSP)
- use `routes` when the question is specifically API/HTTP endpoint inventory; add `--json` for smoke tests or other machine-readable checks
- use `diagnostics` when you need focused LSP diagnostics for explicit files
- use `check` when you need lower-level diagnostics details
- use `cache save` before target edits only when later graph diff evidence is valuable
- use `git-history` only when history or ownership context is the actual question
- when another skill needs repo understanding, prefer delegating to the `repomap` skill so command selection stays consistent

## Tests

### Full Runtime Suite

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m unittest discover -s tests -v
```

### Full Suite Including Binary Build

```bash
uv run --with pyinstaller,tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m unittest discover -s tests -v
```

## Project Structure

```text
cli-created/
└── cli/
    └── repomap/
        ├── repomap_cli/            # standalone CLI entrypoint
        ├── repomap_core.py         # scan pipeline
        ├── repomap_parser.py       # AST parsing, import/export bindings
        ├── repomap_resolver.py     # import resolution
        ├── repomap_ranking.py      # graph analysis
        ├── repomap_topic.py        # topic scoring, test matching, file roles
        ├── repomap_check.py        # diagnostics
        ├── repomap_toolkit.py      # cache/diff/git helper logic
        ├── repomap_ai.py           # markdown report rendering
        ├── repomap_support.py      # core data structures
        ├── tests/                  # unit and binary E2E tests
        ├── docs/deliverables/      # delivery reports
        ├── dist/repomap            # Linux binary
        └── .github/workflows/      # CI matrix build flow
```

## Known Limits

- Dynamic dispatch, reflection, runtime-generated code, and string-built calls can still be missed.
- Windows/macOS binaries are defined in workflow, but not produced locally on Linux.
- `diff` still depends on an existing saved cache baseline created with `cache save` before the target edits.
- `routes` intentionally focuses on production HTTP/API route definitions and filters common test/e2e/spec DSL noise; use `query` or `file-detail` if you need mock route strings inside tests.
- `overview` lists non-AST supporting files as a lightweight inventory only; it does not parse Markdown/shell/service files and does not replace `AGENTS.md` / `CLAUDE.md` context.
- `query` uses hand-weighted keyword scoring (path + filename + symbol name). Will upgrade to BM25 in a future iteration for better multi-keyword ranking.
- `impact` and `verify --quick` identify affected files via graph edge analysis; event-level coupling (CustomEvent, postMessage) is not yet detected (planned as `event-map` command).
- Test matching uses 5-level heuristics (name → path → import → symbol → git co-change). Coverage depends on project structure and git history depth.
- `verify --quick` depends on `git status` and works best within a git repository.

## Delivery Status

See:

- `docs/deliverables/delivery-report-2026-04-26.md`
