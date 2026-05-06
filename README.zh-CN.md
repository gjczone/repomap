# RepoMap

**一句话：让 Claude Code、Codex、OpenCode 等 AI agent 在命令行里拥有"项目地图"——知道应该读哪个文件、改了会影响谁、改完该验证什么。**

`repomap` 是一个 CLI 工具。AI agent（如 Claude Code）通过 skill 调用它，在改动代码前后获得仓库级的结构化信息：

- **动手前**：项目入口在哪、关键词对应哪些文件、改一个文件会影响谁、风险有多高、该先读什么
- **动手后**：改了哪些文件、风险等级、建议跑哪些测试、诊断结果有没有问题

它不会替你改代码，也不会替代测试。它做的是 AI agent 最缺的那件事：**在几十上百个文件里快速给出高信号的结构化上下文**，让 agent 少翻不相关的文件、不瞎猜影响范围。

---

## 安装

复制下面这段话给你的 AI agent，它会自动完成安装：

```
请帮我安装 repomap：

1. 下载 skill：
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. 下载二进制文件（Linux x86_64）：
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/raw/main/dist/repomap
   chmod +x ~/.local/bin/repomap

3. 验证：
   repomap doctor

如果 ~/.local/bin 不在 PATH 里：
   export PATH="$HOME/.local/bin:$PATH"
```

> 手动安装：clone 仓库 → `cp skills/repomap ~/.claude/skills/` → 下载二进制 → 完成。

---

## 典型用法

### 改代码前：了解 → 评估 → 计划

```bash
# 初次接触项目：快速了解结构
repomap overview --project /path/to/project

# 按业务关键词找代码
repomap query --project /path/to/project --query "用户认证 token"

# 详细看某个文件
repomap file-detail --project /path/to/project --file-path src/auth/login.ts

# 改之前评估影响
repomap impact --project /path/to/project --files src/auth/login.ts --with-symbols
```

### 改代码后：验证 → 确认

```bash
# 快速检查
repomap verify --project /path/to/project --quick

# 完整验证（含编译器/linter诊断）
repomap verify --project /path/to/project
```

---

## 全部命令

| 命令 | 用途 |
|------|------|
| `overview` | 项目总览：入口点、热点文件、关键符号、推荐阅读顺序 |
| `query --query <关键词>` | 按业务主题搜索，不知道文件名时使用 |
| `file-detail --file-path <文件>` | 查看文件的符号、签名和重要性 |
| `impact --files <文件> --with-symbols` | 改动前分析：影响范围、风险、建议测试 |
| `call-chain --symbol <名称>` | 追踪调用链：谁调它、它调谁 |
| `query-symbol --symbol <名称>` | 精确/模糊查找符号定义 |
| `refs --symbol <名称>` | 查找符号的引用 |
| `verify` | 改动后汇总：变更、风险、诊断、建议测试 |
| `verify --quick` | 快速风险检查（跳过编译/LSP） |
| `check` | 语言诊断：tsc / cargo check / ruff / mypy / go vet |
| `routes --json` | HTTP API 路由清单 |
| `orphan` | 死代码候选检测 |
| `lsp doctor` | 检查本机 LSP 服务器可用性 |
| `diagnostics --source lsp --files <文件>` | 指定文件的 LSP 诊断 |

---

## 支持语言

Python, JavaScript / TypeScript（含 TSX）, Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON

---

## 起源

`repomap` 的名字和核心理念来自 **[aider](https://github.com/Aider-AI/aider)**。aider 作者 Paul Gauthier 最早提出"在 CLI 里用 tree-sitter + PageRank 给 AI 建项目地图"的思路，并证明了一个反直觉的事实：一张紧凑的结构化地图，对 AI 的价值往往超过大量原始代码。

我们保留了 "repo map" 这个名称以表达对起源的尊重，同时将 repomap 发展为独立的开源项目（MIT）。目前正在将其核心引擎用 Rust 重写，[提交 PR](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap) 集成到 [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) 中（内置工具 `deepmap`）。

本项目由非专业开发者借助 AI 编程助手完成。

---

## 许可证

[English version](./README.en.md)

MIT — [LICENSE](./LICENSE)
