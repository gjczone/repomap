# RepoMap — Codebase Awareness for Coding Agents

> Tree-sitter project maps, 18-language LSP, pre/post-edit impact analysis — for Claude Code, Cursor, Codex, OpenCode.
>
> Inspired by [aider](https://github.com/Aider-AI/aider)'s repo map. Built by [@gjczone](https://github.com/gjczone) with deepseek-v4-pro and glm-5.1.

[中文 README](README.zh-CN.md)

**What agents get**: structured repo context instead of grep + raw reads:

- **Where to start**: `overview`, `query` (synonym expansion), `routes`
- **What will break**: `impact` (incl. type-level), `call-chain`, `refs`, `state-map`
- **What was missed**: `verify` (contract risk + missed-files), `check`, `orphan`
- **Auto-fix & ready**: `fix` (ruff + eslint auto-fix), `ready` (pre-commit check)
- **Encoding auto-detect**: UTF-8 → GBK → GB2312 fallback for legacy projects
- **Adaptive search**: never returns empty — keyword expansion → hotspot fallback

---

## Quick Start

One command installs everything. The skill tells agents *when* to call each repomap command; the CLI does the actual work.

```bash
# 1. Install skill (agent decision procedure)
mkdir -p ~/.claude/skills
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
rm -rf /tmp/repomap-install

# 2. Install CLI (Linux x64 only)
npm install -g repomap-bin

# 3. Verify
repomap doctor --project .
```

**Result**: The agent reads `~/.claude/skills/repomap/SKILL.md` and automatically calls `repomap overview`, `repomap impact`, `repomap verify` at the right moments. Use the CLI directly for manual analysis.

> **Note**: `--project` is a required argument for all commands (except `build-binary`). Always pass it as an absolute path.

---


### Build from Source (Windows / macOS)

Pre-built binaries are Linux x64 only. Windows and macOS users can build from source:

```bash
# 1. Clone the repo
git clone https://github.com/gjczone/repomap.git
cd repomap

# 2. Install uv (Python package manager)
# macOS:    brew install uv
# Windows:  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 3. Install dependencies
uv sync --all-extras

# 4. Build binary
uv run --with pyinstaller python -m src.cli build-binary --output dist

# 5. Smoke test
./dist/repomap doctor --project .
```

Or run from source without building: `uv run repomap <command> --project <path>`

### LSP Setup

Adds compiler-grade precision for symbol lookups. The agent handles this automatically:

```bash
repomap doctor --lsp --project .          # check available servers
repomap lsp setup --dry-run --project .   # preview install plan
repomap lsp setup --project .             # install missing servers
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
| Bash | `bash-language-server` | `npm install -g bash-language-server` |
| CSS / SCSS | `vscode-css-language-server` | `npm install -g vscode-langservers-extracted` |
| HTML | `vscode-html-language-server` | `npm install -g vscode-langservers-extracted` |
| JSON | `vscode-json-language-server` | `npm install -g vscode-langservers-extracted` |
| YAML | `yaml-language-server` | `npm install -g yaml-language-server` |

LSP-backed commands use local LSP servers by default when available. All commands still work without LSP; missing servers are reported as skipped, and `--no-lsp` disables LSP evidence when needed.

---

## Commands

| Command | Purpose |
|---------|---------|
| `overview` | Project map: entry points, hotspots, key symbols (PageRank), reading order |
| `query --query <keywords>` | Topic search with synonym expansion; `--context-lines <N>` for matched code; `--json` |
| `search --query <text>` | BM25 semantic symbol search; `--top-k <N>` for result count |
| `file-detail --file-path <f>` | File symbols + signatures + callers; LSP symbol tree by default; `--json` |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius: key symbols, affected files, risk, suggested tests |
| `call-chain --symbol <name>` | Callers and callees with configurable depth; `--direction`; `--json` |
| `query-symbol --symbol <name>` | Exact/fuzzy symbol lookup; LSP hover + definition/reference evidence by default; `--json` |
| `refs --symbol <name>` | All references to a symbol; LSP precision by default; `--json` |
| `routes [--json] [--with-consumers]` | HTTP/API route inventory (FastAPI, Express, Axum, Spring Boot) |
| `state-map --symbol <name>` | Enum/const state values, writers, readers |
| `verify [--quick] [--no-lsp] [--with-diff]` | Post-edit evidence gate: git changes, risk, diagnostics, missed-files detection |
| `check [--no-lsp]` | Compiler/type/lint diagnostics (tsc, pyright, ruff, cargo check, go vet) |
| `orphan [--json]` | Dead-code candidates with confidence tiers |
| `hotspots` | High-density files ranked by complexity |
| `cache save` / `diff` | Graph baseline save + comparison against baseline |
| `doctor [--lsp]` | Health check: parsers, runtime, LSP availability |
| `lsp setup [--dry-run]` | Auto-install missing LSP servers for detected languages |
| `fix [--dry-run]` | Auto-fix: ruff --fix + eslint --fix |
| `ready` | Pre-commit readiness: verify + check + format in one command |

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
repomap verify --project .                            # full evidence gate (incl. missed-files)
repomap fix --project .                               # auto-fix lint issues
repomap ready --project .                             # pre-commit readiness check
repomap check --project .                             # compiler diagnostics
repomap orphan --project . --min-confidence 70        # dead code check
```

---

## Origin

`repomap`'s core idea comes from **[aider](https://github.com/Aider-AI/aider)** — tree-sitter + PageRank for coding-agent codebase awareness. LSP integration patterns draw from **[serena](https://github.com/oraios/serena)**, including server auto-detection, search result formatting, and hierarchical symbol indexing.

---

## License

MIT — [LICENSE](./LICENSE)
