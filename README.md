# RepoMap — Repository Intelligence for AI Coding Agents

> **For Humans** — Copy the prompt below. Paste it into Claude Code, Cursor, or any AI agent with shell access. The agent will install and configure everything automatically.

```
Install repomap for me:

1. Clone the skill to my skills directory:
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. Download the prebuilt binary:
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/raw/main/dist/repomap
   chmod +x ~/.local/bin/repomap

3. Verify:
   repomap doctor

If ~/.local/bin is not on PATH, add it:
   export PATH="$HOME/.local/bin:$PATH"
   (add this line to ~/.bashrc or ~/.zshrc for persistence)
```

> After installation, your AI agent will automatically discover the skill and use `repomap` whenever it needs to understand a codebase — no manual invocation needed.

---

> **For AI Agents** — This section is written for you. Read it once when you start a session.  
> `repomap` is a CLI binary on `PATH`. Invoke it as `repomap <command> --project <absolute_path>`.
> Full command reference: `references/command-map.md`. Quick decision rules below.

## What is RepoMap

`repomap` is a repository intelligence layer for CLI/TUI AI coding assistants. It scans a codebase with tree-sitter, builds a dependency graph, ranks symbols by PageRank, and produces structured reports that help AI agents decide what to read, what to change, and what to verify.

In plain terms: instead of `grep` + `read file` guessing, the agent gets a **project map** — entry points, hotspots, key symbols, call chains, impact analysis, and risk assessment — in a single command.

`repomap` is also integrated into [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) as the built-in Rust tool **deepmap**.

## Quick Install

### Option A: Copy-paste the prompt above to your AI agent (recommended)

The block at the top of this README is designed to be pasted directly into any AI agent. The agent will execute the commands.

### Option B: Manual install

```bash
# 1. Clone the skill
mkdir -p ~/.claude/skills
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/
rm -rf /tmp/repomap-install

# 2. Download binary
mkdir -p ~/.local/bin
curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/raw/main/dist/repomap
chmod +x ~/.local/bin/repomap

# 3. Verify
repomap doctor
```

### Option C: Run from Python source (no binary)

```bash
git clone https://github.com/gjczone/repomap.git
cd repomap
uv run repomap --help
```

## Supported Languages

Python, JavaScript / TypeScript (including TSX), Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON. LSP integration for TypeScript / Python / Rust / Go is available on an opt-in basis.

## Command Overview

| Command | Purpose |
|---------|---------|
| `overview` | First-look project map: entry points, hotspots, key symbols, reading order |
| `query --query <topic>` | Topic/keyword search when you don't know exact file names |
| `query-symbol --symbol <name>` | Exact or fuzzy symbol lookup |
| `file-detail --file-path <file>` | Inspect a file's symbols with signatures and PageRank scores |
| `call-chain --symbol <name>` | Trace callers and callees of a symbol |
| `impact --files <files> --with-symbols` | Pre-edit planning: affected files, key symbols, risk, suggested tests |
| `verify` | Post-edit evidence gate: changed files, risk, diagnostics, suggested tests |
| `verify --quick` | Quick post-edit risk check (skips compiler/LSP) |
| `check` | Run language diagnostics (tsc, cargo check, ruff, mypy, go vet) |
| `routes --json` | HTTP/API route inventory |
| `refs --symbol <name>` | Discover references to a symbol |
| `orphan` | Dead-code candidate detection |
| `cache save` / `diff` | Snapshot and compare graph state |
| `lsp doctor` | Check local LSP server availability |
| `diagnostics --source lsp --files <files>` | Focused LSP diagnostics |

## Origin

`repomap` is inspired by [aider](https://github.com/Aider-AI/aider), which pioneered the idea of using tree-sitter + PageRank to give CLI AI agents codebase awareness. aider proved a key insight: a compact 1K-token structural map often outperforms 50K tokens of raw code for agent understanding.

On aider's shoulders, `repomap` extends the concept with 15-language support, incremental scanning, impact analysis, post-edit verification, LSP integration, and AI-friendly structured reports. Both `repomap` and the upstream `deepmap` Rust engine were built by a non-professional developer with the help of AI coding assistants.

## License

MIT — see [LICENSE](./LICENSE).

## Related Projects

- [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) — `deepmap` is the Rust port of `repomap`'s engine, integrated as a built-in TUI tool
- [aider](https://github.com/Aider-AI/aider) — the original inspiration for repo mapping in CLI environments
