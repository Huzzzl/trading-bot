"""
backtest/metrics.py
-------------------
Compute summary performance statistics from a completed backtest.

All metrics are pure, deterministic, and offline-only.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
**No orders are placed.**
**No trade records are written.**
**No portfolio state is mutated.**
**No settings are mutated.**
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.trade import Trade

# ---------------------------------------------------------------------------
# Annualisation lookup — US equity regular-session assumptions:
# 252 trading days/year · 6.5 trading hours/day · 390 trading minutes/day.
# For hourly intervals, only *complete* bars within the session are counted:
#   1h → 6 bars/day  (6.5-h session; last 30 min produces a partial bar)
#   2h → 3 bars/day
#   4h → 1 bar/day
# ---------------------------------------------------------------------------
_INTERVAL_BARS_PER_YEAR: dict[str, int] = {
    "1m":  252 * 390,   # 98 280
    "5m":  252 * 78,    # 19 656
    "15m": 252 * 26,    #  6 552
    "30m": 252 * 13,    #  3 276
    "1h":  252 * 6,     #  1 512
    "2h":  252 * 3,     #    756
    "4h":  252 * 1,     #    252
    "1d":  252,         #    252
}


def bars_per_year_for_interval(interval: str) -> int:
    """Return the number of bars per year for a given bar interval.

    Assumes US equity regular trading session (252 trading days per year,
    6.5 trading hours per day, 390 trading minutes per day).

    For intraday hourly intervals only complete bars within the regular
    session are counted:

    - ``"1h"`` → 6 bars per day (the last 30 min of the 6.5-h session
      produces a partial bar and is excluded from the count).
    - ``"2h"`` → 3 bars per day.
    - ``"4h"`` → 1 bar per day.

    Parameters
    ----------
    interval:
        Bar interval string.  Supported: ``"1m"``, ``"5m"``, ``"15m"``,
        ``"30m"``, ``"1h"``, ``"2h"``, ``"4h"``, ``"1d"``.

    Returns
    -------
    int
        Number of bars in a typical trading year for that interval.

    Raises
    ------
    ValueError
        ``"invalid interval"`` when *interval* is not a supported string.
        The raw value is never echoed.
    """
    result = _INTERVAL_BARS_PER_YEAR.get(interval)
    if result is None:
        raise ValueError("invalid interval")
    return result


def compute_metrics(
    trades: list[Trade],
    equity_curve: pd.DataFrame,
    initial_capital: float,
    risk_free_rate: float = 0.05,
    interval: str = "5m",
) -> dict[str, Any]:
    """Compute the full suite of backtest performance metrics.

    Parameters
    ----------
    trades:
        All completed trades from the backtest.
    equity_curve:
        DataFrame with a ``DatetimeIndex`` and an ``equity`` column
        (one row per bar snapshot).
    initial_capital:
        Starting cash, used to compute returns.
    risk_free_rate:
        Annualised risk-free rate for the Sharpe ratio (default 5 %).
    interval:
        Bar interval for Sharpe-ratio annualisation.  Must be one of the
        values accepted by ``bars_per_year_for_interval``.  Defaults to
        ``"5m"`` to preserve the previous behaviour for existing callers.

    Returns
    -------
    dict
        Keys described in the docstring below.
    """
    result: dict[str, Any] = {
        "initial_capital":        initial_capital,
        "num_trades":             len(trades),
        "total_return_pct":       0.0,
        "annualized_return_pct":  0.0,
        "max_drawdown_pct":       0.0,
        "sharpe_ratio":           0.0,
        "win_rate_pct":           0.0,
        "avg_winning_trade":      0.0,
        "avg_losing_trade":       0.0,
        "total_commission":       sum(t.commission for t in trades),
        "final_equity":           initial_capital,
    }

    if not trades and equity_curve.empty:
        return result

    # ---- Final equity ------------------------------------------------
    if not equity_curve.empty:
        final_equity: float = float(equity_curve["equity"].iloc[-1])
        result["final_equity"] = final_equity
    else:
        final_equity = initial_capital + sum(t.pnl for t in trades)
        result["final_equity"] = final_equity

    # ---- Total return ------------------------------------------------
    total_ret = (final_equity - initial_capital) / initial_capital * 100.0
    result["total_return_pct"] = round(total_ret, 4)

    # ---- Annualised return -------------------------------------------
    if not equity_curve.empty and len(equity_curve) >= 2:
        start_ts = equity_curve.index[0]
        end_ts   = equity_curve.index[-1]
        years    = (end_ts - start_ts).days / 365.25
        if years > 0 and final_equity > 0:
            ann_ret = ((final_equity / initial_capital) ** (1.0 / years) - 1.0) * 100.0
        else:
            ann_ret = 0.0
    else:
        ann_ret = 0.0
    result["annualized_return_pct"] = round(ann_ret, 4)

    # ---- Maximum drawdown -------------------------------------------
    if not equity_curve.empty:
        eq = equity_curve["equity"]
        rolling_max = eq.cummax()
        drawdowns   = (eq - rolling_max) / rolling_max * 100.0
        max_dd      = float(drawdowns.min())
    else:
        max_dd = 0.0
    result["max_drawdown_pct"] = round(max_dd, 4)

    # ---- Sharpe ratio ------------------------------------------------
    if not equity_curve.empty and len(equity_curve) >= 2:
        eq_vals       = equity_curve["equity"].values
        bar_rets      = np.diff(eq_vals) / eq_vals[:-1]
        bars_per_year = bars_per_year_for_interval(interval)
        excess        = bar_rets - (risk_free_rate / bars_per_year)
        std           = np.std(excess, ddof=1)
        if std > 0:
            sharpe = float(np.mean(excess) / std * math.sqrt(bars_per_year))
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0
    result["sharpe_ratio"] = round(sharpe, 4)

    # ---- Win / loss stats -------------------------------------------
    if trades:
        pnls      = [t.pnl for t in trades]
        winners   = [p for p in pnls if p > 0]
        losers    = [p for p in pnls if p <= 0]

        win_rate  = len(winners) / len(pnls) * 100.0
        avg_win   = float(np.mean(winners)) if winners else 0.0
        avg_loss  = float(np.mean(losers))  if losers  else 0.0

        result["win_rate_pct"]      = round(win_rate, 2)
        result["avg_winning_trade"] = round(avg_win,  2)
        result["avg_losing_trade"]  = round(avg_loss, 2)

    return result


def format_metrics(metrics: dict[str, Any]) -> str:
    """Return a human-readable summary string for console output."""
    lines = [
        "=" * 52,
        "  BACKTEST RESULTS",
        "=" * 52,
        f"  Initial capital     : ${metrics['initial_capital']:>12,.2f}",
        f"  Final equity        : ${metrics['final_equity']:>12,.2f}",
        f"  Total return        : {metrics['total_return_pct']:>10.2f} %",
        f"  Annualised return   : {metrics['annualized_return_pct']:>10.2f} %",
        f"  Max drawdown        : {metrics['max_drawdown_pct']:>10.2f} %",
        f"  Sharpe ratio        : {metrics['sharpe_ratio']:>10.4f}",
        "-" * 52,
        f"  # Trades            : {metrics['num_trades']:>12}",
        f"  Win rate            : {metrics['win_rate_pct']:>10.2f} %",
        f"  Avg winning trade   : ${metrics['avg_winning_trade']:>12,.2f}",
        f"  Avg losing trade    : ${metrics['avg_losing_trade']:>12,.2f}",
        f"  Total commission    : ${metrics['total_commission']:>12,.2f}",
        "=" * 52,
    ]
    return "\n".join(lines)
