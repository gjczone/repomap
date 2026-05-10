# RepoMap — Repository Intelligence for AI Agents

> **A CLI tool + skill + MCP server that gives AI agents a "project map" — where to start reading, what a change affects, and what to verify — before and after editing code.**
>
> Inspired by [aider](https://github.com/Aider-AI/aider)'s repo map concept. Built by [@gjczone](https://github.com/gjczone).

[中文 README](README.zh-CN.md)

AI agents use repomap to get structured repository context instead of guessing via `grep` + raw file reads:

- **Pre-positioning**: where to start for a task — `overview`, `query` (with synonym expansion), `routes --with-consumers`
- **Prevention**: what will break if I edit this — `impact`, `call-chain`, `refs`, `state-map`
- **Gap detection**: what did I miss after editing — `verify` (with contract risk warnings), `check`, `orphan`

It comes in three forms — pick the one that fits your workflow.

---

## Quick Install

### Option A: MCP Server (Claude Code, Cursor, VS Code)

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

Or via CLI: `claude mcp add --transport stdio repomap -- npx --force-refresh -y repomap-mcp-server`

Zero setup — the binary auto-installs on first run. All 18 commands exposed as MCP tools.

### Option B: npm (binary only)

```bash
npm install -g repomap-bin
repomap doctor
```

### Option C: skill + binary (manual)

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

### LSP setup (optional, agent handles this)

The agent runs `repomap lsp doctor` to check language servers. If missing:

| Language | Install |
|----------|---------|
| TypeScript | `npm install -g typescript-language-server` |
| Python | `npm install -g pyright` |
| Rust | `rustup component add rust-analyzer` |
| Go | `go install golang.org/x/tools/gopls@latest` |

Without LSP, all commands still work — LSP adds compiler-grade precision for symbol-level lookups.

---

## All Commands

| Command | What it does |
|---------|-------------|
| `overview` | Project map: entry points, hotspots, key symbols (PageRank), reading order, module clusters |
| `scan` | Initial scan summary: file/symbol/edge counts, entry points, scan health |
| `query --query <keywords>` | Topic search with synonym expansion across paths, filenames, symbols, and routes |
| `file-detail --file-path <f>` | All symbols in a file: signatures, visibility, PageRank, callers |
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
| `diagnostics --source lsp --files <f...>` | Focused LSP diagnostics for specific files |
| `orphan` | Dead-code candidates with confidence tiers and pre-deletion checklist |
| `hotspots` | High-density files ranked by complexity and change frequency |
| `cache save` | Save current graph as baseline for later `diff` or `verify --with-diff` |
| `git-history --symbol <name>` | Commit history for a specific symbol |
| `diff` | Graph comparison against a pre-edit `cache save` baseline |
| `doctor` | Binary health check: parsers, runtime, LSP availability |
| `lsp doctor` | Check locally installed LSP servers (pyright, tsc, rust-analyzer, gopls) |

---

## How AI Agents Use Repomap

You don't type these commands yourself. The AI agent calls them during its work, guided by the skill file at `skills/repomap/SKILL.md`.

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

When using the MCP server (`repomap-mcp-server`), the following tools are available to the AI agent:

`repomap_overview` · `repomap_query` · `repomap_file_detail` · `repomap_impact` · `repomap_call_chain` · `repomap_query_symbol` · `repomap_refs` · `repomap_routes` · `repomap_routes_consumers` · `repomap_state_map` · `repomap_verify` · `repomap_check` · `repomap_orphan` · `repomap_hotspots` · `repomap_diff` · `repomap_cache_save` · `repomap_git_history` · `repomap_scan`

---

## Origin

`repomap`'s name and core idea come from **[aider](https://github.com/Aider-AI/aider)**. aider's author Paul Gauthier pioneered "repo mapping" — tree-sitter + PageRank for AI-agent codebase awareness. He proved a counterintuitive insight: a compact structural map often outperforms large amounts of raw code for agent understanding.

`repomap` extends the concept: 15 languages, incremental scanning, query synonym expansion, route-to-consumer mapping, contract risk detection, state-map, community detection, and optional LSP integration.

---

## Related Projects

- **[aider](https://github.com/Aider-AI/aider)** — the original CLI repo mapping pioneer. repomap's core idea (tree-sitter + PageRank for agent codebase awareness) comes from aider.
- **[DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI)** — we contributed `deepmap`, a Rust port of repomap's engine, via [PR](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap).

---

## License

MIT — [LICENSE](./LICENSE)
