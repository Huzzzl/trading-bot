"""
tools/backtest_strategy_eval.py
-------------------------------
Read-only backtest evaluation of the current live paper strategy.

The paper strategy that ``run_automated_paper_cycle`` submits is the
SMA-crossover engine in ``src.strategy.signal_engine`` (short/long
simple moving averages of the close series). This tool replays the
same crossover rule against cached SPY 60m bars and reports the
metrics an operator would look at before considering a change:

    total_return, buy_and_hold_return, max_drawdown, sharpe_ratio,
    trade_count, win_rate, avg_trade_return, avg_holding_bars,
    profit_factor, exposure_time, final_equity.

Usage::

    python -m src.tools.backtest_strategy_eval
    python -m src.tools.backtest_strategy_eval \\
        --short-windows 5,10,20 --long-windows 20,50,100

The CLI is read-only:

* no Alpaca imports, no credentials read, no network calls
* no order submission, no ledger writes
* no changes to production trading behavior or the scheduler

Backtest execution model
------------------------
The signal at bar ``t`` uses ``closes[0..t]`` only — no lookahead.

Default execution is ``next_open``: the signal at bar ``t`` is only
knowable after the bar closes, so the fill happens at
``bars[t+1].open``. A signal that fires on the final bar cannot
execute — there is no next bar — and no new trade is opened.

``--execution same_close`` is available only for diagnostic
comparison: it fills at ``bars[t].close`` on the same bar the signal
was generated. It is optimistic (the strategy could not act on
close[t] in real time) and must not be treated as a realistic
result. Tests assert the crossover rule, the no-lookahead property,
and that the two execution modes actually behave differently.

Commissions and slippage (``--commission-bps``, ``--slippage-bps``,
both default 0) are applied on both entry and exit; buy price is
scaled by ``1 + cost/10_000`` and sell price by ``1 - cost/10_000``.

A position still open at the end of the run is not counted as a
completed trade — completed-trade metrics (``win_rate``,
``profit_factor``, ``avg_trade_return``, ``avg_holding_bars``)
exclude it. ``final_equity`` still marks the open position to market
against the last close, and open-position details are surfaced via
``open_position`` / ``open_entry_price`` / ``open_entry_index`` /
``open_unrealized_return``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

_DEFAULT_SYMBOL = "SPY"
_DEFAULT_INTERVAL = "60m"
_DEFAULT_CACHE_DIR = Path("data/cache")
_DEFAULT_OUTPUT_DIR = Path("logs/backtests")
_DEFAULT_SHORT = 10
_DEFAULT_LONG = 20

# Annualization constants for the Sharpe ratio. The 60m bar count per
# trading year comes from ~6.5 regular-hours bars * 252 sessions.
_BARS_PER_YEAR = {
    "60m": 6.5 * 252,
    "1h": 6.5 * 252,
    "1d": 252,
}
_REQUIRED_COLS = ("open", "high", "low", "close", "volume")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


class BacktestError(Exception):
    """Raised on any user-facing backtest CLI failure."""


def _load_bars_from_path(path: Path) -> "list[Bar]":
    """Load a single OHLCV cache file into a bar list."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise BacktestError(f"pandas required: {exc}") from exc
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise BacktestError(f"{path.name} missing columns: {missing}")
    if df.index.hasnans:
        raise BacktestError(f"{path.name} has unparseable timestamps")
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    bars: list[Bar] = []
    for ts, row in df.iterrows():
        try:
            o = float(row["open"])
            h = float(row["high"])
            lo = float(row["low"])
            c = float(row["close"])
            v = float(row["volume"])
        except (TypeError, ValueError) as exc:
            raise BacktestError(f"{path.name} malformed OHLCV row: {exc}") from exc
        if any(math.isnan(x) or math.isinf(x) for x in (o, h, lo, c, v)):
            raise BacktestError(f"{path.name} non-finite OHLCV value")
        bars.append(Bar(ts=ts, open=o, high=h, low=lo, close=c, volume=v))
    return bars


def load_cached_bars(cache_dir: Path, symbol: str, interval: str) -> "list[Bar]":
    """Return bars from the newest fully-valid cache file for symbol/interval."""
    if not cache_dir.is_dir():
        raise BacktestError(f"cache directory not found: {cache_dir}")
    safe_iv = re.sub(r"[^\w\-]", "_", interval)
    candidates = sorted(
        list(cache_dir.glob(f"{symbol}_*_{safe_iv}.parquet"))
        + list(cache_dir.glob(f"{symbol}_*_{safe_iv}.csv"))
    )
    if not candidates:
        raise BacktestError(
            f"no cached bars found for {symbol}/{interval} under {cache_dir}"
        )
    last_error: str | None = None
    valid: list[list[Bar]] = []
    for path in candidates:
        try:
            valid.append(_load_bars_from_path(path))
        except BacktestError as exc:
            last_error = str(exc)
    if not valid:
        raise BacktestError(last_error or "no valid cache file")
    valid.sort(key=lambda b: b[-1].ts)
    return valid[-1]


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    ts: Any
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Trade:
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float

    @property
    def bars_held(self) -> int:
        return self.exit_index - self.entry_index

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class BacktestResult:
    short_window: int
    long_window: int
    execution: str
    commission_bps: float
    slippage_bps: float
    total_return: float
    buy_and_hold_return: float
    max_drawdown: float
    sharpe_ratio: float
    trade_count: int
    completed_trade_count: int
    win_rate: float
    avg_trade_return: float
    avg_holding_bars: float
    profit_factor: float
    exposure_time: float
    final_equity: float
    bar_count: int
    open_position: bool
    open_entry_price: float | None
    open_entry_index: int | None
    open_unrealized_return: float | None
    trades: list[Trade] = field(default_factory=list)

    def to_dict(self, include_trades: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "short_window": self.short_window,
            "long_window": self.long_window,
            "execution": self.execution,
            "commission_bps": self.commission_bps,
            "slippage_bps": self.slippage_bps,
            "bar_count": self.bar_count,
            "total_return": self.total_return,
            "buy_and_hold_return": self.buy_and_hold_return,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "trade_count": self.trade_count,
            "completed_trade_count": self.completed_trade_count,
            "win_rate": self.win_rate,
            "avg_trade_return": self.avg_trade_return,
            "avg_holding_bars": self.avg_holding_bars,
            "profit_factor": self.profit_factor,
            "exposure_time": self.exposure_time,
            "final_equity": self.final_equity,
            "open_position": self.open_position,
            "open_entry_price": self.open_entry_price,
            "open_entry_index": self.open_entry_index,
            "open_unrealized_return": self.open_unrealized_return,
        }
        if include_trades:
            out["trades"] = [
                {
                    "entry_index": t.entry_index,
                    "exit_index": t.exit_index,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "bars_held": t.bars_held,
                    "return_pct": t.return_pct,
                }
                for t in self.trades
            ]
        return out


def _sma(values: Sequence[float], window: int, index: int) -> float | None:
    """Return SMA of ``values[index-window+1 .. index]`` or ``None`` if
    fewer than ``window`` values are available. Uses only prior + current
    values — never peeks ahead."""
    if index + 1 < window:
        return None
    total = 0.0
    for i in range(index - window + 1, index + 1):
        total += values[i]
    return total / window


def sma_crossover_signal(
    closes: Sequence[float],
    index: int,
    short_window: int,
    long_window: int,
    has_position: bool,
) -> str:
    """Return one of ``"BUY"``, ``"SELL"``, ``"HOLD"``.

    Mirrors the rule in ``src.strategy.signal_engine.evaluate_signal`` but
    is a pure function over closes at ``index`` — the caller supplies the
    position state. The signal at ``index`` uses only ``closes[0..index]``.
    """
    short = _sma(closes, short_window, index)
    long = _sma(closes, long_window, index)
    if short is None or long is None:
        return "HOLD"
    if short > long and not has_position:
        return "BUY"
    if short < long and has_position:
        return "SELL"
    return "HOLD"


_EXECUTION_MODES = ("next_open", "same_close")


def run_backtest(
    bars: Sequence[Bar],
    short_window: int,
    long_window: int,
    *,
    initial_equity: float = 10_000.0,
    bars_per_year: float = _BARS_PER_YEAR[_DEFAULT_INTERVAL],
    execution: str = "next_open",
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> BacktestResult:
    """Replay the SMA crossover rule bar by bar.

    Execution model
    ---------------
    ``next_open`` (default and realistic): the signal at bar ``t`` uses
    ``closes[0..t]`` — it can only be acted on after the bar closes, so
    the fill happens at ``bars[t+1].open``. Signals on the final bar
    cannot execute (there is no next bar).

    ``same_close`` (diagnostic only): the signal at bar ``t`` fills at
    ``bars[t].close``. This is optimistic and should be used only for
    comparison; the default is ``next_open``.

    Costs
    -----
    ``commission_bps`` and ``slippage_bps`` are applied on both entry
    and exit: buy price is scaled by ``(1 + cost/10_000)``, sell price
    by ``(1 - cost/10_000)``. Total round-trip cost is
    ``2 * (commission_bps + slippage_bps)`` basis points.

    Open positions
    --------------
    A position still open at the end of the run is NOT counted as a
    completed trade — completed-trade metrics (win_rate, profit_factor,
    avg_trade_return, avg_holding_bars) exclude it. The open position
    is mark-to-market against the final close so ``final_equity``
    reflects total portfolio value; open-position details are surfaced
    via ``open_position`` / ``open_entry_price`` / ``open_entry_index``
    / ``open_unrealized_return``.
    """
    if execution not in _EXECUTION_MODES:
        raise BacktestError(
            f"execution must be one of {_EXECUTION_MODES}, got {execution!r}"
        )
    if short_window <= 0 or long_window <= 0:
        raise BacktestError("windows must be positive")
    if short_window >= long_window:
        raise BacktestError(
            f"short_window ({short_window}) must be < long_window ({long_window})"
        )
    if commission_bps < 0 or slippage_bps < 0:
        raise BacktestError("commission_bps and slippage_bps must be >= 0")

    closes = [b.close for b in bars]
    n = len(bars)
    cost_factor = (commission_bps + slippage_bps) / 10_000.0

    equity_curve: list[float] = []
    trades: list[Trade] = []
    position_qty: float = 0.0
    entry_price: float | None = None   # cost-adjusted
    entry_index: int | None = None
    cash = initial_equity
    exposed_bars = 0

    def _buy_fill_price(bar: Bar) -> float:
        return bar.close if execution == "same_close" else bar.open

    def _sell_fill_price(bar: Bar) -> float:
        return bar.close if execution == "same_close" else bar.open

    for i, bar in enumerate(bars):
        has_position = position_qty > 0

        # Signal on THIS bar acts here in same_close mode; in next_open
        # mode it was generated at bar i-1 and fills at bar i's open.
        if execution == "same_close":
            signal = sma_crossover_signal(
                closes, i, short_window, long_window, has_position,
            )
        else:
            if i == 0:
                signal = "HOLD"
            else:
                signal = sma_crossover_signal(
                    closes, i - 1, short_window, long_window, has_position,
                )

        if signal == "BUY" and not has_position:
            raw = _buy_fill_price(bar)
            effective = raw * (1 + cost_factor)
            if effective > 0:
                position_qty = cash / effective
                cash = 0.0
                entry_price = effective
                entry_index = i
        elif signal == "SELL" and has_position:
            raw = _sell_fill_price(bar)
            effective = raw * (1 - cost_factor)
            cash = position_qty * effective
            trades.append(Trade(
                entry_index=entry_index if entry_index is not None else i,
                exit_index=i,
                entry_price=entry_price if entry_price is not None else raw,
                exit_price=effective,
            ))
            position_qty = 0.0
            entry_price = None
            entry_index = None

        if position_qty > 0:
            equity_curve.append(position_qty * bar.close)
            exposed_bars += 1
        else:
            equity_curve.append(cash)

    # Open position at the end: mark to market for equity, but do NOT
    # record as a completed trade.
    open_position = position_qty > 0
    open_entry_price = entry_price if open_position else None
    open_entry_index = entry_index if open_position else None
    open_unrealized_return: float | None = None
    if open_position and entry_price and entry_price > 0:
        open_unrealized_return = (bars[-1].close - entry_price) / entry_price

    final_equity = equity_curve[-1] if equity_curve else initial_equity
    total_return = (final_equity - initial_equity) / initial_equity

    first_close = bars[0].close if bars else 0.0
    last_close = bars[-1].close if bars else 0.0
    bh_return = (
        (last_close - first_close) / first_close if first_close > 0 else 0.0
    )

    peak = -math.inf
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (eq - peak) / peak
            if dd < max_dd:
                max_dd = dd

    returns: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            returns.append((equity_curve[i] - prev) / prev)
    sharpe = _sharpe_ratio(returns, bars_per_year)

    # Completed-trade metrics exclude any still-open position.
    completed_trade_count = len(trades)
    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [t.return_pct for t in trades if t.return_pct <= 0]
    win_rate = (len(wins) / completed_trade_count) if completed_trade_count else 0.0
    avg_trade_return = (
        sum(t.return_pct for t in trades) / completed_trade_count
        if completed_trade_count else 0.0
    )
    avg_holding_bars = (
        sum(t.bars_held for t in trades) / completed_trade_count
        if completed_trade_count else 0.0
    )
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    if gross_losses > 0:
        profit_factor = gross_wins / gross_losses
    elif gross_wins > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    exposure_time = exposed_bars / n if n else 0.0

    return BacktestResult(
        short_window=short_window,
        long_window=long_window,
        execution=execution,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        total_return=total_return,
        buy_and_hold_return=bh_return,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
        trade_count=completed_trade_count,
        completed_trade_count=completed_trade_count,
        win_rate=win_rate,
        avg_trade_return=avg_trade_return,
        avg_holding_bars=avg_holding_bars,
        profit_factor=profit_factor,
        exposure_time=exposure_time,
        final_equity=final_equity,
        bar_count=n,
        open_position=open_position,
        open_entry_price=open_entry_price,
        open_entry_index=open_entry_index,
        open_unrealized_return=open_unrealized_return,
        trades=trades,
    )


def _sharpe_ratio(returns: Sequence[float], bars_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(bars_per_year)


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def _parse_window_list(raw: str) -> list[int]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError as exc:
            raise BacktestError(f"invalid window value: {p!r}") from exc
    if not out:
        raise BacktestError("window list is empty")
    return out


def _return_over_drawdown(r: dict[str, Any]) -> float:
    """Return / abs(max_drawdown). Zero drawdown becomes +inf when the
    strategy actually made money, else 0.0."""
    dd = r.get("max_drawdown") or 0.0
    tr = r.get("total_return") or 0.0
    if dd == 0:
        return float("inf") if tr > 0 else 0.0
    return tr / abs(dd)


def _pick_best(
    results: Sequence[dict[str, Any]],
    key: str | Any,
    *,
    filter_zero_trades: bool = False,
) -> dict[str, Any] | None:
    """Return the single result maximising ``key``.

    ``key`` is either a string field name or a callable. When
    ``filter_zero_trades`` is True, configs with
    ``completed_trade_count == 0`` are excluded — unless every config
    has zero trades, in which case the whole pool is kept so a "best"
    is still surfaced.
    """
    if not results:
        return None
    pool = list(results)
    if filter_zero_trades:
        non_zero = [r for r in pool if r.get("completed_trade_count", 0) > 0]
        if non_zero:
            pool = non_zero
    key_fn = key if callable(key) else (lambda r, _k=key: r.get(_k, 0.0))
    return max(pool, key=key_fn)


def _top_n(
    results: Sequence[dict[str, Any]],
    key: str,
    n: int,
) -> list[dict[str, Any]]:
    return sorted(
        results, key=lambda r: r.get(key, 0.0), reverse=True,
    )[:n]


def rank_sweep_results(
    results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Return ranking summaries over a completed sweep."""
    if not results:
        return {
            "best_by_total_return": None,
            "best_by_sharpe_ratio": None,
            "best_by_max_drawdown": None,
            "best_by_profit_factor": None,
            "best_by_return_over_drawdown": None,
            "top_10_by_total_return": [],
            "top_10_by_sharpe_ratio": [],
        }
    return {
        "best_by_total_return":         _pick_best(results, "total_return"),
        "best_by_sharpe_ratio":         _pick_best(results, "sharpe_ratio"),
        # max_drawdown is <= 0; the "best" is the closest-to-zero value.
        # Zero-trade configs trivially score 0 here, so filter them out
        # unless every config has zero trades.
        "best_by_max_drawdown":         _pick_best(
            results, "max_drawdown", filter_zero_trades=True,
        ),
        "best_by_profit_factor":        _pick_best(
            results, "profit_factor", filter_zero_trades=True,
        ),
        "best_by_return_over_drawdown": _pick_best(
            results, _return_over_drawdown,
        ),
        "top_10_by_total_return":       _top_n(results, "total_return", 10),
        "top_10_by_sharpe_ratio":       _top_n(results, "sharpe_ratio", 10),
    }


def compare_to_baseline(
    baseline: BacktestResult | dict[str, Any],
) -> dict[str, Any]:
    """Return baseline vs buy-and-hold comparison fields."""
    if isinstance(baseline, BacktestResult):
        bt_return = baseline.total_return
        bh_return = baseline.buy_and_hold_return
    else:
        bt_return = float(baseline.get("total_return", 0.0))
        bh_return = float(baseline.get("buy_and_hold_return", 0.0))
    gap = bt_return - bh_return
    return {
        "baseline_total_return": bt_return,
        "buy_and_hold_return": bh_return,
        "baseline_outperformed_buy_and_hold": bt_return > bh_return,
        "baseline_return_gap_vs_buy_and_hold": gap,
    }


def run_sweep(
    bars: Sequence[Bar],
    short_windows: Sequence[int],
    long_windows: Sequence[int],
    *,
    bars_per_year: float = _BARS_PER_YEAR[_DEFAULT_INTERVAL],
    execution: str = "next_open",
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> dict[str, Any]:
    """Run a backtest for every valid (short, long) pair.

    Combinations where ``short >= long`` are rejected and recorded so the
    operator sees they were considered but skipped. Ranking summaries
    are attached under ``rankings`` so the operator can pick a config
    without re-sorting the raw ``sweep`` list.
    """
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for s in short_windows:
        for l in long_windows:
            if s >= l:
                skipped.append({
                    "short_window": s, "long_window": l,
                    "reason": "short_window >= long_window",
                })
                continue
            r = run_backtest(
                bars, s, l,
                bars_per_year=bars_per_year,
                execution=execution,
                commission_bps=commission_bps,
                slippage_bps=slippage_bps,
            )
            results.append(r.to_dict())
    return {
        "sweep": results,
        "skipped_combinations": skipped,
        "combination_count": len(results),
        "rankings": rank_sweep_results(results),
    }


# ---------------------------------------------------------------------------
# Summary + output
# ---------------------------------------------------------------------------


def build_summary(
    *,
    bars: Sequence[Bar],
    symbol: str,
    interval: str,
    now_utc: datetime,
    short_window: int,
    long_window: int,
    short_windows: Sequence[int] | None = None,
    long_windows: Sequence[int] | None = None,
    include_trades: bool = False,
    execution: str = "next_open",
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
    split_ratio: float | None = None,
    split_mode: str = "chronological",
) -> dict[str, Any]:
    bpy = _BARS_PER_YEAR.get(interval, _BARS_PER_YEAR[_DEFAULT_INTERVAL])
    baseline = run_backtest(
        bars, short_window, long_window,
        bars_per_year=bpy,
        execution=execution,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
    )
    summary: dict[str, Any] = {
        "timestamp_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "symbol": symbol,
        "interval": interval,
        "bar_count": len(bars),
        "first_bar_ts": str(bars[0].ts) if bars else None,
        "last_bar_ts": str(bars[-1].ts) if bars else None,
        "execution": execution,
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
        "baseline": baseline.to_dict(include_trades=include_trades),
        "baseline_comparison": compare_to_baseline(baseline),
    }
    if short_windows and long_windows:
        summary["sweep"] = run_sweep(
            bars, short_windows, long_windows,
            bars_per_year=bpy,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
        )

    if split_ratio is not None:
        if split_mode not in _SPLIT_MODES:
            raise BacktestError(
                f"split-mode must be one of {_SPLIT_MODES}, got {split_mode!r}"
            )
        min_bars = _max_long_window(long_window, long_windows) + 1
        train_bars, test_bars = split_bars_chronological(
            bars, split_ratio, min_partition_bars=min_bars,
        )
        train_summary = _run_partition(
            train_bars,
            symbol=symbol, interval=interval,
            short_window=short_window, long_window=long_window,
            short_windows=short_windows, long_windows=long_windows,
            execution=execution,
            commission_bps=commission_bps, slippage_bps=slippage_bps,
            include_trades=include_trades,
        )
        test_summary = _run_partition(
            test_bars,
            symbol=symbol, interval=interval,
            short_window=short_window, long_window=long_window,
            short_windows=short_windows, long_windows=long_windows,
            execution=execution,
            commission_bps=commission_bps, slippage_bps=slippage_bps,
            include_trades=include_trades,
        )
        summary["split"] = {
            "mode": split_mode,
            "ratio": split_ratio,
            "total_bar_count": len(bars),
            "train_bar_count": len(train_bars),
            "test_bar_count": len(test_bars),
            "train_start": str(train_bars[0].ts) if train_bars else None,
            "train_end":   str(train_bars[-1].ts) if train_bars else None,
            "test_start":  str(test_bars[0].ts) if test_bars else None,
            "test_end":    str(test_bars[-1].ts) if test_bars else None,
        }
        summary["train_summary"] = train_summary
        summary["test_summary"] = test_summary
        summary["generalization_report"] = build_generalization_report(
            train_summary, test_summary,
        )

    return summary


_SPLIT_MODES = ("chronological",)


def _max_long_window(
    long_window: int, long_windows: Sequence[int] | None,
) -> int:
    """Largest long-window across baseline + sweep — the smallest partition
    that could still fit a full SMA computation."""
    candidates = [long_window]
    if long_windows:
        candidates.extend(long_windows)
    return max(candidates)


def split_bars_chronological(
    bars: Sequence[Bar],
    ratio: float,
    *,
    min_partition_bars: int = 2,
) -> tuple[list[Bar], list[Bar]]:
    """Split bars chronologically into (train, test).

    Bars are assumed to already be in ascending timestamp order — the
    caller loaded them via ``load_cached_bars`` which sorts. Splitting
    is by count only; no timestamp math involved, so order is preserved.
    """
    if not (0.0 < ratio < 1.0):
        raise BacktestError(
            f"split-ratio must be strictly between 0 and 1, got {ratio}"
        )
    n = len(bars)
    n_train = int(n * ratio)
    n_test = n - n_train
    if n_train < min_partition_bars or n_test < min_partition_bars:
        raise BacktestError(
            f"split produces a partition too small: train={n_train} "
            f"test={n_test}, need at least {min_partition_bars} bars each"
        )
    return list(bars[:n_train]), list(bars[n_train:])


def _run_partition(
    bars: Sequence[Bar],
    *,
    symbol: str,
    interval: str,
    short_window: int,
    long_window: int,
    short_windows: Sequence[int] | None,
    long_windows: Sequence[int] | None,
    execution: str,
    commission_bps: float,
    slippage_bps: float,
    include_trades: bool,
) -> dict[str, Any]:
    """Return a compact baseline+sweep summary for a single bar subset.

    This is the per-partition payload used inside ``train_summary`` and
    ``test_summary`` when a split is enabled.
    """
    bpy = _BARS_PER_YEAR.get(interval, _BARS_PER_YEAR[_DEFAULT_INTERVAL])
    baseline = run_backtest(
        bars, short_window, long_window,
        bars_per_year=bpy,
        execution=execution,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
    )
    payload: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "bar_count": len(bars),
        "first_bar_ts": str(bars[0].ts) if bars else None,
        "last_bar_ts": str(bars[-1].ts) if bars else None,
        "baseline": baseline.to_dict(include_trades=include_trades),
        "baseline_comparison": compare_to_baseline(baseline),
    }
    if short_windows and long_windows:
        payload["sweep"] = run_sweep(
            bars, short_windows, long_windows,
            bars_per_year=bpy,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
        )
    return payload


def _find_matching_result(
    partition: dict[str, Any], short: int, long: int,
) -> dict[str, Any] | None:
    """Return the sweep or baseline result in ``partition`` with the
    same (short, long) window pair, or ``None`` if not present."""
    if "sweep" in partition:
        for r in partition["sweep"].get("sweep", []):
            if r.get("short_window") == short and r.get("long_window") == long:
                return r
    baseline = partition.get("baseline")
    if (
        isinstance(baseline, dict)
        and baseline.get("short_window") == short
        and baseline.get("long_window") == long
    ):
        return baseline
    return None


def _select_best_train(train_summary: dict[str, Any]) -> dict[str, Any] | None:
    """Winner-by-total-return in train.

    When the sweep is enabled we use ``sweep.rankings.best_by_total_return``;
    otherwise the baseline stands in as the only "config" to evaluate.
    """
    sweep = train_summary.get("sweep")
    if sweep:
        best = sweep.get("rankings", {}).get("best_by_total_return")
        if best:
            return best
    return train_summary.get("baseline")


def build_generalization_report(
    train_summary: dict[str, Any],
    test_summary: dict[str, Any],
    *,
    return_gap_threshold: float = 0.10,
    min_trades_required: int = 3,
) -> dict[str, Any]:
    """Compare the train winner against its own performance in test.

    An ``overfit_warning`` is raised when any of the following holds:

    * train_total_return > 0 and test_total_return <= 0
    * train_sharpe > 0 and test_sharpe <= 0
    * train_total_return - test_total_return > return_gap_threshold
    * the train winner traded fewer than ``min_trades_required`` times
      in either partition
    """
    best_train = _select_best_train(train_summary)
    reasons: list[str] = []
    best_test: dict[str, Any] | None = None
    gap_ret = gap_sh = gap_dd = None
    test_out: bool | None = None

    if best_train is None:
        reasons.append("NO_TRAIN_WINNER")
    else:
        best_test = _find_matching_result(
            test_summary,
            best_train.get("short_window"),
            best_train.get("long_window"),
        )
        if best_test is None:
            reasons.append("NO_MATCHING_TEST_CONFIG")
        else:
            train_ret = float(best_train.get("total_return", 0.0))
            test_ret  = float(best_test.get("total_return", 0.0))
            train_sh  = float(best_train.get("sharpe_ratio", 0.0))
            test_sh   = float(best_test.get("sharpe_ratio", 0.0))
            train_dd  = float(best_train.get("max_drawdown", 0.0))
            test_dd   = float(best_test.get("max_drawdown", 0.0))
            gap_ret = train_ret - test_ret
            gap_sh  = train_sh - test_sh
            gap_dd  = train_dd - test_dd
            test_out = test_ret > float(best_test.get("buy_and_hold_return", 0.0))

            if train_ret > 0 and test_ret <= 0:
                reasons.append("TRAIN_POSITIVE_TEST_NON_POSITIVE_RETURN")
            if train_sh > 0 and test_sh <= 0:
                reasons.append("TRAIN_POSITIVE_TEST_NON_POSITIVE_SHARPE")
            if gap_ret > return_gap_threshold:
                reasons.append("LARGE_TRAIN_TEST_RETURN_GAP")
            train_tc = int(best_train.get("completed_trade_count", 0) or 0)
            test_tc  = int(best_test.get("completed_trade_count", 0) or 0)
            if train_tc < min_trades_required or test_tc < min_trades_required:
                reasons.append("INSUFFICIENT_TRADE_COUNT")

    return {
        "best_train_by_total_return":   best_train,
        "corresponding_test_result":    best_test,
        "train_test_return_gap":        gap_ret,
        "train_test_sharpe_gap":        gap_sh,
        "train_test_drawdown_gap":      gap_dd,
        "test_outperformed_buy_and_hold": test_out,
        "overfit_warning":              bool(reasons),
        "overfit_reasons":              reasons,
    }


def write_summary(summary: dict[str, Any], output_dir: Path, date_utc: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{date_utc}.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.tools.backtest_strategy_eval",
        description=(
            "Read-only backtest of the SMA-crossover paper strategy. "
            "No Alpaca calls, no order submission, no scheduler changes."
        ),
    )
    p.add_argument("--symbol", default=_DEFAULT_SYMBOL)
    p.add_argument("--interval", default=_DEFAULT_INTERVAL, choices=["60m", "1h", "1d"])
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE_DIR))
    p.add_argument("--short-window", type=int, default=_DEFAULT_SHORT)
    p.add_argument("--long-window", type=int, default=_DEFAULT_LONG)
    p.add_argument(
        "--short-windows", default=None,
        help="Comma-separated short windows for a parameter sweep, e.g. 5,10,20",
    )
    p.add_argument(
        "--long-windows", default=None,
        help="Comma-separated long windows for a parameter sweep, e.g. 20,50,100",
    )
    p.add_argument("--execution", default="next_open",
                   choices=list(_EXECUTION_MODES),
                   help="Fill convention. next_open (default) is realistic; "
                        "same_close is diagnostic only.")
    p.add_argument("--commission-bps", type=float, default=0.0,
                   help="Per-side commission in basis points (default 0).")
    p.add_argument("--slippage-bps", type=float, default=0.0,
                   help="Per-side slippage in basis points (default 0).")
    p.add_argument("--include-trades", action="store_true",
                   help="Include per-trade details in the JSON output.")
    p.add_argument("--split-ratio", type=float, default=None,
                   help="Fraction of bars (0<x<1) used for the train "
                        "partition; the rest becomes the test partition. "
                        "Disabled unless explicitly provided.")
    p.add_argument("--split-mode", default="chronological",
                   choices=list(_SPLIT_MODES),
                   help="Split strategy (default: chronological).")
    p.add_argument("--output-dir", default=str(_DEFAULT_OUTPUT_DIR))
    p.add_argument("--no-write", action="store_true",
                   help="Do not write the summary to disk; stdout only.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        bars = load_cached_bars(Path(args.cache_dir), args.symbol, args.interval)
    except BacktestError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    try:
        short_ws = _parse_window_list(args.short_windows) if args.short_windows else None
        long_ws = _parse_window_list(args.long_windows) if args.long_windows else None
    except BacktestError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    now_utc = datetime.now(timezone.utc)
    try:
        summary = build_summary(
            bars=bars,
            symbol=args.symbol,
            interval=args.interval,
            now_utc=now_utc,
            short_window=args.short_window,
            long_window=args.long_window,
            short_windows=short_ws,
            long_windows=long_ws,
            include_trades=args.include_trades,
            execution=args.execution,
            commission_bps=args.commission_bps,
            slippage_bps=args.slippage_bps,
            split_ratio=args.split_ratio,
            split_mode=args.split_mode,
        )
    except BacktestError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    print(json.dumps(summary, indent=2, default=str))

    if not args.no_write:
        write_summary(summary, Path(args.output_dir), now_utc.date().isoformat())

    return 0


if __name__ == "__main__":
    sys.exit(main())
