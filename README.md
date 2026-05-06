# RepoMap — 给 AI 代码助手用的仓库智能层

> **给人类看** — 把下面这段话粘贴给你的 AI 助手（Claude Code、Cursor 或任何有 shell 权限的 AI agent），剩下的它会自动搞定。

```
请帮我安装 repomap：

1. 下载 skill 到我的 skills 目录：
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. 下载预编译好的二进制文件：
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/releases/latest/download/repomap
   chmod +x ~/.local/bin/repomap

3. 验证安装：
   repomap doctor

如果 ~/.local/bin 不在 PATH 里，需要加一下：
   export PATH="$HOME/.local/bin:$PATH"
   (把这一行加到 ~/.bashrc 或 ~/.zshrc 里，这样以后每次打开终端都会生效)
```

> 安装完成后，AI agent 会自动发现这个 skill，并在需要理解代码库时主动调用 `repomap`——你不需要手动告诉它。

---

> **给 AI Agent 看** — 这段是写给 AI 的。每次会话开始时读一遍。  
> `repomap` 是一个在 `PATH` 上的命令行工具。用法：`repomap <命令> --project <项目绝对路径>`。  
> 完整命令参考：`references/command-map.md`。

## RepoMap 是什么

`repomap` 是一个为 CLI/TUI 环境中的 AI 编程助手设计的**仓库智能层**。它用 tree-sitter 扫描源码、构建依赖图、跑 PageRank 排名，生成结构化的"项目地图"——入口文件、热点、关键符号、调用链、影响范围、风险评估。

简单说：AI 不用再靠 `grep` + 逐文件阅读来猜项目结构，而是先拿到一张地图，再决定读什么、改什么、验证什么。

`repomap` 的核心引擎已用 Rust 重写，[正在提交 PR](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap) 集成到 [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) 中，作为内置工具 **deepmap**。Python 版 CLI 仍独立维护。

## 起源

`repomap` 的核心理念源于 [aider](https://github.com/Aider-AI/aider) —— 一个开创性的 AI 结对编程 CLI 工具。aider 的作者最早提出"用 tree-sitter + PageRank 在命令行里给 AI 建项目地图"的想法，并证明了一个关键事实：**一张 1K token 的结构化地图，往往比塞 50K token 原始代码对 AI 更有用**。

我们在 aider 的基础上做了大幅延展——15 种语言支持、增量扫描、影响分析、编辑后验证、LSP 集成、AI 友好的结构化报告。`repomap` 和 `deepmap` 都是由非专业开发者借助 AI 编程助手完成的——算是 AI 辅助开发的实践样本。

## 解决的问题

IDE 里的 AI 助手（Cursor、Trae、Qoder）天然有 LSP 和代码索引，知道项目有哪些符号、谁调谁。但 CLI 里的 AI agent（aider、Claude Code、DeepSeek-TUI）过去只能靠 `grep` + 逐文件阅读来理解代码库。

`repomap` 的目标是**尽量弥合这个差距**——不是要把 CLI 拉到和 IDE 一样的大规模代码理解水平（IDE 还有嵌入模型等能力），而是给 CLI agent 一张紧凑、高信号的"地图"，让它在动手改代码前少猜、少翻、少漏。

## 安装

### 方式一：把上面的提示词粘贴给 AI（推荐）

本文档最上面那段话就是设计来直接粘贴给 AI agent 的。把 repo 克隆到 skills 目录 + 下载二进制到 PATH，AI 会自动执行。

### 方式二：手动安装

```bash
# 1. 克隆 skill
mkdir -p ~/.claude/skills
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/
rm -rf /tmp/repomap-install

# 2. 下载二进制
mkdir -p ~/.local/bin
curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/releases/latest/download/repomap
chmod +x ~/.local/bin/repomap

# 3. 验证
repomap doctor
```

### 方式三：Python 源码运行

```bash
git clone https://github.com/gjczone/repomap.git
cd repomap
uv run repomap --help
```

## 支持的语言

Python, JavaScript / TypeScript (含 TSX), Go, Rust, Java, Kotlin, Swift, C/C++, C#, PHP, Ruby, HTML, CSS, JSON。LSP 集成支持 TypeScript / Python / Rust / Go（需本机已安装对应语言服务器，opt-in 使用）。

## 命令一览

| 命令 | 用途 |
|------|------|
| `overview` | 首次了解项目：入口点、热点、关键符号、推荐阅读顺序 |
| `query --query <关键词>` | 按业务主题搜索，不知道文件名时用 |
| `query-symbol --symbol <名称>` | 精确/模糊查找符号 |
| `file-detail --file-path <文件>` | 查看某个文件里有哪些符号、签名和 PR 分数 |
| `call-chain --symbol <名称>` | 追踪调用链：谁调它、它调谁 |
| `impact --files <文件> --with-symbols` | 改动前规划：影响范围、风险、建议测试 |
| `verify` | 改动后验证：变更汇总、风险、诊断、建议测试 |
| `verify --quick` | 快速风险检查（跳过编译器/LSP） |
| `check` | 运行语言诊断（tsc, cargo check, ruff, mypy, go vet） |
| `routes --json` | HTTP/API 路由清单 |
| `refs --symbol <名称>` | 发现某个符号的引用 |
| `orphan` | 死代码检测 |
| `cache save` / `diff` | 保存和比较图快照 |
| `lsp doctor` | 检查本机 LSP 可用性 |
| `diagnostics --source lsp --files <文件>` | 对指定文件运行 LSP 诊断 |

## 许可证

MIT — 详见 [LICENSE](./LICENSE)。

## 相关项目

- [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) — `deepmap` 是 `repomap` 引擎的 Rust 移植，已作为内置 TUI 工具集成
- [aider](https://github.com/Aider-AI/aider) — CLI 环境下 repo mapping 理念的原创者
