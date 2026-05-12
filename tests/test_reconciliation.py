"""
tests/test_reconciliation.py
-----------------------------
Tests for the dry-run broker reconciliation checks added to ReportGenerator.

No network calls, no real broker, no API keys.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.config.loader import (
    AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
    LoggingConfig, RiskConfig, StrategyConfig,
)
from src.data.base import BaseDataProvider
from src.execution.broker import OrderResult
from src.execution.fake_broker import FakeBrokerAdapter
from src.execution.order_intent import OrderIntent
from src.portfolio.portfolio import Portfolio
from src.reporting.report_generator import ReportGenerator
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout

_ET = ZoneInfo("America/New_York")

_DEFAULT_PARAMS = {
    "opening_range_start": "09:30",
    "opening_range_end":   "10:00",
    "force_exit_time":     "15:55",
    "position_size_pct":   0.95,
    "long_only":           True,
}


def _ts(date: str, time: str) -> pd.Timestamp:
    return pd.Timestamp(f"{date} {time}", tz=_ET)


def _make_breakout_day(date: str = "2024-01-02") -> pd.DataFrame:
    or_high = 490.0
    rows = {
        _ts(date, "09:30"): (480, 485, 475, 482),
        _ts(date, "09:50"): (482, or_high, 476, 488),
        _ts(date, "10:00"): (488, or_high + 1, 477, 490),
        _ts(date, "10:05"): (490, 496, 488, 495),
        _ts(date, "15:55"): (493, 494, 492, 493),
    }
    df = pd.DataFrame.from_dict(rows, orient="index", columns=["open", "high", "low", "close"])
    df["volume"] = 1_000_000
    df.index = pd.DatetimeIndex(df.index)
    return df.sort_index()


class _FakeDataProvider(BaseDataProvider):
    def __init__(self, bars):
        self._bars = bars

    def fetch_bars(self, symbol, start, end, interval):
        return self._bars.get(symbol, pd.DataFrame())


def _build_engine(bars):
    return BacktestEngine(
        strategy=OpeningRangeBreakout(_DEFAULT_PARAMS),
        data_provider=_FakeDataProvider(bars),
        portfolio=Portfolio(100_000.0, commission_per_share=0.0, slippage_per_share=0.0),
        risk_manager=RiskManager(force_exit_time="15:55"),
        symbols=list(bars.keys()),
        start_date="2024-01-02",
        end_date="2024-01-10",
        position_size_pct=0.95,
    )


def _make_config(dry_run: bool = False) -> AppConfig:
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-02", end_date="2024-01-02",
            initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(name="opening_range_breakout", params=_DEFAULT_PARAMS),
        risk=RiskConfig(),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(dry_run_broker=dry_run),
    )


def _run_full(dry_run: bool, bars=None) -> tuple[list[OrderIntent], list[OrderResult] | None, pathlib.Path]:
    """Run engine + optional broker submission + reporter. Returns (intents, results, out_dir)."""
    if bars is None:
        bars = {"SPY": _make_breakout_day()}
    result  = _build_engine(bars).run()
    intents = result.get("order_intents", [])

    order_results: list[OrderResult] | None = None
    if dry_run:
        broker = FakeBrokerAdapter(fill_immediately=True)
        order_results = [broker.submit_order(oi) for oi in intents]

    out_dir = pathlib.Path(tempfile.mkdtemp())
    reporter = ReportGenerator(
        metrics=result["metrics"],
        trades=result["trades"],
        equity_curve=result["equity_curve"],
        config=_make_config(dry_run=dry_run),
        output_dir=out_dir,
        order_intents=intents,
        order_results=order_results,
    )
    reporter.generate_all()
    return intents, order_results, out_dir


def _reporter_with(
    intents: list[OrderIntent],
    results: list[OrderResult] | None,
    dry_run: bool = True,
) -> tuple[ReportGenerator, pathlib.Path]:
    out_dir = pathlib.Path(tempfile.mkdtemp())
    reporter = ReportGenerator(
        metrics={}, trades=[], equity_curve=pd.DataFrame(),
        config=_make_config(dry_run=dry_run),
        output_dir=out_dir,
        order_intents=intents,
        order_results=results,
    )
    return reporter, out_dir


def _make_intent(
    symbol: str = "SPY",
    side: str = "buy",
    qty: float = 100.0,
    reason: str = "entry",
) -> OrderIntent:
    return OrderIntent(
        symbol=symbol, side=side, quantity=qty, order_type="market",
        reason=reason, timestamp=_ts("2024-01-02", "10:05"),
        metadata={"entry_price": 450.0},
    )


def _make_result(
    symbol: str = "SPY",
    side: str = "buy",
    qty: float = 100.0,
    reason: str = "entry",
    status: str = "filled",
    filled_price: float | None = 450.0,
) -> OrderResult:
    return OrderResult(
        order_id="FAKE-000001",
        symbol=symbol, side=side, quantity=qty,
        status=status,
        submitted_at=_ts("2024-01-02", "10:05"),
        reason=reason,
        filled_price=filled_price,
        filled_at=_ts("2024-01-02", "10:05") if status == "filled" else None,
    )


# ===========================================================================
# Dry-run disabled — no reconciliation artefacts
# ===========================================================================

class TestReconciliationDisabled:
    def test_no_json_when_dry_run_false(self):
        _, _, out_dir = _run_full(dry_run=False)
        assert not (out_dir / "order_reconciliation.json").exists()

    def test_report_has_no_order_reconciliation_section(self):
        _, _, out_dir = _run_full(dry_run=False)
        text = (out_dir / "backtest_report.md").read_text()
        assert "## Order Reconciliation" not in text

    def test_reporter_none_results_no_json(self):
        reporter, out_dir = _reporter_with([], None, dry_run=False)
        reporter.generate_all()
        assert not (out_dir / "order_reconciliation.json").exists()


# ===========================================================================
# Dry-run enabled — JSON written and schema correct
# ===========================================================================

class TestReconciliationJSON:
    def test_json_written_when_dry_run_true(self):
        _, _, out_dir = _run_full(dry_run=True)
        assert (out_dir / "order_reconciliation.json").exists()

    def test_json_has_all_required_fields(self):
        _, _, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        required = {
            "intent_count", "result_count", "unmatched_count",
            "rejected_count", "accepted_count", "filled_count",
            "mismatch_count", "overall_status",
        }
        assert required == set(data.keys())

    def test_json_counts_are_integers(self):
        _, _, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        for field in ("intent_count", "result_count", "unmatched_count",
                      "rejected_count", "accepted_count", "filled_count", "mismatch_count"):
            assert isinstance(data[field], int), f"{field} is not int"

    def test_json_overall_status_is_string(self):
        _, _, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        assert isinstance(data["overall_status"], str)

    def test_json_overall_status_valid_values(self):
        _, _, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        assert data["overall_status"] in {"PASS", "WARN"}

    def test_empty_results_list_writes_json(self):
        """order_results=[] should still write the JSON."""
        reporter, out_dir = _reporter_with([], [], dry_run=True)
        reporter.generate_all()
        assert (out_dir / "order_reconciliation.json").exists()


# ===========================================================================
# Clean reconciliation — PASS case
# ===========================================================================

class TestReconciliationPass:
    def test_clean_dry_run_is_pass(self):
        _, _, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        assert data["overall_status"] == "PASS"

    def test_intent_count_equals_result_count(self):
        intents, results, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        assert data["intent_count"] == len(intents)
        assert data["result_count"] == len(results)
        assert data["intent_count"] == data["result_count"]

    def test_unmatched_count_zero(self):
        _, _, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        assert data["unmatched_count"] == 0

    def test_mismatch_count_zero(self):
        _, _, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        assert data["mismatch_count"] == 0

    def test_status_counts_sum_to_result_count(self):
        _, results, out_dir = _run_full(dry_run=True)
        data = json.loads((out_dir / "order_reconciliation.json").read_text())
        total = data["filled_count"] + data["accepted_count"] + data["rejected_count"]
        assert total == data["result_count"]

    def test_report_contains_order_reconciliation_section(self):
        _, _, out_dir = _run_full(dry_run=True)
        text = (out_dir / "backtest_report.md").read_text()
        assert "## Order Reconciliation" in text

    def test_report_shows_pass_badge(self):
        _, _, out_dir = _run_full(dry_run=True)
        text = (out_dir / "backtest_report.md").read_text()
        assert "PASS" in text


# ===========================================================================
# Warn cases
# ===========================================================================

class TestReconciliationWarn:
    def _reconcile(self, intents, results) -> dict:
        reporter, out_dir = _reporter_with(intents, results)
        reporter.generate_all()
        return json.loads((out_dir / "order_reconciliation.json").read_text())

    def test_mismatched_count_produces_warn(self):
        intent = _make_intent()
        # Only one intent, but zero results
        data = self._reconcile([intent], [])
        assert data["overall_status"] == "WARN"
        assert data["unmatched_count"] == 1
        assert data["result_count"] == 0

    def test_extra_result_produces_warn(self):
        # Zero intents, one result
        result = _make_result()
        data = self._reconcile([], [result])
        assert data["overall_status"] == "WARN"
        assert data["unmatched_count"] == 1

    def test_symbol_mismatch_produces_warn(self):
        intent = _make_intent(symbol="SPY")
        result = _make_result(symbol="QQQ")
        data = self._reconcile([intent], [result])
        assert data["overall_status"] == "WARN"
        assert data["mismatch_count"] == 1

    def test_side_mismatch_produces_warn(self):
        intent = _make_intent(side="buy")
        result = _make_result(side="sell")
        data = self._reconcile([intent], [result])
        assert data["overall_status"] == "WARN"
        assert data["mismatch_count"] == 1

    def test_quantity_mismatch_produces_warn(self):
        intent = _make_intent(qty=100.0)
        result = _make_result(qty=50.0)
        data = self._reconcile([intent], [result])
        assert data["overall_status"] == "WARN"
        assert data["mismatch_count"] == 1

    def test_reason_mismatch_produces_warn(self):
        intent = _make_intent(reason="entry")
        result = _make_result(reason="force_exit")
        data = self._reconcile([intent], [result])
        assert data["overall_status"] == "WARN"
        assert data["mismatch_count"] == 1

    def test_report_shows_warn_badge_on_mismatch(self):
        intent = _make_intent(symbol="SPY")
        result = _make_result(symbol="QQQ")
        reporter, out_dir = _reporter_with([intent], [result])
        reporter.generate_all()
        text = (out_dir / "backtest_report.md").read_text()
        assert "WARN" in text

    def test_multiple_mismatches_counted_correctly(self):
        intents = [_make_intent(symbol="SPY"), _make_intent(symbol="QQQ")]
        results = [_make_result(symbol="QQQ"), _make_result(symbol="SPY")]
        data = self._reconcile(intents, results)
        assert data["mismatch_count"] == 2
        assert data["overall_status"] == "WARN"


# ===========================================================================
# Rejected count
# ===========================================================================

class TestRejectedCount:
    def _reconcile(self, intents, results) -> dict:
        reporter, out_dir = _reporter_with(intents, results)
        reporter.generate_all()
        return json.loads((out_dir / "order_reconciliation.json").read_text())

    def test_rejected_count_zero_when_all_filled(self):
        intent = _make_intent()
        result = _make_result(status="filled")
        data = self._reconcile([intent], [result])
        assert data["rejected_count"] == 0
        assert data["filled_count"] == 1

    def test_rejected_count_increments_per_rejection(self):
        intents = [_make_intent(), _make_intent()]
        results = [_make_result(status="rejected", filled_price=None),
                   _make_result(status="filled")]
        data = self._reconcile(intents, results)
        assert data["rejected_count"] == 1
        assert data["filled_count"] == 1

    def test_accepted_count_increments(self):
        intent = _make_intent()
        result = _make_result(status="accepted", filled_price=None)
        data = self._reconcile([intent], [result])
        assert data["accepted_count"] == 1
        assert data["filled_count"] == 0

    def test_all_rejected_still_pass_if_counts_and_fields_match(self):
        """Rejection is a valid broker outcome — doesn't by itself cause WARN."""
        intent = _make_intent(side="sell")
        result = _make_result(side="sell", status="rejected", filled_price=None)
        data = self._reconcile([intent], [result])
        assert data["rejected_count"] == 1
        assert data["overall_status"] == "PASS"
