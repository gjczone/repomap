"""Issue #70 P1 regression tests — JSON envelope, LSP consolidation, exit codes, symbol resolution.

P1-1: All commands must use json_envelope() for --json output
P1-2: LSP calls must not spawn 3 separate processes per symbol
P1-3: verify status must be at top level, not nested in result
P1-4: query-symbol JSON mode must return EXIT_NO_RESULTS (3), not 0
P1-5: _select_symbol_match must not fall back to full unfiltered list
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_envelope(testcase: unittest.TestCase, output: str) -> dict:
    """Parse JSON and verify it has envelope fields."""
    data = json.loads(output)
    testcase.assertIn("schema_version", data, "Missing schema_version")
    testcase.assertIn("command", data, "Missing command")
    testcase.assertIn("project", data, "Missing project")
    testcase.assertIn("status", data, "Missing status")
    testcase.assertIn("result", data, "Missing result")
    return data


def _make_engine(project_root: str) -> MagicMock:
    """Create a mock engine with minimal graph."""
    engine = MagicMock()
    engine.project_root = project_root
    engine.graph = MagicMock()
    engine.graph.symbols = {}
    engine.graph.outgoing = {}
    engine.graph.incoming = {}
    engine.graph.file_symbols = {}
    engine.graph.file_imports = {}
    engine.graph.file_calls = {}
    engine.scan_stats = MagicMock(
        listed_source_files=0,
        selected_source_files=0,
        processed_files=0,
        filtered_path_files=0,
        filtered_large_files=0,
        truncated_files=0,
        failed_files=[],
        scan_duration_ms=0,
        timeout_triggered=False,
    )
    engine.entry_points.return_value = []
    engine.hotspots.return_value = []
    engine.suggested_reading_order.return_value = []
    engine.module_summary.return_value = []
    engine.summary_symbols.return_value = []
    engine.supporting_files.return_value = []
    engine.list_routes.return_value = []
    engine.query_symbol.return_value = []
    return engine


def _format_symbol_ref_stub(_engine: object, sid: str) -> dict:
    """Serializable stub for _format_symbol_ref."""
    return {"name": sid, "file": "test.py", "line": 1, "kind": "function"}


# ---------------------------------------------------------------------------
# P1-1: All --json outputs must use json_envelope()
# ---------------------------------------------------------------------------


class TestJsonEnvelopeCompliance(unittest.TestCase):
    """Every command's --json output must parse as valid json_envelope format."""

    def test_overview_json_uses_envelope(self) -> None:
        """P1-1: overview --json must use json_envelope()."""
        from src.cli.commands.overview import run_overview

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("x = 1\n")
            engine = _make_engine(tmpdir)
            with patch("src.cli.commands.overview._scan_engine", return_value=engine):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_overview(tmpdir, 1000, 5000, as_json=True)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "overview")

    def test_call_chain_json_uses_envelope(self) -> None:
        """P1-1: call-chain --json must use json_envelope()."""
        from src.cli.commands.symbol import run_call_chain

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("def foo(): pass\n")
            engine = _make_engine(tmpdir)
            sym = MagicMock()
            sym.id = "test::foo"
            sym.name = "foo"
            sym.kind = "function"
            sym.file = "dummy.py"
            sym.line = 1
            sym.signature = "foo()"
            sym.pagerank = 0.5
            engine.query_symbol.return_value = [sym]
            engine.call_chain.return_value = {"callers": [], "callees": []}
            with (
                patch("src.cli.commands.symbol._scan_engine", return_value=engine),
                patch(
                    "src.cli.commands.symbol._select_symbol_match",
                    return_value=(sym, None, "treesitter"),
                ),
            ):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_call_chain(
                        tmpdir, 1000, "foo", None, "both", 5, 5000, as_json=True
                    )
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "call-chain")

    def test_query_symbol_json_uses_envelope(self) -> None:
        """P1-1: query-symbol --json must use json_envelope()."""
        from src.cli.commands.symbol import run_query_symbol

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("def foo(): pass\n")
            engine = _make_engine(tmpdir)
            sym = MagicMock()
            sym.name = "foo"
            sym.kind = "function"
            sym.file = "dummy.py"
            sym.line = 1
            sym.pagerank = 0.5
            sym.signature = "foo()"
            sym.return_type = None
            sym.params = None
            engine.query_symbol.return_value = [sym]
            with (
                patch("src.cli.commands.symbol._scan_engine", return_value=engine),
                patch(
                    "src.cli.commands.symbol._collect_lsp_evidence_for_symbol",
                    return_value={"status": "skipped"},
                ),
            ):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_query_symbol(tmpdir, 1000, "foo", None, 5000, as_json=True)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "query-symbol")

    def test_file_detail_json_uses_envelope(self) -> None:
        """P1-1: file-detail --json must use json_envelope()."""
        from src.cli.commands.symbol import run_file_detail

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "dummy.py"
            fpath.write_text("def foo(): pass\n")
            engine = _make_engine(tmpdir)
            sym = MagicMock()
            sym.name = "foo"
            sym.kind = "function"
            sym.line = 1
            sym.pagerank = 0.5
            sym.signature = "foo()"
            sym.return_type = None
            sym.params = None
            engine.graph.file_symbols = {"dummy.py": ["test::foo"]}
            engine.graph.symbols = {"test::foo": sym}
            engine.render_file_detail.return_value = "detail"
            with (
                patch("src.cli.commands.symbol._scan_engine", return_value=engine),
                patch(
                    "src.lsp.collect_lsp_symbol_tree",
                    return_value=[],
                ),
            ):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_file_detail(
                        tmpdir, 1000, "dummy.py", 50, 5000, as_json=True
                    )
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "file-detail")

    def test_refs_global_json_uses_envelope(self) -> None:
        """P1-1: refs (no --symbol) --json must use json_envelope()."""
        from src.cli.commands.symbol import run_refs

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("def foo(): pass\n")
            engine = _make_engine(tmpdir)
            engine.graph.symbols = {"s1": MagicMock()}
            engine.graph.outgoing = {}
            with (
                patch("src.cli.commands.symbol._scan_engine", return_value=engine),
                patch(
                    "src.cli.commands.symbol._format_symbol_ref",
                    side_effect=_format_symbol_ref_stub,
                ),
            ):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_refs(tmpdir, 1000, None, None, as_json=True)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "refs")

    def test_orphan_json_uses_envelope(self) -> None:
        """P1-1: orphan --json must use json_envelope()."""
        from src.cli.commands.verify import run_orphan

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("def foo(): pass\n")
            engine = _make_engine(tmpdir)
            with patch("src.cli.commands.verify._scan_engine", return_value=engine):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_orphan(tmpdir, 1000, as_json=True)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "orphan")

    def test_diff_json_uses_envelope(self) -> None:
        """P1-1: diff --json must use json_envelope()."""
        from src.cli.commands.cache import run_diff

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "src.cli.commands.cache.diff_project",
                return_value={
                    "summary": {"added": 0, "removed": 0, "modified": 0},
                    "added_symbols": [],
                    "removed_symbols": [],
                    "modified_symbols": [],
                },
            ):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_diff(tmpdir, as_json=True)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "diff")

    def test_query_json_uses_envelope(self) -> None:
        """P1-1: query --json must use json_envelope()."""
        from src.cli.commands.query import run_query

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("def foo(): pass\n")
            engine = _make_engine(tmpdir)
            engine.file_analysis.return_value = {}
            engine.list_routes.return_value = []
            with (
                patch("src.cli.commands.query._scan_engine", return_value=engine),
                patch("src.cli.commands.query.find_related_tests", return_value=[]),
                patch("src.cli.commands.query.is_test_like_file", return_value=False),
            ):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_query(tmpdir, 1000, "foo", 10, 50, False, True, None, None)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "query")

    def test_lsp_doctor_json_uses_envelope(self) -> None:
        """P1-1: lsp-doctor --json must use json_envelope()."""
        from src.cli.commands.doctor import run_lsp_doctor

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.lsp.detect_lsp_servers", return_value=[]):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_lsp_doctor(tmpdir, as_json=True)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "lsp doctor")


# ---------------------------------------------------------------------------
# P1-3: verify status must be at top level
# ---------------------------------------------------------------------------


class TestVerifyStatusTopLevel(unittest.TestCase):
    """P1-3: verify --json must have status at top level, not nested in result."""

    def test_verify_status_at_top_level(self) -> None:
        """Status must be accessible as payload['status'], not payload['result']['status']."""
        from src.cli.commands.verify import run_verify

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("x = 1\n")
            engine = MagicMock()
            engine.project_root = tmpdir
            engine.graph = MagicMock()
            engine.graph.symbols = {}
            engine.graph.outgoing = {}
            engine.graph.incoming = {}
            engine.scan_stats = MagicMock(
                listed_source_files=0,
                selected_source_files=0,
                processed_files=0,
                filtered_path_files=0,
                filtered_large_files=0,
                truncated_files=0,
                failed_files=[],
                scan_duration_ms=0,
                timeout_triggered=False,
            )
            with (
                patch("src.cli.commands.verify._scan_engine", return_value=engine),
                patch(
                    "src.cli.commands.verify._collect_changed_files",
                    return_value=([], None),
                ),
                patch(
                    "src.cli.commands.verify._diff_risk_evidence",
                    return_value={
                        "riskLevel": "low",
                        "riskReasons": [],
                        "missingChecks": [],
                        "affectedList": [],
                        "tests": [],
                    },
                ),
                patch(
                    "src.cli.commands.verify._detect_contract_risks",
                    return_value=[],
                ),
                patch(
                    "src.cli.commands.verify._overall_verify_status",
                    return_value="pass",
                ),
                patch(
                    "src.cli.commands.verify._verify_graph_diff_payload",
                    return_value={},
                ),
                patch(
                    "src.cli.commands.verify._verify_impact_session_payload",
                    return_value={},
                ),
                patch(
                    "src.cli.commands.verify.find_untested_symbols",
                    return_value=[],
                ),
            ):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_verify(
                        tmpdir,
                        as_json=True,
                        types=None,
                        max_issues=50,
                        resolve_symbols=False,
                        lsp_timeout=8.0,
                        lsp_max_files=20,
                        with_diff=False,
                        quick=True,
                    )
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = json.loads(output)
        # P1-3: status MUST be at top level (via json_envelope)
        self.assertIn("status", data, "status must be at top level of json_envelope")
        self.assertEqual(
            data["status"], "pass", "top-level status must match verify result"
        )


# ---------------------------------------------------------------------------
# P1-4: query-symbol exit code consistency
# ---------------------------------------------------------------------------


class TestQuerySymbolExitCode(unittest.TestCase):
    """P1-4: query-symbol --json with no results must return EXIT_NO_RESULTS (3)."""

    def test_json_mode_no_results_returns_exit_3(self) -> None:
        """JSON mode with empty matches must return EXIT_NO_RESULTS, not 0."""
        from src.cli.commands.symbol import run_query_symbol
        from src.cli.handlers import EXIT_NO_RESULTS

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MagicMock()
            engine.project_root = tmpdir
            engine.query_symbol.return_value = []
            with patch("src.cli.commands.symbol._scan_engine", return_value=engine):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_query_symbol(
                        tmpdir, 1000, "nonexistent", None, 5000, as_json=True
                    )
        self.assertEqual(
            rc,
            EXIT_NO_RESULTS,
            f"Expected exit code {EXIT_NO_RESULTS} for no results, got {rc}",
        )


# ---------------------------------------------------------------------------
# P1-5: _select_symbol_match fallback
# ---------------------------------------------------------------------------


class TestSelectSymbolMatchFallback(unittest.TestCase):
    """P1-5: When LSP definition doesn't match any candidate, fall back to
    the candidate (tree-sitter), NOT to the full unfiltered matches list."""

    def test_lsp_mismatch_falls_back_to_candidate_not_global(self) -> None:
        """When LSP says symbol is in file_b but candidate is in file_a,
        must return the candidate, not a random symbol from file_b."""
        from src.cli.handlers import _select_symbol_match

        # Create a candidate in file_a
        candidate = MagicMock()
        candidate.name = "foo"
        candidate.file = "file_a.py"
        candidate.line = 10
        candidate.id = "file_a::foo"

        # Create a different symbol with same name in file_b
        other = MagicMock()
        other.name = "foo"
        other.file = "file_b.py"
        other.line = 5
        other.id = "file_b::foo"

        engine = MagicMock()
        engine.project_root = "/tmp/test"
        # query_symbol returns both symbols
        engine.query_symbol.return_value = [candidate, other]

        # LSP says definition is in file_b (different from candidate)
        lsp_result = MagicMock()
        lsp_result.status = "ok"
        lsp_def = MagicMock()
        lsp_def.file = "file_b.py"
        lsp_def.line = 5
        lsp_result.definitions = [lsp_def]
        lsp_result.references = []

        with patch(
            "src.lsp.collect_lsp_symbol_evidence",
            return_value=lsp_result,
        ):
            # file_path filters to file_a, so candidates_for_lsp = [candidate]
            result, _error, _tier = _select_symbol_match(
                engine, "foo", file_path="file_a.py"
            )

        # Must return the candidate from file_a, NOT the symbol from file_b
        self.assertIsNotNone(result, "Should return a symbol")
        assert result is not None  # for pyright
        self.assertEqual(
            result.file,
            "file_a.py",
            "Must fall back to candidate in file_a, not global match in file_b",
        )


# ---------------------------------------------------------------------------
# P1-2: LSP process consolidation
# ---------------------------------------------------------------------------


class TestLspProcessConsolidation(unittest.TestCase):
    """P1-2: _collect_lsp_evidence_for_symbol must use collect_lsp_full_evidence (single session)."""

    def test_collect_lsp_evidence_uses_consolidated_function(self) -> None:
        """Must call collect_lsp_full_evidence, not separate collect_lsp_symbol_evidence + collect_lsp_hover."""
        from src.cli.handlers import _collect_lsp_evidence_for_symbol

        engine = MagicMock()
        engine.project_root = "/tmp/test"

        symbol = MagicMock()
        symbol.file = "test.py"
        symbol.line = 10
        symbol.name = "foo"

        evidence_result = MagicMock()
        evidence_result.status = "ok"
        evidence_result.server = "pyright"
        evidence_result.language = "python"
        evidence_result.definitions = [MagicMock(file="test.py", line=10)]
        evidence_result.references = []
        evidence_result.duration_ms = 100
        evidence_result.command = ["pyright"]
        evidence_result.workspace_root = "/tmp/test"
        evidence_result.reason = None

        hover_result = MagicMock()
        hover_result.file = "test.py"
        hover_result.line = 10
        hover_result.col = 5
        hover_result.contents = "def foo() -> None"

        full_evidence_calls: list[int] = []

        def mock_full_evidence(*_args: object, **_kwargs: object) -> tuple:
            full_evidence_calls.append(1)
            return (evidence_result, hover_result)

        with (
            patch(
                "src.lsp.collect_lsp_full_evidence",
                side_effect=mock_full_evidence,
            ),
            patch(
                "src.lsp.run_result_to_dict",
                return_value={
                    "status": "ok",
                    "server": "pyright",
                    "language": "python",
                    "definitions": [{"file": "test.py", "line": 10}],
                    "references": [],
                    "duration_ms": 100,
                },
            ),
        ):
            result = _collect_lsp_evidence_for_symbol(engine, symbol, 8.0)

        # Must call consolidated function exactly once
        self.assertEqual(
            len(full_evidence_calls),
            1,
            "Must call collect_lsp_full_evidence exactly once",
        )
        # Result must include hover from the single session
        self.assertIn("hover", result)
        self.assertEqual(result["hover"]["contents"], "def foo() -> None")

    def test_impact_json_uses_envelope(self) -> None:
        """P1-6: impact --json must use json_envelope()."""
        from src.cli.commands.impact import run_impact

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dummy.py").write_text("def target(): pass\n")
            engine = _make_engine(tmpdir)
            # Mock additional attributes needed by impact
            engine.scan_stats.processed_files = 1
            engine.scan_stats.listed_source_files = 1
            engine.scan_stats.selected_source_files = 1
            engine.scan_stats.filtered_path_files = 0
            engine.scan_stats.filtered_large_files = 0
            engine.scan_stats.truncated_files = 0
            engine.scan_stats.failed_files = set()
            engine.scan_stats.scan_duration_ms = 100
            engine.scan_stats.timeout_triggered = False
            # Mock graph attributes for impact
            engine.graph.incoming = {}
            engine.graph.outgoing = {}
            engine.entry_points.return_value = []
            engine.file_analysis.return_value = {}
            with patch("src.cli.commands.impact._scan_engine", return_value=engine):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_impact(tmpdir, 100, ["dummy.py"], 100, as_json=True)
                output = buf.getvalue()
        self.assertEqual(rc, 0)
        data = _parse_envelope(self, output)
        self.assertEqual(data["command"], "impact")
        # P1-7: project must be resolved path
        self.assertEqual(data["project"], str(Path(tmpdir).resolve()))
        # P1-8: scan_stats should be inside result, not top-level
        self.assertIn("scan_stats", data["result"])
        self.assertNotIn("scanStats", data)
        self.assertNotIn("scan_stats", data)


if __name__ == "__main__":
    unittest.main()
