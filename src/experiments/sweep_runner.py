"""
experiments/sweep_runner.py
---------------------------
Grid-search over Opening Range Breakout strategy parameters.

Each cell of the grid is a complete, isolated backtest run.  Results are
collected into a single ``experiments.csv`` for comparison.
"""

from __future__ import annotations

import copy
import json
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.engine import BacktestEngine
from src.config.loader import AppConfig
from src.data.base import BaseDataProvider
from src.data.yahoo_provider import YahooDataProvider
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_GRID: dict[str, list[Any]] = {
    "symbols":            [["SPY"], ["QQQ"], ["SPY", "QQQ"]],
    "opening_range_end":  ["09:45", "10:00", "10:15"],
    "breakout_trigger":   ["close", "high"],
    "position_size_pct":  [0.25, 0.50, 0.95],
    "entry_cutoff_time":  [None, "10:30", "11:30", "13:30"],
}

_PARAM_COLS: list[str] = [
    "experiment_id",
    "symbols",
    "opening_range_end",
    "breakout_trigger",
    "position_size_pct",
    "entry_cutoff_time",
]

_METRIC_COLS: list[str] = [
    "num_trades",
    "total_return_pct",
    "annualized_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "win_rate_pct",
    "avg_winning_trade",
    "avg_losing_trade",
    "total_commission",
    "final_equity",
]

_STATUS_COLS: list[str] = ["status", "error"]

OUTPUT_COLS: list[str] = _PARAM_COLS + _METRIC_COLS + _STATUS_COLS


# ---------------------------------------------------------------------------
# CachedDataProvider
# ---------------------------------------------------------------------------


class CachedDataProvider(BaseDataProvider):
    """Memoising wrapper around any :class:`~src.data.base.BaseDataProvider`.

    On the first call for a given ``(symbol, start, end, interval)`` tuple the
    request is forwarded to the wrapped provider and the result is stored.
    Subsequent identical requests return a fresh ``DataFrame.copy()`` so callers
    cannot mutate the cache.

    Parameters
    ----------
    provider:
        The underlying provider to delegate cache misses to.
    """

    def __init__(self, provider: BaseDataProvider) -> None:
        self._provider = provider
        self._cache: dict[tuple[str, str, str, str], pd.DataFrame] = {}

    # Expose the underlying provider for testing / introspection.
    @property
    def provider(self) -> BaseDataProvider:
        return self._provider

    @property
    def cache_size(self) -> int:
        """Number of distinct (symbol, start, end, interval) entries cached."""
        return len(self._cache)

    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> pd.DataFrame:
        key = (symbol, start, end, interval)
        if key not in self._cache:
            logger.debug("CachedDataProvider: cache miss — fetching %s [%s, %s] %s",
                         symbol, start, end, interval)
            self._cache[key] = self._provider.fetch_bars(symbol, start, end, interval)
        return self._cache[key].copy()


# ---------------------------------------------------------------------------
# SweepRunner
# ---------------------------------------------------------------------------


class SweepRunner:
    """Run a parameter grid search over ORB strategy configurations.

    Parameters
    ----------
    base_config:
        Template configuration.  A deep copy is made for each experiment so
        the original is never modified.
    output_dir:
        Directory where ``experiments.csv`` is written.
    grid:
        Mapping of parameter name → list of values to sweep.
        Defaults to :data:`DEFAULT_GRID`.
    data_provider:
        Optional data provider.  Whatever is passed (or the default
        ``YahooDataProvider``) is automatically wrapped in
        :class:`CachedDataProvider` so each unique symbol/date range is
        fetched at most once across all experiments.  Pass a
        ``CachedDataProvider`` directly to skip double-wrapping.
    """

    def __init__(
        self,
        base_config: AppConfig,
        output_dir: str | Path,
        grid: dict[str, list[Any]] | None = None,
        data_provider: BaseDataProvider | None = None,
    ) -> None:
        self._base_config = base_config
        self._output_dir  = Path(output_dir)
        self._grid        = grid if grid is not None else DEFAULT_GRID
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Always use a caching layer; avoid double-wrapping.
        raw = data_provider if data_provider is not None else YahooDataProvider()
        self._data_provider: BaseDataProvider = (
            raw if isinstance(raw, CachedDataProvider) else CachedDataProvider(raw)
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        """Execute all experiments and write ``experiments.csv``.

        Returns
        -------
        pd.DataFrame
            One row per experiment (columns = :data:`OUTPUT_COLS`).
        """
        keys   = list(self._grid.keys())
        values = [self._grid[k] for k in keys]
        combos = list(product(*values))
        total  = len(combos)

        logger.info("=== Parameter sweep starting: %d experiments ===", total)

        rows: list[dict[str, Any]] = []

        for exp_id, combo in enumerate(combos, start=1):
            params = dict(zip(keys, combo))
            cfg    = self._patch_config(params)

            status = "ok"
            error  = ""
            try:
                engine  = self._build_engine(cfg)
                metrics = engine.run()["metrics"]
            except Exception as exc:
                status  = "failed"
                error   = str(exc)
                metrics = {col: None for col in _METRIC_COLS}
                logger.warning("Experiment %d/%d FAILED: %s", exp_id, total, exc)

            symbols_json = json.dumps(list(cfg.symbols))

            row: dict[str, Any] = {
                "experiment_id":     exp_id,
                "symbols":           symbols_json,
                "opening_range_end": params.get("opening_range_end"),
                "breakout_trigger":  params.get("breakout_trigger"),
                "position_size_pct": params.get("position_size_pct"),
                "entry_cutoff_time": params.get("entry_cutoff_time"),
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
                "Experiment %3d/%d | %-14s | or_end=%s | trigger=%-5s | size=%.2f"
                " → trades=%s  return=%s%%  [%s]",
                exp_id, total,
                symbols_json,
                row["opening_range_end"],
                row["breakout_trigger"],
                row["position_size_pct"],
                metrics.get("num_trades", "?"),
                ret_str,
                status,
            )

        df   = pd.DataFrame(rows)[OUTPUT_COLS]
        path = self._output_dir / "experiments.csv"
        df.to_csv(path, index=False)
        logger.info("=== Sweep complete. %d results saved to %s ===", total, path)
        return df

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _patch_config(self, params: dict[str, Any]) -> AppConfig:
        """Return a deep copy of the base config with *params* applied."""
        cfg = copy.deepcopy(self._base_config)
        if "symbols" in params:
            cfg.symbols = list(params["symbols"])
        if "opening_range_end" in params:
            cfg.strategy.params["opening_range_end"] = params["opening_range_end"]
        if "breakout_trigger" in params:
            cfg.strategy.params["breakout_trigger"] = params["breakout_trigger"]
        if "position_size_pct" in params:
            cfg.strategy.params["position_size_pct"] = params["position_size_pct"]
        if "entry_cutoff_time" in params:
            cfg.strategy.params["entry_cutoff_time"] = params["entry_cutoff_time"]
        return cfg

    def _build_engine(self, cfg: AppConfig) -> BacktestEngine:
        """Construct a fully-wired, fresh ``BacktestEngine`` from *cfg*."""
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
            daily_loss_limit_pct=cfg.risk.daily_loss_limit_pct,
            daily_loss_action=cfg.risk.daily_loss_action,
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
