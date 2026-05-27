"""
indicators/trend.py
--------------------
Rolling breakout high/low helpers and breakout detection indicators.

Pure, deterministic, offline-only. No look-ahead.

``rolling_high`` and ``rolling_low`` default to ``exclude_current=True``,
which shifts the series by one bar before applying the rolling window.
This prevents look-ahead bias: a spike on the current bar never affects
the breakout reference level computed at the same bar.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
"""

from __future__ import annotations

import pandas as pd


def rolling_high(
    values: pd.Series,
    window: int,
    *,
    exclude_current: bool = True,
) -> pd.Series:
    """Rolling maximum over *window* bars.

    Parameters
    ----------
    values:
        Input series. Not mutated.
    window:
        Look-back period in bars. Must be >= 1.
    exclude_current:
        If ``True`` (default), the current bar is excluded from the window
        by shifting the series one bar forward before rolling. This is the
        correct setting for breakout entry logic where the reference high
        must be determined from bars prior to the current bar.

    Returns
    -------
    pd.Series
        Rolling maximum with the same index as *values*.

    Raises
    ------
    ValueError
        If *window* is less than 1: ``"invalid window"``.
    """
    if window < 1:
        raise ValueError("invalid window")
    source = values.shift(1) if exclude_current else values
    return source.rolling(window=window).max()


def rolling_low(
    values: pd.Series,
    window: int,
    *,
    exclude_current: bool = True,
) -> pd.Series:
    """Rolling minimum over *window* bars.

    Parameters
    ----------
    values:
        Input series. Not mutated.
    window:
        Look-back period in bars. Must be >= 1.
    exclude_current:
        If ``True`` (default), the current bar is excluded from the window
        by shifting the series one bar forward before rolling. This is the
        correct setting for breakout entry logic where the reference low
        must be determined from bars prior to the current bar.

    Returns
    -------
    pd.Series
        Rolling minimum with the same index as *values*.

    Raises
    ------
    ValueError
        If *window* is less than 1: ``"invalid window"``.
    """
    if window < 1:
        raise ValueError("invalid window")
    source = values.shift(1) if exclude_current else values
    return source.rolling(window=window).min()


def breakout_above(
    close: pd.Series,
    high: pd.Series,
    lookback: int,
) -> pd.Series:
    """Boolean series: True when close exceeds the prior *lookback*-bar high.

    Uses ``rolling_high(high, lookback, exclude_current=True)`` so the
    reference level never includes the current bar's high.

    Parameters
    ----------
    close:
        Bar close prices.
    high:
        Bar high prices (used to compute the reference level).
    lookback:
        Number of prior bars to include in the rolling high.

    Returns
    -------
    pd.Series[bool]
        True on bars where ``close > rolling_high(high, lookback)``.
    """
    ref = rolling_high(high, lookback, exclude_current=True)
    return close > ref


def breakout_below(
    close: pd.Series,
    low: pd.Series,
    lookback: int,
) -> pd.Series:
    """Boolean series: True when close falls below the prior *lookback*-bar low.

    Uses ``rolling_low(low, lookback, exclude_current=True)`` so the
    reference level never includes the current bar's low.

    Parameters
    ----------
    close:
        Bar close prices.
    low:
        Bar low prices (used to compute the reference level).
    lookback:
        Number of prior bars to include in the rolling low.

    Returns
    -------
    pd.Series[bool]
        True on bars where ``close < rolling_low(low, lookback)``.
    """
    ref = rolling_low(low, lookback, exclude_current=True)
    return close < ref
