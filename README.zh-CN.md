# RepoMap — 编程代理的代码库感知工具

> Tree-sitter 项目地图、13 语言 LSP、编辑前后影响分析 — 为 Claude Code、Cursor、Codex、OpenCode 设计。
>
> 灵感源自 [aider](https://github.com/Aider-AI/aider) 的 repo map。由 [@gjczone](https://github.com/gjczone) 与 deepseek-v4-pro 和 glm-5.1 共同构建。

[English README](README.md)

**编程代理获得的能力**：结构化仓库上下文，替代 grep + 原始文件读取：

- **从哪里开始**：`overview`、`query`（同义词扩展）、`routes`
- **什么会被破坏**：`impact`、`call-chain`、`refs`、`state-map`
- **遗漏了什么**：`verify`（合约风险警告）、`check`、`orphan`

---

## 快速开始

### 方案一：CLI + Skill

Skill 文件告诉代理*何时*调用每个命令。适用于任何支持自定义 skill 的编程代理。

```bash
# 1. 克隆 skill
mkdir -p ~/.claude/skills
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
rm -rf /tmp/repomap-install

# 2. 安装 CLI
pip install repomap
# 或：uv tool install repomap

# 3. 验证
repomap doctor
```

**效果**：代理读取 `~/.claude/skills/repomap/SKILL.md`，在恰当的时机自动调用 `repomap overview`、`repomap impact`、`repomap verify`。Skill 包含决策规则和强制使用模式。

### 方案二：仅 CLI

直接安装 CLI 工具，用于手动使用或集成到任何工作流：

```bash
pip install repomap
# 或：uv tool install repomap

# 验证
repomap doctor
```

---

### LSP 设置（可选）

为符号查找提供编译器级精度。代理自动处理：

```bash
repomap doctor --lsp                 # 检查可用服务器
repomap lsp setup --dry-run          # 预览安装计划
repomap lsp setup                    # 安装缺失的服务器
```

| 语言 | 服务器 | 安装 |
|------|--------|------|
| Python | `pyright` | `npm install -g pyright` |
| TypeScript / JS | `typescript-language-server` | `npm install -g typescript-language-server typescript` |
| Rust | `rust-analyzer` | `rustup component add rust-analyzer` |
| Go | `gopls` | `go install golang.org/x/tools/gopls@latest` |
| C / C++ | `clangd` | `apt install clangd` / `brew install llvm` |
| C# | `csharp-ls` | `dotnet tool install -g csharp-ls` |
| Java | `jdtls` | mason 或手动安装 |
| Lua | `lua-language-server` | `npm install -g lua-language-server` |
| PHP | `intelephense` | `npm install -g intelephense` |
| Ruby | `ruby-lsp` | `gem install ruby-lsp` |
| Swift | `sourcekit-lsp` | Xcode / Swift toolchain 自带 |
| Kotlin | `kotlin-language-server` | mason 或手动安装 |

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

代理按此模式自动工作（由 skill 指令引导）：

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

## 起源

`repomap` 的核心思想来自 **[aider](https://github.com/Aider-AI/aider)**——用 tree-sitter + PageRank 为编程代理提供代码库感知。LSP 集成借鉴了 **[serena](https://github.com/oraios/serena)** 的服务器自动检测、搜索结果格式和分级符号索引。

---

## 许可证

MIT — [LICENSE](./LICENSE)
