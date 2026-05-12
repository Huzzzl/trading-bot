"""
tests/test_daily_loss_limit.py
-------------------------------
Tests for the daily loss limit / kill-switch feature.

All tests use fake data — no network calls, no yfinance, no broker.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.config.loader import AppConfig, BacktestConfig, DataConfig, LoggingConfig, RiskConfig, StrategyConfig
from src.data.base import BaseDataProvider
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.base import Signal, SignalDirection
from src.strategy.opening_range_breakout import OpeningRangeBreakout

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rm(
    limit_pct: float | None = None,
    action: str = "block_new_entries",
    **kwargs,
) -> RiskManager:
    return RiskManager(
        force_exit_time="15:55",
        max_open_positions=None,
        daily_loss_limit_pct=limit_pct,
        daily_loss_action=action,
        **kwargs,
    )


def _make_portfolio(capital: float = 10_000.0) -> Portfolio:
    return Portfolio(
        initial_capital=capital,
        commission_per_share=0.0,
        slippage_per_share=0.0,
    )


def _signal(symbol: str = "SPY", ts: pd.Timestamp | None = None) -> Signal:
    ts = ts or pd.Timestamp("2024-01-02 10:05", tz=_ET)
    return Signal(
        direction=SignalDirection.LONG,
        symbol=symbol,
        entry_price=100.0,
        stop_loss=1.0,
        timestamp=ts,
    )


def _ts(date: str, hhmm: str) -> pd.Timestamp:
    h, m = hhmm.split(":")
    return pd.Timestamp(date, tz=_ET).replace(hour=int(h), minute=int(m))


def _bar(close: float, low: float | None = None) -> dict:
    low = low if low is not None else close
    return {"open": close, "high": close, "low": low, "close": close}


# ---------------------------------------------------------------------------
# Crafted multi-day bar provider for engine integration tests.
#
# Day structure (5-min bars, Eastern):
#   09:30–09:55 : opening range (OR high=or_high, OR low=or_low)
#   10:00       : first post-OR bar (range formed)
#   10:05       : breakout bar (close=breakout_close > or_high → LONG signal)
#   10:10       : loss bar    (close=loss_close, induces daily P&L drop)
#   10:15–15:55 : flat bars   (close=loss_close, force-exit at 15:55)
# ---------------------------------------------------------------------------

def _make_day(
    date_str: str,
    *,
    or_high: float = 100.0,
    or_low: float  = 1.0,
    breakout_close: float = 105.0,
    loss_close: float = 105.0,  # default: flat, no loss
) -> pd.DataFrame:
    rows: list[dict] = []
    base = pd.Timestamp(date_str, tz=_ET).replace(hour=9, minute=30)

    # 09:30–09:55: six OR bars
    for i in range(6):
        ts = base + pd.Timedelta(minutes=5 * i)
        rows.append(dict(timestamp=ts, open=or_high, high=or_high, low=or_low, close=or_high, volume=1000))

    # 10:00: range formation bar
    rows.append(dict(timestamp=base + pd.Timedelta(minutes=30),
                     open=or_high, high=or_high, low=or_low, close=or_high, volume=1000))

    # 10:05: breakout bar
    rows.append(dict(timestamp=base + pd.Timedelta(minutes=35),
                     open=or_high, high=breakout_close, low=or_high, close=breakout_close, volume=1000))

    # 10:10: loss bar (low stays above or_low=1, so stop never fires)
    rows.append(dict(timestamp=base + pd.Timedelta(minutes=40),
                     open=loss_close, high=loss_close, low=loss_close, close=loss_close, volume=1000))

    # 10:15–15:55: flat at loss_close (78 bars total per day — 8 already done → 70 more)
    for i in range(1, 71):
        ts = base + pd.Timedelta(minutes=40 + 5 * i)
        hhmm = ts.strftime("%H:%M")
        if hhmm > "15:55":
            break
        rows.append(dict(timestamp=ts, open=loss_close, high=loss_close,
                          low=loss_close, close=loss_close, volume=1000))

    df = pd.DataFrame(rows).set_index("timestamp")
    return df


class FixedProvider(BaseDataProvider):
    """Returns pre-built per-symbol DataFrames."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = frames

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return self._frames[symbol].copy()


def _run_engine(
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    limit_pct: float | None = None,
    action: str = "block_new_entries",
    initial_capital: float = 10_000.0,
    position_size_pct: float = 0.95,
    max_open_positions: int | None = 1,
) -> list:
    """Run a full backtest and return the completed trade list."""
    strategy = OpeningRangeBreakout(params={
        "opening_range_start": "09:30",
        "opening_range_end":   "10:00",
        "force_exit_time":     "15:55",
        "position_size_pct":   position_size_pct,
        "long_only":           True,
        "breakout_trigger":    "close",
    })
    portfolio = Portfolio(
        initial_capital=initial_capital,
        commission_per_share=0.0,
        slippage_per_share=0.0,
    )
    risk_manager = RiskManager(
        force_exit_time="15:55",
        max_open_positions=max_open_positions,
        daily_loss_limit_pct=limit_pct,
        daily_loss_action=action,
    )
    provider = FixedProvider(bars_by_symbol)
    symbols  = list(bars_by_symbol.keys())
    engine = BacktestEngine(
        strategy=strategy,
        data_provider=provider,
        portfolio=portfolio,
        risk_manager=risk_manager,
        symbols=symbols,
        start_date="2024-01-01",
        end_date="2024-01-31",
        bar_interval="5m",
        position_size_pct=position_size_pct,
    )
    results = engine.run()
    return results["trades"]


# ===========================================================================
# Config validation
# ===========================================================================

class TestDailyLossLimitConfig:
    def test_invalid_action_raises_value_error_in_risk_manager(self):
        with pytest.raises(ValueError, match="daily_loss_action"):
            RiskManager(daily_loss_limit_pct=5.0, daily_loss_action="invalid_action")

    def test_block_new_entries_action_accepted(self):
        rm = _make_rm(limit_pct=5.0, action="block_new_entries")
        assert rm._daily_loss_action == "block_new_entries"

    def test_close_all_action_accepted(self):
        rm = _make_rm(limit_pct=5.0, action="close_all")
        assert rm._daily_loss_action == "close_all"

    def test_none_limit_accepted(self):
        rm = _make_rm(limit_pct=None)
        assert rm._daily_loss_limit_pct is None

    def test_invalid_action_raises_from_loader(self):
        from src.config.loader import load_config
        import yaml, tempfile, pathlib
        bad_yaml = """
backtest:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  initial_capital: 100000
  commission_per_share: 0.005
  slippage_per_share: 0.01
symbols: [SPY]
data:
  provider: yahoo
  bar_interval: "5m"
  timezone: "America/New_York"
strategy:
  name: opening_range_breakout
  params: {}
risk:
  daily_loss_action: "bad_action"
logging:
  level: WARNING
  format: "%(message)s"
"""
        tmp = pathlib.Path(tempfile.mktemp(suffix=".yaml"))
        tmp.write_text(bad_yaml)
        with pytest.raises(ValueError, match="daily_loss_action"):
            load_config(tmp)


# ===========================================================================
# RiskManager unit tests
# ===========================================================================

class TestRiskManagerDailyLoss:
    # ---- no limit ----

    def test_no_limit_approve_entry_unaffected(self):
        rm        = _make_rm(limit_pct=None)
        portfolio = _make_portfolio()
        ts        = _ts("2024-01-02", "10:05")
        assert rm.approve_entry(_signal(ts=ts), portfolio, ts) is True

    def test_no_limit_check_exits_returns_no_daily_limit_exits(self):
        rm        = _make_rm(limit_pct=None)
        portfolio = _make_portfolio()
        ts        = _ts("2024-01-02", "10:10")
        bar_data  = {"SPY": _bar(50.0)}
        exits = rm.check_exits(portfolio, ts, bar_data)
        assert all(reason != "daily_loss_limit" for _, _, reason in exits)

    # ---- day-start equity tracking ----

    def test_day_start_equity_set_on_first_bar(self):
        rm        = _make_rm(limit_pct=5.0)
        portfolio = _make_portfolio(10_000.0)
        ts        = _ts("2024-01-02", "09:30")
        rm.check_exits(portfolio, ts, {"SPY": _bar(100.0)})
        assert rm._day_start_equity == pytest.approx(10_000.0)
        assert rm._current_day == "2024-01-02"

    def test_day_start_equity_resets_on_new_day(self):
        rm        = _make_rm(limit_pct=5.0)
        portfolio = _make_portfolio(10_000.0)

        # Day 1 first bar
        rm.check_exits(portfolio, _ts("2024-01-02", "09:30"), {"SPY": _bar(100.0)})
        assert rm._day_start_equity == pytest.approx(10_000.0)

        # Simulate cash drop
        portfolio.cash = 8_000.0

        # Day 2 first bar — day_start_equity resets to current equity (8000)
        rm.check_exits(portfolio, _ts("2024-01-03", "09:30"), {"SPY": _bar(100.0)})
        assert rm._day_start_equity == pytest.approx(8_000.0)
        assert rm._current_day == "2024-01-03"

    def test_breach_flag_resets_on_new_day(self):
        rm        = _make_rm(limit_pct=5.0)
        rm._current_day          = "2024-01-02"
        rm._day_start_equity     = 10_000.0
        rm._daily_limit_breached = True

        portfolio = _make_portfolio(9_500.0)
        rm.check_exits(portfolio, _ts("2024-01-03", "09:30"), {"SPY": _bar(95.0)})

        assert rm._daily_limit_breached is False
        assert rm._current_day == "2024-01-03"

    # ---- breach detection ----

    def test_limit_not_breached_when_loss_within_threshold(self):
        rm        = _make_rm(limit_pct=5.0)
        portfolio = _make_portfolio(10_000.0)

        # Pre-set day tracking
        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 10_000.0

        # Loss of 3% — below 5% threshold
        portfolio.cash = 9_700.0
        rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), {"SPY": _bar(97.0)})

        assert rm._daily_limit_breached is False

    def test_limit_breached_when_loss_exceeds_threshold(self):
        rm        = _make_rm(limit_pct=5.0)
        portfolio = _make_portfolio(10_000.0)

        # Pre-set: open position worth 9500, now worth 8000 → equity ≈ 8000
        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 10_000.0
        # Open a position then drop price
        portfolio.open_long("SPY", 95.0, _ts("2024-01-02", "10:05"), 0.95, stop_loss=1.0)
        # portfolio now: cash≈500, shares=100, position at 95
        # At close=50: equity = 500 + 100*50 = 5500 → -45%
        bar_data = {"SPY": {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}}
        rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), bar_data)

        assert rm._daily_limit_breached is True

    # ---- block_new_entries ----

    def test_block_new_entries_rejects_approve_entry_when_breached(self):
        rm = _make_rm(limit_pct=5.0, action="block_new_entries")
        rm._daily_limit_breached = True
        portfolio = _make_portfolio()
        ts = _ts("2024-01-02", "10:15")
        assert rm.approve_entry(_signal(ts=ts), portfolio, ts) is False

    def test_block_new_entries_no_daily_loss_exits_in_check_exits(self):
        rm        = _make_rm(limit_pct=5.0, action="block_new_entries")
        portfolio = _make_portfolio()

        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 10_000.0

        # Position that lost a lot
        portfolio.open_long("SPY", 95.0, _ts("2024-01-02", "10:05"), 0.95, stop_loss=1.0)
        bar_data = {"SPY": {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}}
        exits = rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), bar_data)

        # Limit IS breached
        assert rm._daily_limit_breached is True
        # But no forced exit from daily_loss_limit
        assert not any(reason == "daily_loss_limit" for _, _, reason in exits)

    # ---- close_all ----

    def test_close_all_adds_exit_for_each_open_position(self):
        rm        = _make_rm(limit_pct=5.0, action="close_all")
        portfolio = _make_portfolio(20_000.0)

        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 20_000.0

        # Two open positions
        portfolio.open_long("SPY", 100.0, _ts("2024-01-02", "10:05"), 0.48, stop_loss=1.0)
        portfolio.open_long("QQQ", 200.0, _ts("2024-01-02", "10:05"), 0.48, stop_loss=1.0)

        # Big drop → both below limit
        bar_data = {
            "SPY": {"open": 40.0, "high": 40.0, "low": 40.0, "close": 40.0},
            "QQQ": {"open": 80.0, "high": 80.0, "low": 80.0, "close": 80.0},
        }
        exits = rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), bar_data)

        exit_syms    = [sym    for sym, _, _      in exits]
        exit_reasons = [reason for _,   _, reason in exits]
        assert "SPY"               in exit_syms
        assert "QQQ"               in exit_syms
        assert exit_reasons.count("daily_loss_limit") == 2

    def test_close_all_exit_price_is_bar_close(self):
        rm        = _make_rm(limit_pct=5.0, action="close_all")
        portfolio = _make_portfolio(10_000.0)

        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 10_000.0

        portfolio.open_long("SPY", 95.0, _ts("2024-01-02", "10:05"), 0.95, stop_loss=1.0)
        bar_data = {"SPY": {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}}
        exits = rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), bar_data)

        daily_exits = [(sym, px) for sym, px, reason in exits if reason == "daily_loss_limit"]
        assert len(daily_exits) == 1
        assert daily_exits[0] == ("SPY", 50.0)

    def test_close_all_does_not_fire_twice_same_day(self):
        rm        = _make_rm(limit_pct=5.0, action="close_all")
        portfolio = _make_portfolio(10_000.0)

        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 10_000.0

        portfolio.open_long("SPY", 95.0, _ts("2024-01-02", "10:05"), 0.95, stop_loss=1.0)
        bar_data = {"SPY": {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}}

        # First call triggers close_all
        exits1 = rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), bar_data)
        assert any(r == "daily_loss_limit" for _, _, r in exits1)

        # Second call same day — no double-close
        exits2 = rm.check_exits(portfolio, _ts("2024-01-02", "10:15"), bar_data)
        assert not any(r == "daily_loss_limit" for _, _, r in exits2)


# ===========================================================================
# Engine integration tests
# ===========================================================================

class TestDailyLossLimitIntegration:
    """End-to-end tests using BacktestEngine + ORB strategy + crafted bars."""

    # ---- no limit — baseline behaviour ----

    def test_no_limit_trade_executes_normally(self):
        bars = {"SPY": _make_day("2024-01-02", breakout_close=105.0, loss_close=105.0)}
        trades = _run_engine(bars, limit_pct=None)
        assert len(trades) == 1
        assert trades[0].symbol == "SPY"

    # ---- close_all ----

    def test_close_all_exits_with_daily_loss_limit_reason(self):
        # breakout at 105, then drop to 50 → −52 % day loss → close_all
        bars = {"SPY": _make_day("2024-01-02", breakout_close=105.0, loss_close=50.0)}
        trades = _run_engine(bars, limit_pct=10.0, action="close_all")
        reasons = [t.exit_reason for t in trades]
        assert "daily_loss_limit" in reasons

    def test_close_all_exit_reason_not_force_exit(self):
        bars = {"SPY": _make_day("2024-01-02", breakout_close=105.0, loss_close=50.0)}
        trades = _run_engine(bars, limit_pct=10.0, action="close_all")
        # The position should be closed by daily_loss_limit, not by force_exit
        assert trades[0].exit_reason == "daily_loss_limit"

    def test_close_all_exit_price_equals_loss_bar_close(self):
        bars = {"SPY": _make_day("2024-01-02", breakout_close=105.0, loss_close=50.0)}
        trades = _run_engine(bars, limit_pct=10.0, action="close_all")
        daily_loss_trade = next(t for t in trades if t.exit_reason == "daily_loss_limit")
        assert daily_loss_trade.exit_price == pytest.approx(50.0)

    # ---- block_new_entries ----

    def test_block_new_entries_does_not_force_close_open_position(self):
        # Position is open when limit is breached; with block_new_entries the
        # position must NOT be closed at the loss bar — it should stay open
        # and exit later (force_exit or stop_loss).
        bars = {"SPY": _make_day("2024-01-02", breakout_close=105.0, loss_close=50.0)}
        trades = _run_engine(bars, limit_pct=10.0, action="block_new_entries")
        # No daily_loss_limit exit
        assert all(t.exit_reason != "daily_loss_limit" for t in trades)
        # Position should exit via force_exit or stop_loss (stop=1, price=50>1)
        assert trades[0].exit_reason in ("force_exit", "stop_loss")

    # ---- limit resets next day ----

    def test_limit_resets_next_day_allows_new_entry(self):
        # Day 1: big loss → limit breached (close_all closes position)
        # Day 2: normal breakout → entry should be allowed
        day1 = _make_day("2024-01-02", breakout_close=105.0, loss_close=50.0)
        day2 = _make_day("2024-01-03", breakout_close=105.0, loss_close=105.0)
        bars = {"SPY": pd.concat([day1, day2])}
        trades = _run_engine(bars, limit_pct=10.0, action="close_all")

        # There should be two trades: one from day 1 (daily_loss_limit) and
        # one from day 2 (force_exit — no loss)
        trade_reasons = [t.exit_reason for t in trades]
        assert "daily_loss_limit" in trade_reasons  # day 1 closed by kill-switch
        assert any(r in ("force_exit", "stop_loss") for r in trade_reasons)  # day 2 normal

    def test_limit_resets_next_day_block_new_entries(self):
        # Day 1: position is opened, limit breached (block_new_entries), position
        # exits at force_exit. Day 2: new entry should be allowed.
        day1 = _make_day("2024-01-02", breakout_close=105.0, loss_close=50.0)
        day2 = _make_day("2024-01-03", breakout_close=105.0, loss_close=105.0)
        bars = {"SPY": pd.concat([day1, day2])}
        # Without limit: 2 trades.  With limit that blocks day-1 re-entries only:
        # still 2 trades (one per day) since block_new_entries does not close existing.
        trades = _run_engine(bars, limit_pct=10.0, action="block_new_entries")
        assert len(trades) == 2

    # ---- no breach when loss is within threshold ----

    def test_no_breach_when_loss_below_threshold(self):
        # Small loss bar (loss_close=103, entry≈105) → ~2 % drop → under 10 % limit
        bars = {"SPY": _make_day("2024-01-02", breakout_close=105.0, loss_close=103.0)}
        trades = _run_engine(bars, limit_pct=10.0, action="close_all")
        # No daily_loss_limit exit; position exits normally
        assert all(t.exit_reason != "daily_loss_limit" for t in trades)

    # ---- priority: daily_loss_limit beats stop_loss / force_exit on same bar ----

    def test_close_all_takes_priority_over_stop_loss_same_bar(self):
        # Set OR low=50 so stop_loss=50; loss bar has low=50 → stop would fire.
        # daily_loss_limit fires first and should be the only exit reason recorded.
        rm = _make_rm(limit_pct=5.0, action="close_all")
        portfolio = _make_portfolio(10_000.0)

        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 10_000.0

        # Open position with stop_loss=50
        portfolio.open_long("SPY", 95.0, _ts("2024-01-02", "10:05"), 0.95, stop_loss=50.0)

        # Bar where low=50 (hits stop) AND big drop triggers daily limit
        bar_data = {"SPY": {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}}
        exits = rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), bar_data)

        spy_exits = [(px, reason) for sym, px, reason in exits if sym == "SPY"]
        assert len(spy_exits) == 1, "Only one exit per symbol"
        assert spy_exits[0][1] == "daily_loss_limit"

    def test_close_all_takes_priority_over_force_exit_same_bar(self):
        # daily_loss_limit fires; force_exit time is also reached on the same bar.
        rm = RiskManager(
            force_exit_time="10:10",
            daily_loss_limit_pct=5.0,
            daily_loss_action="close_all",
        )
        portfolio = _make_portfolio(10_000.0)

        rm._current_day      = "2024-01-02"
        rm._day_start_equity = 10_000.0

        portfolio.open_long("SPY", 95.0, _ts("2024-01-02", "10:05"), 0.95, stop_loss=1.0)

        # Bar at 10:10 — matches force_exit_time AND causes big loss
        bar_data = {"SPY": {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}}
        exits = rm.check_exits(portfolio, _ts("2024-01-02", "10:10"), bar_data)

        spy_exits = [(px, reason) for sym, px, reason in exits if sym == "SPY"]
        assert len(spy_exits) == 1, "Only one exit per symbol"
        assert spy_exits[0][1] == "daily_loss_limit"

    def test_engine_no_duplicate_trades_when_daily_loss_and_stop_coincide(self):
        # OR low=50 so stop fires at 50; loss bar also drops to 50 → daily limit.
        # The engine should record exactly one trade (not two).
        day = _make_day("2024-01-02", or_low=50.0, breakout_close=105.0, loss_close=50.0)
        bars = {"SPY": day}
        trades = _run_engine(bars, limit_pct=10.0, action="close_all")
        spy_trades = [t for t in trades if t.symbol == "SPY"]
        assert len(spy_trades) == 1
        assert spy_trades[0].exit_reason == "daily_loss_limit"
