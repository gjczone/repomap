"""
主题评分引擎 + 测试匹配 + 文件角色分类。

被 query、impact、diff-risk、overview 共用。
零外部依赖（只依赖 repomap_support 的数据结构）。
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("repomap")

if TYPE_CHECKING:
    from . import RepoGraph

from . import LOW_SIGNAL_KINDS, signal_weight_for_symbol


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

# Reuse SKIP_DIR_NAMES from core as additional reference
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

# ═══════════════════════════════════════════════════════════════════════════════
# Synonym table for query expansion
# ═══════════════════════════════════════════════════════════════════════════════

SYNONYM_TABLE: dict[str, set[str]] = {
    "task": {"job", "process", "run", "execution"},
    "cancel": {"stop", "abort", "interrupt"},
    "progress": {"status", "poll"},
    "reload": {"refresh", "revalidate"},
    "import": {"ingest", "load", "etl"},
    "history": {"log", "audit"},
    "data": {"row", "table"},
    "failure": {"error", "exception", "failed"},
    "success": {"ok", "completed", "done"},
    "auth": {"login", "session", "token", "credential"},
    "config": {"setting", "option", "preference", "env"},
    "delete": {"remove", "destroy", "drop", "purge"},
    "create": {"add", "new", "insert", "build"},
    "update": {"edit", "modify", "change", "patch"},
    "api": {"endpoint", "route", "handler", "controller"},
    "ui": {"frontend", "component", "page", "view", "render"},
    "db": {"database", "sql", "migration", "schema"},
    "file": {"io", "read", "write", "stream", "upload", "download"},
    "queue": {"message", "event", "dispatch", "worker"},
    "cache": {"redis", "memcache", "store", "invalidate"},
}

# Build reverse lookup: synonym -> originating keyword
_SYNONYM_REVERSE: dict[str, str] = {}
for _kw, _syns in SYNONYM_TABLE.items():
    for _syn in _syns:
        _SYNONYM_REVERSE[_syn] = _kw


def expand_keywords(keywords: list[str]) -> list[tuple[str, str | None]]:
    """Expand query keywords with synonyms.

    Returns list of (keyword, source) tuples.
    source is None for original keywords, or the originating keyword for synonyms.
    """
    expanded: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for kw in keywords:
        if kw not in seen:
            expanded.append((kw, None))
            seen.add(kw)
        for syn in SYNONYM_TABLE.get(kw, set()):
            if syn not in seen:
                expanded.append((syn, kw))
                seen.add(syn)
    # Also check reverse: if keyword is a synonym, expand to original
    for kw in keywords:
        source = _SYNONYM_REVERSE.get(kw)
        if source:
            if source not in seen:
                expanded.append((source, kw))
                seen.add(source)
            for syn in SYNONYM_TABLE.get(source, set()):
                if syn not in seen and syn != kw:
                    expanded.append((syn, source))
                    seen.add(syn)
    return expanded


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


def classify_file_role(file_path: str, graph: "RepoGraph | None" = None) -> str:
    """基于路径和符号信息的角色分类。"""
    path = file_path.lower()
    if is_test_like_file(file_path):
        return "test"
    if any(p in path for p in ["/components/", "/pages/", "/views/"]):
        return "frontend-ui"
    if any(p in path for p in ["/stores/", "/hooks/"]):
        return "frontend-state"
    if any(p in path for p in ["/server/", "/routes/", "/api/"]) or path.startswith(
        "server/"
    ):
        return "backend"
    if any(
        path.endswith(ext)
        for ext in [".config.ts", ".config.js", ".config.tsx", "package.json"]
    ):
        return "config"

    # Use symbol information for more precise classification
    if graph is not None:
        symbol_ids = graph.file_symbols.get(file_path, [])
        if symbol_ids:
            kind_counts: dict[str, int] = {}
            for sid in symbol_ids:
                symbol = graph.symbols.get(sid)
                if symbol:
                    kind_counts[symbol.kind] = kind_counts.get(symbol.kind, 0) + 1

            # Many exported symbols → core module
            exported_count = sum(
                1
                for sid in symbol_ids
                if graph.symbols.get(sid)
                and graph.symbols[sid].visibility == "exported"
            )
            if exported_count >= 3:
                return "core"

            # Many classes/interfaces → model/type definitions
            if kind_counts.get("class", 0) >= 2 or kind_counts.get("interface", 0) >= 2:
                return "model"

            # Many functions → utility/service
            if kind_counts.get("function", 0) >= 3 or kind_counts.get("method", 0) >= 3:
                return "service"

            # Dense file with many symbols → core
            if len(symbol_ids) >= 10:
                return "core"

    return "other"


# ═══════════════════════════════════════════════════════════════════════════════
# 标识符拆分
# ═══════════════════════════════════════════════════════════════════════════════

_CAMEL_SPLIT_RE = re.compile(r"([A-Z][a-z0-9]+|[a-z0-9]+|[A-Z0-9]+(?=[A-Z]|$))")


def split_identifier(name: str) -> list[str]:
    """Split camelCase/PascalCase/snake_case/kebab-case identifiers into tokens.

    "VirtualKeyboard" -> ["virtual", "keyboard"]
    "queueInput"     -> ["queue", "input"]
    "terminal_store" -> ["terminal", "store"]
    "foo::bar"       -> ["foo", "bar"]  (Rust module paths)
    """
    name = name.replace("_", " ").replace("-", " ").replace("::", " ")
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
    """Score a file against query keywords, with synonym expansion.

    Original keywords get full weight; synonyms get reduced weight.
    keyword_weights provides IDF-style penalty for high-frequency keywords.
    """
    keywords = query.lower().split()
    expanded = expand_keywords(keywords)
    score = 0.0

    path_lower = file_path.lower()
    file_name = PurePosixPath(file_path).stem.lower()
    file_name_tokens = split_identifier(PurePosixPath(file_path).stem)

    for kw, source in expanded:
        is_synonym = source is not None
        kw_weight = keyword_weights.get(kw, 1.0) if keyword_weights else 1.0

        # Path hit: 30 for original, 15 for synonym
        if kw in path_lower:
            score += (
                (15 if is_synonym else 30)
                * (2.0 if kw in file_name else 1.0)
                * kw_weight
            )

        # Filename hit: 25/12, including tokenized match 15/8
        if kw in file_name:
            score += (12 if is_synonym else 25) * kw_weight
        elif any(kw in t for t in file_name_tokens):
            score += (8 if is_synonym else 15) * kw_weight

        # Symbol name hit: 15/8 per keyword per file
        for sid in graph.file_symbols.get(file_path, []):
            symbol = graph.symbols.get(sid)
            if symbol and kw in symbol.name.lower():
                score += (8 if is_synonym else 15) * kw_weight
                break

    # Noise penalty
    if is_noise_file(file_path):
        score *= 0.05

    # Test file deprioritization
    if is_test_like_file(file_path):
        score *= 0.55

    return score


def compute_keyword_weights(
    keywords: list[str],
    candidate_files: list[str],
    graph: "RepoGraph",
) -> dict[str, float]:
    """Compute IDF-style weights for keywords (including synonyms).

    Match ratio > 80% → weight 0.2
    Match ratio > 50% → weight 0.5
    Otherwise weight 1.0.
    """
    expanded = expand_keywords(keywords)
    total = len(candidate_files)
    if total == 0:
        return {kw: 1.0 for kw, _ in expanded}

    weights: dict[str, float] = {}
    for kw, _source in expanded:
        matched = 0
        kw_lower = kw.lower()
        for f in candidate_files:
            path_lower = f.lower()
            file_name = PurePosixPath(f).stem.lower()
            tokens = split_identifier(PurePosixPath(f).stem)
            if (
                kw_lower in path_lower
                or kw_lower in file_name
                or any(kw_lower in t for t in tokens)
            ):
                matched += 1
                continue
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
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".spec.ts")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.tsx")
    )


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
        f
        for f in graph.file_symbols
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
                results.append(
                    TestMatch(
                        test_file,
                        target,
                        "high",
                        "exact filename match",
                    )
                )
                continue

            # 策略2: 路径邻近匹配（medium confidence）
            if _share_test_dir(test_file, target):
                results.append(
                    TestMatch(
                        test_file,
                        target,
                        "medium",
                        "same test directory",
                    )
                )
                continue

            # 策略3: import 路径命中（high confidence）
            if _test_imports_target(test_file, target, graph):
                results.append(
                    TestMatch(
                        test_file,
                        target,
                        "high",
                        "test imports target module",
                    )
                )
                continue

            # 策略4: 符号边命中（medium confidence）
            test_symbols = graph.file_symbols.get(test_file, [])
            for sid in test_symbols:
                found = False
                for edge in graph.outgoing.get(sid, []):
                    if edge.target in target_symbol_ids:
                        target_sym = graph.symbols.get(edge.target)
                        sym_name = target_sym.name if target_sym else "?"
                        results.append(
                            TestMatch(
                                test_file,
                                target,
                                "medium",
                                f"test references {sym_name}",
                            )
                        )
                        found = True
                        break
                if found:
                    break
            else:
                # 策略5: git 共变更历史（medium confidence）
                co_score = get_co_change_score(project_root, test_file, target)
                if co_score >= 3:
                    results.append(
                        TestMatch(
                            test_file,
                            target,
                            "medium",
                            f"git co-changed {co_score} times",
                        )
                    )

    return _dedupe_test_matches(results)


def _dedupe_test_matches(matches: list[TestMatch]) -> list[TestMatch]:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    reason_rank = {
        "exact filename match": 5,
        "test imports target module": 4,
        "test references": 3,
        "same test directory": 2,
        "git co-changed": 1,
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

_co_change_cache: OrderedDict[tuple[str, int], dict[tuple[str, str], int]] = (
    OrderedDict()
)
_MAX_CO_CHANGE_CACHE = 32  # 最多缓存 32 个项目的共变更数据


def _get_or_load_co_change_cache(
    project_root: str, since_days: int
) -> dict[tuple[str, str], int]:
    """获取或加载共变更缓存（消除 get_co_change_score 和 get_co_change_neighbors 的重复逻辑）。"""
    cache_key = (project_root, since_days)
    cache = _co_change_cache.get(cache_key)
    if cache is not None:
        _co_change_cache.move_to_end(cache_key)
    else:
        if len(_co_change_cache) >= _MAX_CO_CHANGE_CACHE:
            _co_change_cache.popitem(last=False)
        cache = _load_co_change_scores(project_root, since_days=since_days)
        _co_change_cache[cache_key] = cache
    return cache


def get_co_change_score(
    project_root: str, file_a: str, file_b: str, since_days: int = 30
) -> int:
    """查询两个文件的 git 共变更次数（带缓存，公开接口）。"""
    cache = _get_or_load_co_change_cache(project_root, since_days)
    a, b = sorted([file_a, file_b])
    return cache.get((a, b), 0)


def get_co_change_neighbors(
    project_root: str,
    file_path: str,
    top_n: int = 5,
    since_days: int = 30,
) -> list[tuple[str, int]]:
    """返回与指定文件共变频率最高的文件列表（降序）。

    用途：识别隐式耦合——两个文件在 git 历史中频繁一起修改，
    即使代码上没有显式依赖，也可能存在隐含关联。
    """
    cache = _get_or_load_co_change_cache(project_root, since_days)
    neighbors: dict[str, int] = {}
    for (a, b), count in cache.items():
        if a == file_path:
            neighbors[b] = count
        elif b == file_path:
            neighbors[a] = count
    return sorted(neighbors.items(), key=lambda x: -x[1])[:top_n]


def _load_co_change_scores(
    project_root: str, since_days: int = 30
) -> dict[tuple[str, str], int]:
    """统计项目中文件对的 git 共变更次数。"""
    from .git_backend import GitBackend

    scores: dict[tuple[str, str], int] = defaultdict(int)
    try:
        git = GitBackend(project_root)
        commit_groups = git.log_commits_grouped(since_days=since_days)
    except Exception:
        logger.warning("Failed to load co-change scores from git", exc_info=True)
        return dict(scores)

    for commit_files in commit_groups:
        if len(commit_files) > 1:
            for i in range(len(commit_files)):
                for j in range(i + 1, len(commit_files)):
                    a, b = sorted([commit_files[i], commit_files[j]])
                    scores[(a, b)] += 1

    return dict(scores)


# ═══════════════════════════════════════════════════════════════════════════════
# 测试盲区检测
# ═══════════════════════════════════════════════════════════════════════════════

# LOW_SIGNAL_KINDS 已从 __init__.py 统一导入，不再重复定义


def _signal_weight_for_symbol(sym: Any) -> float:
    """独立版符号信号权重，委托给 __init__.py 中的统一实现。"""
    kind = getattr(sym, "kind", "") if hasattr(sym, "kind") else sym.get("kind", "")
    name = getattr(sym, "name", "") if hasattr(sym, "name") else sym.get("name", "")
    visibility = (
        getattr(sym, "visibility", "")
        if hasattr(sym, "visibility")
        else sym.get("visibility", "")
    )
    return signal_weight_for_symbol(kind, name, visibility)


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
        untested.append(
            {
                "symbol": getattr(sym, "name", str(sid)),
                "kind": getattr(sym, "kind", ""),
                "file": getattr(sym, "file", ""),
                "line": getattr(sym, "line", 0),
                "incoming_calls": incoming,
                "risk_score": round(score, 1),
            }
        )

    untested.sort(key=lambda x: -x["risk_score"])
    return untested[:max_results]
