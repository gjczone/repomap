# Repomap Boundary Log

This file records boundaries, issues, and optimization opportunities discovered while using `repomap`. All items use checkbox format: `[x]` = resolved, `[ ]` = pending.

## Scope

This log governs optimization of the following files:

| # | File | Type |
|---|------|------|
| 1 | **repomap CLI binary** (`src/`) | Upstream — CLI behavior |
| 2 | **AGENTS.md** | Upstream — project rules |
| 3 | **SKILL.md** (`skills/repomap/SKILL.md`) | Upstream — agent decision procedure, distributed to users |

When an optimization item is resolved, the fix may touch one or more of these files. Mark the item `[x]` only after verifying the fix across all relevant files.

## Rules

- **SKILL.md changes must first be logged here** — do not modify without a corresponding entry
- **AGENTS.md changes must first be logged here** — same rule
- Entries are triaged into CLI fixes (file 1) and SKILL/AGENTS content fixes (files 2-3)
- Mark `[x]` only after the fix is verified

## Format

### Pending items (multi-line)

New discoveries use the full multi-line format from SKILL.md's `## Optimization Feedback` section (the authoritative source):

```markdown
- [ ] **Short name**
  - Discovery scenario: [What task exposed this boundary]
  - Current behavior: [What `repomap` actually did]
  - Expected behavior: [What would have been ideal]
  - Impact: [How this affects AI agent workflows]
```

### Resolved items (single-line archive)

When a fix is verified, compress the entry to one line and mark `[x]`:

```markdown
- [x] **Short name** — Brief outcome
```

Example:
```markdown
- [x] **doctor does not support --project** — doctor rejects --project, violating always-pass-project rule
```

No extra fields. The full history lives in git log.

## 2026-05-06 — Cross-project test batch (deeper-web / DeepSeek-TUI / bi-next)

### Resolved

- [x] **SKILL.md Optimization Feedback -> AI agent language** — Rewrote section with AI agent perspective, all fields updated
- [x] **ai/AGENTS.md 8.2 -> deduplicate + reference SKILL.md** — Replaced duplicated content with reference to SKILL.md as authority
- [x] **optimize.md itself -> AI agent perspective** — Header and all Impact fields rewritten

### Resolved -- repomap CLI fixes

- [x] **doctor does not support --project** — Added --project parameter for consistency, doctor accepts but ignores it
- [x] **file-detail defaults to 12 symbols, truncating large files** — Dynamic adjustment based on file symbol count
- [x] **call-chain ambiguous error has no --file-path hint** — Added explicit hint with example command
- [x] **orphan empty result gives no fallback guidance** — Added guidance to lower --min-confidence threshold
- [x] **verify --quick all SKIPPED when no git changes** — Added next-step suggestions for both quick and full modes
- [x] **check --with-lsp skips LSP when no file arg given** — Documented --modified-file requirement in SKILL.md
- [x] **query role classification is path-only, inaccurate** — Enhanced with symbol-level role info (core/model/service)
- [x] **Large Rust project check runs 59s with no progress feedback** — Added progress banner for cargo check

### Resolved -- SKILL.md content fixes

- [x] **refs --with-lsp / check --with-lsp LSP behavior not documented** — Added note in Command selection table
- [x] **Boundaries section contains implementation details unsuitable for AI agent** — Moved to references/implementation-details.md
- [x] **LSP and Claude Code plugins section references Claude-specific plugins** — Generalized to "editor plugins"
- [x] **cache save / diff decision rules not practically used** — Kept as-is, rules are clear and practical
- [x] **References section files are outside workspace, hard to read** — Using relative paths, format is correct
- [x] **Optimization Feedback write path is outside workspace** — Added environment variable override (REPOMAP_OPTIMIZE_LOG)

### Resolved -- ai/AGENTS.md content fixes

- [x] **8.1 Repomap Core Usage does not distinguish primary/secondary agent** — Added differentiated guidance section
- [x] **LSP and Claude Code plugins section references Claude-specific plugins** — Simplified to "LSP"
- [x] **Optimization Feedback write path is outside workspace** — Added environment variable override (REPOMAP_OPTIMIZE_LOG)

### Resolved -- SKILL.md content fixes

- [x] **cache save / diff decision rules not practically used** — Removed from decision rules, already covered in Boundaries section
- [x] **References section files are outside workspace, hard to read** — Added exec_shell cat read method note

### Not bugs

- [x] **orphan found no dead code in deeper-web** — project genuinely has no dead code after optimization, empty result is correct
- [x] **Claude Code LSP plugins section** — primary agent is Claude Code, this is fine for the main use case
