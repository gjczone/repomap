# Repomap Prompt Examples

Use this file only when an AI agent needs examples for mapping natural-language phrasing to `repomap` actions. This is a supplemental mapping aid, not the main workflow and not user-facing product documentation. Do not read it on every task; read it only when intent-to-command selection is unclear.

## Table of Contents

- Runtime and binary maintenance: self-check, rebuild
- Repository entrypoints: overview, scan, hotspots, query, supporting files
- File/symbol investigation: file-detail, query-symbol, impact, call-chain, refs, LSP evidence
- Change validation: verify, verify --quick, check, diagnostics, LSP doctor
- Baseline/history/cleanup: cache save, diff, git-history, orphan
- Natural-language examples below use `User phrasing example` only to show possible triggers. The agent action is the instruction to follow.

## Runtime Self-Check

User phrasing example:

`I think repomap is still the old version, can you confirm?`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap doctor`

Why:

- verifies the installed PATH binary is callable
- output should include tree-sitter parsers, including `tsx`
- output should include actual module load paths, which helps detect stale binaries or wrong environments

## Rebuild Repomap Binary

User phrasing example:

`Rebuild the repomap binary and replace the old one`

Agent action:

1. invoke the `repomap` skill
2. run tests for the repomap source project first
3. build to a temporary output directory with `repomap build-binary --output <tmpdir>` or the source command equivalent
4. run `<tmpdir>/repomap doctor`
5. back up the old PATH target, replace it, then run `repomap doctor` again

Important:

- only do this when maintaining repomap itself
- do not overwrite the PATH binary before the new binary passes `doctor`

## First Contact With A Repository

User phrasing example:

`Help me quickly understand this project`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap overview --project <project>`

Why:

- best first-pass repository summary
- gives modules, entrypoints, reading order, hotspots, and a lightweight non-AST supporting-file inventory

## Quick Scan Summary

User phrasing example:

`About how many files and symbols are in this repo?`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap scan --project <project>`

Why:

- `scan` is for counts and scan health
- use `overview` instead if the user wants an explanation of the codebase

## Find Hot / Complex Files

User phrasing example:

`Which files are the most complex and should be looked at first?`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap hotspots --project <project> --limit 20`

Why:

- `hotspots` prioritizes dense files
- use after `overview` or `query` if the task needs complexity prioritization

## Find Code By Feature Area, No Symbol Name

User phrasing example:

`Where is the terminal-related code?`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap query --project <project> --query terminal`

Why:

- your task has a feature area, not exact symbols
- `query` searches paths, filenames, and symbols with keyword scoring
- output includes reading order, test files, and key symbols

## Search Within A Directory

User phrasing example:

`Only look at login-related code inside src/auth`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap query --project <project> --query login --paths src/auth`

Why:

- `--paths` limits search to path segments
- use `--exclude` when the user wants to ignore generated, legacy, or unrelated areas

## Find A Symbol

User phrasing example:

`Help me find where calculate_kpi is`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap query-symbol --project <project> --symbol calculate_kpi`

If multiple candidates appear, add `--file-path <file>` before relying on the result.

## Find A JS/TS Object Literal API Method

User phrasing example:

`Can getMetadata in api.ts be found?`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap query-symbol --project <project> --symbol getMetadata --file-path <api-file>`

Why:

- JS/TS object literal function properties are supported
- example supported shape: `export const api = { getMetadata: () => ... }`

## Understand A File Before Reading It

User phrasing example:

`Don't open the source yet, tell me what's in service.py`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap file-detail --project <project> --file-path service.py`

Why:

- gives local structure before spending context on full source reads

## File-Level Impact Before Editing

User phrasing example:

`If I modify VirtualKeyboard.tsx, which files will be affected?`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap impact --project <project> --files src/components/terminal/VirtualKeyboard.tsx --with-symbols`

Why:

- `impact --with-symbols` gives a file-level edit plan: key symbols, read-next order, who references your symbols, who your symbols call
- includes related tests, risk assessment, and local LSP availability hints
- better first step than `refs` / `call-chain` when the file is known but specific symbols are not
- important: the LSP hint is detection only; use `query-symbol --with-lsp`, `refs --with-lsp`, `check --with-lsp`, or `verify --with-lsp` if exact LSP evidence is needed

## Non-Trivial Known File Edit

User phrasing example:

`I need to modify useChartLinkage.ts, first help me figure out how to approach it`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap file-detail --project <project> --file-path src/hooks/useChartLinkage.ts`
3. choose `repomap impact --project <project> --files src/hooks/useChartLinkage.ts --with-symbols`

Why:

- `file-detail` gives local structure before opening the file
- `impact --with-symbols` gives the edit plan: key symbols, affected files, read-next order, suggested tests, risk, and LSP availability
- this prevents the agent from editing a file without checking callers and tests first

## Understand Impact Of A Specific Symbol

User phrasing example:

`If I modify helper, which areas will be affected`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap query-symbol --project <project> --symbol helper`
3. choose `repomap call-chain --project <project> --symbol helper`
4. if needed, follow with `repomap refs --project <project> --symbol helper`

Why:

- `query-symbol` finds candidates and ambiguity
- `call-chain` explains flow
- `refs` focuses on reference evidence

## Symbol Definition/References With LSP

User phrasing example:

`I need more accurate definition and references for this function, use LSP`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap query-symbol --project <project> --symbol <name> --file-path <file> --with-lsp`
3. or choose `repomap refs --project <project> --symbol <name> --file-path <file> --with-lsp`

Important:

- LSP is opt-in and local-only
- missing LSP server should be reported as skipped, not invented as success

## API Route Inventory

User phrasing example:

`List all the HTTP endpoints in this project`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap routes --project <project>`
3. if a specific route file needs detail, use `repomap file-detail --project <project> --file-path <route-file>`

Why:

- `routes` is the focused HTTP/API route inventory command
- use `overview` only when route inventory is part of broader repo understanding
- use `file-detail` on a specific route file when deeper symbol-level inspection is needed

## Pre-Commit Evidence Gate

User phrasing example:

`I've modified some code, help me check for risks before committing`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap verify --project <project>`

Why:

- detects changed files from `git status --porcelain`
- shows affected files, risk level, and suggested tests
- runs the built-in `check` diagnostics summary
- flags missing evidence before the final handoff

If the task only needs risk without diagnostics, choose `repomap verify --project <project> --quick` instead.

## Validate After Editing

User phrasing example:

`I've just modified some code, check for obvious issues`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap verify --project <project>`

Important:

- `verify` is the default post-edit evidence gate
- it aggregates changed files, risk, suggested tests, `check`, optional LSP diagnostics, and optional graph diff
- it does not run the suggested project tests automatically
- if an underlying tool exits non-zero, explain the failing tool, command, exit code, and report excerpt when present

## Validate After Editing With LSP

User phrasing example:

`I'm done with changes, add LSP diagnostics and review again`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap verify --project <project> --with-lsp`

Important:

- LSP is opt-in and uses only locally available language servers
- missing local LSP servers should be reported as skipped, not invented as passed

## Diagnostics Only

User phrasing example:

`No risk report needed, just run static analysis`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap check --project <project>`

If the user names files or a commit range:

```bash
repomap check --project <project> --modified-file <file>
repomap check --project <project> --since-commit <rev>
```

## Focused LSP Diagnostics

User phrasing example:

`Run LSP diagnostics on only these two files`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap diagnostics --project <project> --source lsp --files <file1> <file2>`

Why:

- this is narrower than `verify --with-lsp`
- use when the user explicitly names files and wants LSP diagnostics only

## LSP Availability

User phrasing example:

`Which language servers are available locally for this project?`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap lsp doctor --project <project>`

Important:

- reports local availability only
- does not install, download, run daemon, or use `npx`/`pnpx`/`bunx`

## Validate With Saved Baseline

User phrasing example:

`I've already cached the baseline, also check structural changes before committing`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap verify --project <project> --with-diff`

Important:

- `--with-diff` needs an existing `cache save` baseline for graph diff evidence
- missing baseline is a skipped graph diff, not a failed verification by itself

## Prepare Graph Baseline Before Edits

User phrasing example:

`Cache the graph baseline before this round of changes`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap cache save --project <project>` before target edits

Important:

- `cache save` is low-level baseline preparation, not a validation result
- saving after edits cannot prove those edits are safe

## Review Graph Change with Baseline

User phrasing example:

`I've already cached it, help me see the structural changes this time`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap diff --project <project>` for graph-only comparison, or `repomap verify --project <project> --with-diff` for final handoff evidence

Important:

- graph comparison requires an existing pre-edit `cache save` baseline
- missing baseline is skipped evidence, not proof of safety
- immediate `repomap cache save --project <project> && repomap diff --project <project>` should show zero graph changes when code is unchanged

## Need Historical Context

User phrasing example:

`Who recently modified this function and why is it error-prone`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap git-history --project <project> --symbol <name>`
3. add `--file-path <file>` if the symbol is ambiguous

## Dead Code Cleanup

User phrasing example:

`Help me find potentially unused code`

Agent action:

1. invoke the `repomap` skill
2. choose `repomap orphan --project <project>`
3. review high-confidence (≥70) tier first; use `--min-confidence 70` to filter
4. for each candidate worth acting on, verify with `refs` or `query-symbol` before deletion
5. use `--json` for structured programmatic analysis

Why:

- output is tiered by confidence (high/medium/low); structural elements (module, element, json_key) are auto-excluded
- do not delete solely from `orphan` — always verify with additional code/business context

## Member Call Safety

User phrasing example:

`Why doesn't session.pty.onData() connect to onData in another file?`

Agent answer:

- member calls such as `obj.method()` do not use unrelated global fallback targets
- `.tsx` files use the dedicated TSX parser; import dependencies should come from module source strings, not imported symbol names
- default `overview` avoids Git co-change history scans; use `--with-co-change` only when coupling history is needed
- a call edge needs same-file, explicit import binding, or imported-file evidence
- this avoids false cross-file links from common method names

## Quick Decision Rules

- if your task starts in a new or unfamiliar repo: use `overview`
- if your task needs scan counts only: use `scan`
- if your task needs complex files: use `hotspots`
- if your task has a feature area but no symbol names: use `query`
- if your task has a known symbol: use `query-symbol`
- if your task has a known file: use `file-detail`
- if your task needs file-level impact before editing: use `impact --with-symbols`
- if your task concerns API routes/endpoints: use `routes`, then `file-detail` on specific route files when needed
- if code changed, handoff is near, or final evidence is needed: use `verify`
- if only changed-file risk without compiler/LSP checks is needed: use `verify --quick`
- if only diagnostics/toolchain output is needed: use `check`; for focused LSP diagnostics use `diagnostics --source lsp --files ...`
- if LSP availability is needed: use `lsp doctor`
- if symbol-level impact is needed: use `call-chain` or `refs`
- if graph changes against a saved baseline are needed: use `diff` for graph-only comparison or `verify --with-diff` for final evidence
- if recent symbol history is needed: use `git-history`
- if dead-code candidates are needed: use `orphan`; focus on high-confidence tier; verify before deletion
- if installed repomap may be unhealthy or stale: use `doctor`
- if maintaining repomap itself requires a rebuild: use `build-binary`, smoke-test, back up the old PATH target, then replace it
