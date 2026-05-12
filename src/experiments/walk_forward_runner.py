"""
experiments/walk_forward_runner.py
-----------------------------------
Walk-forward validation for the Opening Range Breakout strategy.

Runs a fixed set of candidate parameter configs over multiple rolling time
windows to test whether the best parameters remain stable across different
market samples.

Each (candidate, window) pair is a complete, isolated backtest.  Results are
written to ``walk_forward.csv`` for comparison.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.tseries.offsets import BDay

from src.backtest.engine import BacktestEngine
from src.config.loader import AppConfig
from src.data.base import BaseDataProvider
from src.data.yahoo_provider import YahooDataProvider
from src.experiments.sweep_runner import CachedDataProvider
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

CANDIDATES: dict[str, dict[str, Any]] = {
    "A": {
        "symbols":            ["QQQ"],
        "opening_range_end":  "09:45",
        "breakout_trigger":   "close",
        "position_size_pct":  0.95,
        "entry_cutoff_time":  None,
    },
    "B0": {
        "symbols":            ["QQQ"],
        "opening_range_end":  "09:45",
        "breakout_trigger":   "close",
        "position_size_pct":  0.50,
        "entry_cutoff_time":  None,
    },
    "B1130": {
        "symbols":            ["QQQ"],
        "opening_range_end":  "09:45",
        "breakout_trigger":   "close",
        "position_size_pct":  0.50,
        "entry_cutoff_time":  "11:30",
    },
    "C": {
        "symbols":            ["QQQ"],
        "opening_range_end":  "10:00",
        "breakout_trigger":   "high",
        "position_size_pct":  0.50,
        "entry_cutoff_time":  None,
    },
}

# Trading-day lookback windows.  Longer windows may exceed yfinance's ~60
# calendar-day 5-minute retention limit; those runs record status="failed".
WINDOWS_TRADING_DAYS: list[int] = [10, 20, 30, 45, 60]

_METRIC_COLS: list[str] = [
    "num_trades",
    "total_return_pct",
    "annualized_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "win_rate_pct",
    "final_equity",
]

OUTPUT_COLS: list[str] = [
    "candidate_id",
    "symbols",
    "opening_range_end",
    "breakout_trigger",
    "position_size_pct",
    "entry_cutoff_time",
    "start_date",
    "end_date",
    *_METRIC_COLS,
    "status",
    "error",
]


# ---------------------------------------------------------------------------
# WalkForwardRunner
# ---------------------------------------------------------------------------


class WalkForwardRunner:
    """Run each candidate config across multiple rolling lookback windows.

    Parameters
    ----------
    base_config:
        Template ``AppConfig``.  A deep copy is made for every run so the
        original is never modified.
    output_dir:
        Directory where ``walk_forward.csv`` is written.
    candidates:
        Mapping of candidate ID → parameter dict.  Defaults to
        :data:`CANDIDATES`.
    windows:
        List of lookback lengths in trading days.  Defaults to
        :data:`WINDOWS_TRADING_DAYS`.
    data_provider:
        Optional provider.  Automatically wrapped in
        :class:`~src.experiments.sweep_runner.CachedDataProvider` unless
        already cached, so the same symbol/date-range is fetched at most once.
    reference_end_date:
        ISO-8601 date string used as the "today" anchor when computing window
        boundaries.  Pass a fixed date in tests for determinism; leave ``None``
        in production to use the real current date.
    """

    def __init__(
        self,
        base_config: AppConfig,
        output_dir: str | Path,
        candidates: dict[str, dict[str, Any]] | None = None,
        windows: list[int] | None = None,
        data_provider: BaseDataProvider | None = None,
        reference_end_date: str | None = None,
    ) -> None:
        self._base_config         = base_config
        self._output_dir          = Path(output_dir)
        self._candidates          = candidates if candidates is not None else CANDIDATES
        self._windows             = windows    if windows    is not None else WINDOWS_TRADING_DAYS
        self._reference_end_date  = reference_end_date
        self._output_dir.mkdir(parents=True, exist_ok=True)

        raw = data_provider if data_provider is not None else YahooDataProvider()
        self._data_provider: BaseDataProvider = (
            raw if isinstance(raw, CachedDataProvider) else CachedDataProvider(raw)
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        """Execute all (candidate × window) combinations and write results.

        Returns
        -------
        pd.DataFrame
            One row per (candidate, window) pair (columns = :data:`OUTPUT_COLS`).
        """
        total = len(self._candidates) * len(self._windows)
        logger.info(
            "=== Walk-forward validation starting: %d candidates × %d windows = %d runs ===",
            len(self._candidates), len(self._windows), total,
        )

        rows: list[dict[str, Any]] = []
        run_num = 0

        for n_days in self._windows:
            start_date, end_date = self._window_dates(n_days)

            for candidate_id, params in self._candidates.items():
                run_num += 1
                cfg = self._patch_config(params, start_date, end_date)

                status = "ok"
                error  = ""
                try:
                    engine  = self._build_engine(cfg)
                    metrics = engine.run()["metrics"]
                except Exception as exc:
                    status  = "failed"
                    error   = str(exc)
                    metrics = {col: None for col in _METRIC_COLS}
                    logger.warning(
                        "Run %d/%d FAILED  candidate=%s  window=%d days  [%s, %s]: %s",
                        run_num, total, candidate_id, n_days, start_date, end_date, exc,
                    )

                row: dict[str, Any] = {
                    "candidate_id":      candidate_id,
                    "symbols":           json.dumps(list(params["symbols"])),
                    "opening_range_end": params["opening_range_end"],
                    "breakout_trigger":  params["breakout_trigger"],
                    "position_size_pct": params["position_size_pct"],
                    "entry_cutoff_time": params.get("entry_cutoff_time"),
                    "start_date":        start_date,
                    "end_date":          end_date,
                }
                for col in _METRIC_COLS:
                    row[col] = metrics.get(col)
                row["status"] = status
                row["error"]  = error

                rows.append(row)

                ret_str = (
                    f"{metrics.get('total_return_pct', 0.0):.2f}"
                    if metrics.get("total_return_pct") is not None else "?"
                )
                logger.info(
                    "Run %3d/%d | candidate=%s | window=%2d days [%s → %s]"
                    " → trades=%s  return=%s%%  [%s]",
                    run_num, total,
                    candidate_id, n_days, start_date, end_date,
                    metrics.get("num_trades", "?"),
                    ret_str,
                    status,
                )

        df   = pd.DataFrame(rows)[OUTPUT_COLS]
        path = self._output_dir / "walk_forward.csv"
        df.to_csv(path, index=False)
        logger.info("=== Walk-forward complete. %d results saved to %s ===", total, path)
        return df

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _window_dates(self, n_trading_days: int) -> tuple[str, str]:
        """Return (start_date, end_date) strings for a lookback of *n_trading_days*."""
        if self._reference_end_date is not None:
            end = pd.Timestamp(self._reference_end_date).normalize()
        else:
            end = pd.Timestamp.today().normalize()

        # Roll back to the most recent weekday if end falls on a weekend.
        if end.weekday() == 5:    # Saturday
            end -= pd.Timedelta(days=1)
        elif end.weekday() == 6:  # Sunday
            end -= pd.Timedelta(days=2)

        start = end - BDay(n_trading_days)
        return start.date().isoformat(), end.date().isoformat()

    def _patch_config(
        self,
        params: dict[str, Any],
        start_date: str,
        end_date: str,
    ) -> AppConfig:
        """Deep-copy the base config and apply *params* plus the window dates."""
        cfg = copy.deepcopy(self._base_config)

        cfg.symbols                                = list(params["symbols"])
        cfg.backtest.start_date                    = start_date
        cfg.backtest.end_date                      = end_date
        cfg.strategy.params["opening_range_end"]   = params["opening_range_end"]
        cfg.strategy.params["breakout_trigger"]    = params["breakout_trigger"]
        cfg.strategy.params["position_size_pct"]   = params["position_size_pct"]
        cfg.strategy.params["entry_cutoff_time"]   = params.get("entry_cutoff_time")

        return cfg

    def _build_engine(self, cfg: AppConfig) -> BacktestEngine:
        """Construct a fresh, fully-wired ``BacktestEngine`` from *cfg*."""
        strategy = OpeningRangeBreakout(params=cfg.strategy.params)
        portfolio = Portfolio(
            initial_capital=cfg.backtest.initial_capital,
            commission_per_share=cfg.backtest.commission_per_share,
            slippage_per_share=cfg.backtest.slippage_per_share,
        )
        force_exit     = cfg.strategy.params.get("force_exit_time", "15:55")
        stop_execution = cfg.strategy.params.get("stop_execution", "bar_close")
        risk_manager = RiskManager(
            force_exit_time=force_exit,
            max_open_positions=cfg.risk.max_open_positions,
            stop_execution=stop_execution,
        )
        return BacktestEngine(
            strategy=strategy,
            data_provider=self._data_provider,
            portfolio=portfolio,
            risk_manager=risk_manager,
            symbols=cfg.symbols,
            start_date=cfg.backtest.start_date,
            end_date=cfg.backtest.end_date,
            bar_interval=cfg.data.bar_interval,
            position_size_pct=cfg.strategy.params.get("position_size_pct", 0.95),
            stop_execution=stop_execution,
        )
