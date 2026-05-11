"""
data/base.py
------------
Abstract base class for all market-data providers.

Adding a new provider (e.g. Alpaca, Polygon) only requires subclassing
``BaseDataProvider`` and implementing ``fetch_bars``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseDataProvider(ABC):
    """Contract that every data provider must fulfil."""

    @abstractmethod
    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> pd.DataFrame:
        """Download OHLCV bars for *symbol* over [*start*, *end*].

        Parameters
        ----------
        symbol:
            Ticker, e.g. ``"SPY"``.
        start:
            ISO-8601 date string, e.g. ``"2024-01-01"``.
        end:
            ISO-8601 date string (inclusive end), e.g. ``"2024-12-31"``.
        interval:
            Bar width accepted by the provider, e.g. ``"5m"``.

        Returns
        -------
        pd.DataFrame
            Columns: ``open``, ``high``, ``low``, ``close``, ``volume``.
            Index: timezone-aware ``pd.DatetimeIndex`` in US/Eastern time.
            Rows are sorted ascending by timestamp.
            No NaN values in OHLC columns.
        """
        ...
