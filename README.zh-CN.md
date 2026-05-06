# RepoMap — Skill + CLI，AI Agent 的代码库"索引"工具

> **一个 skill + CLI 工具，让 Claude Code、Codex、OpenCode 等 AI agent 拥有"项目地图"——知道应该读哪个文件、改了会影响谁、改完该验证什么。**
>
> 灵感源于 [aider](https://github.com/Aider-AI/aider) 的 repo map 概念。

`repomap` 是一个以 skill + 二进制形式分发的 CLI 工具。AI agent 通过 skill 调用它，获得仓库级的结构化信息，而不是靠 `grep` + 逐文件阅读来猜：

- **动手前**：项目入口在哪、关键词对应哪些文件、改一个文件会影响谁、风险有多高、该先读什么
- **动手后**：改了哪些文件、风险等级、建议跑哪些测试、诊断结果有没有问题

它不改代码，不替代测试。只是在 agent 读文件前给一张"地图"，改代码后帮忙确认改了哪些、有没有风险。

---

## 安装

### Linux (x86_64) — 有预编译二进制

复制下面这段话给你的 AI agent：

```
请帮我安装 repomap：

1. 下载 skill：
   mkdir -p ~/.claude/skills
   git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
   cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
   rm -rf /tmp/repomap-install

2. 下载二进制文件：
   mkdir -p ~/.local/bin
   curl -L -o ~/.local/bin/repomap https://github.com/gjczone/repomap/raw/main/dist/repomap
   chmod +x ~/.local/bin/repomap

3. 设置语言支持（agent 自动处理）：
   告诉 agent："检查我用的语言，帮我把 repomap 的语言支持配好"。
   agent 会安装对应语言的 tree-sitter 解析器。8 种语言开箱即用；如果你还用 Java、Kotlin、Swift、C/C++、C#、PHP、Ruby，agent 会额外执行 `uv sync --all-extras`。
   
   然后 agent 运行 `repomap lsp doctor` 检查 LSP。如果有缺失，会提示安装命令：

   | 语言 | LSP 安装命令 |
   |------|-------------|
   | TypeScript | `npm install -g typescript-language-server` |
   | Python | `npm install -g pyright` |
   | Rust | `rustup component add rust-analyzer` |
   | Go | `go install golang.org/x/tools/gopls@latest` |

   不需要理解这些是什么——agent 全权处理。

4. 验证：
   repomap doctor

如果 ~/.local/bin 不在 PATH 里：
   export PATH="$HOME/.local/bin:$PATH"
```

### macOS / Windows — 从源码构建

暂无预编译二进制，需要本地构建：

```bash
# 1. 克隆并安装 skill
git clone https://github.com/gjczone/repomap.git /tmp/repomap-install
cp -r /tmp/repomap-install/skills/repomap ~/.claude/skills/repomap
rm -rf /tmp/repomap-install

# 2. 克隆并构建二进制
git clone https://github.com/gjczone/repomap.git ~/repomap-src
cd ~/repomap-src
uv run --with pyinstaller python -m repomap.cli build-binary --output dist

# 3. 安装二进制
mkdir -p ~/.local/bin
cp dist/repomap ~/.local/bin/repomap
chmod +x ~/.local/bin/repomap

# 4. 验证
repomap doctor
```

需要：Python 3.10+, [uv](https://docs.astral.sh/uv/) 包管理器。

> 安装完成后，agent 在编码任务中会自动使用 repomap —— 需要时自行调用 `overview`、`impact`、`verify` 等命令。

---

## 典型用法

> 这些命令不需要你手动输入。AI agent 在工作过程中通过 repomap skill 来调用——它根据你的任务自己决定什么时候用 `overview`、`impact`、`verify` 等。

### 改代码前：了解 → 评估 → 计划

```bash
# 初次接触项目：快速了解结构
repomap overview --project /path/to/project

# 按业务关键词找代码（不知道文件名时）
repomap query --project /path/to/project --query "用户认证 token"

# 读文件前先看里面有什么
repomap file-detail --project /path/to/project --file-path src/auth/login.ts

# 改之前评估影响
repomap impact --project /path/to/project --files src/auth/login.ts --with-symbols

# 追踪函数调用链：谁调它、它调谁
repomap call-chain --project /path/to/project --symbol refreshToken
```

### 改代码后：验证 → 确认

```bash
# 快速检查：改了哪些文件 + 风险 + 建议测试
repomap verify --project /path/to/project --quick

# 完整验证：以上 + 编译器/linter 诊断 + 可选 LSP
repomap verify --project /path/to/project
```

---

## 全部命令

| 命令 | 用途 |
|------|------|
| `overview` | 项目总览：入口点、热点文件、关键符号（PageRank 排名）、推荐阅读顺序 |
| `query --query <关键词>` | 按业务主题搜索，跨路径、文件名和符号名匹配 |
| `file-detail --file-path <文件>` | 查看文件所有符号：签名、可见性、PageRank 分数 |
| `impact --files <文件> --with-symbols` | 改动前分析：影响范围、关键符号、风险等级、建议测试 |
| `call-chain --symbol <名称>` | 追踪调用链：谁调它、它调谁，按重要性排序 |
| `query-symbol --symbol <名称>` | 精确/模糊查找符号定义位置 |
| `refs --symbol <名称>` | 查找符号的所有引用（可选 LSP 精确查找） |
| `verify` | 改动后汇总：git 变更、风险、诊断、建议测试 |
| `verify --quick` | 快速风险检查（跳过编译器/LSP，更快） |
| `check` | 语言诊断：tsc / cargo check / ruff / mypy / go vet |
| `routes --json` | HTTP API 路由清单（FastAPI / Express / Axum / Spring Boot） |
| `orphan` | 死代码候选检测（带置信度分级） |
| `lsp doctor` | 检查本机可用的 LSP 服务器（typescript / pyright / rust-analyzer / gopls） |

---

## 支持语言

> **你不需要做任何事。** AI agent 会在安装时自动处理语言设置——只需告诉它你用哪些语言。

8 种语言开箱即用：Python、JavaScript、TypeScript（TSX）、Go、Rust、HTML、CSS、JSON。

另外 7 种（Java、Kotlin、Swift、C/C++、C#、PHP、Ruby）需要多跑一条命令，agent 会帮你执行：`uv sync --all-extras`。

如果需要更深入的分析，agent 可以使用本机已有的语言服务器。如果没有，安装命令如下：

| 语言 | 安装命令 |
|------|---------|
| TypeScript | `npm install -g typescript-language-server` |
| Python | `npm install -g pyright` |
| Rust | `rustup component add rust-analyzer` |
| Go | `go install golang.org/x/tools/gopls@latest` |

没有 LSP 不影响 `overview` / `query` / `impact` 等核心功能——LSP 只是给符号级查询增加额外精度。

---

## 起源

`repomap` 的名字和核心理念来自 **[aider](https://github.com/Aider-AI/aider)**。aider 作者 Paul Gauthier 首创了 "repo mapping" ——用 tree-sitter + PageRank 在 CLI 里给 AI agent 提供代码库感知能力。他证明了一个反直觉的洞察：一张紧凑的结构化地图，对 AI 的价值往往超过大量原始代码。我们保留了 "repo map" 这个名称，以表达对起源的敬意。

`repomap` 在这个基础上进行了大幅延展：15 种语言、增量扫描、改动前影响分析、改动后验证门控、可选本地 LSP 集成。由 [@gjczone](https://github.com/gjczone)（非程序员）使用 DeepSeek-V4-Pro 开发，GLM-5.1 和 MIMO-V2.5-Pro 辅助交叉验证与审核。

---

## 相关项目

- **[aider](https://github.com/Aider-AI/aider)** — CLI 环境下 repo mapping 理念的原创者。Paul Gauthier 最早构思了 tree-sitter + PageRank 做 AI agent 代码库感知。此项目立于其肩膀之上。
- **[DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI)** — `deepmap`（repomap 引擎的 Rust 移植，[PR 提交中](https://github.com/Hmbown/DeepSeek-TUI/pulls?q=deepmap)）

---

## 许可证

MIT — [LICENSE](./LICENSE)
