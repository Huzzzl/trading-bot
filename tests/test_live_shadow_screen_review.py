"""
tests/test_live_shadow_screen_review.py
-----------------------------------------
Tests for src/tools/live_shadow_screen_review.py.

Fully offline: no Alpaca calls, no credentials, no files written.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _report(
    overall_result: str = "PASS",
    symbols: list[str] | None = None,
    buying_power: str = "10000.00",
    portfolio_value: str = "50000.00",
    trading_blocked: bool = False,
    account_blocked: bool = False,
    error: str | None = None,
    symbol_results: list[dict] | None = None,
) -> dict:
    return {
        "checked_at_utc": "2026-05-19T12:00:00+00:00",
        "symbols":         symbols or ["SPY", "QQQ"],
        "overall_result":  overall_result,
        "account": {
            "status":           "active",
            "trading_blocked":  trading_blocked,
            "account_blocked":  account_blocked,
            "buying_power":     buying_power,
            "portfolio_value":  portfolio_value,
            "error":            error,
        },
        "symbol_results": symbol_results or [],
    }


def _csv_rows(rows: list[dict]) -> list[dict[str, str]]:
    return [
        {
            "symbol":                  r.get("symbol", "SPY"),
            "candidate_count":         str(r.get("candidate_count", 1)),
            "best_status":             r.get("best_status", "PASS"),
            "min_estimated_notional":  str(r.get("min_estimated_notional", "400.00")),
            "max_estimated_notional":  str(r.get("max_estimated_notional", "400.00")),
            "pass_count":              str(r.get("pass_count", 1)),
            "fail_count":              str(r.get("fail_count", 0)),
            "position_for_symbol":     str(r.get("position_for_symbol", False)),
            "open_orders_for_symbol":  str(r.get("open_orders_for_symbol", False)),
            "blocker_summary":         r.get("blocker_summary", ""),
        }
        for r in rows
    ]


def _write_fixtures(tmp_path: Path, report: dict, rows: list[dict]) -> tuple[Path, Path]:
    report_path = tmp_path / "live_shadow_symbol_screen_report.json"
    csv_path    = tmp_path / "live_shadow_symbol_screen.csv"

    report_path.write_text(json.dumps(report), encoding="utf-8")

    fieldnames = [
        "symbol", "candidate_count", "best_status",
        "min_estimated_notional", "max_estimated_notional",
        "pass_count", "fail_count",
        "position_for_symbol", "open_orders_for_symbol", "blocker_summary",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in _csv_rows(rows):
            writer.writerow(row)

    return report_path, csv_path


def _run_main(tmp_path: Path, report: dict, rows: list[dict]) -> int:
    from src.tools.live_shadow_screen_review import main
    report_path, csv_path = _write_fixtures(tmp_path, report, rows)
    try:
        main(["--report", str(report_path), "--csv", str(csv_path)])
        return 0
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# 1. parse_report
# ---------------------------------------------------------------------------

class TestParseReport:
    def test_reads_valid_json(self, tmp_path):
        from src.tools.live_shadow_screen_review import parse_report
        p = tmp_path / "r.json"
        p.write_text(json.dumps({"overall_result": "PASS"}), encoding="utf-8")
        assert parse_report(p)["overall_result"] == "PASS"

    def test_missing_file_raises(self, tmp_path):
        from src.tools.live_shadow_screen_review import parse_report
        with pytest.raises(FileNotFoundError):
            parse_report(tmp_path / "missing.json")

    def test_malformed_json_raises(self, tmp_path):
        from src.tools.live_shadow_screen_review import parse_report
        p = tmp_path / "bad.json"
        p.write_text("{bad json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_report(p)


# ---------------------------------------------------------------------------
# 2. parse_csv
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_reads_valid_csv(self, tmp_path):
        from src.tools.live_shadow_screen_review import parse_csv
        p = tmp_path / "s.csv"
        p.write_text("symbol,best_status\nSPY,PASS\n", encoding="utf-8")
        rows = parse_csv(p)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "SPY"

    def test_missing_file_raises(self, tmp_path):
        from src.tools.live_shadow_screen_review import parse_csv
        with pytest.raises(FileNotFoundError):
            parse_csv(tmp_path / "missing.csv")

    def test_empty_csv_returns_empty_list(self, tmp_path):
        from src.tools.live_shadow_screen_review import parse_csv
        p = tmp_path / "empty.csv"
        p.write_text("symbol,best_status\n", encoding="utf-8")
        assert parse_csv(p) == []


# ---------------------------------------------------------------------------
# 3. classify_rows
# ---------------------------------------------------------------------------

class TestClassifyRows:
    def test_pass_row_is_suitable(self):
        from src.tools.live_shadow_screen_review import classify_rows
        rows = _csv_rows([{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}])
        c = classify_rows(rows)
        assert "SPY" in c["suitable"]

    def test_no_candidate_blocker(self):
        from src.tools.live_shadow_screen_review import classify_rows
        rows = _csv_rows([{
            "symbol": "IWM", "best_status": "FAIL",
            "blocker_summary": "no candidates",
        }])
        c = classify_rows(rows)
        assert "IWM" in c["no_candidate"]

    def test_notional_blocker_is_sizing_blocked(self):
        from src.tools.live_shadow_screen_review import classify_rows
        rows = _csv_rows([{
            "symbol": "SPY", "best_status": "FAIL",
            "blocker_summary": "all 1 candidate(s) fail sizing — estimated_notional exceeds live_max_notional",
        }])
        c = classify_rows(rows)
        assert "SPY" in c["sizing_blocked"]

    def test_fail_sizing_blocker_is_sizing_blocked(self):
        from src.tools.live_shadow_screen_review import classify_rows
        rows = _csv_rows([{
            "symbol": "QQQ", "best_status": "FAIL",
            "blocker_summary": "all 2 candidate(s) fail sizing — qty too large",
        }])
        c = classify_rows(rows)
        assert "QQQ" in c["sizing_blocked"]

    def test_position_blocker(self):
        from src.tools.live_shadow_screen_review import classify_rows
        rows = _csv_rows([{
            "symbol": "DIA", "best_status": "FAIL",
            "blocker_summary": "live position exists for DIA",
        }])
        c = classify_rows(rows)
        assert "DIA" in c["position_blocked"]

    def test_order_blocker(self):
        from src.tools.live_shadow_screen_review import classify_rows
        rows = _csv_rows([{
            "symbol": "TLT", "best_status": "FAIL",
            "blocker_summary": "live open order exists for TLT",
        }])
        c = classify_rows(rows)
        assert "TLT" in c["order_blocked"]

    def test_multiple_buckets(self):
        from src.tools.live_shadow_screen_review import classify_rows
        rows = _csv_rows([
            {"symbol": "SPY", "best_status": "PASS",  "blocker_summary": ""},
            {"symbol": "QQQ", "best_status": "FAIL",  "blocker_summary": "no candidates"},
            {"symbol": "IWM", "best_status": "FAIL",  "blocker_summary": "all 1 fail sizing — notional too large"},
        ])
        c = classify_rows(rows)
        assert c["suitable"]       == ["SPY"]
        assert c["no_candidate"]   == ["QQQ"]
        assert c["sizing_blocked"] == ["IWM"]


# ---------------------------------------------------------------------------
# 4. find_account_warnings
# ---------------------------------------------------------------------------

class TestFindAccountWarnings:
    def test_zero_buying_power(self):
        from src.tools.live_shadow_screen_review import find_account_warnings
        rep = _report(buying_power="0")
        warnings = find_account_warnings(rep)
        assert any("buying_power" in w for w in warnings)

    def test_zero_portfolio_value(self):
        from src.tools.live_shadow_screen_review import find_account_warnings
        rep = _report(portfolio_value="0")
        warnings = find_account_warnings(rep)
        assert any("portfolio_value" in w for w in warnings)

    def test_trading_blocked(self):
        from src.tools.live_shadow_screen_review import find_account_warnings
        rep = _report(trading_blocked=True)
        warnings = find_account_warnings(rep)
        assert any("trading_blocked" in w for w in warnings)

    def test_account_error(self):
        from src.tools.live_shadow_screen_review import find_account_warnings
        rep = _report(error="network timeout")
        warnings = find_account_warnings(rep)
        assert any("network timeout" in w for w in warnings)

    def test_clean_account_no_warnings(self):
        from src.tools.live_shadow_screen_review import find_account_warnings
        assert find_account_warnings(_report()) == []


# ---------------------------------------------------------------------------
# 5. suggest_actions
# ---------------------------------------------------------------------------

class TestSuggestActions:
    def _classification(self, **kw):
        base = {
            "suitable": [], "no_candidate": [], "sizing_blocked": [],
            "position_blocked": [], "order_blocked": [], "other_blocked": [],
        }
        base.update(kw)
        return base

    def test_no_suitable_action(self):
        from src.tools.live_shadow_screen_review import suggest_actions
        actions = suggest_actions(self._classification(), [], "FAIL")
        assert any("No symbols currently suitable" in a for a in actions)

    def test_sizing_blocked_action(self):
        from src.tools.live_shadow_screen_review import suggest_actions
        c = self._classification(sizing_blocked=["SPY"])
        actions = suggest_actions(c, [], "FAIL")
        assert any("live_max_notional" in a for a in actions)

    def test_no_candidate_action(self):
        from src.tools.live_shadow_screen_review import suggest_actions
        c = self._classification(no_candidate=["IWM"])
        actions = suggest_actions(c, [], "FAIL")
        assert any("signal day" in a for a in actions)

    def test_zero_funds_action(self):
        from src.tools.live_shadow_screen_review import suggest_actions
        c = self._classification(suitable=["SPY"])
        actions = suggest_actions(c, ["buying_power is 0"], "PASS")
        assert any("Fund/activate" in a for a in actions)

    def test_position_blocked_action(self):
        from src.tools.live_shadow_screen_review import suggest_actions
        c = self._classification(position_blocked=["DIA"])
        actions = suggest_actions(c, [], "FAIL")
        assert any("DIA" in a and "position" in a for a in actions)

    def test_order_blocked_action(self):
        from src.tools.live_shadow_screen_review import suggest_actions
        c = self._classification(order_blocked=["TLT"])
        actions = suggest_actions(c, [], "FAIL")
        assert any("TLT" in a and "order" in a for a in actions)

    def test_all_pass_action(self):
        from src.tools.live_shadow_screen_review import suggest_actions
        c = self._classification(suitable=["SPY", "QQQ"])
        actions = suggest_actions(c, [], "PASS")
        assert any("passed" in a or "suitable" in a.lower() for a in actions)


# ---------------------------------------------------------------------------
# 6. build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_result_pass_when_suitable_and_overall_pass(self):
        from src.tools.live_shadow_screen_review import build_summary
        rep  = _report(overall_result="PASS", symbols=["SPY"])
        rows = _csv_rows([{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}])
        s = build_summary(rep, rows)
        assert s["result"] == "PASS"

    def test_result_fail_when_no_suitable(self):
        from src.tools.live_shadow_screen_review import build_summary
        rep  = _report(overall_result="FAIL", symbols=["SPY"])
        rows = _csv_rows([{"symbol": "SPY", "best_status": "FAIL", "blocker_summary": "no candidates"}])
        s = build_summary(rep, rows)
        assert s["result"] == "FAIL"

    def test_result_fail_when_overall_fail_despite_pass_row(self):
        from src.tools.live_shadow_screen_review import build_summary
        rep  = _report(overall_result="FAIL", symbols=["SPY"])
        rows = _csv_rows([{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}])
        s = build_summary(rep, rows)
        assert s["result"] == "FAIL"

    def test_suitable_count_correct(self):
        from src.tools.live_shadow_screen_review import build_summary
        rep  = _report(overall_result="PASS", symbols=["SPY", "QQQ"])
        rows = _csv_rows([
            {"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""},
            {"symbol": "QQQ", "best_status": "FAIL", "blocker_summary": "no candidates"},
        ])
        s = build_summary(rep, rows)
        assert s["suitable_count"] == 1
        assert s["suitable_symbols"] == ["SPY"]

    def test_summary_keys_present(self):
        from src.tools.live_shadow_screen_review import build_summary
        rep  = _report()
        rows = _csv_rows([])
        s = build_summary(rep, rows)
        for key in (
            "overall_result", "result", "symbols_checked", "suitable_count",
            "suitable_symbols", "no_candidate_symbols", "sizing_blocked_symbols",
            "account_warnings", "suggested_actions",
        ):
            assert key in s, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 7. CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_all_pass_exits_0(self, tmp_path):
        rep  = _report(overall_result="PASS", symbols=["SPY"])
        rows = [{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}]
        assert _run_main(tmp_path, rep, rows) in (0, None)

    def test_zero_suitable_exits_1(self, tmp_path):
        rep  = _report(overall_result="FAIL", symbols=["SPY", "QQQ"])
        rows = [
            {"symbol": "SPY", "best_status": "FAIL", "blocker_summary": "no candidates"},
            {"symbol": "QQQ", "best_status": "FAIL", "blocker_summary": "all 1 fail sizing — notional"},
        ]
        assert _run_main(tmp_path, rep, rows) == 1

    def test_overall_warn_exits_1(self, tmp_path):
        rep  = _report(overall_result="WARN", symbols=["SPY"])
        rows = [{"symbol": "SPY", "best_status": "FAIL", "blocker_summary": "no candidates"}]
        assert _run_main(tmp_path, rep, rows) == 1

    def test_suitable_symbols_in_output(self, tmp_path, capsys):
        rep  = _report(overall_result="PASS", symbols=["SPY", "QQQ"])
        rows = [
            {"symbol": "SPY", "best_status": "PASS",  "blocker_summary": ""},
            {"symbol": "QQQ", "best_status": "FAIL",  "blocker_summary": "no candidates"},
        ]
        _run_main(tmp_path, rep, rows)
        out = capsys.readouterr().out
        assert "SPY" in out

    def test_no_candidate_symbols_in_output(self, tmp_path, capsys):
        rep  = _report(overall_result="FAIL", symbols=["IWM"])
        rows = [{"symbol": "IWM", "best_status": "FAIL", "blocker_summary": "no candidates"}]
        _run_main(tmp_path, rep, rows)
        out = capsys.readouterr().out
        assert "IWM" in out
        assert "No-candidate" in out

    def test_sizing_blocked_in_output(self, tmp_path, capsys):
        rep  = _report(overall_result="FAIL", symbols=["SPY"])
        rows = [{"symbol": "SPY", "best_status": "FAIL",
                 "blocker_summary": "all 1 fail sizing — notional exceeds cap"}]
        _run_main(tmp_path, rep, rows)
        out = capsys.readouterr().out
        assert "Sizing-blocked" in out
        assert "SPY" in out

    def test_account_zero_warning_action_shown(self, tmp_path, capsys):
        rep  = _report(overall_result="FAIL", symbols=["SPY"], buying_power="0")
        rows = [{"symbol": "SPY", "best_status": "FAIL", "blocker_summary": "no candidates"}]
        _run_main(tmp_path, rep, rows)
        out = capsys.readouterr().out
        assert "Fund/activate" in out

    def test_missing_report_exits_1(self, tmp_path):
        from src.tools.live_shadow_screen_review import main
        _, csv_path = _write_fixtures(tmp_path, _report(), [])
        with pytest.raises(SystemExit) as exc_info:
            main(["--report", str(tmp_path / "missing.json"), "--csv", str(csv_path)])
        assert exc_info.value.code == 1

    def test_missing_csv_exits_1(self, tmp_path):
        from src.tools.live_shadow_screen_review import main
        report_path, _ = _write_fixtures(tmp_path, _report(), [])
        with pytest.raises(SystemExit) as exc_info:
            main(["--report", str(report_path), "--csv", str(tmp_path / "missing.csv")])
        assert exc_info.value.code == 1

    def test_malformed_json_exits_1(self, tmp_path):
        from src.tools.live_shadow_screen_review import main
        report_path = tmp_path / "bad.json"
        report_path.write_text("{bad", encoding="utf-8")
        _, csv_path = _write_fixtures(tmp_path, _report(), [])
        with pytest.raises(SystemExit) as exc_info:
            main(["--report", str(report_path), "--csv", str(csv_path)])
        assert exc_info.value.code == 1

    def test_no_credentials_read(self, tmp_path):
        """Tool must not touch ALPACA_LIVE_* env vars."""
        import os
        from src.tools.live_shadow_screen_review import main
        rep  = _report(overall_result="PASS", symbols=["SPY"])
        rows = [{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}]
        report_path, csv_path = _write_fixtures(tmp_path, rep, rows)
        env_before = {k: v for k, v in os.environ.items() if "ALPACA" in k}
        try:
            main(["--report", str(report_path), "--csv", str(csv_path)])
        except SystemExit:
            pass
        env_after = {k: v for k, v in os.environ.items() if "ALPACA" in k}
        assert env_before == env_after

    def test_no_file_written(self, tmp_path):
        """Tool must never write files beyond the two input artifacts."""
        rep  = _report(overall_result="PASS", symbols=["SPY"])
        rows = [{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}]
        report_path, csv_path = _write_fixtures(tmp_path, rep, rows)
        before = set(tmp_path.iterdir())
        _run_main(tmp_path, rep, rows)
        after = set(tmp_path.iterdir())
        assert after == before

    def test_output_contains_result_line(self, tmp_path, capsys):
        rep  = _report(overall_result="PASS", symbols=["SPY"])
        rows = [{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}]
        _run_main(tmp_path, rep, rows)
        out = capsys.readouterr().out
        assert "RESULT:" in out

    def test_output_contains_overall_status(self, tmp_path, capsys):
        rep  = _report(overall_result="PASS", symbols=["SPY"])
        rows = [{"symbol": "SPY", "best_status": "PASS", "blocker_summary": ""}]
        _run_main(tmp_path, rep, rows)
        out = capsys.readouterr().out
        assert "overall_status" in out
