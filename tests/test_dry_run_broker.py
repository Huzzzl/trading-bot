"""
tests/test_dry_run_broker.py
-----------------------------
Tests for the fake-broker dry-run execution path.

When execution.dry_run_broker is true, every OrderIntent produced by the
backtest is submitted through FakeBrokerAdapter and the results are written
to output/order_results.csv.  The underlying backtest metrics and trades
must be identical regardless of whether the dry-run flag is set.

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
    def __init__(self, bars: dict[str, pd.DataFrame]) -> None:
        self._bars = bars

    def fetch_bars(self, symbol, start, end, interval):
        return self._bars.get(symbol, pd.DataFrame())


def _build_engine(bars: dict[str, pd.DataFrame]) -> BacktestEngine:
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


def _run_with_reporter(dry_run: bool) -> tuple[dict, pathlib.Path]:
    """Run engine and reporter; return (results, output_dir)."""
    bars   = {"SPY": _make_breakout_day()}
    result = _build_engine(bars).run()
    intents = result.get("order_intents", [])

    order_results = None
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
    return result, out_dir


# ===========================================================================
# ExecutionConfig — dry_run_broker defaults
# ===========================================================================

class TestExecutionConfigDryRunDefault:
    def test_default_is_false(self):
        ec = ExecutionConfig()
        assert ec.dry_run_broker is False

    def test_explicit_false_accepted(self):
        ec = ExecutionConfig(dry_run_broker=False)
        assert ec.dry_run_broker is False

    def test_explicit_true_accepted(self):
        ec = ExecutionConfig(dry_run_broker=True)
        assert ec.dry_run_broker is True

    def test_settings_yaml_default_is_false(self):
        from src.config.loader import load_config
        cfg = load_config()
        assert cfg.execution.dry_run_broker is False

    def test_yaml_loader_parses_true(self):
        import pathlib, tempfile
        from src.config.loader import load_config
        yaml = (
            "backtest:\n  start_date: '2024-01-01'\n  end_date: '2024-01-31'\n"
            "  initial_capital: 100000\n  commission_per_share: 0.005\n  slippage_per_share: 0.01\n"
            "symbols: [SPY]\n"
            "data:\n  provider: yahoo\n  bar_interval: '5m'\n  timezone: 'America/New_York'\n"
            "strategy:\n  name: opening_range_breakout\n  params: {}\n"
            "risk: {}\n"
            "logging:\n  level: WARNING\n  format: '%(message)s'\n"
            "execution:\n  mode: backtest\n  dry_run_broker: true\n"
        )
        tmp = pathlib.Path(tempfile.mktemp(suffix=".yaml"))
        tmp.write_text(yaml)
        cfg = load_config(tmp)
        assert cfg.execution.dry_run_broker is True

    def test_yaml_loader_parses_false(self):
        import pathlib, tempfile
        from src.config.loader import load_config
        yaml = (
            "backtest:\n  start_date: '2024-01-01'\n  end_date: '2024-01-31'\n"
            "  initial_capital: 100000\n  commission_per_share: 0.005\n  slippage_per_share: 0.01\n"
            "symbols: [SPY]\n"
            "data:\n  provider: yahoo\n  bar_interval: '5m'\n  timezone: 'America/New_York'\n"
            "strategy:\n  name: opening_range_breakout\n  params: {}\n"
            "risk: {}\n"
            "logging:\n  level: WARNING\n  format: '%(message)s'\n"
            "execution:\n  mode: backtest\n  dry_run_broker: false\n"
        )
        tmp = pathlib.Path(tempfile.mktemp(suffix=".yaml"))
        tmp.write_text(yaml)
        cfg = load_config(tmp)
        assert cfg.execution.dry_run_broker is False


# ===========================================================================
# Dry-run disabled — no FakeBroker, no order_results.csv
# ===========================================================================

class TestDryRunDisabled:
    def test_no_order_results_csv_when_disabled(self):
        _, out_dir = _run_with_reporter(dry_run=False)
        assert not (out_dir / "order_results.csv").exists()

    def test_reporter_without_order_results_param_still_works(self):
        out_dir = pathlib.Path(tempfile.mkdtemp())
        reporter = ReportGenerator(
            metrics={}, trades=[], equity_curve=pd.DataFrame(),
            config=_make_config(dry_run=False),
            output_dir=out_dir,
        )
        reporter.generate_all()
        assert not (out_dir / "order_results.csv").exists()


# ===========================================================================
# Dry-run enabled — FakeBrokerAdapter submission
# ===========================================================================

class TestDryRunEnabled:
    def _run(self):
        bars   = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        intents = result.get("order_intents", [])
        broker  = FakeBrokerAdapter(fill_immediately=True)
        return result, intents, [broker.submit_order(oi) for oi in intents]

    def test_each_intent_produces_one_result(self):
        _, intents, order_results = self._run()
        assert len(order_results) == len(intents)

    def test_all_results_are_order_result_objects(self):
        _, _, order_results = self._run()
        for r in order_results:
            assert isinstance(r, OrderResult)

    def test_buy_intents_become_filled_results(self):
        _, intents, order_results = self._run()
        for oi, r in zip(intents, order_results):
            if oi.side == "buy":
                # buys have a resolvable price in metadata → filled immediately
                assert r.status in {"filled", "accepted"}

    def test_order_ids_are_unique(self):
        _, _, order_results = self._run()
        ids = [r.order_id for r in order_results]
        assert len(ids) == len(set(ids))

    def test_result_symbols_match_intent_symbols(self):
        _, intents, order_results = self._run()
        for oi, r in zip(intents, order_results):
            assert r.symbol == oi.symbol

    def test_result_sides_match_intent_sides(self):
        _, intents, order_results = self._run()
        for oi, r in zip(intents, order_results):
            assert r.side == oi.side

    def test_result_quantities_match_intent_quantities(self):
        _, intents, order_results = self._run()
        for oi, r in zip(intents, order_results):
            assert r.quantity == oi.quantity

    def test_backtest_metrics_unchanged_by_dry_run(self):
        """Dry-run must not alter portfolio results."""
        bars    = {"SPY": _make_breakout_day()}
        result1 = _build_engine(bars).run()

        # Same engine run with dry-run submission afterwards
        result2 = _build_engine(bars).run()
        intents2 = result2.get("order_intents", [])
        broker = FakeBrokerAdapter(fill_immediately=True)
        [broker.submit_order(oi) for oi in intents2]

        assert result1["metrics"] == result2["metrics"]
        assert len(result1["trades"]) == len(result2["trades"])

    def test_backtest_trades_unchanged_by_dry_run(self):
        bars    = {"SPY": _make_breakout_day()}
        result1 = _build_engine(bars).run()
        result2 = _build_engine(bars).run()
        intents2 = result2.get("order_intents", [])
        broker = FakeBrokerAdapter(fill_immediately=True)
        [broker.submit_order(oi) for oi in intents2]

        for t1, t2 in zip(result1["trades"], result2["trades"]):
            assert t1.pnl       == t2.pnl
            assert t1.symbol    == t2.symbol
            assert t1.exit_reason == t2.exit_reason


# ===========================================================================
# order_results.csv
# ===========================================================================

class TestOrderResultsCSV:
    def test_csv_is_written_when_dry_run_true(self):
        _, out_dir = _run_with_reporter(dry_run=True)
        assert (out_dir / "order_results.csv").exists()

    def test_csv_has_expected_columns(self):
        _, out_dir = _run_with_reporter(dry_run=True)
        df = pd.read_csv(out_dir / "order_results.csv")
        expected = {
            "order_id", "symbol", "side", "quantity", "status",
            "submitted_at", "filled_at", "filled_price", "reason", "metadata_json",
        }
        assert expected.issubset(set(df.columns))

    def test_csv_row_count_equals_intent_count(self):
        bars   = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        intents = result.get("order_intents", [])
        broker  = FakeBrokerAdapter(fill_immediately=True)
        order_results = [broker.submit_order(oi) for oi in intents]

        out_dir = pathlib.Path(tempfile.mkdtemp())
        reporter = ReportGenerator(
            metrics=result["metrics"], trades=result["trades"],
            equity_curve=result["equity_curve"],
            config=_make_config(dry_run=True),
            output_dir=out_dir,
            order_intents=intents,
            order_results=order_results,
        )
        reporter.generate_all()
        df = pd.read_csv(out_dir / "order_results.csv")
        assert len(df) == len(intents)

    def test_csv_statuses_are_valid(self):
        _, out_dir = _run_with_reporter(dry_run=True)
        df = pd.read_csv(out_dir / "order_results.csv")
        valid = {"accepted", "filled", "cancelled", "rejected"}
        for status in df["status"]:
            assert status in valid

    def test_csv_metadata_json_parseable(self):
        _, out_dir = _run_with_reporter(dry_run=True)
        df = pd.read_csv(out_dir / "order_results.csv")
        for raw in df["metadata_json"]:
            assert isinstance(json.loads(raw), dict)

    def test_csv_not_written_when_dry_run_false(self):
        _, out_dir = _run_with_reporter(dry_run=False)
        assert not (out_dir / "order_results.csv").exists()

    def test_csv_order_ids_are_unique(self):
        _, out_dir = _run_with_reporter(dry_run=True)
        df = pd.read_csv(out_dir / "order_results.csv")
        assert df["order_id"].nunique() == len(df)


# ===========================================================================
# Paper mode still raises NotImplementedError
# ===========================================================================

class TestPaperModeUnchanged:
    def test_paper_mode_still_raises(self):
        import unittest.mock as mock
        from src.main import main as _main
        from src.config.loader import load_config as _lc

        cfg = AppConfig(
            backtest=BacktestConfig(
                start_date="2024-01-01", end_date="2024-01-31",
                initial_capital=100_000, commission_per_share=0.005, slippage_per_share=0.01,
            ),
            symbols=["SPY"],
            data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
            strategy=StrategyConfig(name="opening_range_breakout", params={}),
            risk=RiskConfig(),
            logging=LoggingConfig(level="WARNING", format="%(message)s"),
            execution=ExecutionConfig(mode="paper", dry_run_broker=True),
        )
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            with pytest.raises(NotImplementedError, match="Paper trading is not implemented"):
                _main()
