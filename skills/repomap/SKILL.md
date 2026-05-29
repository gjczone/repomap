---
name: repomap
description: "MUST invoke this skill for ANY task that touches code — before reading, editing, searching, debugging, refactoring, navigating, understanding, deleting, renaming, or moving code. Before every grep, every find, every file read, every edit, every symbol lookup, every impact check, every dependency question. After every edit for verification. Also invoke for: investigating bugs, planning features, reviewing PRs, assessing dead code, mapping API routes, tracing call chains, checking diagnostics, auditing state machines. The only exceptions: single-character typo fixes, pure prose/doc edits with zero code impact, and purely non-coding conversations. When in doubt whether to invoke, invoke it."
---

# repomap

`repomap` is a CLI binary on `PATH` that builds a codebase graph (tree-sitter AST → symbols → dependencies → PageRank) and answers structural questions faster than grep + raw reads. It also runs linters, compilers, and LSP diagnostics through `check` and `verify`. It does not replace project tests — run those separately.

`--project` is a **required** argument for every command (except `build-binary`). Always pass it as an absolute path. Do not rely on the subprocess cwd — it may be the user's home directory.

## Core rules

- **Repomap first, grep later.** Before running `grep`, `find`, `rg`, `ls`, or reading a file you haven't seen before, ask: can a repomap command answer this faster and with more context? `query` beats grep for topic search. `query-symbol` beats grep for symbol lookup. `file-detail` beats `cat` for understanding a file's structure. `refs` beats grep for finding all callers. The answer is almost always yes.
- **TIMEOUT: 120 SECONDS MINIMUM — NON-NEGOTIABLE.** Every `bash` command invoking repomap **MUST** use a `timeout` of **at least 120 seconds**. A 15s or 30s timeout WILL cause the command to be SIGKILL'd mid-flight, producing truncated output and unreliable decisions. **This is a HARD RULE. You WILL use 120s. There is NO exception. Any violation — even once — means you are operating on incomplete data and the resulting analysis is untrustworthy. DO NOT compromise on this.**
- **LSP is automatic.** Run `repomap doctor --lsp --project <project>` early in every project. `query-symbol`, `refs`, `file-detail`, `verify`, and `check` automatically use LSP when a server is available. It is the highest-precision signal repomap can provide.
- **Read files before editing.** RepoMap output tells you which files matter — it does not replace reading them.
- **`verify` is the default post-edit gate.** Run it after every non-trivial code change. It aggregates changed-files, risk, contract warnings, and diagnostics. It does not run project tests — run those separately.
- **`impact` before editing, not after.** For any edit that touches more than a few lines, run `impact --files <file> --with-symbols` first. It tells you what else might break.
- **Orphan is a candidate list, not a deletion license.** Verify every high-confidence (≥70) candidate with `refs` before deleting. Check for dynamic references the graph cannot see: string dispatch, reflection, macros, config-driven routing.

## Command selection

Every row is a situation you WILL encounter. Use the command. Do not default to grep/read/find.

| Agent situation                    | Command                                                               | Use when                                                                                                                                                                                                                                                                   |
| ---------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| First repository overview          | `repomap overview --project <project>`                                | ALWAYS at the start of any project. Gives you modules, entrypoints, reading order, hotspots. Co-change analysis is OFF by default (opt-in via `--with-co-change`, window configurable with `--co-change-days`).                                                            |
| Reading a file for the first time  | `repomap file-detail --project <project> --file-path <file>`          | ALWAYS before opening a file you haven't read. Shows symbols, signatures, called-by, and LSP tree.                                                                                                                                                                         |
| Finding where something is defined | `repomap query-symbol --project <project> --symbol <name>`            | ALWAYS instead of grep for symbol lookup. LSP precision by default. Add `--file-path` to narrow.                                                                                                                                                                           |
| Finding all callers of a function  | `repomap refs --project <project> --symbol <name>`                    | ALWAYS before changing a function signature or behavior. Shows every reference.                                                                                                                                                                                            |
| Understanding call flow            | `repomap call-chain --project <project> --symbol <name>`              | ALWAYS before changing logic in a function. Shows callers AND callees. `--direction callers`/`callees` to filter.                                                                                                                                                          |
| Topic/feature search               | `repomap query --project <project> --query <keyword>`                 | When you know the business domain but not the exact files. Like grep but with synonym expansion and relevance ranking.                                                                                                                                                     |
| Symbol search (BM25)               | `repomap search --project <project> --query <text>`                   | When you have a natural-language description of what you're looking for.                                                                                                                                                                                                   |
| Before editing any file            | `repomap impact --project <project> --files <file...> --with-symbols` | ALWAYS before non-trivial edits. Shows key symbols, affected files, suggested tests, risk level, and "Read Next" files.                                                                                                                                                    |
| After editing any file             | `repomap verify --project <project>`                                  | ALWAYS after edits. Aggregates git changes, risk, contract warnings, diagnostics, and test suggestions.                                                                                                                                                                    |
| Quick post-edit check              | `repomap verify --project <project> --quick`                          | After small changes; git-changed files + risk only (skips compiler/LSP).                                                                                                                                                                                                   |
| Auto-fix lint                      | `repomap fix --project <project>`                                     | After making changes, before committing — auto-fixes ruff and eslint issues.                                                                                                                                                                                               |
| Pre-commit readiness               | `repomap ready --project <project>`                                   | One command to verify + check + format before committing. Use right before `git commit`.                                                                                                                                                                                   |
| Compiler/lint diagnostics          | `repomap check --project <project>`                                   | When you need to know if the code compiles or has lint errors. Use `--modified-file` for focused checks.                                                                                                                                                                   |
| Before deleting anything           | `repomap orphan --project <project>`                                  | ALWAYS before deleting code. Finds dead-code candidates. Verify ≥70 confidence with `refs` first.                                                                                                                                                                          |
| Finding API routes                 | `repomap routes --project <project> --json`                           | When working with HTTP/API endpoints. Add `--with-consumers` to find frontend callers.                                                                                                                                                                                     |
| Changing state/enum logic          | `repomap state-map --project <project> --symbol <EnumName>`           | ALWAYS before modifying enums, constants, or state machines. Shows all values, writers, and readers.                                                                                                                                                                       |
| Identifying complex files          | `repomap hotspots --project <project>`                                | When you need to know which files are the densest/most complex.                                                                                                                                                                                                            |
| Checking LSP availability          | `repomap doctor --lsp --project <project>`                            | Early in every project. Shows installed LSP servers and install suggestions.                                                                                                                                                                                               |
| Installing LSP servers             | `repomap lsp setup --project <project>`                               | When doctor shows missing servers. Use `--dry-run` first.                                                                                                                                                                                                                  |
| Pre-edit baseline                  | `repomap cache save --project <project>`                              | Before major edits; enables `verify --with-diff` for contract change detection.                                                                                                                                                                                            |
| Graph comparison                   | `repomap diff --project <project>`                                    | Compare current graph against baseline. Prefer `verify --with-diff` for integrated view.                                                                                                                                                                                   |
| Quick project scan                 | `repomap overview --project <project> --quick`                        | When you only need file/symbol counts and entrypoints.                                                                                                                                                                                                                     |
| Binary health check                | `repomap doctor --project <project>`                                  | When suspecting stale binary or PATH issues.                                                                                                                                                                                                                               |
| Implicit coupling detection        | `repomap overview --project <project> --with-co-change`               | When changing a cross-module hub file, or when `impact` shows surprisingly few dependents for a heavily-used module. Analyzes git history to find co-changing files (window configurable via `--co-change-days`, default 30 days). Expensive: adds 30-60s and ~100 MB RSS. |

## Workflow recipes

Pick the recipe that matches your situation. Commands are shown without `--project` for brevity — always add it.

**New / unfamiliar repo:**

1. `overview` → grasp structure
2. `doctor --lsp` → check LSP availability
3. `query --query <topic>` → find relevant files
4. `file-detail --file-path <top-candidate>` → understand before reading

**Reading a file:**

1. `file-detail --file-path <file>` → understand symbols, structure, callers
2. THEN read the file itself

**Known file, non-trivial edit:**

1. `file-detail --file-path <file>` → understand before touching
2. `impact --files <file> --with-symbols` → read the "Read Next" files
3. Edit
4. `verify` → evidence gate

**Known symbol, changing behavior:**

1. `query-symbol --symbol <name>` → find definition
2. `call-chain --symbol <name>` → understand call flow
3. `refs --symbol <name>` → all references
4. Edit
5. `verify`

**Bug investigation:**

1. `query --query <error/domain>` → find suspects
2. `query-symbol --symbol <name>` / `call-chain --symbol <name>` → trace logic
3. `check --modified-file <fixed-file>` → verify no regressions
4. `verify`

**API/endpoint change:**

1. `routes --json` → full route inventory
2. `routes --with-consumers` → frontend callers
3. `impact --files <route-file> --with-symbols` before editing
4. `refs --symbol <handler>` → all references
5. Edit
6. `verify`

**State/lifecycle change:**

1. `state-map --symbol <EnumName>` → values, writers, readers
2. `refs --symbol <EnumName>` → all references
3. Edit
4. Re-run `state-map` to confirm coverage

**Dead-code check before deletion:**

1. `orphan` → scan candidates
2. Focus on ≥70 confidence
3. `refs --symbol <candidate>` → verify each one
4. Check for dynamic references (string dispatch, reflection, macros)
5. Only then delete

**PR review / assessing someone else's changes:**

1. `overview` → refresh project map
2. `impact --files <changed-files...> --with-symbols` → blast radius
3. `check --modified-file <file>` → focused diagnostics
4. `verify` → aggregate evidence

**Suspected implicit coupling (files change together without code-level deps):**

1. `overview --with-co-change` → git co-change pairs appended to report
2. For each high-frequency pair, run `file-detail --file-path <neighbor>` to understand why
3. Treat co-change pairs as "check these too" candidates — not guaranteed dependencies

**Simple grep/read replacement:**

- Instead of `grep -r "functionName"` → `query-symbol --symbol functionName`
- Instead of `grep -r "keyword" src/` → `query --query "keyword"`
- Instead of `cat file.py | head` → `file-detail --file-path file.py`
- Instead of `grep -r "caller of X"` → `refs --symbol X`

## Critical traps

- **`check` reports `unknown`** → no diagnostic tool ran. This is _not_ passing — it means the project type was detected but nothing verified it. Investigate.
- **`check` reports failure** → any non-skipped tool returned non-zero. Treat as failed even if no structured issue was parsed.
- **`verify --quick` shows no changed files** → cannot assess risk. Stage or commit changes, then use full `verify`.
- **`verify` says "SKIPPED"** → state the limitation explicitly in your completion report — do not treat as passing.
- **`verify` reports contract risk warnings** → address each one before claiming completion. They flag API/signature/state mismatches the graph detected.
- **Orphan high-confidence (≥70)** → still requires `refs` verification. The graph cannot see string dispatch, reflection, macros, or config-driven routing.
- **`cache save` must run _before_ target edits** — `diff`/`verify --with-diff` need a pre-edit baseline. Missing baseline is not proof of safety.
- **`--with-co-change` is opt-in, not default** → adds 30-60s and reads git history. Window configurable with `--co-change-days` (default 30 days). Only use it when: (a) changing a file with many cross-module callers, (b) `impact` shows fewer dependents than expected, or (c) working in a module where history suggests hidden coupling. Never use on first contact or routine edits.
- **Skipping repomap for "simple" tasks** → a "simple read" becomes a "simple edit" becomes a multi-file change. `file-detail` + `impact` cost seconds and prevent hours of debugging. When in doubt, run it.

## Capabilities

- **Precise call graph**: Python (ast module), TypeScript/TSX, Go, Rust (tree-sitter). `self.method()` / `this.method()` / `Self::method()` resolve to class/impl methods with cross-file import resolution.
- **Type inference**: extracts return types and parameters for 10 languages (Python, TS/TSX, Go, Rust, Java, Kotlin, Swift, C#, C++).
- **Git backend**: pygit2 (libgit2 C binding) when available, subprocess git fallback. Both produce consistent output.
- **Search**: BM25 ranking (rank-bm25) with keyword fallback. Symbol documents include name tokens, signature, docstring, return_type, params.
- **LSP**: default-on when available, local-only. Checks project-local executables, PATH, and trusted tool directories. Does not use plugin/MCP, install servers, bundle servers, or run a daemon.

## Boundaries

- This is an AI-agent operating procedure — not user-facing documentation.
- Prefer `repomap` over raw grep/reads when repository intelligence would reduce uncertainty. Skip it for tiny single-line fixes, pure prose edits, or tasks where code relationships clearly don't matter.
- Quote only real CLI output. If `repomap` fails, report the failure — do not guess. Fall back to manual search only after reporting.
- `verify` requires a Git repository. Staged, unstaged, untracked, and rename paths must not be truncated.
- `routes` filters test/e2e DSL noise (Playwright `test.describe`, `console.log`, etc.). Use `query` for mock routes inside test files.
- `overview`'s "supporting files" section is a lightweight inventory — it does not parse Markdown/shell/service files and does not replace `AGENTS.md`/`CLAUDE.md`.
- `.tsx` uses the dedicated TSX parser. Import dependency extraction uses module source strings, not imported symbol names.
- JS/TS object-literal arrow methods (`getMetadata: () => ...`) are recognized as named symbols.
- Member-call resolution (`obj.method()`) requires same-file, explicit import binding, or imported-file evidence — no fallback to unrelated globals.
- Path-taking commands normalize `./...` and absolute paths; paths outside the project are rejected.
