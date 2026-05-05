# RepoMap Skill — AI Agent Repository Intelligence

## Purpose

This skill provides AI coding agents with structured repository intelligence through the `repomap` CLI tool. It replaces blind `grep`/`find` exploration with a project map: key files, symbol relationships, change impact, test suggestions, risk assessment, and optional local LSP diagnostics.

## When to Use

Invoke `repomap` proactively when the task involves:
- Entering an unfamiliar codebase → `overview`
- Finding code by business topic → `query`
- Inspecting a known file → `file-detail`
- Assessing edit impact before changing code → `impact --with-symbols`
- Verifying changes before handoff → `verify`
- Searching for a specific symbol → `query-symbol` / `call-chain` / `refs`

## Available Commands

| Command | Purpose |
|---|---|
| `overview` | Project map: modules, entry points, reading order, key symbols |
| `query -q keyword` | Topic/feature discovery by business words |
| `file-detail --file-path <f>` | Symbols and structure of a known file |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius + edit planning |
| `query-symbol --symbol <name>` | Exact/fuzzy symbol lookup |
| `call-chain --symbol <name>` | Caller/callee context |
| `refs [--symbol <name>] [--with-lsp]` | Reference discovery |
| `verify [--quick] [--with-lsp] [--with-diff]` | Post-edit evidence gate |
| `check` | Compiler/type/lint diagnostics |
| `routes [--json]` | HTTP/API route inventory |
| `orphan [--json]` | Dead-code candidate discovery |
| `hotspots` | Dense-file complexity inventory |
| `cache save` | Save graph baseline for later `diff` |
| `diff` | Graph comparison against saved baseline |
| `lsp doctor` | Inspect local LSP server availability |

## Execution

Binary mode (preferred when installed):
```bash
repomap <command> --project <absolute-path>
```

Source mode (development):
```bash
uv run python -m repomap_cli <command> --project <absolute-path>
```

Always pass `--project` as an absolute path when invoked from AI agent context.

## Workflow Patterns

### First touch / unfamiliar repository
```bash
repomap overview --project /path/to/project
repomap query --project /path/to/project --query "auth"
```

### Plan an edit
```bash
repomap file-detail --project /path/to/project --file-path src/foo.ts
repomap impact --project /path/to/project --files src/foo.ts --with-symbols
```

### Verify after edits
```bash
repomap verify --project /path/to/project
```

### Quick risk-only check
```bash
repomap verify --project /path/to/project --quick
```

## Constraints

- No auto-install of tools or LSP servers
- No background daemons
- No MCP server dependency
- `verify` does not run project tests automatically
- Not a replacement for real test suites
