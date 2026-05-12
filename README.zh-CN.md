# RepoMap — 编程代理的代码库感知工具

> Tree-sitter 项目地图、13 语言 LSP、编辑前后影响分析 — 为 Claude Code、Cursor、Codex、OpenCode 设计。
>
> 灵感源自 [aider](https://github.com/Aider-AI/aider) 的 repo map。由 [@gjczone](https://github.com/gjczone) 与 [DeepSeek](https://chat.deepseek.com/) 共同构建。

[English README](README.md)

**编程代理获得的能力**：结构化仓库上下文，替代 grep + 原始文件读取：

- **从哪里开始**：`overview`、`query`（同义词扩展）、`routes`
- **什么会被破坏**：`impact`、`call-chain`、`refs`、`state-map`
- **遗漏了什么**：`verify`（合约风险警告）、`check`、`orphan`

---

## 快速开始

### 方案一（推荐）：CLI + Skill

Skill 文件告诉代理*何时*调用每个命令。适用于任何支持自定义 skill 的编程代理。

```bash
# 1. 克隆 skill
mkdir -p ~/.claude/skills
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
rm -rf /tmp/repomap-install

# 2. 安装二进制文件
npm install -g repomap-bin

# 3. 验证
repomap doctor
```

**效果**：代理读取 `~/.claude/skills/repomap/SKILL.md`，在恰当的时机自动调用 `repomap overview`、`repomap impact`、`repomap verify`。Skill 包含决策规则和强制使用模式。

### 方案二：MCP 服务器

MCP 工具出现在代理的工具列表中，附带内置工作流指令。最适合 MCP 原生客户端（Claude Code、Cursor、VS Code）。

添加到 `~/.claude/settings.json`：

```json
{
  "mcpServers": {
    "repomap": {
      "command": "npx",
      "args": ["-y", "repomap-mcp-server@latest"]
    }
  }
}
```

或命令行：`claude mcp add --transport stdio repomap -- npx -y repomap-mcp-server@latest`

**效果**：19 个 MCP 工具（`repomap_overview`、`repomap_query`、`repomap_impact` 等），含强制性工作流规则注入系统提示。

> **Skill vs MCP**：Skill 模式给代理一个决策流程（何时调用什么）。MCP 模式给代理工具和描述。两者都可用——根据你的客户端选择。Skill 模式适用于任何能读取 markdown 的代理；MCP 模式需要 MCP 协议支持。

---

### LSP 设置（可选）

为符号查找提供编译器级精度。代理自动处理：

```bash
repomap doctor --lsp                 # 检查可用服务器
repomap lsp setup --dry-run          # 预览安装计划
repomap lsp setup                    # 安装缺失的服务器
```

**默认服务器**（按语言自动检测）：`pyright`（Python）、`typescript-language-server`（TS/JS）、`rust-analyzer`（Rust）、`gopls`（Go）、`clangd`（C/C++）、`csharp-ls`（C#）、`jdtls`（Java）、`lua-language-server`（Lua）、`intelephense`（PHP）、`ruby-lsp`（Ruby）、`sourcekit-lsp`（Swift）、`kotlin-language-server`（Kotlin）。

所有命令无需 LSP 即可工作——LSP 是可选的精度增强层。

---

## 命令

| 命令 | 功能 |
|------|------|
| `overview` | 项目地图：入口点、热点、关键符号（PageRank）、阅读顺序 |
| `query --query <关键词>` | 主题搜索（同义词扩展）；`--context-lines <N>` 显示匹配代码行 |
| `file-detail --file-path <文件>` | 文件符号 + 签名；`--with-lsp` 查看分级符号树 |
| `impact --files <文件...> --with-symbols` | 编辑前影响范围：关键符号、受影响文件、风险、建议测试 |
| `call-chain --symbol <名称>` | 调用者和被调用者，支持配置深度 |
| `query-symbol --symbol <名称>` | 精确/模糊符号查找；`--with-lsp` 获取 hover + 定义/引用证据 |
| `refs --symbol <名称>` | 符号的所有引用；`--with-lsp` 获取精确跨文件结果 |
| `routes [--json] [--with-consumers]` | HTTP/API 路由清单（FastAPI、Express、Axum、Spring Boot） |
| `state-map --symbol <名称>` | 枚举/常量状态值、写入者、读取者 |
| `verify [--quick] [--with-lsp] [--with-diff]` | 编辑后证据门：git 变更、风险、诊断 |
| `check [--with-lsp]` | 编译器/类型/lint 诊断（tsc、ruff、cargo check、go vet） |
| `orphan [--json]` | 死代码候选发现，含置信度分级 |
| `hotspots` | 按复杂度排名的高密度文件 |
| `doctor [--lsp]` | 健康检查：解析器、运行时、LSP 可用性 |
| `lsp setup [--dry-run]` | 自动安装缺失的 LSP 服务器 |

---

## 代理工作流

代理按此模式自动工作（由 skill 或 MCP 指令引导）：

```bash
# 编辑前
repomap overview --project .                          # 初次接触
repomap query --project . --query "auth token"        # 按关键词查找
repomap file-detail --project . --file-path src/auth/login.ts
repomap impact --project . --files src/auth/login.ts --with-symbols
repomap routes --project . --with-consumers           # API 消费者映射
repomap call-chain --project . --symbol refreshToken

# 编辑后
repomap verify --project . --with-lsp                 # 完整证据门
repomap check --project .                             # 编译器诊断
repomap orphan --project . --min-confidence 70        # 死代码检查
```

---

## 支持的语言

**8 种内置**（零配置）：Python、JavaScript、TypeScript（TSX）、Go、Rust、HTML、CSS、JSON

**7 种扩展**（`npm install -g repomap-bin` 已全部包含）：Java、Kotlin、Swift、C/C++、C#、PHP、Ruby

---

## MCP 工具

使用 MCP 服务器时可用：

`repomap_overview` · `repomap_query` · `repomap_file_detail` · `repomap_impact` · `repomap_call_chain` · `repomap_query_symbol` · `repomap_refs` · `repomap_routes` · `repomap_routes_consumers` · `repomap_state_map` · `repomap_verify` · `repomap_check` · `repomap_orphan` · `repomap_hotspots` · `repomap_diff` · `repomap_cache_save` · `repomap_doctor` · `repomap_lsp_setup` · `repomap_scan`

---

## 起源

`repomap` 的核心思想来自 **[aider](https://github.com/Aider-AI/aider)**。Paul Gauthier 首创了仓库映射——用 tree-sitter + PageRank 为编程代理提供代码库感知——证明紧凑的结构化地图往往优于原始代码供代理理解。

`repomap` 扩展了这一概念：15 种语言、增量扫描、查询同义词扩展、路由到消费者映射、合约风险检测、状态映射，以及可选的 LSP 集成。

---

## 相关项目

- **[aider](https://github.com/Aider-AI/aider)** — CLI 仓库映射的先驱。
- **[serena](https://github.com/oraios/serena)** — 功能全面的 MCP 编程工具包，深度 LSP 集成（solidlsp）。repomap v2.3 借鉴了 serena 的 LSP 检测模式、`TextLine`/`MatchedConsecutiveLines` 搜索格式和 `NamePath` 风格的分级符号索引。
- **[DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI)** — 我们贡献了 `deepmap`，即 repomap 引擎的 Rust 移植。

---

## 许可证

MIT — [LICENSE](./LICENSE)
