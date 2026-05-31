# RepoMap — Codebase Awareness for Coding Agents

> Tree-sitter project maps, 17-language LSP, pre/post-edit impact analysis — for Claude Code, Cursor, Codex, OpenCode.
>
> Inspired by [aider](https://github.com/Aider-AI/aider)'s repo map. Built by [@gjczone](https://github.com/gjczone) with deepseek-v4-pro, mimo-v2.5-pro, glm-5.1 and qwen3.7-max.

[中文 README](README.zh-CN.md)

**What agents get**: structured repo context instead of grep + raw reads:

- **Where to start**: `overview`, `query` (synonym expansion), `routes`
- **What will break**: `impact` (incl. type-level), `call-chain` (incl. references)
- **What was missed**: `verify` (contract risk + missed-files + orphan symbols), `check`
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

> **Note**: `--project` is optional. If not specified, repomap auto-detects the git root directory.

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

# 4. Run from source
uv run repomap doctor --project .
```

Or build a binary: `uv run --with pyinstaller python -m PyInstaller --onefile --name repomap src/cli/__main__.py`

### LSP Setup

Adds compiler-grade precision for symbol lookups. The agent handles this automatically:

```bash
repomap doctor --project .                # check runtime + LSP status (default)
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

LSP-backed commands automatically use local LSP servers when available. All commands work without LSP; missing servers are reported as skipped.

---

## Commands

| Command | Purpose |
|---------|---------|
| `overview` | Project map: entry points, hotspots, key symbols (PageRank), reading order |
| `query --query <keywords>` | Topic search with synonym expansion; `--context-lines <N>` for matched code; `--json` |
| `query --symbol <name>` | Exact/fuzzy symbol lookup; LSP hover + definition/reference + state map; `--json` |
| `query --search <text>` | BM25 semantic symbol search; `--top-k <N>` for result count; `--json` |
| `query --file <path>` | File symbols + signatures + callers; LSP symbol tree by default; `--json` |
| `impact --files <f...> --with-symbols` | Pre-edit blast radius: key symbols, affected files, risk, suggested tests; `--compact` concise; `--top-n <N>` |
| `call-chain --symbol <name>` | Callers, callees, and references with configurable depth; `--direction`; `--json` |
| `routes [--json] [--with-consumers]` | HTTP/API route inventory (FastAPI, Express, Axum, Spring Boot) |
| `verify [--quick] [--no-diff]` | Post-edit evidence gate: git changes, risk, diagnostics, orphan symbols, graph diff; `--risk-threshold HIGH\|MED\|LOW` |
| `check` | Compiler/type/lint diagnostics (tsc, pyright, ruff, cargo check, go vet) |
| `cache save` | Graph baseline save for diff comparison |
| `doctor [--no-lsp]` | Health check: parsers, runtime, LSP status (default) |
| `lsp setup [--dry-run]` | Auto-install missing LSP servers for detected languages |
| `fix [--dry-run]` | Auto-fix: ruff --fix + eslint --fix |
| `ready` | Pre-commit readiness: verify + check + format in one command |

---

## Agent Workflow

The agent follows this pattern automatically (guided by the skill instructions):

```bash
# Before editing
repomap overview                                      # first contact
repomap query --query "auth token"                    # find by keywords
repomap query --file src/auth/login.ts                # file detail
repomap impact --files src/auth/login.ts --with-symbols
repomap routes --with-consumers                       # API consumer map
repomap call-chain --symbol refreshToken

# After editing
repomap verify                                        # full evidence gate (incl. orphan symbols + graph diff)
repomap fix                                           # auto-fix lint issues
repomap ready                                         # pre-commit readiness check
repomap check                                         # compiler diagnostics
```

---

## Origin

`repomap`'s core idea comes from **[aider](https://github.com/Aider-AI/aider)** — tree-sitter + PageRank for coding-agent codebase awareness. LSP integration patterns draw from **[serena](https://github.com/oraios/serena)**, including server auto-detection, search result formatting, and hierarchical symbol indexing.

---

## License

MIT — [LICENSE](./LICENSE)
