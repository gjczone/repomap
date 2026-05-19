---
name: repomap
description: "MUST invoke this skill for any coding task — before reading, editing, searching, debugging, refactoring, navigating, or understanding code. Before every find, every grep, every file read, every edit, every symbol lookup, every impact check. After every edit. The only exceptions: single-character typo fixes and purely non-coding conversations."
---

# repomap

`repomap` is a CLI binary on `PATH` that builds a codebase graph (tree-sitter AST → symbols → dependencies → PageRank) and answers structural questions faster than grep + raw reads. It does not run or replace tests, linters, or compilers — it tells you *where* to look and *what depends on what*.

`--project` is a **required** argument for every command (except `build-binary`). Always pass it as an absolute path. Do not rely on the subprocess cwd — it may be the user's home directory.

## Core rules

- **LSP is not optional when available.** Run `repomap doctor --lsp --project <project>` early in every project. `query-symbol`, `refs`, `file-detail`, `verify`, and `check` use LSP by default; use `--no-lsp` only when a slow or broken server blocks progress. It is the highest-precision signal repomap can provide.
- **Read files before editing.** RepoMap output tells you which files matter — it does not replace reading them.
- **Run only the command the current step needs.** Do not chain `scan` → `overview` → `hotspots` mechanically.
- **`verify` is the default post-edit gate.** It aggregates changed-files, risk, contract warnings, and diagnostics. It does not run project tests — run those separately.
- **Orphan is a candidate list, not a deletion license.** Verify every high-confidence (≥70) candidate with `refs` before deleting. Check for dynamic references the graph cannot see: string dispatch, reflection, macros, config-driven routing.

## Command selection

| Agent situation | Command | Use when |
|---|---|---|
| First repository overview | `repomap overview --project <project>` | Need modules, entrypoints, reading order, hotspots; add `--with-heat`/`--with-co-change` only when needed. |
| Topic/feature search | `repomap query --project <project> --query <keyword>` | Know the area but not exact files; supports `--paths`, `--exclude`, `--no-tests`, `--json`. |
| Symbol search (BM25) | `repomap search --project <project> --query <text>` | Natural-language symbol search; uses BM25 with keyword fallback; supports `--top-k`. |
| Dense known file | `repomap file-detail --project <project> --file-path <file>` | Before reading/editing one file; includes LSP hierarchical symbol tree when available; supports `--json`. |
| Known symbol lookup | `repomap query-symbol --project <project> --symbol <name>` | Need definition candidates; add `--file-path` if ambiguous; LSP evidence is default when available; supports `--json`. |
| Call flow | `repomap call-chain --project <project> --symbol <name>` | Need callers/callees before behavior change; supports `--direction`, `--depth`, `--json`. |
| References | `repomap refs --project <project> --symbol <name>` | Need all references; add `--file-path` if ambiguous; LSP evidence is default when available. |
| Edit planning | `repomap impact --project <project> --files <file...> --with-symbols` | Best default before non-trivial edits: key symbols, read-next, affected files, tests, risk. |
| Compact file impact | `repomap impact --project <project> --files <file...>` | Only need affected files/tests/risk (no edit-plan sections). |
| Final post-edit evidence | `repomap verify --project <project>` | Default after edits; aggregates changes, risk, tests, contract warnings, diagnostics. |
| Quick change risk | `repomap verify --project <project> --quick` | Only git-changed files + risk (skips compiler/LSP). |
| Diagnostics | `repomap check --project <project>` | Compiler/static-analysis plus LSP by default; use `--modified-file`/`--since-commit` for incremental. |
| LSP availability | `repomap doctor --lsp --project <project>` | Check installed servers, get install suggestions. |
| LSP auto-install | `repomap lsp setup --project <project>` | Install missing servers; supports `--languages`, `--dry-run`. |
| API routes | `repomap routes --project <project> --json` | HTTP/API route inventory; add `--with-consumers` to find frontend callers. |
| Hot files | `repomap hotspots --project <project>` | Need dense/complex files first; use sparingly. |
| Orphan candidates | `repomap orphan --project <project>` | Dead-code discovery; use `--min-confidence 70` to filter noise; always verify before deleting. |
| State/enum map | `repomap state-map --project <project> --symbol <name>` | Need state values, writers, readers before lifecycle changes. |
| Graph baseline | `repomap cache save --project <project>` | Pre-edit snapshot for later `verify --with-diff`. |
| Graph diff | `repomap diff --project <project>` | Compare against baseline; prefer `verify --with-diff`. |
| Runtime sanity | `repomap doctor` | Suspect stale binary or PATH mismatch. |
| Project scan summary | `repomap scan --project <project>` | Need counts/entrypoints; usually secondary to `overview`. |
| Build repomap itself | `repomap build-binary --output <dir>` | Only when maintaining repomap; run tests + `doctor` first. |

## Workflow recipes

Pick the recipe that matches your situation. Commands are shown without `--project` for brevity — always add it.

**New / unfamiliar repo:**
1. `overview` → grasp structure
2. `query --query <topic>` → find relevant files
3. `file-detail --file-path <top-candidate>` → understand before reading

**Known file, non-trivial edit:**
1. `file-detail --file-path <file>`
2. `impact --files <file> --with-symbols` → read the "Read Next" files
3. Edit, then `verify`

**Known symbol, changing behavior:**
1. `query-symbol --symbol <name> --file-path <file>` (if ambiguous)
2. `call-chain --symbol <name>` + `refs --symbol <name>` → understand impact
3. Edit, then `verify`

**Bug investigation:**
1. `query --query <error/domain>` → find suspects
2. `query-symbol --symbol <name>` / `call-chain --symbol <name>` → trace logic
3. Fix, then `check` or `verify`

**API/endpoint change:**
1. `routes --json` → full route inventory
2. `routes --with-consumers` → frontend callers
3. `impact --files <route-file> --with-symbols` before editing
4. `refs --symbol <handler>` → all references
5. Edit, then `verify`

**State/lifecycle change:**
1. `state-map --symbol <EnumName>` → values, writers, readers
2. `refs --symbol <EnumName>` → all references
3. Edit, re-run `state-map` to confirm coverage

**Dead-code check:**
1. `orphan` → scan candidates
2. Focus on ≥70 confidence; verify each with `refs`
3. Check for dynamic references before any deletion

## Critical traps

- **`check` reports `unknown`** → no diagnostic tool ran. This is *not* passing — it means the project type was detected but nothing verified it. Investigate.
- **`check` reports failure** → any non-skipped tool returned non-zero. Treat as failed even if no structured issue was parsed.
- **`verify --quick` shows no changed files** → cannot assess risk. Stage or commit changes, then use full `verify`.
- **`verify` says "SKIPPED"** → state the limitation explicitly in your completion report — do not treat as passing.
- **`verify` reports contract risk warnings** → address each one before claiming completion. They flag API/signature/state mismatches the graph detected.
- **Orphan high-confidence (≥70)** → still requires `refs` verification. The graph cannot see string dispatch, reflection, macros, or config-driven routing.
- **`cache save` must run *before* target edits** — `diff`/`verify --with-diff` need a pre-edit baseline. Missing baseline is not proof of safety.

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
