"""
backtest/trade_diagnostics.py
------------------------------
Pure offline helper: aggregate diagnostic fields from a list of closed trades.

**No Alpaca SDK imported.**
**No network library imported.**
**No credentials read.**
**No environment variable access.**
**No broker calls.**
**No orders placed.**
**No raw trade prices emitted in output.**
**No individual trade records emitted in output.**
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from src.backtest.trade import Trade

_NUMERIC_FIELDS = ("entry_price", "exit_price", "shares", "commission", "pnl")

_SAFETY: dict[str, Any] = {
    "broker_calls_made": False,
    "credentials_read": False,
    "network_calls_made": False,
    "order_action_requested": False,
}


def trade_summary_diagnostics(
    trades: list[Trade],
    *,
    total_bars: int | None = None,
) -> dict[str, Any]:
    """Return aggregate diagnostic fields from a list of closed trades.

    Parameters
    ----------
    trades:
        Closed round-trip trades (e.g. from BacktestRunResult.trades).
        Must be a list; mutation is not performed.
    total_bars:
        Total number of bars in the backtest window. Required to compute
        ``trades_per_100_bars`` and ``exposure_pct``; those fields are
        ``None`` when omitted or ≤ 0.

    Returns
    -------
    dict with the following keys:

        result              "PASS" or "BLOCKED"
        blocker             None (PASS) or a reason string (BLOCKED)
        trade_count         int
        trades_per_100_bars float or None
        avg_holding_bars    float or None — see Notes
        median_holding_bars float or None
        min_holding_bars    float or None
        max_holding_bars    float or None
        exposure_pct        float or None — see Notes
        entry_count         int
        exit_count          int
        unmatched_entries   int
        unmatched_exits     int
        win_rate_pct        float or None
        avg_trade_return_pct float or None
        avg_win_pct         float or None
        avg_loss_pct        float or None
        profit_factor       float or None
        exit_reason_counts  dict[str, int] or None
        broker_calls_made   False
        credentials_read    False
        network_calls_made  False
        order_action_requested False

    Raises
    ------
    Never raises. Returns BLOCKED on invalid input.

    Notes
    -----
    **Holding-period approximation.** ``avg/median/min/max_holding_bars``
    are computed as ``(exit_time - entry_time).total_seconds() / 3600``,
    i.e. the holding period expressed in approximate hours.  For same-bar
    trades (entry_time == exit_time), this value is 0.0.  The caller must
    interpret these values in terms of their bar interval (e.g. 1 unit
    ≈ 1 bar for 1-hour data, or ≈ 1/6.5 of a trading day for daily data).

    **Exposure approximation.** ``exposure_pct`` is a conservative lower
    bound: each trade is counted as 1 bar (``trade_count / total_bars``).
    For multi-bar trades the actual exposure is higher; an exact calculation
    requires the full bar index.
    """
    _empty: dict[str, Any] = {
        "trade_count": 0,
        "trades_per_100_bars": 0.0 if (total_bars is not None and total_bars > 0) else None,
        "avg_holding_bars": None,
        "median_holding_bars": None,
        "min_holding_bars": None,
        "max_holding_bars": None,
        "exposure_pct": 0.0 if (total_bars is not None and total_bars > 0) else None,
        "entry_count": 0,
        "exit_count": 0,
        "unmatched_entries": 0,
        "unmatched_exits": 0,
        "win_rate_pct": None,
        "avg_trade_return_pct": None,
        "avg_win_pct": None,
        "avg_loss_pct": None,
        "profit_factor": None,
        "exit_reason_counts": {},
    }

    if not trades:
        return {"result": "PASS", "blocker": None, **_empty, **_SAFETY}

    def _blocked_return(reason: str) -> dict[str, Any]:
        """Return a BLOCKED result dict with a safe fixed reason string."""
        return {
            "result": "BLOCKED",
            "blocker": reason,
            **{k: None for k in _empty},
            **_SAFETY,
        }

    # Non-finite numeric fields
    for t in trades:
        for field in _NUMERIC_FIELDS:
            val = float(getattr(t, field))
            if not math.isfinite(val):
                return _blocked_return(f"non-finite value in trade.{field}")

    # Structural validity (messages contain no raw trade values)
    for t in trades:
        if t.entry_price <= 0.0:
            return _blocked_return("entry_price must be positive")
        if t.shares <= 0.0:
            return _blocked_return("shares must be positive")
        if t.exit_time < t.entry_time:
            return _blocked_return("exit_time before entry_time")

    n = len(trades)

    # --- Holding period (hours) -------------------------------------------
    holding_hours: list[float] = [
        (t.exit_time - t.entry_time).total_seconds() / 3600.0
        for t in trades
    ]
    avg_h = statistics.mean(holding_hours)
    med_h = statistics.median(holding_hours)
    min_h = min(holding_hours)
    max_h = max(holding_hours)

    # --- Frequency / exposure ---------------------------------------------
    if total_bars is not None and total_bars > 0:
        trades_per_100 = n / total_bars * 100.0
        exposure = n / total_bars * 100.0  # conservative: 1 bar per trade
    else:
        trades_per_100 = None
        exposure = None

    # --- For closed round-trip list: entries == exits by construction ------
    entry_count = n
    exit_count = n
    unmatched_entries = 0
    unmatched_exits = 0

    # --- PnL statistics (no raw values emitted) ---------------------------
    wins = [t for t in trades if t.pnl > 0.0]
    losses = [t for t in trades if t.pnl <= 0.0]
    strict_losses = [t for t in trades if t.pnl < 0.0]

    win_rate = len(wins) / n * 100.0

    def _ret_pct(t: Trade) -> float | None:
        denom = t.entry_price * t.shares
        return (t.pnl / denom * 100.0) if denom != 0.0 else None

    all_rets: list[float] = [r for t in trades for r in [_ret_pct(t)] if r is not None]
    win_rets: list[float] = [r for t in wins for r in [_ret_pct(t)] if r is not None]
    loss_rets: list[float] = [r for t in losses for r in [_ret_pct(t)] if r is not None]

    avg_ret = statistics.mean(all_rets) if all_rets else None
    avg_win = statistics.mean(win_rets) if win_rets else None
    avg_loss = statistics.mean(loss_rets) if loss_rets else None

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in strict_losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0.0 else None

    # --- Exit reason breakdown --------------------------------------------
    reason_counts: dict[str, int] = {}
    for t in trades:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1

    return {
        "result": "PASS",
        "blocker": None,
        "trade_count": n,
        "trades_per_100_bars": trades_per_100,
        "avg_holding_bars": avg_h,
        "median_holding_bars": med_h,
        "min_holding_bars": min_h,
        "max_holding_bars": max_h,
        "exposure_pct": exposure,
        "entry_count": entry_count,
        "exit_count": exit_count,
        "unmatched_entries": unmatched_entries,
        "unmatched_exits": unmatched_exits,
        "win_rate_pct": win_rate,
        "avg_trade_return_pct": avg_ret,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "profit_factor": profit_factor,
        "exit_reason_counts": reason_counts,
        **_SAFETY,
    }
