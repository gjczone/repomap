"""Issue #73 — 运行时提示系统测试

每个测试对应 issue #73 中一个命令的 hint 函数，验证：文本模式输出 1-3 行提示，JSON 模式不输出提示。
"""

from __future__ import annotations

import unittest


class TestQuerySymbolHints(unittest.TestCase):
    """query-symbol 命令的运行时提示"""

    def test_single_exact_match_suggests_call_chain_and_refs(self) -> None:
        from src.hints import query_symbol_hint

        hints = query_symbol_hint(match_count=1, has_file_filter=False)
        self.assertIsInstance(hints, list)
        self.assertGreaterEqual(len(hints), 1)
        self.assertLessEqual(len(hints), 3)
        self.assertTrue(any("call-chain" in h for h in hints))
        self.assertTrue(any("refs" in h for h in hints))

    def test_multiple_matches_without_filter_suggests_file_path(self) -> None:
        from src.hints import query_symbol_hint

        hints = query_symbol_hint(match_count=5, has_file_filter=False)
        self.assertTrue(any("--file-path" in h for h in hints))

    def test_multiple_matches_with_filter_no_path_hint(self) -> None:
        from src.hints import query_symbol_hint

        hints = query_symbol_hint(match_count=5, has_file_filter=True)
        self.assertFalse(any("--file-path" in h for h in hints))

    def test_zero_matches_suggests_query(self) -> None:
        from src.hints import query_symbol_hint

        hints = query_symbol_hint(match_count=0, has_file_filter=False)
        self.assertTrue(any("query" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import query_symbol_hint

        for match_count in (0, 1, 3, 10):
            hints = query_symbol_hint(match_count=match_count, has_file_filter=False)
            for h in hints:
                self.assertTrue(h.startswith("> "), f"Hint must start with '> ': {h}")


class TestCallChainHints(unittest.TestCase):
    """call-chain 命令的运行时提示"""

    def test_many_callers_suggests_direction_filter(self) -> None:
        from src.hints import call_chain_hint

        hints = call_chain_hint(caller_count=15, callee_count=3)
        self.assertTrue(any("--direction" in h for h in hints))

    def test_many_callees_suggests_direction_filter(self) -> None:
        from src.hints import call_chain_hint

        hints = call_chain_hint(caller_count=2, callee_count=15)
        self.assertTrue(any("--direction" in h for h in hints))

    def test_few_results_suggests_refs(self) -> None:
        from src.hints import call_chain_hint

        hints = call_chain_hint(caller_count=2, callee_count=1)
        self.assertTrue(any("refs" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import call_chain_hint

        for caller, callee in ((15, 3), (3, 15), (1, 2), (20, 20)):
            hints = call_chain_hint(caller_count=caller, callee_count=callee)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestRefsHints(unittest.TestCase):
    """refs 命令的运行时提示"""

    def test_has_callers_suggests_call_chain(self) -> None:
        from src.hints import refs_hint

        hints = refs_hint(called_by_count=5)
        self.assertTrue(any("call-chain" in h for h in hints))

    def test_no_callers_suggests_orphan(self) -> None:
        from src.hints import refs_hint

        hints = refs_hint(called_by_count=0)
        self.assertTrue(any("orphan" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import refs_hint

        for count in (0, 1, 10):
            hints = refs_hint(called_by_count=count)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestOverviewHints(unittest.TestCase):
    """overview 命令的运行时提示"""

    def test_has_hotspots_suggests_file_detail(self) -> None:
        from src.hints import overview_hint

        hints = overview_hint(
            has_hotspots=True, has_reading_order=False, has_modules=False
        )
        self.assertTrue(any("file-detail" in h for h in hints))

    def test_has_reading_order_suggests_impact(self) -> None:
        from src.hints import overview_hint

        hints = overview_hint(
            has_hotspots=False, has_reading_order=True, has_modules=False
        )
        self.assertTrue(any("impact" in h for h in hints))

    def test_has_modules_suggests_query(self) -> None:
        from src.hints import overview_hint

        hints = overview_hint(
            has_hotspots=False, has_reading_order=False, has_modules=True
        )
        self.assertTrue(any("query" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import overview_hint

        for hh, hr, hm in (
            (True, False, False),
            (False, True, False),
            (False, False, True),
            (True, True, True),
        ):
            hints = overview_hint(has_hotspots=hh, has_reading_order=hr, has_modules=hm)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestFileDetailHints(unittest.TestCase):
    """file-detail 命令的运行时提示"""

    def test_has_symbols_suggests_impact(self) -> None:
        from src.hints import file_detail_hint

        hints = file_detail_hint(has_symbols=True, has_callers=False)
        self.assertTrue(any("impact" in h for h in hints))

    def test_has_callers_suggests_call_chain(self) -> None:
        from src.hints import file_detail_hint

        hints = file_detail_hint(has_symbols=False, has_callers=True)
        self.assertTrue(any("call-chain" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import file_detail_hint

        for hs, hc in ((True, False), (False, True), (True, True)):
            hints = file_detail_hint(has_symbols=hs, has_callers=hc)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestImpactHints(unittest.TestCase):
    """impact 命令的运行时提示"""

    def test_high_risk_suggests_verify(self) -> None:
        from src.hints import impact_hint

        hints = impact_hint(risk_level="high", has_suggested_tests=False)
        self.assertTrue(any("verify" in h for h in hints))

    def test_has_suggested_tests_shows_test_hint(self) -> None:
        from src.hints import impact_hint

        hints = impact_hint(risk_level="low", has_suggested_tests=True)
        self.assertTrue(any("test" in h.lower() for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import impact_hint

        for risk, tests in (("high", False), ("low", True), ("medium", True)):
            hints = impact_hint(risk_level=risk, has_suggested_tests=tests)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestVerifyHints(unittest.TestCase):
    """verify 命令的运行时提示"""

    def test_failed_status_suggests_check(self) -> None:
        from src.hints import verify_hint

        hints = verify_hint(status="failed", has_contract_risks=False)
        self.assertTrue(any("check" in h for h in hints))

    def test_passed_status_suggests_fix(self) -> None:
        from src.hints import verify_hint

        hints = verify_hint(status="passed", has_contract_risks=False)
        self.assertTrue(any("fix" in h for h in hints))

    def test_contract_risks_show_warning(self) -> None:
        from src.hints import verify_hint

        hints = verify_hint(status="passed", has_contract_risks=True)
        self.assertTrue(any("contract" in h.lower() for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import verify_hint

        for status, risks in (("failed", False), ("passed", False), ("passed", True)):
            hints = verify_hint(status=status, has_contract_risks=risks)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestCheckHints(unittest.TestCase):
    """check 命令的运行时提示"""

    def test_has_errors_shows_fix_hint(self) -> None:
        from src.hints import check_hint

        hints = check_hint(has_errors=True)
        self.assertTrue(any("check" in h for h in hints))

    def test_passed_suggests_verify(self) -> None:
        from src.hints import check_hint

        hints = check_hint(has_errors=False)
        self.assertTrue(any("verify" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import check_hint

        for errors in (True, False):
            hints = check_hint(has_errors=errors)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestOrphanHints(unittest.TestCase):
    """orphan 命令的运行时提示"""

    def test_has_candidates_suggests_refs(self) -> None:
        from src.hints import orphan_hint

        hints = orphan_hint(has_high_confidence_candidates=True)
        self.assertTrue(any("refs" in h for h in hints))

    def test_no_candidates_no_refs(self) -> None:
        from src.hints import orphan_hint

        hints = orphan_hint(has_high_confidence_candidates=False)
        self.assertFalse(any("refs" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import orphan_hint

        for has_candidates in (True, False):
            hints = orphan_hint(has_high_confidence_candidates=has_candidates)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestHotspotsHints(unittest.TestCase):
    """hotspots 命令的运行时提示"""

    def test_has_hotspots_suggests_file_detail_and_impact(self) -> None:
        from src.hints import hotspots_hint

        hints = hotspots_hint(has_hotspots=True)
        self.assertTrue(any("file-detail" in h for h in hints))
        self.assertTrue(any("impact" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import hotspots_hint

        for has in (True, False):
            hints = hotspots_hint(has_hotspots=has)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestQueryHints(unittest.TestCase):
    """query 命令的运行时提示"""

    def test_found_files_suggests_file_detail(self) -> None:
        from src.hints import query_hint

        hints = query_hint(file_match_count=5)
        self.assertTrue(any("file-detail" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import query_hint

        for count in (0, 1, 10):
            hints = query_hint(file_match_count=count)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestSearchHints(unittest.TestCase):
    """search 命令的运行时提示"""

    def test_found_symbols_suggests_query_symbol(self) -> None:
        from src.hints import search_hint

        hints = search_hint(symbol_match_count=5)
        self.assertTrue(any("query-symbol" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import search_hint

        for count in (0, 1, 10):
            hints = search_hint(symbol_match_count=count)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestRoutesHints(unittest.TestCase):
    """routes 命令的运行时提示"""

    def test_has_routes_suggests_refs(self) -> None:
        from src.hints import routes_hint

        hints = routes_hint(has_routes=True)
        self.assertTrue(any("refs" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import routes_hint

        for has in (True, False):
            hints = routes_hint(has_routes=has)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestStateMapHints(unittest.TestCase):
    """state-map 命令的运行时提示"""

    def test_has_writers_suggests_refs(self) -> None:
        from src.hints import state_map_hint

        hints = state_map_hint(has_writers=True)
        self.assertTrue(any("refs" in h for h in hints))

    def test_all_hints_start_with_arrow(self) -> None:
        from src.hints import state_map_hint

        for has in (True, False):
            hints = state_map_hint(has_writers=has)
            for h in hints:
                self.assertTrue(h.startswith("> "))


class TestHintFormat(unittest.TestCase):
    """通用格式验证"""

    def test_all_hint_functions_return_at_most_3_hints(self) -> None:
        from src.hints import (
            query_symbol_hint,
            call_chain_hint,
            refs_hint,
            overview_hint,
            file_detail_hint,
            impact_hint,
            verify_hint,
            check_hint,
            orphan_hint,
            hotspots_hint,
            query_hint,
            search_hint,
            routes_hint,
            state_map_hint,
        )

        all_funcs = [
            lambda: query_symbol_hint(match_count=1, has_file_filter=False),
            lambda: call_chain_hint(caller_count=15, callee_count=3),
            lambda: refs_hint(called_by_count=0),
            lambda: overview_hint(
                has_hotspots=True, has_reading_order=True, has_modules=True
            ),
            lambda: file_detail_hint(has_symbols=True, has_callers=True),
            lambda: impact_hint(risk_level="high", has_suggested_tests=True),
            lambda: verify_hint(status="failed", has_contract_risks=True),
            lambda: check_hint(has_errors=True),
            lambda: orphan_hint(has_high_confidence_candidates=True),
            lambda: hotspots_hint(has_hotspots=True),
            lambda: query_hint(file_match_count=10),
            lambda: search_hint(symbol_match_count=10),
            lambda: routes_hint(has_routes=True),
            lambda: state_map_hint(has_writers=True),
        ]

        for fn in all_funcs:
            hints = fn()
            self.assertLessEqual(
                len(hints),
                3,
                f"Function returned {len(hints)} hints, max is 3: {hints}",
            )
            self.assertGreaterEqual(len(hints), 1)

    def test_hints_contain_project_flag(self) -> None:
        from src.hints import (
            query_symbol_hint,
            call_chain_hint,
            refs_hint,
            overview_hint,
            file_detail_hint,
            impact_hint,
            verify_hint,
            check_hint,
        )

        cmd_hints = {
            "query-symbol": query_symbol_hint(match_count=1, has_file_filter=False),
            "call-chain": call_chain_hint(caller_count=15, callee_count=3),
            "refs": refs_hint(called_by_count=5),
            "file-detail": file_detail_hint(has_symbols=True, has_callers=False),
            "impact": impact_hint(risk_level="high", has_suggested_tests=False),
            "verify": verify_hint(status="passed", has_contract_risks=False),
            "check": check_hint(has_errors=True),
        }

        for cmd, hints in cmd_hints.items():
            for h in hints:
                if f"repomap {cmd}" in h:
                    self.assertIn(
                        "--project", h, f"Hint for {cmd} missing --project: {h}"
                    )
