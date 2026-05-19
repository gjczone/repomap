# RepoMap — Codebase Awareness for Coding Agents

> Tree-sitter project maps, 13-language LSP, pre/post-edit impact analysis — for Claude Code, Cursor, Codex, OpenCode.
>
> Inspired by [aider](https://github.com/Aider-AI/aider)'s repo map. Built by [@gjczone](https://github.com/gjczone) with deepseek-v4-pro and glm-5.1.

[中文 README](README.zh-CN.md)

**What agents get**: structured repo context instead of grep + raw reads:

- **Where to start**: `overview`, `query` (synonym expansion), `routes`
- **What will break**: `impact`, `call-chain`, `refs`, `state-map`
- **What was missed**: `verify` (contract risk warnings), `check`, `orphan`

---

## Quick Start

### Option 1: CLI + Skill

The skill file tells the agent *when* to call each command. Works with any coding agent that supports custom skills.

```bash
# 1. Clone the skill
mkdir -p ~/.claude/skills
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
rm -rf /tmp/repomap-install


# 2. Install CLI

npm install -g repomap-bin
# or: pip install repomap-cli
# or: uv tool install repomap

# 3. Verify
repomap doctor
```

**Result**: The agent reads `~/.claude/skills/repomap/SKILL.md` and automatically calls `repomap overview`, `repomap impact`, `repomap verify` at the right moments. The skill includes decision rules and mandatory usage patterns.

### Option 2: CLI Only

Install the CLI tool directly for manual use or integration with any workflow:

```bash


npm install -g repomap-bin
# or: pip install repomap-cli

# Verify
repomap doctor
```

---

### LSP Setup

Adds compiler-grade precision for symbol lookups. The agent handles this automatically:

```bash
repomap doctor --lsp                 # check available servers
repomap lsp setup --dry-run          # preview install plan
repomap lsp setup                    # install missing servers
```

| Language | Server | Install |
|----------|--------|---------|
| Python | `pyright` | `npm install -g pyright` |
| TypeScript / JS | `typescript-language-server` | `npm install -g typescript-language-server typescript` |
| Rust | `rust-analyzer` | `rustup component add rust-analyzer` |
| Go | `gopls` | `go install golang.org/x/tools/gopls@latest` |
| C / C++ | `clangd` | `apt install clangd` / `brew install llvm` |
| C# | `csharp-ls` | `dotnet tool install -g csharp-ls` |
| Java | `jdtls` | mason or manual |
| Lua | `lua-language-server` | `npm install -g lua-language-server` |
| PHP | `intelephense` | `npm install -g intelephense` |
| Ruby | `ruby-lsp` | `gem install ruby-lsp` |
| Swift | `sourcekit-lsp` | bundled with Xcode / Swift toolchain |
| Kotlin | `kotlin-language-server` | mason or manual |

LSP-backed commands use local LSP servers by default when available. All commands still work without LSP; missing servers are reported as skipped, and `--no-lsp` disables LSP evidence when needed.

---

## Commands

| Command | Purpose |
|---------|---------|
| `overview` | Project map: entry points, hotspots, key symbols (PageRank), reading order |
| `query --query <keywords>` | Topic search with synonym expansion; `--context-lines <N>` for matched code |
| `search --query <text>` | BM25 semantic symbol search; `--top-k <N>` for result count |
| `file-detail --file-path <f>` | File symbols + signatures; LSP symbol tree by default when available |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius: key symbols, affected files, risk, suggested tests |
| `call-chain --symbol <name>` | Callers and callees with configurable depth |
| `query-symbol --symbol <name>` | Exact/fuzzy symbol lookup; LSP hover + definition/reference evidence by default |
| `refs --symbol <name>` | All references to a symbol; LSP precision by default when available |
| `routes [--json] [--with-consumers]` | HTTP/API route inventory (FastAPI, Express, Axum, Spring Boot) |
| `state-map --symbol <name>` | Enum/const state values, writers, readers |
| `verify [--quick] [--no-lsp] [--with-diff]` | Post-edit evidence gate: git changes, risk, diagnostics |
| `check [--no-lsp]` | Compiler/type/lint diagnostics (tsc, mypy, ruff, cargo check, go vet) |
| `orphan [--json]` | Dead-code candidates with confidence tiers |
| `hotspots` | High-density files ranked by complexity |
| `doctor [--lsp]` | Health check: parsers, runtime, LSP availability |
| `lsp setup [--dry-run]` | Auto-install missing LSP servers for detected languages |

---

## Agent Workflow

The agent follows this pattern automatically (guided by the skill instructions):

```bash
# Before editing
repomap overview --project .                          # first contact
repomap query --project . --query "auth token"        # find by keywords
repomap file-detail --project . --file-path src/auth/login.ts
repomap impact --project . --files src/auth/login.ts --with-symbols
repomap routes --project . --with-consumers           # API consumer map
repomap call-chain --project . --symbol refreshToken

# After editing
repomap verify --project .                            # full evidence gate; LSP runs by default when available
repomap check --project .                             # compiler diagnostics
repomap orphan --project . --min-confidence 70        # dead code check
```

---

## Origin

`repomap`'s core idea comes from **[aider](https://github.com/Aider-AI/aider)** — tree-sitter + PageRank for coding-agent codebase awareness. LSP integration patterns draw from **[serena](https://github.com/oraios/serena)**, including server auto-detection, search result formatting, and hierarchical symbol indexing.

---

## License

MIT — [LICENSE](./LICENSE)
