"""
backtest/backtest_runner.py
---------------------------
Offline backtest runner: wires strategy factory + BacktestEngine.

All computation is offline and deterministic.  BacktestEngine mutates a
simulated Portfolio during the run and may produce simulated trades and
order intents in memory.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
**No orders are placed.**
**Simulated trades and order intents are produced in memory only.**
**No broker order is sent.  No live record or file is written.**
**No settings are mutated.**
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.backtest.engine import BacktestEngine
from src.backtest.metrics import bars_per_year_for_interval
from src.backtest.trade import Trade
from src.data.base import BaseDataProvider
from src.execution.order_intent import OrderIntent
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.factory import build_strategy

_VALID_STOP_EXECUTION = frozenset({"bar_close", "stop_price"})
_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-/]{1,10}$")


@dataclass(frozen=True)
class BacktestRunConfig:
    """Immutable configuration for a single backtest run.

    Parameters
    ----------
    strategy_name:
        Strategy identifier accepted by ``build_strategy``.
    strategy_params:
        Free-form params forwarded verbatim to ``build_strategy``.
    symbols:
        Non-empty list of ticker symbols to trade.
    start_date:
        ISO-8601 date string, e.g. ``"2024-01-01"``.
    end_date:
        ISO-8601 date string (inclusive), e.g. ``"2024-12-31"``.
    bar_interval:
        Bar width string, e.g. ``"5m"``.  Must be accepted by
        ``bars_per_year_for_interval``.
    initial_capital:
        Starting cash in USD.  Must be strictly positive.
    position_size_pct:
        Fraction of available cash used for each entry.  Must be in (0, 1].
    stop_execution:
        Stop-loss fill mode: ``"bar_close"`` or ``"stop_price"``.
    """

    strategy_name: str
    strategy_params: dict[str, Any]
    symbols: list[str]
    start_date: str
    end_date: str
    bar_interval: str = "5m"
    initial_capital: float = 100_000.0
    position_size_pct: float = 0.95
    stop_execution: str = "bar_close"


@dataclass(frozen=True)
class BacktestRunResult:
    """Immutable result returned by ``run_backtest``.

    Attributes
    ----------
    metrics:
        Dict produced by ``compute_metrics``.
    trades:
        All completed trades from the engine.
    equity_curve:
        Bar-by-bar equity snapshot DataFrame.
    order_intents:
        Audit log of all intended orders (never sent to a broker).
    strategy_name:
        Echo of ``BacktestRunConfig.strategy_name``.
    symbols:
        Echo of ``BacktestRunConfig.symbols``.
    bar_interval:
        Echo of ``BacktestRunConfig.bar_interval``.
    broker_calls_made:
        Always ``False``; asserts that no broker was contacted.
    credentials_read:
        Always ``False``; asserts that no credentials were accessed.
    live_submit_enabled:
        Always ``False``; live order submission is never enabled.
    order_action_requested:
        Always ``False``; no market actions were requested.
    paper_trading_enabled:
        Always ``False``; paper trading is never enabled.
    recommendation_only:
        Always ``True``; output is advisory only.
    """

    metrics: dict[str, Any]
    trades: list[Trade]
    equity_curve: pd.DataFrame
    order_intents: list[OrderIntent]
    strategy_name: str
    symbols: list[str]
    bar_interval: str
    broker_calls_made: bool = False
    credentials_read: bool = False
    live_submit_enabled: bool = False
    order_action_requested: bool = False
    paper_trading_enabled: bool = False
    recommendation_only: bool = True


def _validate_config(config: BacktestRunConfig) -> None:
    """Raise ValueError('invalid backtest run config') if *config* is invalid.

    Raw values are never echoed in the error message.
    """
    try:
        if not isinstance(config.strategy_name, str) or not config.strategy_name:
            raise ValueError
        if not isinstance(config.strategy_params, dict):
            raise ValueError
        if not isinstance(config.symbols, list) or not config.symbols:
            raise ValueError
        if not all(
            isinstance(s, str) and bool(_VALID_SYMBOL_RE.match(s))
            for s in config.symbols
        ):
            raise ValueError
        if not isinstance(config.start_date, str) or not config.start_date:
            raise ValueError
        if not isinstance(config.end_date, str) or not config.end_date:
            raise ValueError
        bars_per_year_for_interval(config.bar_interval)
        cap = float(config.initial_capital)
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError
        pct = float(config.position_size_pct)
        if pct <= 0 or pct > 1 or pct != pct:
            raise ValueError
        if config.stop_execution not in _VALID_STOP_EXECUTION:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("invalid backtest run config")


def run_backtest(
    config: BacktestRunConfig,
    *,
    data_provider: BaseDataProvider,
) -> BacktestRunResult:
    """Run a full offline backtest and return the result.

    Parameters
    ----------
    config:
        Validated run configuration.
    data_provider:
        Market-data source (must implement ``BaseDataProvider``).

    Returns
    -------
    BacktestRunResult
        All metrics, trades, and the equity curve.  No live/paper actions
        are taken and no broker is contacted.

    Raises
    ------
    ValueError
        ``"invalid backtest run config"`` when *config* fails validation.
        ``"unknown strategy name"`` when *config.strategy_name* is not found.
        ``"invalid strategy parameters"`` when *config.strategy_params* are
        incompatible with the chosen strategy.
        Raw values are never echoed in any of these messages.
    """
    _validate_config(config)

    strategy = build_strategy(config.strategy_name, config.strategy_params)

    portfolio = Portfolio(
        initial_capital=config.initial_capital,
    )

    risk_manager = RiskManager(
        stop_execution=config.stop_execution,
    )

    engine = BacktestEngine(
        strategy=strategy,
        data_provider=data_provider,
        portfolio=portfolio,
        risk_manager=risk_manager,
        symbols=list(config.symbols),
        start_date=config.start_date,
        end_date=config.end_date,
        bar_interval=config.bar_interval,
        position_size_pct=config.position_size_pct,
        stop_execution=config.stop_execution,
    )

    raw = engine.run()

    return BacktestRunResult(
        metrics=raw["metrics"],
        trades=raw["trades"],
        equity_curve=raw["equity_curve"],
        order_intents=raw["order_intents"],
        strategy_name=config.strategy_name,
        symbols=list(config.symbols),
        bar_interval=config.bar_interval,
    )
