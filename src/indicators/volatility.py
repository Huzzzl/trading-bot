"""
indicators/volatility.py
-------------------------
True range and average true range indicators.

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


def true_range(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
) -> pd.Series:
    """True range for each bar.

    True range is:
        max(high - low, |high - prev_close|, |low - prev_close|)

    For the first bar, prev_close is NaN so the result falls back to
    ``high - low``.

    Parameters
    ----------
    high, low, close:
        OHLCV series aligned on the same index. Inputs are not mutated.

    Returns
    -------
    pd.Series
        True range with the same index as *high*.
    """
    prev_close = close.shift(1)
    hl = high - low
    hpc = (high - prev_close).abs()
    lpc = (low - prev_close).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    tr.index = high.index
    return tr


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """Average true range.

    Rolling mean of ``true_range`` over *window* bars.

    Parameters
    ----------
    high, low, close:
        OHLCV series aligned on the same index. Inputs are not mutated.
    window:
        Look-back period in bars. Must be >= 1.

    Returns
    -------
    pd.Series
        ATR with the same index as *high*.
        Values before the first full window are NaN.

    Raises
    ------
    ValueError
        If *window* is less than 1: ``"invalid window"``.
    """
    if window < 1:
        raise ValueError("invalid window")
    tr = true_range(high, low, close)
    return tr.rolling(window=window).mean()
