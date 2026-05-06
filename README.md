# RepoMap — AI Agent 的代码库地图

`repomap` 是一个命令行工具，专为 CLI/TUI 环境中的 AI 编程助手设计。它用 tree-sitter 扫描源码，构建依赖图，用 PageRank 算法排名，生成结构化的"项目地图"——让 AI agent 在动手改代码之前就知道应该看什么、改了会影响谁、改完后该验证什么。

它的核心价值不是替代程序员或测试，而是让 AI agent 在一个命令里拿到一张紧凑高信号的信息图，少猜、少乱翻文件、少漏掉影响范围。

**我们保留了 [aider](https://github.com/Aider-AI/aider) 原创的 "repo map" 名称**，以此致敬这个理念的开创者。`repomap` 是一个独立的开源项目（MIT），同时我们正在将其核心引擎用 Rust 重写，[提交 PR](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap) 集成到 [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) 中，作为内置工具 **deepmap**。

---

## 安装

把下面这段话复制给你的 AI agent（Claude Code、Cursor 或任何有 shell 权限的 agent），它会自动完成安装：

```
请帮我安装 repomap：

1. 下载 skill 到我的 skills 目录：
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. 下载预编译的二进制文件：
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/releases/latest/download/repomap
   chmod +x ~/.local/bin/repomap

3. 验证安装：
   repomap doctor

如果 ~/.local/bin 不在 PATH 里：
   export PATH="$HOME/.local/bin:$PATH"
   （把这行加到 ~/.bashrc 或 ~/.zshrc 让它持久生效）
```

安装完成后，AI agent 会自动发现 skill，在需要理解代码库时主动调用 `repomap`。

> 也可以手动安装：`git clone` → `cp skills/repomap ~/.claude/skills/` → 下载二进制 → `chmod +x`。或从 Python 源码运行：`git clone` → `cd repomap` → `uv run repomap --help`。

---

## 它能做什么？

### 改动代码之前：了解项目、评估影响

| 场景 | 命令 | 作用 |
|------|------|------|
| 初次接触陌生项目 | `overview --project <项目路径>` | 入口文件、模块分布、热点文件、关键符号、推荐阅读顺序 |
| 知道业务关键词但不知道具体文件 | `query --project <项目路径> --query <关键词>` | 按业务主题搜索，找到相关文件、符号和测试 |
| 知道要改哪个文件 | `file-detail --project <项目路径> --file-path <文件>` | 查看文件里有哪些函数/类、签名和重要性排名 |
| 查某个函数/类的调用关系 | `call-chain --project <项目路径> --symbol <符号名>` | 追踪谁调了它、它调了谁 |
| 编辑前评估影响 | `impact --project <项目路径> --files <文件> --with-symbols` | 改动会影响哪些文件、风险多高、应该跑哪些测试、建议下一步读什么 |

### 改动代码之后：验证、确认、交付

| 场景 | 命令 | 作用 |
|------|------|------|
| 改动后快速检查风险 | `verify --project <项目路径> --quick` | 列出改了哪些文件、影响范围、风险等级、建议测试 |
| 改动后完整验证 | `verify --project <项目路径>` | 变更汇总 + 风险 + 诊断结果（编译器/linter）+ 建议测试 |
| 运行语言诊断 | `check --project <项目路径>` | TypeScript（tsc）、Python（ruff + mypy）、Rust（cargo check）、Go（go vet） |

### 其他能力

| 命令 | 作用 |
|------|------|
| `query-symbol --symbol <名称>` | 精确/模糊查找符号定义位置 |
| `refs --symbol <名称>` | 查找某个符号被谁引用 |
| `routes --json` | HTTP API 路由清单（支持 FastAPI / Express / Axum / Spring Boot） |
| `orphan` | 死代码候选检测（带置信度分级） |
| `lsp doctor` | 检查本机可用的 LSP 语言服务器 |
| `diagnostics --source lsp --files <文件>` | 对指定文件运行本地 LSP 诊断 |

### 完整场景示例：改一个文件

```bash
# 1. 改动前：了解这个文件
repomap file-detail --project /path/to/project --file-path src/auth/login.ts

# 2. 改动前：评估影响
repomap impact --project /path/to/project --files src/auth/login.ts --with-symbols

# 3. 改代码...

# 4. 改动后：验证
repomap verify --project /path/to/project
```

### 完整场景示例：探索陌生项目

```bash
# 1. 快速了解项目结构
repomap overview --project /path/to/project

# 2. 按业务关键词找到相关代码
repomap query --project /path/to/project --query "用户认证 token"

# 3. 仔细看找到的文件
repomap file-detail --project /path/to/project --file-path src/auth/session.ts

# 4. 追踪关键函数的调用链
repomap call-chain --project /path/to/project --symbol refreshToken
```

---

## 支持的语言

Python, JavaScript / TypeScript（含 TSX）, Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON。

LSP 集成支持 TypeScript / Python / Rust / Go（需本机已安装对应语言服务器，opt-in 使用）。

---

## 起源与致谢

`repomap` 的核心理念源于 **[aider](https://github.com/Aider-AI/aider)** —— Paul Gauthier（aider 作者）最早提出"在 CLI 里用 tree-sitter + PageRank 给 AI agent 建项目地图"的想法，并证明了一个反直觉的关键事实：**一张紧凑的结构化地图，往往比塞大量原始代码对 AI 更有用**。这个洞察是本项目的理论基础。

我们在 aider 的基础上，延续了它的命名（repomap = repository map），并进行了大幅延展：更多语言、增量扫描、改动前影响分析、改动后验证门控、AI 友好的结构化报告、可选的本地 LSP 集成。`repomap` 和其 Rust 移植版 `deepmap` 均由非专业开发者借助 AI 编程助手完成。

---

## 相关项目

- **[aider](https://github.com/Aider-AI/aider)** — CLI 环境下 repo mapping 理念的原创者，向 Paul 和所有贡献者致以最诚挚的感谢
- **[DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI)** — `deepmap` 是 `repomap` 引擎的 Rust 移植，正在提交 PR 集成中

---

## 许可证

MIT — 详见 [LICENSE](./LICENSE)。
