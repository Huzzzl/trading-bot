"""
data/cached_provider.py
-----------------------
Disk-backed caching layer for any BaseDataProvider.

Cache files are stored as Parquet when pyarrow is available; otherwise the
module falls back to CSV so the project stays runnable in environments that
do not have the Arrow stack installed.

Timezone-aware DatetimeIndex values are preserved in both formats.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from zoneinfo import ZoneInfo

from src.data.base import BaseDataProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)

_EASTERN = ZoneInfo("America/New_York")

# Detect pyarrow once at import time.
try:
    import pyarrow  # noqa: F401
    _PARQUET_AVAILABLE = True
except ImportError:
    _PARQUET_AVAILABLE = False


def _safe_filename(symbol: str, start: str, end: str, interval: str) -> str:
    """Build a filesystem-safe stem from the cache-key components."""
    raw = f"{symbol}_{start}_{end}_{interval}"
    return re.sub(r"[^\w\-]", "_", raw)


class CachedMarketDataProvider(BaseDataProvider):
    """Disk-backed caching wrapper around any :class:`~src.data.base.BaseDataProvider`.

    On the first call for a given ``(symbol, start, end, interval)`` tuple the
    request is forwarded to the wrapped provider and the result is persisted to
    disk.  Subsequent calls with the same key load from disk without hitting the
    underlying provider.

    A fresh ``DataFrame.copy()`` is always returned so callers cannot corrupt
    the on-disk representation or affect subsequent reads.

    Parameters
    ----------
    provider:
        The underlying provider to delegate cache misses to.
    cache_dir:
        Root directory for cache files.  Created automatically if absent.
        Defaults to ``"data/cache"``.
    use_parquet:
        ``True``  — always use Parquet (raises if pyarrow is missing).
        ``False`` — always use CSV.
        ``None``  — auto-detect: Parquet when pyarrow is available, else CSV.
    """

    def __init__(
        self,
        provider: BaseDataProvider,
        cache_dir: str | Path = "data/cache",
        use_parquet: bool | None = None,
    ) -> None:
        self._provider   = provider
        self._cache_dir  = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        if use_parquet is True and not _PARQUET_AVAILABLE:
            raise ImportError(
                "use_parquet=True but pyarrow is not installed. "
                "Install it with: pip install pyarrow"
            )
        if use_parquet is None:
            self._use_parquet = _PARQUET_AVAILABLE
        else:
            self._use_parquet = use_parquet

        fmt = "parquet" if self._use_parquet else "csv"
        logger.debug("CachedMarketDataProvider initialised — dir=%s  format=%s", self._cache_dir, fmt)

    # ------------------------------------------------------------------
    # BaseDataProvider interface
    # ------------------------------------------------------------------

    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> pd.DataFrame:
        path = self._cache_path(symbol, start, end, interval)

        if path.exists():
            logger.debug(
                "CachedMarketDataProvider: cache hit — %s [%s, %s] %s",
                symbol, start, end, interval,
            )
            return self._load(path)

        logger.debug(
            "CachedMarketDataProvider: cache miss — fetching %s [%s, %s] %s",
            symbol, start, end, interval,
        )
        df = self._provider.fetch_bars(symbol, start, end, interval).copy()
        self._save(df, path)
        return df.copy()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_path(self, symbol: str, start: str, end: str, interval: str) -> Path:
        stem = _safe_filename(symbol, start, end, interval)
        ext  = ".parquet" if self._use_parquet else ".csv"
        return self._cache_dir / (stem + ext)

    def _save(self, df: pd.DataFrame, path: Path) -> None:
        if self._use_parquet:
            df.to_parquet(path, index=True)
        else:
            df.to_csv(path, index=True)
        logger.debug("CachedMarketDataProvider: saved %d rows to %s", len(df), path)

    def _load(self, path: Path) -> pd.DataFrame:
        if self._use_parquet:
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, index_col=0)
            # Restore timezone-aware DatetimeIndex.  CSV stores the offset as
            # part of the timestamp string (e.g. "2024-01-02 09:30:00-05:00");
            # parsing with utc=True interprets the offset then converts to UTC,
            # and tz_convert brings it back to Eastern.
            df.index = (
                pd.to_datetime(df.index, utc=True)
                .tz_convert(_EASTERN)
            )
            df.index.name = None

        # Ensure OHLCV columns are present and numeric.
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.copy()
