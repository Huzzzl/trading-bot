"""
tests/test_stop_execution.py
-----------------------------
Tests for configurable stop-loss execution pricing.

Two modes:
  "bar_close"  — exit price is the bar close when low <= stop_loss.
  "stop_price" — exit price is the stop_loss itself when low <= stop_loss.

All tests use in-process fake data — no network calls, no yfinance.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.config.loader import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    LoggingConfig,
    RiskConfig,
    StrategyConfig,
)
from src.data.base import BaseDataProvider
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout

_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(date: str, hhmm: str) -> pd.Timestamp:
    h, m = hhmm.split(":")
    return pd.Timestamp(date, tz=_ET).replace(hour=int(h), minute=int(m))


def _make_bars(date: str) -> pd.DataFrame:
    """
    Craft a minimal 5-minute bar sequence for one session that guarantees:
      - An ORB entry at ~10:05 (close breaks above the 09:30–10:00 range high)
      - A stop-loss trigger on the 10:10 bar (low falls below or_low)

    OR-high  is set by the 09:30–09:55 bars (highest close = 490).
    OR-low   is the lowest low of those same bars (= 475).
    Entry    occurs at 10:05 because close (495) > or_high (490).
    Stop     fires at 10:10 because low (470) < or_low (475).
    """
    rows = {
        _ts(date, "09:30"): (480, 490, 475, 482),  # establishes or_high=490, or_low=475
        _ts(date, "09:35"): (482, 487, 477, 484),
        _ts(date, "09:40"): (484, 488, 478, 486),
        _ts(date, "09:45"): (486, 489, 479, 488),
        _ts(date, "09:50"): (488, 490, 481, 489),
        _ts(date, "09:55"): (489, 490, 482, 489),
        _ts(date, "10:00"): (489, 491, 483, 490),  # last OR bar; or_high confirmed 490
        _ts(date, "10:05"): (490, 497, 488, 495),  # breakout close 495 > 490 → ENTRY
        _ts(date, "10:10"): (495, 496, 470, 471),  # low 470 < stop 475 → STOP EXIT
        _ts(date, "15:55"): (490, 492, 488, 491),
    }
    df = pd.DataFrame.from_dict(
        rows, orient="index", columns=["open", "high", "low", "close"]
    )
    df["volume"] = 1_000_000
    df.index = pd.DatetimeIndex(df.index)
    return df.sort_index()


class FixedDataProvider(BaseDataProvider):
    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return self._bars.copy()


def _build_engine(stop_execution: str, bars: pd.DataFrame | None = None) -> BacktestEngine:
    if bars is None:
        bars = _make_bars("2024-01-02")
    strategy = OpeningRangeBreakout(params={
        "opening_range_start": "09:30",
        "opening_range_end":   "10:00",
        "force_exit_time":     "15:55",
        "position_size_pct":   0.95,
        "long_only":           True,
        "breakout_trigger":    "close",
    })
    portfolio    = Portfolio(100_000, commission_per_share=0.005, slippage_per_share=0.01)
    risk_manager = RiskManager(
        force_exit_time="15:55",
        max_open_positions=1,
        stop_execution=stop_execution,
    )
    return BacktestEngine(
        strategy=strategy,
        data_provider=FixedDataProvider(bars),
        portfolio=portfolio,
        risk_manager=risk_manager,
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-01-02",
        bar_interval="5m",
        position_size_pct=0.95,
        stop_execution=stop_execution,
    )


# ===========================================================================
# RiskManager — invalid stop_execution raises ValueError
# ===========================================================================

class TestInvalidStopExecution:
    def test_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError, match="stop_execution"):
            RiskManager(stop_execution="fill_at_open")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            RiskManager(stop_execution="")

    def test_case_sensitive(self):
        with pytest.raises(ValueError):
            RiskManager(stop_execution="Bar_Close")

    def test_bar_close_does_not_raise(self):
        rm = RiskManager(stop_execution="bar_close")
        assert rm is not None

    def test_stop_price_does_not_raise(self):
        rm = RiskManager(stop_execution="stop_price")
        assert rm is not None


# ===========================================================================
# RiskManager.check_exits — exit price selection
# ===========================================================================

class TestCheckExitsPrice:
    """Direct unit tests on RiskManager.check_exits without running the engine."""

    def _open_position(self, stop_loss: float = 475.0) -> Portfolio:
        port = Portfolio(100_000)
        port.open_long("SPY", 490.0, _ts("2024-01-02", "10:05"), 0.95, stop_loss=stop_loss)
        return port

    def test_bar_close_mode_uses_close(self):
        rm   = RiskManager("15:55", stop_execution="bar_close")
        port = self._open_position(stop_loss=475.0)
        bar  = {"SPY": {"open": 495, "high": 496, "low": 470, "close": 471}}
        exits = rm.check_exits(port, _ts("2024-01-02", "10:10"), bar)
        assert len(exits) == 1
        symbol, exit_px, reason = exits[0]
        assert reason == "stop_loss"
        assert exit_px == pytest.approx(471.0)  # bar close

    def test_stop_price_mode_uses_stop_loss(self):
        rm   = RiskManager("15:55", stop_execution="stop_price")
        port = self._open_position(stop_loss=475.0)
        bar  = {"SPY": {"open": 495, "high": 496, "low": 470, "close": 471}}
        exits = rm.check_exits(port, _ts("2024-01-02", "10:10"), bar)
        assert len(exits) == 1
        symbol, exit_px, reason = exits[0]
        assert reason == "stop_loss"
        assert exit_px == pytest.approx(475.0)  # stop_loss price

    def test_bar_close_and_stop_price_differ(self):
        """Confirm the two modes produce different prices for the same bar."""
        bar   = {"SPY": {"open": 495, "high": 496, "low": 470, "close": 471}}
        stop  = 475.0

        rm_close = RiskManager("15:55", stop_execution="bar_close")
        port_c   = self._open_position(stop_loss=stop)
        px_close = rm_close.check_exits(port_c, _ts("2024-01-02", "10:10"), bar)[0][1]

        rm_stop  = RiskManager("15:55", stop_execution="stop_price")
        port_s   = self._open_position(stop_loss=stop)
        px_stop  = rm_stop.check_exits(port_s, _ts("2024-01-02", "10:10"), bar)[0][1]

        assert px_close != px_stop
        assert px_close == pytest.approx(471.0)
        assert px_stop  == pytest.approx(475.0)

    def test_no_trigger_when_low_above_stop(self):
        rm   = RiskManager("15:55", stop_execution="bar_close")
        port = self._open_position(stop_loss=475.0)
        bar  = {"SPY": {"open": 492, "high": 494, "low": 480, "close": 491}}
        exits = rm.check_exits(port, _ts("2024-01-02", "10:10"), bar)
        assert exits == []

    def test_trigger_when_low_equals_stop(self):
        """Boundary: low == stop_loss must trigger."""
        rm   = RiskManager("15:55", stop_execution="bar_close")
        port = self._open_position(stop_loss=475.0)
        bar  = {"SPY": {"open": 480, "high": 482, "low": 475, "close": 478}}
        exits = rm.check_exits(port, _ts("2024-01-02", "10:10"), bar)
        assert len(exits) == 1
        assert exits[0][2] == "stop_loss"


# ===========================================================================
# Full engine — trade exit price reflects the mode
# ===========================================================================

class TestEngineStopPrice:
    """End-to-end: the exit price recorded on the completed Trade must match mode."""

    OR_LOW   = 475.0   # opening range low in _make_bars
    BAR_CLOSE_AT_STOP = 471.0   # close of the 10:10 bar

    def _stop_trades(self, stop_execution: str) -> list:
        result = _build_engine(stop_execution).run()
        return [t for t in result["trades"] if t.exit_reason == "stop_loss"]

    def test_bar_close_mode_trade_count(self):
        stops = self._stop_trades("bar_close")
        assert len(stops) >= 1

    def test_stop_price_mode_trade_count(self):
        stops = self._stop_trades("stop_price")
        assert len(stops) >= 1

    def test_bar_close_exit_price_is_close_minus_slippage(self):
        """In bar_close mode the fill = close − slippage (0.01)."""
        stops    = self._stop_trades("bar_close")
        exit_px  = stops[0].exit_price
        expected = self.BAR_CLOSE_AT_STOP - 0.01  # Portfolio subtracts slippage
        assert exit_px == pytest.approx(expected, abs=1e-6)

    def test_stop_price_mode_exit_price_is_stop_minus_slippage(self):
        """In stop_price mode the fill = stop_loss − slippage (0.01)."""
        stops    = self._stop_trades("stop_price")
        exit_px  = stops[0].exit_price
        expected = self.OR_LOW - 0.01  # Portfolio subtracts slippage
        assert exit_px == pytest.approx(expected, abs=1e-6)

    def test_stop_price_pnl_differs_from_bar_close(self):
        """
        Bar close is 471 while stop is 475 — the stop price is HIGHER than
        bar close, so stop_price mode should yield a better (less negative) PnL.
        Regardless: the two modes must produce different PnLs on this fixture.
        """
        pnl_close = self._stop_trades("bar_close")[0].pnl
        pnl_stop  = self._stop_trades("stop_price")[0].pnl
        assert pnl_close != pytest.approx(pnl_stop)


# ===========================================================================
# Meta propagation — stop_execution recorded on completed Trade
# ===========================================================================

class TestStopExecutionMeta:
    def test_trade_meta_records_bar_close_mode(self):
        result = _build_engine("bar_close").run()
        for trade in result["trades"]:
            assert trade.meta.get("stop_execution") == "bar_close"

    def test_trade_meta_records_stop_price_mode(self):
        result = _build_engine("stop_price").run()
        for trade in result["trades"]:
            assert trade.meta.get("stop_execution") == "stop_price"


# ===========================================================================
# Candidate B compatibility — defaults to bar_close
# ===========================================================================

class TestCandidateBCompatibility:
    def test_candidate_b_config_uses_bar_close_default(self):
        from src.main import apply_candidate_b, CANDIDATE_B_OVERRIDES
        from src.config.loader import AppConfig, BacktestConfig, DataConfig, LoggingConfig

        cfg = AppConfig(
            backtest=BacktestConfig("2024-01-02", "2024-01-05", 100_000, 0.005, 0.01),
            symbols=["SPY"],
            data=DataConfig("yahoo", "5m", "America/New_York"),
            strategy=StrategyConfig(
                name="opening_range_breakout",
                params={
                    "opening_range_start": "09:30",
                    "opening_range_end":   "10:00",
                    "force_exit_time":     "15:55",
                    "position_size_pct":   0.95,
                    "long_only":           True,
                    "breakout_trigger":    "close",
                },
            ),
            risk=RiskConfig(max_open_positions=1),
            logging=LoggingConfig(level="WARNING", format="%(message)s"),
        )
        b_cfg = apply_candidate_b(cfg)
        stop_exec = b_cfg.strategy.params.get("stop_execution", "bar_close")
        assert stop_exec == "bar_close"

    def test_risk_manager_built_with_bar_close_default_does_not_raise(self):
        rm = RiskManager(
            force_exit_time="15:55",
            max_open_positions=1,
            stop_execution="bar_close",
        )
        assert rm is not None
