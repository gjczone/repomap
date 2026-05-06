# RepoMap — Repository Intelligence for AI Coding Agents

> **A CLI tool that gives AI agents (Claude Code, Codex, OpenCode) a "project map" — so they know what to read, what a change affects, and what to verify — before and after editing code.**
>
> Inspired by [aider](https://github.com/Aider-AI/aider)'s repo map concept.

[中文 README](README.zh-CN.md)

`repomap` is a CLI tool distributed as a skill + binary. AI agents invoke it to get structured repository-level context instead of guessing via `grep` + raw file reads:

- **Before editing**: entry points, keyword-to-file mapping, change impact, risk level, suggested reading order
- **After editing**: changed files, risk assessment, suggested tests, compiler/linter diagnostics

It doesn't modify code. It doesn't replace tests. It does the one thing CLI agents historically lacked: **high-signal structural context in a single command**.

---

## Install

Copy this to your AI agent:

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

> Manual: clone → `cp skills/repomap ~/.claude/skills/` → download binary → done.

---

## Typical Usage

### Before editing

```bash
# First contact: get project structure
repomap overview --project /path/to/project

# Search by business keyword (when you don't know file names)
repomap query --project /path/to/project --query "auth token refresh"

# Inspect a file before reading it
repomap file-detail --project /path/to/project --file-path src/auth/login.ts

# Assess impact before changing a file
repomap impact --project /path/to/project --files src/auth/login.ts --with-symbols

# Trace who calls a function and what it calls
repomap call-chain --project /path/to/project --symbol refreshToken
```

### After editing

```bash
# Quick: changed files + risk + suggested tests
repomap verify --project /path/to/project --quick

# Full: above + compiler/linter diagnostics + optional LSP
repomap verify --project /path/to/project
```

---

## All Commands

| Command | Purpose |
|---------|---------|
| `overview` | Project map: entry points, hotspots, key symbols (by PageRank), reading order |
| `query --query <keywords>` | Topic/keyword search across paths, filenames, and symbols |
| `file-detail --file-path <file>` | All symbols in a file: signatures, visibility, PageRank scores |
| `impact --files <files> --with-symbols` | Pre-edit blast radius: affected files, key symbols, risk level, suggested tests |
| `call-chain --symbol <name>` | Callers and callees of a symbol, sorted by importance |
| `query-symbol --symbol <name>` | Exact or fuzzy symbol lookup (shows definition location) |
| `refs --symbol <name>` | All references to a symbol (opt-in LSP for precise results) |
| `verify` | Post-edit gate: git changes, risk, diagnostics, suggested tests |
| `verify --quick` | Post-edit risk-only (skips compiler/LSP, faster) |
| `check` | Language diagnostics: tsc, cargo check, ruff, mypy, go vet |
| `routes --json` | HTTP API route inventory (FastAPI, Express, Axum, Spring Boot) |
| `orphan` | Dead-code candidate detection with confidence tiers |
| `lsp doctor` | Check locally installed LSP servers (typescript, pyright, rust-analyzer, gopls) |

---

## Supported Languages

| Status | Languages |
|--------|-----------|
| Built-in | Python, JavaScript, TypeScript (TSX), Go, Rust, HTML, CSS, JSON |
| Optional | Java, Kotlin, Swift, C/C++, C#, PHP, Ruby (install extra tree-sitter bindings) |
| LSP (opt-in) | TypeScript, Python, Rust, Go (requires local language server) |

---

## Origin

`repomap`'s name and core idea come from **[aider](https://github.com/Aider-AI/aider)**. aider's author Paul Gauthier pioneered "repo mapping" — using tree-sitter + PageRank to give CLI AI agents codebase awareness. He proved a counterintuitive insight: a compact structural map often outperforms large amounts of raw code for agent understanding. We keep the "repo map" name to honor that origin.

`repomap` extends the concept: 15 languages, incremental scanning, pre-edit impact analysis, post-edit verification, and optional local LSP integration. Built as an independent MIT-licensed project by a non-professional developer with the help of AI coding assistants.

---

## Related Projects

- **[DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI)** — `deepmap` (Rust port of `repomap`'s engine, [PR submitted](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap))
- **[aider](https://github.com/Aider-AI/aider)** — the original CLI repo mapping pioneer

---

## License

MIT — [LICENSE](./LICENSE)
