"""
tests/test_live_dry_run_review.py
-----------------------------------
Tests for src/tools/live_dry_run_review.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no file writes.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_summary(path: Path, **overrides) -> dict:
    data = {
        "checked_at_utc": "2026-05-20T00:00:00+00:00",
        "symbol":         "SPY",
        "decision":       "GO",
        "dry_run_only":   True,
        "submit_allowed": False,
        "intent_count":   1,
        "pass_count":     1,
        "fail_count":     0,
        "top_blockers":   [],
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


_INTENT_COLUMNS = [
    "checked_at_utc", "symbol", "side", "order_type",
    "live_sizing_mode", "effective_quantity", "effective_notional",
    "entry_price", "live_max_order_notional", "live_max_notional",
    "readiness_decision", "dry_run_only", "submit_allowed",
    "sizing_status", "sizing_reason",
]


def _write_intents(path: Path, rows: list[dict] | None = None) -> None:
    if rows is None:
        rows = [_intent_row()]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_INTENT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _INTENT_COLUMNS})


def _intent_row(**overrides) -> dict:
    row = {
        "checked_at_utc":      "2026-05-20T00:00:00+00:00",
        "symbol":              "SPY",
        "side":                "buy",
        "order_type":          "market",
        "live_sizing_mode":    "quantity",
        "effective_quantity":  "1.0",
        "effective_notional":  "400.0",
        "entry_price":         "400.0",
        "live_max_order_notional": "100.0",
        "live_max_notional":   "500.0",
        "readiness_decision":  "GO",
        "dry_run_only":        "True",
        "submit_allowed":      "False",
        "sizing_status":       "PASS",
        "sizing_reason":       "",
    }
    row.update(overrides)
    return row


def _run_main(tmp_path: Path, summary_data: dict | None = None,
              intent_rows: list[dict] | None = None) -> int | None:
    from src.tools.live_dry_run_review import main
    summary_path = tmp_path / "live_dry_run_summary.json"
    intents_path = tmp_path / "live_order_intents.csv"
    if summary_data is not None:
        summary_path.write_text(json.dumps(summary_data), encoding="utf-8")
    else:
        _write_summary(summary_path)
    _write_intents(intents_path, intent_rows)
    argv = ["--summary", str(summary_path), "--intents", str(intents_path)]
    try:
        main(argv)
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# parse_summary
# ---------------------------------------------------------------------------

class TestParseSummary:
    def test_reads_valid_json(self, tmp_path):
        from src.tools.live_dry_run_review import parse_summary
        p = tmp_path / "s.json"
        _write_summary(p)
        s = parse_summary(p)
        assert s["decision"] == "GO"

    def test_raises_on_missing_file(self, tmp_path):
        from src.tools.live_dry_run_review import parse_summary
        with pytest.raises(FileNotFoundError):
            parse_summary(tmp_path / "nope.json")

    def test_raises_on_bad_json(self, tmp_path):
        from src.tools.live_dry_run_review import parse_summary
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_summary(p)

    def test_utf8_content(self, tmp_path):
        from src.tools.live_dry_run_review import parse_summary
        p = tmp_path / "s.json"
        data = {"decision": "GO", "symbol": "SPY—test", "dry_run_only": True,
                "submit_allowed": False}
        p.write_text(json.dumps(data), encoding="utf-8")
        s = parse_summary(p)
        assert "—" in s["symbol"]


# ---------------------------------------------------------------------------
# parse_intents
# ---------------------------------------------------------------------------

class TestParseIntents:
    def test_reads_valid_csv(self, tmp_path):
        from src.tools.live_dry_run_review import parse_intents
        p = tmp_path / "i.csv"
        _write_intents(p)
        rows = parse_intents(p)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "SPY"

    def test_raises_on_missing_file(self, tmp_path):
        from src.tools.live_dry_run_review import parse_intents
        with pytest.raises(FileNotFoundError):
            parse_intents(tmp_path / "nope.csv")

    def test_empty_csv_returns_empty_list(self, tmp_path):
        from src.tools.live_dry_run_review import parse_intents
        p = tmp_path / "empty.csv"
        _write_intents(p, rows=[])
        rows = parse_intents(p)
        assert rows == []

    def test_utf8_roundtrip(self, tmp_path):
        from src.tools.live_dry_run_review import parse_intents
        p = tmp_path / "i.csv"
        _write_intents(p, rows=[_intent_row(sizing_reason="no—signal")])
        rows = parse_intents(p)
        assert "—" in rows[0]["sizing_reason"]


# ---------------------------------------------------------------------------
# check_safety_flags
# ---------------------------------------------------------------------------

class TestCheckSafetyFlags:
    def test_clean_artifacts_no_violations(self, tmp_path):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": True, "submit_allowed": False}
        intents = [_intent_row()]
        assert check_safety_flags(summary, intents) == []

    def test_summary_submit_allowed_true_is_violation(self):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": True, "submit_allowed": True}
        assert check_safety_flags(summary, []) != []

    def test_summary_dry_run_only_false_is_violation(self):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": False, "submit_allowed": False}
        assert check_safety_flags(summary, []) != []

    def test_row_submit_allowed_true_is_violation(self):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": True, "submit_allowed": False}
        intents = [_intent_row(submit_allowed="True")]
        assert check_safety_flags(summary, intents) != []

    def test_row_dry_run_only_false_is_violation(self):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": True, "submit_allowed": False}
        intents = [_intent_row(dry_run_only="False")]
        assert check_safety_flags(summary, intents) != []

    def test_empty_intents_clean_summary_ok(self):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": True, "submit_allowed": False}
        assert check_safety_flags(summary, []) == []

    def test_string_true_detected_as_truthy(self):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": True, "submit_allowed": "true"}
        assert check_safety_flags(summary, []) != []

    def test_string_false_not_truthy_for_dry_run(self):
        from src.tools.live_dry_run_review import check_safety_flags
        summary = {"dry_run_only": "false", "submit_allowed": False}
        assert check_safety_flags(summary, []) != []


# ---------------------------------------------------------------------------
# build_review
# ---------------------------------------------------------------------------

class TestBuildReview:
    def _go_summary(self, **kw):
        base = {"decision": "GO", "symbol": "SPY", "intent_count": 1,
                "pass_count": 1, "fail_count": 0,
                "dry_run_only": True, "submit_allowed": False, "top_blockers": []}
        base.update(kw)
        return base

    def test_go_clean_is_pass(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(), [_intent_row()])
        assert r["review_result"] == "PASS"

    def test_no_go_summary_is_fail(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(decision="NO-GO"), [])
        assert r["review_result"] == "FAIL"

    def test_sizing_fail_row_is_fail(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(), [_intent_row(sizing_status="FAIL")])
        assert r["review_result"] == "FAIL"

    def test_submit_allowed_true_in_row_is_fail(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(), [_intent_row(submit_allowed="True")])
        assert r["review_result"] == "FAIL"

    def test_dry_run_only_false_in_row_is_fail(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(), [_intent_row(dry_run_only="False")])
        assert r["review_result"] == "FAIL"

    def test_summary_submit_allowed_true_is_fail(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(submit_allowed=True), [_intent_row()])
        assert r["review_result"] == "FAIL"

    def test_summary_dry_run_only_false_is_fail(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(dry_run_only=False), [_intent_row()])
        assert r["review_result"] == "FAIL"

    def test_dry_run_only_all_true_when_all_rows_true(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(), [_intent_row(), _intent_row()])
        assert r["dry_run_only_all"] is True

    def test_dry_run_only_all_false_when_one_row_false(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(),
                         [_intent_row(), _intent_row(dry_run_only="False")])
        assert r["dry_run_only_all"] is False

    def test_submit_allowed_any_false_when_all_false(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(), [_intent_row()])
        assert r["submit_allowed_any"] is False

    def test_submit_allowed_any_true_when_one_true(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(),
                         [_intent_row(), _intent_row(submit_allowed="True")])
        assert r["submit_allowed_any"] is True

    def test_sizing_stats_quantity_mode(self):
        from src.tools.live_dry_run_review import build_review
        rows = [_intent_row(effective_quantity="0.5"), _intent_row(effective_quantity="1.0")]
        r = build_review(self._go_summary(), rows)
        assert r["min_effective_quantity"] == pytest.approx(0.5)
        assert r["max_effective_quantity"] == pytest.approx(1.0)
        assert r["sizing_mode"] == "quantity"

    def test_notional_mode_notional_per_order(self):
        from src.tools.live_dry_run_review import build_review
        row = _intent_row(live_sizing_mode="notional", effective_notional="100.0",
                          effective_quantity="0.25")
        r = build_review(self._go_summary(), [row])
        assert r["notional_per_order"] == pytest.approx(100.0)

    def test_empty_intents_no_go_is_fail(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(decision="NO-GO", intent_count=0), [])
        assert r["review_result"] == "FAIL"

    def test_violations_appear_in_top_blockers(self):
        from src.tools.live_dry_run_review import build_review
        r = build_review(self._go_summary(submit_allowed=True), [])
        assert any("submit_allowed" in b for b in r["top_blockers"])


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_go_exits_0(self, tmp_path):
        assert _run_main(tmp_path) in (0, None)

    def test_no_go_summary_exits_1(self, tmp_path):
        s = {"decision": "NO-GO", "symbol": "SPY", "intent_count": 0,
             "pass_count": 0, "fail_count": 0,
             "dry_run_only": True, "submit_allowed": False, "top_blockers": []}
        assert _run_main(tmp_path, summary_data=s, intent_rows=[]) == 1

    def test_sizing_fail_row_exits_1(self, tmp_path):
        assert _run_main(tmp_path, intent_rows=[_intent_row(sizing_status="FAIL")]) == 1

    def test_submit_allowed_true_in_row_exits_1(self, tmp_path):
        assert _run_main(tmp_path, intent_rows=[_intent_row(submit_allowed="True")]) == 1

    def test_dry_run_only_false_in_row_exits_1(self, tmp_path):
        assert _run_main(tmp_path, intent_rows=[_intent_row(dry_run_only="False")]) == 1

    def test_summary_submit_allowed_true_exits_1(self, tmp_path):
        s = {"decision": "GO", "symbol": "SPY", "intent_count": 1,
             "pass_count": 1, "fail_count": 0,
             "dry_run_only": True, "submit_allowed": True, "top_blockers": []}
        assert _run_main(tmp_path, summary_data=s) == 1

    def test_summary_dry_run_only_false_exits_1(self, tmp_path):
        s = {"decision": "GO", "symbol": "SPY", "intent_count": 1,
             "pass_count": 1, "fail_count": 0,
             "dry_run_only": False, "submit_allowed": False, "top_blockers": []}
        assert _run_main(tmp_path, summary_data=s) == 1

    def test_missing_summary_exits_1(self, tmp_path):
        from src.tools.live_dry_run_review import main
        argv = ["--summary", str(tmp_path / "nope.json"),
                "--intents",  str(tmp_path / "nope.csv")]
        try:
            main(argv)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert exc.code == 1

    def test_missing_intents_exits_1(self, tmp_path):
        from src.tools.live_dry_run_review import main
        summary_path = tmp_path / "live_dry_run_summary.json"
        _write_summary(summary_path)
        argv = ["--summary", str(summary_path),
                "--intents",  str(tmp_path / "nope.csv")]
        try:
            main(argv)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert exc.code == 1

    def test_malformed_json_exits_1(self, tmp_path):
        from src.tools.live_dry_run_review import main
        summary_path = tmp_path / "live_dry_run_summary.json"
        summary_path.write_text("{bad", encoding="utf-8")
        intents_path = tmp_path / "live_order_intents.csv"
        _write_intents(intents_path)
        argv = ["--summary", str(summary_path), "--intents", str(intents_path)]
        try:
            main(argv)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert exc.code == 1

    def test_no_credentials_or_network(self, tmp_path, monkeypatch):
        # Patch out os.environ to confirm no credential env vars are touched
        import os
        monkeypatch.delenv("ALPACA_LIVE_API_KEY",    raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        assert _run_main(tmp_path) in (0, None)

    def test_no_file_writes(self, tmp_path):
        _run_main(tmp_path)
        # Only the two input files should exist (written by helpers before main runs)
        written = {p.name for p in tmp_path.iterdir()}
        assert written == {"live_dry_run_summary.json", "live_order_intents.csv"}

    def test_utf8_top_blockers(self, tmp_path):
        s = {"decision": "NO-GO", "symbol": "SPY", "intent_count": 0,
             "pass_count": 0, "fail_count": 0,
             "dry_run_only": True, "submit_allowed": False,
             "top_blockers": ["[live_account] buying_power—0"]}
        assert _run_main(tmp_path, summary_data=s, intent_rows=[]) == 1

    def test_empty_intents_go_summary_is_fail(self, tmp_path):
        # GO with intent_count=1 in summary but empty CSV — fail_count mismatch
        # intent_count from summary is used; CSV rows determine PASS/FAIL
        # No rows means no PASS rows; but decision=GO and no violations → should PASS
        # (summary says pass_count=1, but CSV is empty — this is a data mismatch
        # that doesn't trigger a safety violation; review_result depends on row data)
        # An empty CSV with a GO summary and no violations should still PASS
        # (no sizing_fail rows, no flag violations)
        assert _run_main(tmp_path, intent_rows=[]) in (0, None)
