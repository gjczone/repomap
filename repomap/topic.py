"""
主题评分引擎 + 测试匹配 + 文件角色分类。

被 query、impact、diff-risk、overview 共用。
零外部依赖（只依赖 repomap_support 的数据结构）。
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repomap.support import RepoGraph


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FileMatch:
    path: str
    role: str
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class TestMatch:
    test_file: str
    target_file: str
    confidence: str  # high | medium | low
    reason: str


# ═══════════════════════════════════════════════════════════════════════════════
# 噪音文件判断
# ═══════════════════════════════════════════════════════════════════════════════

NOISE_PATTERNS = [
    "public/monaco-editor/",
    ".min.js",
    ".bundle.js",
    ".generated.",
    ".d.ts",
]

# 复用 core 中的 SKIP_DIR_NAMES 作为额外参考
NOISE_PATH_SEGMENTS = {
    "monaco-editor",
    "vendor",
    "third_party",
    "third-party",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".cache",
}


def is_noise_file(file_path: str) -> bool:
    """判断是否为噪音文件（构建产物、vendor 等）。"""
    path_lower = file_path.lower()
    for pattern in NOISE_PATTERNS:
        if pattern in path_lower:
            return True
    parts = PurePosixPath(file_path).parts
    for part in parts:
        if part.lower() in NOISE_PATH_SEGMENTS:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 文件角色分类
# ═══════════════════════════════════════════════════════════════════════════════


def classify_file_role(file_path: str) -> str:
    """基于路径的粗略角色分类。"""
    path = file_path.lower()
    if is_test_like_file(file_path):
        return "test"
    if any(p in path for p in ["/components/", "/pages/", "/views/"]):
        return "frontend-ui"
    if any(p in path for p in ["/stores/", "/hooks/"]):
        return "frontend-state"
    if any(p in path for p in ["/server/", "/routes/", "/api/"]
           ) or path.startswith("server/"):
        return "backend"
    if any(path.endswith(ext) for ext in [".config.ts", ".config.js", ".config.tsx", "package.json"]):
        return "config"
    return "other"


# ═══════════════════════════════════════════════════════════════════════════════
# 标识符拆分
# ═══════════════════════════════════════════════════════════════════════════════

_CAMEL_SPLIT_RE = re.compile(r'([A-Z][a-z0-9]+|[a-z0-9]+|[A-Z0-9]+(?=[A-Z]|$))')


def split_identifier(name: str) -> list[str]:
    """将 camelCase/PascalCase/snake_case 标识符拆分为词元列表。

    "VirtualKeyboard" -> ["virtual", "keyboard"]
    "queueInput"     -> ["queue", "input"]
    "terminal_store" -> ["terminal", "store"]
    """
    name = name.replace("_", " ").replace("-", " ")
    tokens: list[str] = []
    for part in name.split():
        part_tokens = [t.lower() for t in _CAMEL_SPLIT_RE.findall(part) if t]
        tokens.extend(part_tokens)
    return tokens


# ═══════════════════════════════════════════════════════════════════════════════
# 主题评分
# ═══════════════════════════════════════════════════════════════════════════════


def topic_score(
    query: str,
    file_path: str,
    file_data: dict,
    graph: "RepoGraph",
    keyword_weights: dict[str, float] | None = None,
) -> float:
    """对文件与查询关键词的相关性进行评分。

    第一版用手写加权快速上线，后续可升级为 BM25。
    keyword_weights 用于高频词惩罚：命中文件比例越高，权重越低。
    """
    keywords = query.lower().split()
    score = 0.0

    path_lower = file_path.lower()
    file_name = PurePosixPath(file_path).stem.lower()
    file_name_tokens = split_identifier(PurePosixPath(file_path).stem)

    for kw in keywords:
        kw_weight = keyword_weights.get(kw, 1.0) if keyword_weights else 1.0

        # 1. 路径命中（权重 30，文件名命中翻倍）
        if kw in path_lower:
            score += 30 * (2.0 if kw in file_name else 1.0) * kw_weight

        # 2. 文件名命中（权重 25），含 camelCase/snake_case 拆词
        if kw in file_name:
            score += 25 * kw_weight
        elif any(kw in t for t in file_name_tokens):
            score += 15 * kw_weight

        # 3. 符号名命中（权重 15）
        for sid in graph.file_symbols.get(file_path, []):
            symbol = graph.symbols.get(sid)
            if symbol and kw in symbol.name.lower():
                score += 15 * kw_weight
                break  # 每个关键词每个文件只计一次

    # 4. 噪音惩罚
    if is_noise_file(file_path):
        score *= 0.05

    # 5. 测试文件降权（默认优先看实现，再看测试）
    if is_test_like_file(file_path):
        score *= 0.55

    return score


def compute_keyword_weights(
    keywords: list[str],
    candidate_files: list[str],
    graph: "RepoGraph",
) -> dict[str, float]:
    """计算每个关键词的 IDF 风格权重。

    命中文件比例 > 80% → 权重 0.2
    命中文件比例 > 50% → 权重 0.5
    否则保持 1.0。
    """
    total = len(candidate_files)
    if total == 0:
        return {kw: 1.0 for kw in keywords}

    weights: dict[str, float] = {}
    for kw in keywords:
        matched = 0
        kw_lower = kw.lower()
        for f in candidate_files:
            path_lower = f.lower()
            file_name = PurePosixPath(f).stem.lower()
            tokens = split_identifier(PurePosixPath(f).stem)
            if kw_lower in path_lower or kw_lower in file_name or any(kw_lower in t for t in tokens):
                matched += 1
                continue
            # 也检查符号名
            for sid in graph.file_symbols.get(f, []):
                sym = graph.symbols.get(sid)
                if sym and kw_lower in sym.name.lower():
                    matched += 1
                    break

        ratio = matched / total
        if ratio > 0.8:
            weights[kw] = 0.2
        elif ratio > 0.5:
            weights[kw] = 0.5
        else:
            weights[kw] = 1.0

    return weights


# ═══════════════════════════════════════════════════════════════════════════════
# 测试文件判断
# ═══════════════════════════════════════════════════════════════════════════════


def is_test_like_file(file_path: str) -> bool:
    """判断是否为测试文件。"""
    path = PurePosixPath(file_path)
    name = path.name.lower()
    if any(part.lower() in {"test", "tests", "__tests__"} for part in path.parts):
        return True
    return name.startswith("test_") or name.endswith("_test.py") or name.endswith(".spec.ts") or name.endswith(".test.ts") or name.endswith(".test.tsx") or name.endswith(".spec.tsx")


def _is_boilerplate_test(file_path: str) -> bool:
    """排除低语义测试文件（package marker、pytest fixture 等）。"""
    name = PurePosixPath(file_path).name
    return name in ("__init__.py", "conftest.py", "__init__.pyi")


def _bare_name(stem: str) -> str:
    """去除测试相关后缀，返回基础文件名。

    "VirtualKeyboard.test" -> "VirtualKeyboard"
    "terminal_test"        -> "terminal"
    """
    for suffix in (".test", ".spec", "_test", "Test"):
        if stem.endswith(suffix) and len(stem) > len(suffix):
            return stem[: -len(suffix)]
    if stem.startswith("test_") and len(stem) > 5:
        return stem[5:]
    return stem


# ═══════════════════════════════════════════════════════════════════════════════
# 测试匹配
# ═══════════════════════════════════════════════════════════════════════════════


def find_related_tests(
    target_files: list[str],
    graph: "RepoGraph",
    analysis: dict,
    project_root: str,
) -> list[TestMatch]:
    """根据目标文件查找相关测试（5 级优先级匹配）。"""
    results: list[TestMatch] = []
    test_files = [
        f for f in graph.file_symbols
        if is_test_like_file(f) and not _is_boilerplate_test(f)
    ]
    if not test_files:
        return _dedupe_test_matches(results)

    for target in target_files:
        target_name = PurePosixPath(target).stem
        target_bare = _bare_name(target_name)
        target_symbol_ids = set(graph.file_symbols.get(target, []))

        for test_file in test_files:
            test_bare = _bare_name(PurePosixPath(test_file).stem)

            # 策略1: 文件名强匹配（high confidence）
            if test_bare == target_bare:
                results.append(TestMatch(
                    test_file, target, "high",
                    "文件名精确匹配",
                ))
                continue

            # 策略2: 路径邻近匹配（medium confidence）
            if _share_test_dir(test_file, target):
                results.append(TestMatch(
                    test_file, target, "medium",
                    "同测试目录",
                ))
                continue

            # 策略3: import 路径命中（high confidence）
            if _test_imports_target(test_file, target, graph):
                results.append(TestMatch(
                    test_file, target, "high",
                    "测试 import 了目标模块",
                ))
                continue

            # 策略4: 符号边命中（medium confidence）
            test_symbols = graph.file_symbols.get(test_file, [])
            for sid in test_symbols:
                found = False
                for edge in graph.outgoing.get(sid, []):
                    if edge.target in target_symbol_ids:
                        target_sym = graph.symbols.get(edge.target)
                        sym_name = target_sym.name if target_sym else "?"
                        results.append(TestMatch(
                            test_file, target, "medium",
                            f"测试引用了 {sym_name}",
                        ))
                        found = True
                        break
                if found:
                    break
            else:
                # 策略5: git 共变更历史（medium confidence）
                co_score = _get_co_change_score(project_root, test_file, target)
                if co_score >= 3:
                    results.append(TestMatch(
                        test_file, target, "medium",
                        f"git 共变更 {co_score} 次",
                    ))

    return _dedupe_test_matches(results)


def _dedupe_test_matches(matches: list[TestMatch]) -> list[TestMatch]:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    reason_rank = {
        "文件名精确匹配": 5,
        "测试 import 了目标模块": 4,
        "测试引用了": 3,
        "同测试目录": 2,
        "git 共变更": 1,
    }

    def score(match: TestMatch) -> tuple[int, int]:
        reason_score = 0
        for prefix, value in reason_rank.items():
            if match.reason.startswith(prefix):
                reason_score = value
                break
        return confidence_rank.get(match.confidence, 0), reason_score

    best: dict[tuple[str, str], TestMatch] = {}
    order: list[tuple[str, str]] = []
    for match in matches:
        key = (match.test_file, match.target_file)
        if key not in best:
            best[key] = match
            order.append(key)
            continue
        if score(match) > score(best[key]):
            best[key] = match
    return [best[key] for key in order]

def _share_test_dir(test_file: str, target: str) -> bool:
    """检查测试文件和目标文件是否在同一目录下（含 __tests__ 相邻目录）。"""
    test_path = PurePosixPath(test_file)
    target_path = PurePosixPath(target)
    test_parent = test_path.parent
    target_parent = target_path.parent
    # 同为根目录文件不视为"同目录"
    if test_parent == target_parent:
        name = test_parent.name if test_parent.name else str(test_parent)
        if name in ("", "."):
            return False
        return True
    # __tests__/foo.test.ts 对应 ../foo.ts
    if test_parent.name in ("__tests__", "tests", "test"):
        grandparent = test_parent.parent
        if grandparent == target_parent:
            gp_name = grandparent.name if grandparent.name else str(grandparent)
            if gp_name in ("", "."):
                return False
            return True
    return False


def _test_imports_target(test_file: str, target: str, graph: "RepoGraph") -> bool:
    """检查测试文件是否 import 了目标模块路径。"""
    target_module = _file_to_module_path(target)
    imports = graph.file_imports.get(test_file, [])
    for imp in imports:
        if target_module in imp or imp.endswith(target_module):
            return True
    return False


def _file_to_module_path(file_path: str) -> str:
    """将文件路径转为模块路径。

    "src/components/terminal/VirtualKeyboard.tsx" -> "src/components/terminal/VirtualKeyboard"
    """
    p = PurePosixPath(file_path)
    # 去除扩展名
    stem_path = str(p.parent / p.stem) if p.suffix else str(p)
    # 去除 index
    if p.stem == "index":
        stem_path = str(p.parent)
    return stem_path


# ═══════════════════════════════════════════════════════════════════════════════
# Git 共变更热度
# ═══════════════════════════════════════════════════════════════════════════════

_co_change_cache: dict[str, dict[tuple[str, str], int]] = {}


def get_co_change_score(project_root: str, file_a: str, file_b: str) -> int:
    """查询两个文件的 git 共变更次数（带缓存，公开接口）。"""
    cache = _co_change_cache.get(project_root)
    if cache is None:
        cache = _load_co_change_scores(project_root)
        _co_change_cache[project_root] = cache
    a, b = sorted([file_a, file_b])
    return cache.get((a, b), 0)


# 向后兼容别名
_get_co_change_score = get_co_change_score


def get_co_change_neighbors(
    project_root: str, file_path: str, top_n: int = 5,
) -> list[tuple[str, int]]:
    """返回与指定文件共变频率最高的文件列表（降序）。

    用途：识别隐式耦合——两个文件在 git 历史中频繁一起修改，
    即使代码上没有显式依赖，也可能存在隐含关联。
    """
    cache = _co_change_cache.get(project_root)
    if cache is None:
        cache = _load_co_change_scores(project_root)
        _co_change_cache[project_root] = cache
    neighbors: dict[str, int] = {}
    for (a, b), count in cache.items():
        if a == file_path:
            neighbors[b] = count
        elif b == file_path:
            neighbors[a] = count
    return sorted(neighbors.items(), key=lambda x: -x[1])[:top_n]


def _load_co_change_scores(project_root: str) -> dict[tuple[str, str], int]:
    """统计项目中文件对的 git 共变更次数。"""
    scores: dict[tuple[str, str], int] = defaultdict(int)
    try:
        result = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:", "--since=90.days.ago", "--", "."],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return dict(scores)

    current_commit_files: list[str] = []
    for line in result.stdout.split("\n"):
        stripped = line.strip()
        if not stripped:
            # 空行分隔 commit
            if len(current_commit_files) > 1:
                for i in range(len(current_commit_files)):
                    for j in range(i + 1, len(current_commit_files)):
                        a, b = sorted([current_commit_files[i], current_commit_files[j]])
                        scores[(a, b)] += 1
            current_commit_files = []
        else:
            current_commit_files.append(stripped)

    # 处理最后一个 commit
    if len(current_commit_files) > 1:
        for i in range(len(current_commit_files)):
            for j in range(i + 1, len(current_commit_files)):
                a, b = sorted([current_commit_files[i], current_commit_files[j]])
                scores[(a, b)] += 1

    return dict(scores)


# ═══════════════════════════════════════════════════════════════════════════════
# 测试盲区检测
# ═══════════════════════════════════════════════════════════════════════════════

LOW_SIGNAL_KINDS = {"element", "selector", "class_selector", "id_selector", "json_key"}


def _signal_weight_for_symbol(sym: Any) -> float:
    """独立版符号信号权重，不依赖 GraphAnalyzer 实例。"""
    kind = getattr(sym, "kind", "") if hasattr(sym, "kind") else sym.get("kind", "")
    name = getattr(sym, "name", "") if hasattr(sym, "name") else sym.get("name", "")
    visibility = getattr(sym, "visibility", "") if hasattr(sym, "visibility") else sym.get("visibility", "")
    if kind in LOW_SIGNAL_KINDS:
        return 0.002
    if name in {"__init__", "__main__"}:
        return 0.35
    if name.startswith("_") and visibility == "private":
        return 0.85
    return 1.0


def find_untested_symbols(
    graph: "RepoGraph",
    min_incoming_calls: int = 2,
    min_score: float = 5.0,
    max_results: int = 30,
) -> list[dict]:
    """找出没有测试覆盖的符号，按风险分降序排列。

    风险分 = incoming_calls × signal_weight × 5.0
    只返回非测试文件中被调用过但无测试关联的符号。
    """
    # 收集所有测试文件中的符号 ID
    test_symbol_ids: set[str] = set()
    for f in graph.file_symbols:
        if is_test_like_file(f):
            test_symbol_ids.update(graph.file_symbols[f])

    if not test_symbol_ids:
        return []

    # BFS 一层：被测试符号直接引用的非测试符号视为"已覆盖"
    covered: set[str] = set()
    for tsid in test_symbol_ids:
        for edge in graph.outgoing.get(tsid, []):
            if edge.target not in test_symbol_ids:
                covered.add(edge.target)

    untested = []
    for sid, sym in graph.symbols.items():
        if sid in test_symbol_ids or sid in covered:
            continue
        kind = getattr(sym, "kind", "")
        if kind in LOW_SIGNAL_KINDS:
            continue
        incoming = sum(1 for e in graph.incoming.get(sid, []) if e.kind == "call")
        if incoming < min_incoming_calls:
            continue
        sw = _signal_weight_for_symbol(sym)
        score = incoming * sw * 5.0
        if score < min_score:
            continue
        untested.append({
            "symbol": getattr(sym, "name", str(sid)),
            "kind": getattr(sym, "kind", ""),
            "file": getattr(sym, "file", ""),
            "line": getattr(sym, "line", 0),
            "incoming_calls": incoming,
            "risk_score": round(score, 1),
        })

    untested.sort(key=lambda x: -x["risk_score"])
    return untested[:max_results]


def fuzzy_symbol_suggest(query: str, graph: "RepoGraph", limit: int = 5) -> list[str]:
    """用编辑距离找最接近的符号名，用于 query 无结果时的友好建议。"""
    import difflib
    all_names = sorted({s.name for s in graph.symbols.values() if len(s.name) >= 3})
    return difflib.get_close_matches(query, all_names, n=limit, cutoff=0.5)
