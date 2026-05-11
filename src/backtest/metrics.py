"""
backtest/metrics.py
-------------------
Compute summary performance statistics from a completed backtest.

All metrics are pure functions — no side effects, easy to unit-test.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.trade import Trade


def compute_metrics(
    trades: list[Trade],
    equity_curve: pd.DataFrame,
    initial_capital: float,
    risk_free_rate: float = 0.05,
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
        # Use bar-level returns (5-min bars → annualise with 252 × 78 bars/day)
        eq_vals    = equity_curve["equity"].values
        bar_rets   = np.diff(eq_vals) / eq_vals[:-1]
        bars_per_year = 252 * 78  # 78 five-minute bars in a 6.5-hour session
        excess     = bar_rets - (risk_free_rate / bars_per_year)
        std        = np.std(excess, ddof=1)
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
