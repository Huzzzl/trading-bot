"""
tests/test_live_readiness_history_review.py
---------------------------------------------
Tests for src/tools/live_readiness_history_review.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no file writes by the tool.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIELDNAMES = [
    "checked_at_utc",
    "decision",
    "account_check",
    "shadow_preflight",
    "shadow_review",
    "symbol_screen",
    "symbol_screen_review",
    "top_blockers",
]


def _write_history(path: Path, rows: list[dict]) -> None:
    """Write a history CSV with standard header."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            full = {k: "" for k in _FIELDNAMES}
            full.update(row)
            writer.writerow(full)


def _row(
    decision: str = "NO-GO",
    checked_at_utc: str = "2026-01-01T00:00:00+00:00",
    account_check: str = "PASS",
    shadow_preflight: str = "PASS",
    shadow_review: str = "PASS",
    symbol_screen: str = "PASS",
    symbol_screen_review: str = "PASS",
    top_blockers: str = "",
) -> dict:
    return {
        "checked_at_utc":      checked_at_utc,
        "decision":            decision,
        "account_check":       account_check,
        "shadow_preflight":    shadow_preflight,
        "shadow_review":       shadow_review,
        "symbol_screen":       symbol_screen,
        "symbol_screen_review": symbol_screen_review,
        "top_blockers":        top_blockers,
    }


def _run_main(argv: list[str]) -> int:
    from src.tools.live_readiness_history_review import main
    try:
        main(argv)
        return 0
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


# ---------------------------------------------------------------------------
# 1. parse_history
# ---------------------------------------------------------------------------

class TestParseHistory:
    def test_missing_file_raises_file_not_found(self, tmp_path):
        from src.tools.live_readiness_history_review import parse_history
        with pytest.raises(FileNotFoundError):
            parse_history(tmp_path / "missing.csv")

    def test_empty_csv_raises_value_error(self, tmp_path):
        from src.tools.live_readiness_history_review import parse_history
        hist = tmp_path / "history.csv"
        hist.write_text("checked_at_utc,decision\n", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            parse_history(hist)

    def test_returns_rows_as_dicts(self, tmp_path):
        from src.tools.live_readiness_history_review import parse_history
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("GO"), _row("NO-GO")])
        rows = parse_history(hist)
        assert len(rows) == 2
        assert rows[0]["decision"] == "GO"
        assert rows[1]["decision"] == "NO-GO"

    def test_utf8_top_blockers_with_em_dash(self, tmp_path):
        """UTF-8 em-dash in top_blockers must not cause encoding errors."""
        from src.tools.live_readiness_history_review import parse_history
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row(top_blockers="[account] status—active")])
        rows = parse_history(hist)
        assert "—" in rows[0]["top_blockers"]


# ---------------------------------------------------------------------------
# 2. count_decisions
# ---------------------------------------------------------------------------

class TestCountDecisions:
    def test_all_go(self):
        from src.tools.live_readiness_history_review import count_decisions
        rows = [_row("GO"), _row("GO"), _row("GO")]
        assert count_decisions(rows) == (3, 0)

    def test_all_no_go(self):
        from src.tools.live_readiness_history_review import count_decisions
        rows = [_row("NO-GO"), _row("NO-GO")]
        assert count_decisions(rows) == (0, 2)

    def test_mixed(self):
        from src.tools.live_readiness_history_review import count_decisions
        rows = [_row("GO"), _row("NO-GO"), _row("GO")]
        assert count_decisions(rows) == (2, 1)

    def test_single_go(self):
        from src.tools.live_readiness_history_review import count_decisions
        assert count_decisions([_row("GO")]) == (1, 0)

    def test_single_no_go(self):
        from src.tools.live_readiness_history_review import count_decisions
        assert count_decisions([_row("NO-GO")]) == (0, 1)


# ---------------------------------------------------------------------------
# 3. top_recurring_blockers
# ---------------------------------------------------------------------------

class TestTopRecurringBlockers:
    def test_counts_repeated_phrases(self):
        from src.tools.live_readiness_history_review import top_recurring_blockers
        rows = [
            _row(top_blockers="[account_check] buying_power=0"),
            _row(top_blockers="[account_check] buying_power=0 | [shadow_review] sizing"),
            _row(top_blockers="[account_check] buying_power=0"),
        ]
        result = dict(top_recurring_blockers(rows))
        assert result["[account_check] buying_power=0"] == 3
        assert result["[shadow_review] sizing"] == 1

    def test_empty_top_blockers_ignored(self):
        from src.tools.live_readiness_history_review import top_recurring_blockers
        rows = [_row(top_blockers=""), _row(top_blockers="  ")]
        assert top_recurring_blockers(rows) == []

    def test_capped_at_top_n(self):
        from src.tools.live_readiness_history_review import top_recurring_blockers
        rows = [_row(top_blockers=f"blocker_{i}") for i in range(10)]
        result = top_recurring_blockers(rows, top_n=3)
        assert len(result) <= 3

    def test_sorted_by_count_descending(self):
        from src.tools.live_readiness_history_review import top_recurring_blockers
        rows = [
            _row(top_blockers="rare"),
            _row(top_blockers="common | common | common"),
        ]
        result = top_recurring_blockers(rows)
        phrases = [p for p, _ in result]
        assert phrases.index("common") < phrases.index("rare")

    def test_utf8_em_dash_phrase(self):
        from src.tools.live_readiness_history_review import top_recurring_blockers
        phrase = "[account] status—active"
        rows = [_row(top_blockers=phrase), _row(top_blockers=phrase)]
        result = dict(top_recurring_blockers(rows))
        assert result[phrase] == 2


# ---------------------------------------------------------------------------
# 4. trend_summary
# ---------------------------------------------------------------------------

class TestTrendSummary:
    def test_all_no_go_returns_no_go_yet(self):
        from src.tools.live_readiness_history_review import trend_summary
        rows = [_row("NO-GO"), _row("NO-GO")]
        assert trend_summary(rows) == "No GO observed yet."

    def test_latest_go_returns_latest_go(self):
        from src.tools.live_readiness_history_review import trend_summary
        rows = [_row("NO-GO"), _row("GO")]
        assert trend_summary(rows) == "Latest run is GO."

    def test_regression_returns_regressed(self):
        from src.tools.live_readiness_history_review import trend_summary
        rows = [_row("GO"), _row("NO-GO")]
        assert trend_summary(rows) == "Readiness regressed from GO to NO-GO."

    def test_single_go_run(self):
        from src.tools.live_readiness_history_review import trend_summary
        assert trend_summary([_row("GO")]) == "Latest run is GO."

    def test_single_no_go_run(self):
        from src.tools.live_readiness_history_review import trend_summary
        assert trend_summary([_row("NO-GO")]) == "No GO observed yet."

    def test_prior_go_then_no_go(self):
        from src.tools.live_readiness_history_review import trend_summary
        rows = [_row("NO-GO"), _row("GO"), _row("GO"), _row("NO-GO")]
        assert trend_summary(rows) == "Readiness regressed from GO to NO-GO."


# ---------------------------------------------------------------------------
# 5. build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_keys_present(self, tmp_path):
        from src.tools.live_readiness_history_review import build_summary
        rows = [_row("GO", checked_at_utc="2026-01-01T00:00:00+00:00")]
        summary = build_summary(rows)
        for key in ("total_runs", "latest_checked_at_utc", "latest_decision",
                    "go_count", "no_go_count", "latest_stage_statuses",
                    "recurring_blockers", "trend_summary"):
            assert key in summary, f"missing key: {key}"

    def test_total_runs_correct(self):
        from src.tools.live_readiness_history_review import build_summary
        rows = [_row(), _row(), _row()]
        assert build_summary(rows)["total_runs"] == 3

    def test_latest_row_is_last(self):
        from src.tools.live_readiness_history_review import build_summary
        rows = [
            _row("NO-GO", checked_at_utc="2026-01-01T00:00:00+00:00"),
            _row("GO",    checked_at_utc="2026-01-02T00:00:00+00:00"),
        ]
        s = build_summary(rows)
        assert s["latest_decision"] == "GO"
        assert s["latest_checked_at_utc"] == "2026-01-02T00:00:00+00:00"

    def test_stage_statuses_from_latest_row(self):
        from src.tools.live_readiness_history_review import build_summary
        rows = [
            _row(account_check="FAIL"),
            _row(account_check="PASS"),
        ]
        s = build_summary(rows)
        assert s["latest_stage_statuses"]["account_check"] == "PASS"


# ---------------------------------------------------------------------------
# 6. CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_missing_file_exits_1(self, tmp_path):
        code = _run_main(["--history", str(tmp_path / "missing.csv")])
        assert code == 1

    def test_malformed_empty_csv_exits_1(self, tmp_path):
        hist = tmp_path / "history.csv"
        hist.write_text("checked_at_utc,decision\n", encoding="utf-8")
        code = _run_main(["--history", str(hist)])
        assert code == 1

    def test_single_no_go_exits_1(self, tmp_path):
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("NO-GO")])
        assert _run_main(["--history", str(hist)]) == 1

    def test_single_go_exits_0(self, tmp_path):
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("GO")])
        assert _run_main(["--history", str(hist)]) in (0, None)

    def test_multiple_rows_go_count(self, tmp_path):
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("GO"), _row("NO-GO"), _row("GO")])
        from src.tools.live_readiness_history_review import parse_history, build_summary
        rows = parse_history(hist)
        s = build_summary(rows)
        assert s["go_count"] == 2
        assert s["no_go_count"] == 1

    def test_latest_row_by_file_order(self, tmp_path):
        hist = tmp_path / "history.csv"
        _write_history(hist, [
            _row("NO-GO", checked_at_utc="2026-01-01T00:00:00+00:00"),
            _row("GO",    checked_at_utc="2026-01-02T00:00:00+00:00"),
        ])
        from src.tools.live_readiness_history_review import parse_history, build_summary
        s = build_summary(parse_history(hist))
        assert s["latest_decision"] == "GO"
        assert s["latest_checked_at_utc"] == "2026-01-02T00:00:00+00:00"

    def test_recurring_blockers_counted(self, tmp_path):
        hist = tmp_path / "history.csv"
        _write_history(hist, [
            _row(top_blockers="[account_check] buying_power=0"),
            _row(top_blockers="[account_check] buying_power=0 | [shadow_review] sizing"),
            _row(top_blockers="[account_check] buying_power=0"),
        ])
        from src.tools.live_readiness_history_review import parse_history, build_summary
        s = build_summary(parse_history(hist))
        blocker_map = dict(s["recurring_blockers"])
        assert blocker_map.get("[account_check] buying_power=0") == 3

    def test_utf8_em_dash_reads_correctly(self, tmp_path):
        """Top blockers containing em-dash must not fail on Windows-like locales."""
        hist = tmp_path / "history.csv"
        phrase = "[account_check] status—active"
        _write_history(hist, [_row(top_blockers=phrase)])
        from src.tools.live_readiness_history_review import parse_history
        rows = parse_history(hist)
        assert "—" in rows[0]["top_blockers"]

    def test_no_credentials_read(self, tmp_path, monkeypatch):
        """Tool must not access ALPACA_* environment variables."""
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("GO")])
        code = _run_main(["--history", str(hist)])
        assert code in (0, None)

    def test_no_files_written(self, tmp_path):
        """Tool must not write any files."""
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("GO")])
        before = {f.name for f in tmp_path.iterdir()}
        _run_main(["--history", str(hist)])
        after = {f.name for f in tmp_path.iterdir()}
        assert after == before

    def test_output_contains_expected_fields(self, tmp_path, capsys):
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("GO")])
        _run_main(["--history", str(hist)])
        out = capsys.readouterr().out
        for field in ("total_runs", "latest_decision", "go_count",
                      "no_go_count", "trend_summary"):
            assert field in out, f"missing field in output: {field}"

    def test_latest_no_go_after_go_exits_1(self, tmp_path):
        hist = tmp_path / "history.csv"
        _write_history(hist, [_row("GO"), _row("NO-GO")])
        assert _run_main(["--history", str(hist)]) == 1
