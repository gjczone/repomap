# RepoMap CLI

Standalone `repomap` CLI for repository analysis.

This project replaces the old MCP protocol surface with direct CLI commands so skills can call `repomap` as a normal binary or Python command, without starting an MCP server.

## What Changed

- Former MCP tools are now direct CLI subcommands.
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
| `repomap_cache` | `repomap cache save|load --project <path>` |
| `repomap_diff` | `repomap diff --project <path>` |
| `repomap_git_history` | `repomap git-history --project <path> --symbol <name>` |
| `repomap_refs` | `repomap refs --project <path> [--symbol <name>]` |
| `repomap_orphan` | `repomap orphan --project <path>` |
| `repomap_check` | `repomap check --project <path>` |
| *(new)* | `repomap query --project <path> --query <keyword>` |
| *(new)* | `repomap impact --project <path> --files <file...>` |
| *(new)* | `repomap diff-risk --project <path>` |

## Command Semantics

The old MCP server kept an in-memory scan state between tool calls. This CLI is intentionally stateless.

- Commands that need a symbol graph scan the target project during that invocation.
- Cache-dependent commands (`cache load`, `diff`) use `~/.cache/repomap/`.
- `check` can resolve symbols without a long-lived server by scanning internally.
- `check` treats any non-skipped underlying tool with a non-zero exit code as a failed report, even if no structured issue can be parsed.
- `diff-risk` preserves porcelain status spacing, so staged, unstaged, untracked, and rename paths are reported without truncation.
- Member calls such as `obj.method()` avoid unrelated global fallback targets unless same-file or import evidence exists.
- JS/TS object literal API methods such as `export const api = { getMetadata: () => ... }` are emitted as named method symbols.
- `impact` and `diff-risk` de-duplicate related-test recommendations by `(test_file, target_file)`.

This makes the CLI predictable for skills and shell automation.

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

```bash
uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli cache save --project /path/to/project

uv run --with tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli diff --project /path/to/project
```

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
uv run python -m repomap_cli impact --project /path/to/project --files src/foo.ts
```

File-level change impact: shows who references your symbols, who your symbols call, related tests, and a three-layer risk assessment (structural + domain + change-type). Supports `--json`.

### Change Risk Report (new)

```bash
uv run python -m repomap_cli diff-risk --project /path/to/project
```

Pre-commit safety check: detects all changed files (staged, unstaged, untracked, renamed), runs impact analysis on them, suggests de-duplicated tests to run, flags missing test coverage, and gives a risk level. Supports `--json`.

## Command Value Assessment

### `check`

Value: High

Best use:

- after edits
- before commit
- before handing work back to a skill or another agent
- after cross-file refactors

Why it helps:

- catches real compiler, type, and lint failures
- gives a fast regression gate
- can associate issues back to symbols when scan-based resolution is enabled

Tradeoff:

- depends on project toolchain availability
- can be slower than pure graph queries on large repos

Recommendation:

- keep as a primary command
- in most real workflows this is more valuable than `git-history`

### `file-detail`

Value: Medium-High

Best use:

- when a skill already knows the target file
- before opening a very dense file
- when you want a file-level summary instead of raw source first

Why it helps:

- compresses one file's symbol layout
- shows signatures and local structure
- good for focused reading plans

Tradeoff:

- less useful than `overview` for first contact with an unfamiliar repo
- less useful than `query-symbol` when you do not know the file yet

Recommendation:

- keep it
- position it as a "focused inspection" command, not a first-step command

### `git-history`

Value: Medium-Low

Best use:

- when behavior changed recently and you need commit context
- when the same symbol has churn or ownership questions
- when debugging regressions tied to a recent change window

Why it helps:

- gives local blame and recent commit trail
- useful for "why was this changed?" questions

Tradeoff:

- only works well in a healthy git repo
- usually less important than `overview`, `call-chain`, `refs`, or `check`
- commit history often explains context, but not current runtime impact

Recommendation:

- keep it as a secondary command
- do not make it part of the default first-pass workflow unless the user explicitly asks for history

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
repomap impact --project /some/repo --files src/main.ts
repomap check --project /some/repo
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
repomap call-chain --project /repo --symbol build_query
repomap check --project /repo --since-commit HEAD~1
repomap diff --project /repo
```

Recommended pattern:

- use `overview` when first entering a codebase
- use `query-symbol` or `file-detail` for pinpoint navigation
- use `query` (topic search) when you know the feature area but not the symbol names
- use `impact` before modifying files to assess change blast radius
- use `diff-risk` before committing to validate changes and suggest tests
- use `call-chain`, `refs`, `diff`, `check` for change impact
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
- `diff` still depends on an existing saved cache baseline.
- `query` uses hand-weighted keyword scoring (path + filename + symbol name). Will upgrade to BM25 in a future iteration for better multi-keyword ranking.
- `impact` and `diff-risk` identify affected files via graph edge analysis; event-level coupling (CustomEvent, postMessage) is not yet detected (planned as `event-map` command).
- Test matching uses 5-level heuristics (name → path → import → symbol → git co-change). Coverage depends on project structure and git history depth.
- `diff-risk` depends on `git status` and works best within a git repository.

## Delivery Status

See:

- `docs/deliverables/delivery-report-2026-04-26.md`
