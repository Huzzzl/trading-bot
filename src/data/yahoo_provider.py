"""
data/yahoo_provider.py
----------------------
Market-data provider backed by *yfinance*.

Notes
-----
* yfinance returns timezone-aware timestamps; we normalise them to
  US/Eastern so all downstream code works in a single timezone.
* Intraday data from Yahoo Finance is available for roughly the last
  60 days for sub-daily intervals ≤ 1h, and ~730 days for 1h bars.
  For backtests longer than 60 days at 5-minute resolution you may
  need to fetch in rolling 58-day windows — a TODO for a future provider.
"""

from __future__ import annotations

import pandas as pd
from zoneinfo import ZoneInfo
try:
    import yfinance as yf
    _YFINANCE_AVAILABLE = True
except ImportError:
    _YFINANCE_AVAILABLE = False
    yf = None  # type: ignore

from src.data.base import BaseDataProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)

_EASTERN = ZoneInfo("America/New_York")

# Maximum history available from yfinance per interval (calendar days).
# Requests whose start date falls outside this window will silently return
# no data, so we raise early with a clear message instead.
_INTRADAY_MAX_HISTORY_DAYS: dict[str, int] = {
    "1m":  7,
    "2m":  60,
    "5m":  60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "90m": 60,
    "1h":  730,
}


class YahooDataProvider(BaseDataProvider):
    """Fetch OHLCV bars from Yahoo Finance via *yfinance*."""

    # yfinance only keeps ~60 days of intraday data; we chunk by this window.
    _MAX_INTRADAY_DAYS = 58

    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str = "5m",
    ) -> pd.DataFrame:
        """Download intraday bars and return them in Eastern time.

        Parameters
        ----------
        symbol, start, end, interval:
            See :class:`~src.data.base.BaseDataProvider`.

        Returns
        -------
        pd.DataFrame
            Columns ``open high low close volume`` (lower-case).
            Index is a timezone-aware ``DatetimeIndex`` in US/Eastern.
        """
        logger.info("Fetching %s bars for %s from %s to %s", interval, symbol, start, end)

        start_dt = pd.Timestamp(start, tz=_EASTERN)
        end_dt   = pd.Timestamp(end,   tz=_EASTERN) + pd.Timedelta(days=1)  # inclusive

        self._validate_intraday_request(interval, start_dt)

        frames: list[pd.DataFrame] = []
        chunk_start = start_dt

        while chunk_start < end_dt:
            chunk_end = min(chunk_start + pd.Timedelta(days=self._MAX_INTRADAY_DAYS), end_dt)
            logger.debug("  chunk %s → %s", chunk_start.date(), chunk_end.date())

            ticker = yf.Ticker(symbol)
            raw = ticker.history(
                start=chunk_start.strftime("%Y-%m-%d"),
                end=chunk_end.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=True,
                prepost=False,   # regular-session bars only
            )

            if not raw.empty:
                frames.append(raw)

            chunk_start = chunk_end

        if not frames:
            logger.warning("No data returned for %s (%s – %s)", symbol, start, end)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="first")]

        # Normalise column names
        df.columns = df.columns.str.lower()
        df = df[["open", "high", "low", "close", "volume"]]

        # Ensure Eastern timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(_EASTERN)

        # Drop rows with NaN OHLC (e.g. extended-hours artefacts)
        df = df.dropna(subset=["open", "high", "low", "close"])

        # Sort ascending — critical for look-ahead-bias-free iteration
        df = df.sort_index()

        logger.info("  → %d bars loaded for %s", len(df), symbol)
        return df

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_intraday_request(interval: str, start_dt: pd.Timestamp) -> None:
        """Raise :exc:`ValueError` when *start_dt* is outside yfinance's retention window.

        yfinance silently returns empty data for intraday intervals when the
        requested history is too old.  This method raises early so the caller
        gets a clear error message rather than a silent empty DataFrame.
        """
        max_days = _INTRADAY_MAX_HISTORY_DAYS.get(interval)
        if max_days is None:
            return  # daily/weekly intervals have no intraday cap

        today   = pd.Timestamp.now(tz=_EASTERN).normalize()
        cutoff  = today - pd.Timedelta(days=max_days)

        if start_dt < cutoff:
            raise ValueError(
                f"yfinance only retains ~{max_days} days of intraday history for "
                f"interval '{interval}'.  Requested start {start_dt.date()} predates "
                f"the available cutoff ({cutoff.date()}).  "
                f"Use a start date on or after {cutoff.date()}."
            )
