# RepoMap — Codebase Awareness for Coding Agents

> **A CLI tool that gives coding agents structured codebase awareness — where to start reading, what a change breaks, and what to verify — before and after editing code.**
>
> Built for agent harnesses like **Claude Code**, **Cursor**, **Codex**, and **OpenCode**. Two integration modes: **MCP Server** (tools appear in the agent's tool list) or **Skill + CLI** (a skill file guides the agent to call the CLI directly).
>
> Inspired by [aider](https://github.com/Aider-AI/aider)'s repo map concept. Built by [@gjczone](https://github.com/gjczone) vibe coding with [DeepSeek](https://chat.deepseek.com/).

[中文 README](README.zh-CN.md)

Coding agents use repomap to get structured repository context instead of guessing via `grep` + raw file reads:

- **Pre-positioning**: where to start for a task — `overview`, `query` (with synonym expansion), `routes --with-consumers`
- **Prevention**: what will break if I edit this — `impact`, `call-chain`, `refs`, `state-map`
- **Gap detection**: what did I miss after editing — `verify` (with contract risk warnings), `check`, `orphan`

---

## Quick Install

### Step 1: Install the binary (required for both modes)

```bash
npm install -g repomap-bin
repomap doctor   # verify it works
```

### Step 2: Choose your integration mode

| | MCP Server | Skill + CLI |
|---|---|---|
| **How it works** | Tools appear in the agent's tool list. The server injects workflow instructions into the system prompt — the agent decides when to call each tool. | A skill file (`SKILL.md`) tells the agent *when* and *how* to call each CLI command. The agent runs `repomap ...` directly. |
| **Best for** | Claude Code, Cursor, VS Code, or any MCP-compatible harness | Any coding agent that supports custom skills (Claude Code, Codex, OpenCode, etc.) |
| **Setup** | Add 5 lines of JSON config | Clone skill + install binary |

#### Option 1: MCP Server

Add to Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "repomap": {
      "command": "npx",
      "args": ["--force-refresh", "-y", "repomap-mcp-server"]
    }
  }
}
```

Or via CLI: `claude mcp add --transport stdio repomap -- npx -y repomap-mcp-server@latest`

**What happens**: 19 MCP tools appear in the agent's tool list (`repomap_overview`, `repomap_query`, `repomap_impact`, etc.). The MCP server injects mandatory workflow instructions — the agent is told to use these tools BEFORE grep, BEFORE file reads, BEFORE edits, and AFTER edits. Each tool description includes decision rules about when it's MANDATORY vs. optional.

#### Option 2: Skill + CLI

**LLM agent: read the code block matching the user's OS. Execute every step in order.**

<details>
<summary>Linux / macOS / Windows</summary>

```
Install repomap for me:

1. Clone the skill:
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. Install binary (handles platform automatically):
   npm install -g repomap-bin

3. Verify:
   repomap doctor
```
</details>

**What happens**: The skill file at `~/.claude/skills/repomap/SKILL.md` tells the agent exactly when to run each `repomap` CLI command. The skill includes decision rules ("if X then use Y"), workflow patterns, and mandatory usage rules.

---

### LSP setup (optional, the agent handles this)

The agent runs `repomap doctor --lsp` to check language servers, then `repomap lsp setup` to install missing ones automatically:

```
repomap lsp setup --dry-run   # preview install plan
repomap lsp setup             # install missing servers
```

Supported LSP servers (13 languages): `pyright`/`pylsp` (Python), `typescript-language-server` (TS/JS), `rust-analyzer` (Rust), `gopls` (Go), `clangd` (C/C++), `csharp-ls` (C#), `jdtls` (Java), `lua-language-server` (Lua), `intelephense` (PHP), `ruby-lsp` (Ruby), `sourcekit-lsp` (Swift), `kotlin-language-server` (Kotlin).

Without LSP, all commands still work — LSP adds compiler-grade precision for symbol-level lookups.

---

## All Commands

| Command | What it does |
|---------|-------------|
| `overview` | Project map: entry points, hotspots, key symbols (PageRank), reading order, module clusters |
| `scan` | Initial scan summary: file/symbol/edge counts, entry points, scan health |
| `query --query <keywords>` | Topic search with synonym expansion; supports `--context-lines <N>` for matched code display |
| `file-detail --file-path <f>` | All symbols in a file; add `--with-lsp` for hierarchical LSP symbol tree |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius: key symbols, affected files, risk level, suggested tests |
| `call-chain --symbol <name>` | Callers and callees of a symbol with configurable depth |
| `query-symbol --symbol <name>` | Exact or fuzzy symbol lookup; add `--with-lsp` for compiler-grade precision |
| `refs --symbol <name>` | All references to a symbol; add `--with-lsp` for precise cross-file results |
| `routes --json` | HTTP/API route inventory (FastAPI, Express, Axum, Spring Boot) |
| `routes --with-consumers` | Map each route to frontend/test consumers with confidence levels |
| `state-map --symbol <name>` | Enum/const state values, writers, and readers (Python/TS/Rust/Go) |
| `verify` | Post-edit gate: git changes, risk, contract risk warnings, suggested tests, diagnostics, LSP, graph diff |
| `verify --quick` | Post-edit risk-only (skips compiler/LSP, faster) |
| `check` | Language diagnostics: tsc, cargo check, ruff, mypy, go vet |
| `orphan` | Dead-code candidates with confidence tiers and pre-deletion checklist |
| `hotspots` | High-density files ranked by complexity and change frequency |
| `cache save` | Save current graph as baseline for later `diff` or `verify --with-diff` |
| `diff` | Graph comparison against a pre-edit `cache save` baseline |
| `doctor` | Health check: parsers, runtime, LSP servers; use `--lsp` for full LSP report |
| `lsp setup` | Auto-detect and install missing LSP servers; use `--dry-run` first |

---

## How Coding Agents Use Repomap

You don't type these commands yourself. The coding agent calls them during its work, guided by either the MCP server instructions or the skill file at `skills/repomap/SKILL.md`.

### Before editing

```bash
repomap overview --project .                          # First contact: project structure
repomap query --project . --query "auth token"        # Find files by business keywords
repomap file-detail --project . --file-path src/auth/login.ts
repomap impact --project . --files src/auth/login.ts --with-symbols   # Blast radius
repomap routes --project . --with-consumers           # Who calls this API?
repomap state-map --project . --symbol TaskStatus     # State lifecycle before changing it
repomap call-chain --project . --symbol refreshToken  # Callers and callees
```

### After editing

```bash
repomap verify --project . --with-lsp                 # Full evidence gate
repomap check --project .                             # Compiler/linter diagnostics
repomap orphan --project . --min-confidence 70        # Dead code check after deletion
```

---

## Supported Languages

**8 built-in** (zero config): Python, JavaScript, TypeScript (TSX), Go, Rust, HTML, CSS, JSON

**7 extended** (`uv sync --all-extras`): Java, Kotlin, Swift, C/C++, C#, PHP, Ruby

---

## MCP Tools

When using the MCP server (`repomap-mcp-server`), the following tools are available to the coding agent. The server injects workflow instructions telling the agent when each tool is MANDATORY.

`repomap_overview` · `repomap_query` · `repomap_file_detail` · `repomap_impact` · `repomap_call_chain` · `repomap_query_symbol` · `repomap_refs` · `repomap_routes` · `repomap_routes_consumers` · `repomap_state_map` · `repomap_verify` · `repomap_check` · `repomap_orphan` · `repomap_hotspots` · `repomap_diff` · `repomap_cache_save` · `repomap_doctor` · `repomap_lsp_setup` · `repomap_scan`

---

## Origin

`repomap`'s name and core idea come from **[aider](https://github.com/Aider-AI/aider)**. aider's author Paul Gauthier pioneered "repo mapping" — tree-sitter + PageRank for coding-agent codebase awareness. He proved a counterintuitive insight: a compact structural map often outperforms large amounts of raw code for agent understanding.

`repomap` extends the concept: 15 languages, incremental scanning, query synonym expansion, route-to-consumer mapping, contract risk detection, state mapping, community detection, and optional LSP integration.

---

## Related Projects

- **[aider](https://github.com/Aider-AI/aider)** — the original CLI repo mapping pioneer. repomap's core idea (tree-sitter + PageRank for coding-agent codebase awareness) comes from aider.
- **[DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI)** — we contributed `deepmap`, a Rust port of repomap's engine, via [PR](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap).

---

## License

MIT — [LICENSE](./LICENSE)
