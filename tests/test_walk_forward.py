"""
tests/test_walk_forward.py
--------------------------
Unit tests for WalkForwardRunner.

All tests use FakeDataProvider — no network calls, no yfinance, no broker.
reference_end_date is pinned to "2024-01-31" (a Wednesday) so window
boundaries are always deterministic.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.config.loader import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    LoggingConfig,
    RiskConfig,
    StrategyConfig,
)
from src.data.base import BaseDataProvider
from src.experiments.sweep_runner import CachedDataProvider
from src.experiments.walk_forward_runner import (
    CANDIDATES,
    OUTPUT_COLS,
    WINDOWS_TRADING_DAYS,
    WalkForwardRunner,
)

_ET = ZoneInfo("America/New_York")
_REF_END = "2024-01-31"  # Wednesday — stable anchor for all tests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRADING_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def _make_day_bars(date_str: str) -> pd.DataFrame:
    session_start = pd.Timestamp(date_str, tz=_ET).replace(hour=9, minute=30)
    idx = pd.date_range(session_start, periods=78, freq="5min")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        index=idx,
    )


class FakeDataProvider(BaseDataProvider):
    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return pd.concat([_make_day_bars(d) for d in _TRADING_DAYS])


class CountingDataProvider(BaseDataProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        self.calls.append((symbol, start, end, interval))
        return pd.concat([_make_day_bars(d) for d in _TRADING_DAYS])


class ErrorDataProvider(BaseDataProvider):
    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        raise RuntimeError(f"Simulated failure for {symbol}")


def _make_config() -> AppConfig:
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


def _make_runner(
    windows: list[int] | None = None,
    provider: BaseDataProvider | None = None,
    candidates: dict | None = None,
    tmp: Path | None = None,
) -> tuple[WalkForwardRunner, Path]:
    if tmp is None:
        tmp = Path(tempfile.mkdtemp())
    return WalkForwardRunner(
        base_config=_make_config(),
        output_dir=tmp,
        candidates=candidates,
        windows=windows if windows is not None else [10, 20],
        data_provider=provider or FakeDataProvider(),
        reference_end_date=_REF_END,
    ), tmp


# ===========================================================================
# Default constants
# ===========================================================================

class TestConstants:
    def test_four_candidates(self):
        assert set(CANDIDATES.keys()) == {"A", "B0", "B1130", "C"}

    def test_all_candidates_use_qqq(self):
        for cid, params in CANDIDATES.items():
            assert params["symbols"] == ["QQQ"], f"Candidate {cid} should use QQQ"

    def test_candidate_a_params(self):
        p = CANDIDATES["A"]
        assert p["opening_range_end"] == "09:45"
        assert p["breakout_trigger"]  == "close"
        assert p["position_size_pct"] == 0.95

    def test_candidate_c_params(self):
        p = CANDIDATES["C"]
        assert p["opening_range_end"] == "10:00"
        assert p["breakout_trigger"]  == "high"
        assert p["position_size_pct"] == 0.50

    def test_candidate_b0_params(self):
        p = CANDIDATES["B0"]
        assert p["opening_range_end"]  == "09:45"
        assert p["breakout_trigger"]   == "close"
        assert p["position_size_pct"]  == 0.50
        assert p["entry_cutoff_time"]  is None

    def test_candidate_b1130_params(self):
        p = CANDIDATES["B1130"]
        assert p["opening_range_end"]  == "09:45"
        assert p["breakout_trigger"]   == "close"
        assert p["position_size_pct"]  == 0.50
        assert p["entry_cutoff_time"]  == "11:30"

    def test_five_default_windows(self):
        assert WINDOWS_TRADING_DAYS == [10, 20, 30, 45, 60]

    def test_output_cols_complete(self):
        required = {
            "candidate_id", "symbols", "opening_range_end", "breakout_trigger",
            "position_size_pct", "entry_cutoff_time", "start_date", "end_date",
            "num_trades", "total_return_pct", "annualized_return_pct",
            "max_drawdown_pct", "sharpe_ratio", "win_rate_pct", "final_equity",
            "status", "error",
        }
        assert required.issubset(set(OUTPUT_COLS))


# ===========================================================================
# Window date computation
# ===========================================================================

class TestWindowDates:
    def _runner(self) -> WalkForwardRunner:
        return WalkForwardRunner(
            base_config=_make_config(),
            output_dir=Path(tempfile.mkdtemp()),
            data_provider=FakeDataProvider(),
            reference_end_date=_REF_END,
        )

    def test_end_date_equals_reference(self):
        r = self._runner()
        _, end = r._window_dates(10)
        assert end == _REF_END

    def test_start_is_before_end(self):
        r = self._runner()
        for n in [10, 20, 30]:
            start, end = r._window_dates(n)
            assert start < end

    def test_larger_window_gives_earlier_start(self):
        r = self._runner()
        start10, _ = r._window_dates(10)
        start20, _ = r._window_dates(20)
        assert start20 < start10

    def test_saturday_reference_rolls_to_friday(self):
        r = WalkForwardRunner(
            base_config=_make_config(),
            output_dir=Path(tempfile.mkdtemp()),
            data_provider=FakeDataProvider(),
            reference_end_date="2024-02-03",  # Saturday
        )
        _, end = r._window_dates(10)
        assert end == "2024-02-02"  # Friday

    def test_sunday_reference_rolls_to_friday(self):
        r = WalkForwardRunner(
            base_config=_make_config(),
            output_dir=Path(tempfile.mkdtemp()),
            data_provider=FakeDataProvider(),
            reference_end_date="2024-02-04",  # Sunday
        )
        _, end = r._window_dates(10)
        assert end == "2024-02-02"  # Friday

    def test_window_10_start_is_10_bdays_before_end(self):
        r = self._runner()
        start, end = r._window_dates(10)
        end_ts   = pd.Timestamp(end)
        start_ts = pd.Timestamp(start)
        # 10 business days forward from start should equal end
        from pandas.tseries.offsets import BDay
        assert start_ts + BDay(10) == end_ts


# ===========================================================================
# Output structure
# ===========================================================================

class TestOutputStructure:
    def test_csv_created(self):
        runner, out = _make_runner()
        runner.run()
        assert (out / "walk_forward.csv").exists()

    def test_all_output_cols_present(self):
        runner, out = _make_runner()
        runner.run()
        df = pd.read_csv(out / "walk_forward.csv")
        for col in OUTPUT_COLS:
            assert col in df.columns, f"Missing column: {col}"

    def test_row_count_candidates_times_windows(self):
        runner, _ = _make_runner(windows=[10, 20])
        df = runner.run()
        assert len(df) == 4 * 2  # 4 candidates × 2 windows

    def test_single_window_gives_four_rows(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        assert len(df) == 4

    def test_candidate_ids_are_a_b0_b1130_c(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        assert set(df["candidate_id"]) == {"A", "B0", "B1130", "C"}

    def test_window_ordering_in_csv(self):
        runner, _ = _make_runner(windows=[10, 20, 30])
        df = runner.run()
        # Rows come out window-major: first all 4 candidates for window 10, then 20, then 30
        assert df["start_date"].iloc[0] == df["start_date"].iloc[3]  # all 4 share window 10
        assert df["start_date"].iloc[0] != df["start_date"].iloc[4]  # next window starts at 4

    def test_csv_has_no_extra_index_column(self):
        runner, out = _make_runner()
        runner.run()
        df = pd.read_csv(out / "walk_forward.csv")
        assert "Unnamed: 0" not in df.columns


# ===========================================================================
# Column values
# ===========================================================================

class TestColumnValues:
    def test_symbols_is_json_list(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        for val in df["symbols"]:
            parsed = json.loads(val)
            assert isinstance(parsed, list)

    def test_all_symbols_are_qqq(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        for val in df["symbols"]:
            assert json.loads(val) == ["QQQ"]

    def test_start_and_end_dates_are_iso_strings(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        for col in ["start_date", "end_date"]:
            for val in df[col]:
                pd.Timestamp(val)  # raises if not parseable

    def test_start_date_varies_by_window(self):
        runner, _ = _make_runner(windows=[10, 20])
        df = runner.run()
        starts = df["start_date"].unique()
        assert len(starts) == 2  # two different windows → two different starts

    def test_end_date_is_same_for_all_rows(self):
        runner, _ = _make_runner(windows=[10, 20])
        df = runner.run()
        assert df["end_date"].nunique() == 1
        assert df["end_date"].iloc[0] == _REF_END

    def test_opening_range_end_matches_candidate(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        row_a = df[df["candidate_id"] == "A"].iloc[0]
        row_c = df[df["candidate_id"] == "C"].iloc[0]
        assert row_a["opening_range_end"] == "09:45"
        assert row_c["opening_range_end"] == "10:00"

    def test_position_size_matches_candidate(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        row_a  = df[df["candidate_id"] == "A"].iloc[0]
        row_b0 = df[df["candidate_id"] == "B0"].iloc[0]
        assert abs(row_a["position_size_pct"] - 0.95) < 1e-9
        assert abs(row_b0["position_size_pct"] - 0.50) < 1e-9

    def test_metrics_are_numeric_on_success(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        for col in ["num_trades", "total_return_pct", "final_equity"]:
            assert pd.api.types.is_numeric_dtype(df[col]), f"{col} should be numeric"


# ===========================================================================
# Status and error columns
# ===========================================================================

class TestStatusError:
    def test_successful_rows_have_status_ok(self):
        runner, _ = _make_runner()
        df = runner.run()
        assert (df["status"] == "ok").all()

    def test_successful_rows_have_empty_error(self):
        runner, _ = _make_runner()
        df = runner.run()
        assert (df["error"] == "").all()

    def test_failed_rows_have_status_failed(self):
        runner, _ = _make_runner(provider=ErrorDataProvider())
        df = runner.run()
        assert (df["status"] == "failed").all()

    def test_failed_rows_have_error_message(self):
        runner, _ = _make_runner(provider=ErrorDataProvider())
        df = runner.run()
        assert df["error"].str.contains("Simulated failure").all()

    def test_failed_rows_have_null_metrics(self):
        runner, _ = _make_runner(provider=ErrorDataProvider())
        df = runner.run()
        assert df["num_trades"].isna().all()
        assert df["final_equity"].isna().all()


# ===========================================================================
# Caching
# ===========================================================================

class TestCaching:
    def test_provider_is_wrapped_in_cache(self):
        inner = FakeDataProvider()
        runner, _ = _make_runner(provider=inner)
        assert isinstance(runner._data_provider, CachedDataProvider)

    def test_already_cached_not_double_wrapped(self):
        already = CachedDataProvider(FakeDataProvider())
        runner = WalkForwardRunner(
            base_config=_make_config(),
            output_dir=Path(tempfile.mkdtemp()),
            data_provider=already,
            reference_end_date=_REF_END,
        )
        assert runner._data_provider is already

    def test_same_symbol_window_fetched_once(self):
        # All 3 candidates for window=10 use QQQ with the same date range.
        # The underlying provider should be called exactly once for that key.
        inner = CountingDataProvider()
        runner = WalkForwardRunner(
            base_config=_make_config(),
            output_dir=Path(tempfile.mkdtemp()),
            windows=[10],
            data_provider=inner,
            reference_end_date=_REF_END,
        )
        runner.run()
        qqq_calls = [c for c in runner._data_provider._cache if c[0] == "QQQ"]
        assert len(qqq_calls) == 1, "QQQ should be fetched only once for a single window"

    def test_different_windows_each_fetch_once(self):
        # 2 windows → 2 distinct (QQQ, start, end, interval) cache entries.
        inner = CountingDataProvider()
        runner = WalkForwardRunner(
            base_config=_make_config(),
            output_dir=Path(tempfile.mkdtemp()),
            windows=[10, 20],
            data_provider=inner,
            reference_end_date=_REF_END,
        )
        runner.run()
        qqq_calls = [c for c in runner._data_provider._cache if c[0] == "QQQ"]
        assert len(qqq_calls) == 2  # one per distinct date range


# ===========================================================================
# Config isolation
# ===========================================================================

class TestConfigIsolation:
    def test_base_config_dates_not_mutated(self):
        cfg = _make_config()
        orig_start = cfg.backtest.start_date
        orig_end   = cfg.backtest.end_date
        WalkForwardRunner(
            base_config=cfg,
            output_dir=Path(tempfile.mkdtemp()),
            windows=[10],
            data_provider=FakeDataProvider(),
            reference_end_date=_REF_END,
        ).run()
        assert cfg.backtest.start_date == orig_start
        assert cfg.backtest.end_date   == orig_end

    def test_base_config_symbols_not_mutated(self):
        cfg = _make_config()
        original = list(cfg.symbols)
        WalkForwardRunner(
            base_config=cfg,
            output_dir=Path(tempfile.mkdtemp()),
            windows=[10],
            data_provider=FakeDataProvider(),
            reference_end_date=_REF_END,
        ).run()
        assert cfg.symbols == original

    def test_base_config_strategy_params_not_mutated(self):
        cfg = _make_config()
        orig_or_end   = cfg.strategy.params["opening_range_end"]
        orig_trigger  = cfg.strategy.params["breakout_trigger"]
        orig_size     = cfg.strategy.params["position_size_pct"]
        WalkForwardRunner(
            base_config=cfg,
            output_dir=Path(tempfile.mkdtemp()),
            windows=[10],
            data_provider=FakeDataProvider(),
            reference_end_date=_REF_END,
        ).run()
        assert cfg.strategy.params["opening_range_end"]  == orig_or_end
        assert cfg.strategy.params["breakout_trigger"]   == orig_trigger
        assert cfg.strategy.params["position_size_pct"]  == orig_size


# ===========================================================================
# entry_cutoff_time in walk-forward output
# ===========================================================================

class TestEntryCutoffWalkForward:
    def test_entry_cutoff_time_column_in_dataframe(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        assert "entry_cutoff_time" in df.columns

    def test_entry_cutoff_time_column_in_csv(self):
        runner, out = _make_runner(windows=[10])
        runner.run()
        df = pd.read_csv(out / "walk_forward.csv")
        assert "entry_cutoff_time" in df.columns

    def test_b1130_has_cutoff_11_30(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        row = df[df["candidate_id"] == "B1130"].iloc[0]
        assert row["entry_cutoff_time"] == "11:30"

    def test_b0_has_null_cutoff(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        row = df[df["candidate_id"] == "B0"].iloc[0]
        assert pd.isna(row["entry_cutoff_time"]) or row["entry_cutoff_time"] is None

    def test_a_b0_c_have_null_cutoff(self):
        runner, _ = _make_runner(windows=[10])
        df = runner.run()
        for cid in ("A", "B0", "C"):
            row = df[df["candidate_id"] == cid].iloc[0]
            assert pd.isna(row["entry_cutoff_time"]) or row["entry_cutoff_time"] is None

    def test_patch_config_sets_entry_cutoff_time(self):
        runner, _ = _make_runner(windows=[10])
        params = {"symbols": ["QQQ"], "opening_range_end": "09:45",
                  "breakout_trigger": "close", "position_size_pct": 0.50,
                  "entry_cutoff_time": "11:30"}
        patched = runner._patch_config(params, "2024-01-02", "2024-01-05")
        assert patched.strategy.params["entry_cutoff_time"] == "11:30"

    def test_patch_config_sets_none_cutoff(self):
        runner, _ = _make_runner(windows=[10])
        params = {"symbols": ["QQQ"], "opening_range_end": "09:45",
                  "breakout_trigger": "close", "position_size_pct": 0.50,
                  "entry_cutoff_time": None}
        patched = runner._patch_config(params, "2024-01-02", "2024-01-05")
        assert patched.strategy.params["entry_cutoff_time"] is None
