"""
tests/test_disk_cache.py
------------------------
Unit tests for CachedMarketDataProvider.

All tests use a FakeDataProvider — no network calls, no yfinance.
Temporary directories are used so tests do not pollute data/cache/.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import src.data.cached_provider as _cached_provider_module

from src.config.loader import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    LoggingConfig,
    RiskConfig,
    StrategyConfig,
)
from src.data.base import BaseDataProvider
from src.data.cached_provider import CachedMarketDataProvider
from src.experiments.sweep_runner import CachedDataProvider, SweepRunner
from src.experiments.walk_forward_runner import WalkForwardRunner

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TRADING_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def _make_day_bars(date_str: str) -> pd.DataFrame:
    session_start = pd.Timestamp(date_str, tz=_ET).replace(hour=9, minute=30)
    idx = pd.date_range(session_start, periods=78, freq="5min")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000},
        index=idx,
    )


def _make_bars() -> pd.DataFrame:
    return pd.concat([_make_day_bars(d) for d in _TRADING_DAYS])


class CountingProvider(BaseDataProvider):
    """Records every fetch_bars call."""

    def __init__(self) -> None:
        self.call_count = 0

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        self.call_count += 1
        return _make_bars()


class ErrorProvider(BaseDataProvider):
    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        raise RuntimeError("Should not be called on cache hit")


def _make_app_config() -> AppConfig:
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-02",
            end_date="2024-01-05",
            initial_capital=100_000.0,
            commission_per_share=0.005,
            slippage_per_share=0.01,
        ),
        symbols=["QQQ"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(
            name="opening_range_breakout",
            params={
                "opening_range_start": "09:30",
                "opening_range_end":   "10:00",
                "force_exit_time":     "15:55",
                "position_size_pct":   0.95,
                "long_only":           True,
                "breakout_trigger":    "close",
            },
        ),
        risk=RiskConfig(max_open_positions=1),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
    )


# ===========================================================================
# Cache miss / hit behaviour
# ===========================================================================

class TestCacheMissAndHit:
    def test_miss_calls_wrapped_provider_once(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        assert inner.call_count == 1

    def test_miss_writes_file_to_disk(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        files = list(tmp_path.glob("*.csv"))
        assert len(files) == 1

    def test_hit_does_not_call_wrapped_provider(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        assert inner.call_count == 1

    def test_different_keys_each_fetch_once(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        provider.fetch_bars("QQQ", "2024-01-01", "2024-01-05", "5m")
        assert inner.call_count == 2
        assert len(list(tmp_path.glob("*.csv"))) == 2

    def test_hit_does_not_call_provider_even_when_it_raises(self, tmp_path):
        # Prime the cache using CountingProvider, then swap to ErrorProvider.
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")

        # New instance pointing to the same cache directory but with ErrorProvider.
        provider2 = CachedMarketDataProvider(ErrorProvider(), cache_dir=tmp_path, use_parquet=False)
        result = provider2.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        assert not result.empty


# ===========================================================================
# Copy semantics — returned DataFrame must be independent of cache
# ===========================================================================

class TestCopySafety:
    def test_miss_returns_copy(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        df = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        df["close"] = -999.0
        df2 = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        assert (df2["close"] != -999.0).all()

    def test_hit_returns_copy(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        df = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        df["close"] = -999.0
        df2 = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        assert (df2["close"] != -999.0).all()

    def test_provider_mutation_does_not_corrupt_saved_cache(self, tmp_path):
        """Mutating the DataFrame returned by the inner provider must not affect
        what gets persisted — only possible if we .copy() before _save()."""
        mutating_df = _make_bars()

        class MutatingProvider(BaseDataProvider):
            def fetch_bars(self, symbol, start, end, interval):
                return mutating_df  # returns the same object every time

        provider = CachedMarketDataProvider(
            MutatingProvider(), cache_dir=tmp_path, use_parquet=False
        )
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")

        # Mutate the original DataFrame after the first fetch.
        mutating_df["close"] = -999.0

        # The cached file must still contain the original values.
        cached = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        assert (cached["close"] != -999.0).all()


# ===========================================================================
# Timezone preservation
# ===========================================================================

class TestTimezonePreservation:
    def _check_tz(self, df: pd.DataFrame) -> None:
        assert df.index.tz is not None, "Index must be timezone-aware"
        assert str(df.index.tz) == "America/New_York"

    def test_csv_preserves_tz_on_miss(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        df = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        self._check_tz(df)

    def test_csv_preserves_tz_on_hit(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        df = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        self._check_tz(df)

    def test_csv_index_timestamps_match_original(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        original = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        cached   = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        assert original.index.equals(cached.index)


# ===========================================================================
# OHLCV columns
# ===========================================================================

class TestOhlcvColumns:
    def test_ohlcv_columns_present_after_csv_roundtrip(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        df = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in df.columns

    def test_ohlcv_values_match_original_after_csv_roundtrip(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        original = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        cached   = provider.fetch_bars("SPY", "2024-01-01", "2024-01-05", "5m")
        pd.testing.assert_frame_equal(original, cached)


# ===========================================================================
# CSV fallback (explicit use_parquet=False)
# ===========================================================================

class TestCsvFallback:
    def test_csv_file_created_when_parquet_disabled(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("QQQ", "2024-01-01", "2024-01-05", "5m")
        assert len(list(tmp_path.glob("*.csv"))) == 1
        assert len(list(tmp_path.glob("*.parquet"))) == 0

    def test_csv_roundtrip_row_count(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        original = provider.fetch_bars("QQQ", "2024-01-01", "2024-01-05", "5m")
        cached   = provider.fetch_bars("QQQ", "2024-01-01", "2024-01-05", "5m")
        assert len(original) == len(cached)

    def test_csv_hit_skips_provider(self, tmp_path):
        inner = CountingProvider()
        provider = CachedMarketDataProvider(inner, cache_dir=tmp_path, use_parquet=False)
        provider.fetch_bars("QQQ", "2024-01-01", "2024-01-05", "5m")
        provider.fetch_bars("QQQ", "2024-01-01", "2024-01-05", "5m")
        assert inner.call_count == 1


# ===========================================================================
# use_parquet=True without pyarrow raises ImportError
# ===========================================================================

class TestParquetUnavailableError:
    def test_raises_import_error_when_parquet_forced_but_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cached_provider_module, "_PARQUET_AVAILABLE", False)
        with pytest.raises(ImportError, match="pyarrow"):
            CachedMarketDataProvider(
                CountingProvider(), cache_dir=tmp_path, use_parquet=True
            )

    def test_no_error_when_parquet_false_and_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cached_provider_module, "_PARQUET_AVAILABLE", False)
        provider = CachedMarketDataProvider(
            CountingProvider(), cache_dir=tmp_path, use_parquet=False
        )
        assert provider is not None

    def test_no_error_when_parquet_none_and_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cached_provider_module, "_PARQUET_AVAILABLE", False)
        provider = CachedMarketDataProvider(
            CountingProvider(), cache_dir=tmp_path, use_parquet=None
        )
        # Auto-detects unavailability and falls back to CSV without raising.
        assert provider is not None


# ===========================================================================
# Cache directory creation
# ===========================================================================

class TestCacheDirectoryCreation:
    def test_cache_dir_created_if_absent(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "cache"
        assert not nested.exists()
        CachedMarketDataProvider(CountingProvider(), cache_dir=nested, use_parquet=False)
        assert nested.exists()


# ===========================================================================
# Integration — SweepRunner uses CachedMarketDataProvider
# ===========================================================================

class TestSweepRunnerIntegration:
    def test_sweep_runner_accepts_disk_cache_provider(self, tmp_path):
        inner = CountingProvider()
        disk  = CachedMarketDataProvider(inner, cache_dir=tmp_path / "cache", use_parquet=False)
        runner = SweepRunner(
            base_config=_make_app_config(),
            output_dir=tmp_path / "output",
            grid={
                "symbols":           [["QQQ"]],
                "opening_range_end": ["09:45"],
                "breakout_trigger":  ["close"],
                "position_size_pct": [0.95],
            },
            data_provider=disk,
        )
        df = runner.run()
        assert len(df) == 1
        assert df["status"].iloc[0] == "ok"

    def test_sweep_with_disk_cache_wraps_in_memory_cache(self, tmp_path):
        inner = CountingProvider()
        disk  = CachedMarketDataProvider(inner, cache_dir=tmp_path / "cache", use_parquet=False)
        runner = SweepRunner(
            base_config=_make_app_config(),
            output_dir=tmp_path / "output",
            grid={
                "symbols":           [["QQQ"]],
                "opening_range_end": ["09:45", "10:00"],
                "breakout_trigger":  ["close"],
                "position_size_pct": [0.95],
            },
            data_provider=disk,
        )
        runner.run()
        # SweepRunner auto-wraps disk in CachedDataProvider; both runs use QQQ
        # with the same date range → underlying provider called at most once.
        assert inner.call_count == 1


# ===========================================================================
# Integration — WalkForwardRunner uses CachedMarketDataProvider
# ===========================================================================

class TestWalkForwardRunnerIntegration:
    def test_walk_forward_accepts_disk_cache_provider(self, tmp_path):
        inner = CountingProvider()
        disk  = CachedMarketDataProvider(inner, cache_dir=tmp_path / "cache", use_parquet=False)
        runner = WalkForwardRunner(
            base_config=_make_app_config(),
            output_dir=tmp_path / "output",
            windows=[10],
            data_provider=disk,
            reference_end_date="2024-01-31",
        )
        df = runner.run()
        assert len(df) == 4  # 4 candidates × 1 window
        assert (df["status"] == "ok").all()

    def test_walk_forward_disk_cache_fetches_once_per_window(self, tmp_path):
        inner = CountingProvider()
        disk  = CachedMarketDataProvider(inner, cache_dir=tmp_path / "cache", use_parquet=False)
        runner = WalkForwardRunner(
            base_config=_make_app_config(),
            output_dir=tmp_path / "output",
            windows=[10],
            data_provider=disk,
            reference_end_date="2024-01-31",
        )
        runner.run()
        # All 4 candidates share the same QQQ date range for window=10.
        # The underlying provider should be called exactly once.
        assert inner.call_count == 1

    def test_walk_forward_two_windows_fetch_twice(self, tmp_path):
        inner = CountingProvider()
        disk  = CachedMarketDataProvider(inner, cache_dir=tmp_path / "cache", use_parquet=False)
        runner = WalkForwardRunner(
            base_config=_make_app_config(),
            output_dir=tmp_path / "output",
            windows=[10, 20],
            data_provider=disk,
            reference_end_date="2024-01-31",
        )
        runner.run()
        # Two distinct date ranges → two distinct cache files → two provider calls.
        assert inner.call_count == 2


# ===========================================================================
# Config dataclass defaults
# ===========================================================================

class TestDataConfigDefaults:
    def test_cache_enabled_defaults_to_true(self):
        cfg = DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York")
        assert cfg.cache_enabled is True

    def test_cache_dir_defaults_to_data_cache(self):
        cfg = DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York")
        assert cfg.cache_dir == "data/cache"
