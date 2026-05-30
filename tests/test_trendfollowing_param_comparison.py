"""
tests/test_trendfollowing_param_comparison.py
---------------------------------------------
Characterization tests: compare TrendFollowing strategy default params vs
cached_real_data_backtest_check._TREND_PARAMS.

Findings locked in by this file:
  - Strategy default fast_ema_period=20 (TrendFollowing() with no params → 20)
  - Checker _TREND_PARAMS fast_ema_period=10 (intentional — shorter period for
    broader signal characterization during offline backtest runs)
  - All other shared params match: slow_ema_period=50, atr_period=14,
    atr_stop_mult=2.0, volatility_lookback=50, breakout_lookback=5
  - Checker uses correct param key names (fast_ema_period / slow_ema_period),
    not the obsolete ema_fast / ema_slow keys

No parameter optimization. No best-params selection. No trading.
No broker/API/credentials. All tests are offline and deterministic.

No Alpaca SDK imported. No network library imported. No credentials read.
No orders submitted. No live trading approved. No paper trading approved.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.backtest.backtest_runner import BacktestRunConfig, BacktestRunResult, run_backtest
from src.data.base import BaseDataProvider
from src.strategy.trend_following import TrendFollowing
from src.tools.cached_real_data_backtest_check import _TREND_PARAMS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EASTERN = ZoneInfo("America/New_York")

# Strategy defaults as an explicit dict (matches TrendFollowing() no-arg defaults)
_STRATEGY_DEFAULTS: dict[str, Any] = {
    "fast_ema_period": 20,
    "slow_ema_period": 50,
    "atr_period": 14,
    "atr_stop_mult": 2.0,
    "volatility_lookback": 50,
    "breakout_lookback": 5,
}

# Required metric keys per backtest design
_REQUIRED_METRIC_KEYS = frozenset({
    "num_trades",
    "total_return_pct",
    "annualized_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "win_rate_pct",
})

# Fixture: 150 daily bars — well above 63-bar warmup for slow_ema=50, vol_lookback=50
# min_required = max(slow_ema=50, atr_period+vol_lookback-1=63, breakout_lookback+1=6) = 63
_FIXTURE_BARS = 150


# ---------------------------------------------------------------------------
# Synthetic deterministic fixture helpers
# ---------------------------------------------------------------------------


def _make_daily_bars(n: int = _FIXTURE_BARS, seed: int = 99) -> pd.DataFrame:
    """Return deterministic uptrending daily OHLCV bars. No network."""
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
        force_exit_time="15:55",
    )


def _run_with_params(params: dict[str, Any]) -> BacktestRunResult:
    bars = _make_daily_bars()
    provider = _FakeProvider(bars)
    config = _make_config(params)
    return run_backtest(config, data_provider=provider)


# ---------------------------------------------------------------------------
# TestStrategyDefaultParams — TrendFollowing() with no args uses fast_ema=20
# ---------------------------------------------------------------------------


class TestStrategyDefaultParams:
    """TrendFollowing() with no params uses fast_ema_period=20 as its default."""

    def test_strategy_default_fast_ema_period_is_20(self) -> None:
        strategy = TrendFollowing()
        assert strategy._fast_ema_period == 20

    def test_strategy_default_slow_ema_period_is_50(self) -> None:
        strategy = TrendFollowing()
        assert strategy._slow_ema_period == 50

    def test_strategy_constructed_with_no_params_accepted(self) -> None:
        strategy = TrendFollowing()
        assert strategy is not None

    def test_strategy_default_params_dict_accepted(self) -> None:
        strategy = TrendFollowing(params=_STRATEGY_DEFAULTS)
        assert strategy._fast_ema_period == 20


# ---------------------------------------------------------------------------
# TestCheckerParams — _TREND_PARAMS has fast_ema_period=10, correct key names
# ---------------------------------------------------------------------------


class TestCheckerParams:
    """_TREND_PARAMS has fast_ema_period=10 and uses correct key names."""

    def test_checker_fast_ema_period_is_10(self) -> None:
        assert _TREND_PARAMS["fast_ema_period"] == 10

    def test_checker_params_accepted_by_trendfollowing(self) -> None:
        strategy = TrendFollowing(params=_TREND_PARAMS)
        assert strategy._fast_ema_period == 10

    def test_checker_params_no_obsolete_ema_fast_key(self) -> None:
        assert "ema_fast" not in _TREND_PARAMS

    def test_checker_params_no_obsolete_ema_slow_key(self) -> None:
        assert "ema_slow" not in _TREND_PARAMS

    def test_checker_params_has_fast_ema_period_key(self) -> None:
        assert "fast_ema_period" in _TREND_PARAMS

    def test_checker_params_has_slow_ema_period_key(self) -> None:
        assert "slow_ema_period" in _TREND_PARAMS


# ---------------------------------------------------------------------------
# TestSharedDefaults — params that are intentionally identical
# ---------------------------------------------------------------------------


class TestSharedDefaults:
    """Params other than fast_ema_period are identical between checker and strategy defaults."""

    def test_slow_ema_period_shared(self) -> None:
        strategy = TrendFollowing()
        assert strategy._slow_ema_period == _TREND_PARAMS["slow_ema_period"] == 50

    def test_atr_period_shared(self) -> None:
        strategy = TrendFollowing()
        assert strategy._atr_period == _TREND_PARAMS["atr_period"] == 14

    def test_atr_stop_mult_shared(self) -> None:
        strategy = TrendFollowing()
        assert strategy._atr_stop_mult == _TREND_PARAMS["atr_stop_mult"] == 2.0

    def test_volatility_lookback_shared(self) -> None:
        strategy = TrendFollowing()
        assert strategy._volatility_lookback == _TREND_PARAMS["volatility_lookback"] == 50

    def test_breakout_lookback_shared(self) -> None:
        strategy = TrendFollowing()
        assert strategy._breakout_lookback == _TREND_PARAMS["breakout_lookback"] == 5


# ---------------------------------------------------------------------------
# TestParamDivergence — fast_ema_period intentionally differs (10 vs 20)
# ---------------------------------------------------------------------------


class TestParamDivergence:
    """The divergence in fast_ema_period is intentional and locked in by this test."""

    def test_fast_ema_period_diverges(self) -> None:
        strategy = TrendFollowing()
        assert strategy._fast_ema_period != _TREND_PARAMS["fast_ema_period"]

    def test_checker_fast_ema_less_than_strategy_default(self) -> None:
        strategy = TrendFollowing()
        assert _TREND_PARAMS["fast_ema_period"] < strategy._fast_ema_period

    def test_expected_divergence_values(self) -> None:
        assert _TREND_PARAMS["fast_ema_period"] == 10
        strategy = TrendFollowing()
        assert strategy._fast_ema_period == 20


# ---------------------------------------------------------------------------
# TestSyntheticComparison — run both param sets; record aggregate outputs only
# ---------------------------------------------------------------------------


class TestSyntheticComparison:
    """Deterministic synthetic backtest comparing strategy defaults vs checker params.

    Fixture: 150 uptrending daily bars (seed=99), SPY, $100,000 initial capital.
    Warmup: max(slow_ema=50, atr_period+vol_lookback-1=63, breakout_lookback+1=6) = 63 bars.
    Both param sets share the same 63-bar warmup (slow_ema=50 unchanged).

    No optimization. No trading. No broker/API/credentials.
    Only aggregate metric fields are recorded; no raw bar values.
    """

    def test_default_params_run_completes(self) -> None:
        result = _run_with_params(_STRATEGY_DEFAULTS)
        assert isinstance(result, BacktestRunResult)

    def test_checker_params_run_completes(self) -> None:
        result = _run_with_params(_TREND_PARAMS)
        assert isinstance(result, BacktestRunResult)

    def test_default_params_required_metrics_present(self) -> None:
        result = _run_with_params(_STRATEGY_DEFAULTS)
        missing = _REQUIRED_METRIC_KEYS - set(result.metrics)
        assert not missing, f"Missing metric keys: {missing}"

    def test_checker_params_required_metrics_present(self) -> None:
        result = _run_with_params(_TREND_PARAMS)
        missing = _REQUIRED_METRIC_KEYS - set(result.metrics)
        assert not missing, f"Missing metric keys: {missing}"

    def test_default_params_num_trades_non_negative(self) -> None:
        result = _run_with_params(_STRATEGY_DEFAULTS)
        assert result.metrics["num_trades"] >= 0

    def test_checker_params_num_trades_non_negative(self) -> None:
        result = _run_with_params(_TREND_PARAMS)
        assert result.metrics["num_trades"] >= 0

    def test_default_params_safety_flags(self) -> None:
        result = _run_with_params(_STRATEGY_DEFAULTS)
        assert result.broker_calls_made is False
        assert result.credentials_read is False
        assert result.order_action_requested is False
        assert result.live_submit_enabled is False
        assert result.paper_trading_enabled is False

    def test_checker_params_safety_flags(self) -> None:
        result = _run_with_params(_TREND_PARAMS)
        assert result.broker_calls_made is False
        assert result.credentials_read is False
        assert result.order_action_requested is False
        assert result.live_submit_enabled is False
        assert result.paper_trading_enabled is False

    def test_both_runs_produce_equity_curve(self) -> None:
        r_defaults = _run_with_params(_STRATEGY_DEFAULTS)
        r_checker = _run_with_params(_TREND_PARAMS)
        assert isinstance(r_defaults.equity_curve, pd.DataFrame)
        assert isinstance(r_checker.equity_curve, pd.DataFrame)

    def test_both_runs_deterministic(self) -> None:
        r1 = _run_with_params(_STRATEGY_DEFAULTS)
        r2 = _run_with_params(_STRATEGY_DEFAULTS)
        assert r1.metrics["num_trades"] == r2.metrics["num_trades"]
        assert r1.metrics["total_return_pct"] == r2.metrics["total_return_pct"]

    def test_recommendation_only_both(self) -> None:
        r_defaults = _run_with_params(_STRATEGY_DEFAULTS)
        r_checker = _run_with_params(_TREND_PARAMS)
        assert r_defaults.recommendation_only is True
        assert r_checker.recommendation_only is True
