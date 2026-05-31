---
name: repomap
description: "MUST invoke this skill for ANY task that touches code — before reading, editing, searching, debugging, refactoring, navigating, understanding, deleting, renaming, or moving code. Before every grep, every find, every file read, every edit, every symbol lookup, every impact check, every dependency question. After every edit for verification. Also invoke for: investigating bugs, planning features, reviewing PRs, assessing dead code, mapping API routes, tracing call chains, checking diagnostics, auditing state machines. The only exceptions: single-character typo fixes, pure prose/doc edits with zero code impact, and purely non-coding conversations. When in doubt whether to invoke, invoke it."
---

# repomap

`repomap` is a CLI tool that builds a codebase graph (tree-sitter AST → symbols → dependencies → PageRank) and answers structural questions faster than grep + raw reads.

## Usage

```bash
repomap <command> [--project <path>] [options]
```

**`--project` is optional.** If not specified, repomap auto-detects the git root directory.

## Core Rules

1. **Repomap first, grep later.** Use repomap commands instead of grep/find for code understanding.
2. **TIMEOUT: 120 seconds minimum.** Every bash command invoking repomap MUST use timeout ≥120s.
3. **LSP is automatic.** `query --symbol`, `call-chain`, `query --file`, `verify`, and `check` use LSP when available.
4. **`verify` after edits.** Run after every non-trivial code change.
5. **`impact` before edits.** Run before non-trivial edits to assess blast radius.

## Commands

| Situation | Command | Notes |
|-----------|---------|-------|
| Project overview | `overview` | Modules, entrypoints, reading order, hotspots |
| Find symbol | `query --symbol <name>` | LSP precision, state map for enums |
| Call flow | `call-chain --symbol <name>` | Callers, callees, references |
| Topic search | `query --query <keyword>` | Synonym expansion, relevance ranking |
| BM25 search | `query --search <text>` | BM25 symbol ranking |
| Read a file | `query --file <path>` | Symbols, signatures, callers, LSP tree |
| Impact analysis | `impact --files <f...> --with-symbols` | Blast radius, suggested tests; `--compact` concise output; `--top-n <N>` limit files |
| Post-edit verify | `verify` | Git changes, risk, diagnostics, orphan symbols, graph diff; `--risk-threshold HIGH\|MED\|LOW` |
| Quick check | `verify --quick` | Git changes + risk only |
| Lint diagnostics | `check` | Compiler/lint errors |
| Auto-fix | `fix` | ruff --fix, eslint --fix |
| Pre-commit | `ready` | verify + check + format |
| API routes | `routes` | HTTP route inventory |
| Prepare for changes | `cache save` | For verify diff comparison |
| LSP status / setup | `lsp doctor` / `lsp setup` | Detects and installs language servers |
| Health check | `doctor` | Runtime + LSP server availability |

## Value-Added Features (Auto-Enabled)

- **verify** automatically outputs high-confidence orphan symbols (≥70) and graph diff (when baseline exists)
- **query --symbol** automatically outputs state map for enum/const symbols and references
- **call-chain** automatically outputs all references
- **overview** automatically includes hotspot files
- **doctor** automatically outputs LSP server status

## Workflows

**New repo:**
1. `overview` → grasp structure
2. `doctor` → check runtime + LSP availability

**Edit file:**
1. `query --file <f>` → understand before touching
2. `impact --files <f> --with-symbols` → assess blast radius
3. Edit
4. `verify` → evidence gate (includes graph diff)

**Change symbol behavior:**
1. `query --symbol <name>` → find definition + state map
2. `call-chain --symbol <name>` → understand call flow + references
3. Edit
4. `verify`

**Delete code:**
1. `verify` → check orphan symbols (≥70 confidence)
2. Check for dynamic references (string dispatch, reflection, macros)
3. Delete

**API change:**
1. `routes` → route inventory
2. `impact --files <route-file> --with-symbols` → blast radius
3. Edit
4. `verify`

## Diagnostic Decision

| After editing… | Use | Time | What it checks |
|---|---|---|---|
| Just saved a file | `verify --quick` | ~2s | Git changes + risk level only |
| Ready to commit | `verify` | 10-30s | Full evidence: changes, risk, orphan symbols, LSP diagnostics, graph diff |
| Need compiler/lint errors | `check` | varies | Standalone diagnostics (eslint, tsc, ruff, go vet) — no git dependency |
| CI pipeline | `check` + `verify` | varies | Diagnostics first, then full evidence gate |

**Rule**: `verify --quick` after each edit. `verify` before commit. `check` when you need diagnostics independent of git state.

## Critical Rules

- `check` reports `unknown` → no diagnostic tool ran, investigate
- `check` reports `parse_error` (truncated=True) → diagnostic tool ran but output could not be parsed; investigate project config or tool version
- `verify` says "SKIPPED" → state limitation explicitly
- `verify` says "NO_CHANGES" → no git changes detected, cannot assess risk; distinct from SKIPPED
- `verify` reports contract risks → address each before claiming completion

### Session Flags

- **`cache save`**: Run BEFORE a refactoring session. Saves graph baseline so `verify` can show a before/after graph diff. Skip for isolated single-file edits.
- **`--with-co-change`**: Enable for HIGH-RISK edits (changing exported symbols, core modules). Uses git history to find files that are often modified together. Adds 30-60s. Skip for routine edits.
- **`--no-incremental`**: Force a full rescan, ignoring cached data. Use when cache may be stale (switched branches, pulled new commits, getting unexpected results).

## Capabilities

- **Call graph**: Python, TypeScript/TSX, Go, Rust, Java, C#, C++ (tree-sitter, 7 languages)
- **Type inference**: 11 languages (Python, TS/TSX, Go, Rust, Java, Kotlin, Swift, C#, C++, PHP)
- **Parsing**: 17 languages — Python, JS/TS/TSX, Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, Lua, HTML, CSS, JSON, YAML, Bash
- **Git backend**: pygit2 (libgit2) when available, subprocess fallback
- **Search**: BM25 ranking with keyword fallback
- **LSP**: default-on when available, local-only

## Boundaries

- Prefer repomap over grep/reads when repository intelligence reduces uncertainty
- Quote only real CLI output, do not guess
- `verify` requires a Git repository
- Path-taking commands normalize `./...` and absolute paths
