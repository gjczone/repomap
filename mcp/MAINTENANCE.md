# 维护指南：repomap 更新时如何同步 MCP 服务器

本文档说明当 repomap CLI 发生变更时，如何同步更新 `repomap-mcp-server`。

---

## 架构关系

```
repomap (Python CLI)          repomap-mcp-server (TypeScript MCP)
├── src/cli/cli.py            ├── src/repomap.ts    ← 子进程调用层 + 自动安装
│   └── build_parser()        ├── src/tools.ts      ← 工具注册（参数映射）
│       └── 子命令定义         └── src/index.ts      ← 服务器入口（启动时自动安装）
└── src/core.py

repomap-bin (npm 二进制分发)
├── repomap-bin                ← 主包（JS wrapper + optionalDependencies）
├── repomap-bin-linux-x64      ← Linux x64 二进制
├── repomap-bin-darwin-arm64   ← macOS arm64 二进制
└── repomap-bin-windows-x64    ← Windows x64 二进制
```

MCP 服务器是 repomap CLI 的**薄适配层**，所有分析逻辑都在 CLI 中实现。同步更新的核心是：**确保 MCP 工具的参数定义与 CLI 的 argparse 定义一致**。

---

## 自动安装机制

服务器启动时会调用 `ensureRepomapInstalled()`（定义在 `src/repomap.ts`），按以下优先级查找或安装 repomap 二进制：

1. **npm 平台包** — 检查 `node_modules/repomap-bin-{platform}/repomap` 是否存在且可运行（随 npm 依赖自动安装，国内有 npmmirror 镜像，速度快）
2. **PATH 查找** — 检测 `repomap` 命令是否在系统 PATH 中可用（用户手动安装的）
3. **pip 安装** — 按 `python3` → `python` 顺序查找 Python，执行 `pip install repomap-cli`（国内有清华/阿里镜像，速度快）；如果遇到 Ubuntu 的 `externally-managed-environment` 错误，自动加 `--break-system-packages` 重试
4. **报错** — 以上都失败时，输出清晰的错误信息和手动安装指引

**维护注意**：
- 如果 repomap-cli 的 PyPI 包名变了，需要修改 `pipInstall()` 函数中的包名
- 如果平台包的 npm 包名变了，需要修改 `package.json` 中的 `optionalDependencies` 和 `src/repomap.ts` 中的 `PLATFORM_PACKAGES` 映射

---

## 完整发布流程

### 前提：已登录 npm

```bash
npm login
# 或设置 token
echo "//registry.npmjs.org/:_authToken=YOUR_TOKEN" > ~/.npmrc
```

### 步骤一：更新 PyPI 包（repomap-cli）

当上游 repomap Python 代码有更新时：

```bash
# 1. 克隆上游仓库
git clone https://github.com/gjczone/repomap.git /tmp/repomap-publish
cd /tmp/repomap-publish

# 2. 重命名 src/ → repomap/（PyPI 不允许包名叫 src）
mv src repomap

# 3. 修复 __main__.py 中的绝对导入
sed -i 's/from src\./from repomap./g' repomap/cli/__main__.py

# 4. 更新 pyproject.toml
#    - packages = ["repomap"]（不是 ["src"]）
#    - [project.scripts] repomap = "repomap.cli:main"（不是 src.cli:main）
#    - 补充 readme, license, authors, keywords, classifiers, project.urls

# 5. 构建包
pip install build
python3 -m build

# 6. 发布到 PyPI
pip install twine
twine upload dist/* -u __token__ -p YOUR_PYPI_TOKEN

# 7. 验证
pip install repomap-cli
repomap --help
```

**关键注意**：上游 `pyproject.toml` 的包目录叫 `src`，必须重命名为 `repomap` 后才能发布到 PyPI，否则会和其他包冲突。

### 步骤二：更新 npm 平台包（repomap-bin-*）

当 repomap 发布新版本后，需要更新各平台的 npm 二进制包：

```bash
# 1. 下载各平台二进制
mkdir -p /tmp/repomap-platforms/linux-x64 /tmp/repomap-platforms/darwin-arm64 /tmp/repomap-platforms/win32-x64

curl -L -o /tmp/repomap-platforms/linux-x64/repomap \
  https://github.com/gjczone/repomap/releases/download/vX.Y.Z/repomap-linux

curl -L -o /tmp/repomap-platforms/darwin-arm64/repomap \
  https://github.com/gjczone/repomap/releases/download/vX.Y.Z/repomap-macos

curl -L -o /tmp/repomap-platforms/win32-x64/repomap.exe \
  https://github.com/gjczone/repomap/releases/download/vX.Y.Z/repomap.exe

# 2. 创建各平台包的 package.json 并发布

# --- linux-x64 ---
mkdir -p /tmp/repomap-platforms/linux-x64-pkg
cat > /tmp/repomap-platforms/linux-x64-pkg/package.json << 'EOF'
{
  "name": "repomap-bin-linux-x64",
  "version": "X.Y.Z",
  "description": "repomap binary for Linux x64",
  "os": ["linux"],
  "cpu": ["x64"],
  "files": ["repomap"],
  "license": "MIT"
}
EOF
cp /tmp/repomap-platforms/linux-x64/repomap /tmp/repomap-platforms/linux-x64-pkg/
chmod +x /tmp/repomap-platforms/linux-x64-pkg/repomap
cd /tmp/repomap-platforms/linux-x64-pkg && npm publish --access public

# --- darwin-arm64 ---
mkdir -p /tmp/repomap-platforms/darwin-arm64-pkg
cat > /tmp/repomap-platforms/darwin-arm64-pkg/package.json << 'EOF'
{
  "name": "repomap-bin-darwin-arm64",
  "version": "X.Y.Z",
  "description": "repomap binary for macOS arm64",
  "os": ["darwin"],
  "cpu": ["arm64"],
  "files": ["repomap"],
  "license": "MIT"
}
EOF
cp /tmp/repomap-platforms/darwin-arm64/repomap /tmp/repomap-platforms/darwin-arm64-pkg/
chmod +x /tmp/repomap-platforms/darwin-arm64-pkg/repomap
cd /tmp/repomap-platforms/darwin-arm64-pkg && npm publish --access public

# --- windows-x64 ---
mkdir -p /tmp/repomap-platforms/win32-x64-pkg
cat > /tmp/repomap-platforms/win32-x64-pkg/package.json << 'EOF'
{
  "name": "repomap-bin-windows-x64",
  "version": "X.Y.Z",
  "description": "repomap binary for Windows x64",
  "os": ["win32"],
  "cpu": ["x64"],
  "files": ["repomap.exe"],
  "license": "MIT"
}
EOF
cp /tmp/repomap-platforms/win32-x64/repomap.exe /tmp/repomap-platforms/win32-x64-pkg/
cd /tmp/repomap-platforms/win32-x64-pkg && npm publish --access public
```

**注意**：三个平台包的 `version` 必须和 repomap 上游版本一致。

### 步骤三：更新 repomap-bin 主包

```bash
cd /path/to/repomap-bin

# 1. 更新 package.json 中的 version 和 optionalDependencies 版本号
#    version: "X.Y.Z"
#    optionalDependencies 中三个平台包的版本也要更新

# 2. 发布
npm publish --access public
```

### 步骤四：更新 repomap-mcp-server

```bash
cd /path/to/repomap-mcp-server

# 1. 修改代码（同步 CLI 参数变更等）
# 2. 更新 package.json 中的 version 和 repomap-bin 依赖版本
# 3. 构建
npm run build

# 4. 验证（见下方验证流程）

# 5. 发布
npm publish --access public
```

### 发布顺序总结

```
1. PyPI: repomap-cli@X.Y.Z
2. npm:  repomap-bin-linux-x64@X.Y.Z
        repomap-bin-darwin-arm64@X.Y.Z
        repomap-bin-windows-x64@X.Y.Z
3. npm:  repomap-bin@X.Y.Z
4. npm:  repomap-mcp-server@X.Y.Z
```

**必须按此顺序发布**，因为后续包依赖前面的包。

---

## 需要同步的变更类型

### 1. CLI 新增子命令 → MCP 新增工具

**步骤**：

1. 在 `src/tools.ts` 中新增 `server.registerTool(...)` 调用
2. `inputSchema` 中的参数名使用 snake_case（会自动转换为 CLI 的 kebab-case）
3. 如果命令支持 `--json`，将 `expectJson` 设为 `true`，并使用 `jsonResult()` 返回
4. 如果命令有位置参数（如 `cache save` 的 `save`），使用 `{ flags: {...}, positional: [...] }` 格式
5. 更新 `README.md` 的工具列表

**示例**：假设 repomap 新增了 `deps` 子命令

```typescript
server.registerTool(
  "repomap_deps",
  {
    title: "Dependency Graph",
    description: "Analyze dependency relationships between modules.",
    inputSchema: {
      project: ProjectPathSchema,
      format: z.enum(["tree", "flat"]).optional()
        .describe("Output format (default: tree)"),
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  async ({ project, format }) => {
    try {
      const output = await runRepomap("deps", { project, format }, true);
      return jsonResult(output);
    } catch (err) {
      return toolError(err instanceof Error ? err.message : String(err));
    }
  },
);
```

### 2. CLI 子命令新增参数 → MCP 工具新增 inputSchema 字段

**步骤**：

1. 在 `src/tools.ts` 中找到对应的 `server.registerTool` 调用
2. 在 `inputSchema` 中添加新字段（snake_case 命名）
3. 在 handler 的解构参数中添加新字段
4. 在 `runRepomap` 的 flags 对象中传递新字段

**示例**：假设 `overview` 命令新增了 `--with-deps` 参数

```typescript
inputSchema: {
  project: ProjectPathSchema,
  max_files: MaxFilesSchema,
  with_deps: z.boolean().optional()
    .describe("Include dependency graph section"),
},

async ({ project, max_files, with_deps, ... }) => {
  const output = await runRepomap("overview", {
    project, max_files, with_deps, ...
  }, true);
```

### 3. CLI 参数改名或删除 → MCP 对应修改

**步骤**：

1. 更新 `inputSchema` 中的字段名和 handler 解构
2. 更新 `runRepomap` flags 对象中的字段名
3. **注意**：如果旧参数仍有用户依赖，考虑保留一个过渡期

### 4. CLI 子命令删除 → MCP 删除对应工具

**步骤**：

1. 从 `src/tools.ts` 中删除对应的 `server.registerTool` 调用
2. 更新 `README.md` 的工具列表

### 5. CLI 输出格式变更 → MCP 调整解析逻辑

- 如果 `--json` 输出的 JSON 结构变了，MCP 服务器无需改动（它只是透传 JSON）
- 如果 CLI 原来输出文本、现在改为 JSON（或反之），调整 `expectJson` 参数和返回方式（`textResult` vs `jsonResult`）

---

## 参数名转换规则

MCP 工具的 `inputSchema` 使用 **snake_case**，`src/repomap.ts` 中的 `buildArgs` 函数会自动将 snake_case 转为 CLI 的 kebab-case：

| MCP inputSchema | CLI 参数 |
|---|---|
| `max_files` | `--max-files` |
| `file_path` | `--file-path` |
| `with_lsp` | `--with-lsp` |
| `no_tests` | `--no-tests` |

**例外**：位置参数（如 `cache save` 的 `save`）需要通过 `positional` 数组单独传递，不走 `buildArgs` 转换。

---

## 验证流程

每次同步更新后，执行以下验证：

```bash
# 1. 构建
cd repomap-mcp-server
npm run build

# 2. 验证工具列表（应包含所有工具）
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n' \
  | timeout 5 node dist/index.js 2>/dev/null

# 3. 验证具体工具调用
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"repomap_scan","arguments":{"project":"/path/to/test/project"}}}\n' \
  | timeout 30 node dist/index.js 2>/dev/null

# 4. 使用 MCP Inspector 进行交互式测试
npx @modelcontextprotocol/inspector node dist/index.js
```

---

## 快速检查清单

当 repomap 发布新版本时，按此清单检查：

- [ ] 阅读 repomap 的 CHANGELOG 或 commit log
- [ ] 检查 `src/cli/cli.py` 的 `build_parser()` 是否有新增/修改/删除的子命令
- [ ] 检查每个子命令的 argparse 参数是否有变化
- [ ] 检查 `--json` flag 的支持情况是否有变化
- [ ] 检查是否有新的位置参数（需要 `positional` 传递）
- [ ] 下载新版本各平台二进制，更新并发布 npm 平台包（repomap-bin-linux-x64 等）
- [ ] 更新并发布 repomap-bin 主包
- [ ] 更新 PyPI 包 repomap-cli（如 Python 代码有变更）
- [ ] 更新 `src/tools.ts` 中对应的工具定义
- [ ] 更新 `README.md` 的工具列表
- [ ] 执行验证流程
- [ ] 更新 `package.json` 版本号
- [ ] 提交、推送、发布 repomap-mcp-server 到 npm
