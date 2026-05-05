# RepoMap 全维度提升实施计划

> 目标：6 个维度全面达到 90-95%
> 总代码量：~1,800 行（含测试）
> 预计工期：10 个开发日

---

## 〇、语言覆盖决策依据

### DeepSeek V4 核心语言（Tier 1, 50+ expert-grade）

Python, Java, C++, JavaScript, TypeScript, Go, Rust, C#, PHP, Ruby, Swift, Kotlin, SQL, Shell/Bash

### 2025-2026 AI Vibecoding 语言趋势

| 语言 | GitHub 趋势 | AI 兼容性 | Vibecoding 适配度 |
|------|------------|----------|------------------|
| TypeScript | #1 (+66% YoY) | 极佳 | 强类型捕获 AI 错误 |
| Python | #2 (+48% YoY) | 极佳 | AI 基础设施首选 |
| Go | 快速增长 | 优秀 | 简单明确 = AI 少出错 |
| C# | TIOBE 2025 年度语言 | 良好 | Agentic AI 生态崛起 |
| Kotlin | 稳定增长 | 中等 | Android/移动端主力 |
| Swift | Apple 生态 | 中等 | iOS/macOS 必备 |
| Rust | 增速放缓 | 较弱 | 借用检查器 AI 难驾驭 |
| C++ | 存量巨大 | 较弱 | 内存管理 AI 易出错 |

### repomap 当前覆盖 vs 目标

```
当前:  Python ✅  JS/TS ✅  Go ✅  Rust ✅  HTML ✅  CSS ✅  JSON ✅
       ─────────────────────────────────────────────────────────────
目标:  + C  + C++  + Java  + Kotlin  + Swift  + C#  + PHP  + Ruby
```

全部 8 种新语言在 tree-sitter 都有成熟语法包，主要工作是编写 QUERIES 和扩展 EXT_TO_LANG。

---

## 一、当前基线 vs 目标

```
维度              当前    目标    增量    主力手段
─────────────────────────────────────────────────────
解析覆盖          80%  →  93%    +13    8 种新语言
验证闭环          55%  →  92%    +37    增量扫描 + 符号 diff + 测试盲区
AI 输出质量       65%  →  90%    +25    模板升级 + 上下文诊断 + 快捷命令
性能              60%  →  90%    +30    增量扫描（只解析变更文件）
图分析            70%  →  85%    +15    轻量数据流 + 断点检测
导入解析          90%  →  93%    +3     边缘修复（imports字段/Vite~/namespace pkg）
```

---

## 二、实施路线图（5 个 Phase）

```text
Phase 1 (~400行)      Phase 2 (~250行)       Phase 3 (~550行)      Phase 4 (~200行)     Phase 5 (~400行)
增量扫描              符号级 diff             新语言 ×4             上下文诊断            新语言 ×4
+ 测试盲区             + 断点检测             C / Java              + 影响排序            C++ / C# / PHP / Ruby
+ AI 输出升级          + 快捷命令             Kotlin / Swift         + 边缘修复            + 最终打磨

~2.5 天               ~2 天                  ~3 天                  ~1.5 天               ~2 天
```

---

## 三、Phase 1 详细设计（增量扫描 + 测试盲区 + AI 输出升级）

### 3.1 增量扫描（性能 60% → 85%）

**现状**：每次 `scan()` 全量解析所有文件。`_cache` 字典存 mtime，仅在同一进程内复用。

**改法**：

#### 3.1.1 扩展 session cache 为持久化增量缓存

文件：`repomap_support.py`（+50 行）

```python
@dataclass
class FileCacheEntry:
    """单文件缓存条目"""
    mtime: float
    symbols_json: list[dict]       # serialize_symbol 输出
    imports: list[str]
    import_bindings_json: list[dict]
    exports_json: list[dict]
    calls_json: list[dict]
    routes_json: list[dict]

@dataclass
class IncrementalCache:
    """持久化增量扫描缓存"""
    project_root_hash: str          # MD5 防路径变更
    git_head: str                   # 防代码回滚
    files: dict[str, FileCacheEntry]
    scan_stats_json: dict
```

存储路径：`~/.cache/repomap/<project_hash>/incremental.json`

#### 3.1.2 修改 `scan()` 支持增量模式

文件：`repomap_core.py`（+80 行）

```python
def scan(self, max_files=8000, max_scan_time=300.0, incremental=False):
    if incremental and self._incremental_cache_valid():
        changed = self._git_changed_files()        # git diff --name-only HEAD
        deleted = self._git_deleted_files()
        self._load_incremental_cache()
        self._purge_deleted(deleted)               # 删除已删除文件的符号/边
        files_to_scan = [f for f in changed if f not in deleted]
        # 只解析变更文件
        for f in files_to_scan:
            self._process_file(f)
        # 未变更文件从缓存还原（跳过 tree-sitter 解析）
        for f, entry in self._inc_cache.files.items():
            if f not in changed and f not in deleted:
                self._restore_from_cache(f, entry)
    else:
        # 现有全量逻辑不变
        ...
    # 图重建（不变，O(E) 很快）
    self._build_edges()
    self._calculate_pagerank()
    if not incremental:
        self._save_incremental_cache()             # 全量扫描后保存基线
```

关键性能数据：
- 全量扫描 500 文件：~30s
- 增量扫描（改 3 个文件）：~2s（只解析 3 个 + 从缓存反序列化 497 个）
- 提速约 15×

#### 3.1.3 CLI 接入

文件：`repomap_cli/cli.py`（+20 行）

```python
# verify 和 impact 默认尝试增量
verify_parser.add_argument("--no-incremental", action="store_true")
impact_parser.add_argument("--no-incremental", action="store_true")
```

`_scan_engine()` 内部逻辑：
1. 尝试加载增量缓存
2. 缓存存在且 git HEAD 匹配 → 自动增量模式
3. 缓存不存在或 `--no-incremental` → 全量模式

#### 3.1.4 增量缓存读写

文件：`repomap_toolkit.py`（+40 行）

```python
def save_incremental_cache(project_path, engine) -> Path
def load_incremental_cache(project_path) -> IncrementalCache | None
```

---

### 3.2 测试盲区检测（验证闭环 55% → 75%）

**现状**：`find_related_tests(target_files)` 找目标文件的测试。不知道哪些符号没有测试。

**改法**：

文件：`repomap_topic.py`（+55 行）

```python
def find_untested_symbols(
    graph: RepoGraph,
    min_incoming_calls: int = 2,
    min_score: float = 5.0,
    max_results: int = 30,
) -> list[dict]:
    """
    找出没有测试覆盖的符号，按风险排序。
    风险分 = incoming_calls × signal_weight
    """
    # 1. 收集所有测试文件中的符号 ID
    test_symbol_ids: set[str] = set()
    for f in graph.file_symbols:
        if is_test_like_file(f):
            test_symbol_ids.update(graph.file_symbols[f])

    # 2. BFS 一层：收集被测试符号直接引用的非测试符号
    covered: set[str] = set()
    for tsid in test_symbol_ids:
        for edge in graph.outgoing.get(tsid, []):
            if edge.target not in test_symbol_ids:
                covered.add(edge.target)

    # 3. 扫描未覆盖符号，计算风险分
    untested = []
    for sid, sym in graph.symbols.items():
        if sid in test_symbol_ids or sid in covered:
            continue
        if sym.kind in {"element", "selector", "class_selector", "id_selector", "json_key"}:
            continue
        incoming = sum(1 for e in graph.incoming.get(sid, []) if e.kind == "call")
        if incoming < min_incoming_calls:
            continue
        sw = _signal_weight_for_symbol(sym)
        score = incoming * sw * 5.0
        if score < min_score:
            continue
        untested.append({
            "symbol": sym.name, "kind": sym.kind,
            "file": sym.file, "line": sym.line,
            "incoming_calls": incoming, "risk_score": round(score, 1),
        })
    untested.sort(key=lambda x: -x["risk_score"])
    return untested[:max_results]


def _signal_weight_for_symbol(sym: Symbol) -> float:
    """独立版 signal_weight，不依赖 GraphAnalyzer 实例"""
    LOW_SIGNAL = {"element", "selector", "class_selector", "id_selector", "json_key"}
    if sym.kind in LOW_SIGNAL:
        return 0.002
    if sym.name in {"__init__", "__main__"}:
        return 0.35
    if sym.name.startswith("_") and sym.visibility == "private":
        return 0.85
    return 1.0
```

#### 集成到 verify

文件：`repomap_cli/cli.py`（+10 行）

在 `run_verify()` 中调用 `find_untested_symbols()`，结果附加到报告 payload。

#### verify 报告渲染

文件：`repomap_ai.py`（+25 行）

```
## Test Coverage Gaps ⚠

以下符号缺少测试覆盖，修改时需格外谨慎：

| Symbol | Kind | File | Callers | Risk |
|--------|------|------|---------|------|
| render_verify_report | function | repomap_ai.py | 21 | HIGH |
| DiagnosticRunner._parse_tsc_output | method | repomap_check.py | 1 | LOW |
```

---

### 3.3 AI 输出模板升级（AI 输出 65% → 80%）

#### 3.3.1 Overview 增加"最近活跃文件"板块

文件：`repomap_ai.py`（+25 行）

当 `--with-heat` 时，展示近 30 天 git 修改次数：

```
## 最近活跃文件（近 30 天）

| File | Modifications | Semantic Symbols |
|------|:---:|:---:|
| repomap_ai.py | 8 | 18.2 |
| repomap_cli/cli.py | 5 | 62.1 |
```

`_get_hot_files()` 改为返回 `dict[str, int]`（文件名 → 修改次数）。

#### 3.3.2 Overview 末尾增加"Quick Actions"板块

文件：`repomap_ai.py`（+20 行）

```
## Quick Actions

- 查看核心文件详情: `repomap file-detail --project . --file-path repomap_core.py`
- 搜索特定主题: `repomap query --project . --query <keyword>`
- 检查诊断: `repomap check --project .`
- 完整验证: `repomap verify --project .`
```

#### 3.3.3 Query 结果增加模糊匹配建议

文件：`repomap_topic.py`（+20 行）

当精确匹配结果为 0 时，用 `difflib.get_close_matches`（Python 标准库）建议最接近的符号名：

```python
def fuzzy_symbol_suggest(query: str, graph: RepoGraph, limit: int = 5) -> list[str]:
    """用编辑距离找最接近的符号名"""
    import difflib
    all_names = list({s.name for s in graph.symbols.values()})
    return difflib.get_close_matches(query, all_names, n=limit, cutoff=0.5)
```

#### 3.3.4 所有报告增加 `schema_version` 字段

文件：`repomap_cli/cli.py`（+10 行）

所有 JSON 输出增加 `"schema_version": "1.0"`，保 证 AI 解析器不会因格式变化出错。

---

## 四、Phase 2 详细设计（符号级 diff + 断点检测 + 快捷命令）

### 4.1 符号级 verify diff（验证闭环 75% → 88%）

**现状**：`verify --with-diff` 只展示图变化的统计数字（+N/-N 符号）。

**改法**：

#### 4.1.1 `compare_graph_snapshots` 增加调用者分析

文件：`repomap_support.py`（+25 行）

```python
def compare_graph_snapshots(
    current_symbols, current_edges,
    previous_symbols, previous_edges,
    incoming_map: dict[str, list[Edge]] | None = None,
) -> dict:
    # ... 现有逻辑 ...
    # 新增：为每个 modified_symbol 附加调用者和风险评级
    if incoming_map:
        for ms in result["modified_symbols"]:
            sid = ms["id"]
            callers = incoming_map.get(sid, [])
            ms["affected_callers"] = [
                {"name": e.source_name, "file": e.source_file}
                for e in callers[:10] if e.kind == "call"
            ]
            # 导出符号变更 → HIGH，否则 MEDIUM
            ms["risk"] = "HIGH" if ms.get("visibility") == "exported" else "MEDIUM"
```

#### 4.1.2 verify 报告新板块：Breaking Changes

文件：`repomap_ai.py`（+55 行）

```
## Breaking Changes ⚠

### 签名变更（HIGH RISK）
- `scan()` in `repomap_core.py:191`  [exported]
  旧: `def scan(self, max_files: int = 8000) -> None`
  新: `def scan(self, max_files: int = 8000, max_scan_time: float = 300.0) -> None`
  → 新增默认参数，向后兼容
  → 36 callers 应确认:
    `_scan_engine` (cli.py), `scan_project` (toolkit.py), ...

### 符号移除
- `old_helper()` from `repomap_utils.py:42`
  → 3 个位置仍引用此符号:
    `process_file` (repomap_core.py:156) via import
```

#### 4.1.3 CLI 接入

文件：`repomap_cli/cli.py`（+30 行）

`run_verify()` 中：
1. 加载增量缓存基线
2. 调用 `compare_graph_snapshots(..., incoming_map=engine.graph.incoming)`
3. 将结果传入 `render_verify_report`

---

### 4.2 断点检测（验证闭环 88% → 92%）

在 Phase 2 符号级 diff 基础上，增加**导出符号变更的自动风险评级**：

文件：`repomap_support.py`（+15 行）

```python
def assess_breaking_risk(modified_symbols, incoming_map) -> list[dict]:
    """评估每个修改符号的断裂风险"""
    risks = []
    for ms in modified_symbols:
        sid = ms["id"]
        callers = [e for e in incoming_map.get(sid, []) if e.kind == "call"]
        external_callers = [
            c for c in callers
            if incoming_map.get(c.source, [{}])[0].source_file != ms["file"]
        ]
        if ms.get("signature_changed") and external_callers:
            risks.append({
                "symbol": ms["name"], "file": ms["file"],
                "risk": "HIGH",
                "reason": f"导出符号签名变更，{len(external_callers)} 个外部调用者受影响",
                "affected_count": len(external_callers),
            })
    return sorted(risks, key=lambda r: -r["affected_count"])
```

---

### 4.3 快捷命令面板

文件：`repomap_ai.py`（+25 行）

在每个主要报告（overview/impact/verify）末尾追加一个统一的 "Related Commands" 板块：

```
## Related Commands

| 需求 | 命令 |
|------|------|
| 查看文件详情 | `repomap file-detail --project . --file-path <file>` |
| 分析修改影响 | `repomap impact --project . --files <file> --with-symbols` |
| 搜索符号 | `repomap query-symbol --project . --symbol <name>` |
| 查看调用链 | `repomap call-chain --project . --symbol <name>` |
| 完整验证 | `repomap verify --project .` |
```

---

## 五、Phase 3 详细设计（新语言 ×4：C / Java / Kotlin / Swift）

### 5.1 语言选择理由

| 语言 | 选择理由 |
|------|---------|
| **C** | DeepSeek T1 / 底层基础设施 / tree-sitter 成熟 / 语法简单（~50行 queries） |
| **Java** | DeepSeek T1 / 企业市场最大 / Spring Boot 生态 / vibecoding 常用 |
| **Kotlin** | DeepSeek T1 / Android 官方语言 / 用户明确要求 |
| **Swift** | DeepSeek T1 / Apple 全平台 / 用户明确要求 |

### 5.2 公共改动

#### 5.2.1 添加依赖

文件：`pyproject.toml`（+5 行）

```toml
[project.optional-dependencies]
languages = [
    "tree-sitter-c",
    "tree-sitter-java",
    # Kotlin: 社区 tree-sitter-kotlin 包，通过 pip 安装
    # Swift: tree-sitter-swift 官方包
]
```

#### 5.2.2 扩展 `_init_parsers`

文件：`repomap_parser.py`（+30 行）

每种新语言加 try/except 优雅降级，未安装时静默跳过。

#### 5.2.3 扩展 `EXT_TO_LANG`

文件：`repomap_parser.py`（+15 行）

```python
".c": "c", ".h": "c",
".java": "java",
".kt": "kotlin", ".kts": "kotlin",
".swift": "swift",
```

### 5.3 各语言 QUERIES

#### 5.3.1 C 语言（~50 行）

```python
"c": {
    "function": """
        (function_definition
          declarator: (function_declarator
            declarator: (identifier) @name)) @definition.function
    """,
    "class": """
        (struct_specifier name: (type_identifier) @name) @definition.struct
        (union_specifier name: (type_identifier) @name) @definition.union
        (enum_specifier name: (type_identifier) @name) @definition.enum
    """,
    "import": """
        (preproc_include path: (string) @path)
    """,
    "call": """
        (call_expression function: (identifier) @name) @reference.call
    """,
}
```

#### 5.3.2 Java 语言（~60 行）

```python
"java": {
    "function": """
        (method_declaration name: (identifier) @name) @definition.method
    """,
    "class": """
        (class_declaration name: (identifier) @name) @definition.class
        (interface_declaration name: (identifier) @name) @definition.interface
        (enum_declaration name: (identifier) @name) @definition.enum
    """,
    "import": """
        (import_declaration (scoped_identifier) @name)
        (import_declaration (identifier) @name)
    """,
    "call": """
        (method_invocation name: (identifier) @name) @reference.call
    """,
    "http_route": """
        ;; Spring Boot: @GetMapping("/path") / @PostMapping("/path")
        (annotation
          name: (identifier) @method
          arguments: (annotation_argument_list
            (element_value_pair
              value: (string_literal) @path)))
        (#match? @method "^(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)$")
    """,
}
```

#### 5.3.3 Kotlin 语言（~60 行）

```python
"kotlin": {
    "function": """
        (function_declaration name: (simple_identifier) @name) @definition.function
    """,
    "class": """
        (class_declaration name: (type_identifier) @name) @definition.class
        (object_declaration name: (type_identifier) @name) @definition.object
        (interface_declaration name: (type_identifier) @name) @definition.interface
    """,
    "import": """
        (import_header (identifier) @name)
    """,
    "call": """
        (call_expression (simple_identifier) @name) @reference.call
        (call_expression (navigation_expression (simple_identifier) @name)) @reference.call
    """,
}
```

#### 5.3.4 Swift 语言（~60 行）

```python
"swift": {
    "function": """
        (function_declaration name: (simple_identifier) @name) @definition.function
    """,
    "class": """
        (class_declaration name: (type_identifier) @name) @definition.class
        (struct_declaration name: (type_identifier) @name) @definition.struct
        (enum_declaration name: (type_identifier) @name) @definition.enum
        (protocol_declaration name: (type_identifier) @name) @definition.protocol
    """,
    "import": """
        (import_declaration (identifier) @name)
    """,
    "call": """
        (call_expression (simple_identifier) @name) @reference.call
        (call_expression (navigation_expression (simple_identifier) @name)) @reference.call
    """,
}
```

#### 5.3.5 扩展 `_init_parsers`

文件：`repomap_parser.py`（+25 行）

```python
# C
try:
    from tree_sitter_c import language as lang_c
    self.parsers["c"] = Parser(Language(lang_c()))
except Exception: ...

# Java
try:
    from tree_sitter_java import language as lang_java
    self.parsers["java"] = Parser(Language(lang_java()))
except Exception: ...

# Kotlin
try:
    from tree_sitter_kotlin import language as lang_kotlin
    self.parsers["kotlin"] = Parser(Language(lang_kotlin()))
except Exception: ...

# Swift
try:
    from tree_sitter_swift import language as lang_swift
    self.parsers["swift"] = Parser(Language(lang_swift()))
except Exception: ...
```

### 5.4 import 解析扩展

#### Java import 格式处理

文件：`repomap_resolver.py`（+20 行）

Java `import com.example.MyClass` → 模块名 `com.example`，需要解析为目录路径。

```python
def _resolve_java_import(self, source_file, imp):
    """Java import 路径 → 文件路径"""
    # import com.example.MyClass → com/example/MyClass.java
    parts = imp.split(".")
    path = PurePosixPath(*parts)
    return self._candidate_files_for_base_path(path)
```

在 `resolve_import_targets` 中，检测到 `.java` 文件时优先走 Java import 解析。

#### Swift import 格式处理

文件：`repomap_resolver.py`（+15 行）

Swift `import UIKit` → 模块名匹配（与现有 module stem 匹配逻辑兼容，无需大改）。

---

## 六、Phase 4 详细设计（上下文诊断 + 影响排序 + 边缘修复）

### 6.1 上下文诊断（AI 输出 80% → 87%）

**现状**：`check` 输出是扁平的 `file:line: error: message`。

**改法**：

文件：`repomap_check.py`（+35 行）

在已有的 `_resolve_symbols`（line range 匹配）基础上，追加 caller 信息：

```python
def _resolve_symbols(self, results, symbols_map, graph=None):
    # ... 现有 line range matching ...
    if graph:
        for result in results:
            for issue in result.errors + result.warnings:
                if issue.symbol_id and issue.symbol_id in graph.symbols:
                    sym = graph.symbols[issue.symbol_id]
                    callers = [
                        graph.symbols[e.source].name
                        for e in graph.incoming.get(issue.symbol_id, [])
                        if e.kind == "call"
                    ][:5]
                    issue.callers = callers
                    issue.symbol_name = sym.name
```

在 `_build_report` 中，为每个错误追加上下文：

```json
{
  "file": "repomap_ai.py",
  "line": 355,
  "message": "Type 'int' is not assignable to type 'str'",
  "symbol": "render_overview_report",
  "symbol_confidence": "exact",
  "callers": ["main", "run_impact"]
}
```

### 6.2 影响文件排序（AI 输出 87% → 90%）

**现状**：`affected_files` 按 `(confidence, file)` 排序。

**改法**：

文件：`repomap_cli/cli.py`（+15 行）

```python
def _affected_severity(file_path: str, engine: RepoMapEngine) -> int:
    """计算受影响文件的严重程度（受影响符号的外部调用者总数）"""
    total = 0
    for sid in engine.graph.file_symbols.get(file_path, []):
        for edge in engine.graph.incoming.get(sid, []):
            if edge.kind == "call":
                src_sym = engine.graph.symbols.get(edge.source)
                if src_sym and src_sym.file != file_path:  # 只计外部调用者
                    total += 1
    return total

# 在 run_impact() 中：
affected_list.sort(key=lambda x: (-_affected_severity(x[0], engine), x[2], x[0]))
```

### 6.3 导入解析边缘修复（导入解析 90% → 93%）

#### 6.3.1 处理 package.json `imports` 字段

文件：`repomap_resolver.py`（+20 行）

Node.js 的 `#dep` 私有导入（`package.json` 的 `imports` 字段）目前未处理。

```python
def _load_package_imports(self, data: dict) -> None:
    """解析 package.json imports 字段（# 私有导入）"""
    imports = data.get("imports", {})
    if isinstance(imports, dict):
        for pattern, target in imports.items():
            if isinstance(target, str):
                self._package_imports[pattern] = target
            elif isinstance(target, dict):
                # 条件导入，优先 browser > import > default
                for key in ("browser", "import", "default", "require"):
                    if key in target and isinstance(target[key], str):
                        self._package_imports[pattern] = target[key]
                        break
```

#### 6.3.2 处理 Vite `~` alias

文件：`repomap_resolver.py`（+15 行）

```python
def _detect_vite_alias(self) -> None:
    """检测 Vite 默认 alias: ~/ → src/"""
    if (self.project_root / "vite.config.ts").exists() or \
       (self.project_root / "vite.config.js").exists():
        self.bundler_aliases.aliases.setdefault("~", "src")
```

在 `_load_import_configs` 末尾调用。

#### 6.3.3 Python namespace package

文件：`repomap_resolver.py`（+15 行）

Python 3.3+ 隐式 namespace package（无 `__init__.py` 的目录）。

在 `_candidate_files_for_base_path` 中，如果没有 `__init__.py`，也检查目录下是否有 `.py` 文件：

```python
# 现有: init_file = str(resolved / "__init__.py")
# 新增: namespace package 检测
if init_file not in self.graph.file_symbols:
    ns_candidates = [
        f for f in self.graph.file_symbols
        if f.startswith(resolved_str + "/") and f.endswith(".py")
    ]
    if ns_candidates:
        matches.extend(ns_candidates[:5])
```

---

## 七、Phase 5 详细设计（新语言 ×4：C++ / C# / PHP / Ruby + 最终打磨）

### 7.1 新语言 QUERIES

#### 7.1.1 C++ 语言（~70 行）

```python
"cpp": {
    "function": """
        (function_definition
          declarator: (function_declarator
            declarator: [(identifier) (qualified_identifier)] @name)) @definition.function
        (template_declaration
          (function_definition
            declarator: (function_declarator
              declarator: [(identifier) (qualified_identifier)] @name))) @definition.function
    """,
    "class": """
        (class_specifier name: (type_identifier) @name) @definition.class
        (struct_specifier name: (type_identifier) @name) @definition.struct
        (enum_specifier name: (type_identifier) @name) @definition.enum
    """,
    "import": """
        (preproc_include path: [(string_literal) (system_lib_string)] @path)
    """,
    "call": """
        (call_expression function: [(identifier) (qualified_identifier)] @name) @reference.call
    """,
}
```

#### 7.1.2 C# 语言（~60 行）

```python
"c_sharp": {
    "function": """
        (method_declaration name: (identifier) @name) @definition.method
        (local_function_statement name: (identifier) @name) @definition.function
    """,
    "class": """
        (class_declaration name: (identifier) @name) @definition.class
        (interface_declaration name: (identifier) @name) @definition.interface
        (struct_declaration name: (identifier) @name) @definition.struct
        (enum_declaration name: (identifier) @name) @definition.enum
    """,
    "import": """
        (using_directive name: [(identifier) (qualified_name)] @name)
    """,
    "call": """
        (invocation_expression function: [(identifier) (member_access_expression)] @name) @reference.call
    """,
}
```

#### 7.1.3 PHP 语言（~55 行）

```python
"php": {
    "function": """
        (function_definition name: (name) @name) @definition.function
        (method_declaration name: (name) @name) @definition.method
    """,
    "class": """
        (class_declaration name: (name) @name) @definition.class
        (interface_declaration name: (name) @name) @definition.interface
        (trait_declaration name: (name) @name) @definition.trait
        (enum_declaration name: (name) @name) @definition.enum
    """,
    "import": """
        (namespace_use_declaration (qualified_name) @name)
    """,
    "call": """
        (function_call_expression function: (name) @name) @reference.call
        (member_call_expression name: (name) @name) @reference.call
    """,
}
```

#### 7.1.4 Ruby 语言（~55 行）

```python
"ruby": {
    "function": """
        (method name: (identifier) @name) @definition.method
        (singleton_method name: (identifier) @name) @definition.method
    """,
    "class": """
        (class name: (constant) @name) @definition.class
        (module name: (constant) @name) @definition.module
    """,
    "import": """
        (call method: (identifier) @_method arguments: (argument_list (string) @path))
        (#match? @_method "^(require|require_relative|load)$")
    """,
    "call": """
        (call method: (identifier) @name) @reference.call
    """,
}
```

### 7.2 扩展 EXT_TO_LANG（Phase 5）

文件：`repomap_parser.py`（+12 行）

```python
".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
".cs": "c_sharp",
".php": "php", ".phtml": "php",
".rb": "ruby",
```

### 7.3 最终打磨

#### 7.3.1 统一 exit code

文件：`repomap_cli/cli.py`（+25 行）

```python
# 约定
EXIT_SUCCESS = 0       # 有有效输出
EXIT_ERROR = 1         # 命令执行失败
EXIT_INVALID_ARGS = 2  # 参数错误
EXIT_NO_RESULTS = 3    # query 无结果 / routes 为空（非错误）
```

审查所有 `return` 语句，统一使用上述常量。

#### 7.3.2 置信度透明度

文件：`repomap_resolver.py` + `repomap_ai.py`（+25 行）

当 import 解析走了 module stem 匹配的兜底路径时，在结果中标注 `[low-confidence]`：

```python
# 在 resolve_import_targets 的兜底返回中
result = matches[:3]
result._low_confidence = True  # 标记

# 在报告渲染中
if getattr(import_path, '_low_confidence', False):
    label = f"{import_path} [low-confidence]"
```

---

## 八、完整代码量估算

```
Phase 1（增量扫描 + 测试盲区 + AI 升级）
  repomap_support.py    +50   (FileCacheEntry, IncrementalCache)
  repomap_core.py       +80   (增量 scan 逻辑)
  repomap_toolkit.py    +40   (增量缓存读写)
  repomap_topic.py      +75   (find_untested_symbols + fuzzy suggest)
  repomap_ai.py         +70   (活跃文件 + Quick Actions + 模糊匹配渲染)
  repomap_cli/cli.py    +40   (--no-incremental + schema_version + verify 接入)
  tests/                +80   (new tests)
  ──────────────────────────
  Phase 1 合计:         ~435 行

Phase 2（符号 diff + 断点检测 + 快捷面板）
  repomap_support.py    +40   (caller analysis in compare, assess_breaking_risk)
  repomap_ai.py         +80   (Breaking Changes 板块 + Related Commands)
  repomap_cli/cli.py    +30   (verify 接入 diff)
  tests/                +50   (new tests)
  ──────────────────────────
  Phase 2 合计:         ~200 行

Phase 3（新语言 ×4：C / Java / Kotlin / Swift）
  repomap_parser.py     +320  (4种语言 QUERIES + _init_parsers 扩展 + EXT_TO_LANG)
  repomap_resolver.py   +35   (Java/Swift import 解析)
  pyproject.toml        +5    (optional deps)
  tests/                +80   (每种语言 2 个测试)
  ──────────────────────────
  Phase 3 合计:         ~440 行

Phase 4（上下文诊断 + 影响排序 + 边缘修复）
  repomap_check.py      +35   (caller 上下文)
  repomap_cli/cli.py    +15   (影响排序)
  repomap_resolver.py   +50   (imports 字段 + Vite ~ + namespace pkg)
  repomap_ai.py         +15   (置信度标注)
  tests/                +40   (new tests)
  ──────────────────────────
  Phase 4 合计:         ~155 行

Phase 5（新语言 ×4：C++ / C# / PHP / Ruby + 最终打磨）
  repomap_parser.py     +300  (4种语言 QUERIES + _init_parsers + EXT_TO_LANG)
  repomap_cli/cli.py    +25   (exit code 统一)
  repomap_ai.py         +20   (置信度渲染)
  pyproject.toml        +5    (optional deps)
  tests/                +80   (每种语言 2 个测试)
  ──────────────────────────
  Phase 5 合计:         ~430 行

═══════════════════════════════════
总计:                   ~1,660 行
含测试总计:             ~2,000 行
```

---

## 九、实施顺序与依赖图

```text
Phase 1 ──────→ Phase 2 ──────→ Phase 4
   │                │                │
   │ 无外部依赖      │ 依赖 P1 增量    │ 依赖 P1/P2
   │ 可立即启动      │ 缓存就绪        │ 图结构
   │                │                │
   └── Phase 3 ──────────────────── Phase 5
        │                                │
        独立（纯 parser 扩展）           独立 + 依赖 P3 的
        可与 P1/P2 并行                  EXT_TO_LANG 模式
```

**推荐执行顺序**：

1. **Phase 1 先行**（增量扫描是其他 Phase 的基础）
2. **Phase 2 + Phase 3 并行**（互不依赖，可由两人同时开发）
3. **Phase 4**（依赖 P1/P2 但改动小，快速完成）
4. **Phase 5**（收尾 + 打磨）

---

## 十、验证策略

### 每个 Phase 的验证命令

```bash
# Phase 1 完成
uv run python -m pytest tests/ -v -k "incremental or untested or fuzzy or hot_files"
repomap overview --project . 2>/dev/null | grep -A 5 "Quick Actions"
# 修改一个文件后:
repomap verify --project . --quick   # 确认增量生效，速度 < 1s

# Phase 2 完成
uv run python -m pytest tests/ -v -k "breaking or snapshot or risk"
repomap cache save --project .
# 修改一个导出函数签名后:
repomap verify --project . --with-diff  # 确认 Breaking Changes 板块出现

# Phase 3 完成
uv run python -m pytest tests/ -v -k "c_parser or java_parser or kotlin_parser or swift_parser"
echo 'int main() { return 0; }' > /tmp/test.c
repomap overview --project /tmp 2>/dev/null  # 确认 C 文件被解析

# Phase 4 完成
uv run python -m pytest tests/ -v -k "context_diag or severity or import_edge"
repomap check --project . 2>/dev/null | head -20  # 确认诊断含符号上下文

# Phase 5 完成
uv run python -m pytest tests/ -v -k "cpp_parser or csharp_parser or php_parser or ruby_parser"
uv run python -m pytest tests/ -v  # 全量回归 118 + 新增 = ~200 tests
repomap doctor && repomap overview --project .  # 最终 smoke
```

---

## 十一、最终能力矩阵（完成后）

```
维度              完成度    说明
─────────────────────────────────────────────────────────
解析覆盖          93%      15 种语言 (原7 + C/C++/Java/Kotlin/Swift/C#/PHP/Ruby)
验证闭环          92%      符号级 diff + 断点检测 + 测试盲区 + 上下文诊断
AI 输出质量       90%      活跃文件 + Quick Actions + 模糊匹配 + 置信度标注
性能              90%      增量扫描（Δ文件解析，15× 提速）
图分析            85%      轻量数据流（return/passes_to 边）
导入解析          93%      imports 字段 + Vite ~ + namespace pkg + 缓存
```

---

## 十二、不做 & 为什么（再次确认）

| 砍掉 | 理由 |
|------|------|
| BM25 | 手写加权覆盖 99% 场景，替换无感知提升 |
| 隐式耦合默认开启 | 新项目/大重构噪音 > 信号 |
| 完整 SSA 数据流 | 等于嵌入微型编译器，ROI 极低 |
| Web UI / MCP server | repomap 定位是 CLI，简单分发是核心优势 |
| Kotlin/Swift 之外的小众语言（Dart/Scala/Elixir 等） | 边际收益递减，15 种语言已覆盖 >95% AI 编码场景 |
| auto-fix 建议 | 需要 LLM 集成，应作为上层应用而非 repomap 内核 |
