# RepoMap — CLI/TUI AI 代码助手的项目地图

`repomap` 是一个仓库智能层（repository intelligence layer），专为 CLI/TUI 环境中的 AI 代码助手设计。它读取代码项目，生成结构化的"项目地图"：入口文件、关键符号、调用链、热点文件、阅读顺序、改动影响范围和风险评估。

如果把代码项目比作一栋大楼，普通搜索（grep / find）相当于在楼里挨个房间喊关键词；`repomap` 则是先给你楼层平面图、房间用途表、通道关系和安全出口。它的核心价值不是替代程序员或测试，而是让 AI 代码助手在动手改代码前少猜一点、少乱翻文件、少漏掉影响范围。

`repomap` 已被集成到 [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) 中，作为内置 Rust 工具 **deepmap**。Python 版本的 CLI 仍然独立维护，两者的核心算法（tree-sitter AST 解析 + PageRank 图分析）同源。

---

## 目录

- [起源与致谢](#起源与致谢)
- [解决的问题](#解决的问题)
- [快速开始](#快速开始)
- [安装](#安装)
- [命令总览](#命令总览)
- [核心工作流](#核心工作流)
- [命令详细说明](#命令详细说明)
- [项目结构](#项目结构)
- [维护策略](#维护策略)
- [已知限制](#已知限制)
- [许可证](#许可证)
- [相关项目](#相关项目)

---

## 起源与致谢

`repomap` 的核心理念源于 [aider](https://github.com/Aider-AI/aider) —— 一个开创性的 AI 结对编程 CLI 工具。aider 的作者最早提出了"在命令行环境中使用 tree-sitter 解析代码语法树，结合 PageRank 算法生成项目地图，让 AI 拥有代码库感知能力"的想法。这个想法非常优雅且实用。

我们在 aider 的 repomap 基础上做了大量扩展，使其更适合 CLI/TUI agent 工作流：

- 从单一 overview 扩展为完整的命令家族（impact / verify / query / call-chain / routes / orphan 等）
- 增加了编辑前影响分析和编辑后证据汇总
- 增加了主题搜索（query）和死代码检测（orphan）
- 增加了基于 Git 变更的风险评估
- 增加了可选的本地 LSP 集成（诊断、定义跳转、引用查找）
- 增加了缓存快照和图对比（cache save / diff）

本项目由一位非专业开发者借助 AI 编程助手构建。它不是一个商业产品，而是一个来自实践的开源工具，目标是让 AI 代码助手在终端环境中的工作更高效、更可靠。

**我们站在 aider 的肩膀上。** 没有 aider 的先行探索，就不会有 repomap。如果你还不了解 aider，强烈建议去看看。

---

## 解决的问题

### 背景：CLI 工具 vs IDE 工具的代码感知鸿沟

在 CLI 编码工具（aider、Claude Code、DeepSeek-TUI、Cursor CLI 模式等）中，AI agent 历史上几乎没有代码库感知能力。它的工作方式大致是：

1. 用户说"改一下登录逻辑"
2. AI 用 `grep` / `find` 搜索关键词
3. AI 读取搜索到的文件
4. AI 猜测要改哪里，然后动手

这种方式的问题很明显：

- **不知道从哪里开始看**：项目文件很多，只靠文本搜索容易漏掉关键文件，或者读一堆不相关文件。
- **不知道改动会影响谁**：改一个文件或函数前，不清楚谁在调用它、它又依赖谁。常常改完才发现破坏了其他功能。
- **不知道该验证什么**：改完之后，不知道应该跑哪些测试、看哪些风险点。
- **上下文窗口有限**：AI 能读的文件有限，没法把整个项目都塞进上下文。必须有一个机制来决定"哪些文件最值得读"。

相比之下，IDE 工具（Cursor、Trae、Qoder）内置了 LSP（语言服务器协议）和代码索引，天然拥有完整的代码库感知能力：跳转定义、查找引用、重构预览、诊断信息等。

`repomap` 的目标是**弥合这个鸿沟** —— 给 CLI agent 提供一个 IDE 级别的项目地图，而不需要 IDE、MCP 服务器、插件或后台守护进程。

### 它不是要替代 IDE

重要说明：`repomap` 的目标不是达到 IDE 级别的代码理解（IDE 有嵌入模型、LSP 持久连接、增量索引等重型机制）。它的目标是**用一份紧凑、高信号密度的项目地图，显著提升 CLI agent 的代码理解效率**。

具体来说：

- **不会自动改代码**。
- **不会替代真实测试**。
- **不会自动安装工具或语言服务器**。
- **不会启动后台常驻服务**。
- **不依赖 IDE、插件、MCP server**。
- **LSP 能力是可选的**，只在明确加 `--with-lsp` 或相关命令时使用。

### 核心能力

| 能力 | 说明 |
|------|------|
| 结构感知 | 用 tree-sitter 解析语法树，提取函数、类、方法、导入/导出关系 |
| 图分析 | 用 PageRank 算法计算文件/符号的重要性，识别核心模块 |
| 调用链追踪 | 追踪符号的调用者和被调用者 |
| 影响分析 | 评估文件改动的波及范围和风险 |
| 主题搜索 | 用业务关键词搜索代码，不需要知道精确的文件名 |
| 死代码检测 | 发现未被引用或低引用的符号 |
| 变更验证 | 汇总改动、风险、诊断、测试建议 |
| 可选 LSP | 本地语言服务器的精确诊断、定义跳转、引用查找 |

---

## 快速开始

### 一键安装（二进制）

从 [Releases 页面](https://github.com/Hmbown/DeepSeek-TUI/releases) 下载预编译二进制，或自行构建：

```bash
# 放在 PATH 中
cp repomap ~/.local/bin/
repomap --help
```

### Python 源码运行

```bash
git clone https://github.com/Hmbown/DeepSeek-TUI.git
cd repomap

# 使用 uv（推荐）
uv run python -m repomap_cli doctor

# 或者用 pip
pip install -e .
repomap doctor
```

### 自检

```bash
repomap doctor
```

这行命令检查：Python 版本、tree-sitter 解析器是否可用、本地是否有可用的 LSP 服务器。

### 第一个项目地图

```bash
repomap overview --project /path/to/your/project
```

这将输出：项目结构概览、核心文件、入口点、热点文件、关键符号和建议阅读顺序。

### 最常用的一条工作流

当 AI 准备修改一个已知文件时，推荐流程是：

```bash
# 1. 查看文件结构
repomap file-detail --project /path/to/project --file-path src/foo.ts

# 2. 评估改动影响（编辑前计划）
repomap impact --project /path/to/project --files src/foo.ts --with-symbols

# 3. 修改代码后验证
repomap verify --project /path/to/project
```

通俗解释：

1. 先看这个文件里面有哪些关键结构（函数、类、导出）。
2. 再看改它会影响哪些文件、哪些符号、哪些测试，以及风险高不高。
3. 改完后用 `verify` 汇总变更、风险、建议测试和诊断结果。

---

## 安装

### 方式一：预编译二进制（推荐）

从 GitHub Releases 下载对应平台的二进制文件：

```bash
# Linux x86_64
wget https://github.com/Hmbown/DeepSeek-TUI/releases/latest/download/repomap-linux-x86_64
mv repomap-linux-x86_64 ~/.local/bin/repomap
chmod +x ~/.local/bin/repomap
repomap doctor
```

### 方式二：Python 源码

```bash
# 克隆仓库
git clone https://github.com/Hmbown/DeepSeek-TUI.git
cd DeepSeek-TUI/repomap

# 使用 uv（推荐，自动管理依赖）
uv run python -m repomap_cli --help

# 或使用脚本入口
uv run repomap --help
```

也可以用 pip 安装依赖后运行：

```bash
pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript tree-sitter-go tree-sitter-rust tree-sitter-html tree-sitter-css tree-sitter-json
python -m repomap_cli doctor
```

### 方式三：符号链接到 PATH

```bash
ln -sf /path/to/repomap/dist/repomap ~/.local/bin/repomap
repomap --help
```

### 方式四：DeepSeek-TUI 内置 deepmap

如果你使用 DeepSeek-TUI，直接使用 `deepmap` 命令即可：

```bash
deepmap overview --project /path/to/project
deepmap impact --project /path/to/project --files src/main.rs --with-symbols
```

---

## 命令总览

`repomap` 的所有命令按使用场景分类：

### 项目发现（Project Discovery）

| 命令 | 用途 |
|------|------|
| `overview` | 初次进入项目时的全局概览：入口点、核心文件、热点、关键符号、阅读顺序 |
| `query` | 主题/关键词搜索，当你不知道精确文件名时使用 |
| `query-symbol` | 精确或模糊的符号查找 |
| `file-detail` | 查看文件的符号结构，含签名信息 |

### 编辑计划（Edit Planning）

| 命令 | 用途 |
|------|------|
| `impact` | 文件级影响分析：改了某个文件会影响谁 |
| `impact --with-symbols` | 增强版编辑前计划器：含关键符号、建议阅读顺序、关联测试、风险说明、LSP 可用性提示 |
| `call-chain` | 追踪符号的调用者和被调用者 |
| `refs` | 查找符号的所有引用位置（支持 `--with-lsp` 获取本地精确引用） |

### 验证与质量（Verification & Quality）

| 命令 | 用途 |
|------|------|
| `verify` | 编辑后证据汇总：变更文件、风险、测试建议、check 结果、可选 LSP 诊断和图对比 |
| `verify --quick` | 快速模式：跳过编译器/LSP，只看变更和风险 |
| `check` | 运行语言诊断工具（tsc、cargo check、ruff、mypy、go vet 等） |
| `diagnostics` | 针对特定文件的 LSP 精确诊断 |

### 专项分析（Specialized Analysis）

| 命令 | 用途 |
|------|------|
| `routes` | HTTP/API 路由清单 |
| `orphan` | 死代码检测：发现未被引用或低引用的符号 |
| `hotspots` | 高复杂度/高变更频率的文件清单 |
| `git-history` | 符号的 Git 历史上下文（谁改过、为什么改） |

### 基础设施（Infrastructure）

| 命令 | 用途 |
|------|------|
| `cache save` | 编辑前保存图快照，用于后续对比 |
| `diff` | 对比当前图与缓存快照的差异 |
| `lsp doctor` | 检查本地可用的 LSP 服务器 |
| `doctor` | 整体自检：解析器、依赖、LSP |

---

## 核心工作流

### 初次接触陌生项目

```bash
repomap overview --project /path/to/project
repomap query --project /path/to/project --query "feature or domain keyword"
repomap file-detail --project /path/to/project --file-path src/foo.ts
```

适用场景：AI 第一次进入项目，需要了解项目结构、核心文件、入口点和相关测试。

### 定位功能 / Bug / 符号

```bash
repomap query --project /path/to/project --query "auth token refresh"
repomap query-symbol --project /path/to/project --symbol refreshToken
repomap refs --project /path/to/project --symbol refreshToken
repomap call-chain --project /path/to/project --symbol refreshToken
```

适用场景：你知道业务关键词，但不知道具体文件在哪。

### 编辑前规划（推荐）

```bash
repomap file-detail --project /path/to/project --file-path src/foo.ts
repomap impact --project /path/to/project --files src/foo.ts --with-symbols
```

`--with-symbols` 会额外输出：

- **关键符号**：目标文件中的重要函数/类/方法。如果你要改其中之一，说明改的是行为敏感区域。
- **建议阅读顺序**：优先看目标文件，然后是高置信度受影响文件，再是关联测试。
- **风险等级 / 风险说明**：业务层面的警告信号，比如大范围结构影响、敏感领域、配置/构建变更。
- **LSP 可用性提示**：本地是否有可用的 LSP 做更精确的检查。

### 编辑后验证

```bash
repomap verify --project /path/to/project
repomap verify --project /path/to/project --with-lsp
repomap verify --project /path/to/project --with-diff
```

`verify` 输出：

- Git 变更文件清单
- 风险评估和遗漏检查警告
- 受影响文件和关联测试建议
- `check` 结果（编译器/类型检查/代码检查）
- 可选：LSP 诊断（`--with-lsp`）
- 可选：图对比差异（`--with-diff`，需事先用 `cache save` 保存基线）
- 最终检查清单：告诉 AI 哪些证据还没覆盖

### 行为变更前的符号追踪

```bash
repomap query-symbol --project /path/to/project --symbol helper --file-path src/foo.ts
repomap call-chain --project /path/to/project --symbol helper --file-path src/foo.ts
repomap refs --project /path/to/project --symbol helper --file-path src/foo.ts --with-lsp
```

适用场景：准备修改一个函数/方法的行为，先搞清楚所有调用者和被调用者。

### 仅知道业务关键词时

```bash
repomap query --project /path/to/project --query "login token refresh"
```

然后检查最相关的文件：

```bash
repomap file-detail --project /path/to/project --file-path src/auth/session.ts
```

### 快速变更风险检查

```bash
repomap verify --project /path/to/project --quick
```

适合提交前快速检查：检测所有变更文件，运行影响分析，去重测试建议，标记缺失测试覆盖，输出风险等级。不运行编译器/LSP 检查，所以速度很快。

---

## 命令详细说明

### overview — 项目全局概览

初次进入项目的首选命令。

```bash
repomap overview --project /path/to/project
```

输出内容：

- **项目结构**：目录拓扑和模块划分
- **入口点**：`main`、`index`、入口配置文件等
- **核心文件**：PageRank 排名最高的关键实现文件
- **热点文件**：符号密度高或近期变更频繁的文件
- **关键符号**：重要的函数、类、方法
- **建议阅读顺序**：从入口到核心实现再到测试
- **支撑文件清单**：README、CLAUDE.md、package.json、脚本等非 AST 文件

可选参数：

- `--with-heat`：标记近期变更文件
- `--with-co-change`：启用 Git 协同变更分析（较慢）
- `--json`：JSON 格式输出

---

### query — 主题/关键词搜索

当你不知道精确文件名时的首选命令。

```bash
repomap query --project /path/to/project --query "auth token refresh"
```

用业务关键词搜索代码库，通过路径匹配、文件名匹配和符号名匹配来发现相关文件。输出包含建议阅读顺序、核心文件、支撑文件、关联测试和关键符号。

参数：

- `--query`：搜索关键词（必填）
- `--paths`：限定搜索目录
- `--exclude`：排除目录
- `--no-tests`：不包含测试文件
- `--json`：JSON 格式输出

---

### query-symbol — 符号查找

```bash
repomap query-symbol --project /path/to/project --symbol helper
repomap query-symbol --project /path/to/project --symbol helper --file-path src/foo.ts
```

精确或模糊的符号名查找。加上 `--file-path` 可在多文件重复符号名时消歧。加上 `--with-lsp` 可获取本地 LSP 的精确定义位置。

---

### file-detail — 文件符号详情

```bash
repomap file-detail --project /path/to/project --file-path src/foo.ts
```

输出指定文件中的符号清单，含类型（函数/类/方法/变量/接口/类型）、签名和行号范围。默认输出紧凑格式，支持 `--full` 展开。

---

### call-chain — 调用链追踪

```bash
repomap call-chain --project /path/to/project --symbol helper
repomap call-chain --project /path/to/project --symbol helper --depth 3
repomap call-chain --project /path/to/project --symbol helper --file-path src/foo.ts
```

追踪符号的调用者（callers）和被调用者（callees）。默认深度为 2 层。通过树形缩进展示调用链结构。加上 `--file-path` 进行同名符号消歧。

---

### impact — 影响分析（编辑前计划器）

```bash
repomap impact --project /path/to/project --files src/foo.ts
repomap impact --project /path/to/project --files src/foo.ts --with-symbols
repomap impact --project /path/to/project --files src/foo.ts --with-symbols --json
```

核心编辑前规划命令。回答一个问题："如果我要改这个文件，我应该先知道什么？"

普通 `impact` 输出：

- 目标文件
- 可能受影响的其他文件（通过调用/导入关系发现）
- 关联测试文件
- 风险等级和说明

`--with-symbols` 额外输出：

- **关键符号**：文件中的重要函数/类/方法及其简要说明
- **建议阅读顺序**：优先看哪些文件
- **风险说明**：结构性风险（如改了核心模块）、领域敏感性（如改了支付/鉴权）、变更类型风险（如改了配置/构建）
- **LSP 可用性提示**：是否建议用 LSP 做进一步精确检查

---

### verify — 编辑后证据汇总

```bash
repomap verify --project /path/to/project
repomap verify --project /path/to/project --quick
repomap verify --project /path/to/project --with-lsp
repomap verify --project /path/to/project --with-diff
repomap verify --project /path/to/project --json
```

编辑后的标准证据门禁。在一个报告中聚合：

1. Git 变更文件（暂存/未暂存/未跟踪/重命名）
2. 风险评估和遗漏检查警告
3. 受影响文件和关联测试建议
4. `check` 结果（运行语言诊断工具）
5. 可选：LSP 诊断（`--with-lsp`）
6. 可选：图对比差异（`--with-diff`，需事先 `cache save`）
7. 最终检查清单

`--quick` 模式跳过编译器/LSP 检查，只做 Git 变更分析和风险评估，适合提交前快速检查。

注意：`verify` 不会自动运行项目测试套件。它会建议可能相关的测试，但 AI 需要自行运行或明确说明为什么跳过。

---

### check — 语言诊断

```bash
repomap check --project /path/to/project
repomap check --project /path/to/project --modified-file src/foo.ts
repomap check --project /path/to/project --with-lsp --modified-file src/foo.ts
```

运行项目语言相关的诊断工具：

- TypeScript / JavaScript：`tsc --noEmit`
- Rust：`cargo check`
- Python：`ruff check`、`mypy`
- Go：`go vet`

`--modified-file` 仅检查指定文件（传给工具做增量检查）。`--with-lsp` 会额外启动本地 LSP 获取诊断。

---

### routes — HTTP/API 路由清单

```bash
repomap routes --project /path/to/project
repomap routes --project /path/to/project --json
```

扫描项目中的 HTTP/API 路由定义。支持常见框架：

- Express / Fastify / Koa（JS/TS）
- Flask / FastAPI / Django（Python）
- Gin / Echo（Go）
- Axum / Actix（Rust）

输出每个路由的：HTTP 方法、路径、处理器函数和所在文件。`--json` 输出机器可读格式。

注意：`routes` 聚焦生产路由定义，会自动过滤测试/e2e/spec 文件中的路由噪音。

---

### orphan — 死代码检测

```bash
repomap orphan --project /path/to/project
repomap orphan --project /path/to/project --json
repomap orphan --project /path/to/project --min-confidence 0.3
```

发现项目中未被引用或引用次数极低的符号。输出包括：符号名、类型、所在文件、引用次数和置信度评分。

`--min-confidence` 设置置信度阈值（默认 0.1），值越低发现的候选越多但误报也越多。

---

### refs — 引用发现

```bash
repomap refs --project /path/to/project
repomap refs --project /path/to/project --symbol helper
repomap refs --project /path/to/project --symbol helper --file-path src/foo.ts --with-lsp
```

查找符号的所有引用位置。不加 `--symbol` 时扫描全局引用拓扑。加上 `--with-lsp` 时启动本地 LSP 获取精确引用证据。

---

### diagnostics — LSP 诊断

```bash
repomap diagnostics --project /path/to/project --source lsp --files src/foo.ts src/bar.ts
```

针对特定文件启动本地 LSP 服务器并获取诊断结果（错误、警告、信息）。比 `check` 更精确，但需要 LSP 服务器可用。

---

### lsp doctor — LSP 可用性检查

```bash
repomap lsp doctor --project /path/to/project
```

检测项目本地可用的 LSP 服务器。检查路径包括：

- 项目本地 `node_modules/.bin/` 等
- 系统 `PATH`
- npm / pnpm / yarn / bun 的全局 bin 目录
- pipx / uv 的 bin 目录
- cargo / go 的 bin 目录
- mason 的 bin 目录

支持检测的 LSP 服务器：

- `typescript-language-server`（JS/TS）
- `pyright-langserver` / `pylsp`（Python）
- `rust-analyzer`（Rust）
- `gopls`（Go）

注意：`lsp doctor` 只检测不安装。缺失的 LSP 服务器会报告为"跳过"，不影响 `repomap` 核心功能。

---

### cache save / diff — 图快照与对比

```bash
# 编辑前保存基线
repomap cache save --project /path/to/project

# ... 执行编辑 ...

# 对比当前图与基线
repomap diff --project /path/to/project
repomap verify --project /path/to/project --with-diff
```

`cache save` 将当前符号图的快照保存到 `~/.cache/repomap/`。`diff` 对比当前图与基线图的差异。缓存按项目规范路径键控，因此相同项目的相对和绝对路径引用共享缓存，不同目录下的同名项目保持隔离。

---

### doctor — 整体自检

```bash
repomap doctor
```

检查：Python 版本、tree-sitter 解析器可用性（JS/TS/Python/Go/Rust/HTML/CSS/JSON）、项目依赖完整性、本地 LSP 可用性。建议在新环境安装后首先运行。

---

## 项目结构

```
repomap/
├── repomap_cli/              # CLI 入口模块
│   └── __init__.py           # 命令路由和参数解析
├── repomap_core.py           # 扫描管线：文件遍历、AST 解析、图构建
├── repomap_parser.py         # tree-sitter AST 解析，导入/导出绑定提取
├── repomap_resolver.py       # 导入路径解析（含别名、baseUrl、monorepo）
├── repomap_ranking.py        # 图分析：PageRank、中心度、依赖连通性
├── repomap_topic.py          # 主题评分、测试文件匹配、文件角色识别
├── repomap_check.py          # 语言诊断工具封装（tsc、cargo check、ruff 等）
├── repomap_lsp.py            # LSP 集成：启动、诊断、定义跳转、引用查找
├── repomap_toolkit.py        # 缓存、diff、Git 历史等辅助逻辑
├── repomap_ai.py             # Markdown 报告渲染和 LLM 提示生成
├── repomap_support.py        # 核心数据结构定义
├── tests/                    # 单元测试和二进制 E2E 测试
│   ├── test_repomap_*.py     # 各模块单元测试
│   └── test_repomap_binary_e2e.py  # 二进制端到端测试
├── docs/                     # 文档
│   └── deliverables/         # 交付报告
├── dist/                     # 构建产物
│   └── repomap               # Linux 预编译二进制
├── skills/                   # AI 技能定义
├── .github/workflows/        # CI 构建矩阵
│   └── build-binaries.yml    # 跨平台构建工作流
├── AGENTS.md                 # AI agent 上下文说明
├── CLAUDE.md -> AGENTS.md    # 软链接
├── SKILL.md                  # AI 技能描述
├── pyproject.toml            # Python 项目配置
└── uv.lock                   # 依赖锁定文件
```

### 各模块职责

| 文件 | 职责 |
|------|------|
| `repomap_cli/` | CLI 入口和参数解析。所有子命令的路由逻辑 |
| `repomap_core.py` | 扫描管线核心。统合文件遍历、AST 解析、依赖图构建和 PageRank 计算 |
| `repomap_parser.py` | tree-sitter 语法解析。提取符号定义、导入/导出绑定。支持 JS/TS/Python/Go/Rust |
| `repomap_resolver.py` | 导入路径解析。处理别名、`baseUrl`、monorepo 自引用、`node_modules` 查找 |
| `repomap_ranking.py` | 图分析引擎。PageRank、入度/出度、连通分量、符号中心度 |
| `repomap_topic.py` | 主题搜索。关键词评分、文件角色分类、测试匹配启发式 |
| `repomap_check.py` | 外部诊断工具封装。运行 tsc / cargo check / ruff / mypy / go vet 并解析输出 |
| `repomap_lsp.py` | LSP 集成层。服务器发现、stdio 协议、诊断/定义/引用请求 |
| `repomap_toolkit.py` | 辅助逻辑。缓存读写、图 diff、Git 历史、变更汇总 |
| `repomap_ai.py` | Markdown 报告渲染。把所有命令输出格式化为 AI 友好的文本 |
| `repomap_support.py` | 核心数据类型。符号、文件节点、图边、配置结构 |

### 数据流

```
文件系统
    │
    ▼
repomap_core.py  ──►  repomap_parser.py  ──►  AST 节点 + 导入绑定
    │                                                │
    │                                                ▼
    │                                    repomap_resolver.py  ──► 解析后的导入图
    │                                                │
    ▼                                                ▼
repomap_ranking.py  ◄────────────────  完整符号依赖图
    │
    ▼
PageRank 分数 + 中心度指标
    │
    ▼
命令层（overview / query / impact / verify / ...）
    │
    ▼
repomap_ai.py  ──►  Markdown / JSON 输出
```

---

## 维护策略

### 更新节奏

不需要固定的频繁发版节奏，除非出现以下情况：

- CLI 开始遗漏你仓库中的重要符号关系
- 你用的框架出现了新的导入/导出模式
- tree-sitter 解析器行为变化
- `check` 支持的语言工具链有重大变化
- 你增加了对工作流有价值的新命令

建议的实际节奏：

- 每次有实质性影响假阳性/假阴性时：尽快更新
- 每次增加新的语言模式或仓库风格支持时：尽快更新
- 否则：每 1-2 个月做一次轻量自检

### 自检命令

```bash
repomap doctor
repomap overview --project /some/repo
repomap query --project /some/repo --query main
repomap impact --project /some/repo --files src/main.ts --with-symbols
repomap verify --project /some/repo
```

### 构建二进制

```bash
# 需要安装 PyInstaller
pip install pyinstaller
python -m repomap_cli build-binary --output dist
./dist/repomap doctor
```

### 运行测试

```bash
# 运行全部单元测试
uv run --with tree-sitter,tree-sitter-* python -m unittest discover -s tests -v

# 运行二进制端到端测试（会构建二进制再运行）
uv run --with pyinstaller,tree-sitter,tree-sitter-* python -m unittest tests/test_repomap_binary_e2e.py -v
```

---

## 已知限制

- **动态调度**：运行时动态分发、反射、运行时生成代码和字符串构建的调用可能被遗漏。
- **跨平台二进制**：Linux 二进制可在本地构建。Windows/macOS 二进制需在对应平台或 CI（GitHub Actions）上构建。
- **图对比**：`diff` 和 `verify --with-diff` 依赖于编辑前通过 `cache save` 保存的缓存基线。
- **路由检测**：`routes` 聚焦生产路由定义，会过滤测试/e2e 文件中的 DSL 噪音。如需 mock 路由，请使用 `query` 或 `file-detail`。
- **概览文件清单**：`overview` 对非 AST 文件（Markdown、shell、service 文件）仅做轻量清单列出，不解析其内容。
- **主题搜索**：`query` 使用手工加权的关键词评分（路径 + 文件名 + 符号名）。后续计划升级为 BM25 以获得更好的多关键词排序。
- **事件级耦合**：`impact` 和 `verify --quick` 通过图边分析检测受影响文件，但事件级耦合（CustomEvent、postMessage）尚未检测（计划作为独立的 `event-map` 命令）。
- **测试匹配**：使用 5 级启发式（名称 → 路径 → 导入 → 符号 → Git 协同变更）。覆盖率取决于项目结构和 Git 历史深度。
- **Git 依赖**：`verify --quick` 依赖于 `git status`，在 Git 仓库中效果最佳。
- **JS/TS 语法**：`.tsx` 文件使用专用的 TSX tree-sitter 语法解析，`doctor` 会报告解析器可用性。

---

## 从 MCP 到 CLI

`repomap` 的早期版本基于 MCP（Model Context Protocol）服务器运行。现在的版本是直接 CLI 子命令，不再依赖 MCP 服务器、后台进程或长时间运行的守护进程。

| 之前的 MCP 工具 | 现在的 CLI 命令 |
|---|---|
| `repomap_scan` | `repomap scan --project <path>` |
| `repomap_overview` | `repomap overview --project <path>` |
| `repomap_call_chain` | `repomap call-chain --project <path> --symbol <name>` |
| `repomap_query_symbol` | `repomap query-symbol --project <path> --symbol <name>` |
| `repomap_file_detail` | `repomap file-detail --project <path> --file-path <file>` |
| `repomap_hotspots` | `repomap hotspots --project <path>` |
| `repomap_cache` | `repomap cache save --project <path>` |
| `repomap_diff` | `repomap diff --project <path>` |
| `repomap_git_history` | `repomap git-history --project <path> --symbol <name>` |
| `repomap_refs` | `repomap refs --project <path> [--symbol <name>]` |
| `repomap_orphan` | `repomap orphan --project <path> [--json] [--min-confidence N]` |
| `repomap_check` | `repomap check --project <path>` |
| *(新增)* | `repomap query --project <path> --query <keyword>` |
| *(新增)* | `repomap impact --project <path> --files <file...> [--with-symbols]` |
| *(新增)* | `repomap verify --project <path> [--quick] [--with-lsp] [--with-diff]` |
| *(新增)* | `repomap routes --project <path> [--json]` |
| *(新增)* | `repomap diagnostics --project <path> --source lsp --files <file...>` |
| *(新增)* | `repomap lsp doctor --project <path>` |

---

## 跨平台构建

### 本地 Linux 构建

```bash
# 使用 PyInstaller 构建单文件二进制
pip install pyinstaller
python -m repomap_cli build-binary --output dist

# 验证
./dist/repomap doctor
./dist/repomap overview --project /some/repo
```

### CI 矩阵构建

参见 `.github/workflows/build-binaries.yml`：

- Ubuntu Linux → `dist/repomap`
- Windows → `dist/repomap.exe`
- macOS → `dist/repomap`

CI 工作流运行顺序：完整测试套件 → 二进制构建 → 二进制冒烟测试 → 构建产物上传。

### Windows 注意事项

- 输出文件名为 `repomap.exe`
- 冒烟测试通过 PowerShell 运行
- PATH 安装路径通常为 `%USERPROFILE%\AppData\Local\Microsoft\WindowsApps`
- 建议通过 GitHub Actions 在 `windows-latest` 上构建，不推荐在 Linux 上交叉编译

### macOS 注意事项

- 输出文件名仍为 `repomap`
- 必须在 macOS 原生 runner 上构建
- 分发到外部使用时可能需要 Apple 签名/公证
- 未签名的二进制可能触发 Gatekeeper 警告

---

## 相关项目

- [aider](https://github.com/Aider-AI/aider) — AI 结对编程 CLI 工具，repomap 的理念起源。我们对 aider 团队的开创性工作表示敬意。
- [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) — repomap 已被集成为此项目的内置 Rust 工具 **deepmap**。DeepSeek-TUI 是一个终端界面的 AI 编程助手。
- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) — repomap 的核心解析引擎，提供增量语法解析能力。

---

## 许可证

[MIT](LICENSE)

Copyright (c) 2026 gjczone

---

**如果你觉得 repomap 有用，欢迎给 [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) 点个 Star。也建议去看看 [aider](https://github.com/Aider-AI/aider) —— 没有它的开创性工作，就不会有 repomap。**
