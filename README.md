# RepoMap — Repository Intelligence for AI Coding Agents

> **One-liner: A CLI tool that gives AI agents (Claude Code, Codex, OpenCode) a "project map" — so they know what to read, what a change affects, and what to verify — before and after editing code.**

[中文 README](README.zh-CN.md)

`repomap` is a CLI tool. AI agents invoke it via skill to get structured repository-level context:

- **Before editing**: where are the entry points? Which files match a keyword? What does a change affect? How risky is it? What should I read first?
- **After editing**: what files changed? What's the risk level? Which tests should I run? Are there diagnostics issues?

It doesn't modify code or replace tests. It does the thing AI agents need most: **high-signal structural context in one command**, so they stop reading irrelevant files and guessing impact.

---

## Install

Copy this to your AI agent (Claude Code, Cursor, or any agent with shell access):

```
Install repomap for me:

1. Clone the skill:
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. Download binary (Linux x86_64):
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/raw/main/dist/repomap
   chmod +x ~/.local/bin/repomap

3. Verify:
   repomap doctor

If ~/.local/bin is not on PATH:
   export PATH="$HOME/.local/bin:$PATH"
```

> Manual install: clone → `cp skills/repomap ~/.claude/skills/` → download binary → done.

---

## Typical Usage

### Before editing: understand → assess → plan

```bash
# First contact: get project structure
repomap overview --project /path/to/project

# Search by business keyword
repomap query --project /path/to/project --query "auth token refresh"

# Inspect a file
repomap file-detail --project /path/to/project --file-path src/auth/login.ts

# Assess impact before changing
repomap impact --project /path/to/project --files src/auth/login.ts --with-symbols
```

### After editing: verify → confirm

```bash
# Quick risk check
repomap verify --project /path/to/project --quick

# Full verification (with compiler/linter diagnostics)
repomap verify --project /path/to/project
```

---

## All Commands

| Command | Purpose |
|---------|---------|
| `overview` | Project map: entry points, hotspots, key symbols, reading order |
| `query --query <keywords>` | Keyword search when you don't know file names |
| `file-detail --file-path <file>` | Symbols, signatures, and importance in a file |
| `impact --files <files> --with-symbols` | Pre-edit: affected files, risk, suggested tests |
| `call-chain --symbol <name>` | Trace callers and callees |
| `query-symbol --symbol <name>` | Exact/fuzzy symbol lookup |
| `refs --symbol <name>` | Find symbol references |
| `verify` | Post-edit: changed files, risk, diagnostics, suggested tests |
| `verify --quick` | Quick risk check (skip compiler/LSP) |
| `check` | Language diagnostics: tsc / cargo check / ruff / mypy / go vet |
| `routes --json` | HTTP API route inventory |
| `orphan` | Dead-code candidate detection |
| `lsp doctor` | Check local LSP server availability |
| `diagnostics --source lsp --files <files>` | LSP diagnostics for specific files |

---

## Supported Languages

Python, JavaScript / TypeScript (including TSX), Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON

---

## Origin

`repomap`'s name and core idea come from **[aider](https://github.com/Aider-AI/aider)**. aider's author Paul Gauthier pioneered the concept of using tree-sitter + PageRank to give CLI AI agents codebase awareness — and proved the counterintuitive insight that a compact structural map often outperforms large amounts of raw code for agent understanding.

We keep the "repo map" name to honor that origin, while evolving `repomap` as an independent MIT-licensed project. The core engine is being rewritten in Rust and [submitted as a PR](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap) to [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) as the built-in tool **deepmap**.

This project was built by a non-professional developer with the help of AI coding assistants.

---

## License

MIT — [LICENSE](./LICENSE)
