"""
tests/test_reporting.py
-----------------------
Unit tests for the ReportGenerator class.

All tests are self-contained: they build minimal Trade / equity-curve fixtures
and call ReportGenerator methods directly — no network, no real data, no broker.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from src.backtest.trade import Trade
from src.config.loader import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    LoggingConfig,
    RiskConfig,
    StrategyConfig,
)
from src.reporting.report_generator import (
    TRADE_LOG_COLUMNS,
    ReportGenerator,
    _date_of,
    _time_of,
    _parse_hhmm,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_config(
    strategy_name: str = "opening_range_breakout",
    params: dict | None = None,
    max_open_positions: int | None = 1,
) -> AppConfig:
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-01",
            end_date="2024-01-31",
            initial_capital=100_000.0,
            commission_per_share=0.005,
            slippage_per_share=0.01,
        ),
        symbols=["SPY", "QQQ"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(
            name=strategy_name,
            params=params or {
                "force_exit_time": "15:55",
                "opening_range_start": "09:30",
                "opening_range_end": "10:00",
                "breakout_trigger": "close",
            },
        ),
        risk=RiskConfig(max_open_positions=max_open_positions),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
    )


def _make_trade(
    symbol: str = "SPY",
    entry_time: str = "2024-01-02 10:05:00-05:00",
    exit_time:  str = "2024-01-02 15:55:00-05:00",
    entry_price: float = 470.0,
    exit_price:  float = 473.0,
    shares: float = 100.0,
    exit_reason: str = "force_exit",
    meta: dict | None = None,
) -> Trade:
    return Trade(
        symbol=symbol,
        entry_time=pd.Timestamp(entry_time),
        exit_time=pd.Timestamp(exit_time),
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
        commission=shares * 0.005 * 2,
        direction="LONG",
        exit_reason=exit_reason,
        meta={"or_high": 471.0, "or_low": 468.0,
              "breakout_trigger": "close", "trigger_val": 471.5} if meta is None else meta,
    )


def _make_equity_curve(rows: int = 5, start: float = 100_000.0) -> pd.DataFrame:
    ts  = pd.date_range("2024-01-02 09:30", periods=rows, freq="5min", tz="America/New_York")
    eq  = [start + i * 100 for i in range(rows)]
    return pd.DataFrame({"equity": eq}, index=ts).rename_axis("timestamp")


def _make_reporter(
    trades: list[Trade] | None = None,
    config: AppConfig | None = None,
    output_dir: Path | None = None,
    open_positions_count: int = 0,
    metrics: dict | None = None,
) -> tuple[ReportGenerator, Path]:
    if output_dir is None:
        tmp = tempfile.mkdtemp()
        output_dir = Path(tmp)
    cfg = config or _make_config()
    eq  = _make_equity_curve()
    trs = trades if trades is not None else [_make_trade()]
    default_metrics = {
        "initial_capital":       100_000.0,
        "final_equity":          100_300.0,
        "num_trades":            len(trs),
        "total_return_pct":      0.30,
        "annualized_return_pct": 3.60,
        "max_drawdown_pct":     -0.10,
        "sharpe_ratio":          1.20,
        "win_rate_pct":          60.0,
        "avg_winning_trade":     350.0,
        "avg_losing_trade":     -80.0,
        "total_commission":      sum(t.commission for t in trs),
    }
    return ReportGenerator(
        metrics=metrics if metrics is not None else default_metrics,
        trades=trs,
        equity_curve=eq,
        config=cfg,
        output_dir=output_dir,
        open_positions_count=open_positions_count,
    ), output_dir


# ===========================================================================
# Helper function tests
# ===========================================================================

class TestHelpers:
    def test_date_of_tz_aware(self):
        ts = pd.Timestamp("2024-01-02 10:00:00-05:00")
        assert _date_of(ts) == date(2024, 1, 2)

    def test_time_of_tz_aware(self):
        ts = pd.Timestamp("2024-01-02 15:55:00-05:00")
        t  = _time_of(ts)
        assert t.hour == 15 and t.minute == 55

    def test_parse_hhmm(self):
        assert _parse_hhmm("15:55") == time(15, 55)
        assert _parse_hhmm("09:30") == time(9, 30)


# ===========================================================================
# metrics.json
# ===========================================================================

class TestMetricsJson:
    def test_file_created(self):
        reporter, out = _make_reporter()
        reporter._write_metrics_json()
        assert (out / "metrics.json").exists()

    def test_json_valid_and_complete(self):
        reporter, out = _make_reporter()
        reporter._write_metrics_json()
        data = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        assert data["num_trades"] == 1
        assert data["initial_capital"] == 100_000.0
        assert "total_return_pct" in data

    def test_zero_trades_still_writes(self):
        reporter, out = _make_reporter(trades=[])
        reporter._write_metrics_json()
        data = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        assert data["num_trades"] == 0


# ===========================================================================
# trade_log.csv
# ===========================================================================

class TestTradeLogCsv:
    def test_file_created(self):
        reporter, out = _make_reporter()
        reporter._write_trade_log_csv()
        assert (out / "trade_log.csv").exists()

    def test_columns_present(self):
        reporter, out = _make_reporter()
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        expected_cols = {
            "symbol", "direction", "exit_time", "entry_price", "exit_price",
            "shares", "pnl", "pnl_pct", "commission", "exit_reason",
            "or_high", "or_low", "breakout_trigger", "trigger_val",
        }
        assert expected_cols.issubset(set(df.columns))

    def test_pnl_pct_computed(self):
        trade = _make_trade(entry_price=470.0, exit_price=473.0, shares=100.0)
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        cost_basis = 470.0 * 100.0
        expected_pnl_pct = trade.pnl / cost_basis * 100.0
        assert abs(df["pnl_pct"].iloc[0] - expected_pnl_pct) < 0.01

    def test_meta_fields_populated(self):
        meta = {"or_high": 471.5, "or_low": 469.0, "breakout_trigger": "close", "trigger_val": 471.6}
        trade = _make_trade(meta=meta)
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        assert abs(df["or_high"].iloc[0] - 471.5) < 0.001
        assert abs(df["or_low"].iloc[0] - 469.0) < 0.001

    def test_missing_meta_fields_blank(self):
        trade = _make_trade(meta={})  # no ORB meta
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        assert str(df["or_high"].iloc[0]) in ("", "nan", "NaN")

    def test_empty_trades(self):
        reporter, out = _make_reporter(trades=[])
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv")
        assert len(df) == 0

    def test_empty_trade_log_has_required_columns(self):
        reporter, out = _make_reporter(trades=[])
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        expected = {c for c in TRADE_LOG_COLUMNS if c != "entry_time"}
        assert expected == set(df.columns)


# ===========================================================================
# daily_summary.csv
# ===========================================================================

class TestDailySummaryCsv:
    def test_file_created(self):
        reporter, out = _make_reporter()
        reporter._write_daily_summary_csv()
        assert (out / "daily_summary.csv").exists()

    def test_columns_present(self):
        reporter, out = _make_reporter()
        reporter._write_daily_summary_csv()
        df = pd.read_csv(out / "daily_summary.csv", index_col="date")
        for col in ["num_trades", "gross_pnl", "avg_pnl", "win_rate_pct",
                    "symbols_traded", "exit_reasons"]:
            assert col in df.columns

    def test_aggregation_single_day(self):
        t1 = _make_trade(symbol="SPY", exit_reason="force_exit")
        t2 = _make_trade(symbol="QQQ", exit_reason="stop_loss",
                         exit_time="2024-01-02 14:00:00-05:00",
                         entry_time="2024-01-02 10:10:00-05:00",
                         entry_price=380.0, exit_price=378.0)
        reporter, out = _make_reporter(trades=[t1, t2])
        reporter._write_daily_summary_csv()
        df = pd.read_csv(out / "daily_summary.csv", index_col="date")
        assert len(df) == 1
        assert df["num_trades"].iloc[0] == 2
        assert "SPY" in df["symbols_traded"].iloc[0]
        assert "QQQ" in df["symbols_traded"].iloc[0]

    def test_two_days_separate_rows(self):
        t1 = _make_trade(exit_time="2024-01-02 15:55:00-05:00")
        t2 = _make_trade(
            entry_time="2024-01-03 10:05:00-05:00",
            exit_time="2024-01-03 15:55:00-05:00",
        )
        reporter, out = _make_reporter(trades=[t1, t2])
        reporter._write_daily_summary_csv()
        df = pd.read_csv(out / "daily_summary.csv", index_col="date")
        assert len(df) == 2
        assert "2024-01-02" in df.index
        assert "2024-01-03" in df.index

    def test_empty_trades(self):
        reporter, out = _make_reporter(trades=[])
        reporter._write_daily_summary_csv()
        df = pd.read_csv(out / "daily_summary.csv")
        assert len(df) == 0


# ===========================================================================
# Validation checks
# ===========================================================================

class TestValidationChecks:
    def test_all_pass_for_clean_trade(self):
        reporter, _ = _make_reporter()
        results = reporter._run_validation_checks()
        warns = [r for r in results if r["status"] == "WARN"]
        assert warns == [], f"Unexpected WARNs: {warns}"

    def test_exit_before_entry_warns(self):
        bad = _make_trade(
            entry_time="2024-01-02 11:00:00-05:00",
            exit_time="2024-01-02 10:00:00-05:00",
        )
        reporter, _ = _make_reporter(trades=[bad])
        results = reporter._run_validation_checks()
        exit_check = next(r for r in results if "Exit time after entry" in r["check"])
        assert exit_check["status"] == "WARN"

    def test_overnight_non_session_end_warns(self):
        overnight = _make_trade(
            entry_time="2024-01-02 10:05:00-05:00",
            exit_time="2024-01-03 09:35:00-05:00",
            exit_reason="force_exit",
        )
        reporter, _ = _make_reporter(trades=[overnight])
        results = reporter._run_validation_checks()
        check = next(r for r in results if "overnight" in r["check"].lower())
        assert check["status"] == "WARN"

    def test_session_end_overnight_passes(self):
        # session_end crossing midnight is allowed by the overnight check
        se = _make_trade(
            entry_time="2024-01-02 10:05:00-05:00",
            exit_time="2024-01-03 09:35:00-05:00",
            exit_reason="session_end",
        )
        reporter, _ = _make_reporter(trades=[se])
        results = reporter._run_validation_checks()
        check = next(r for r in results if "overnight" in r["check"].lower())
        assert check["status"] == "PASS"

    def test_force_exit_before_configured_time_warns(self):
        early = _make_trade(
            exit_time="2024-01-02 14:00:00-05:00",
            exit_reason="force_exit",
        )
        reporter, _ = _make_reporter(trades=[early])
        results = reporter._run_validation_checks()
        check = next(r for r in results if "Force-exit" in r["check"])
        assert check["status"] == "WARN"

    def test_force_exit_at_configured_time_passes(self):
        on_time = _make_trade(
            exit_time="2024-01-02 15:55:00-05:00",
            exit_reason="force_exit",
        )
        reporter, _ = _make_reporter(trades=[on_time])
        results = reporter._run_validation_checks()
        check = next(r for r in results if "Force-exit" in r["check"])
        assert check["status"] == "PASS"

    def test_zero_entry_price_warns(self):
        bad = _make_trade(entry_price=0.0, exit_price=473.0)
        reporter, _ = _make_reporter(trades=[bad])
        results = reporter._run_validation_checks()
        check = next(r for r in results if "zero or negative prices" in r["check"].lower())
        assert check["status"] == "WARN"

    def test_zero_shares_warns(self):
        bad = _make_trade(shares=0.0)
        reporter, _ = _make_reporter(trades=[bad])
        results = reporter._run_validation_checks()
        check = next(r for r in results if "share counts" in r["check"].lower())
        assert check["status"] == "WARN"

    def test_orb_missing_meta_warns(self):
        bad = _make_trade(meta={})  # no or_high / or_low
        reporter, _ = _make_reporter(trades=[bad])
        results = reporter._run_validation_checks()
        check = next((r for r in results if "or_high" in r["check"]), None)
        assert check is not None, "ORB meta check not found"
        assert check["status"] == "WARN"

    def test_orb_check_skipped_for_non_orb_strategy(self):
        cfg = _make_config(strategy_name="some_other_strategy")
        reporter, _ = _make_reporter(trades=[_make_trade(meta={})], config=cfg)
        results = reporter._run_validation_checks()
        orb_checks = [r for r in results if "or_high" in r["check"]]
        assert orb_checks == [], "ORB check should not run for non-ORB strategy"

    def test_session_end_different_date_warns(self):
        # session_end should exit same date as entry
        bad = _make_trade(
            entry_time="2024-01-02 10:05:00-05:00",
            exit_time="2024-01-03 09:35:00-05:00",
            exit_reason="session_end",
        )
        reporter, _ = _make_reporter(trades=[bad])
        results = reporter._run_validation_checks()
        check = next(r for r in results if "Session-end exits on entry date" in r["check"])
        assert check["status"] == "WARN"

    def test_open_positions_count_zero_passes(self):
        reporter, _ = _make_reporter(open_positions_count=0)
        results = reporter._run_validation_checks()
        check = next(r for r in results if "No open positions at end" in r["check"])
        assert check["status"] == "PASS"
        assert check["detail"] == "OK"

    def test_open_positions_count_nonzero_warns(self):
        reporter, _ = _make_reporter(open_positions_count=2)
        results = reporter._run_validation_checks()
        check = next(r for r in results if "No open positions at end" in r["check"])
        assert check["status"] == "WARN"
        assert "2" in check["detail"]


# ===========================================================================
# backtest_report.md
# ===========================================================================

class TestMarkdownReport:
    def test_file_created(self):
        reporter, out = _make_reporter()
        reporter._write_markdown_report(reporter._run_validation_checks())
        assert (out / "backtest_report.md").exists()

    def test_sections_present(self):
        reporter, out = _make_reporter()
        reporter._write_markdown_report(reporter._run_validation_checks())
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        for section in ["## Configuration", "## Performance Metrics",
                         "## Trade Summary", "## Daily Summary",
                         "## Validation Checks"]:
            assert section in content, f"Missing section: {section}"

    def test_config_values_rendered(self):
        reporter, out = _make_reporter()
        reporter._write_markdown_report(reporter._run_validation_checks())
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "opening_range_breakout" in content
        assert "100,000.00" in content

    def test_pass_status_present(self):
        reporter, out = _make_reporter()
        reporter._write_markdown_report(reporter._run_validation_checks())
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "PASS" in content

    def test_warn_status_present_when_bad_trade(self):
        bad = _make_trade(entry_price=0.0)
        reporter, out = _make_reporter(trades=[bad])
        reporter._write_markdown_report(reporter._run_validation_checks())
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "WARN" in content

    def test_no_trades_message(self):
        reporter, out = _make_reporter(trades=[])
        reporter._write_markdown_report([])
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "No trades" in content

    def test_trade_summary_stats_present(self):
        t1 = _make_trade(symbol="SPY", exit_reason="force_exit")
        t2 = _make_trade(
            symbol="QQQ",
            entry_time="2024-01-02 10:10:00-05:00",
            exit_time="2024-01-02 15:55:00-05:00",
            entry_price=380.0, exit_price=383.0,
            exit_reason="stop_loss",
        )
        reporter, out = _make_reporter(trades=[t1, t2])
        reporter._write_markdown_report(reporter._run_validation_checks())
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "Total trades" in content
        assert "Trades by symbol" in content
        assert "Trades by exit reason" in content
        assert "Best trade" in content
        assert "Worst trade" in content
        assert "Avg winning trade" in content
        assert "Avg losing trade" in content

    def test_minimal_metrics_no_crash(self):
        # Report must be created even if only a subset of known metrics are present.
        reporter, out = _make_reporter(metrics={"num_trades": 0, "initial_capital": 50_000.0})
        reporter._write_markdown_report([])
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "## Performance Metrics" in content
        assert "N/A" in content  # missing keys rendered as N/A


# ===========================================================================
# generate_all (integration smoke test)
# ===========================================================================

class TestGenerateAll:
    def test_all_four_files_created(self):
        reporter, out = _make_reporter()
        reporter.generate_all()
        for fname in ["metrics.json", "trade_log.csv", "daily_summary.csv", "backtest_report.md"]:
            assert (out / fname).exists(), f"Missing: {fname}"

    def test_generate_all_empty_trades(self):
        reporter, out = _make_reporter(trades=[])
        reporter.generate_all()
        for fname in ["metrics.json", "trade_log.csv", "daily_summary.csv", "backtest_report.md"]:
            assert (out / fname).exists(), f"Missing: {fname}"

    def test_multiple_trades_different_symbols(self):
        t1 = _make_trade(symbol="SPY")
        t2 = _make_trade(
            symbol="QQQ",
            entry_time="2024-01-02 10:10:00-05:00",
            exit_time="2024-01-02 15:55:00-05:00",
            entry_price=380.0,
            exit_price=383.0,
        )
        reporter, out = _make_reporter(trades=[t1, t2])
        reporter.generate_all()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        assert set(df["symbol"]) == {"SPY", "QQQ"}


# ===========================================================================
# Trade Detail columns in backtest_report.md
# ===========================================================================

class TestMarkdownTradeDetail:
    """Verify that the Trade Detail section of backtest_report.md contains
    all columns required for ORB audit, including meta fields."""

    # Required columns as specified in the task.
    REQUIRED_COLS = [
        "symbol",
        "direction",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "shares",
        "pnl",
        "pnl_pct",
        "exit_reason",
        "or_high",
        "or_low",
        "breakout_trigger",
        "trigger_val",
        "stop_execution",
    ]

    def _report_text(self, meta: dict | None = None) -> str:
        trade = _make_trade(meta=meta)
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_markdown_report(reporter._run_validation_checks())
        return (out / "backtest_report.md").read_text(encoding="utf-8")

    def test_trade_detail_section_present(self):
        assert "Trade Detail" in self._report_text()

    @pytest.mark.parametrize("col", REQUIRED_COLS)
    def test_required_column_present(self, col):
        content = self._report_text()
        assert col in content, f"Column '{col}' missing from Trade Detail"

    def test_all_required_columns_in_header_row(self):
        content = self._report_text()
        # Find the header row of the Trade Detail table.
        for line in content.splitlines():
            if "symbol" in line and "entry_time" in line and "stop_execution" in line:
                for col in self.REQUIRED_COLS:
                    assert col in line, f"Column '{col}' absent from header row"
                return
        pytest.fail("No Trade Detail header row found containing all expected columns")

    def test_stop_execution_value_rendered_from_meta(self):
        trade = _make_trade(meta={
            "or_high": 471.0, "or_low": 468.0,
            "breakout_trigger": "close", "trigger_val": 471.5,
            "stop_execution": "stop_price",
        })
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_markdown_report(reporter._run_validation_checks())
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "stop_price" in content

    def test_stop_execution_blank_when_meta_missing(self):
        # Trade with empty meta should render blank (not crash).
        trade = _make_trade(meta={})
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_markdown_report(reporter._run_validation_checks())
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "Trade Detail" in content  # report still generated

    def test_or_high_value_rendered(self):
        content = self._report_text()
        assert "471.0" in content  # or_high from default _make_trade meta

    def test_or_low_value_rendered(self):
        content = self._report_text()
        assert "468.0" in content  # or_low from default _make_trade meta

    def test_breakout_trigger_value_rendered(self):
        content = self._report_text()
        assert "close" in content  # breakout_trigger from default meta

    def test_entry_time_column_present(self):
        """entry_time must appear in Trade Detail (was missing in original impl)."""
        content = self._report_text()
        # Check it appears in the detail table, not just the header
        detail_idx = content.find("Trade Detail")
        assert detail_idx != -1
        detail_section = content[detail_idx:]
        assert "entry_time" in detail_section

    def test_no_trades_does_not_crash(self):
        reporter, out = _make_reporter(trades=[])
        reporter._write_markdown_report([])
        content = (out / "backtest_report.md").read_text(encoding="utf-8")
        assert "backtest" in content.lower()


# ===========================================================================
# stop_execution column in trade_log.csv
# ===========================================================================

class TestTradeLogStopExecutionColumn:
    def test_stop_execution_column_in_csv_schema(self):
        from src.reporting.report_generator import TRADE_LOG_COLUMNS
        assert "stop_execution" in TRADE_LOG_COLUMNS

    def test_stop_execution_value_written_to_csv(self):
        trade = _make_trade(meta={
            "or_high": 471.0, "or_low": 468.0,
            "breakout_trigger": "close", "trigger_val": 471.5,
            "stop_execution": "bar_close",
        })
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        assert "stop_execution" in df.columns
        assert df["stop_execution"].iloc[0] == "bar_close"

    def test_stop_execution_blank_when_not_in_meta(self):
        trade = _make_trade(meta={})
        reporter, out = _make_reporter(trades=[trade])
        reporter._write_trade_log_csv()
        df = pd.read_csv(out / "trade_log.csv", index_col="entry_time")
        assert "stop_execution" in df.columns
        assert str(df["stop_execution"].iloc[0]) in ("", "nan", "NaN")
