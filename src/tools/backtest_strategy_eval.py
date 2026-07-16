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
    entry_filter: str = "none"
    bullish_signal_count: int = 0
    entry_allowed_count: int = 0
    entry_blocked_count: int = 0
    entry_blocked_rate: float = 0.0
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
            "entry_filter": self.entry_filter,
            "bullish_signal_count": self.bullish_signal_count,
            "entry_allowed_count": self.entry_allowed_count,
            "entry_blocked_count": self.entry_blocked_count,
            "entry_blocked_rate": self.entry_blocked_rate,
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


# ---------------------------------------------------------------------------
# S61 — entry filters
# ---------------------------------------------------------------------------
#
# Entry filters gate NEW LONG entries only. They must never delay or
# block an existing bearish SMA exit — the ordinary crossover rule
# stays authoritative for exits. Every filter uses only information
# available through the signal bar; under next_open that signal bar is
# ``i - 1``, so the filter reads at most closes/highs/lows[0..i-1].

_FILTER_VARIANTS = (
    "none",
    "price_above_sma200",
    "long_sma_slope_up_20",
    "ma_separation_25bps",
    "ma_separation_50bps",
    "atr14_pct_below_2",
    "trend200_and_separation25",
)

_DEFAULT_FILTER_BASE_PARAMS: tuple[tuple[int, int], ...] = (
    (10, 20),   # current paper baseline
    (15, 50),   # best S60 fixed aggregate return
    (20, 50),   # best S60 worst-window return
)


def parse_filter_variants(raw: str) -> list[str]:
    """Parse a comma-separated filter list against the allowlist.

    First occurrence wins on duplicates. Unknown or blank entries
    raise :class:`BacktestError`.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise BacktestError("wf-filter-variants must be a non-empty string")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in _FILTER_VARIANTS:
            raise BacktestError(
                f"unknown filter variant: {p!r}; allowed: {list(_FILTER_VARIANTS)}"
            )
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    if not out:
        raise BacktestError("wf-filter-variants yielded no valid entries")
    return out


def _atr14_at(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    index: int,
) -> float | None:
    """Standard 14-period ATR at ``index`` using true-range averaging.

    Each true range needs the previous close, so index 0 cannot be
    used. Requires 14 complete true ranges (bars 1..14 minimum).
    Returns ``None`` when not enough history exists.
    """
    if index < 14:
        return None
    total = 0.0
    for k in range(index - 13, index + 1):
        if k <= 0:
            return None
        tr = max(
            highs[k] - lows[k],
            abs(highs[k] - closes[k - 1]),
            abs(lows[k] - closes[k - 1]),
        )
        total += tr
    return total / 14


def _filter_allow(
    variant: str,
    *,
    signal_index: int,
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    short_window: int,
    long_window: int,
) -> bool:
    """Return ``True`` iff ``variant`` allows a new long entry given
    the SMA-crossover BUY signal at ``signal_index``.

    Filters read only bars through ``signal_index`` — under next_open
    that is ``i - 1`` for an order that fills at bar ``i``'s open, so
    no future information leaks.
    """
    if variant == "none":
        return True

    def _price_above_sma200() -> bool:
        sma200 = _sma(closes, 200, signal_index)
        if sma200 is None:
            return False
        return closes[signal_index] > sma200

    def _long_slope_up_20() -> bool:
        current = _sma(closes, long_window, signal_index)
        earlier = _sma(closes, long_window, signal_index - 20)
        if current is None or earlier is None:
            return False
        return current > earlier

    def _separation(threshold_bps: float) -> bool:
        ss = _sma(closes, short_window, signal_index)
        ls = _sma(closes, long_window, signal_index)
        prev = closes[signal_index]
        if ss is None or ls is None or prev <= 0:
            return False
        return (ss - ls) / prev >= threshold_bps / 10_000.0

    def _atr_below() -> bool:
        atr = _atr14_at(highs, lows, closes, signal_index)
        prev = closes[signal_index]
        if atr is None or prev <= 0:
            return False
        return atr / prev <= 0.02

    if variant == "price_above_sma200":
        return _price_above_sma200()
    if variant == "long_sma_slope_up_20":
        return _long_slope_up_20()
    if variant == "ma_separation_25bps":
        return _separation(25)
    if variant == "ma_separation_50bps":
        return _separation(50)
    if variant == "atr14_pct_below_2":
        return _atr_below()
    if variant == "trend200_and_separation25":
        return _price_above_sma200() and _separation(25)
    raise BacktestError(f"unknown filter variant: {variant!r}")


def filter_warmup_requirement(
    long_window: int,
    variant: str,
    *,
    execution: str = "next_open",
) -> int:
    """Bars of history a filter needs before the first executable test bar.

    Combines the base SMA requirement with any filter-specific need
    (SMA200, long_window + 20 for the slope check, 15 for ATR14) and
    adds 1 when the caller uses ``next_open`` execution.
    """
    if variant not in _FILTER_VARIANTS:
        raise BacktestError(f"unknown filter variant: {variant!r}")
    reqs = [long_window]
    if variant in ("price_above_sma200", "trend200_and_separation25"):
        reqs.append(200)
    if variant == "long_sma_slope_up_20":
        reqs.append(long_window + 20)
    if variant == "atr14_pct_below_2":
        reqs.append(15)
    base = max(reqs)
    return base + (1 if execution == "next_open" else 0)


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
    evaluation_start_index: int = 0,
    entry_filter: str = "none",
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

    Warmup
    ------
    ``evaluation_start_index`` lets the caller supply prior bars purely
    as SMA context. During the warmup region ``i < evaluation_start_index``
    signals are computed as usual (so the SMA warms up) but no trades
    fire and no equity is tracked, so a training position cannot leak
    across into the evaluated region. All post-run metrics
    (``bar_count``, ``buy_and_hold_return``, drawdown, exposure, Sharpe,
    trades) reflect only the evaluated slice. Default 0 preserves the
    original behavior.
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
    if evaluation_start_index < 0 or evaluation_start_index >= max(1, len(bars)):
        raise BacktestError(
            f"evaluation_start_index must be in [0, len(bars)), got "
            f"{evaluation_start_index} for {len(bars)} bars"
        )
    if entry_filter not in _FILTER_VARIANTS:
        raise BacktestError(
            f"entry_filter must be one of {_FILTER_VARIANTS}, "
            f"got {entry_filter!r}"
        )

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    n = len(bars)
    cost_factor = (commission_bps + slippage_bps) / 10_000.0

    equity_curve: list[float] = []
    trades: list[Trade] = []
    position_qty: float = 0.0
    entry_price: float | None = None   # cost-adjusted
    entry_index: int | None = None
    cash = initial_equity
    exposed_bars = 0
    bullish_signal_count = 0
    entry_allowed_count = 0

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

        # Warmup: compute signals so SMA history builds up, but do not
        # execute trades or track equity. This guarantees the evaluated
        # region begins flat.
        if i < evaluation_start_index:
            continue

        if signal == "BUY" and not has_position:
            bullish_signal_count += 1
            # Under next_open, the signal is generated at bar i-1 and
            # fills at bar i's open — the filter reads the same
            # signal_index. Under same_close, both the signal and the
            # fill happen at bar i.
            signal_index = i - 1 if execution == "next_open" else i
            allowed = _filter_allow(
                entry_filter,
                signal_index=signal_index,
                closes=closes, highs=highs, lows=lows,
                short_window=short_window, long_window=long_window,
            ) if signal_index >= 0 else False
            if allowed:
                entry_allowed_count += 1
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

    # Buy-and-hold reflects only the evaluated slice — the warmup region
    # is not part of the strategy comparison.
    if n > evaluation_start_index:
        first_close = closes[evaluation_start_index]
        last_close = closes[-1]
    else:
        first_close = 0.0
        last_close = 0.0
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
    evaluated_bars = n - evaluation_start_index
    exposure_time = exposed_bars / evaluated_bars if evaluated_bars else 0.0

    entry_blocked_count = bullish_signal_count - entry_allowed_count
    entry_blocked_rate = (
        entry_blocked_count / bullish_signal_count
        if bullish_signal_count > 0 else 0.0
    )

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
        bar_count=evaluated_bars,
        open_position=open_position,
        open_entry_price=open_entry_price,
        open_entry_index=open_entry_index,
        open_unrealized_return=open_unrealized_return,
        entry_filter=entry_filter,
        bullish_signal_count=bullish_signal_count,
        entry_allowed_count=entry_allowed_count,
        entry_blocked_count=entry_blocked_count,
        entry_blocked_rate=entry_blocked_rate,
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
    walk_forward: bool = False,
    wf_train_bars: int = 1600,
    wf_test_bars: int = 400,
    wf_step_bars: int = 400,
    wf_selection_metric: str = "total_return",
    wf_fixed_params: Sequence[tuple[int, int]] | None = None,
    wf_compare_fixed: bool = False,
    wf_filter_base_params: Sequence[tuple[int, int]] | None = None,
    wf_filter_variants: Sequence[str] | None = None,
    wf_compare_filters: bool = False,
) -> dict[str, Any]:
    if walk_forward and split_ratio is not None:
        raise BacktestError(
            "--walk-forward and --split-ratio cannot be used together"
        )
    if (wf_fixed_params or wf_compare_fixed) and not walk_forward:
        raise BacktestError(
            "--wf-fixed-params / --wf-compare-fixed require --walk-forward"
        )
    if (
        wf_filter_base_params or wf_filter_variants or wf_compare_filters
    ) and not walk_forward:
        raise BacktestError(
            "--wf-filter-base-params / --wf-filter-variants / "
            "--wf-compare-filters require --walk-forward"
        )
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

    if walk_forward:
        summary["walk_forward"] = run_walk_forward(
            bars,
            symbol=symbol,
            interval=interval,
            baseline_short=short_window,
            baseline_long=long_window,
            short_windows=short_windows,
            long_windows=long_windows,
            train_bars=wf_train_bars,
            test_bars=wf_test_bars,
            step_bars=wf_step_bars,
            selection_metric=wf_selection_metric,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            include_trades=include_trades,
            fixed_params=wf_fixed_params,
            compare_fixed=wf_compare_fixed,
            filter_base_params=wf_filter_base_params,
            filter_variants=wf_filter_variants,
            compare_filters=(
                wf_compare_filters
                or bool(wf_filter_base_params)
                or bool(wf_filter_variants)
            ),
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


# ---------------------------------------------------------------------------
# Walk-forward validation (S59)
# ---------------------------------------------------------------------------

_WF_SELECTION_METRICS = ("total_return",)


def walk_forward_windows(
    n_bars: int,
    train_bars: int,
    test_bars: int,
    step_bars: int,
) -> list[tuple[int, int, int, int, int]]:
    """Return ``(win_index, train_start, train_end, test_start, test_end)``
    tuples for every complete rolling window.

    Windows advance by ``step_bars``; the final incomplete window (where
    a full ``test_bars`` cannot be sliced) is dropped rather than
    trimmed. Train and test are strictly non-overlapping — the guard
    ``step_bars >= test_bars`` also keeps consecutive test windows from
    overlapping so aggregate returns compound cleanly.
    """
    out: list[tuple[int, int, int, int, int]] = []
    idx = 0
    wi = 0
    while True:
        train_start = idx
        train_end = idx + train_bars
        test_start = train_end
        test_end = test_start + test_bars
        if test_end > n_bars:
            break
        out.append((wi, train_start, train_end, test_start, test_end))
        idx += step_bars
        wi += 1
    return out


def _select_metric_key(metric: str) -> str:
    """Map a selection metric name to the key inside sweep rankings."""
    if metric == "total_return":
        return "best_by_total_return"
    raise BacktestError(
        f"walk-forward selection metric must be one of "
        f"{_WF_SELECTION_METRICS}, got {metric!r}"
    )


def _param_key(short: int, long: int) -> str:
    return f"{short}/{long}"


def parse_fixed_params(raw: str) -> list[tuple[int, int]]:
    """Parse a ``"10/20,15/50"`` string into deduplicated pairs.

    Duplicates are silently dropped (first occurrence wins) so a caller
    can concatenate lists without a preprocessing pass. Malformed
    entries, non-integer values, non-positive values, and pairs where
    ``short >= long`` all raise :class:`BacktestError`. An empty result
    is also rejected — a fixed-comparison request must name at least
    one pair.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise BacktestError("wf-fixed-params must be a non-empty string")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    seen: set[tuple[int, int]] = set()
    result: list[tuple[int, int]] = []
    for p in parts:
        if "/" not in p:
            raise BacktestError(f"malformed fixed-param pair: {p!r}")
        sh_str, lo_str = p.split("/", 1)
        try:
            s = int(sh_str)
            l = int(lo_str)
        except ValueError as exc:
            raise BacktestError(
                f"fixed-param pair must be two integers separated by /: {p!r}"
            ) from exc
        if s <= 0 or l <= 0:
            raise BacktestError(f"fixed-param values must be positive: {p!r}")
        if s >= l:
            raise BacktestError(
                f"fixed-param short must be < long: {p!r}"
            )
        key = (s, l)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    if not result:
        raise BacktestError("wf-fixed-params yielded no valid pairs")
    return result


def _default_fixed_params(
    baseline_short: int,
    baseline_long: int,
    short_windows: Sequence[int] | None,
    long_windows: Sequence[int] | None,
) -> list[tuple[int, int]]:
    """Derive a default fixed-comparison list from baseline + sweep.

    Order preserved: baseline first, then each valid ``(short, long)``
    from the requested sweep. Invalid ``short >= long`` combinations
    and duplicates are dropped deterministically.
    """
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []

    def _add(s: int, l: int) -> None:
        if s <= 0 or l <= 0 or s >= l:
            return
        key = (s, l)
        if key in seen:
            return
        seen.add(key)
        out.append(key)

    _add(baseline_short, baseline_long)
    if short_windows and long_windows:
        for s in short_windows:
            for l in long_windows:
                _add(s, l)
    return out


def _fixed_param_key(short: int, long: int) -> str:
    return f"{short}/{long}"


def _evaluate_fixed_on_windows(
    bars: Sequence[Bar],
    all_windows: Sequence[tuple[int, int, int, int, int]],
    fixed_params: Sequence[tuple[int, int]],
    *,
    bars_per_year: float,
    execution: str,
    commission_bps: float,
    slippage_bps: float,
    include_trades: bool,
) -> dict[str, dict[str, Any]]:
    """Run each fixed pair on every walk-forward window.

    Returns a dict keyed by ``"short/long"`` so the output preserves
    the requested parameter order (Python dicts are insertion-ordered).
    Each entry has ``short_window``, ``long_window``, ``windows`` (a
    per-window list) and ``aggregate`` (compounded metrics).
    """
    parameters: dict[str, dict[str, Any]] = {}
    for s, l in fixed_params:
        window_entries: list[dict[str, Any]] = []
        for wi, ts, te, sts, ste in all_windows:
            test_slice = bars[sts:ste]
            warmup = l
            warmup_start = sts - warmup
            eval_slice = bars[warmup_start:ste]
            test_result = run_backtest(
                eval_slice, s, l,
                bars_per_year=bars_per_year,
                execution=execution,
                commission_bps=commission_bps,
                slippage_bps=slippage_bps,
                evaluation_start_index=warmup,
            )
            result_dict = test_result.to_dict(include_trades=include_trades)

            first_c = test_slice[0].close
            last_c  = test_slice[-1].close
            bh = (last_c - first_c) / first_c if first_c > 0 else 0.0
            exposure = float(result_dict.get("exposure_time", 0.0))
            xm = exposure * bh
            test_ret = float(result_dict.get("total_return", 0.0))
            test_sh  = float(result_dict.get("sharpe_ratio", 0.0))

            window_entries.append({
                "window_index": wi,
                "test_start":  str(test_slice[0].ts),
                "test_end":    str(test_slice[-1].ts),
                "test_bar_count": len(test_slice),
                "result": result_dict,
                "test_buy_and_hold_return":            bh,
                "test_outperformed_buy_and_hold":      test_ret > bh,
                "test_profitable":                     test_ret > 0,
                "test_positive_sharpe":                test_sh > 0,
                "exposure_matched_buy_and_hold_return": xm,
            })

        aggregate = _fixed_parameter_aggregate(window_entries)
        parameters[_fixed_param_key(s, l)] = {
            "short_window": s,
            "long_window":  l,
            "windows":      window_entries,
            "aggregate":    aggregate,
        }
    return parameters


def _fixed_parameter_aggregate(
    windows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate a fixed pair's per-window metrics.

    Exposure-matched benchmark: ``exposure_time * buy_and_hold_return``
    on each window, compounded across windows. This is a simple
    constant-allocation surrogate — it does not replicate the
    strategy's timing and is not a substitute for full buy-and-hold.
    """
    n = len(windows)
    if n == 0:
        return {
            "window_count": 0,
            "profitable_test_window_count": 0,
            "profitable_test_window_rate": 0.0,
            "positive_sharpe_window_count": 0,
            "positive_sharpe_window_rate": 0.0,
            "outperformed_buy_and_hold_window_count": 0,
            "outperformed_buy_and_hold_window_rate": 0.0,
            "outperformed_exposure_matched_window_count": 0,
            "outperformed_exposure_matched_window_rate": 0.0,
            "average_test_return": 0.0,
            "median_test_return": 0.0,
            "average_test_sharpe": 0.0,
            "median_test_sharpe": 0.0,
            "average_exposure_time": 0.0,
            "worst_test_return": None,
            "worst_test_drawdown": None,
            "total_completed_test_trades": 0,
            "aggregate_return": 0.0,
            "aggregate_buy_and_hold_return": 0.0,
            "aggregate_exposure_matched_buy_and_hold_return": 0.0,
            "aggregate_gap_vs_buy_and_hold": 0.0,
            "aggregate_gap_vs_exposure_matched": 0.0,
            "largest_positive_window_contribution": None,
            "best_window": None,
            "worst_window": None,
        }

    def _median(vals: Sequence[float]) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0

    def _compound(rs: Sequence[float]) -> float:
        prod = 1.0
        for r in rs:
            prod *= (1.0 + r)
        return prod - 1.0

    test_returns  = [float(w["result"].get("total_return", 0.0)) for w in windows]
    test_sharpes  = [float(w["result"].get("sharpe_ratio", 0.0)) for w in windows]
    test_dds      = [float(w["result"].get("max_drawdown", 0.0)) for w in windows]
    exposures     = [float(w["result"].get("exposure_time", 0.0)) for w in windows]
    trades        = [int(w["result"].get("completed_trade_count", 0) or 0) for w in windows]
    bh_returns    = [float(w["test_buy_and_hold_return"]) for w in windows]
    xm_returns    = [float(w["exposure_matched_buy_and_hold_return"]) for w in windows]

    profitable   = [w for w in windows if w["test_profitable"]]
    positive_sh  = [w for w in windows if w["test_positive_sharpe"]]
    outperf_bh   = [w for w in windows if w["test_outperformed_buy_and_hold"]]
    outperf_xm   = [
        w for w in windows
        if float(w["result"].get("total_return", 0.0))
           > float(w["exposure_matched_buy_and_hold_return"])
    ]

    agg_ret = _compound(test_returns)
    agg_bh  = _compound(bh_returns)
    agg_xm  = _compound(xm_returns)

    positive_returns = [r for r in test_returns if r > 0]
    if positive_returns:
        lpc = max(positive_returns) / sum(positive_returns)
    else:
        lpc = None

    best_window  = max(windows, key=lambda w: float(w["result"].get("total_return", 0.0)))
    worst_window = min(windows, key=lambda w: float(w["result"].get("total_return", 0.0)))

    return {
        "window_count": n,
        "profitable_test_window_count":            len(profitable),
        "profitable_test_window_rate":             len(profitable) / n,
        "positive_sharpe_window_count":            len(positive_sh),
        "positive_sharpe_window_rate":             len(positive_sh) / n,
        "outperformed_buy_and_hold_window_count":  len(outperf_bh),
        "outperformed_buy_and_hold_window_rate":   len(outperf_bh) / n,
        "outperformed_exposure_matched_window_count": len(outperf_xm),
        "outperformed_exposure_matched_window_rate":  len(outperf_xm) / n,
        "average_test_return":   sum(test_returns) / n,
        "median_test_return":    _median(test_returns),
        "average_test_sharpe":   sum(test_sharpes) / n,
        "median_test_sharpe":    _median(test_sharpes),
        "average_exposure_time": sum(exposures) / n,
        "worst_test_return":     min(test_returns),
        "worst_test_drawdown":   min(test_dds),
        "total_completed_test_trades": sum(trades),
        "aggregate_return":                              agg_ret,
        "aggregate_buy_and_hold_return":                 agg_bh,
        "aggregate_exposure_matched_buy_and_hold_return": agg_xm,
        "aggregate_gap_vs_buy_and_hold":                 agg_ret - agg_bh,
        "aggregate_gap_vs_exposure_matched":             agg_ret - agg_xm,
        "largest_positive_window_contribution":          lpc,
        "best_window":  best_window,
        "worst_window": worst_window,
    }


def _fixed_tiebreak_key(entry: dict[str, Any]) -> tuple:
    """Deterministic tiebreak — higher-is-better on every element.

    Order: aggregate return, profitable-window rate, small |drawdown|,
    trade count, small short window, small long window.
    """
    wd = entry.get("worst_test_drawdown") or 0.0
    return (
        float(entry.get("aggregate_return", 0.0)),
        float(entry.get("profitable_test_window_rate", 0.0)),
        -abs(float(wd)),
        int(entry.get("total_completed_test_trades", 0) or 0),
        -int(entry.get("short_window", 0)),
        -int(entry.get("long_window", 0)),
    )


def _return_over_worst_drawdown(entry: dict[str, Any]) -> float | None:
    """Return ``aggregate_return / abs(worst_test_drawdown)``.

    Missing values return ``None``. A zero drawdown also returns
    ``None`` because the ratio is undefined — but a genuine zero
    aggregate return over a non-zero drawdown returns ``0.0``, NOT
    ``None`` (0.0 is a valid, distinct result).
    """
    wd = entry.get("worst_test_drawdown")
    ar = entry.get("aggregate_return")
    if wd is None or ar is None or float(wd) == 0.0:
        return None
    return float(ar) / abs(float(wd))


def _build_adaptive_vs_fixed(
    adaptive_aggregate: dict[str, Any],
    parameters: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare adaptive walk-forward against every fixed parameter."""
    fixed_entries: list[dict[str, Any]] = []
    for key, p in parameters.items():
        entry = dict(p["aggregate"])
        entry["parameter_key"] = key
        entry["short_window"] = p["short_window"]
        entry["long_window"] = p["long_window"]
        entry["return_over_worst_drawdown"] = _return_over_worst_drawdown(entry)
        fixed_entries.append(entry)

    def _best(key_fn) -> dict[str, Any] | None:
        if not fixed_entries:
            return None
        return max(fixed_entries, key=lambda e: (key_fn(e), _fixed_tiebreak_key(e)))

    best_by_agg = _best(
        lambda e: float(e.get("aggregate_return", 0.0)),
    )
    best_by_rate = _best(
        lambda e: float(e.get("profitable_test_window_rate", 0.0)),
    )
    # Only None means "unavailable" — a genuine 0.0 must survive as a
    # valid ranking value, so we do not use ``x or default``.
    def _or_ninf(x: Any) -> float:
        return float(x) if x is not None else float("-inf")

    best_by_worst_return = _best(
        lambda e: _or_ninf(e.get("worst_test_return")),
    )
    best_by_worst_dd = _best(
        # max_drawdown values are <= 0; less negative wins.
        lambda e: _or_ninf(e.get("worst_test_drawdown")),
    )
    best_by_rod = _best(
        lambda e: _or_ninf(e.get("return_over_worst_drawdown")),
    )

    adaptive_return = float(
        adaptive_aggregate.get("aggregate_walk_forward_return", 0.0)
    )
    adaptive_rate = float(
        adaptive_aggregate.get("profitable_test_window_rate", 0.0)
    )

    beating_return = [
        e["parameter_key"] for e in fixed_entries
        if float(e.get("aggregate_return", 0.0)) > adaptive_return
    ]
    beating_rate = [
        e["parameter_key"] for e in fixed_entries
        if float(e.get("profitable_test_window_rate", 0.0)) > adaptive_rate
    ]

    # Rank = 1 + number of fixed strictly beating adaptive.
    adaptive_rank = len(beating_return) + 1
    # Adaptive "outperformed all" requires strict > on every fixed —
    # a tie means adaptive did NOT strictly beat that entry.
    adaptive_beats_all = bool(fixed_entries) and all(
        adaptive_return > float(e.get("aggregate_return", 0.0))
        for e in fixed_entries
    )

    return {
        "adaptive_aggregate_return":                     adaptive_return,
        "adaptive_profitable_window_rate":               adaptive_rate,
        "adaptive_worst_test_return":                    adaptive_aggregate.get("worst_test_return"),
        "adaptive_worst_test_drawdown":                  adaptive_aggregate.get("worst_test_drawdown"),
        "adaptive_total_completed_test_trades":          adaptive_aggregate.get("total_completed_test_trades", 0),
        "adaptive_largest_positive_window_contribution": adaptive_aggregate.get("largest_positive_window_contribution"),
        "best_fixed_by_aggregate_return":                best_by_agg,
        "best_fixed_by_profitable_window_rate":          best_by_rate,
        "best_fixed_by_worst_test_return":               best_by_worst_return,
        "best_fixed_by_worst_drawdown":                  best_by_worst_dd,
        "best_fixed_by_return_over_drawdown":            best_by_rod,
        "fixed_parameters_beating_adaptive_aggregate_return":       beating_return,
        "fixed_parameters_beating_adaptive_profitable_window_rate": beating_rate,
        "adaptive_rank_by_aggregate_return":             adaptive_rank,
        "adaptive_outperformed_all_fixed_parameters":    adaptive_beats_all,
    }


def _build_robustness_report(
    parameters: dict[str, dict[str, Any]],
    adaptive_aggregate: dict[str, Any],
) -> dict[str, Any]:
    """Summarize which fixed parameters passed the robustness screens.

    ``stable_fixed_candidates`` requires ALL of:
      - profitable_test_window_rate >= 0.60
      - aggregate_return > 0
      - total_completed_test_trades >= 15
      - largest_positive_window_contribution <= 0.60
      - worst_test_drawdown > -0.15

    Buy-and-hold outperformance is reported separately and does NOT
    gate stable-candidate status — the strategy can be stable without
    beating buy-and-hold across every regime.
    """
    keys = list(parameters.keys())
    aggregates = {k: parameters[k]["aggregate"] for k in keys}

    if aggregates:
        max_rate = max(
            float(a.get("profitable_test_window_rate", 0.0))
            for a in aggregates.values()
        )
        most_freq = [
            k for k, a in aggregates.items()
            if float(a.get("profitable_test_window_rate", 0.0)) == max_rate
        ]
    else:
        most_freq = []

    def _filter(pred) -> list[str]:
        return [k for k, a in aggregates.items() if pred(a)]

    prof_60 = _filter(
        lambda a: float(a.get("profitable_test_window_rate", 0.0)) >= 0.60,
    )
    pos_agg = _filter(
        lambda a: float(a.get("aggregate_return", 0.0)) > 0,
    )
    beat_bh = _filter(
        lambda a: float(a.get("aggregate_return", 0.0))
                  > float(a.get("aggregate_buy_and_hold_return", 0.0)),
    )
    beat_xm = _filter(
        lambda a: float(a.get("aggregate_return", 0.0))
                  > float(a.get("aggregate_exposure_matched_buy_and_hold_return", 0.0)),
    )
    trades_15 = _filter(
        lambda a: int(a.get("total_completed_test_trades", 0) or 0) >= 15,
    )
    concentration_60 = _filter(
        lambda a: (a.get("largest_positive_window_contribution") or 0.0) > 0.60,
    )

    stable: list[str] = []
    for k, a in aggregates.items():
        lpc = a.get("largest_positive_window_contribution")
        wd  = a.get("worst_test_drawdown")
        if (
            float(a.get("profitable_test_window_rate", 0.0)) >= 0.60
            and float(a.get("aggregate_return", 0.0)) > 0
            and int(a.get("total_completed_test_trades", 0) or 0) >= 15
            and (lpc is None or float(lpc) <= 0.60)
            and wd is not None and float(wd) > -0.15
        ):
            stable.append(k)

    reasons: list[str] = []
    if not stable:
        reasons.append("NO_STABLE_FIXED_CANDIDATE")
    # Strict underperformance: a fixed pair equal to buy-and-hold has
    # not underperformed it. Require every fixed strictly below BH.
    if aggregates and all(
        float(a.get("aggregate_return", 0.0))
        < float(a.get("aggregate_buy_and_hold_return", 0.0))
        for a in aggregates.values()
    ):
        reasons.append("ALL_FIXED_UNDERPERFORMED_BUY_AND_HOLD")
    if aggregates:
        adaptive_return = float(
            adaptive_aggregate.get("aggregate_walk_forward_return", 0.0)
        )
        # Strict underperformance: a fixed pair tied with adaptive has
        # not underperformed it.
        if all(
            float(a.get("aggregate_return", 0.0)) < adaptive_return
            for a in aggregates.values()
        ):
            reasons.append("ALL_FIXED_UNDERPERFORMED_ADAPTIVE")
    if not trades_15:
        reasons.append("LOW_FIXED_SAMPLE_TRADE_COUNT")
    if concentration_60:
        reasons.append("FIXED_RESULTS_PROFIT_CONCENTRATED")

    return {
        "most_frequently_profitable_fixed_parameters":            most_freq,
        "fixed_parameters_profitable_in_at_least_60_percent_of_windows": prof_60,
        "fixed_parameters_with_positive_aggregate_return":        pos_agg,
        "fixed_parameters_outperforming_buy_and_hold":            beat_bh,
        "fixed_parameters_outperforming_exposure_matched_buy_and_hold": beat_xm,
        "fixed_parameters_with_at_least_15_completed_test_trades": trades_15,
        "fixed_parameters_with_profit_concentration_above_60_percent": concentration_60,
        "stable_fixed_candidates":                                stable,
        "fixed_comparison_warning":                               bool(reasons),
        "fixed_comparison_warning_reasons":                       reasons,
    }


# ---------------------------------------------------------------------------
# S61 — entry-filter comparison + robustness
# ---------------------------------------------------------------------------


def _evaluate_filter_windows(
    bars: Sequence[Bar],
    all_windows: Sequence[tuple[int, int, int, int, int]],
    short_window: int,
    long_window: int,
    variant: str,
    *,
    bars_per_year: float,
    execution: str,
    commission_bps: float,
    slippage_bps: float,
    include_trades: bool,
) -> list[dict[str, Any]]:
    """Run (short/long, variant) across every walk-forward window.

    Warmup for each window equals the filter's history requirement so
    every indicator is fully formed by the first executable bar.
    """
    warmup = filter_warmup_requirement(long_window, variant, execution=execution)
    out: list[dict[str, Any]] = []
    for wi, ts, te, sts, ste in all_windows:
        test_slice = bars[sts:ste]
        warmup_start = sts - warmup
        eval_slice = bars[warmup_start:ste]
        r = run_backtest(
            eval_slice, short_window, long_window,
            bars_per_year=bars_per_year,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            evaluation_start_index=warmup,
            entry_filter=variant,
        )
        rd = r.to_dict(include_trades=include_trades)
        first_c = test_slice[0].close
        last_c  = test_slice[-1].close
        bh = (last_c - first_c) / first_c if first_c > 0 else 0.0
        exposure = float(rd.get("exposure_time", 0.0))
        xm = exposure * bh
        test_ret = float(rd.get("total_return", 0.0))
        test_sh  = float(rd.get("sharpe_ratio", 0.0))
        out.append({
            "window_index": wi,
            "test_start":  str(test_slice[0].ts),
            "test_end":    str(test_slice[-1].ts),
            "test_bar_count": len(test_slice),
            "result": rd,
            "test_buy_and_hold_return":            bh,
            "exposure_matched_buy_and_hold_return": xm,
            "test_outperformed_buy_and_hold":      test_ret > bh,
            "test_outperformed_exposure_matched":  test_ret > xm,
            "test_profitable":                     test_ret > 0,
            "test_positive_sharpe":                test_sh > 0,
            "bullish_signal_count": int(rd.get("bullish_signal_count", 0)),
            "entry_allowed_count":  int(rd.get("entry_allowed_count", 0)),
            "entry_blocked_count":  int(rd.get("entry_blocked_count", 0)),
            "entry_blocked_rate":   float(rd.get("entry_blocked_rate", 0.0)),
        })
    return out


def _filter_aggregate(windows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a (base, filter) combination across walk-forward windows."""
    n = len(windows)
    if n == 0:
        return {
            "window_count": 0,
            "profitable_test_window_count": 0,
            "profitable_test_window_rate": 0.0,
            "positive_sharpe_window_count": 0,
            "positive_sharpe_window_rate": 0.0,
            "outperformed_buy_and_hold_window_count": 0,
            "outperformed_buy_and_hold_window_rate": 0.0,
            "outperformed_exposure_matched_window_count": 0,
            "outperformed_exposure_matched_window_rate": 0.0,
            "average_test_return": 0.0,
            "median_test_return": 0.0,
            "average_test_sharpe": 0.0,
            "median_test_sharpe": 0.0,
            "average_exposure_time": 0.0,
            "worst_test_return": None,
            "worst_test_drawdown": None,
            "total_completed_test_trades": 0,
            "total_bullish_signal_count": 0,
            "total_entry_allowed_count": 0,
            "total_entry_blocked_count": 0,
            "aggregate_entry_blocked_rate": 0.0,
            "aggregate_return": 0.0,
            "aggregate_buy_and_hold_return": 0.0,
            "aggregate_exposure_matched_buy_and_hold_return": 0.0,
            "aggregate_gap_vs_buy_and_hold": 0.0,
            "aggregate_gap_vs_exposure_matched": 0.0,
            "largest_positive_window_contribution": None,
            "best_window": None,
            "worst_window": None,
            "return_over_worst_drawdown": None,
        }

    def _median(vals: Sequence[float]) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0

    def _compound(rs: Sequence[float]) -> float:
        prod = 1.0
        for r in rs:
            prod *= (1.0 + r)
        return prod - 1.0

    test_returns = [float(w["result"].get("total_return", 0.0)) for w in windows]
    test_sharpes = [float(w["result"].get("sharpe_ratio", 0.0)) for w in windows]
    test_dds     = [float(w["result"].get("max_drawdown", 0.0)) for w in windows]
    exposures    = [float(w["result"].get("exposure_time", 0.0)) for w in windows]
    trades       = [int(w["result"].get("completed_trade_count", 0) or 0) for w in windows]
    bh_returns   = [float(w["test_buy_and_hold_return"]) for w in windows]
    xm_returns   = [float(w["exposure_matched_buy_and_hold_return"]) for w in windows]
    bulls        = [int(w["bullish_signal_count"]) for w in windows]
    alloweds     = [int(w["entry_allowed_count"]) for w in windows]
    blockeds     = [int(w["entry_blocked_count"]) for w in windows]

    profitable  = [w for w in windows if w["test_profitable"]]
    pos_sharpe  = [w for w in windows if w["test_positive_sharpe"]]
    outperf_bh  = [w for w in windows if w["test_outperformed_buy_and_hold"]]
    outperf_xm  = [w for w in windows if w["test_outperformed_exposure_matched"]]

    agg_ret = _compound(test_returns)
    agg_bh  = _compound(bh_returns)
    agg_xm  = _compound(xm_returns)

    positive_returns = [r for r in test_returns if r > 0]
    if positive_returns:
        lpc = max(positive_returns) / sum(positive_returns)
    else:
        lpc = None

    best_window  = max(windows, key=lambda w: float(w["result"].get("total_return", 0.0)))
    worst_window = min(windows, key=lambda w: float(w["result"].get("total_return", 0.0)))

    total_bulls = sum(bulls)
    total_allowed = sum(alloweds)
    total_blocked = sum(blockeds)
    agg_blocked_rate = (total_blocked / total_bulls) if total_bulls > 0 else 0.0
    worst_dd = min(test_dds)

    if worst_dd == 0.0:
        rod: float | None = None
    else:
        rod = agg_ret / abs(worst_dd)

    return {
        "window_count": n,
        "profitable_test_window_count":            len(profitable),
        "profitable_test_window_rate":             len(profitable) / n,
        "positive_sharpe_window_count":            len(pos_sharpe),
        "positive_sharpe_window_rate":             len(pos_sharpe) / n,
        "outperformed_buy_and_hold_window_count":  len(outperf_bh),
        "outperformed_buy_and_hold_window_rate":   len(outperf_bh) / n,
        "outperformed_exposure_matched_window_count": len(outperf_xm),
        "outperformed_exposure_matched_window_rate":  len(outperf_xm) / n,
        "average_test_return":   sum(test_returns) / n,
        "median_test_return":    _median(test_returns),
        "average_test_sharpe":   sum(test_sharpes) / n,
        "median_test_sharpe":    _median(test_sharpes),
        "average_exposure_time": sum(exposures) / n,
        "worst_test_return":     min(test_returns),
        "worst_test_drawdown":   worst_dd,
        "total_completed_test_trades": sum(trades),
        "total_bullish_signal_count":  total_bulls,
        "total_entry_allowed_count":   total_allowed,
        "total_entry_blocked_count":   total_blocked,
        "aggregate_entry_blocked_rate": agg_blocked_rate,
        "aggregate_return":                              agg_ret,
        "aggregate_buy_and_hold_return":                 agg_bh,
        "aggregate_exposure_matched_buy_and_hold_return": agg_xm,
        "aggregate_gap_vs_buy_and_hold":                 agg_ret - agg_bh,
        "aggregate_gap_vs_exposure_matched":             agg_ret - agg_xm,
        "largest_positive_window_contribution":          lpc,
        "best_window":  best_window,
        "worst_window": worst_window,
        "return_over_worst_drawdown":                    rod,
    }


def _filter_vs_unfiltered(
    base_key: str,
    per_variant: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per-base delta report for every non-``none`` variant."""
    if "none" not in per_variant:
        return []
    unf = per_variant["none"]["aggregate"]
    rows: list[dict[str, Any]] = []

    def _num(x, default=0.0):
        return default if x is None else float(x)

    for variant, entry in per_variant.items():
        if variant == "none":
            continue
        agg = entry["aggregate"]
        u_ret = _num(unf.get("aggregate_return"))
        f_ret = _num(agg.get("aggregate_return"))
        u_rate = _num(unf.get("profitable_test_window_rate"))
        f_rate = _num(agg.get("profitable_test_window_rate"))
        u_wr = unf.get("worst_test_return")
        f_wr = agg.get("worst_test_return")
        u_wd = unf.get("worst_test_drawdown")
        f_wd = agg.get("worst_test_drawdown")

        # "Improvement" = strictly better. Ties are not improvements.
        beat_ret = f_ret > u_ret
        rate_up  = f_rate > u_rate
        # worst_test_return: less negative wins (higher is better).
        wr_up = (u_wr is not None and f_wr is not None and f_wr > u_wr)
        # worst_test_drawdown: less negative wins.
        wd_up = (u_wd is not None and f_wd is not None and f_wd > u_wd)

        rows.append({
            "base_parameter": base_key,
            "filter_variant": variant,
            "unfiltered_aggregate_return": u_ret,
            "filtered_aggregate_return":   f_ret,
            "aggregate_return_delta":      f_ret - u_ret,
            "unfiltered_profitable_window_rate": u_rate,
            "filtered_profitable_window_rate":   f_rate,
            "profitable_window_rate_delta":      f_rate - u_rate,
            "unfiltered_worst_test_return": u_wr,
            "filtered_worst_test_return":   f_wr,
            "worst_test_return_improvement": (
                (f_wr - u_wr) if (u_wr is not None and f_wr is not None) else None
            ),
            "unfiltered_worst_test_drawdown": u_wd,
            "filtered_worst_test_drawdown":   f_wd,
            "worst_drawdown_improvement": (
                (f_wd - u_wd) if (u_wd is not None and f_wd is not None) else None
            ),
            "unfiltered_total_completed_test_trades": int(unf.get("total_completed_test_trades", 0)),
            "filtered_total_completed_test_trades":   int(agg.get("total_completed_test_trades", 0)),
            "unfiltered_largest_positive_window_contribution":
                unf.get("largest_positive_window_contribution"),
            "filtered_largest_positive_window_contribution":
                agg.get("largest_positive_window_contribution"),
            "unfiltered_average_exposure_time": _num(unf.get("average_exposure_time")),
            "filtered_average_exposure_time":   _num(agg.get("average_exposure_time")),
            "filtered_beat_unfiltered_aggregate_return": beat_ret,
            "filtered_improved_profitable_window_rate":  rate_up,
            "filtered_improved_worst_test_return":       wr_up,
            "filtered_improved_worst_drawdown":          wd_up,
        })
    return rows


def _filter_flat_entries(
    results: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Flatten results into a single list keyed by (base, variant) for
    cross-filter ranking. Adds the sort keys the tiebreak needs."""
    out: list[dict[str, Any]] = []
    for base_key, per_variant in results.items():
        s, l = (int(x) for x in base_key.split("/"))
        for variant, entry in per_variant.items():
            e = dict(entry["aggregate"])
            e["base_parameter"] = base_key
            e["filter_variant"] = variant
            e["short_window"] = s
            e["long_window"] = l
            out.append(e)
    return out


def _filter_tiebreak(entry: dict[str, Any]) -> tuple:
    wd = entry.get("worst_test_drawdown")
    return (
        float(entry.get("aggregate_return", 0.0)),
        float(entry.get("profitable_test_window_rate", 0.0)),
        -abs(float(wd)) if wd is not None else float("-inf"),
        int(entry.get("total_completed_test_trades", 0) or 0),
        -int(entry.get("short_window", 0)),
        -int(entry.get("long_window", 0)),
        # Lexicographically smaller filter name wins → invert order via
        # tuple comparison by using negative sort proxy.
        tuple(-ord(c) for c in str(entry.get("filter_variant", ""))),
    )


def _or_ninf(x: Any) -> float:
    return float(x) if x is not None else float("-inf")


def _rank_filter_combinations(
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Cross-(base, filter) rankings on every metric."""
    if not entries:
        return {
            "best_by_aggregate_return":         None,
            "best_by_profitable_window_rate":   None,
            "best_by_worst_test_return":        None,
            "best_by_worst_drawdown":           None,
            "best_by_return_over_drawdown":     None,
            "best_by_gap_vs_exposure_matched":  None,
            "lowest_profit_concentration":      None,
            "highest_trade_count":              None,
        }
    pool = list(entries)

    def _best(key_fn) -> dict[str, Any]:
        return max(pool, key=lambda e: (key_fn(e), _filter_tiebreak(e)))

    best_by_agg = _best(lambda e: float(e.get("aggregate_return", 0.0)))
    best_by_rate = _best(
        lambda e: float(e.get("profitable_test_window_rate", 0.0)),
    )
    best_by_worst_ret = _best(lambda e: _or_ninf(e.get("worst_test_return")))
    best_by_worst_dd = _best(lambda e: _or_ninf(e.get("worst_test_drawdown")))
    best_by_rod = _best(lambda e: _or_ninf(e.get("return_over_worst_drawdown")))
    best_by_gap_xm = _best(
        lambda e: float(e.get("aggregate_gap_vs_exposure_matched", 0.0)),
    )
    # Lowest concentration: lower is better; treat None (no positive
    # windows) as "worst" so it doesn't spuriously win.
    def _neg_lpc(e):
        lpc = e.get("largest_positive_window_contribution")
        return -float(lpc) if lpc is not None else float("-inf")
    lowest_lpc = _best(_neg_lpc)
    highest_trades = _best(
        lambda e: int(e.get("total_completed_test_trades", 0) or 0),
    )

    return {
        "best_by_aggregate_return":         best_by_agg,
        "best_by_profitable_window_rate":   best_by_rate,
        "best_by_worst_test_return":        best_by_worst_ret,
        "best_by_worst_drawdown":           best_by_worst_dd,
        "best_by_return_over_drawdown":     best_by_rod,
        "best_by_gap_vs_exposure_matched":  best_by_gap_xm,
        "lowest_profit_concentration":      lowest_lpc,
        "highest_trade_count":              highest_trades,
    }


def _filter_robustness_report(
    results: dict[str, dict[str, dict[str, Any]]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Screen combinations, list stable filter candidates, warn if none."""
    positive_agg = [
        f"{e['base_parameter']}|{e['filter_variant']}"
        for e in entries
        if float(e.get("aggregate_return", 0.0)) > 0
    ]
    profitable_75 = [
        f"{e['base_parameter']}|{e['filter_variant']}"
        for e in entries
        if float(e.get("profitable_test_window_rate", 0.0)) >= 0.75
    ]
    beat_bh = [
        f"{e['base_parameter']}|{e['filter_variant']}"
        for e in entries
        if float(e.get("aggregate_return", 0.0))
           > float(e.get("aggregate_buy_and_hold_return", 0.0))
    ]
    beat_xm = [
        f"{e['base_parameter']}|{e['filter_variant']}"
        for e in entries
        if float(e.get("aggregate_return", 0.0))
           > float(e.get("aggregate_exposure_matched_buy_and_hold_return", 0.0))
    ]
    trades_15 = [
        f"{e['base_parameter']}|{e['filter_variant']}"
        for e in entries
        if int(e.get("total_completed_test_trades", 0) or 0) >= 15
    ]
    concentration_le_60 = [
        f"{e['base_parameter']}|{e['filter_variant']}"
        for e in entries
        if (e.get("largest_positive_window_contribution") is not None
            and float(e["largest_positive_window_contribution"]) <= 0.60)
    ]

    # Per-base unfiltered baselines for strict-improvement checks.
    baselines: dict[str, dict[str, Any]] = {}
    for base_key, per_variant in results.items():
        if "none" in per_variant:
            baselines[base_key] = per_variant["none"]["aggregate"]

    improving_ret: list[str] = []
    improving_ww: list[str] = []
    stable: list[str] = []
    for e in entries:
        base_key = e["base_parameter"]
        variant  = e["filter_variant"]
        if variant == "none":
            continue
        b = baselines.get(base_key)
        if b is None:
            continue
        b_ret = float(b.get("aggregate_return", 0.0))
        b_wr  = b.get("worst_test_return")
        combo_key = f"{base_key}|{variant}"

        if float(e.get("aggregate_return", 0.0)) > b_ret:
            improving_ret.append(combo_key)

        e_wr = e.get("worst_test_return")
        if (b_wr is not None and e_wr is not None and e_wr > b_wr):
            improving_ww.append(combo_key)

        lpc = e.get("largest_positive_window_contribution")
        wd  = e.get("worst_test_drawdown")
        e_ret = float(e.get("aggregate_return", 0.0))
        xm_ret = float(e.get("aggregate_exposure_matched_buy_and_hold_return", 0.0))

        if (
            float(e.get("profitable_test_window_rate", 0.0)) >= 0.75
            and e_ret > 0
            and int(e.get("total_completed_test_trades", 0) or 0) >= 15
            and (lpc is None or float(lpc) <= 0.60)
            and (wd is not None and float(wd) > -0.15)
            and e_ret > b_ret
            and (b_wr is not None and e_wr is not None and e_wr > b_wr)
            and e_ret > xm_ret
        ):
            stable.append(combo_key)

    reasons: list[str] = []
    if not stable:
        reasons.append("NO_STABLE_FILTER_CANDIDATE")
    if entries and not improving_ret:
        reasons.append("NO_FILTER_BEAT_UNFILTERED_RETURN")
    if entries and not improving_ww:
        reasons.append("NO_FILTER_IMPROVED_WORST_WINDOW")
    if entries and all(
        float(e.get("aggregate_return", 0.0))
        < float(e.get("aggregate_exposure_matched_buy_and_hold_return", 0.0))
        for e in entries
    ):
        reasons.append("ALL_FILTERS_UNDERPERFORMED_EXPOSURE_MATCHED")
    if entries and not trades_15:
        reasons.append("LOW_FILTER_SAMPLE_TRADE_COUNT")
    if any(
        (e.get("largest_positive_window_contribution") is not None
         and float(e["largest_positive_window_contribution"]) > 0.60)
        for e in entries
    ):
        reasons.append("FILTER_RESULTS_PROFIT_CONCENTRATED")
    # Blocked-too-many-entries: only about non-none filters. All of
    # them (if any exist) must have blocked > 80%.
    non_none = [e for e in entries if e["filter_variant"] != "none"]
    if non_none and all(
        float(e.get("aggregate_entry_blocked_rate", 0.0)) > 0.80
        for e in non_none
    ):
        reasons.append("FILTER_BLOCKED_TOO_MANY_ENTRIES")

    return {
        "combinations_with_positive_aggregate_return":            positive_agg,
        "combinations_profitable_in_at_least_75_percent_of_windows": profitable_75,
        "combinations_outperforming_buy_and_hold":                beat_bh,
        "combinations_outperforming_exposure_matched":            beat_xm,
        "combinations_with_at_least_15_completed_test_trades":    trades_15,
        "combinations_with_profit_concentration_at_or_below_60_percent": concentration_le_60,
        "combinations_improving_unfiltered_return":               improving_ret,
        "combinations_improving_unfiltered_worst_window":         improving_ww,
        "stable_filter_candidates":                               stable,
        "filter_comparison_warning":         bool(reasons),
        "filter_comparison_warning_reasons": reasons,
    }


def _build_filter_comparison(
    bars: Sequence[Bar],
    all_windows: Sequence[tuple[int, int, int, int, int]],
    base_params: Sequence[tuple[int, int]],
    variants: Sequence[str],
    *,
    bars_per_year: float,
    execution: str,
    commission_bps: float,
    slippage_bps: float,
    include_trades: bool,
) -> dict[str, Any]:
    results: dict[str, dict[str, dict[str, Any]]] = {}
    filter_vs_unfiltered: dict[str, list[dict[str, Any]]] = {}
    for s, l in base_params:
        base_key = _fixed_param_key(s, l)
        per_variant: dict[str, dict[str, Any]] = {}
        for variant in variants:
            windows = _evaluate_filter_windows(
                bars, all_windows, s, l, variant,
                bars_per_year=bars_per_year,
                execution=execution,
                commission_bps=commission_bps,
                slippage_bps=slippage_bps,
                include_trades=include_trades,
            )
            per_variant[variant] = {
                "short_window": s,
                "long_window":  l,
                "filter_variant": variant,
                "windows":  windows,
                "aggregate": _filter_aggregate(windows),
            }
        results[base_key] = per_variant
        filter_vs_unfiltered[base_key] = _filter_vs_unfiltered(base_key, per_variant)

    flat_entries = _filter_flat_entries(results)
    rankings = _rank_filter_combinations(flat_entries)
    robustness = _filter_robustness_report(results, flat_entries)

    return {
        "base_parameters":       [_fixed_param_key(s, l) for s, l in base_params],
        "filter_variants":       list(variants),
        "base_parameter_count":  len(base_params),
        "filter_variant_count":  len(variants),
        "window_count":          len(all_windows),
        "comparison_basis":      "same_walk_forward_test_windows",
        "test_windows_identical_to_s60": True,
        "filters_apply_to_entries_only": True,
        "research_only":                     True,
        "automatic_filter_promotion_allowed": False,
        "results":               results,
        "filter_vs_unfiltered":  filter_vs_unfiltered,
        "filter_rankings":       rankings,
        "filter_robustness_report": robustness,
    }


def run_walk_forward(
    bars: Sequence[Bar],
    *,
    symbol: str,
    interval: str,
    baseline_short: int,
    baseline_long: int,
    short_windows: Sequence[int] | None,
    long_windows: Sequence[int] | None,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    selection_metric: str = "total_return",
    execution: str = "next_open",
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
    include_trades: bool = False,
    fixed_params: Sequence[tuple[int, int]] | None = None,
    compare_fixed: bool = False,
    filter_base_params: Sequence[tuple[int, int]] | None = None,
    filter_variants: Sequence[str] | None = None,
    compare_filters: bool = False,
) -> dict[str, Any]:
    """Rolling walk-forward validation.

    For each window: run the full sweep on the training slice, pick the
    winner by ``selection_metric`` (total_return only in v1), then
    evaluate exactly that ``(short, long)`` pair on the immediately
    following test slice — with the last ``long_window`` bars of the
    training slice attached as SMA warmup, marked so no trades fire and
    no equity accrues in that region.
    """
    if train_bars <= 0 or test_bars <= 0 or step_bars <= 0:
        raise BacktestError(
            "wf_train_bars, wf_test_bars, wf_step_bars must all be > 0"
        )
    if step_bars < test_bars:
        raise BacktestError(
            f"wf_step_bars ({step_bars}) must be >= wf_test_bars "
            f"({test_bars}) so test windows do not overlap"
        )
    _select_metric_key(selection_metric)  # validate name
    if len(bars) < train_bars + test_bars:
        raise BacktestError(
            f"insufficient bars for walk-forward: need at least "
            f"{train_bars + test_bars}, got {len(bars)}"
        )

    effective_shorts = list(short_windows) if short_windows else [baseline_short]
    effective_longs  = list(long_windows) if long_windows else [baseline_long]

    # Every selected long window must fit inside the training slice as
    # SMA warmup for the following test window. Under next_open the
    # signal at the first test bar's open reads closes[-2] of the
    # warmup — so we need max_long + 1 prior bars, not just max_long.
    max_long = max(effective_longs)
    if fixed_params:
        max_long = max(max_long, max(l for _, l in fixed_params))
    if train_bars < max_long + 1:
        raise BacktestError(
            f"wf_train_bars ({train_bars}) must be >= max long_window + 1 "
            f"({max_long + 1}) so every test window can receive full "
            f"SMA warmup"
        )

    # Derive default fixed comparison set if requested without an
    # explicit list. Presence of fixed_params always enables comparison.
    if compare_fixed and fixed_params is None:
        fixed_params = _default_fixed_params(
            baseline_short, baseline_long, short_windows, long_windows,
        )
        # Re-run the max-long check in case defaults reintroduced a
        # larger value (defensive; already captured above but harmless).
        if fixed_params:
            max_long = max(max_long, max(l for _, l in fixed_params))
            if train_bars < max_long + 1:
                raise BacktestError(
                    f"wf_train_bars ({train_bars}) must be >= "
                    f"max long_window + 1 ({max_long + 1}) for "
                    f"default fixed-comparison set"
                )

    # Derive default filter base params / variants when the comparison
    # is enabled without an explicit list.
    if compare_filters:
        if filter_base_params is None:
            filter_base_params = list(_DEFAULT_FILTER_BASE_PARAMS)
        if filter_variants is None:
            filter_variants = list(_FILTER_VARIANTS)
        # Validate warmup fit for every (base, variant) pair up front.
        for base_s, base_l in filter_base_params:
            if base_s <= 0 or base_l <= 0 or base_s >= base_l:
                raise BacktestError(
                    f"filter base parameter must be short<long positive: "
                    f"{base_s}/{base_l}"
                )
            for v in filter_variants:
                need = filter_warmup_requirement(base_l, v, execution=execution)
                if train_bars < need:
                    raise BacktestError(
                        f"wf_train_bars ({train_bars}) is smaller than the "
                        f"filter warmup requirement ({need}) for base "
                        f"{base_s}/{base_l} filter={v!r}"
                    )

    bpy = _BARS_PER_YEAR.get(interval, _BARS_PER_YEAR[_DEFAULT_INTERVAL])
    winners: list[dict[str, Any]] = []
    metric_key = _select_metric_key(selection_metric)
    all_windows = walk_forward_windows(
        len(bars), train_bars, test_bars, step_bars,
    )

    for wi, ts, te, sts, ste in all_windows:
        train_slice = bars[ts:te]
        test_slice = bars[sts:ste]

        train_sweep = run_sweep(
            train_slice, effective_shorts, effective_longs,
            bars_per_year=bpy,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
        )
        best_train = train_sweep.get("rankings", {}).get(metric_key)
        if best_train is None:
            # Fail closed: silently dropping a window would leave the
            # adaptive result and the fixed-comparison result running
            # on different sets of windows, so the
            # `test_windows_identical_to_adaptive` flag would be a
            # lie. Force the operator to widen the sweep or the data.
            raise BacktestError(
                f"walk-forward window_index={wi} has no valid training "
                f"winner under selection_metric={selection_metric!r} — "
                f"every requested (short_window, long_window) pair was "
                f"rejected. Widen the sweep or the training data."
            )
        s = int(best_train["short_window"])
        l = int(best_train["long_window"])

        # Test evaluation with warmup: prepend exactly `l` train bars as
        # SMA context. The train_bars >= max_long + 1 guard above ensures
        # every window has enough history — no silent shortening.
        warmup = l
        warmup_start = sts - warmup
        eval_slice = bars[warmup_start:ste]
        test_result = run_backtest(
            eval_slice, s, l,
            bars_per_year=bpy,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            evaluation_start_index=warmup,
        )
        test_dict = test_result.to_dict(include_trades=include_trades)

        # Test-window buy-and-hold (independent of warmup mechanics).
        first_c = test_slice[0].close
        last_c = test_slice[-1].close
        bh = (last_c - first_c) / first_c if first_c > 0 else 0.0

        test_ret = float(test_dict.get("total_return", 0.0))
        test_sh  = float(test_dict.get("sharpe_ratio", 0.0))

        winners.append({
            "window_index": wi,
            "train_start": str(train_slice[0].ts),
            "train_end":   str(train_slice[-1].ts),
            "test_start":  str(test_slice[0].ts),
            "test_end":    str(test_slice[-1].ts),
            "train_bar_count": len(train_slice),
            "test_bar_count":  len(test_slice),
            "selected_short_window": s,
            "selected_long_window":  l,
            "selected_train_result": best_train,
            "selected_test_result":  test_dict,
            "test_buy_and_hold_return":       bh,
            "test_outperformed_buy_and_hold": test_ret > bh,
            "test_profitable":                test_ret > 0,
            "test_positive_sharpe":           test_sh > 0,
        })

    aggregate = _aggregate_walk_forward(winners)
    warnings = _walk_forward_warnings(winners, aggregate)

    result = {
        "mode": "rolling_chronological",
        "train_bars": train_bars,
        "test_bars":  test_bars,
        "step_bars":  step_bars,
        "selection_metric": selection_metric,
        "total_bar_count": len(bars),
        "windows": winners,
        "aggregate": aggregate,
        "walk_forward_warning":         warnings["warning"],
        "walk_forward_warning_reasons": warnings["reasons"],
    }

    if fixed_params:
        parameters = _evaluate_fixed_on_windows(
            bars, all_windows, fixed_params,
            bars_per_year=bpy,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            include_trades=include_trades,
        )
        result["fixed_parameter_comparison"] = {
            "requested_parameters": [_fixed_param_key(s, l) for s, l in fixed_params],
            "parameter_count":      len(fixed_params),
            "window_count":         len(all_windows),
            "comparison_basis":     "same_walk_forward_test_windows",
            "test_windows_identical_to_adaptive": True,
            "parameters":           parameters,
            "adaptive_vs_fixed":    _build_adaptive_vs_fixed(aggregate, parameters),
            "robustness_report":    _build_robustness_report(parameters, aggregate),
            "research_only":                     True,
            "automatic_parameter_promotion_allowed": False,
        }

    if compare_filters:
        result["filter_comparison"] = _build_filter_comparison(
            bars, all_windows, list(filter_base_params), list(filter_variants),
            bars_per_year=bpy,
            execution=execution,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            include_trades=include_trades,
        )

    return result


def _aggregate_walk_forward(windows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compound per-window returns and summarize test-window performance."""
    n = len(windows)
    if n == 0:
        return {
            "window_count": 0,
            "profitable_test_window_count": 0,
            "profitable_test_window_rate": 0.0,
            "positive_sharpe_window_count": 0,
            "positive_sharpe_window_rate": 0.0,
            "outperformed_buy_and_hold_window_count": 0,
            "outperformed_buy_and_hold_window_rate": 0.0,
            "average_test_return": 0.0,
            "median_test_return": 0.0,
            "average_test_sharpe": 0.0,
            "median_test_sharpe": 0.0,
            "worst_test_return": None,
            "worst_test_drawdown": None,
            "total_completed_test_trades": 0,
            "aggregate_walk_forward_return": 0.0,
            "aggregate_buy_and_hold_return": 0.0,
            "aggregate_return_gap_vs_buy_and_hold": 0.0,
            "best_test_window": None,
            "worst_test_window": None,
            "parameter_selection_frequency": {},
            "unique_selected_parameter_count": 0,
            "largest_positive_window_contribution": None,
        }

    test_returns  = [float(w["selected_test_result"].get("total_return", 0.0)) for w in windows]
    test_sharpes  = [float(w["selected_test_result"].get("sharpe_ratio", 0.0)) for w in windows]
    test_dds      = [float(w["selected_test_result"].get("max_drawdown", 0.0)) for w in windows]
    bh_returns    = [float(w["test_buy_and_hold_return"]) for w in windows]
    test_trades   = [int(w["selected_test_result"].get("completed_trade_count", 0) or 0)
                     for w in windows]

    profitable  = [w for w in windows if w["test_profitable"]]
    positive_sh = [w for w in windows if w["test_positive_sharpe"]]
    outperform  = [w for w in windows if w["test_outperformed_buy_and_hold"]]

    def _median(vals: Sequence[float]) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        mid = len(s) // 2
        if len(s) % 2:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2.0

    def _compound(rs: Sequence[float]) -> float:
        prod = 1.0
        for r in rs:
            prod *= (1.0 + r)
        return prod - 1.0

    agg_wf = _compound(test_returns)
    agg_bh = _compound(bh_returns)

    positive_returns = [r for r in test_returns if r > 0]
    if positive_returns:
        largest_contribution = max(positive_returns) / sum(positive_returns)
    else:
        largest_contribution = None

    best_win = max(windows, key=lambda w: float(w["selected_test_result"].get("total_return", 0.0)))
    worst_win = min(windows, key=lambda w: float(w["selected_test_result"].get("total_return", 0.0)))

    freq: dict[str, int] = {}
    for w in windows:
        k = _param_key(w["selected_short_window"], w["selected_long_window"])
        freq[k] = freq.get(k, 0) + 1

    return {
        "window_count": n,
        "profitable_test_window_count": len(profitable),
        "profitable_test_window_rate":  len(profitable) / n,
        "positive_sharpe_window_count": len(positive_sh),
        "positive_sharpe_window_rate":  len(positive_sh) / n,
        "outperformed_buy_and_hold_window_count": len(outperform),
        "outperformed_buy_and_hold_window_rate":  len(outperform) / n,
        "average_test_return": sum(test_returns) / n,
        "median_test_return":  _median(test_returns),
        "average_test_sharpe": sum(test_sharpes) / n,
        "median_test_sharpe":  _median(test_sharpes),
        "worst_test_return":   min(test_returns),
        "worst_test_drawdown": min(test_dds),
        "total_completed_test_trades": sum(test_trades),
        "aggregate_walk_forward_return":    agg_wf,
        "aggregate_buy_and_hold_return":    agg_bh,
        "aggregate_return_gap_vs_buy_and_hold": agg_wf - agg_bh,
        "best_test_window":  best_win,
        "worst_test_window": worst_win,
        "parameter_selection_frequency": freq,
        "unique_selected_parameter_count": len(freq),
        "largest_positive_window_contribution": largest_contribution,
    }


def _walk_forward_warnings(
    windows: Sequence[dict[str, Any]],
    aggregate: dict[str, Any],
    *,
    min_windows: int = 3,
    min_profitable_rate: float = 0.60,
    min_total_test_trades: int = 15,
    max_single_window_share: float = 0.60,
) -> dict[str, Any]:
    reasons: list[str] = []
    if aggregate.get("window_count", 0) < min_windows:
        reasons.append("INSUFFICIENT_WINDOWS")
    if aggregate.get("profitable_test_window_rate", 0.0) < min_profitable_rate:
        reasons.append("LOW_PROFITABLE_WINDOW_RATE")
    if aggregate.get("aggregate_walk_forward_return", 0.0) <= 0:
        reasons.append("NON_POSITIVE_AGGREGATE_RETURN")
    if aggregate.get("total_completed_test_trades", 0) < min_total_test_trades:
        reasons.append("LOW_TOTAL_TEST_TRADE_COUNT")
    lpc = aggregate.get("largest_positive_window_contribution")
    if isinstance(lpc, (int, float)) and lpc > max_single_window_share:
        reasons.append("SINGLE_WINDOW_PROFIT_CONCENTRATION")
    if aggregate.get("aggregate_walk_forward_return", 0.0) < aggregate.get(
        "aggregate_buy_and_hold_return", 0.0,
    ):
        reasons.append("UNDERPERFORMED_BUY_AND_HOLD")
    return {"warning": bool(reasons), "reasons": reasons}


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
    p.add_argument("--walk-forward", action="store_true",
                   help="Run rolling walk-forward validation. Disabled "
                        "unless explicitly provided. Mutually exclusive "
                        "with --split-ratio in v1.")
    p.add_argument("--wf-train-bars", type=int, default=1600,
                   help="Bars per walk-forward training window (default 1600).")
    p.add_argument("--wf-test-bars", type=int, default=400,
                   help="Bars per walk-forward test window (default 400).")
    p.add_argument("--wf-step-bars", type=int, default=400,
                   help="Bars between successive train starts. Must be "
                        ">= --wf-test-bars so test windows never overlap "
                        "(default 400).")
    p.add_argument("--wf-selection-metric", default="total_return",
                   choices=list(_WF_SELECTION_METRICS),
                   help="Sweep metric used to pick the train winner "
                        "(v1: total_return only).")
    p.add_argument("--wf-fixed-params", default=None,
                   help='Comma-separated fixed SMA pairs to evaluate on '
                        'the same walk-forward test windows as adaptive, '
                        'e.g. "10/20,15/20,20/50". Requires --walk-forward. '
                        'Implies --wf-compare-fixed.')
    p.add_argument("--wf-compare-fixed", action="store_true",
                   help="Enable fixed-parameter comparison on the same "
                        "walk-forward test windows. When set without "
                        "--wf-fixed-params, defaults to baseline plus "
                        "every valid sweep pair. Requires --walk-forward.")
    p.add_argument("--wf-compare-filters", action="store_true",
                   help="Enable entry-filter robustness comparison on the "
                        "same walk-forward test windows. Requires "
                        "--walk-forward.")
    p.add_argument("--wf-filter-base-params", default=None,
                   help='Comma-separated base SMA pairs to test filters '
                        'against, e.g. "10/20,15/50,20/50". Requires '
                        '--walk-forward. Implies --wf-compare-filters.')
    p.add_argument("--wf-filter-variants", default=None,
                   help='Comma-separated filter variant list. Allowed: '
                        + ",".join(_FILTER_VARIANTS) + '. Requires '
                        '--walk-forward. Implies --wf-compare-filters.')
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
        fixed = parse_fixed_params(args.wf_fixed_params) if args.wf_fixed_params else None
        filter_base = (
            parse_fixed_params(args.wf_filter_base_params)
            if args.wf_filter_base_params else None
        )
        filter_variants = (
            parse_filter_variants(args.wf_filter_variants)
            if args.wf_filter_variants else None
        )
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
            walk_forward=args.walk_forward,
            wf_train_bars=args.wf_train_bars,
            wf_test_bars=args.wf_test_bars,
            wf_step_bars=args.wf_step_bars,
            wf_selection_metric=args.wf_selection_metric,
            wf_fixed_params=fixed,
            # Fixed pairs supplied → auto-enable comparison; explicit
            # --wf-compare-fixed also enables it.
            wf_compare_fixed=args.wf_compare_fixed or bool(fixed),
            wf_filter_base_params=filter_base,
            wf_filter_variants=filter_variants,
            wf_compare_filters=(
                args.wf_compare_filters
                or bool(filter_base)
                or bool(filter_variants)
            ),
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
