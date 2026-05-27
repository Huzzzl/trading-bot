"""
indicators/moving_average.py
-----------------------------
Simple and exponential moving average indicators.

Pure, deterministic, offline-only. No look-ahead.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
"""

from __future__ import annotations

import pandas as pd


def sma(values: pd.Series, window: int) -> pd.Series:
    """Simple moving average.

    Parameters
    ----------
    values:
        Input series. Not mutated.
    window:
        Look-back period in bars. Must be >= 1.

    Returns
    -------
    pd.Series
        Rolling mean with the same index as *values*.
        Values before the first full window are NaN.

    Raises
    ------
    ValueError
        If *window* is less than 1: ``"invalid window"``.
    """
    if window < 1:
        raise ValueError("invalid window")
    return values.rolling(window=window).mean()


def ema(values: pd.Series, span: int) -> pd.Series:
    """Exponential moving average.

    Parameters
    ----------
    values:
        Input series. Not mutated.
    span:
        EMA span (``com = (span - 1) / 2``). Must be >= 1.

    Returns
    -------
    pd.Series
        EWM mean with the same index as *values*.

    Raises
    ------
    ValueError
        If *span* is less than 1: ``"invalid span"``.
    """
    if span < 1:
        raise ValueError("invalid span")
    return values.ewm(span=span, adjust=False).mean()
