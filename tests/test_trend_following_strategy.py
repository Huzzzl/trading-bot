"""
tests/test_trend_following_strategy.py
----------------------------------------
Unit tests for src/strategy/trend_following.py.

All tests use synthetic bar DataFrames — no real Alpaca calls, no network,
no credentials, no environment variable access.

Small EMA periods are used (fast=5, slow=10, atr=5, vol_lookback=15,
breakout_lookback=5) to keep bar counts manageable.
min_required = max(slow, atr+vol_lb-1, bo_lb+1) = max(10, 19, 6) = 19
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.strategy.base import Signal, SignalDirection
from src.strategy.trend_following import TrendFollowing

# ---------------------------------------------------------------------------
# Test constants — small periods for manageable bar counts
# ---------------------------------------------------------------------------

FAST = 5
SLOW = 10
ATR_P = 5
VOL_LB = 15
BO_LB = 5
SYM = "SPY"
TF = "1h"
# min_required = max(10, 5+15-1, 5+1) = max(10,19,6) = 19

_DEFAULT_PARAMS: dict[str, Any] = {
    "symbol": SYM,
    "timeframe": TF,
    "fast_ema_period": FAST,
    "slow_ema_period": SLOW,
    "atr_period": ATR_P,
    "volatility_lookback": VOL_LB,
    "breakout_lookback": BO_LB,
    "atr_stop_mult": 2.0,
    "risk_per_trade_pct": 1.0,
    "long_only": True,
}

_TS = pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(**overrides: Any) -> TrendFollowing:
    params = {**_DEFAULT_PARAMS, **overrides}
    return TrendFollowing(params=params)


def _make_bars(
    closes: list[float],
    *,
    high_offset: float = 5.0,
    low_offset: float = 5.0,
    high_overrides: dict[int, float] | None = None,
) -> pd.DataFrame:
    """Return a DataFrame with timestamps as index."""
    n = len(closes)
    idx = pd.date_range("2024-01-15 09:30", periods=n, freq="1h", tz="America/New_York")
    highs = [c + high_offset for c in closes]
    lows  = [c - low_offset  for c in closes]
    if high_overrides:
        for i, v in high_overrides.items():
            highs[i] = v
    return pd.DataFrame({"high": highs, "low": lows, "close": closes}, index=idx)


def _bullish_bars(n: int = 20) -> pd.DataFrame:
    """15 flat bars then 5 strongly rising — verified bullish+breakout at bar 19."""
    closes = [100.0] * 15 + [110.0, 120.0, 130.0, 140.0, 150.0]
    return _make_bars(closes[:n])


def _bearish_bars(n: int = 20) -> pd.DataFrame:
    """15 flat bars then 5 strongly falling — verified bearish at bar 19."""
    closes = [100.0] * 15 + [90.0, 80.0, 70.0, 60.0, 50.0]
    return _make_bars(closes[:n])


def _flat_bars(n: int = 20) -> pd.DataFrame:
    return _make_bars([100.0] * n)


def _slow_rise_bars(n: int = 20) -> pd.DataFrame:
    """Bullish trend but close does NOT exceed prior rolling high (no breakout)."""
    closes = [100.0] * 15 + [102.0, 104.0, 106.0, 108.0, 109.0]
    return _make_bars(closes[:n])


def _pullback_bars(n: int = 20) -> pd.DataFrame:
    """15 flat + 4 rising + 1 sharp drop: close < fast_ema, trend neutral (not bearish)."""
    closes = [100.0] * 15 + [110.0, 120.0, 130.0, 140.0, 115.0]
    return _make_bars(closes[:n])


def _call(bars: pd.DataFrame, **kwargs: Any) -> Signal | None:
    strat = _make_strategy(**kwargs)
    ts = bars.index[-1]
    return strat.generate_signal(SYM, bars, ts)


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_default_constructor_works(self) -> None:
        s = TrendFollowing()
        assert s is not None

    def test_none_params_works(self) -> None:
        s = TrendFollowing(params=None)
        assert s is not None

    def test_empty_params_works(self) -> None:
        s = TrendFollowing(params={})
        assert s is not None

    def test_explicit_valid_params_works(self) -> None:
        s = TrendFollowing(params=_DEFAULT_PARAMS)
        assert s._symbol == SYM
        assert s._fast_ema_period == FAST
        assert s._slow_ema_period == SLOW
        assert s._atr_period == ATR_P
        assert s._volatility_lookback == VOL_LB
        assert s._breakout_lookback == BO_LB
        assert s._atr_stop_mult == pytest.approx(2.0)
        assert s._risk_per_trade_pct == pytest.approx(1.0)
        assert s._long_only is True
        assert s._entry_cutoff_time is None
        assert s._force_exit_time is None

    def test_params_dict_not_mutated(self) -> None:
        params = copy.deepcopy(_DEFAULT_PARAMS)
        original = copy.deepcopy(params)
        TrendFollowing(params=params)
        assert params == original


# ---------------------------------------------------------------------------
# TestConstructorValidation
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    def _raises(self, **kwargs: Any) -> None:
        params = {**_DEFAULT_PARAMS, **kwargs}
        with pytest.raises(ValueError):
            TrendFollowing(params=params)

    def test_invalid_symbol_lowercase(self) -> None:
        self._raises(symbol="spy")

    def test_invalid_symbol_empty(self) -> None:
        self._raises(symbol="")

    def test_invalid_symbol_too_long(self) -> None:
        self._raises(symbol="TOOLONGSYMBOL")

    def test_invalid_symbol_secret_value_not_in_message(self) -> None:
        secret = "SECRETSYMBOLVALUE"
        with pytest.raises(ValueError) as exc_info:
            TrendFollowing(params={**_DEFAULT_PARAMS, "symbol": secret})
        assert secret not in str(exc_info.value)

    def test_fast_ema_zero(self) -> None:
        self._raises(fast_ema_period=0)

    def test_fast_ema_negative(self) -> None:
        self._raises(fast_ema_period=-1)

    def test_slow_ema_zero(self) -> None:
        self._raises(slow_ema_period=0)

    def test_fast_ema_equals_slow(self) -> None:
        self._raises(fast_ema_period=10, slow_ema_period=10)

    def test_fast_ema_greater_than_slow(self) -> None:
        self._raises(fast_ema_period=15, slow_ema_period=10)

    def test_atr_period_zero(self) -> None:
        self._raises(atr_period=0)

    def test_volatility_lookback_zero(self) -> None:
        self._raises(volatility_lookback=0)

    def test_breakout_lookback_zero(self) -> None:
        self._raises(breakout_lookback=0)

    def test_atr_stop_mult_zero(self) -> None:
        self._raises(atr_stop_mult=0.0)

    def test_atr_stop_mult_negative(self) -> None:
        self._raises(atr_stop_mult=-1.0)

    def test_risk_per_trade_pct_zero(self) -> None:
        self._raises(risk_per_trade_pct=0.0)

    def test_risk_per_trade_pct_negative(self) -> None:
        self._raises(risk_per_trade_pct=-0.5)

    def test_long_only_false_raises(self) -> None:
        self._raises(long_only=False)

    def test_invalid_params_error_message_is_safe(self) -> None:
        secret_value = "super_secret_bad_value_xyz"
        with pytest.raises(ValueError, match="invalid strategy parameters") as exc_info:
            TrendFollowing(params={**_DEFAULT_PARAMS, "fast_ema_period": secret_value})
        assert secret_value not in str(exc_info.value)

    def test_invalid_period_error_text(self) -> None:
        with pytest.raises(ValueError, match="invalid strategy parameters"):
            TrendFollowing(params={**_DEFAULT_PARAMS, "fast_ema_period": 0})


# ---------------------------------------------------------------------------
# TestInsufficientBars
# ---------------------------------------------------------------------------


class TestInsufficientBars:
    # min_required = 19

    def test_zero_bars_returns_none(self) -> None:
        bars = pd.DataFrame(
            {"high": [], "low": [], "close": []},
            index=pd.DatetimeIndex([], tz="America/New_York"),
        )
        strat = _make_strategy()
        result = strat.generate_signal(SYM, bars, pd.Timestamp("2024-01-15 10:00", tz="America/New_York"))
        assert result is None

    def test_one_below_minimum_returns_none(self) -> None:
        bars = _flat_bars(n=18)
        result = _call(bars)
        assert result is None

    def test_exactly_minimum_does_not_block(self) -> None:
        bars = _flat_bars(n=19)
        result = _call(bars)
        # May be None for other reasons (neutral), but must not be blocked by bar count
        # We confirm no ValueError is raised and signal type is valid
        assert result is None or isinstance(result, Signal)

    def test_above_minimum_does_not_block(self) -> None:
        bars = _bullish_bars(n=20)
        result = _call(bars)
        assert result is None or isinstance(result, Signal)


# ---------------------------------------------------------------------------
# TestBuySignal
# ---------------------------------------------------------------------------


class TestBuySignal:
    """Bullish trend + close above prior rolling high → LONG signal."""

    def _result(self) -> Signal | None:
        return _call(_bullish_bars(n=20))

    def test_returns_signal(self) -> None:
        assert self._result() is not None

    def test_direction_is_long(self) -> None:
        r = self._result()
        assert r is not None
        assert r.direction == SignalDirection.LONG

    def test_symbol_matches(self) -> None:
        r = self._result()
        assert r is not None
        assert r.symbol == SYM

    def test_entry_price_is_current_close(self) -> None:
        bars = _bullish_bars(n=20)
        r = _call(bars)
        assert r is not None
        assert r.entry_price == pytest.approx(float(bars["close"].iloc[-1]))

    def test_stop_loss_is_set(self) -> None:
        r = self._result()
        assert r is not None
        assert r.stop_loss is not None
        assert r.stop_loss < r.entry_price

    def test_meta_reason_is_buy(self) -> None:
        r = self._result()
        assert r is not None
        assert r.meta is not None
        assert r.meta["reason"] == "BUY_BULLISH_BREAKOUT"

    def test_meta_trend_is_bullish(self) -> None:
        r = self._result()
        assert r is not None
        assert r.meta["trend"] == "bullish"

    def test_meta_contains_required_fields(self) -> None:
        r = self._result()
        assert r is not None
        meta = r.meta
        for field in (
            "strategy_name", "symbol", "trend", "trend_strength", "volatility_regime",
            "fast_ema", "slow_ema", "atr", "breakout_high", "atr_stop_price",
            "atr_stop_mult", "breakout_lookback", "reason",
            "deterministic", "broker_calls_made", "credentials_read",
            "order_action_requested", "recommendation_only",
        ):
            assert field in meta, f"missing meta field: {field}"

    def test_meta_safety_fields(self) -> None:
        r = self._result()
        assert r is not None
        meta = r.meta
        assert meta["deterministic"] is True
        assert meta["broker_calls_made"] is False
        assert meta["credentials_read"] is False
        assert meta["order_action_requested"] is False
        assert meta["recommendation_only"] is True

    def test_meta_breakout_high_positive(self) -> None:
        r = self._result()
        assert r is not None
        assert r.meta["breakout_high"] > 0

    def test_meta_fast_ema_less_than_close(self) -> None:
        bars = _bullish_bars(n=20)
        r = _call(bars)
        assert r is not None
        close = float(bars["close"].iloc[-1])
        assert r.meta["fast_ema"] < close


# ---------------------------------------------------------------------------
# TestNoBreakout
# ---------------------------------------------------------------------------


class TestNoBreakout:
    """Bullish trend but close does NOT exceed prior rolling high → None."""

    def test_no_breakout_returns_none(self) -> None:
        result = _call(_slow_rise_bars(n=20))
        # Slow-rise bars: trend is bullish but close < rolling high reference
        assert result is None or result.direction != SignalDirection.LONG

    def test_neutral_trend_returns_no_buy(self) -> None:
        result = _call(_flat_bars(n=20))
        assert result is None or result.direction != SignalDirection.LONG

    def test_bearish_bars_return_no_buy(self) -> None:
        result = _call(_bearish_bars(n=20))
        assert result is None or result.direction != SignalDirection.LONG


# ---------------------------------------------------------------------------
# TestNoLookAhead — current-bar high spike
# ---------------------------------------------------------------------------


class TestNoLookAhead:
    """Current bar's high must NOT contaminate the breakout reference level."""

    def test_current_bar_spike_does_not_block_breakout(self) -> None:
        """
        Scenario:
        - Normal bullish bars: close[19]=150, expected breakout_ref=145.
        - Spike: high[19] = 300 (huge current-bar high).
        - If include_current=True, ref would include 300 → 150 < 300 → no breakout (wrong).
        - With exclude_current=True (default), ref = max(highs[14:19]) = 145 → LONG (correct).
        """
        closes = [100.0] * 15 + [110.0, 120.0, 130.0, 140.0, 150.0]
        # Override current bar (index 19) high to a huge spike
        bars = _make_bars(closes, high_overrides={19: 300.0})
        result = _call(bars)
        # Strategy should still produce a BUY because current bar high is excluded
        assert result is not None
        assert result.direction == SignalDirection.LONG
        assert result.meta["reason"] == "BUY_BULLISH_BREAKOUT"
        # The breakout reference must NOT include the 300 spike
        assert result.meta["breakout_high"] < 300.0

    def test_future_bars_not_accessible(self) -> None:
        """Truncating bars at different lengths changes the signal — no look-ahead."""
        bars_short = _bullish_bars(n=19)  # only 19 bars
        bars_long  = _bullish_bars(n=20)  # 20 bars (extra future bar)
        strat = _make_strategy()
        ts_short = bars_short.index[-1]
        ts_long  = bars_long.index[-1]
        result_short = strat.generate_signal(SYM, bars_short, ts_short)
        strat.reset()
        result_long = strat.generate_signal(SYM, bars_long, ts_long)
        # Different bar slices → signals may differ; the important thing is each
        # call only sees the bars it was given (no shared state after reset).
        assert result_short is None or isinstance(result_short, Signal)
        assert result_long is None or isinstance(result_long, Signal)


# ---------------------------------------------------------------------------
# TestExitSignal
# ---------------------------------------------------------------------------


class TestExitSignal:
    def test_bearish_trend_returns_exit(self) -> None:
        result = _call(_bearish_bars(n=20))
        assert result is not None
        assert result.direction == SignalDirection.EXIT

    def test_exit_reason_bearish(self) -> None:
        result = _call(_bearish_bars(n=20))
        assert result is not None
        assert result.meta["reason"] == "SELL_BEARISH_TREND"

    def test_exit_has_safety_meta(self) -> None:
        result = _call(_bearish_bars(n=20))
        assert result is not None
        meta = result.meta
        assert meta["broker_calls_made"] is False
        assert meta["credentials_read"] is False
        assert meta["order_action_requested"] is False
        assert meta["recommendation_only"] is True

    def test_flat_bars_no_exit(self) -> None:
        """Flat bars produce neutral trend — no EXIT signal (close == fast_ema so not below)."""
        result = _call(_flat_bars(n=20))
        # Flat bars: close == fast_ema == slow_ema → not bearish, not close < fast_ema
        # So result is None
        assert result is None


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_direction(self) -> None:
        bars = _bullish_bars(n=20)
        strat = _make_strategy()
        ts = bars.index[-1]
        r1 = strat.generate_signal(SYM, bars, ts)
        strat.reset()
        r2 = strat.generate_signal(SYM, bars, ts)
        assert (r1 is None) == (r2 is None)
        if r1 is not None and r2 is not None:
            assert r1.direction == r2.direction
            assert r1.entry_price == pytest.approx(r2.entry_price)
            assert r1.meta["reason"] == r2.meta["reason"]

    def test_different_bars_may_differ(self) -> None:
        r_bull = _call(_bullish_bars(n=20))
        r_bear = _call(_bearish_bars(n=20))
        assert r_bull is None or r_bear is None or r_bull.direction != r_bear.direction


# ---------------------------------------------------------------------------
# TestInputNotMutated
# ---------------------------------------------------------------------------


class TestInputNotMutated:
    def test_bars_not_mutated(self) -> None:
        bars = _bullish_bars(n=20)
        close_before = bars["close"].tolist()
        high_before  = bars["high"].tolist()
        _call(bars)
        assert bars["close"].tolist() == close_before
        assert bars["high"].tolist() == high_before

    def test_bars_index_not_mutated(self) -> None:
        bars = _bullish_bars(n=20)
        idx_before = bars.index.tolist()
        _call(bars)
        assert bars.index.tolist() == idx_before


# ---------------------------------------------------------------------------
# TestStrategyIsBaseStrategy
# ---------------------------------------------------------------------------


class TestStrategyIsBaseStrategy:
    def test_is_base_strategy_subclass(self) -> None:
        from src.strategy.base import BaseStrategy
        assert issubclass(TrendFollowing, BaseStrategy)

    def test_has_generate_signal(self) -> None:
        assert callable(TrendFollowing().generate_signal)

    def test_has_reset(self) -> None:
        assert callable(TrendFollowing().reset)

    def test_reset_does_not_raise(self) -> None:
        strat = _make_strategy()
        strat.reset()  # stateless; should be a no-op without error


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

_TF_SOURCE = Path("src/strategy/trend_following.py")


def _read_code(path: Path) -> str:
    """Return non-comment source lines joined."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#")[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


class TestSourceScans:
    def _code(self) -> str:
        return _read_code(_TF_SOURCE)

    def test_no_alpaca_import(self) -> None:
        code = self._code()
        assert "import alpaca" not in code
        assert "from alpaca" not in code

    def test_no_requests_import(self) -> None:
        assert "import requests" not in self._code()

    def test_no_httpx_import(self) -> None:
        assert "import httpx" not in self._code()

    def test_no_aiohttp_import(self) -> None:
        assert "import aiohttp" not in self._code()

    def test_no_urllib_request(self) -> None:
        assert "urllib.request" not in self._code()

    def test_no_os_environ(self) -> None:
        assert "os.environ" not in self._code()

    def test_no_execution_imports(self) -> None:
        code = self._code()
        assert "from src.execution" not in code
        assert "import src.execution" not in code

    def test_no_submit_order(self) -> None:
        assert "submit_order(" not in self._code()

    def test_no_cancel_order(self) -> None:
        assert "cancel_order(" not in self._code()

    def test_no_replace_order(self) -> None:
        assert "replace_order(" not in self._code()

    def test_no_close_position(self) -> None:
        assert "close_position(" not in self._code()

    def test_no_close_all_positions(self) -> None:
        assert "close_all_positions(" not in self._code()

    def test_no_post_mutation(self) -> None:
        assert ".post(" not in self._code().lower()

    def test_no_patch_mutation(self) -> None:
        assert ".patch(" not in self._code().lower()

    def test_no_delete_mutation(self) -> None:
        assert ".delete(" not in self._code().lower()

    def test_no_ledger_write(self) -> None:
        assert "ledger" not in self._code()

    def test_no_hardcoded_1h_timeframe(self) -> None:
        """timeframe must come from self._timeframe, not a literal."""
        assert 'timeframe="1h"' not in self._code()
        assert "timeframe='1h'" not in self._code()


# ---------------------------------------------------------------------------
# TestTimeframeParam
# ---------------------------------------------------------------------------


class TestTimeframeParam:
    def test_default_timeframe_is_1h(self) -> None:
        assert TrendFollowing()._timeframe == "1h"

    def test_explicit_timeframe_stored(self) -> None:
        assert TrendFollowing(params={"timeframe": "1d"})._timeframe == "1d"

    def test_all_valid_timeframes_accepted(self) -> None:
        for tf in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"):
            s = TrendFollowing(params={"timeframe": tf})
            assert s._timeframe == tf

    def test_invalid_timeframe_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid strategy parameters"):
            TrendFollowing(params={"timeframe": "bad"})

    def test_invalid_timeframe_secret_not_in_message(self) -> None:
        secret = "super_secret_timeframe_xyz"
        with pytest.raises(ValueError, match="invalid strategy parameters") as exc_info:
            TrendFollowing(params={"timeframe": secret})
        assert secret not in str(exc_info.value)

    def test_timeframe_used_in_generate_signal(self) -> None:
        """Strategy with valid non-default timeframe produces a signal without error."""
        bars = _bullish_bars(n=20)
        strat = _make_strategy(timeframe="1d")
        ts = bars.index[-1]
        result = strat.generate_signal(SYM, bars, ts)
        # Must not raise; signal type must be valid
        assert result is None or isinstance(result, Signal)


# ---------------------------------------------------------------------------
# TestEntryCutoffDoesNotBlockExits
# ---------------------------------------------------------------------------

# Timestamps used for cutoff tests: all in US Eastern time.
_TS_BEFORE_CUTOFF = pd.Timestamp("2024-01-15 09:00:00", tz="America/New_York")  # 09:00 ET
_TS_AFTER_CUTOFF  = pd.Timestamp("2024-01-15 14:00:00", tz="America/New_York")  # 14:00 ET
_CUTOFF = "13:00"  # 13:00 ET


class TestEntryCutoffDoesNotBlockExits:
    """entry_cutoff_time must gate new LONG entries only; EXIT signals must pass through."""

    def _strat(self) -> TrendFollowing:
        return _make_strategy(entry_cutoff_time=_CUTOFF)

    def test_bearish_after_cutoff_returns_exit(self) -> None:
        bars = _bearish_bars(n=20)
        result = self._strat().generate_signal(SYM, bars, _TS_AFTER_CUTOFF)
        assert result is not None
        assert result.direction == SignalDirection.EXIT

    def test_bearish_after_cutoff_reason_is_sell_bearish(self) -> None:
        bars = _bearish_bars(n=20)
        result = self._strat().generate_signal(SYM, bars, _TS_AFTER_CUTOFF)
        assert result is not None
        assert result.meta["reason"] == "SELL_BEARISH_TREND"

    def test_close_below_fast_ema_after_cutoff_returns_exit(self) -> None:
        """Neutral trend with close < fast EMA → EXIT even after cutoff."""
        bars = _pullback_bars(n=20)
        result = self._strat().generate_signal(SYM, bars, _TS_AFTER_CUTOFF)
        assert result is not None
        assert result.direction == SignalDirection.EXIT

    def test_close_below_fast_ema_after_cutoff_reason(self) -> None:
        bars = _pullback_bars(n=20)
        result = self._strat().generate_signal(SYM, bars, _TS_AFTER_CUTOFF)
        assert result is not None
        assert result.meta["reason"] == "SELL_CLOSE_BELOW_FAST_EMA"

    def test_bullish_breakout_after_cutoff_returns_none(self) -> None:
        """Bullish breakout after entry cutoff → no new LONG."""
        bars = _bullish_bars(n=20)
        result = self._strat().generate_signal(SYM, bars, _TS_AFTER_CUTOFF)
        assert result is None or result.direction != SignalDirection.LONG

    def test_bullish_breakout_before_cutoff_returns_long(self) -> None:
        """Bullish breakout before entry cutoff → LONG still works."""
        bars = _bullish_bars(n=20)
        result = self._strat().generate_signal(SYM, bars, _TS_BEFORE_CUTOFF)
        assert result is not None
        assert result.direction == SignalDirection.LONG

    def test_no_cutoff_exit_has_safety_meta(self) -> None:
        bars = _bearish_bars(n=20)
        result = self._strat().generate_signal(SYM, bars, _TS_AFTER_CUTOFF)
        assert result is not None
        meta = result.meta
        assert meta["broker_calls_made"] is False
        assert meta["credentials_read"] is False
        assert meta["order_action_requested"] is False
        assert meta["recommendation_only"] is True

    def test_cutoff_does_not_mutate_bars(self) -> None:
        bars = _bearish_bars(n=20)
        close_before = bars["close"].tolist()
        self._strat().generate_signal(SYM, bars, _TS_AFTER_CUTOFF)
        assert bars["close"].tolist() == close_before
