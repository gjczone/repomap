# Repomap Command Map

This is an agent command-selection reference. Use it when you already know repository analysis is needed, but need deterministic selection among `repomap` commands. It intentionally lists every public `repomap` command.

Do not read this as user-facing product documentation. Convert the current task state into an agent action: what you need to know before editing, validating, explaining, or handing off.

## Command Inventory

| Command | Main purpose | Key options | Default use |
|---|---|---|---|
| `overview` | AI-friendly repository overview: reading order, modules, entry points, routes, hotspots, key symbols, and lightweight non-AST supporting files. | `--project`, `--max-files`, `--max-chars`, `--json`, `--with-heat`, `--with-co-change`, `--granularity` | First look at an unfamiliar repo. |
| `query` | Topic/feature search by keyword across paths, filenames, symbols. | `--query`, `--paths`, `--exclude`, `--max-files`, `--max-symbols`, `--no-tests`, `--json` | You know the area but not files/symbols. |
| `file-detail` | File-level structure summary. | `--file-path`, `--max-symbols`, `--max-chars` | Before reading/editing a known dense file. |
| `impact` | File-level change impact and edit planning. | `--files`, `--json`, `--max-files`, `--with-symbols` | Best default before non-trivial known-file edits. |
| `query-symbol` | Find symbol definitions/candidates. | `--symbol`, `--file-path`, `--max-chars`, `--with-lsp`, `--lsp-timeout` | You know a function/class/method name. |
| `call-chain` | Traverse callers/callees around a symbol. | `--symbol`, `--file-path`, `--direction`, `--depth`, `--max-chars`, `--json` | Before behavior changes; understand flow. |
| `refs` | Reference/caller evidence for a symbol or repository references. | `--symbol`, `--file-path`, `--json`, `--with-lsp`, `--lsp-timeout` | Understand who uses a symbol. |
| `verify` | Post-edit evidence gate; `--quick` is risk-only mode for current Git changes. | `--json`, `--types`, `--max-issues`, `--no-symbols`, `--with-lsp`, `--lsp-timeout`, `--lsp-max-files`, `--with-diff`, `--quick` | Default after edits/before final handoff. |
| `check` | Compiler/static-analysis diagnostics. | `--types`, `--max-issues`, `--since-commit`, `--modified-file`, `--no-symbols`, `--with-lsp`, `--lsp-timeout`, `--lsp-max-files` | Diagnostics-only evidence; add `--with-lsp` for explicit files. |
| `routes` | Direct HTTP/API route inventory. | `--project`, `--max-files`, `--json` | Endpoint listing tasks; `routes --json` for machine-readable smoke tests. |
| `diagnostics` | Focused diagnostics for explicit files. | `--project`, `--source lsp`, `--files`, `--json`, `--lsp-timeout` | Explicit-file LSP diagnostics without full `verify`. |
| `lsp doctor` | Detect local LSP servers. | `--project`, `--json` | Check LSP availability without installing/daemonizing. |
| `hotspots` | Dense/high-complexity files. | `--limit` | Find likely complex files; use after overview/query if needed. |
| `git-history` | Symbol commit history. | `--symbol`, `--file-path`, `--max-files` | Historical/ownership context for a symbol. |
| `orphan` | Dead-code candidates with confidence tiers. | `--project`, `--max-files`, `--json`, `--limit`, `--min-confidence` | Verify each candidate before deletion. |
| `cache save` | Prepare a graph baseline before target edits. | `--project`, `--max-files` | Low-level baseline prep for later `diff` or `verify --with-diff`. |
| `diff` | Advanced graph-only comparison against a saved baseline. | `--project`, `--max-files`, `--json`, `--baseline`, `--current` | Use when graph diff itself is the question. |
| `scan` | Scan summary: file/symbol/edge counts, entrypoints, hotspots. | `--project`, `--max-files` | Secondary health/summary check; prefer `overview` for AI reading. |
| `doctor` | Runtime/parser/binary sanity. | none | PATH binary stale or parser issue suspected. |
| `build-binary` | Build repomap executable. | `--output`, `--name` | Only when maintaining repomap itself. |

## Deterministic Decision Rules

1. If your task starts in a new or unfamiliar repo -> `overview`.
2. If your task has a feature/topic but no known file -> `query`.
3. If your task names a file -> `file-detail`; before non-trivial edits also run `impact --with-symbols`.
4. If your task names a symbol -> `query-symbol`; before behavior change use `call-chain` and/or `refs`.
5. If your task concerns API endpoints/routes -> `routes`; use `overview` only for broader repo context.
6. If edits are already done or final handoff evidence is needed -> `verify`.
7. If only changed-file risk without compiler/LSP checks is needed -> `verify --quick`.
8. If only compiler/lint/type diagnostics are needed -> `check`; for explicit-file LSP diagnostics use `diagnostics --source lsp --files ...`.
9. If only LSP availability is needed -> `lsp doctor`.
10. If commit/history context is needed -> `git-history`.
11. If likely dead-code candidates are needed -> `orphan`; focus on high (≥70) and medium (40-69) confidence tiers; use `--min-confidence 70` to filter noise; verify with `refs` before deletion.
12. If graph diff evidence will matter later -> run `cache save` before target edits; after edits use `diff` or `verify --with-diff`.
13. If the installed binary/parser/runtime may be stale -> `doctor`.
14. If maintaining the repomap binary -> source tests, `build-binary`, new binary `doctor`, then PATH validation.

## Common Calling Patterns

### First Look

```bash
repomap overview --project <project>
```

Use `--with-heat` for recent-change markers. Use `--with-co-change` only when Git coupling history matters because it is heavier. The supporting-file section is an inventory only; read `AGENTS.md`/`CLAUDE.md` directly when injected context matters.

### API Route Inventory

```bash
repomap routes --project <project>
```

Prefer `routes` over generic `overview` when the user asks for endpoints/routes.

### Topic Search Then Edit Plan

```bash
repomap query --project <project> --query "auth token"
repomap file-detail --project <project> --file-path <top-match>
repomap impact --project <project> --files <top-match> --with-symbols
```

### Known File Edit

```bash
repomap file-detail --project <project> --file-path <file>
repomap impact --project <project> --files <file> --with-symbols
```

### Known Symbol Behavior Change

```bash
repomap query-symbol --project <project> --symbol <name> --file-path <file-if-needed>
repomap call-chain --project <project> --symbol <name> --file-path <file-if-needed> --direction both
repomap refs --project <project> --symbol <name> --file-path <file-if-needed>
```

Add `--with-lsp` to `query-symbol` or `refs` only when local LSP evidence is valuable enough to pay startup cost.

### Post-Edit Evidence Gate

```bash
repomap verify --project <project>
```

Useful variants:

```bash
repomap verify --project <project> --quick
repomap verify --project <project> --with-lsp
repomap verify --project <project> --with-diff
repomap verify --project <project> --json
```

`verify` does not run project tests automatically; it suggests tests and reports diagnostic evidence. Use `verify --quick` only for risk without compiler/LSP checks.

### Diagnostics Only

```bash
repomap check --project <project>
repomap check --project <project> --modified-file <file>
repomap diagnostics --project <project> --source lsp --files <file1> <file2>
```

### Baseline And Graph Diff

```bash
repomap cache save --project <project>   # before target edits
repomap diff --project <project>         # after edits, graph-only
repomap verify --project <project> --with-diff
```

`cache save` must happen before target edits. Missing baseline is not evidence of safety. There is no public `cache load` action.

### Runtime / Binary Maintenance

```bash
repomap doctor
repomap build-binary --output <tmpdir>
<tmpdir>/repomap doctor
```

Only replace PATH binary after the new binary passes `doctor` and relevant smoke checks.

## LSP Integration

Claude Code LSP plugins and `repomap` are complementary:

- Keep working Claude Code LSP plugins enabled; they provide IDE-like precision inside the agent environment.
- Use `repomap` as the repository workflow layer: map, query, impact, risk, diagnostics summary, and `verify` evidence gate.
- Use repomap LSP options only when the current step needs LSP evidence recorded in repomap output: `--with-lsp` (on `query-symbol`, `refs`, `check`, or `verify`), `diagnostics --source lsp --files ...`, or `lsp doctor`.
- Do not treat plugin availability as a replacement for `impact --with-symbols` or `verify`.

## Important Semantics

- `overview`: source-symbol graph first; `支撑文件（非符号图）` is a lightweight inventory for docs/scripts/config and does not replace `AGENTS.md`/`CLAUDE.md` context.
- `verify`: default post-edit evidence gate. Aggregates Git changed files, risk, suggested tests, `check`, optional LSP diagnostics, and optional graph diff. Requires a Git repository; fails clearly in non-Git projects. `status=failed` should block completion; `warning` should be reported as incomplete confidence, not hidden.
- `verify --quick`: parses `git status --porcelain` and `git rev-parse --show-toplevel` for risk-only review (skips compiler/LSP checks); requires a Git repository. Staged, unstaged, untracked, and rename paths must be preserved without truncation.
- `check`: if any non-skipped underlying tool exits non-zero, the report is failed even when no structured issue is parsed. Explain the failing tool from the report; do not assume `repomap` itself is broken. When ALL diagnostic tools are skipped (visible as `tools_run=0` in the summary), the report status is `unknown`, not `passed` — no tool actually verified the project.
- `diagnostics`: focused explicit-file diagnostics. Use `--source lsp --files ...`; do not use it as a broad repo scan.
- `impact --with-symbols`: pre-edit planner for known files. It adds key target symbols, ordered read-next guidance, related tests, risk, and local LSP availability hints. It detects LSP availability only; it does not start an LSP server.
- `routes`: production HTTP/API route inventory only. It filters common test/e2e/spec DSL noise (for example Playwright `test.describe`, `console.log`, and ordinary Array/Option calls). Use `file-detail` after it when a specific route file needs deeper symbol inspection; use `query` / `file-detail` for mock routes inside tests.
- `cache save`: low-level baseline prep before target edits. `diff` and `verify --with-diff` read that baseline later.
- `orphan`: confidence-tiered output (high/medium/low); structural noise (module, element, json_key) auto-excluded; each candidate needs `refs` verification before deletion.
- Member calls: `obj.method()` does not use unrelated global fallback targets. It needs same-file, explicit import binding, or imported-file evidence.
- JS/TS object literal API methods: property functions such as `getMetadata: () => ...` are emitted as named method symbols.
- `.tsx`: uses dedicated TSX grammar. Import dependency extraction should use source strings only, not imported symbol names.
- Path safety: commands accepting project files/directories normalize in-project absolute/relative paths and reject outside-project paths.
- LSP: opt-in, local-only, no plugin/MCP, no install, no bundled server, no `npx`/`pnpx`/`bunx`, no daemon.

## Value Guidance

- Primary: `overview`, `query`, `file-detail`, `impact --with-symbols`, `query-symbol`, `call-chain`, `refs`, `verify`, `verify --quick`, `check`, `orphan`.
- Focused: `routes`, `diagnostics`, `lsp doctor`, `hotspots`, `git-history`, `diff`.
- Low-level: `cache save` only.
- Maintenance: `doctor`, `build-binary`.
