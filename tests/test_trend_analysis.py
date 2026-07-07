"""
tests/test_trend_analysis.py
-----------------------------
Unit tests for src/analysis/trend.py.

All tests use synthetic bar DataFrames — no real Alpaca calls, no network,
no credentials, no environment variable access.

Small periods are used throughout (fast=5, slow=10, atr=5, vol_lookback=15)
so bar counts remain manageable while exercising the same code paths as the
production defaults.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from src.analysis.trend import TrendState, classify_trend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAST = 5
SLOW = 10
ATR_P = 5
VOL_LB = 15
SYM = "SPY"
TF = "1h"
# min_required = max(SLOW, ATR_P + VOL_LB - 1) = max(10, 19) = 19


def _make_bars(
    closes: list[float],
    *,
    high_offset: float = 5.0,
    low_offset: float = 5.0,
) -> pd.DataFrame:
    """Return a DataFrame with high/low/close columns."""
    closes_s = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "high": closes_s + high_offset,
            "low": closes_s - low_offset,
            "close": closes_s,
        }
    )


def _rising(n: int = 20, start: float = 100.0) -> pd.DataFrame:
    """Monotonically rising closes."""
    return _make_bars([start + i for i in range(n)])


def _falling(n: int = 20, start: float = 119.0) -> pd.DataFrame:
    """Monotonically falling closes."""
    return _make_bars([start - i for i in range(n)])


def _flat(n: int = 20, value: float = 100.0) -> pd.DataFrame:
    """All closes identical."""
    return _make_bars([value] * n)


def _call(**kwargs) -> TrendState:
    """Call classify_trend with test-friendly defaults."""
    bars = kwargs.pop("bars", _rising())
    return classify_trend(
        bars,
        symbol=kwargs.pop("symbol", SYM),
        timeframe=kwargs.pop("timeframe", TF),
        fast_ema_period=kwargs.pop("fast_ema_period", FAST),
        slow_ema_period=kwargs.pop("slow_ema_period", SLOW),
        atr_period=kwargs.pop("atr_period", ATR_P),
        volatility_lookback=kwargs.pop("volatility_lookback", VOL_LB),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TestTrendStateDefaults — frozen dataclass and safety fields
# ---------------------------------------------------------------------------


class TestTrendStateDefaults:
    def test_frozen(self):
        s = _call()
        with pytest.raises((AttributeError, TypeError)):
            s.trend = "different"  # type: ignore[misc]

    def test_deterministic_always_true(self):
        assert _call().deterministic is True

    def test_broker_calls_made_always_false(self):
        assert _call().broker_calls_made is False

    def test_credentials_read_always_false(self):
        assert _call().credentials_read is False

    def test_order_action_requested_always_false(self):
        assert _call().order_action_requested is False

    def test_reason_codes_is_tuple(self):
        assert isinstance(_call().reason_codes, tuple)

    def test_fast_ema_is_float(self):
        s = _call()
        if not math.isnan(s.fast_ema):
            assert isinstance(s.fast_ema, float)

    def test_slow_ema_is_float(self):
        s = _call()
        if not math.isnan(s.slow_ema):
            assert isinstance(s.slow_ema, float)

    def test_atr_is_float(self):
        s = _call()
        if not math.isnan(s.atr):
            assert isinstance(s.atr, float)


# ---------------------------------------------------------------------------
# TestValidationInvalidSymbol
# ---------------------------------------------------------------------------


class TestValidationInvalidSymbol:
    def test_empty_string(self):
        s = _call(symbol="")
        assert "INVALID_SYMBOL" in s.reason_codes

    def test_lowercase(self):
        s = _call(symbol="spy")
        assert "INVALID_SYMBOL" in s.reason_codes

    def test_too_long(self):
        s = _call(symbol="TOOLONGSYMBOL")
        assert "INVALID_SYMBOL" in s.reason_codes

    def test_spaces(self):
        s = _call(symbol="S P Y")
        assert "INVALID_SYMBOL" in s.reason_codes

    def test_none_type(self):
        s = _call(symbol=None)  # type: ignore[arg-type]
        assert "INVALID_SYMBOL" in s.reason_codes

    def test_blocked_state_on_invalid_symbol(self):
        s = _call(symbol="")
        assert s.trend == "unknown"
        assert s.strength == "unknown"
        assert s.volatility_regime == "unknown"
        assert math.isnan(s.fast_ema)
        assert math.isnan(s.slow_ema)
        assert math.isnan(s.atr)

    def test_valid_symbol_with_dot(self):
        s = _call(symbol="BRK.B")
        assert "INVALID_SYMBOL" not in s.reason_codes

    def test_valid_symbol_with_hyphen(self):
        s = _call(symbol="BF-B")
        assert "INVALID_SYMBOL" not in s.reason_codes


# ---------------------------------------------------------------------------
# TestValidationInvalidTimeframe
# ---------------------------------------------------------------------------


class TestValidationInvalidTimeframe:
    def test_empty_string(self):
        s = _call(timeframe="")
        assert "INVALID_TIMEFRAME" in s.reason_codes

    def test_unknown_interval(self):
        s = _call(timeframe="7h")
        assert "INVALID_TIMEFRAME" in s.reason_codes

    def test_uppercase(self):
        s = _call(timeframe="1H")
        assert "INVALID_TIMEFRAME" in s.reason_codes

    def test_none_type(self):
        s = _call(timeframe=None)  # type: ignore[arg-type]
        assert "INVALID_TIMEFRAME" in s.reason_codes

    def test_all_valid_timeframes(self):
        for tf in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"):
            s = _call(timeframe=tf)
            assert "INVALID_TIMEFRAME" not in s.reason_codes, f"rejected valid timeframe {tf}"

    def test_invalid_after_valid_symbol(self):
        s = _call(symbol="SPY", timeframe="bad")
        assert "INVALID_SYMBOL" not in s.reason_codes
        assert "INVALID_TIMEFRAME" in s.reason_codes

    def test_trend_unknown_on_invalid_timeframe(self):
        s = _call(timeframe="bad")
        assert s.trend == "unknown"


# ---------------------------------------------------------------------------
# TestValidationInvalidPeriod
# ---------------------------------------------------------------------------


class TestValidationInvalidPeriod:
    def test_fast_ema_zero(self):
        s = _call(fast_ema_period=0)
        assert "INVALID_PERIOD" in s.reason_codes

    def test_fast_ema_negative(self):
        s = _call(fast_ema_period=-1)
        assert "INVALID_PERIOD" in s.reason_codes

    def test_slow_ema_zero(self):
        s = _call(slow_ema_period=0, fast_ema_period=0)
        assert "INVALID_PERIOD" in s.reason_codes

    def test_atr_period_zero(self):
        s = _call(atr_period=0)
        assert "INVALID_PERIOD" in s.reason_codes

    def test_volatility_lookback_zero(self):
        s = _call(volatility_lookback=0)
        assert "INVALID_PERIOD" in s.reason_codes

    def test_all_periods_one_is_valid(self):
        bars = _flat(n=3)
        s = classify_trend(
            bars,
            symbol=SYM,
            timeframe=TF,
            fast_ema_period=1,
            slow_ema_period=2,
            atr_period=1,
            volatility_lookback=1,
        )
        assert "INVALID_PERIOD" not in s.reason_codes

    def test_trend_unknown_on_invalid_period(self):
        s = _call(fast_ema_period=0)
        assert s.trend == "unknown"


# ---------------------------------------------------------------------------
# TestValidationInvalidPeriodOrder
# ---------------------------------------------------------------------------


class TestValidationInvalidPeriodOrder:
    def test_fast_equals_slow(self):
        s = _call(fast_ema_period=10, slow_ema_period=10)
        assert "INVALID_PERIOD_ORDER" in s.reason_codes

    def test_fast_greater_than_slow(self):
        s = _call(fast_ema_period=15, slow_ema_period=10)
        assert "INVALID_PERIOD_ORDER" in s.reason_codes

    def test_fast_less_than_slow_is_valid(self):
        s = _call(fast_ema_period=5, slow_ema_period=10)
        assert "INVALID_PERIOD_ORDER" not in s.reason_codes

    def test_invalid_period_checked_before_order(self):
        s = _call(fast_ema_period=0, slow_ema_period=0)
        assert "INVALID_PERIOD" in s.reason_codes
        assert "INVALID_PERIOD_ORDER" not in s.reason_codes

    def test_trend_unknown_on_invalid_period_order(self):
        s = _call(fast_ema_period=10, slow_ema_period=10)
        assert s.trend == "unknown"


# ---------------------------------------------------------------------------
# TestValidationMissingColumns
# ---------------------------------------------------------------------------


class TestValidationMissingColumns:
    def test_missing_close(self):
        bars = pd.DataFrame({"high": [101.0] * 20, "low": [99.0] * 20})
        s = _call(bars=bars)
        assert "MISSING_REQUIRED_COLUMNS" in s.reason_codes

    def test_missing_high(self):
        bars = pd.DataFrame({"low": [99.0] * 20, "close": [100.0] * 20})
        s = _call(bars=bars)
        assert "MISSING_REQUIRED_COLUMNS" in s.reason_codes

    def test_missing_low(self):
        bars = pd.DataFrame({"high": [101.0] * 20, "close": [100.0] * 20})
        s = _call(bars=bars)
        assert "MISSING_REQUIRED_COLUMNS" in s.reason_codes

    def test_empty_dataframe(self):
        bars = pd.DataFrame()
        s = _call(bars=bars)
        assert "MISSING_REQUIRED_COLUMNS" in s.reason_codes

    def test_all_columns_present_clears_gate(self):
        s = _call(bars=_flat(n=20))
        assert "MISSING_REQUIRED_COLUMNS" not in s.reason_codes

    def test_trend_unknown_on_missing_columns(self):
        bars = pd.DataFrame({"high": [101.0] * 20, "low": [99.0] * 20})
        s = _call(bars=bars)
        assert s.trend == "unknown"


# ---------------------------------------------------------------------------
# TestValidationInsufficientBars
# ---------------------------------------------------------------------------


class TestValidationInsufficientBars:
    # min_required = max(SLOW, ATR_P + VOL_LB - 1) = max(10, 19) = 19

    def test_zero_bars(self):
        bars = pd.DataFrame({"high": [], "low": [], "close": []})
        s = _call(bars=bars)
        assert "INSUFFICIENT_BARS" in s.reason_codes

    def test_one_below_minimum(self):
        bars = _flat(n=18)  # 18 < 19
        s = _call(bars=bars)
        assert "INSUFFICIENT_BARS" in s.reason_codes

    def test_exactly_minimum(self):
        bars = _flat(n=19)  # 19 == 19
        s = _call(bars=bars)
        assert "INSUFFICIENT_BARS" not in s.reason_codes

    def test_above_minimum(self):
        bars = _flat(n=25)
        s = _call(bars=bars)
        assert "INSUFFICIENT_BARS" not in s.reason_codes

    def test_trend_unknown_on_insufficient_bars(self):
        bars = _flat(n=18)
        s = _call(bars=bars)
        assert s.trend == "unknown"


# ---------------------------------------------------------------------------
# TestBullishTrend
# ---------------------------------------------------------------------------


class TestBullishTrend:
    """Rising bars: close > fast_ema > slow_ema → TREND_BULLISH."""

    def _result(self) -> TrendState:
        return _call(bars=_rising(n=20, start=100.0))

    def test_trend_is_bullish(self):
        assert self._result().trend == "bullish"

    def test_reason_code_trend_bullish(self):
        assert "TREND_BULLISH" in self._result().reason_codes

    def test_no_bearish_code(self):
        assert "TREND_BEARISH" not in self._result().reason_codes

    def test_no_neutral_code(self):
        assert "TREND_NEUTRAL" not in self._result().reason_codes

    def test_fast_ema_above_slow_ema(self):
        s = self._result()
        assert s.fast_ema > s.slow_ema

    def test_close_above_fast_ema(self):
        s = self._result()
        close = _rising(n=20, start=100.0)["close"].iloc[-1]
        assert close > s.fast_ema

    def test_fast_ema_positive(self):
        assert self._result().fast_ema > 0

    def test_slow_ema_positive(self):
        assert self._result().slow_ema > 0


# ---------------------------------------------------------------------------
# TestBearishTrend
# ---------------------------------------------------------------------------


class TestBearishTrend:
    """Falling bars: close < fast_ema < slow_ema → TREND_BEARISH."""

    def _result(self) -> TrendState:
        return _call(bars=_falling(n=20, start=119.0))

    def test_trend_is_bearish(self):
        assert self._result().trend == "bearish"

    def test_reason_code_trend_bearish(self):
        assert "TREND_BEARISH" in self._result().reason_codes

    def test_no_bullish_code(self):
        assert "TREND_BULLISH" not in self._result().reason_codes

    def test_no_neutral_code(self):
        assert "TREND_NEUTRAL" not in self._result().reason_codes

    def test_fast_ema_below_slow_ema(self):
        s = self._result()
        assert s.fast_ema < s.slow_ema

    def test_close_below_fast_ema(self):
        s = self._result()
        close = _falling(n=20, start=119.0)["close"].iloc[-1]
        assert close < s.fast_ema


# ---------------------------------------------------------------------------
# TestNeutralTrend
# ---------------------------------------------------------------------------


class TestNeutralTrend:
    """Flat bars → EMAs converge to close → TREND_NEUTRAL."""

    def _result(self) -> TrendState:
        return _call(bars=_flat(n=20))

    def test_trend_is_neutral(self):
        assert self._result().trend == "neutral"

    def test_reason_code_neutral(self):
        assert "TREND_NEUTRAL" in self._result().reason_codes

    def test_no_bullish_code(self):
        assert "TREND_BULLISH" not in self._result().reason_codes

    def test_no_bearish_code(self):
        assert "TREND_BEARISH" not in self._result().reason_codes

    def test_ema_values_populated(self):
        s = self._result()
        assert not math.isnan(s.fast_ema)
        assert not math.isnan(s.slow_ema)

    def test_neutral_is_not_unknown(self):
        # Valid flat data → computed neutral, not a blocked unknown
        assert self._result().trend == "neutral"
        assert self._result().trend != "unknown"


# ---------------------------------------------------------------------------
# TestStrength
# ---------------------------------------------------------------------------


class TestStrength:
    def test_strong_on_wide_spread(self):
        # Rising series produces fast_ema >> slow_ema → spread ≥ 0.5%
        s = _call(bars=_rising(n=20, start=100.0))
        assert s.strength == "strong"
        assert "STRENGTH_STRONG" in s.reason_codes

    def test_weak_on_flat(self):
        # Flat series → both EMAs converge to same value → spread = 0
        s = _call(bars=_flat(n=20))
        assert s.strength == "weak"
        assert "STRENGTH_WEAK" in s.reason_codes

    def test_no_both_strength_codes(self):
        s = _call(bars=_rising(n=20, start=100.0))
        assert not ("STRENGTH_STRONG" in s.reason_codes and "STRENGTH_WEAK" in s.reason_codes)

    def test_strength_not_unknown_on_valid_bars(self):
        s = _call(bars=_rising(n=20, start=100.0))
        assert s.strength != "unknown"


# ---------------------------------------------------------------------------
# TestVolatilityRegime
# ---------------------------------------------------------------------------


class TestVolatilityRegime:
    def _high_vol_bars(self) -> pd.DataFrame:
        """20 calm bars (TR≈2) then 5 spike bars (TR≈40)."""
        flat_closes = [100.0] * 20
        spike_closes = [100.0] * 5
        closes = flat_closes + spike_closes
        highs = [c + 1 for c in flat_closes] + [c + 20 for c in spike_closes]
        lows = [c - 1 for c in flat_closes] + [c - 20 for c in spike_closes]
        return pd.DataFrame({"high": highs, "low": lows, "close": closes})

    def _low_vol_bars(self) -> pd.DataFrame:
        """20 volatile bars (TR≈30) then 5 calm bars (TR≈2)."""
        vol_closes = [100.0] * 20
        calm_closes = [100.0] * 5
        closes = vol_closes + calm_closes
        highs = [c + 15 for c in vol_closes] + [c + 1 for c in calm_closes]
        lows = [c - 15 for c in vol_closes] + [c - 1 for c in calm_closes]
        return pd.DataFrame({"high": highs, "low": lows, "close": closes})

    def _normal_vol_bars(self) -> pd.DataFrame:
        """20 consistent bars."""
        return _flat(n=20)

    def test_high_volatility_detected(self):
        s = _call(bars=self._high_vol_bars())
        assert s.volatility_regime == "high", f"expected high, got {s.volatility_regime}"
        assert "VOLATILITY_HIGH" in s.reason_codes

    def test_low_volatility_detected(self):
        s = _call(bars=self._low_vol_bars())
        assert s.volatility_regime == "low", f"expected low, got {s.volatility_regime}"
        assert "VOLATILITY_LOW" in s.reason_codes

    def test_normal_volatility_detected(self):
        s = _call(bars=self._normal_vol_bars())
        assert s.volatility_regime == "normal", f"expected normal, got {s.volatility_regime}"
        assert "VOLATILITY_NORMAL" in s.reason_codes

    def test_only_one_volatility_code(self):
        vol_codes = {"VOLATILITY_HIGH", "VOLATILITY_LOW", "VOLATILITY_NORMAL", "VOLATILITY_UNKNOWN"}
        for bars in (self._high_vol_bars(), self._low_vol_bars(), self._normal_vol_bars()):
            s = _call(bars=bars)
            found = [c for c in s.reason_codes if c in vol_codes]
            assert len(found) == 1, f"expected exactly 1 volatility code, got {found}"

    def test_atr_populated_on_valid_bars(self):
        s = _call(bars=self._normal_vol_bars())
        assert not math.isnan(s.atr)
        assert s.atr > 0


# ---------------------------------------------------------------------------
# TestReasonCodes
# ---------------------------------------------------------------------------


class TestReasonCodes:
    def test_bullish_has_exactly_three_codes(self):
        # TREND_BULLISH + STRENGTH_STRONG/WEAK + VOLATILITY_*
        s = _call(bars=_rising(n=20))
        assert len(s.reason_codes) == 3

    def test_bearish_has_exactly_three_codes(self):
        s = _call(bars=_falling(n=20))
        assert len(s.reason_codes) == 3

    def test_neutral_has_exactly_three_codes(self):
        s = _call(bars=_flat(n=20))
        assert len(s.reason_codes) == 3

    def test_validation_failure_has_exactly_one_code(self):
        s = _call(symbol="")
        assert len(s.reason_codes) == 1

    def test_codes_are_strings(self):
        for code in _call(bars=_rising(n=20)).reason_codes:
            assert isinstance(code, str)


# ---------------------------------------------------------------------------
# TestGateOrder
# ---------------------------------------------------------------------------


class TestGateOrder:
    """Earlier gates take priority over later ones."""

    def test_invalid_symbol_before_invalid_timeframe(self):
        s = _call(symbol="", timeframe="bad")
        assert "INVALID_SYMBOL" in s.reason_codes
        assert "INVALID_TIMEFRAME" not in s.reason_codes

    def test_invalid_timeframe_before_invalid_period(self):
        s = _call(timeframe="bad", fast_ema_period=0)
        assert "INVALID_TIMEFRAME" in s.reason_codes
        assert "INVALID_PERIOD" not in s.reason_codes

    def test_invalid_period_before_period_order(self):
        s = _call(fast_ema_period=0, slow_ema_period=0)
        assert "INVALID_PERIOD" in s.reason_codes
        assert "INVALID_PERIOD_ORDER" not in s.reason_codes

    def test_period_order_before_missing_columns(self):
        bars = pd.DataFrame({"open": [100.0] * 20})
        s = _call(bars=bars, fast_ema_period=10, slow_ema_period=5)
        assert "INVALID_PERIOD_ORDER" in s.reason_codes
        assert "MISSING_REQUIRED_COLUMNS" not in s.reason_codes

    def test_missing_columns_before_insufficient_bars(self):
        bars = pd.DataFrame({"close": [100.0] * 5})  # only close, missing high/low
        s = _call(bars=bars)
        assert "MISSING_REQUIRED_COLUMNS" in s.reason_codes
        assert "INSUFFICIENT_BARS" not in s.reason_codes


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output_rising(self):
        bars = _rising(n=20)
        r1 = _call(bars=bars)
        r2 = _call(bars=bars)
        assert r1.trend == r2.trend
        assert r1.strength == r2.strength
        assert r1.volatility_regime == r2.volatility_regime
        assert r1.fast_ema == r2.fast_ema
        assert r1.slow_ema == r2.slow_ema
        assert r1.atr == r2.atr
        assert r1.reason_codes == r2.reason_codes

    def test_same_input_same_output_flat(self):
        bars = _flat(n=20)
        r1 = _call(bars=bars)
        r2 = _call(bars=bars)
        assert r1 == r2

    def test_different_bars_different_output(self):
        r_rising = _call(bars=_rising(n=20))
        r_falling = _call(bars=_falling(n=20))
        assert r_rising.trend != r_falling.trend


# ---------------------------------------------------------------------------
# TestInputNotMutated
# ---------------------------------------------------------------------------


class TestInputNotMutated:
    def test_bars_dataframe_not_mutated(self):
        bars = _rising(n=20)
        original_close = bars["close"].tolist()
        original_high = bars["high"].tolist()
        original_low = bars["low"].tolist()
        _call(bars=bars)
        assert bars["close"].tolist() == original_close
        assert bars["high"].tolist() == original_high
        assert bars["low"].tolist() == original_low

    def test_bars_index_not_mutated(self):
        bars = _rising(n=20)
        original_index = bars.index.tolist()
        _call(bars=bars)
        assert bars.index.tolist() == original_index


# ---------------------------------------------------------------------------
# TestSafetyFields
# ---------------------------------------------------------------------------


class TestSafetyFields:
    def _states(self) -> list[TrendState]:
        return [
            _call(bars=_rising(n=20)),
            _call(bars=_falling(n=20)),
            _call(bars=_flat(n=20)),
            _call(symbol=""),            # validation failure
            _call(bars=_flat(n=5)),      # insufficient bars
        ]

    def test_deterministic_always_true(self):
        for s in self._states():
            assert s.deterministic is True

    def test_broker_calls_made_always_false(self):
        for s in self._states():
            assert s.broker_calls_made is False

    def test_credentials_read_always_false(self):
        for s in self._states():
            assert s.credentials_read is False

    def test_order_action_requested_always_false(self):
        for s in self._states():
            assert s.order_action_requested is False


# ---------------------------------------------------------------------------
# TestSymbolAndTimeframePassthrough
# ---------------------------------------------------------------------------


class TestSymbolAndTimeframePassthrough:
    def test_symbol_in_result(self):
        s = _call(symbol="AAPL")
        assert s.symbol == "AAPL"

    def test_timeframe_in_result(self):
        s = _call(timeframe="4h")
        assert s.timeframe == "4h"

    def test_symbol_in_blocked_result(self):
        s = _call(symbol="SPY", timeframe="bad")
        assert s.symbol == "SPY"


# ---------------------------------------------------------------------------
# TestSourceScans — no forbidden imports or calls
# ---------------------------------------------------------------------------

_ANALYSIS_SOURCE = Path("src/analysis/trend.py")
_ANALYSIS_INIT = Path("src/analysis/__init__.py")


class TestSourceScans:
    def _read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_no_alpaca_import_in_trend(self):
        src = self._read(_ANALYSIS_SOURCE)
        assert "import alpaca" not in src
        assert "from alpaca" not in src

    def test_no_requests_import_in_trend(self):
        src = self._read(_ANALYSIS_SOURCE)
        assert "import requests" not in src

    def test_no_httpx_import_in_trend(self):
        src = self._read(_ANALYSIS_SOURCE)
        assert "import httpx" not in src

    def test_no_aiohttp_import_in_trend(self):
        src = self._read(_ANALYSIS_SOURCE)
        assert "import aiohttp" not in src

    def test_no_urllib_request_in_trend(self):
        src = self._read(_ANALYSIS_SOURCE)
        assert "urllib.request" not in src

    def test_no_os_environ_in_trend(self):
        src = self._read(_ANALYSIS_SOURCE)
        assert "os.environ" not in src

    def test_no_broker_calls_in_trend(self):
        src = self._read(_ANALYSIS_SOURCE)
        for forbidden in ("submit_order", "cancel_order", "close_position"):
            assert forbidden not in src, f"found forbidden call: {forbidden}"

    def test_no_alpaca_import_in_init(self):
        src = self._read(_ANALYSIS_INIT)
        assert "import alpaca" not in src
        assert "from alpaca" not in src
