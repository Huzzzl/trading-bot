"""
tests/test_sweep.py
-------------------
Unit tests for SweepRunner.

All tests use a FakeDataProvider that returns synthetic 5-minute OHLCV bars —
no network calls, no yfinance, no broker.
"""

from __future__ import annotations

import tempfile
from itertools import product
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
from src.experiments.sweep_runner import DEFAULT_GRID, OUTPUT_COLS, SweepRunner

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_TRADING_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def _make_day_bars(date_str: str) -> pd.DataFrame:
    """78 flat 5-minute bars for one trading session."""
    session_start = pd.Timestamp(date_str, tz=_ET).replace(hour=9, minute=30)
    idx = pd.date_range(session_start, periods=78, freq="5min")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        index=idx,
    )


class FakeDataProvider(BaseDataProvider):
    """Returns synthetic bars for any symbol/date without touching the network."""

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return pd.concat([_make_day_bars(d) for d in _TRADING_DAYS])


def _make_config() -> AppConfig:
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-02",
            end_date="2024-01-05",
            initial_capital=100_000.0,
            commission_per_share=0.005,
            slippage_per_share=0.01,
        ),
        symbols=["SPY"],
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


def _minimal_grid(**overrides) -> dict:
    """Single-cell grid, optionally overriding individual parameter lists."""
    base = {
        "symbols":           [["SPY"]],
        "opening_range_end": ["10:00"],
        "breakout_trigger":  ["close"],
        "position_size_pct": [0.95],
    }
    base.update(overrides)
    return base


def _make_sweeper(grid=None, tmp=None) -> tuple[SweepRunner, Path]:
    if tmp is None:
        tmp = Path(tempfile.mkdtemp())
    return SweepRunner(
        base_config=_make_config(),
        output_dir=tmp,
        grid=grid or _minimal_grid(),
        data_provider=FakeDataProvider(),
    ), tmp


# ===========================================================================
# DEFAULT_GRID combinatorics (no backtest needed)
# ===========================================================================

class TestDefaultGrid:
    def test_54_combinations(self):
        keys   = list(DEFAULT_GRID.keys())
        values = [DEFAULT_GRID[k] for k in keys]
        assert len(list(product(*values))) == 54

    def test_all_four_sweep_dimensions_present(self):
        assert "symbols"           in DEFAULT_GRID
        assert "opening_range_end" in DEFAULT_GRID
        assert "breakout_trigger"  in DEFAULT_GRID
        assert "position_size_pct" in DEFAULT_GRID

    def test_expected_symbol_sets(self):
        assert ["SPY"]        in DEFAULT_GRID["symbols"]
        assert ["QQQ"]        in DEFAULT_GRID["symbols"]
        assert ["SPY", "QQQ"] in DEFAULT_GRID["symbols"]

    def test_expected_or_end_values(self):
        assert set(DEFAULT_GRID["opening_range_end"]) == {"09:45", "10:00", "10:15"}

    def test_expected_triggers(self):
        assert set(DEFAULT_GRID["breakout_trigger"]) == {"close", "high"}

    def test_expected_position_sizes(self):
        assert set(DEFAULT_GRID["position_size_pct"]) == {0.25, 0.50, 0.95}


# ===========================================================================
# SweepRunner.run() — structure and output
# ===========================================================================

class TestSweepRunnerStructure:
    def test_single_experiment_returns_one_row(self):
        sweeper, _ = _make_sweeper()
        df = sweeper.run()
        assert len(df) == 1

    def test_two_experiments_returns_two_rows(self):
        grid = _minimal_grid(opening_range_end=["09:45", "10:00"])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert len(df) == 2

    def test_row_count_matches_grid_product(self):
        grid = _minimal_grid(
            opening_range_end=["09:45", "10:00", "10:15"],
            breakout_trigger=["close", "high"],
        )
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert len(df) == 6   # 3 × 2

    def test_experiments_csv_is_created(self):
        sweeper, out = _make_sweeper()
        sweeper.run()
        assert (out / "experiments.csv").exists()

    def test_output_cols_all_present(self):
        sweeper, out = _make_sweeper()
        sweeper.run()
        df = pd.read_csv(out / "experiments.csv")
        for col in OUTPUT_COLS:
            assert col in df.columns, f"Missing column: {col}"

    def test_experiment_id_is_sequential(self):
        grid = _minimal_grid(opening_range_end=["09:45", "10:00", "10:15"])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert list(df["experiment_id"]) == [1, 2, 3]

    def test_metrics_columns_are_numeric(self):
        sweeper, _ = _make_sweeper()
        df = sweeper.run()
        for col in ["num_trades", "total_return_pct", "final_equity", "total_commission"]:
            assert pd.api.types.is_numeric_dtype(df[col]), f"{col} should be numeric"


# ===========================================================================
# Parameter variation
# ===========================================================================

class TestParameterVariation:
    def test_symbols_column_reflects_grid(self):
        grid = _minimal_grid(symbols=[["SPY"], ["QQQ"]])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert set(df["symbols"]) == {"SPY", "QQQ"}

    def test_multi_symbol_formatted_with_plus(self):
        grid = _minimal_grid(symbols=[["SPY", "QQQ"]])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert df["symbols"].iloc[0] == "SPY+QQQ"

    def test_opening_range_end_column_reflects_grid(self):
        grid = _minimal_grid(opening_range_end=["09:45", "10:15"])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert set(df["opening_range_end"]) == {"09:45", "10:15"}

    def test_breakout_trigger_column_reflects_grid(self):
        grid = _minimal_grid(breakout_trigger=["close", "high"])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert set(df["breakout_trigger"]) == {"close", "high"}

    def test_position_size_pct_column_reflects_grid(self):
        grid = _minimal_grid(position_size_pct=[0.25, 0.50, 0.95])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        assert set(df["position_size_pct"]) == {0.25, 0.50, 0.95}


# ===========================================================================
# Config isolation
# ===========================================================================

class TestConfigIsolation:
    def test_base_config_symbols_not_mutated(self):
        cfg = _make_config()
        original = list(cfg.symbols)
        SweepRunner(
            base_config=cfg,
            output_dir=Path(tempfile.mkdtemp()),
            grid=_minimal_grid(symbols=[["QQQ"]]),
            data_provider=FakeDataProvider(),
        ).run()
        assert cfg.symbols == original

    def test_base_config_opening_range_end_not_mutated(self):
        cfg = _make_config()
        original = cfg.strategy.params["opening_range_end"]
        SweepRunner(
            base_config=cfg,
            output_dir=Path(tempfile.mkdtemp()),
            grid=_minimal_grid(opening_range_end=["09:45"]),
            data_provider=FakeDataProvider(),
        ).run()
        assert cfg.strategy.params["opening_range_end"] == original

    def test_base_config_breakout_trigger_not_mutated(self):
        cfg = _make_config()
        original = cfg.strategy.params["breakout_trigger"]
        SweepRunner(
            base_config=cfg,
            output_dir=Path(tempfile.mkdtemp()),
            grid=_minimal_grid(breakout_trigger=["high"]),
            data_provider=FakeDataProvider(),
        ).run()
        assert cfg.strategy.params["breakout_trigger"] == original

    def test_base_config_position_size_not_mutated(self):
        cfg = _make_config()
        original = cfg.strategy.params["position_size_pct"]
        SweepRunner(
            base_config=cfg,
            output_dir=Path(tempfile.mkdtemp()),
            grid=_minimal_grid(position_size_pct=[0.25]),
            data_provider=FakeDataProvider(),
        ).run()
        assert cfg.strategy.params["position_size_pct"] == original

    def test_independent_runs_do_not_share_strategy_state(self):
        # Two experiments with the same symbol must not bleed opening-range
        # cache from the first run into the second.
        grid = _minimal_grid(opening_range_end=["09:45", "10:00"])
        sweeper, _ = _make_sweeper(grid=grid)
        df = sweeper.run()
        # Both should complete without exception and return equal num_trades
        # (flat data → 0 trades either way); the key assertion is no error.
        assert len(df) == 2
        assert (df["num_trades"] == 0).all()


# ===========================================================================
# CSV persistence
# ===========================================================================

class TestCsvOutput:
    def test_csv_has_no_index_column(self):
        sweeper, out = _make_sweeper()
        sweeper.run()
        df = pd.read_csv(out / "experiments.csv")
        assert "Unnamed: 0" not in df.columns

    def test_csv_row_count_matches_returned_dataframe(self):
        grid = _minimal_grid(breakout_trigger=["close", "high"])
        sweeper, out = _make_sweeper(grid=grid)
        df_returned = sweeper.run()
        df_csv = pd.read_csv(out / "experiments.csv")
        assert len(df_returned) == len(df_csv)

    def test_overwrite_on_second_run(self):
        grid = _minimal_grid()
        sweeper, out = _make_sweeper(grid=grid)
        sweeper.run()
        # Second run with a different grid — CSV should reflect new results.
        grid2 = _minimal_grid(opening_range_end=["09:45", "10:00"])
        sweeper2 = SweepRunner(
            base_config=_make_config(),
            output_dir=out,
            grid=grid2,
            data_provider=FakeDataProvider(),
        )
        sweeper2.run()
        df = pd.read_csv(out / "experiments.csv")
        assert len(df) == 2
