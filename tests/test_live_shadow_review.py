"""
tests/test_live_shadow_review.py
----------------------------------
Tests for src/tools/live_shadow_review.py.

All tests run fully offline: no Alpaca calls, no credentials, no file writes
beyond tmp_path fixtures.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_report(
    path: Path,
    final_status: str = "PASS",
    selected_symbol: str = "SPY",
    candidate_count: int = 1,
    account_status: str = "active",
    trading_blocked: bool = False,
    account_blocked: bool = False,
    buying_power: str = "10000.00",
    portfolio_value: str = "50000.00",
    position_for_symbol: bool = False,
    open_orders_for_symbol: bool = False,
    checks: list | None = None,
    sizing_summary: dict | None = None,
) -> Path:
    if checks is None:
        checks = [
            {"label": "config",        "status": "PASS", "detail": ""},
            {"label": "candidates",    "status": "PASS", "detail": f"{candidate_count} candidates"},
            {"label": "live_sizing",   "status": "PASS", "detail": "within limits"},
            {"label": "credentials",   "status": "PASS", "detail": ""},
            {"label": "live_account",  "status": "PASS", "detail": f"status={account_status}"},
            {"label": "live_position", "status": "PASS", "detail": ""},
            {"label": "live_orders",   "status": "PASS", "detail": ""},
        ]
    report = {
        "checked_at_utc":         "2026-05-19T12:00:00+00:00",
        "selected_symbol":        selected_symbol,
        "final_status":           final_status,
        "config_path":            "config/settings.paper.local.yaml",
        "account_status":         account_status,
        "trading_blocked":        trading_blocked,
        "account_blocked":        account_blocked,
        "buying_power":           buying_power,
        "portfolio_value":        portfolio_value,
        "position_for_symbol":    position_for_symbol,
        "open_orders_for_symbol": open_orders_for_symbol,
        "candidate_count":        candidate_count,
        "sizing_summary":         sizing_summary,
        "checks":                 checks,
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


_CSV_COLUMNS = [
    "client_order_id", "symbol", "side", "order_type",
    "original_quantity", "effective_quantity", "entry_price",
    "estimated_notional", "live_max_quantity", "live_max_notional",
    "sizing_status", "sizing_reason",
]


def _write_candidates(
    path: Path,
    rows: list[dict] | None = None,
) -> Path:
    if rows is None:
        rows = [{"sizing_status": "PASS", "sizing_reason": "within limits",
                 "symbol": "SPY", "side": "buy", "order_type": "market",
                 "original_quantity": "1.0", "effective_quantity": "1.0",
                 "entry_price": "450.0", "estimated_notional": "450.0",
                 "live_max_quantity": "1.0", "live_max_notional": "500.0",
                 "client_order_id": "BT-001"}]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in _CSV_COLUMNS})
    return path


def _run_main(tmp_path: Path, report_kwargs=None, candidate_rows=None,
              extra_argv: list[str] | None = None) -> int:
    from src.tools.live_shadow_review import main
    report_path    = tmp_path / "report.json"
    candidates_path = tmp_path / "candidates.csv"
    _write_report(report_path, **(report_kwargs or {}))
    _write_candidates(candidates_path, rows=candidate_rows)
    argv = [
        "--report",     str(report_path),
        "--candidates", str(candidates_path),
    ] + (extra_argv or [])
    try:
        main(argv)
        return 0
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# 1. parse_report / parse_candidates
# ---------------------------------------------------------------------------

class TestParsers:
    def test_parse_report_returns_dict(self, tmp_path):
        from src.tools.live_shadow_review import parse_report
        p = _write_report(tmp_path / "r.json")
        result = parse_report(p)
        assert isinstance(result, dict)
        assert result["final_status"] == "PASS"

    def test_parse_report_missing_file_raises(self, tmp_path):
        from src.tools.live_shadow_review import parse_report
        with pytest.raises(FileNotFoundError):
            parse_report(tmp_path / "missing.json")

    def test_parse_report_bad_json_raises(self, tmp_path):
        from src.tools.live_shadow_review import parse_report
        p = tmp_path / "bad.json"
        p.write_text("NOT JSON {{{", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_report(p)

    def test_parse_candidates_returns_list(self, tmp_path):
        from src.tools.live_shadow_review import parse_candidates
        p = _write_candidates(tmp_path / "c.csv")
        rows = parse_candidates(p)
        assert isinstance(rows, list)
        assert len(rows) == 1

    def test_parse_candidates_missing_file_raises(self, tmp_path):
        from src.tools.live_shadow_review import parse_candidates
        with pytest.raises(FileNotFoundError):
            parse_candidates(tmp_path / "missing.csv")

    def test_parse_candidates_multiple_rows(self, tmp_path):
        from src.tools.live_shadow_review import parse_candidates
        rows = [
            {"sizing_status": "PASS", "sizing_reason": "ok"},
            {"sizing_status": "FAIL", "sizing_reason": "notional exceeded"},
        ]
        p = _write_candidates(tmp_path / "c.csv", rows=rows)
        result = parse_candidates(p)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 2. find_blockers / find_warnings
# ---------------------------------------------------------------------------

class TestAnalysis:
    def _report(self, checks):
        return {"checks": checks, "candidate_count": 1,
                "position_for_symbol": False, "open_orders_for_symbol": False,
                "buying_power": "10000", "portfolio_value": "50000",
                "selected_symbol": "SPY", "final_status": "PASS"}

    def test_no_fail_checks_no_blockers(self):
        from src.tools.live_shadow_review import find_blockers
        report = self._report([{"label": "x", "status": "PASS", "detail": ""}])
        assert find_blockers(report, []) == []

    def test_fail_check_creates_blocker(self):
        from src.tools.live_shadow_review import find_blockers
        checks = [{"label": "live_account", "status": "FAIL", "detail": "inactive"}]
        blockers = find_blockers(self._report(checks), [])
        assert len(blockers) == 1
        assert "live_account" in blockers[0]

    def test_candidate_fail_creates_blocker(self):
        from src.tools.live_shadow_review import find_blockers
        report = self._report([])
        cands = [{"sizing_status": "FAIL", "sizing_reason": "notional exceeded"}]
        blockers = find_blockers(report, cands)
        assert any("candidate sizing" in b for b in blockers)

    def test_warn_check_not_a_blocker(self):
        from src.tools.live_shadow_review import find_blockers
        checks = [{"label": "live_account", "status": "WARN", "detail": "bp=0"}]
        assert find_blockers(self._report(checks), []) == []

    def test_warn_check_creates_warning(self):
        from src.tools.live_shadow_review import find_warnings
        checks = [{"label": "live_account", "status": "WARN", "detail": "bp=0"}]
        warnings = find_warnings(self._report(checks))
        assert len(warnings) == 1
        assert "live_account" in warnings[0]

    def test_no_warn_no_warnings(self):
        from src.tools.live_shadow_review import find_warnings
        checks = [{"label": "x", "status": "PASS", "detail": ""}]
        assert find_warnings(self._report(checks)) == []


# ---------------------------------------------------------------------------
# 3. suggest_actions
# ---------------------------------------------------------------------------

class TestSuggestActions:
    def _report(self, **kw):
        base = {
            "checks": [], "candidate_count": 1,
            "position_for_symbol": False, "open_orders_for_symbol": False,
            "buying_power": "10000", "portfolio_value": "50000",
            "selected_symbol": "SPY", "final_status": "PASS",
        }
        base.update(kw)
        return base

    def test_notional_blocker_suggests_review(self):
        from src.tools.live_shadow_review import suggest_actions
        report = self._report(checks=[
            {"label": "live_sizing", "status": "FAIL",
             "detail": "estimated_notional=600 > live_max_notional=500"},
        ])
        actions = suggest_actions(report, [], ["blocker"], [])
        assert any("notional" in a.lower() for a in actions)

    def test_zero_buying_power_suggests_funding(self):
        from src.tools.live_shadow_review import suggest_actions
        report = self._report(buying_power="0")
        actions = suggest_actions(report, [], [], ["warn"])
        assert any("fund" in a.lower() or "buying_power" in a.lower() for a in actions)

    def test_zero_portfolio_value_suggests_funding(self):
        from src.tools.live_shadow_review import suggest_actions
        report = self._report(portfolio_value="0")
        actions = suggest_actions(report, [], [], ["warn"])
        assert any("fund" in a.lower() or "portfolio_value" in a.lower() for a in actions)

    def test_existing_position_suggests_close(self):
        from src.tools.live_shadow_review import suggest_actions
        report = self._report(position_for_symbol=True)
        actions = suggest_actions(report, [], ["blocker"], [])
        assert any("close" in a.lower() or "position" in a.lower() for a in actions)

    def test_existing_order_suggests_cancel(self):
        from src.tools.live_shadow_review import suggest_actions
        report = self._report(open_orders_for_symbol=True)
        actions = suggest_actions(report, [], ["blocker"], [])
        assert any("cancel" in a.lower() or "order" in a.lower() for a in actions)

    def test_no_candidates_suggests_wait(self):
        from src.tools.live_shadow_review import suggest_actions
        report = self._report(candidate_count=0)
        actions = suggest_actions(report, [], ["blocker"], [])
        assert any("signal" in a.lower() or "candidate" in a.lower() for a in actions)

    def test_all_clear_suggests_review_sizing(self):
        from src.tools.live_shadow_review import suggest_actions
        report = self._report()
        actions = suggest_actions(report, [], [], [])
        assert len(actions) > 0


# ---------------------------------------------------------------------------
# 4. build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_pass_report_no_fail_cands_result_pass(self, tmp_path):
        from src.tools.live_shadow_review import build_summary, parse_report, parse_candidates
        rp = _write_report(tmp_path / "r.json", final_status="PASS")
        cp = _write_candidates(tmp_path / "c.csv")
        summary = build_summary(parse_report(rp), parse_candidates(cp))
        assert summary["result"] == "PASS"

    def test_fail_report_result_fail(self, tmp_path):
        from src.tools.live_shadow_review import build_summary, parse_report, parse_candidates
        checks = [{"label": "live_account", "status": "FAIL", "detail": "blocked"}]
        rp = _write_report(tmp_path / "r.json", final_status="FAIL", checks=checks)
        cp = _write_candidates(tmp_path / "c.csv")
        summary = build_summary(parse_report(rp), parse_candidates(cp))
        assert summary["result"] == "FAIL"

    def test_pass_report_with_candidate_fail_result_fail(self, tmp_path):
        from src.tools.live_shadow_review import build_summary, parse_report, parse_candidates
        rp = _write_report(tmp_path / "r.json", final_status="PASS")
        cp = _write_candidates(tmp_path / "c.csv",
                               rows=[{"sizing_status": "FAIL", "sizing_reason": "notional"}])
        summary = build_summary(parse_report(rp), parse_candidates(cp))
        assert summary["result"] == "FAIL"

    def test_fail_count_correct(self, tmp_path):
        from src.tools.live_shadow_review import build_summary, parse_report, parse_candidates
        rp = _write_report(tmp_path / "r.json")
        cp = _write_candidates(tmp_path / "c.csv", rows=[
            {"sizing_status": "FAIL", "sizing_reason": "notional"},
            {"sizing_status": "FAIL", "sizing_reason": "qty cap"},
            {"sizing_status": "PASS", "sizing_reason": "ok"},
        ])
        summary = build_summary(parse_report(rp), parse_candidates(cp))
        assert summary["fail_count"] == 2
        assert summary["pass_count"] == 1

    def test_notional_blocker_in_summary(self, tmp_path):
        from src.tools.live_shadow_review import build_summary, parse_report, parse_candidates
        checks = [{"label": "live_sizing", "status": "FAIL",
                   "detail": "estimated_notional=600 > live_max_notional=500"}]
        rp = _write_report(tmp_path / "r.json", final_status="FAIL", checks=checks)
        cp = _write_candidates(tmp_path / "c.csv",
                               rows=[{"sizing_status": "FAIL", "sizing_reason": "notional exceeded"}])
        summary = build_summary(parse_report(rp), parse_candidates(cp))
        assert len(summary["blockers"]) > 0
        assert any("notional" in b.lower() for b in summary["blockers"])

    def test_zero_buying_power_in_warnings(self, tmp_path):
        from src.tools.live_shadow_review import build_summary, parse_report, parse_candidates
        checks = [{"label": "live_account", "status": "WARN",
                   "detail": "buying_power=0"}]
        rp = _write_report(tmp_path / "r.json", final_status="WARN",
                           buying_power="0", checks=checks)
        cp = _write_candidates(tmp_path / "c.csv")
        summary = build_summary(parse_report(rp), parse_candidates(cp))
        assert len(summary["warnings"]) > 0


# ---------------------------------------------------------------------------
# 5. CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_pass_report_exits_0(self, tmp_path):
        code = _run_main(tmp_path)
        assert code in (0, None)

    def test_fail_report_exits_1(self, tmp_path):
        checks = [{"label": "live_account", "status": "FAIL", "detail": "blocked"}]
        code = _run_main(tmp_path, report_kwargs={"final_status": "FAIL", "checks": checks})
        assert code == 1

    def test_warn_report_exits_1(self, tmp_path):
        checks = [{"label": "live_account", "status": "WARN", "detail": "buying_power=0"},
                  {"label": "live_sizing",   "status": "PASS", "detail": ""},
                  {"label": "candidates",    "status": "PASS", "detail": ""},
                  {"label": "credentials",   "status": "PASS", "detail": ""},
                  {"label": "live_position", "status": "PASS", "detail": ""},
                  {"label": "live_orders",   "status": "PASS", "detail": ""}]
        code = _run_main(tmp_path, report_kwargs={"final_status": "WARN",
                                                   "buying_power": "0",
                                                   "checks": checks})
        assert code == 1

    def test_candidate_fail_exits_1_even_if_report_pass(self, tmp_path):
        code = _run_main(
            tmp_path,
            report_kwargs={"final_status": "PASS"},
            candidate_rows=[{"sizing_status": "FAIL", "sizing_reason": "notional exceeded"}],
        )
        assert code == 1

    def test_missing_report_file_exits_1(self, tmp_path):
        from src.tools.live_shadow_review import main
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--report",     str(tmp_path / "missing.json"),
                "--candidates", str(tmp_path / "missing.csv"),
            ])
        assert exc_info.value.code == 1

    def test_missing_candidates_file_exits_1(self, tmp_path):
        from src.tools.live_shadow_review import main
        report_path = tmp_path / "r.json"
        _write_report(report_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--report",     str(report_path),
                "--candidates", str(tmp_path / "missing.csv"),
            ])
        assert exc_info.value.code == 1

    def test_malformed_json_exits_1(self, tmp_path):
        from src.tools.live_shadow_review import main
        bad = tmp_path / "bad.json"
        bad.write_text("NOT JSON", encoding="utf-8")
        candidates_path = tmp_path / "c.csv"
        _write_candidates(candidates_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--report", str(bad), "--candidates", str(candidates_path)])
        assert exc_info.value.code == 1

    def test_output_contains_final_status(self, tmp_path, capsys):
        _run_main(tmp_path)
        out = capsys.readouterr().out
        assert "final_status" in out

    def test_output_contains_result(self, tmp_path, capsys):
        _run_main(tmp_path)
        out = capsys.readouterr().out
        assert "RESULT:" in out

    def test_output_contains_blockers_section(self, tmp_path, capsys):
        _run_main(tmp_path)
        out = capsys.readouterr().out
        assert "Blockers:" in out

    def test_output_contains_suggested_actions(self, tmp_path, capsys):
        _run_main(tmp_path)
        out = capsys.readouterr().out
        assert "Suggested actions:" in out

    def test_no_credentials_read(self, tmp_path):
        """Tool never touches Alpaca creds — no env vars needed."""
        import os
        from src.tools.live_shadow_review import main
        report_path    = tmp_path / "r.json"
        candidates_path = tmp_path / "c.csv"
        _write_report(report_path)
        _write_candidates(candidates_path)
        with patch.dict(os.environ, {}, clear=True):
            try:
                main(["--report", str(report_path),
                      "--candidates", str(candidates_path)])
            except SystemExit:
                pass
        # If we get here without credential error, the test passes

    def test_no_network_calls(self, tmp_path):
        """No Alpaca client is ever created."""
        from src.tools.live_shadow_review import main
        report_path    = tmp_path / "r.json"
        candidates_path = tmp_path / "c.csv"
        _write_report(report_path)
        _write_candidates(candidates_path)
        with patch("alpaca.trading.client.TradingClient") as mock_tc:
            try:
                main(["--report", str(report_path),
                      "--candidates", str(candidates_path)])
            except SystemExit:
                pass
        mock_tc.assert_not_called()

    def test_notional_blocker_summarized_in_output(self, tmp_path, capsys):
        checks = [
            {"label": "live_sizing", "status": "FAIL",
             "detail": "estimated_notional=600 > live_max_notional=500"},
            {"label": "candidates", "status": "PASS", "detail": ""},
        ]
        _run_main(tmp_path, report_kwargs={"final_status": "FAIL", "checks": checks},
                  candidate_rows=[{"sizing_status": "FAIL",
                                   "sizing_reason": "notional exceeded"}])
        out = capsys.readouterr().out
        assert "notional" in out.lower()

    def test_zero_buying_power_warning_in_output(self, tmp_path, capsys):
        checks = [{"label": "live_account", "status": "WARN", "detail": "buying_power=0"},
                  {"label": "live_sizing",   "status": "PASS", "detail": ""},
                  {"label": "candidates",    "status": "PASS", "detail": ""},
                  {"label": "credentials",   "status": "PASS", "detail": ""},
                  {"label": "live_position", "status": "PASS", "detail": ""},
                  {"label": "live_orders",   "status": "PASS", "detail": ""}]
        _run_main(tmp_path, report_kwargs={"final_status": "WARN",
                                           "buying_power": "0",
                                           "checks": checks})
        out = capsys.readouterr().out
        assert "buying_power" in out or "fund" in out.lower()
