"""
tests/test_backtest_trade_schema.py
------------------------------------
Characterization tests: Trade schema and BacktestRunResult.trades field.

Locks in:
  - Trade field names, types, and constraints
  - pnl is computed in __post_init__ (not an __init__ parameter)
  - meta is not included in Trade.to_dict()
  - Known exit_reason values from the engine and risk manager
  - BacktestRunResult.trades is list[Trade]
  - All safety flags remain False (no broker calls, credentials, live trading)

Fixture: fast_ema_period=5, slow_ema_period=20, atr_period=5, atr_stop_mult=2.0,
volatility_lookback=10, breakout_lookback=3, seed=42, n=100 daily bars.
Warmup = max(slow_ema=20, atr+vol-1=14, breakout+1=4) = 20 bars.
Produces 13 closed LONG trades with exit_reason='session_end'.

Daily bar timestamps are 00:00 Eastern; force_exit_time=None (PR 10W guard —
1d+force_exit_time is blocked). session_end fires when a position is carried to
the next bar in the daily series.

No broker/API/credentials. No network. All tests are offline and deterministic.
No Alpaca SDK imported. No network library imported. No credentials read.
No orders submitted. No live trading approved. No paper trading approved.
"""

from __future__ import annotations

import dataclasses
import math
import pathlib
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.backtest.backtest_runner import BacktestRunConfig, BacktestRunResult, run_backtest
from src.backtest.trade import Trade
from src.data.base import BaseDataProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EASTERN = ZoneInfo("America/New_York")

_EXIT_REASON_ALLOWLIST = frozenset({
    "stop_loss",
    "force_exit",
    "session_end",
    "end_of_backtest",
    "daily_loss_limit",
})

# Small params so warmup = max(slow_ema=20, atr+vol-1=14, breakout+1=4) = 20 bars,
# leaving 80 signal bars from 100 total. Produces 13 trades (seed=42).
_SMALL_PARAMS: dict[str, Any] = {
    "fast_ema_period": 5,
    "slow_ema_period": 20,
    "atr_period": 5,
    "atr_stop_mult": 2.0,
    "volatility_lookback": 10,
    "breakout_lookback": 3,
}

_TO_DICT_EXPECTED_KEYS = frozenset({
    "symbol",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "shares",
    "commission",
    "direction",
    "exit_reason",
    "pnl",
})


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_daily_bars(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Deterministic uptrending daily OHLCV bars. No network."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n, tz=_EASTERN)
    base = np.linspace(100.0, 130.0, n)
    noise = rng.normal(0.0, 0.5, n)
    close = np.maximum(base + noise, 1.0)
    high = close + rng.uniform(0.05, 1.2, n)
    low = np.maximum(close - rng.uniform(0.05, 1.2, n), 0.01)
    open_ = np.roll(close, 1)
    open_[0] = close[0] * 0.999
    return pd.DataFrame(
        {
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


class _FakeProvider(BaseDataProvider):
    """Deterministic in-memory provider. No network access."""

    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return self._bars.copy()


def _make_config(params: dict[str, Any]) -> BacktestRunConfig:
    return BacktestRunConfig(
        strategy_name="trend_following",
        strategy_params=params,
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-12-31",
        bar_interval="1d",
        initial_capital=100_000.0,
        commission_per_share=0.005,
        slippage_per_share=0.010,
        position_size_pct=0.95,
        stop_execution="bar_close",
        force_exit_time=None,
    )


def _run_small_params() -> BacktestRunResult:
    bars = _make_daily_bars()
    provider = _FakeProvider(bars)
    config = _make_config(_SMALL_PARAMS)
    return run_backtest(config, data_provider=provider)


# ---------------------------------------------------------------------------
# TestTradeSchema — field names, types, constraints
# ---------------------------------------------------------------------------


class TestTradeSchema:
    """Trade field names, types, and computed attributes."""

    def test_fixture_produces_closed_trades(self) -> None:
        result = _run_small_params()
        assert len(result.trades) > 0, "Fixture must produce at least one closed trade"

    def test_fixture_trade_count_is_deterministic(self) -> None:
        result = _run_small_params()
        assert len(result.trades) == 13

    def test_trades_list_type(self) -> None:
        result = _run_small_params()
        assert isinstance(result.trades, list)

    def test_all_trades_are_trade_instances(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t, Trade)

    def test_symbol_is_str(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.symbol, str)

    def test_entry_time_is_timestamp(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.entry_time, pd.Timestamp)

    def test_exit_time_is_timestamp(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.exit_time, pd.Timestamp)

    def test_exit_time_not_before_entry_time(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert t.exit_time >= t.entry_time

    def test_entry_price_is_finite_float(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.entry_price, float)
            assert math.isfinite(t.entry_price)

    def test_exit_price_is_finite_float(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.exit_price, float)
            assert math.isfinite(t.exit_price)

    def test_shares_is_positive_float(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.shares, float)
            assert t.shares > 0.0

    def test_commission_is_non_negative_float(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.commission, float)
            assert t.commission >= 0.0

    def test_direction_is_str(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.direction, str)

    def test_direction_is_long_or_short(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert t.direction in {"LONG", "SHORT"}

    def test_fixture_directions_are_long(self) -> None:
        """Daily uptrending fixture with LONG-only strategy produces LONG trades."""
        result = _run_small_params()
        for t in result.trades:
            assert t.direction == "LONG"

    def test_exit_reason_is_str(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.exit_reason, str)

    def test_exit_reason_non_empty(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert len(t.exit_reason) > 0

    def test_pnl_is_finite_float(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.pnl, float)
            assert math.isfinite(t.pnl)

    def test_meta_is_dict(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.meta, dict)

    def test_pnl_not_in_init_fields(self) -> None:
        """pnl is computed in __post_init__, not an __init__ parameter."""
        init_fields = {f.name for f in dataclasses.fields(Trade) if f.init}
        assert "pnl" not in init_fields

    def test_pnl_is_a_dataclass_field(self) -> None:
        all_fields = {f.name for f in dataclasses.fields(Trade)}
        assert "pnl" in all_fields

    def test_meta_has_default_factory(self) -> None:
        """meta defaults to an empty dict via field(default_factory=dict)."""
        meta_f = next(f for f in dataclasses.fields(Trade) if f.name == "meta")
        assert meta_f.default_factory is not dataclasses.MISSING  # type: ignore[attr-defined]

    def test_pnl_formula_long(self) -> None:
        """pnl = (exit_price - entry_price) * shares - commission for LONG."""
        t = Trade(
            symbol="TEST",
            entry_time=pd.Timestamp("2024-01-02", tz=_EASTERN),
            exit_time=pd.Timestamp("2024-01-03", tz=_EASTERN),
            entry_price=100.0,
            exit_price=102.0,
            shares=10.0,
            commission=1.0,
            direction="LONG",
            exit_reason="session_end",
        )
        assert t.pnl == pytest.approx((102.0 - 100.0) * 10.0 - 1.0)

    def test_pnl_formula_short(self) -> None:
        """pnl = (entry_price - exit_price) * shares - commission for SHORT."""
        t = Trade(
            symbol="TEST",
            entry_time=pd.Timestamp("2024-01-02", tz=_EASTERN),
            exit_time=pd.Timestamp("2024-01-03", tz=_EASTERN),
            entry_price=100.0,
            exit_price=98.0,
            shares=10.0,
            commission=1.0,
            direction="SHORT",
            exit_reason="session_end",
        )
        assert t.pnl == pytest.approx((100.0 - 98.0) * 10.0 - 1.0)


# ---------------------------------------------------------------------------
# TestToDictKeys — to_dict() keys and meta exclusion
# ---------------------------------------------------------------------------


class TestToDictKeys:
    """Trade.to_dict() returns known keys; meta is not included."""

    def test_to_dict_returns_dict(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert isinstance(t.to_dict(), dict)

    def test_to_dict_has_all_expected_keys(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert _TO_DICT_EXPECTED_KEYS <= set(t.to_dict().keys())

    def test_to_dict_exact_keys(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert set(t.to_dict().keys()) == _TO_DICT_EXPECTED_KEYS

    def test_to_dict_meta_not_included(self) -> None:
        """meta is a research field; to_dict() does not include it."""
        result = _run_small_params()
        for t in result.trades:
            assert "meta" not in t.to_dict()

    def test_to_dict_pnl_matches_attribute(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert t.to_dict()["pnl"] == pytest.approx(t.pnl)

    def test_to_dict_symbol_matches_attribute(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert t.to_dict()["symbol"] == t.symbol

    def test_to_dict_direction_matches_attribute(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert t.to_dict()["direction"] == t.direction

    def test_to_dict_exit_reason_matches_attribute(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert t.to_dict()["exit_reason"] == t.exit_reason


# ---------------------------------------------------------------------------
# TestExitReasonValues — known exit_reason strings
# ---------------------------------------------------------------------------


class TestExitReasonValues:
    """exit_reason values are from the documented allowlist."""

    def test_allowlist_contains_stop_loss(self) -> None:
        assert "stop_loss" in _EXIT_REASON_ALLOWLIST

    def test_allowlist_contains_force_exit(self) -> None:
        assert "force_exit" in _EXIT_REASON_ALLOWLIST

    def test_allowlist_contains_session_end(self) -> None:
        assert "session_end" in _EXIT_REASON_ALLOWLIST

    def test_allowlist_contains_end_of_backtest(self) -> None:
        assert "end_of_backtest" in _EXIT_REASON_ALLOWLIST

    def test_allowlist_contains_daily_loss_limit(self) -> None:
        assert "daily_loss_limit" in _EXIT_REASON_ALLOWLIST

    def test_all_exit_reasons_in_allowlist(self) -> None:
        result = _run_small_params()
        for t in result.trades:
            assert t.exit_reason in _EXIT_REASON_ALLOWLIST, (
                f"Unexpected exit_reason {t.exit_reason!r}"
            )

    def test_fixture_produces_session_end_exits(self) -> None:
        """Daily-bar fixture with small params produces session_end exits."""
        result = _run_small_params()
        reasons = {t.exit_reason for t in result.trades}
        assert "session_end" in reasons

    def test_fixture_exit_reasons_deterministic(self) -> None:
        """Same fixture always yields the same set of exit reasons."""
        r1 = _run_small_params()
        r2 = _run_small_params()
        assert {t.exit_reason for t in r1.trades} == {t.exit_reason for t in r2.trades}


# ---------------------------------------------------------------------------
# TestBacktestRunResultTradesField — BacktestRunResult.trades and safety flags
# ---------------------------------------------------------------------------


class TestBacktestRunResultTradesField:
    """BacktestRunResult.trades type, contents, and safety flags."""

    def test_trades_field_exists(self) -> None:
        result = _run_small_params()
        assert hasattr(result, "trades")

    def test_trades_field_is_list(self) -> None:
        result = _run_small_params()
        assert isinstance(result.trades, list)

    def test_broker_calls_made_false(self) -> None:
        result = _run_small_params()
        assert result.broker_calls_made is False

    def test_credentials_read_false(self) -> None:
        result = _run_small_params()
        assert result.credentials_read is False

    def test_live_submit_enabled_false(self) -> None:
        result = _run_small_params()
        assert result.live_submit_enabled is False

    def test_order_action_requested_false(self) -> None:
        result = _run_small_params()
        assert result.order_action_requested is False

    def test_paper_trading_enabled_false(self) -> None:
        result = _run_small_params()
        assert result.paper_trading_enabled is False

    def test_recommendation_only_true(self) -> None:
        result = _run_small_params()
        assert result.recommendation_only is True

    def test_run_is_deterministic(self) -> None:
        r1 = _run_small_params()
        r2 = _run_small_params()
        assert len(r1.trades) == len(r2.trades)
        assert r1.metrics["total_return_pct"] == r2.metrics["total_return_pct"]

    def test_num_trades_matches_trades_list_length(self) -> None:
        result = _run_small_params()
        assert result.metrics["num_trades"] == len(result.trades)

    def test_all_trades_have_entry_and_exit_times(self) -> None:
        """All trades are closed round-trips with entry_time and exit_time."""
        result = _run_small_params()
        for t in result.trades:
            assert t.entry_time is not None
            assert t.exit_time is not None

    def test_result_has_equity_curve(self) -> None:
        result = _run_small_params()
        assert isinstance(result.equity_curve, pd.DataFrame)


# ---------------------------------------------------------------------------
# TestSafetySourceScan — source-level scan of characterized modules
# ---------------------------------------------------------------------------

_TRADE_SRC = pathlib.Path("src/backtest/trade.py")
_RUNNER_SRC = pathlib.Path("src/backtest/backtest_runner.py")


class TestSafetySourceScan:
    """Source-level scan: no network, broker, or credential imports in the
    modules being characterized (trade.py and backtest_runner.py)."""

    @pytest.fixture(scope="class")
    def trade_source(self) -> str:
        return _TRADE_SRC.read_text()

    @pytest.fixture(scope="class")
    def runner_source(self) -> str:
        return _RUNNER_SRC.read_text()

    def test_trade_no_alpaca_import(self, trade_source: str) -> None:
        assert "import alpaca" not in trade_source.lower()

    def test_trade_no_yfinance_import(self, trade_source: str) -> None:
        assert "import yfinance" not in trade_source.lower()

    def test_trade_no_requests_import(self, trade_source: str) -> None:
        assert "import requests" not in trade_source

    def test_trade_no_os_environ(self, trade_source: str) -> None:
        assert "os.environ" not in trade_source

    def test_runner_no_alpaca_import(self, runner_source: str) -> None:
        assert "import alpaca" not in runner_source.lower()

    def test_runner_no_yfinance_import(self, runner_source: str) -> None:
        assert "import yfinance" not in runner_source.lower()

    def test_runner_no_requests_import(self, runner_source: str) -> None:
        assert "import requests" not in runner_source

    def test_runner_no_os_environ(self, runner_source: str) -> None:
        assert "os.environ" not in runner_source
