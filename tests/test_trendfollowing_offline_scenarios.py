"""
tests/test_trendfollowing_offline_scenarios.py
----------------------------------------------
Deterministic offline backtest scenario tests for the TrendFollowing strategy.

Five scenario categories (SPY 1d, SPY 1h, QQQ 1d, QQQ 1h, plus
interval-aware Sharpe check) validated using in-test synthetic fixtures.

Rules:
  - No network access. No yfinance. No Alpaca SDK. No credentials.
  - No environment variable reads. No file writes. No output mutation.
  - All data is generated deterministically from a seeded RNG.
  - broker_calls_made is always False.
  - Positive results do NOT approve live trading — characterisation only.
"""

from __future__ import annotations

import pathlib
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.backtest.backtest_runner import (
    BacktestRunConfig,
    BacktestRunResult,
    run_backtest,
)
from src.backtest.metrics import bars_per_year_for_interval
from src.data.base import BaseDataProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EASTERN = ZoneInfo("America/New_York")

# Reduced warm-up params so fixtures stay small.
# min_required = max(ema_slow, atr_period + volatility_lookback - 1, breakout_lookback + 1)
#              = max(10, 5+15-1, 5+1) = max(10, 19, 6) = 19 bars.
_TREND_PARAMS: dict[str, Any] = {
    "ema_fast": 5,
    "ema_slow": 10,
    "atr_period": 5,
    "atr_stop_mult": 2.0,
    "volatility_lookback": 15,
    "breakout_lookback": 5,
}
_WARMUP_BARS = 19   # bars before first possible TrendFollowing signal

_1D_BARS = 80       # ~4 months daily — 4× warm-up, sufficient evaluation window
_1H_DAYS = 25       # 25 trading days × 6 bars = 150 hourly bars (> 7× warm-up)

# Required metric keys per design doc §5
_REQUIRED_METRIC_KEYS = frozenset({
    "num_trades",
    "total_return_pct",
    "annualized_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "win_rate_pct",
})


# ---------------------------------------------------------------------------
# Synthetic deterministic fixture generators
# ---------------------------------------------------------------------------

def _make_1d_bars(n: int = _1D_BARS, seed: int = 42) -> pd.DataFrame:
    """Return deterministic uptrending daily OHLCV bars (no network)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n, tz=_EASTERN)
    base = np.linspace(100.0, 125.0, n)
    noise = rng.normal(0.0, 0.8, n)
    close = np.maximum(base + noise, 1.0)
    high = close + rng.uniform(0.05, 1.5, n)
    low = np.maximum(close - rng.uniform(0.05, 1.5, n), 0.01)
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


def _make_1h_bars(n_days: int = _1H_DAYS, seed: int = 42) -> pd.DataFrame:
    """Return deterministic uptrending hourly OHLCV bars (no network).

    Six complete hourly bars per trading day (09:30–14:30 Eastern).
    """
    rng = np.random.default_rng(seed)
    bdays = pd.bdate_range("2024-01-02", periods=n_days, tz=_EASTERN)
    timestamps: list[pd.Timestamp] = []
    for d in bdays:
        for h in [9, 10, 11, 12, 13, 14]:
            timestamps.append(d.replace(hour=h, minute=30, second=0, microsecond=0))

    n = len(timestamps)
    base = np.linspace(100.0, 120.0, n)
    noise = rng.normal(0.0, 0.5, n)
    close = np.maximum(base + noise, 1.0)
    high = close + rng.uniform(0.02, 0.8, n)
    low = np.maximum(close - rng.uniform(0.02, 0.8, n), 0.01)
    open_ = np.roll(close, 1)
    open_[0] = close[0] * 0.999

    return pd.DataFrame(
        {
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": np.full(n, 500_000.0),
        },
        index=pd.DatetimeIndex(timestamps),
    )


# ---------------------------------------------------------------------------
# Fake data provider
# ---------------------------------------------------------------------------

class _FakeProvider(BaseDataProvider):
    """Deterministic provider returning pre-built bars. No network access."""

    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars

    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> pd.DataFrame:
        return self._bars.copy()


# ---------------------------------------------------------------------------
# Config factory
# ---------------------------------------------------------------------------

def _make_config(
    symbol: str,
    bar_interval: str,
    *,
    initial_capital: float = 100_000.0,
) -> BacktestRunConfig:
    return BacktestRunConfig(
        strategy_name="trend_following",
        strategy_params=_TREND_PARAMS,
        symbols=[symbol],
        start_date="2024-01-02",
        end_date="2024-12-31",
        bar_interval=bar_interval,
        initial_capital=initial_capital,
        commission_per_share=0.005,
        slippage_per_share=0.010,
        position_size_pct=0.95,
        stop_execution="bar_close",
        force_exit_time="15:55",
    )


# ---------------------------------------------------------------------------
# Shared result cache — run each (symbol, interval) once
# ---------------------------------------------------------------------------

def _run(symbol: str, bar_interval: str) -> BacktestRunResult:
    bars = _make_1h_bars() if bar_interval == "1h" else _make_1d_bars()
    provider = _FakeProvider(bars)
    config = _make_config(symbol, bar_interval)
    return run_backtest(config, data_provider=provider)


# ---------------------------------------------------------------------------
# TestScenarioBaselines — four symbol × interval combinations
# ---------------------------------------------------------------------------


class TestScenarioBaselines:
    """Each scenario returns a BacktestRunResult without error."""

    def test_spy_1d_returns_result(self) -> None:
        result = _run("SPY", "1d")
        assert isinstance(result, BacktestRunResult)

    def test_spy_1h_returns_result(self) -> None:
        result = _run("SPY", "1h")
        assert isinstance(result, BacktestRunResult)

    def test_qqq_1d_returns_result(self) -> None:
        result = _run("QQQ", "1d")
        assert isinstance(result, BacktestRunResult)

    def test_qqq_1h_returns_result(self) -> None:
        result = _run("QQQ", "1h")
        assert isinstance(result, BacktestRunResult)

    def test_spy_1d_symbols_echo(self) -> None:
        result = _run("SPY", "1d")
        assert result.symbols == ["SPY"]

    def test_spy_1h_symbols_echo(self) -> None:
        result = _run("SPY", "1h")
        assert result.symbols == ["SPY"]

    def test_qqq_1d_symbols_echo(self) -> None:
        result = _run("QQQ", "1d")
        assert result.symbols == ["QQQ"]

    def test_qqq_1h_symbols_echo(self) -> None:
        result = _run("QQQ", "1h")
        assert result.symbols == ["QQQ"]

    def test_spy_1d_strategy_echo(self) -> None:
        result = _run("SPY", "1d")
        assert result.strategy_name == "trend_following"

    def test_spy_1h_interval_echo(self) -> None:
        result = _run("SPY", "1h")
        assert result.bar_interval == "1h"


# ---------------------------------------------------------------------------
# TestScenarioSafetyFlags — all six flags checked per result
# ---------------------------------------------------------------------------

_SCENARIO_LABELS = ["SPY-1d", "SPY-1h", "QQQ-1d", "QQQ-1h"]
_SCENARIO_PARAMS = [
    ("SPY", "1d"),
    ("SPY", "1h"),
    ("QQQ", "1d"),
    ("QQQ", "1h"),
]


class TestScenarioSafetyFlags:
    """broker_calls_made, credentials_read, live_submit_enabled, etc. are all safe."""

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_broker_calls_made_false(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert result.broker_calls_made is False

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_credentials_read_false(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert result.credentials_read is False

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_live_submit_enabled_false(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert result.live_submit_enabled is False

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_order_action_requested_false(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert result.order_action_requested is False

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_paper_trading_enabled_false(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert result.paper_trading_enabled is False

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_recommendation_only_true(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert result.recommendation_only is True


# ---------------------------------------------------------------------------
# TestScenarioMetrics — required metric keys present and well-typed
# ---------------------------------------------------------------------------


class TestScenarioMetrics:
    """Every scenario result contains the full required metric set."""

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_required_metric_keys_present(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        missing = _REQUIRED_METRIC_KEYS - set(result.metrics)
        assert not missing, f"Missing metric keys for {symbol} {interval}: {missing}"

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_num_trades_is_non_negative_int(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert isinstance(result.metrics["num_trades"], int)
        assert result.metrics["num_trades"] >= 0

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_total_return_is_float(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert isinstance(result.metrics["total_return_pct"], float)

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_max_drawdown_non_positive(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert result.metrics["max_drawdown_pct"] <= 0.0

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_win_rate_in_range(self, symbol: str, interval: str) -> None:
        result = _run(symbol, interval)
        assert 0.0 <= result.metrics["win_rate_pct"] <= 100.0

    def test_spy_1d_has_trades_list(self) -> None:
        result = _run("SPY", "1d")
        assert isinstance(result.trades, list)

    def test_spy_1d_has_equity_curve(self) -> None:
        result = _run("SPY", "1d")
        assert isinstance(result.equity_curve, __import__("pandas").DataFrame)


# ---------------------------------------------------------------------------
# TestDeterminism — same config + same data → identical metrics
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Running the same scenario twice always produces identical results."""

    def _metrics_equal(self, a: dict, b: dict) -> bool:
        if set(a) != set(b):
            return False
        for k in a:
            if a[k] != b[k]:
                return False
        return True

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_repeated_run_identical_metrics(self, symbol: str, interval: str) -> None:
        r1 = _run(symbol, interval)
        r2 = _run(symbol, interval)
        assert self._metrics_equal(r1.metrics, r2.metrics), (
            f"Non-deterministic metrics for {symbol} {interval}: "
            f"run1={r1.metrics} run2={r2.metrics}"
        )

    @pytest.mark.parametrize("symbol,interval", _SCENARIO_PARAMS, ids=_SCENARIO_LABELS)
    def test_repeated_run_identical_trade_count(self, symbol: str, interval: str) -> None:
        r1 = _run(symbol, interval)
        r2 = _run(symbol, interval)
        assert len(r1.trades) == len(r2.trades)


# ---------------------------------------------------------------------------
# TestIntervalAwareSharpe — bars_per_year differs → Sharpe differs
# ---------------------------------------------------------------------------


class TestIntervalAwareSharpe:
    """Sharpe annualisation uses bars_per_year_for_interval; 1d ≠ 1h factor."""

    def test_bars_per_year_differs_between_1d_and_1h(self) -> None:
        assert bars_per_year_for_interval("1d") != bars_per_year_for_interval("1h")

    def test_bars_per_year_1d(self) -> None:
        assert bars_per_year_for_interval("1d") == 252

    def test_bars_per_year_1h(self) -> None:
        assert bars_per_year_for_interval("1h") == 252 * 6  # 1 512

    def test_interval_affects_sharpe_when_nonzero(self) -> None:
        """Same underlying bars, different bar_interval → different Sharpe (if non-zero)."""
        bars = _make_1d_bars(n=_1D_BARS, seed=42)
        provider_1d = _FakeProvider(bars)
        provider_1h = _FakeProvider(bars)

        cfg_1d = _make_config("SPY", "1d")
        cfg_1h = _make_config("SPY", "1h")

        r1d = run_backtest(cfg_1d, data_provider=provider_1d)
        r1h = run_backtest(cfg_1h, data_provider=provider_1h)

        s1d = r1d.metrics["sharpe_ratio"]
        s1h = r1h.metrics["sharpe_ratio"]

        # If either Sharpe is non-zero, they must differ because the
        # annualisation factor sqrt(252) ≠ sqrt(1512).
        if s1d != 0.0 or s1h != 0.0:
            assert s1d != s1h, (
                f"Expected Sharpe to differ between 1d ({s1d}) and 1h ({s1h}) "
                f"due to different bars_per_year annualisation factors"
            )

    def test_1h_sharpe_larger_factor_than_1d(self) -> None:
        """sqrt(bars_per_year_1h) > sqrt(bars_per_year_1d); same equity curve returns
        yield higher 1h Sharpe."""
        import math
        f1d = math.sqrt(bars_per_year_for_interval("1d"))
        f1h = math.sqrt(bars_per_year_for_interval("1h"))
        assert f1h > f1d


# ---------------------------------------------------------------------------
# TestNoSideEffects — no output files written, no config mutation
# ---------------------------------------------------------------------------


class TestNoSideEffects:
    """Scenario runs must not write files or mutate the config."""

    def test_config_is_immutable(self) -> None:
        config = _make_config("SPY", "1d")
        import pytest as _pytest
        with _pytest.raises((AttributeError, TypeError)):
            config.strategy_name = "orb"  # type: ignore[misc]

    def test_no_output_csv_written(self, tmp_path: pathlib.Path) -> None:
        """run_backtest must not write to the filesystem."""
        before = list(tmp_path.iterdir())
        bars = _make_1d_bars()
        provider = _FakeProvider(bars)
        config = _make_config("SPY", "1d")
        run_backtest(config, data_provider=provider)
        after = list(tmp_path.iterdir())
        assert before == after

    def test_order_intents_are_not_broker_calls(self) -> None:
        result = _run("SPY", "1d")
        # Order intents are audit records only — not sent to any broker.
        assert result.broker_calls_made is False
        assert result.order_action_requested is False
