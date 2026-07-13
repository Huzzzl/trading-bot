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
The signal at bar ``t`` uses closes[0..t] only — no lookahead. A BUY
signal at ``t`` fills at close[t]; the same-bar convention keeps the
comparison to the live cycle simple. Tests assert both the crossover
rule and the no-lookahead property.
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
    total_return: float
    buy_and_hold_return: float
    max_drawdown: float
    sharpe_ratio: float
    trade_count: int
    win_rate: float
    avg_trade_return: float
    avg_holding_bars: float
    profit_factor: float
    exposure_time: float
    final_equity: float
    bar_count: int
    trades: list[Trade] = field(default_factory=list)

    def to_dict(self, include_trades: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "short_window": self.short_window,
            "long_window": self.long_window,
            "bar_count": self.bar_count,
            "total_return": self.total_return,
            "buy_and_hold_return": self.buy_and_hold_return,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "avg_trade_return": self.avg_trade_return,
            "avg_holding_bars": self.avg_holding_bars,
            "profit_factor": self.profit_factor,
            "exposure_time": self.exposure_time,
            "final_equity": self.final_equity,
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


def run_backtest(
    bars: Sequence[Bar],
    short_window: int,
    long_window: int,
    *,
    initial_equity: float = 10_000.0,
    bars_per_year: float = _BARS_PER_YEAR[_DEFAULT_INTERVAL],
) -> BacktestResult:
    """Replay the SMA crossover rule bar by bar.

    Same-bar execution: a BUY signal at bar ``t`` fills at ``close[t]``
    and a SELL signal at ``t`` closes at ``close[t]``. The signal at
    ``t`` is a pure function of ``closes[0..t]``, so no lookahead
    exists regardless of execution convention.
    """
    if short_window <= 0 or long_window <= 0:
        raise BacktestError("windows must be positive")
    if short_window >= long_window:
        raise BacktestError(
            f"short_window ({short_window}) must be < long_window ({long_window})"
        )
    closes = [b.close for b in bars]
    n = len(bars)

    equity_curve: list[float] = []
    trades: list[Trade] = []
    position_qty: float = 0.0
    entry_price: float | None = None
    entry_index: int | None = None
    cash = initial_equity
    exposed_bars = 0

    for i, bar in enumerate(bars):
        has_position = position_qty > 0
        signal = sma_crossover_signal(
            closes, i, short_window, long_window, has_position
        )
        if signal == "BUY" and not has_position:
            position_qty = cash / bar.close if bar.close > 0 else 0.0
            cash = 0.0
            entry_price = bar.close
            entry_index = i
        elif signal == "SELL" and has_position:
            cash = position_qty * bar.close
            trades.append(Trade(
                entry_index=entry_index if entry_index is not None else i,
                exit_index=i,
                entry_price=entry_price if entry_price is not None else bar.close,
                exit_price=bar.close,
            ))
            position_qty = 0.0
            entry_price = None
            entry_index = None
        if position_qty > 0:
            equity_curve.append(position_qty * bar.close)
            exposed_bars += 1
        else:
            equity_curve.append(cash)

    if position_qty > 0 and entry_price is not None and entry_index is not None:
        # Mark-to-market unwind at final close for reporting only.
        final_price = bars[-1].close
        trades.append(Trade(
            entry_index=entry_index,
            exit_index=n - 1,
            entry_price=entry_price,
            exit_price=final_price,
        ))

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

    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [t.return_pct for t in trades if t.return_pct <= 0]
    trade_count = len(trades)
    win_rate = (len(wins) / trade_count) if trade_count else 0.0
    avg_trade_return = (
        sum(t.return_pct for t in trades) / trade_count if trade_count else 0.0
    )
    avg_holding_bars = (
        sum(t.bars_held for t in trades) / trade_count if trade_count else 0.0
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
        total_return=total_return,
        buy_and_hold_return=bh_return,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
        trade_count=trade_count,
        win_rate=win_rate,
        avg_trade_return=avg_trade_return,
        avg_holding_bars=avg_holding_bars,
        profit_factor=profit_factor,
        exposure_time=exposure_time,
        final_equity=final_equity,
        bar_count=n,
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


def run_sweep(
    bars: Sequence[Bar],
    short_windows: Sequence[int],
    long_windows: Sequence[int],
    *,
    bars_per_year: float = _BARS_PER_YEAR[_DEFAULT_INTERVAL],
) -> dict[str, Any]:
    """Run a backtest for every valid (short, long) pair.

    Combinations where ``short >= long`` are rejected and recorded so the
    operator sees they were considered but skipped.
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
            r = run_backtest(bars, s, l, bars_per_year=bars_per_year)
            results.append(r.to_dict())
    return {
        "sweep": results,
        "skipped_combinations": skipped,
        "combination_count": len(results),
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
) -> dict[str, Any]:
    bpy = _BARS_PER_YEAR.get(interval, _BARS_PER_YEAR[_DEFAULT_INTERVAL])
    baseline = run_backtest(bars, short_window, long_window, bars_per_year=bpy)
    summary: dict[str, Any] = {
        "timestamp_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "symbol": symbol,
        "interval": interval,
        "bar_count": len(bars),
        "first_bar_ts": str(bars[0].ts) if bars else None,
        "last_bar_ts": str(bars[-1].ts) if bars else None,
        "baseline": baseline.to_dict(include_trades=include_trades),
    }
    if short_windows and long_windows:
        summary["sweep"] = run_sweep(
            bars, short_windows, long_windows, bars_per_year=bpy,
        )
    return summary


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
    p.add_argument("--include-trades", action="store_true",
                   help="Include per-trade details in the JSON output.")
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

    short_ws = _parse_window_list(args.short_windows) if args.short_windows else None
    long_ws = _parse_window_list(args.long_windows) if args.long_windows else None

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
