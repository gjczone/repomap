# RepoMap — AI 代理的仓库智能工具

> **一个 CLI 工具 + skill + MCP 服务器，为 AI 代理（Claude Code、Codex、OpenCode）提供"项目地图"——在编辑代码之前知道该读什么、改什么会影响什么，编辑之后知道该验证什么。**
>
> 灵感源自 [aider](https://github.com/Aider-AI/aider) 的 repo map 概念。由 [@gjczone](https://github.com/gjczone) 构建。

[English README](README.md)

AI 代理使用 repomap 获取结构化仓库上下文，替代 `grep` + 逐文件阅读的猜测：

- **预定位**：任务该从哪里开始 — `overview`、`query`（含同义词扩展）、`routes --with-consumers`
- **预防**：改这个文件会影响什么 — `impact`、`call-chain`、`refs`、`state-map`
- **缺口检测**：编辑后遗漏了什么 — `verify`（含合约风险警告）、`check`、`orphan`

三种安装方式，选择适合你的。

---

## 快速安装

### 方式 A：MCP 服务器（Claude Code、Cursor、VS Code）

在 Claude Code 配置中添加（`~/.claude/settings.json`）：

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

或命令行：`claude mcp add --transport stdio repomap -- npx --force-refresh -y repomap-mcp-server`

零配置——二进制在首次运行时自动安装。全部 21 个命令以 MCP 工具形式暴露。

### 方式 B：npm（仅二进制）

```bash
npm install -g repomap-bin
repomap doctor
```

### 方式 C：skill + 二进制（手动安装）

<details>
<summary>Linux (x86_64)</summary>

```
安装 repomap：

1. 克隆 skill：
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. 下载二进制：
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/releases/latest/download/repomap-linux
   chmod +x ~/.local/bin/repomap

3. 验证：
   repomap doctor
```
</details>

<details>
<summary>macOS (arm64)</summary>

```
安装 repomap：

1. 克隆 skill：
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. 下载二进制：
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/releases/latest/download/repomap-macos
   chmod +x ~/.local/bin/repomap

3. 验证：
   repomap doctor
```
</details>

<details>
<summary>Windows (x86_64)</summary>

```
安装 repomap：

1. 克隆 skill：
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. 下载二进制：
   mkdir -p ~/AppData/Local/Microsoft/WindowsApps
   curl -L -o ~/AppData/Local/Microsoft/WindowsApps/repomap.exe https://github.com/gjczone/repomap/releases/latest/download/repomap.exe

3. 验证：
   repomap.exe doctor
```
</details>

### LSP 设置（可选，AI 代理自动处理）

代理会运行 `repomap lsp doctor` 检查语言服务器。如有缺失：

| 语言 | 安装命令 |
|------|----------|
| TypeScript | `npm install -g typescript-language-server` |
| Python | `npm install -g pyright` |
| Rust | `rustup component add rust-analyzer` |
| Go | `go install golang.org/x/tools/gopls@latest` |

无 LSP 时所有命令仍可正常工作——LSP 仅为符号级查找提供编译器级别精度。

---

## 全部命令

| 命令 | 功能 |
|------|------|
| `overview` | 项目地图：入口点、热点、关键符号（PageRank）、推荐阅读顺序、模块聚类 |
| `query --query <关键词>` | 主题搜索（含同义词扩展），覆盖路径、文件名、符号名和路由 |
| `file-detail --file-path <文件>` | 文件内所有符号：签名、可见性、PageRank、调用者 |
| `impact --files <文件...> --with-symbols` | 编辑前影响范围：关键符号、受影响文件、风险等级、建议测试 |
| `call-chain --symbol <名称>` | 符号的调用者和被调用者，支持配置深度 |
| `query-symbol --symbol <名称>` | 精确或模糊符号查找；添加 `--with-lsp` 获得编译器级精度 |
| `refs --symbol <名称>` | 符号的所有引用；添加 `--with-lsp` 获得精确跨文件结果 |
| `routes --json` | HTTP/API 路由清单（FastAPI、Express、Axum、Spring Boot） |
| `routes --with-consumers` | 将每个路由映射到前端/测试消费者，含置信度级别 |
| `state-map --symbol <名称>` | 枚举/常量状态值、写入者和读取者（Python/TS/Rust/Go） |
| `verify` | 编辑后证据门：git 变更、风险、合约风险警告、建议测试、诊断、LSP、图差异 |
| `verify --quick` | 仅编辑后风险评估（跳过编译器/LSP，更快） |
| `check` | 语言诊断：tsc、cargo check、ruff、mypy、go vet |
| `orphan` | 死代码候选发现，含置信度分级和删除前检查清单 |
| `hotspots` | 按复杂度和变更频率排名的高密度文件 |
| `git-history --symbol <名称>` | 特定符号的提交历史 |
| `diff` | 与编辑前 `cache save` 基线的图对比 |
| `lsp doctor` | 检查本地已安装的 LSP 服务器 |

---

## AI 代理如何使用 RepoMap

你不需要自己输入这些命令。AI 代理根据 `skills/repomap/SKILL.md` 中的 skill 定义自动调用。

### 编辑前

```bash
repomap overview --project .                          # 初次接触：了解项目结构
repomap query --project . --query "auth token"        # 按业务关键词查找文件
repomap file-detail --project . --file-path src/auth/login.ts
repomap impact --project . --files src/auth/login.ts --with-symbols   # 影响范围
repomap routes --project . --with-consumers           # 谁调用了这个 API？
repomap state-map --project . --symbol TaskStatus     # 状态生命周期
repomap call-chain --project . --symbol refreshToken  # 调用者和被调用者
```

### 编辑后

```bash
repomap verify --project . --with-lsp                 # 完整证据门
repomap check --project .                             # 编译器/linter 诊断
repomap orphan --project . --min-confidence 70        # 删除后检查死代码
```

---

## 支持的语言

**8 种内置**（零配置）：Python、JavaScript、TypeScript（TSX）、Go、Rust、HTML、CSS、JSON

**7 种扩展**（`uv sync --all-extras`）：Java、Kotlin、Swift、C/C++、C#、PHP、Ruby

---

## MCP 工具

使用 MCP 服务器（`repomap-mcp-server`）时，以下工具对 AI 代理可用：

`repomap_overview` · `repomap_query` · `repomap_file_detail` · `repomap_impact` · `repomap_call_chain` · `repomap_query_symbol` · `repomap_refs` · `repomap_routes` · `repomap_routes_consumers` · `repomap_state_map` · `repomap_verify` · `repomap_check` · `repomap_orphan` · `repomap_hotspots` · `repomap_diff` · `repomap_cache_save` · `repomap_git_history` · `repomap_scan`

---

## 起源

`repomap` 的名称和核心思想来自 **[aider](https://github.com/Aider-AI/aider)**。aider 的作者 Paul Gauthier 首创了"仓库映射"——使用 tree-sitter + PageRank 为 AI 代理提供代码库感知能力。他证明了一个违反直觉的洞见：紧凑的结构化地图在代理理解方面往往优于大量原始代码。

`repomap` 扩展了这一概念：15 种语言、增量扫描、查询同义词扩展、路由消费者映射、合约风险检测、状态映射、社区检测以及可选的 LSP 集成。

---

## 相关项目

- **[aider](https://github.com/Aider-AI/aider)** — CLI 仓库映射的先驱
- **[DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI)** — `deepmap`（repomap 引擎的 Rust 移植）
- **[srcwalk](https://github.com/sting8k/srcwalk)** — 带多跳调用图遍历的 Rust CLI
- **[Canopy](https://github.com/LioraLabs/canopy)** — 本地语义代码搜索与知识图谱
- **[GitNexus](https://github.com/abhigyanpatwari/GitNexus)** — Claude Code 的 MCP 原生知识图谱
- **[Graphify](https://github.com/safishamsi/graphify)** — tree-sitter + LLM 知识图谱

---

## 许可证

MIT — [LICENSE](./LICENSE)
