---
name: repomap
description: "MUST invoke this skill before reading, editing, investigating, or deleting code — before every grep, every file read, every edit, and after every edit. The only exceptions are single-character typo fixes and purely non-coding conversations."
---

# repomap

This skill is an AI agent operating procedure for coding work. It is not user-facing product documentation. Use it to decide when and how you should execute `repomap` commands while working in a repository.

`repomap` is a binary CLI already available on `PATH`. Invoke it directly as `repomap ...`, for example `repomap query --project <project> --query <keyword>`.

Always pass `--project` explicitly using the current project directory. Prefer an absolute path supplied by the agent environment over relying on the subprocess cwd. This prevents accidental scans of the user's home directory when the surrounding CLI launches tools from `~`.

In command examples, `<project>` means the absolute path of the current project/workspace.

## Core principle

Use `repomap` as your repository-intelligence entrypoint for AI-agent coding work. Use it before edits, during symbol investigation, after edits, and before final validation when it can reduce uncertainty or save context.

AST graph and repository structure are the default signal. Local LSP diagnostics/definitions/references are opt-in evidence when explicitly useful.

Run only the command needed for the current step. Do not mechanically chain commands.

## Fast default workflow

For most non-trivial coding tasks:

1. Unknown area: `repomap query --project <project> --query <topic>` or `repomap overview --project <project>`.
2. Known file: `repomap file-detail --project <project> --file-path <file>`.
3. Before editing known files: `repomap impact --project <project> --files <file...> --with-symbols`.
4. Before changing a known function/class/method: `repomap query-symbol --project <project> --symbol <name>`, then `repomap call-chain --project <project> --symbol <name>` and/or `repomap refs --project <project> --symbol <name>`. Run `lsp doctor` early; when LSP is available, always add `--with-lsp`.
5. After editing: `repomap verify --project <project>`; add `--with-lsp` for focused local LSP evidence and `--with-diff` when a cache baseline exists.

## Command selection

| Agent situation | Command | Use when |
|---|---|---|
| Installed runtime sanity | `repomap doctor` | Suspect stale binary, parser/runtime issue, or PATH mismatch. |
| Project scan summary | `repomap scan --project <project>` | Need counts, entrypoints, and scan health; usually secondary to `overview`. |
| First repository overview | `repomap overview --project <project>` | Need modules, entrypoints, reading order, hotspots, and lightweight non-AST supporting files; add `--with-heat`/`--with-co-change` only when needed. |
| Topic/feature search | `repomap query --project <project> --query <keyword>` | Know the area but not exact file/symbol names; supports `--paths`, `--exclude`, `--no-tests`, `--json`, `--context-lines <N>`. |
| Dense known file | `repomap file-detail --project <project> --file-path <file>` | Before reading or editing one file; tune `--max-symbols`/`--max-chars` if output is large; add `--with-lsp` for hierarchical LSP symbol tree. |
| Known symbol lookup | `repomap query-symbol --project <project> --symbol <name>` | Need definition candidates; add `--file-path` if ambiguous, `--with-lsp` for local definition/reference evidence. |
| Call flow | `repomap call-chain --project <project> --symbol <name>` | Need callers/callees before behavior change; supports `--direction`, `--depth`, `--file-path`, `--json`. |
| References | `repomap refs --project <project> --symbol <name>` | Need reference edges/callers; add `--file-path` if ambiguous, `--with-lsp` for local LSP evidence. |
| Edit planning | `repomap impact --project <project> --files <file...> --with-symbols` | Best default before non-trivial known-file edits: key symbols, read-next order, affected files, tests, risk, LSP hint. |
| Compact file impact | `repomap impact --project <project> --files <file...>` | Need affected files/tests/risk without edit-planning sections. |
| Current change risk only | `repomap verify --project <project> --quick` | Only need Git changed files, affected files, risk, and suggested tests (skips compiler/LSP checks); requires a Git repository; use full `verify` for final evidence. |
| Final post-edit evidence | `repomap verify --project <project>` | Default after edits; aggregates changed files, risk, suggested tests, contract risk warnings, `check`, optional LSP and graph diff; requires a Git repository. |
| Optional verify LSP | `repomap verify --project <project> --with-lsp` | Need focused local LSP diagnostics for changed files; requires a Git repository. |
| Optional verify graph diff | `repomap verify --project <project> --with-diff` | A `cache save` baseline exists and graph-change evidence matters; requires a Git repository. |
| Diagnostics only | `repomap check --project <project>` | Need compiler/static-analysis evidence without risk aggregation; supports `--with-lsp` for LSP diagnostics; when all tools are skipped, status is `unknown` (not `passed`). |
| Incremental diagnostics | `repomap check --project <project> --modified-file <file>` or `--since-commit <rev>` | Need narrower diagnostics; add `--with-lsp` only for explicit files. |
| Focused LSP diagnostics | `repomap diagnostics --project <project> --source lsp --files <file...>` | Need diagnostics for explicit files without full `verify`; local LSP only, no install/daemon. |
| LSP availability | `repomap lsp doctor --project <project>` | Need to know which local LSP servers can be used; does not install anything. |
| LSP auto-install | `repomap lsp setup --project <project>` | Missing LSP servers detected; supports `--languages`, `--dry-run` to preview install plan first. |
| API route inventory | `repomap routes --project <project> --json` | Need direct HTTP/API route inventory; use `--json` for machine-readable output; add `--with-consumers` to find frontend/client consumers of each route. |
| API consumer mapping | `repomap routes --project <project> --with-consumers` | Need to know which frontend/test files call each API route before changing handlers or response shapes. |
| Hot files | `repomap hotspots --project <project>` | Need dense/complex files first; use sparingly after overview/query. |
| Symbol history | `repomap git-history --project <project> --symbol <name>` | Need commit context for a symbol; add `--file-path` when ambiguous. |
| Orphan candidates | `repomap orphan --project <project>` | Need dead-code candidates with confidence tiers; use `--min-confidence 70` for high-confidence only, `--json` for structured output; verify before deleting. |
| State definition map | `repomap state-map --project <project> --symbol <name>` or `--query <keywords>` | Need state/enum values, writers, and readers before changing lifecycle logic; supports Python/TS/Rust/Go. |
| Graph baseline | `repomap cache save --project <project>` | Low-level preparation before target edits when later `diff`/`verify --with-diff` graph evidence is valuable. |
| Graph diff only | `repomap diff --project <project>` | Advanced graph-only comparison against a pre-edit baseline; prefer `verify --with-diff` for final evidence. |
| Build repomap itself | `repomap build-binary --output <dir>` | Only when maintaining repomap; run source tests and `doctor` before trusting/replacing binary. |

## Decision rules

1. If your task starts in a new or unfamiliar repository: use `overview`.
2. If your task has a feature/topic but no exact file or symbol: use `query`.
3. If your task already names a file: use `file-detail`; use `impact --with-symbols` before non-trivial edits.
4. If your task already names a symbol: use `query-symbol`; add `call-chain` or `refs` before behavior changes.
5. If your task concerns API endpoints/routes: use `routes`; add `--json` for structured smoke tests; use `overview` only when route inventory is part of broader repo understanding.
6. If you changed code, are preparing a handoff, or need final evidence: use `verify`.
7. If you only need changed-file risk without compiler/LSP checks: use `verify --quick`.
8. If you only need toolchain diagnostics: use `check`; for focused LSP diagnostics on explicit files use `diagnostics --source lsp --files ...`.
9. Always run `lsp doctor` early in a project. When a language server is detected, `--with-lsp` is NOT optional — add it to `query-symbol`, `refs`, `file-detail`, `verify`, and `check` for compiler-grade precision. When servers are missing, run `lsp setup --dry-run` to see install plans, then `lsp setup` to install them. LSP is the highest-precision signal repomap provides.
10. If you need recent history for a symbol: use `git-history`.
11. If you need dead-code candidates: use `orphan`; review high (≥70) and medium (40-69) confidence tiers; use `--min-confidence 70` to filter noise; verify each candidate with `refs` before deletion.
12. If you need to understand enum/state lifecycle before changing it: use `state-map --symbol <name>` or `state-map --query <keywords>`.
13. If installed repomap may be stale or unhealthy: use `doctor`.
14. If you are maintaining repomap itself and must rebuild it: use `build-binary`, then smoke-test before replacing PATH.

## Command-specific guidance

### `overview`
Use `overview` for initial orientation only. Do not repeat its content as a summary. After `overview`, use `query` or `file-detail` on the top candidates before editing.

### `query`
Use `query` when the task description does not name a specific file or symbol. After `query`, read the top candidate files before editing. Do not treat `query` results as confirmed implementation locations; treat them as starting points.

### `file-detail`
Use `file-detail` to understand a known file's structure before reading its full content. Add `--with-lsp` to see the hierarchical LSP symbol tree with nested scoping. For non-trivial edits, follow with `impact --with-symbols`.

### `impact`
Use `impact --with-symbols` before non-trivial edits to known files. Read the "Read Next" files before editing. Run the suggested related tests after editing. `impact` does not guarantee completeness; check `routes` and `refs` for cross-boundary relationships when the change touches API, state, or persistence.

### `routes`
Use `routes --json` when the task touches API endpoints, handlers, response shapes, or client code. When changing a route or handler, also check frontend/client consumers and related tests. `routes` filters test DSL noise; use `query` / `file-detail` for mock routes inside tests.

### `verify`
Use `verify` as the default post-edit evidence gate. `verify` does not run tests; run them separately. When `verify` reports contract risk warnings, address each one before final handoff. When `verify` shows "SKIPPED" for diagnostics or graph diff, state the limitation in the completion report. If `verify --quick` shows no changed files, it cannot provide risk assessment; use full `verify` after staging or committing changes.

### `orphan`
Use `orphan` to discover dead-code candidates, not to justify deletion. Always verify high-confidence candidates with `refs` or `query-symbol` before deletion. Check for dynamic references the graph cannot see: string-based dispatch, reflection, macro expansions, config-driven routing, test fixtures. Run the full test suite after any deletion. Never commit a deletion based solely on `orphan` output.

### `refs` and `call-chain`
Use `refs` and `call-chain` before changing a symbol's behavior or signature. When `refs` shows callers in multiple files, inspect each caller before changing the signature. When `call-chain` shows deep chains, focus on direct callers first.

### `check` and `diagnostics`
Use `check` or `diagnostics` when compiler/type/lint evidence is needed. When `check` reports `unknown`, it means no tool ran; do not treat it as passing. When `check` reports failure, investigate before claiming completion.

### `lsp doctor` and LSP evidence
Use `lsp doctor` early — before your first edit in a project — to confirm which language servers are available. When LSP is available, add `--with-lsp` to `query-symbol`, `refs`, `verify`, or `check` for compiler-grade precision on definitions, references, and diagnostics. LSP evidence is especially valuable before refactoring, signature changes, or deleting code. Do not treat LSP as optional when a language server is detected — it is the highest-precision signal repomap can provide.

## Before editing

1. Use RepoMap to locate likely files and compute relationships.
2. Run `lsp doctor` to confirm which language servers are available; use `--with-lsp` on `query-symbol` and `refs` when available.
3. Read the relevant files before editing; do not edit based on RepoMap output alone.
4. When the change touches API, state, or persistence, check `routes --with-consumers` (for API consumers), `state-map --symbol <name>` (for state/lifecycle changes), `refs`, and `call-chain` for cross-boundary relationships.

## After editing

1. Run `verify --with-lsp` as the default post-edit evidence gate. The `--with-lsp` flag adds compiler-grade diagnostics for changed files — use it whenever LSP is available (check with `lsp doctor` first).
2. Address each contract risk warning before final handoff.
3. Run tests separately; RepoMap does not run tests.
4. If `verify` shows missing evidence (e.g., diagnostics skipped), state the limitation in your completion report.

## High-risk operations

- `orphan` output is a candidate list, not a deletion license. Always verify with `refs` and check for dynamic references before deletion.
- Never delete code, drop tables, force-push, or perform other destructive actions based solely on RepoMap output.

## AI-native capability boundary

- Use RepoMap for computed relationships, impact analysis, and risk warnings.
- Use `ls` and file reads for obvious file discovery.
- Use manifests for commands and project-specific verification.
- Use tests/build/lint tools directly for evidence.
- Do not use RepoMap to repeat information the agent can trivially obtain from the filesystem.

## Workflow patterns

### Unknown repository or unfamiliar feature

1. `overview --project <project>` for project map, reading order, and lightweight supporting-file inventory.
2. `query --project <project> --query <topic>` for feature/topic targeting.
3. `file-detail --project <project> --file-path <file>` on the top candidate files before raw reads.
4. `impact --project <project> --files <file...> --with-symbols` if the candidate file may be edited.

### Known file edit

1. `file-detail --project <project> --file-path <file>` to understand local structure.
2. `impact --project <project> --files <file...> --with-symbols` before non-trivial changes.
3. After editing, run `verify --project <project> --with-lsp`; add `--with-diff` when a cache baseline exists.

### Known symbol edit

1. `query-symbol --project <project> --symbol <name>`; add `--file-path` if ambiguous.
2. `call-chain --project <project> --symbol <name>` and/or `refs --project <project> --symbol <name>` before changing behavior.
3. Add `--with-lsp` whenever LSP is available — it adds compiler-grade definition and reference evidence.

### Bug or regression investigation

1. `query --project <project> --query <error/domain>` to find likely files.
2. `query-symbol --project <project> --symbol <name>`, `refs --project <project> --symbol <name>`, or `call-chain --project <project> --symbol <name>` once a symbol is suspected.
3. `git-history --project <project> --symbol <name>` only if recent change context matters and the project is a git repo.
4. Use `check --project <project>` or `verify --project <project>` after a fix.

### API / endpoint change

1. `routes --project <project> --json` for full route inventory.
2. `routes --project <project> --with-consumers` to map every changed route to its frontend/test callers.
3. `impact --project <project> --files <route-file...> --with-symbols` before editing route handlers.
4. `refs --project <project> --symbol <handler-name>` to find all references to the handler.
5. After editing: `verify --project <project> --with-lsp` and review contract risk warnings.

### State / lifecycle change

1. `state-map --project <project> --query <keywords>` or `--symbol <EnumName>` to see all state values, writers, and readers.
2. `refs --project <project> --symbol <EnumName>` for all references across the codebase.
3. After adding/removing a state value: re-run `state-map` to confirm writers and readers are complete.
4. Run tests that cover all state transitions.

### Dead-code investigation

1. `orphan --project <project>` for candidate discovery with confidence tiers.
2. Focus on high-confidence (≥70) tier first; skim medium (40-69) if results are few.
3. Use `--min-confidence 60` to filter out low-confidence noise.
4. For each candidate worth acting on: verify with `refs --project <project> --symbol <name>` or `query-symbol --project <project> --symbol <name>` before deletion.
5. Use `--json` when the agent needs structured output for programmatic analysis.

### Post-edit validation

1. `lsp doctor --project <project>` to confirm LSP availability.
2. `verify --project <project> --with-lsp` as the default final evidence gate (adds compiler-grade diagnostics when LSP is available).
3. Add `--with-diff` when a pre-edit `cache save` baseline exists and graph-change evidence matters.
4. Review contract risk warnings in verify output; address each one before claiming completion.
5. Use `verify --project <project> --quick`, `check --project <project>`, `diagnostics --project <project> --source lsp --files <file...>`, or `diff --project <project>` directly when you need specific evidence instead of the aggregated gate.

## LSP

Keep your LSP plugins enabled when they are working. They do not replace this skill.

Use `repomap` as the workflow and evidence entrypoint: repository map, impact planning, risk review, and `verify` final gate. Use LSP as underlying IDE-like precision tools for definition, reference, and diagnostic help.

When you need LSP evidence inside the repomap workflow, choose repomap's opt-in LSP paths: `repomap query-symbol --project <project> --symbol <name> --with-lsp`, `repomap refs --project <project> --symbol <name> --with-lsp`, `repomap check --project <project> --with-lsp`, or `repomap verify --project <project> --with-lsp`. Do not treat plugin presence as a substitute for `impact` or `verify`.

## Boundaries

- Do not treat this skill as user-facing documentation; it is an AI-agent procedure for choosing and executing repository-intelligence commands.
- Do not skip `repomap` just because direct file reads or grep feel faster; use it wherever repository intelligence would improve the task.
- Skip repomap for tiny exact-file edits, pure prose edits, generic programming questions, or tasks where code relationships/tests/diagnostics clearly do not matter.
- Quote only real CLI output. Do not fabricate results.
- If `repomap ...` fails, report the failure reason. Do not guess. Fall back to normal search/file reads only after reporting the failure.
- Do not mechanically chain `scan`, `overview`, and `hotspots`; run only what the current step needs.
- Always pass `--project <project>` from the current project/workspace when calling repomap from an agent. Do not rely on implicit cwd in agent/tool environments.
- `verify` is the default post-edit evidence gate. It does not run project tests automatically; run real tests separately when required by the task.
- `check` is a validation gate: if any non-skipped underlying tool has `exit_code != 0`, treat the report as failed even when no structured issue is parsed. When ALL diagnostic tools are skipped (no actual tools ran, always reported with `tools_run=0`), the report status is `unknown`, not `passed` — this means the project type was detected but no diagnostic tool was present to verify it.
- `verify` and `verify --quick` use `git status --porcelain` and `git rev-parse --show-toplevel`; both require a Git repository and will fail clearly in non-Git projects. Staged, unstaged, untracked, and rename paths must not be truncated.
- `cache save` is a low-level baseline-preparation command and must be run before target edits; `diff` is an advanced graph-only comparison. Prefer `verify --with-diff` for final handoff evidence. Missing cache baseline should not be treated as proof of safety. `cache load` is not public.
- `orphan` output is tiered by confidence (high/medium/low). Structural elements (module, HTML element, JSON key) are auto-excluded. Never delete solely from this output — use `refs` to verify each high-confidence candidate, and require additional code/business verification before deletion.
- Member-call safety: `obj.method()` must not fall back to unrelated global candidates without evidence. Edges require same-file, explicit import binding, or imported-file evidence.
- JS/TS object literal API methods such as `getMetadata: () => ...` are recognized as named method symbols.
- `.tsx` uses the dedicated TSX parser. Import dependency extraction should use module source strings, not imported symbol names.
- Path-taking commands normalize `./...` and absolute in-project paths and must reject paths outside the project.
- `routes` is a production HTTP/API route inventory. It filters common test/e2e/spec DSL noise such as Playwright `test.describe`, `console.log`, and ordinary Array/Option calls; use `query` / `file-detail` for mock routes inside tests.
- `overview` is primarily a source-symbol graph. Its "supporting files (non-AST)" section is a lightweight inventory of docs/scripts/config only; it does not parse Markdown/shell/service files and does not replace `AGENTS.md`/`CLAUDE.md` context.
- LSP support is opt-in and local-only: repomap checks project-local executables, PATH, and trusted user tool directories, but does not use plugin/MCP, install servers, bundle servers, run `npx`/`pnpx`/`bunx`, or run a daemon.

## References

Read only when needed. These files are in the skill directory (`~/.agents/skills/repomap/references/`), which may be outside the current workspace. Use `exec_shell cat` to read them:

- `references/command-map.md`: complete command inventory, options, and deterministic agent command mapping.
- `references/prompt-examples.md`: natural-language user phrasing examples mapped to agent actions; use only when intent-to-command mapping is unclear.
- `references/authoring-checklist.md`: checklist for maintaining this skill.
