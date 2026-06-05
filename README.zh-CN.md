# RepoMap — 编程代理的代码库感知工具

> Tree-sitter 项目地图、17 语言 LSP、编辑前后影响分析 — 为 Claude Code、Cursor、Codex、OpenCode 设计。
>
> 灵感源自 [aider](https://github.com/Aider-AI/aider) 的 repo map。由 [@gjczone](https://github.com/gjczone) 与 deepseek-v4-pro、mimo-v2.5-pro、glm-5.1 和 qwen3.7-max 共同构建。

[English README](README.md)

**编程代理获得的能力**：结构化仓库上下文，替代 grep + 原始文件读取：

- **从哪里开始**：`overview`、`query`（同义词扩展）、`routes`
- **什么会被破坏**：`impact`（含类型级）、`call-chain`（含引用信息）
- **遗漏了什么**：`verify`（合约风险 + 漏改检测 + 孤儿符号 + 级联调用方 + 密钥扫描）、`check`
- **自动修复与就绪检查**：`fix`（多语言格式化：ruff、biome、prettier、eslint、gofmt、cargo fmt）、`ready`（提交前检查）
- **编码自动检测**：UTF-8 → GBK → GB2312 回退，消除老项目扫描盲区
- **自适应搜索**：永不返回空结果 — 关键词扩展 → 热点兜底

---

## 快速开始

一条命令安装全部。Skill 告诉代理*何时*调用每个 repomap 命令；CLI 负责实际执行。

```bash
# 1. 安装 skill（代理决策流程）
mkdir -p ~/.claude/skills
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
rm -rf /tmp/repomap-install

# 2. 安装 CLI（仅 Linux x64）
npm install -g repomap-bin

# 3. 验证
repomap doctor --project .
```

**效果**：代理读取 `~/.claude/skills/repomap/SKILL.md`，在恰当的时机自动调用 `repomap overview`、`repomap impact`、`repomap verify`。也可以直接使用 CLI 进行手动分析。

> **注意**：`--project` 是可选参数。如果未指定，repomap 会自动检测 git 根目录。

---


### 从源码构建（Windows / macOS）

预编译二进制仅支持 Linux x64。Windows 和 macOS 用户可以从源码构建：

```bash
# 1. 克隆仓库
git clone https://github.com/gjczone/repomap.git
cd repomap

# 2. 安装 uv（Python 包管理器）
# macOS:    brew install uv
# Windows:  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 3. 安装依赖
uv sync --all-extras

# 4. 运行
uv run repomap doctor --project .
```

也可以构建二进制：`uv run --with pyinstaller python -m PyInstaller --onefile --name repomap src/cli/__main__.py`

### LSP 设置

为符号查找提供编译器级精度。代理自动处理：

```bash
repomap doctor --project .                # 检查运行时 + LSP 状态（默认）
repomap lsp setup --dry-run --project .   # 预览安装计划
repomap lsp setup --project .             # 安装缺失的服务器
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
| Bash | `bash-language-server` | `npm install -g bash-language-server` |
| CSS / SCSS | `vscode-css-language-server` | `npm install -g vscode-langservers-extracted` |
| HTML | `vscode-html-language-server` | `npm install -g vscode-langservers-extracted` |
| JSON | `vscode-json-language-server` | `npm install -g vscode-langservers-extracted` |
| YAML | `yaml-language-server` | `npm install -g yaml-language-server` |

支持 LSP 的命令会在服务器可用时自动使用本地 LSP。所有命令在没有 LSP 时仍可工作；缺失服务器会显示为 skipped。

---

## 命令

| 命令 | 功能 |
|------|------|
| `overview` | 项目地图：入口点、热点、关键符号（PageRank）、阅读顺序 |
| `query --query <关键词>` | 主题搜索（同义词扩展）；`--context-lines <N>` 显示匹配代码行；`--json` |
| `query --symbol <名称>` | 精确/模糊符号查找；LSP hover + 定义/引用 + 状态映射；`--json` |
| `query --search <文本>` | BM25 语义符号搜索；`--top-k <N>` 控制结果数；`--json` |
| `query --file <路径>` | 文件符号 + 签名 + 调用者；默认展示 LSP 分级符号树；`--json` |
| `impact --files <文件...> --with-symbols` | 编辑前影响范围：关键符号、受影响文件、风险、建议测试；`--compact` 精简输出；`--top-n <N>` |
| `affected --files <文件...>` | 发现受源代码变更影响的测试；`--stdin` 管道模式；`--filter` 自定义模式 |
| `call-chain --symbol <名称>` | 调用者、被调用者和引用，支持配置深度；`--direction`；`--json` |
| `routes [--json] [--with-consumers]` | HTTP/API 路由清单（FastAPI、Express、Axum、Spring Boot） |
| `verify [--quick] [--no-diff]` | 编辑后证据门：git 变更、风险、诊断、孤儿符号、图差异、级联调用方、密钥扫描；`--risk-threshold HIGH\|MED\|LOW`；`--no-cascade`；`--no-secrets` |
| `fix [--dry-run]` | 自动修复：多语言格式化工具（ruff、biome、prettier、eslint、gofmt、cargo fmt），基于配置就近检测 |
| `ready` | 提交就绪检查：verify + check + format 一键执行 |
| `check` | 编译器/类型/lint 诊断（tsc、pyright、ruff、cargo check、go vet） |
| `cache save` | 图基线保存（用于 diff 对比，自动清理陈旧 session） |
| `cache prune` | 手动清理陈旧 session 缓存（`--ttl-days N`） |
| `doctor [--no-lsp]` | 健康检查：解析器、运行时、LSP 状态（默认） |
| `lsp setup [--dry-run]` | 自动安装缺失的 LSP 服务器 |

---

## 代理工作流

代理按此模式自动工作（由 skill 指令引导）：

```bash
# 编辑前
repomap overview                                      # 初次接触
repomap query --query "auth token"                    # 按关键词查找
repomap query --file src/auth/login.ts                # 文件详情
repomap impact --files src/auth/login.ts --with-symbols
repomap routes --with-consumers                       # API 消费者映射
repomap call-chain --symbol refreshToken

# 编辑后
repomap verify                                        # 完整证据门（含孤儿符号 + 图差异）
repomap fix                                           # 自动修复 lint 问题
repomap ready                                         # 提交就绪检查
repomap check                                         # 编译器诊断
```

---

## 起源

`repomap` 的核心思想来自 **[aider](https://github.com/Aider-AI/aider)**——用 tree-sitter + PageRank 为编程代理提供代码库感知。LSP 集成借鉴了 **[serena](https://github.com/oraios/serena)** 的服务器自动检测、搜索结果格式和分级符号索引。多语言格式化分发和密钥扫描借鉴了 **[pi-lens](https://github.com/apmantza/pi-lens)**，一个面向 pi 编程代理的 TypeScript 扩展包。

---

## 许可证

MIT — [LICENSE](./LICENSE)
