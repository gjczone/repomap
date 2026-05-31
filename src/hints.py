"""Issue #73 — 运行时提示系统

每个命令的 hint 函数根据输出特征返回 1-3 条上下文相关的下一步操作提示。
提示仅在文本模式下使用，JSON 模式忽略。
"""

from __future__ import annotations


def query_symbol_hint(match_count: int, has_file_filter: bool) -> list[str]:
    """query-symbol 命令的运行时提示"""
    hints: list[str] = []
    if match_count == 0:
        hints.append(
            '> No exact match. Try `repomap query --query "<name>" --project .`'
            " for fuzzy topic search"
        )
    elif match_count == 1:
        hints.append(
            "> Next: `repomap call-chain --symbol <name> --project .`"
            " to see callers and callees"
        )
        hints.append(
            "> Next: `repomap refs --symbol <name> --project .` to find all references"
        )
    elif match_count > 1 and not has_file_filter:
        hints.append("> Tip: Use `--file-path <file>` to narrow to a specific file")
    return hints[:3]


def call_chain_hint(caller_count: int, callee_count: int) -> list[str]:
    """call-chain 命令的运行时提示"""
    hints: list[str] = []
    if caller_count > 10:
        hints.append(
            "> Tip: Use `--direction callers` to see only callers,"
            " or `--depth 5` for deeper analysis"
        )
    if callee_count > 10:
        hints.append("> Tip: Use `--direction callees` to see only callees")
    if caller_count <= 2 and callee_count <= 2:
        hints.append(
            "> Next: `repomap refs --symbol <name> --project .`"
            " to find all references including non-call usage"
        )
    if not hints:
        hints.append(
            "> Next: `repomap refs --symbol <name> --project .` to find all references"
        )
    return hints[:3]


def overview_hint(
    has_hotspots: bool, has_reading_order: bool, has_modules: bool
) -> list[str]:
    """overview 命令的运行时提示"""
    hints: list[str] = []
    if has_hotspots:
        hints.append(
            "> Next: `repomap file-detail --file-path <top-hotspot> --project .`"
            " to understand the densest file before editing"
        )
    if has_reading_order:
        hints.append(
            "> Next: `repomap impact --files <file> --with-symbols --project .`"
            " to assess change blast radius"
        )
    if has_modules:
        hints.append(
            '> Next: `repomap query --query "<topic>" --project .`'
            " to find files by feature/domain"
        )
    if not hints:
        hints.append(
            "> Next: `repomap file-detail --file-path <file> --project .`"
            " to inspect a file"
        )
    return hints[:3]


def file_detail_hint(has_symbols: bool, has_callers: bool) -> list[str]:
    """file-detail 命令的运行时提示"""
    hints: list[str] = []
    if has_symbols:
        hints.append(
            "> Next: `repomap impact --files <file> --with-symbols --project .`"
            " before making changes"
        )
    if has_callers:
        hints.append(
            "> Next: `repomap call-chain --symbol <top-symbol> --project .`"
            " to trace call flow"
        )
    return hints[:3]


def impact_hint(risk_level: str, has_suggested_tests: bool) -> list[str]:
    """impact 命令的运行时提示"""
    hints: list[str] = []
    if risk_level == "high":
        hints.append(
            "> High risk — after editing, run"
            " `repomap verify --project .` to verify changes"
        )
    if has_suggested_tests:
        hints.append("> Suggested tests: run the listed test files to verify changes")
    return hints[:3]


def verify_hint(status: str, has_contract_risks: bool) -> list[str]:
    """verify 命令的运行时提示"""
    hints: list[str] = []
    if status == "failed":
        hints.append(
            "> Diagnostics failed. Run `repomap check --project .`"
            " for detailed compiler/lint output"
        )
    if has_contract_risks:
        hints.append("> Contract risks detected — review and fix before committing")
    if status == "passed" and not has_contract_risks:
        hints.append(
            "> All checks passed. Run `repomap fix --project .`"
            " to auto-fix lint, then `repomap ready --project .`"
        )
    return hints[:3]


def check_hint(has_errors: bool) -> list[str]:
    """check 命令的运行时提示"""
    hints: list[str] = []
    if has_errors:
        hints.append("> Fix errors above, then re-run `repomap check --project .`")
    else:
        hints.append(
            "> No errors. Run `repomap verify --project .`"
            " for full post-edit evidence gate"
        )
    return hints[:3]


def query_hint(file_match_count: int) -> list[str]:
    """query 命令的运行时提示"""
    hints: list[str] = []
    if file_match_count > 0:
        hints.append(
            "> Next: `repomap file-detail --file-path <top-result> --project .`"
            " to understand the file structure"
        )
    return hints[:3]


def search_hint(symbol_match_count: int) -> list[str]:
    """search 命令的运行时提示"""
    hints: list[str] = []
    if symbol_match_count > 0:
        hints.append(
            "> Next: `repomap query-symbol --symbol <name> --project .`"
            " for precise symbol lookup with LSP"
        )
    return hints[:3]


def routes_hint(has_routes: bool) -> list[str]:
    """routes 命令的运行时提示"""
    hints: list[str] = []
    if has_routes:
        hints.append(
            "> Next: `repomap refs --symbol <handler> --project .`"
            " to find all callers of a specific route handler"
        )
    return hints[:3]
