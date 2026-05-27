"""
Tests for src/indicators package.

All tests are offline-only — no broker calls, no credentials, no network
access, no Alpaca import, no config reads. All indicator functions are pure,
deterministic, and no-look-ahead.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from src.indicators.moving_average import ema, sma
from src.indicators.volatility import atr, true_range
from src.indicators.trend import (
    breakout_above,
    breakout_below,
    rolling_high,
    rolling_low,
)
from src.indicators import (
    sma as pkg_sma,
    ema as pkg_ema,
    true_range as pkg_true_range,
    atr as pkg_atr,
    rolling_high as pkg_rolling_high,
    rolling_low as pkg_rolling_low,
    breakout_above as pkg_breakout_above,
    breakout_below as pkg_breakout_below,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _series(*values: float, name: str = "v") -> pd.Series:
    return pd.Series(list(values), dtype=float, name=name)


def _isnan(x: float) -> bool:
    return math.isnan(x)


# ---------------------------------------------------------------------------
# TestPackageExports
# ---------------------------------------------------------------------------

class TestPackageExports:
    def test_sma_exported(self) -> None:
        assert pkg_sma is sma

    def test_ema_exported(self) -> None:
        assert pkg_ema is ema

    def test_true_range_exported(self) -> None:
        assert pkg_true_range is true_range

    def test_atr_exported(self) -> None:
        assert pkg_atr is atr

    def test_rolling_high_exported(self) -> None:
        assert pkg_rolling_high is rolling_high

    def test_rolling_low_exported(self) -> None:
        assert pkg_rolling_low is rolling_low

    def test_breakout_above_exported(self) -> None:
        assert pkg_breakout_above is breakout_above

    def test_breakout_below_exported(self) -> None:
        assert pkg_breakout_below is breakout_below


# ---------------------------------------------------------------------------
# TestSma
# ---------------------------------------------------------------------------

class TestSma:
    def test_basic_known_values(self) -> None:
        s = _series(1.0, 2.0, 3.0, 4.0, 5.0)
        result = sma(s, window=3)
        assert _isnan(result.iloc[0])
        assert _isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_window_1_returns_same_values(self) -> None:
        s = _series(10.0, 20.0, 30.0)
        result = sma(s, window=1)
        assert result.iloc[0] == pytest.approx(10.0)
        assert result.iloc[1] == pytest.approx(20.0)
        assert result.iloc[2] == pytest.approx(30.0)

    def test_nan_until_window_filled(self) -> None:
        s = _series(1.0, 2.0, 3.0, 4.0)
        result = sma(s, window=3)
        assert _isnan(result.iloc[0])
        assert _isnan(result.iloc[1])
        assert not _isnan(result.iloc[2])

    def test_preserves_index(self) -> None:
        idx = pd.Index(["a", "b", "c", "d"])
        s = pd.Series([1.0, 2.0, 3.0, 4.0], index=idx)
        result = sma(s, window=2)
        assert list(result.index) == list(idx)

    def test_does_not_mutate_input(self) -> None:
        s = _series(1.0, 2.0, 3.0)
        original = s.copy()
        sma(s, window=2)
        pd.testing.assert_series_equal(s, original)

    def test_returns_series(self) -> None:
        result = sma(_series(1.0, 2.0, 3.0), window=2)
        assert isinstance(result, pd.Series)

    def test_invalid_window_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid window"):
            sma(_series(1.0, 2.0), window=0)

    def test_invalid_window_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid window"):
            sma(_series(1.0, 2.0), window=-1)

    def test_window_larger_than_series(self) -> None:
        s = _series(1.0, 2.0)
        result = sma(s, window=5)
        assert _isnan(result.iloc[0])
        assert _isnan(result.iloc[1])


# ---------------------------------------------------------------------------
# TestEma
# ---------------------------------------------------------------------------

class TestEma:
    def test_basic_sanity_matches_pandas(self) -> None:
        s = _series(1.0, 2.0, 3.0, 4.0, 5.0)
        expected = s.ewm(span=3, adjust=False).mean()
        result = ema(s, span=3)
        pd.testing.assert_series_equal(result, expected)

    def test_span_1_returns_same_values(self) -> None:
        s = _series(10.0, 20.0, 30.0)
        result = ema(s, span=1)
        assert result.iloc[0] == pytest.approx(10.0)
        assert result.iloc[1] == pytest.approx(20.0)
        assert result.iloc[2] == pytest.approx(30.0)

    def test_no_nan_with_adjust_false(self) -> None:
        s = _series(1.0, 2.0, 3.0, 4.0)
        result = ema(s, span=3)
        assert not result.isnull().any()

    def test_preserves_index(self) -> None:
        idx = pd.date_range("2024-01-01", periods=4, freq="h")
        s = pd.Series([1.0, 2.0, 3.0, 4.0], index=idx)
        result = ema(s, span=2)
        assert list(result.index) == list(idx)

    def test_does_not_mutate_input(self) -> None:
        s = _series(1.0, 2.0, 3.0)
        original = s.copy()
        ema(s, span=2)
        pd.testing.assert_series_equal(s, original)

    def test_returns_series(self) -> None:
        result = ema(_series(1.0, 2.0, 3.0), span=2)
        assert isinstance(result, pd.Series)

    def test_invalid_span_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid span"):
            ema(_series(1.0, 2.0), span=0)

    def test_invalid_span_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid span"):
            ema(_series(1.0, 2.0), span=-3)


# ---------------------------------------------------------------------------
# TestTrueRange
# ---------------------------------------------------------------------------

class TestTrueRange:
    def _make_ohlc(self) -> tuple[pd.Series, pd.Series, pd.Series]:
        # bar 0: H=12, L=10, C=11  → TR = 2 (no prev close)
        # bar 1: H=13, L=11, C=12  → prev_close=11, HL=2, H-PC=2, L-PC=0 → TR=2
        # bar 2: H=15, L=10, C=13  → prev_close=12, HL=5, H-PC=3, L-PC=2 → TR=5
        high  = _series(12.0, 13.0, 15.0)
        low   = _series(10.0, 11.0, 10.0)
        close = _series(11.0, 12.0, 13.0)
        return high, low, close

    def test_first_row_equals_high_minus_low(self) -> None:
        high, low, close = self._make_ohlc()
        result = true_range(high, low, close)
        assert result.iloc[0] == pytest.approx(2.0)

    def test_uses_previous_close(self) -> None:
        high, low, close = self._make_ohlc()
        result = true_range(high, low, close)
        # bar 2: max(5, 3, 2) = 5
        assert result.iloc[2] == pytest.approx(5.0)

    def test_large_gap_up_captured(self) -> None:
        # Gap up: prev close=100, H=120, L=115 → max(5, 20, 15) = 20
        high  = _series(100.0, 120.0)
        low   = _series(98.0,  115.0)
        close = _series(100.0, 118.0)
        result = true_range(high, low, close)
        assert result.iloc[1] == pytest.approx(20.0)

    def test_large_gap_down_captured(self) -> None:
        # Gap down: prev close=100, H=85, L=80 → max(5, 15, 20) = 20
        high  = _series(100.0, 85.0)
        low   = _series(98.0,  80.0)
        close = _series(100.0, 82.0)
        result = true_range(high, low, close)
        assert result.iloc[1] == pytest.approx(20.0)

    def test_preserves_index(self) -> None:
        idx = pd.date_range("2024-01-01", periods=3, freq="h")
        high  = pd.Series([12.0, 13.0, 15.0], index=idx)
        low   = pd.Series([10.0, 11.0, 10.0], index=idx)
        close = pd.Series([11.0, 12.0, 13.0], index=idx)
        result = true_range(high, low, close)
        assert list(result.index) == list(idx)

    def test_does_not_mutate_inputs(self) -> None:
        high, low, close = self._make_ohlc()
        h_orig, l_orig, c_orig = high.copy(), low.copy(), close.copy()
        true_range(high, low, close)
        pd.testing.assert_series_equal(high, h_orig)
        pd.testing.assert_series_equal(low, l_orig)
        pd.testing.assert_series_equal(close, c_orig)

    def test_returns_series(self) -> None:
        high, low, close = self._make_ohlc()
        assert isinstance(true_range(high, low, close), pd.Series)


# ---------------------------------------------------------------------------
# TestAtr
# ---------------------------------------------------------------------------

class TestAtr:
    def _ohlc_flat(self, n: int = 10) -> tuple[pd.Series, pd.Series, pd.Series]:
        # Flat bars: H=11, L=9, C=10 → TR=2 always (after first bar)
        high  = _series(*([11.0] * n))
        low   = _series(*([9.0]  * n))
        close = _series(*([10.0] * n))
        return high, low, close

    def test_basic_known_values(self) -> None:
        high, low, close = self._ohlc_flat(10)
        result = atr(high, low, close, window=3)
        # After 3 bars, ATR should be 2.0 (all TRs are 2.0)
        assert result.iloc[3] == pytest.approx(2.0)

    def test_nan_until_window_filled(self) -> None:
        high, low, close = self._ohlc_flat(10)
        result = atr(high, low, close, window=5)
        for i in range(4):
            assert _isnan(result.iloc[i])
        assert not _isnan(result.iloc[5])

    def test_default_window_14(self) -> None:
        n = 20
        high, low, close = self._ohlc_flat(n)
        result = atr(high, low, close)
        assert result.iloc[14] == pytest.approx(2.0)

    def test_preserves_index(self) -> None:
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        high  = pd.Series([11.0] * 5, index=idx)
        low   = pd.Series([9.0]  * 5, index=idx)
        close = pd.Series([10.0] * 5, index=idx)
        result = atr(high, low, close, window=3)
        assert list(result.index) == list(idx)

    def test_does_not_mutate_inputs(self) -> None:
        high, low, close = self._ohlc_flat(5)
        h_orig, l_orig, c_orig = high.copy(), low.copy(), close.copy()
        atr(high, low, close, window=3)
        pd.testing.assert_series_equal(high, h_orig)
        pd.testing.assert_series_equal(low, l_orig)
        pd.testing.assert_series_equal(close, c_orig)

    def test_returns_series(self) -> None:
        high, low, close = self._ohlc_flat(5)
        assert isinstance(atr(high, low, close, window=3), pd.Series)

    def test_invalid_window_zero_raises(self) -> None:
        high, low, close = self._ohlc_flat(5)
        with pytest.raises(ValueError, match="invalid window"):
            atr(high, low, close, window=0)

    def test_invalid_window_negative_raises(self) -> None:
        high, low, close = self._ohlc_flat(5)
        with pytest.raises(ValueError, match="invalid window"):
            atr(high, low, close, window=-1)


# ---------------------------------------------------------------------------
# TestRollingHigh
# ---------------------------------------------------------------------------

class TestRollingHigh:
    def test_includes_current_when_exclude_false(self) -> None:
        s = _series(1.0, 2.0, 3.0, 10.0, 5.0)
        result = rolling_high(s, window=3, exclude_current=False)
        # idx 3: max(2,3,10)=10
        assert result.iloc[3] == pytest.approx(10.0)

    def test_excludes_current_when_exclude_true(self) -> None:
        s = _series(1.0, 2.0, 3.0, 10.0, 5.0)
        result = rolling_high(s, window=3, exclude_current=True)
        # idx 3: uses shifted series; window of 3 prior = indices 0,1,2 → max=3
        assert result.iloc[3] == pytest.approx(3.0)

    def test_no_lookahead_spike_does_not_affect_same_row(self) -> None:
        # Spike at index 4 must not affect rolling_high at index 4
        s = _series(1.0, 1.0, 1.0, 1.0, 999.0)
        result = rolling_high(s, window=3, exclude_current=True)
        assert result.iloc[4] == pytest.approx(1.0)

    def test_default_is_exclude_current(self) -> None:
        s = _series(1.0, 2.0, 3.0, 10.0)
        r_default = rolling_high(s, window=2)
        r_explicit = rolling_high(s, window=2, exclude_current=True)
        pd.testing.assert_series_equal(r_default, r_explicit)

    def test_preserves_index(self) -> None:
        idx = pd.date_range("2024-01-01", periods=5, freq="h")
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=idx)
        result = rolling_high(s, window=2)
        assert list(result.index) == list(idx)

    def test_does_not_mutate_input(self) -> None:
        s = _series(1.0, 2.0, 3.0, 4.0)
        original = s.copy()
        rolling_high(s, window=2)
        pd.testing.assert_series_equal(s, original)

    def test_returns_series(self) -> None:
        assert isinstance(rolling_high(_series(1.0, 2.0, 3.0), window=2), pd.Series)

    def test_invalid_window_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid window"):
            rolling_high(_series(1.0, 2.0), window=0)

    def test_invalid_window_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid window"):
            rolling_high(_series(1.0, 2.0), window=-2)


# ---------------------------------------------------------------------------
# TestRollingLow
# ---------------------------------------------------------------------------

class TestRollingLow:
    def test_includes_current_when_exclude_false(self) -> None:
        s = _series(10.0, 9.0, 8.0, 1.0, 7.0)
        result = rolling_low(s, window=3, exclude_current=False)
        # idx 3: min(8,1,?) wait — window=3, bars are 8,1 at indices 2,3 and 9 at idx 1
        # idx 3: min(s[1], s[2], s[3]) = min(9,8,1) = 1
        assert result.iloc[3] == pytest.approx(1.0)

    def test_excludes_current_when_exclude_true(self) -> None:
        s = _series(10.0, 9.0, 8.0, 1.0, 7.0)
        result = rolling_low(s, window=3, exclude_current=True)
        # idx 3 uses shifted: window of 3 prior = s[0],s[1],s[2] = 10,9,8 → min=8
        assert result.iloc[3] == pytest.approx(8.0)

    def test_no_lookahead_crash_does_not_affect_same_row(self) -> None:
        # Crash at index 4 must not affect rolling_low at index 4
        s = _series(10.0, 10.0, 10.0, 10.0, 0.001)
        result = rolling_low(s, window=3, exclude_current=True)
        assert result.iloc[4] == pytest.approx(10.0)

    def test_default_is_exclude_current(self) -> None:
        s = _series(5.0, 4.0, 3.0, 1.0)
        r_default = rolling_low(s, window=2)
        r_explicit = rolling_low(s, window=2, exclude_current=True)
        pd.testing.assert_series_equal(r_default, r_explicit)

    def test_preserves_index(self) -> None:
        idx = pd.date_range("2024-01-01", periods=5, freq="h")
        s = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0], index=idx)
        result = rolling_low(s, window=2)
        assert list(result.index) == list(idx)

    def test_does_not_mutate_input(self) -> None:
        s = _series(5.0, 4.0, 3.0)
        original = s.copy()
        rolling_low(s, window=2)
        pd.testing.assert_series_equal(s, original)

    def test_returns_series(self) -> None:
        assert isinstance(rolling_low(_series(3.0, 2.0, 1.0), window=2), pd.Series)

    def test_invalid_window_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid window"):
            rolling_low(_series(1.0, 2.0), window=0)

    def test_invalid_window_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid window"):
            rolling_low(_series(1.0, 2.0), window=-1)


# ---------------------------------------------------------------------------
# TestBreakoutAbove
# ---------------------------------------------------------------------------

class TestBreakoutAbove:
    def test_true_when_close_exceeds_prior_high(self) -> None:
        # High series: 10, 10, 10, 10, 10
        # Close series: 5, 5, 5, 5, 15
        # rolling_high(high, 3, exclude_current=True) at idx 4 = max of high[1,2,3] = 10
        # close[4]=15 > 10 → True
        high  = _series(10.0, 10.0, 10.0, 10.0, 10.0)
        close = _series(5.0,  5.0,  5.0,  5.0, 15.0)
        result = breakout_above(close, high, lookback=3)
        assert result.iloc[4] is True or result.iloc[4] == True  # noqa: E712

    def test_false_when_close_does_not_exceed_prior_high(self) -> None:
        high  = _series(10.0, 10.0, 10.0, 10.0, 10.0)
        close = _series(5.0,  5.0,  5.0,  5.0,  9.0)
        result = breakout_above(close, high, lookback=3)
        assert result.iloc[4] is False or result.iloc[4] == False  # noqa: E712

    def test_current_high_spike_not_counted(self) -> None:
        # High spike at index 4: high[4]=999, but rolling_high excludes current
        # Reference high at idx 4 = max of high[1,2,3] = 10
        high  = _series(10.0, 10.0, 10.0, 10.0, 999.0)
        close = _series(5.0,  5.0,  5.0,  5.0,  11.0)
        result = breakout_above(close, high, lookback=3)
        # close[4]=11 > reference 10 → True (but current high spike not used)
        assert result.iloc[4] is True or result.iloc[4] == True  # noqa: E712

    def test_returns_series(self) -> None:
        high  = _series(10.0, 10.0, 10.0)
        close = _series(9.0,  9.0,  9.0)
        assert isinstance(breakout_above(close, high, lookback=2), pd.Series)


# ---------------------------------------------------------------------------
# TestBreakoutBelow
# ---------------------------------------------------------------------------

class TestBreakoutBelow:
    def test_true_when_close_below_prior_low(self) -> None:
        # low=10 series; close crashes to 5 at idx 4
        # rolling_low(low, 3, exclude_current=True) at idx 4 = min of low[1,2,3] = 10
        # close[4]=5 < 10 → True
        low   = _series(10.0, 10.0, 10.0, 10.0, 10.0)
        close = _series(15.0, 15.0, 15.0, 15.0,  5.0)
        result = breakout_below(close, low, lookback=3)
        assert result.iloc[4] is True or result.iloc[4] == True  # noqa: E712

    def test_false_when_close_does_not_breach_prior_low(self) -> None:
        low   = _series(10.0, 10.0, 10.0, 10.0, 10.0)
        close = _series(15.0, 15.0, 15.0, 15.0, 11.0)
        result = breakout_below(close, low, lookback=3)
        assert result.iloc[4] is False or result.iloc[4] == False  # noqa: E712

    def test_returns_series(self) -> None:
        low   = _series(10.0, 10.0, 10.0)
        close = _series(15.0, 15.0, 15.0)
        assert isinstance(breakout_below(close, low, lookback=2), pd.Series)


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

def _indicator_source_files() -> list[Path]:
    base = Path(__file__).parent.parent / "src" / "indicators"
    return [
        base / "moving_average.py",
        base / "volatility.py",
        base / "trend.py",
        base / "__init__.py",
    ]


def _all_indicator_lines() -> list[str]:
    lines: list[str] = []
    for path in _indicator_source_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            code_part = line.split("#")[0]
            if code_part.strip():
                lines.append(code_part)
    return lines


class TestSourceScans:
    def test_no_alpaca_import(self) -> None:
        for line in _all_indicator_lines():
            s = line.strip()
            assert "import alpaca" not in s, f"alpaca import: {line!r}"
            assert "from alpaca" not in s, f"alpaca import: {line!r}"

    def test_no_requests_import(self) -> None:
        for line in _all_indicator_lines():
            assert "import requests" not in line, f"requests import: {line!r}"

    def test_no_httpx_import(self) -> None:
        for line in _all_indicator_lines():
            assert "import httpx" not in line, f"httpx import: {line!r}"

    def test_no_aiohttp_import(self) -> None:
        for line in _all_indicator_lines():
            assert "import aiohttp" not in line, f"aiohttp import: {line!r}"

    def test_no_urllib_import(self) -> None:
        for line in _all_indicator_lines():
            s = line.strip()
            assert not (s.startswith("import urllib") or s.startswith("from urllib")), (
                f"urllib import: {line!r}"
            )

    def test_no_os_environ(self) -> None:
        for line in _all_indicator_lines():
            assert "os.environ" not in line, f"os.environ: {line!r}"

    def test_no_os_import(self) -> None:
        for line in _all_indicator_lines():
            s = line.strip()
            assert s != "import os", f"import os: {line!r}"
            assert not s.startswith("from os "), f"from os: {line!r}"

    def test_no_execution_imports(self) -> None:
        for line in _all_indicator_lines():
            s = line.strip()
            assert "from src.execution" not in s, f"execution import: {line!r}"
            assert "import src.execution" not in s, f"execution import: {line!r}"

    def test_no_submit_order(self) -> None:
        for line in _all_indicator_lines():
            assert "submit_order(" not in line, f"submit_order(: {line!r}"

    def test_no_cancel_order(self) -> None:
        for line in _all_indicator_lines():
            assert "cancel_order(" not in line, f"cancel_order(: {line!r}"

    def test_no_replace_order(self) -> None:
        for line in _all_indicator_lines():
            assert "replace_order(" not in line, f"replace_order(: {line!r}"

    def test_no_close_position(self) -> None:
        for line in _all_indicator_lines():
            assert "close_position(" not in line, f"close_position(: {line!r}"

    def test_no_close_all_positions(self) -> None:
        for line in _all_indicator_lines():
            assert "close_all_positions(" not in line, f"close_all_positions(: {line!r}"

    def test_no_post_mutation(self) -> None:
        for line in _all_indicator_lines():
            assert ".post(" not in line.lower(), f"POST mutation: {line!r}"

    def test_no_patch_mutation(self) -> None:
        for line in _all_indicator_lines():
            assert ".patch(" not in line.lower(), f"PATCH mutation: {line!r}"

    def test_no_delete_mutation(self) -> None:
        for line in _all_indicator_lines():
            assert ".delete(" not in line.lower(), f"DELETE mutation: {line!r}"

    def test_no_write_text(self) -> None:
        for line in _all_indicator_lines():
            assert "write_text(" not in line, f"write_text(: {line!r}"

    def test_no_json_dump(self) -> None:
        for line in _all_indicator_lines():
            assert "json.dump(" not in line, f"json.dump(: {line!r}"
