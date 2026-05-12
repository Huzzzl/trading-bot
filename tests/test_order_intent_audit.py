"""
tests/test_order_intent_audit.py
---------------------------------
Tests for the OrderIntent audit log produced by BacktestEngine and
written to order_intents.csv by ReportGenerator.

No broker adapter, no network, no API keys.
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
from src.execution.order_intent import OrderIntent
from src.portfolio.portfolio import Portfolio
from src.reporting.report_generator import ReportGenerator
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
    """Single-day bar set that triggers one ORB long entry then force-exits at 15:55."""
    or_high = 490.0
    rows = {
        _ts(date, "09:30"): (480, 485, 475, 482),
        _ts(date, "09:35"): (482, 487, 476, 484),
        _ts(date, "09:40"): (484, 488, 477, 486),
        _ts(date, "09:50"): (487, or_high, 480, 488),
        _ts(date, "09:55"): (488, or_high, 481, 489),
        _ts(date, "10:00"): (489, or_high + 1, 482, 490),
        _ts(date, "10:05"): (490, 496, 488, 495),   # breakout bar — entry fires here
        _ts(date, "15:55"): (493, 494, 492, 493),   # force_exit bar
    }
    df = pd.DataFrame.from_dict(
        rows, orient="index", columns=["open", "high", "low", "close"]
    )
    df["volume"] = 1_000_000
    df.index = pd.DatetimeIndex(df.index)
    return df.sort_index()


def _make_two_day_bars(
    date1: str = "2024-01-02",
    date2: str = "2024-01-03",
) -> pd.DataFrame:
    """Two-day bar set — two trades, one per day."""
    return pd.concat([_make_breakout_day(date1), _make_breakout_day(date2)]).sort_index()


class _FakeDataProvider(BaseDataProvider):
    def __init__(self, bars: dict[str, pd.DataFrame]) -> None:
        self._bars = bars

    def fetch_bars(self, symbol, start, end, interval):
        return self._bars.get(symbol, pd.DataFrame())


def _build_engine(
    bars: dict[str, pd.DataFrame],
    capital: float = 100_000.0,
    stop_loss: float | None = None,
) -> BacktestEngine:
    params = dict(_DEFAULT_PARAMS)
    if stop_loss is not None:
        params["stop_loss_pct"] = stop_loss
    return BacktestEngine(
        strategy=OpeningRangeBreakout(params),
        data_provider=_FakeDataProvider(bars),
        portfolio=Portfolio(capital, commission_per_share=0.0, slippage_per_share=0.0),
        risk_manager=RiskManager(force_exit_time="15:55"),
        symbols=list(bars.keys()),
        start_date="2024-01-02",
        end_date="2024-01-10",
        position_size_pct=0.95,
    )


def _make_config() -> AppConfig:
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-02",
            end_date="2024-01-02",
            initial_capital=100_000,
            commission_per_share=0.0,
            slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(name="opening_range_breakout", params=_DEFAULT_PARAMS),
        risk=RiskConfig(),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(),
    )


# ===========================================================================
# BacktestEngine — order_intents in result dict
# ===========================================================================

class TestEngineOrderIntents:
    def test_result_contains_order_intents_key(self):
        bars = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        assert "order_intents" in result

    def test_order_intents_is_list(self):
        bars = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        assert isinstance(result["order_intents"], list)

    def test_order_intents_are_order_intent_objects(self):
        bars = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        for oi in result["order_intents"]:
            assert isinstance(oi, OrderIntent)

    def test_no_broker_adapter_used(self):
        """Engine must produce intents without any BrokerAdapter."""
        from src.execution.broker import BrokerAdapter
        bars = {"SPY": _make_breakout_day()}
        eng = _build_engine(bars)
        # BrokerAdapter should not be referenced anywhere in the engine instance
        for attr in vars(eng).values():
            assert not isinstance(attr, BrokerAdapter)


# ===========================================================================
# One buy + one sell per completed trade
# ===========================================================================

class TestIntentPairsPerTrade:
    def _run(self, days: int = 1) -> list[OrderIntent]:
        if days == 1:
            bars = {"SPY": _make_breakout_day()}
        else:
            bars = {"SPY": _make_two_day_bars()}
        result = _build_engine(bars).run()
        return result["order_intents"]

    def test_single_trade_produces_two_intents(self):
        intents = self._run(days=1)
        assert len(intents) == 2

    def test_single_trade_one_buy_one_sell(self):
        intents = self._run(days=1)
        sides = sorted(oi.side for oi in intents)
        assert sides == ["buy", "sell"]

    def test_two_trades_produce_four_intents(self):
        intents = self._run(days=2)
        assert len(intents) == 4

    def test_two_trades_two_buys_two_sells(self):
        intents = self._run(days=2)
        buys  = [oi for oi in intents if oi.side == "buy"]
        sells = [oi for oi in intents if oi.side == "sell"]
        assert len(buys)  == 2
        assert len(sells) == 2

    def test_no_trades_zero_intents(self):
        # Bars that never break out — all closes stay below or_high
        rows = {
            _ts("2024-01-02", "09:30"): (480, 488, 475, 482),
            _ts("2024-01-02", "09:50"): (482, 489, 476, 483),  # or_high = 489
            _ts("2024-01-02", "10:00"): (483, 489, 477, 484),  # closing at 484 < 489
            _ts("2024-01-02", "10:05"): (484, 488, 478, 485),  # close < or_high
            _ts("2024-01-02", "15:55"): (485, 486, 484, 485),
        }
        df = pd.DataFrame.from_dict(rows, orient="index", columns=["open","high","low","close"])
        df["volume"] = 1_000_000
        df.index = pd.DatetimeIndex(df.index)
        result = _build_engine({"SPY": df.sort_index()}).run()
        assert result["order_intents"] == []


# ===========================================================================
# Entry intent content
# ===========================================================================

class TestEntryIntentContent:
    def _buy_intent(self) -> OrderIntent:
        bars = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        return next(oi for oi in result["order_intents"] if oi.side == "buy")

    def test_entry_symbol(self):
        assert self._buy_intent().symbol == "SPY"

    def test_entry_side(self):
        assert self._buy_intent().side == "buy"

    def test_entry_order_type(self):
        assert self._buy_intent().order_type == "market"

    def test_entry_reason(self):
        assert self._buy_intent().reason == "entry"

    def test_entry_quantity_positive(self):
        assert self._buy_intent().quantity > 0

    def test_entry_metadata_has_strategy(self):
        assert "strategy" in self._buy_intent().metadata

    def test_entry_metadata_has_entry_price(self):
        assert "entry_price" in self._buy_intent().metadata

    def test_entry_metadata_has_or_high(self):
        assert "or_high" in self._buy_intent().metadata

    def test_entry_metadata_has_or_low(self):
        assert "or_low" in self._buy_intent().metadata

    def test_entry_metadata_has_breakout_trigger(self):
        assert "breakout_trigger" in self._buy_intent().metadata

    def test_entry_metadata_has_trigger_val(self):
        assert "trigger_val" in self._buy_intent().metadata

    def test_entry_metadata_entry_price_is_numeric(self):
        ep = self._buy_intent().metadata["entry_price"]
        assert isinstance(ep, (int, float))

    def test_entry_metadata_or_high_is_numeric(self):
        v = self._buy_intent().metadata["or_high"]
        assert isinstance(v, (int, float))

    def test_entry_no_limit_price(self):
        assert self._buy_intent().limit_price is None

    def test_entry_no_stop_price(self):
        assert self._buy_intent().stop_price is None


# ===========================================================================
# Exit intent content
# ===========================================================================

class TestExitIntentContent:
    def _sell_intent(self) -> OrderIntent:
        bars = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        return next(oi for oi in result["order_intents"] if oi.side == "sell")

    def test_exit_symbol(self):
        assert self._sell_intent().symbol == "SPY"

    def test_exit_side(self):
        assert self._sell_intent().side == "sell"

    def test_exit_order_type(self):
        assert self._sell_intent().order_type == "market"

    def test_exit_reason_matches_trade(self):
        bars = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        sell = next(oi for oi in result["order_intents"] if oi.side == "sell")
        trade = result["trades"][0]
        assert sell.reason == trade.exit_reason

    def test_exit_metadata_has_exit_price(self):
        assert "exit_price" in self._sell_intent().metadata

    def test_exit_metadata_has_exit_reason(self):
        assert "exit_reason" in self._sell_intent().metadata

    def test_exit_quantity_positive(self):
        assert self._sell_intent().quantity > 0

    def test_exit_quantity_matches_entry_quantity(self):
        bars = {"SPY": _make_breakout_day()}
        result = _build_engine(bars).run()
        buy  = next(oi for oi in result["order_intents"] if oi.side == "buy")
        sell = next(oi for oi in result["order_intents"] if oi.side == "sell")
        assert buy.quantity == sell.quantity


# ===========================================================================
# ReportGenerator — order_intents.csv
# ===========================================================================

class TestOrderIntentsCSV:
    def _run_and_report(self, days: int = 1) -> pathlib.Path:
        if days == 1:
            bars = {"SPY": _make_breakout_day()}
        else:
            bars = {"SPY": _make_two_day_bars()}
        eng    = _build_engine(bars)
        result = eng.run()

        out_dir = pathlib.Path(tempfile.mkdtemp())
        reporter = ReportGenerator(
            metrics=result["metrics"],
            trades=result["trades"],
            equity_curve=result["equity_curve"],
            config=_make_config(),
            output_dir=out_dir,
            open_positions_count=0,
            order_intents=result["order_intents"],
        )
        reporter.generate_all()
        return out_dir / "order_intents.csv"

    def test_csv_is_written(self):
        path = self._run_and_report()
        assert path.exists()

    def test_csv_has_expected_columns(self):
        path = self._run_and_report()
        df = pd.read_csv(path)
        expected = {
            "timestamp", "symbol", "side", "quantity", "order_type",
            "reason", "limit_price", "stop_price", "metadata_json",
        }
        assert expected.issubset(set(df.columns))

    def test_csv_row_count_matches_intent_count(self):
        bars   = {"SPY": _make_breakout_day()}
        eng    = _build_engine(bars)
        result = eng.run()
        n_intents = len(result["order_intents"])

        out_dir  = pathlib.Path(tempfile.mkdtemp())
        reporter = ReportGenerator(
            metrics=result["metrics"],
            trades=result["trades"],
            equity_curve=result["equity_curve"],
            config=_make_config(),
            output_dir=out_dir,
            order_intents=result["order_intents"],
        )
        reporter.generate_all()
        df = pd.read_csv(out_dir / "order_intents.csv")
        assert len(df) == n_intents

    def test_csv_sides_correct(self):
        path = self._run_and_report()
        df   = pd.read_csv(path)
        assert set(df["side"].unique()).issubset({"buy", "sell"})

    def test_csv_metadata_json_is_parseable(self):
        path = self._run_and_report()
        df   = pd.read_csv(path)
        for raw in df["metadata_json"]:
            obj = json.loads(raw)
            assert isinstance(obj, dict)

    def test_csv_written_with_no_trades(self):
        """Empty order_intents list must still produce a header-only CSV."""
        out_dir = pathlib.Path(tempfile.mkdtemp())
        reporter = ReportGenerator(
            metrics={},
            trades=[],
            equity_curve=pd.DataFrame(),
            config=_make_config(),
            output_dir=out_dir,
            order_intents=[],
        )
        reporter.generate_all()
        path = out_dir / "order_intents.csv"
        assert path.exists()
        df = pd.read_csv(path)
        assert len(df) == 0

    def test_csv_two_trades_four_rows(self):
        path = self._run_and_report(days=2)
        df   = pd.read_csv(path)
        assert len(df) == 4

    def test_reporter_without_order_intents_param_still_works(self):
        """order_intents defaults to empty — generate_all must not raise."""
        out_dir = pathlib.Path(tempfile.mkdtemp())
        reporter = ReportGenerator(
            metrics={},
            trades=[],
            equity_curve=pd.DataFrame(),
            config=_make_config(),
            output_dir=out_dir,
        )
        reporter.generate_all()
        assert (out_dir / "order_intents.csv").exists()


# ===========================================================================
# Schema consistency fixes
# ===========================================================================

class TestEmptyDataReturn:
    def test_no_bar_data_returns_order_intents_key(self):
        eng = _build_engine({})
        result = eng.run()
        assert "order_intents" in result

    def test_no_bar_data_order_intents_is_empty_list(self):
        eng = _build_engine({})
        result = eng.run()
        assert result["order_intents"] == []

    def test_no_bar_data_result_has_all_keys(self):
        eng = _build_engine({})
        result = eng.run()
        for key in ("metrics", "trades", "equity_curve", "order_intents"):
            assert key in result


class TestExitIntentStopExecutionConsistency:
    """All exit paths must include stop_execution in their metadata."""

    def _sell_intents_by_reason(self, reason: str) -> list:
        """Return sell intents matching a specific reason from a two-day run."""
        bars = {"SPY": _make_two_day_bars()}
        result = _build_engine(bars).run()
        return [oi for oi in result["order_intents"] if oi.side == "sell" and oi.reason == reason]

    def test_force_exit_metadata_contains_stop_execution(self):
        intents = self._sell_intents_by_reason("force_exit")
        assert intents, "Expected at least one force_exit sell intent"
        for oi in intents:
            assert "stop_execution" in oi.metadata

    def test_force_exit_stop_execution_value_is_string(self):
        intents = self._sell_intents_by_reason("force_exit")
        for oi in intents:
            assert isinstance(oi.metadata["stop_execution"], str)

    def test_end_of_backtest_metadata_contains_stop_execution(self):
        """Produce an end_of_backtest exit by leaving a position open at last bar."""
        # Use a single day with NO force_exit bar so position survives to end
        rows = {
            _ts("2024-01-02", "09:30"): (480, 485, 475, 482),
            _ts("2024-01-02", "09:50"): (482, 490, 476, 488),
            _ts("2024-01-02", "10:00"): (488, 491, 477, 490),
            _ts("2024-01-02", "10:05"): (490, 496, 488, 495),  # breakout — entry here
            _ts("2024-01-02", "10:10"): (495, 497, 493, 496),  # no force_exit bar
        }
        df = pd.DataFrame.from_dict(rows, orient="index", columns=["open","high","low","close"])
        df["volume"] = 1_000_000
        df.index = pd.DatetimeIndex(df.index)
        result = _build_engine({"SPY": df.sort_index()}).run()
        eob = [oi for oi in result["order_intents"] if oi.reason == "end_of_backtest"]
        assert eob, "Expected at least one end_of_backtest sell intent"
        for oi in eob:
            assert "stop_execution" in oi.metadata

    def test_end_of_backtest_stop_execution_value_is_string(self):
        rows = {
            _ts("2024-01-02", "09:30"): (480, 485, 475, 482),
            _ts("2024-01-02", "09:50"): (482, 490, 476, 488),
            _ts("2024-01-02", "10:00"): (488, 491, 477, 490),
            _ts("2024-01-02", "10:05"): (490, 496, 488, 495),
            _ts("2024-01-02", "10:10"): (495, 497, 493, 496),
        }
        df = pd.DataFrame.from_dict(rows, orient="index", columns=["open","high","low","close"])
        df["volume"] = 1_000_000
        df.index = pd.DatetimeIndex(df.index)
        result = _build_engine({"SPY": df.sort_index()}).run()
        eob = [oi for oi in result["order_intents"] if oi.reason == "end_of_backtest"]
        for oi in eob:
            assert isinstance(oi.metadata["stop_execution"], str)

    def test_session_end_metadata_contains_stop_execution(self):
        """session_end fires when a position is open at the start of a new trading day."""
        # Day 1: entry fires but NO force_exit bar → position survives overnight
        rows_d1 = {
            _ts("2024-01-02", "09:30"): (480, 485, 475, 482),
            _ts("2024-01-02", "09:50"): (482, 490, 476, 488),
            _ts("2024-01-02", "10:00"): (488, 491, 477, 490),
            _ts("2024-01-02", "10:05"): (490, 496, 488, 495),  # breakout — entry
            _ts("2024-01-02", "10:10"): (495, 497, 493, 496),  # session continues, no close
        }
        # Day 2: any bar triggers session_end for the surviving position
        rows_d2 = {
            _ts("2024-01-03", "09:30"): (496, 498, 494, 497),
        }
        all_rows = {**rows_d1, **rows_d2}
        df = pd.DataFrame.from_dict(all_rows, orient="index", columns=["open","high","low","close"])
        df["volume"] = 1_000_000
        df.index = pd.DatetimeIndex(df.index)
        result = _build_engine({"SPY": df.sort_index()}).run()
        se = [oi for oi in result["order_intents"] if oi.reason == "session_end"]
        assert se, "Expected at least one session_end sell intent"
        for oi in se:
            assert "stop_execution" in oi.metadata

    def test_session_end_stop_execution_value_is_string(self):
        rows_d1 = {
            _ts("2024-01-02", "09:30"): (480, 485, 475, 482),
            _ts("2024-01-02", "09:50"): (482, 490, 476, 488),
            _ts("2024-01-02", "10:00"): (488, 491, 477, 490),
            _ts("2024-01-02", "10:05"): (490, 496, 488, 495),
            _ts("2024-01-02", "10:10"): (495, 497, 493, 496),
        }
        rows_d2 = {_ts("2024-01-03", "09:30"): (496, 498, 494, 497)}
        all_rows = {**rows_d1, **rows_d2}
        df = pd.DataFrame.from_dict(all_rows, orient="index", columns=["open","high","low","close"])
        df["volume"] = 1_000_000
        df.index = pd.DatetimeIndex(df.index)
        result = _build_engine({"SPY": df.sort_index()}).run()
        se = [oi for oi in result["order_intents"] if oi.reason == "session_end"]
        for oi in se:
            assert isinstance(oi.metadata["stop_execution"], str)

    def test_all_sell_intents_have_stop_execution(self):
        """Regression: every sell intent from any exit path has stop_execution."""
        bars = {"SPY": _make_two_day_bars()}
        result = _build_engine(bars).run()
        sells = [oi for oi in result["order_intents"] if oi.side == "sell"]
        assert sells
        for oi in sells:
            assert "stop_execution" in oi.metadata, (
                f"Missing stop_execution on sell intent reason={oi.reason!r}"
            )
