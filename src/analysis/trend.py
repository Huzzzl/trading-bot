"""
analysis/trend.py
-----------------
EMA-based trend classification layer.

Pure, deterministic, offline-only. No look-ahead.

classify_trend() examines the latest bar only after computing
EMA-based and ATR-based statistics over the full bar history.
No forward information leaks into the result.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import pandas as pd

from src.indicators.moving_average import ema as _compute_ema
from src.indicators.volatility import atr as _compute_atr

_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-/]{1,10}$")
_VALID_TIMEFRAMES: frozenset[str] = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}
)
_REQUIRED_COLUMNS: frozenset[str] = frozenset({"high", "low", "close"})

_STRENGTH_THRESHOLD = 0.005
_VOLATILITY_HIGH_MULTIPLIER = 1.5
_VOLATILITY_LOW_MULTIPLIER = 0.5


@dataclass(frozen=True)
class TrendState:
    """Immutable snapshot of trend classification for a symbol."""

    symbol: str
    timeframe: str
    trend: str               # "bullish" | "bearish" | "neutral"
    strength: str            # "strong" | "weak" | "unknown"
    volatility_regime: str   # "high" | "low" | "normal" | "unknown"
    fast_ema: float
    slow_ema: float
    atr: float
    reason_codes: tuple[str, ...]
    deterministic: bool = True
    broker_calls_made: bool = False
    credentials_read: bool = False
    order_action_requested: bool = False


def _blocked_state(symbol: str, timeframe: str, reason: str) -> TrendState:
    return TrendState(
        symbol=symbol,
        timeframe=timeframe,
        trend="neutral",
        strength="unknown",
        volatility_regime="unknown",
        fast_ema=float("nan"),
        slow_ema=float("nan"),
        atr=float("nan"),
        reason_codes=(reason,),
    )


def classify_trend(
    bars: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    fast_ema_period: int = 20,
    slow_ema_period: int = 50,
    atr_period: int = 14,
    volatility_lookback: int = 50,
) -> TrendState:
    """Classify trend from OHLCV bars.

    Parameters
    ----------
    bars:
        DataFrame with columns ``high``, ``low``, ``close`` at minimum.
        Rows are in chronological order (oldest first, newest last).
        Input is not mutated.
    symbol:
        Instrument ticker symbol (1-10 uppercase alphanumeric/dot/dash chars).
    timeframe:
        Bar interval string (e.g. ``"1h"``, ``"1d"``).
    fast_ema_period:
        EMA period for the fast line. Must be >= 1 and < ``slow_ema_period``.
    slow_ema_period:
        EMA period for the slow line. Must be > ``fast_ema_period``.
    atr_period:
        ATR rolling window. Must be >= 1.
    volatility_lookback:
        Rolling window for the ATR-ratio median used to identify the
        volatility regime. Must be >= 1.

    Returns
    -------
    TrendState
        Immutable snapshot with ``trend``, ``strength``, ``volatility_regime``,
        EMA/ATR scalars, and ``reason_codes``.
        Safety fields are always: ``deterministic=True``,
        ``broker_calls_made=False``, ``credentials_read=False``,
        ``order_action_requested=False``.

    Notes
    -----
    Minimum bars required:
        ``max(slow_ema_period, atr_period + volatility_lookback - 1)``
    """
    symbol_str = symbol if isinstance(symbol, str) else ""
    timeframe_str = timeframe if isinstance(timeframe, str) else ""

    # --- Validation gates (order matters: symbol → timeframe → periods → cols → length) ---
    if not _VALID_SYMBOL_RE.match(symbol_str):
        return _blocked_state(symbol_str, timeframe_str, "INVALID_SYMBOL")

    if timeframe_str not in _VALID_TIMEFRAMES:
        return _blocked_state(symbol_str, timeframe_str, "INVALID_TIMEFRAME")

    if (
        fast_ema_period < 1
        or slow_ema_period < 1
        or atr_period < 1
        or volatility_lookback < 1
    ):
        return _blocked_state(symbol_str, timeframe_str, "INVALID_PERIOD")

    if fast_ema_period >= slow_ema_period:
        return _blocked_state(symbol_str, timeframe_str, "INVALID_PERIOD_ORDER")

    if not _REQUIRED_COLUMNS.issubset(bars.columns):
        return _blocked_state(symbol_str, timeframe_str, "MISSING_REQUIRED_COLUMNS")

    min_required = max(slow_ema_period, atr_period + volatility_lookback - 1)
    if len(bars) < min_required:
        return _blocked_state(symbol_str, timeframe_str, "INSUFFICIENT_BARS")

    # --- Compute indicators on copies (no mutation) ---
    close = bars["close"].copy()
    high = bars["high"].copy()
    low = bars["low"].copy()

    fast_ema_series = _compute_ema(close, fast_ema_period)
    slow_ema_series = _compute_ema(close, slow_ema_period)
    atr_series = _compute_atr(high, low, close, atr_period)

    fast_ema_val = float(fast_ema_series.iloc[-1])
    slow_ema_val = float(slow_ema_series.iloc[-1])
    atr_val = float(atr_series.iloc[-1])
    close_val = float(close.iloc[-1])

    if math.isnan(fast_ema_val) or math.isnan(slow_ema_val):
        return _blocked_state(symbol_str, timeframe_str, "INSUFFICIENT_BARS")

    reason_codes: list[str] = []

    # --- Trend direction ---
    if close_val > fast_ema_val > slow_ema_val:
        trend = "bullish"
        reason_codes.append("TREND_BULLISH")
    elif close_val < fast_ema_val < slow_ema_val:
        trend = "bearish"
        reason_codes.append("TREND_BEARISH")
    else:
        trend = "neutral"
        reason_codes.append("TREND_NEUTRAL")

    # --- Strength: relative EMA spread ---
    spread = abs(fast_ema_val - slow_ema_val) / slow_ema_val if slow_ema_val != 0.0 else 0.0
    if spread >= _STRENGTH_THRESHOLD:
        strength = "strong"
        reason_codes.append("STRENGTH_STRONG")
    else:
        strength = "weak"
        reason_codes.append("STRENGTH_WEAK")

    # --- Volatility regime: compare latest ATR ratio to rolling median ---
    atr_ratio_series = atr_series / close
    rolling_median = atr_ratio_series.rolling(window=volatility_lookback).median()
    median_ratio = float(rolling_median.iloc[-1])

    if (
        math.isnan(median_ratio)
        or math.isnan(atr_val)
        or median_ratio == 0.0
        or close_val == 0.0
    ):
        volatility_regime = "unknown"
        reason_codes.append("VOLATILITY_UNKNOWN")
    else:
        latest_ratio = atr_val / close_val
        if latest_ratio > _VOLATILITY_HIGH_MULTIPLIER * median_ratio:
            volatility_regime = "high"
            reason_codes.append("VOLATILITY_HIGH")
        elif latest_ratio < _VOLATILITY_LOW_MULTIPLIER * median_ratio:
            volatility_regime = "low"
            reason_codes.append("VOLATILITY_LOW")
        else:
            volatility_regime = "normal"
            reason_codes.append("VOLATILITY_NORMAL")

    return TrendState(
        symbol=symbol_str,
        timeframe=timeframe_str,
        trend=trend,
        strength=strength,
        volatility_regime=volatility_regime,
        fast_ema=fast_ema_val,
        slow_ema=slow_ema_val,
        atr=atr_val,
        reason_codes=tuple(reason_codes),
    )
