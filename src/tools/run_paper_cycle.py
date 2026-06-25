"""Runnable Alpaca paper trading cycle — S52.

Loads cached SPY bars from disk (no network market-data fetch),
constructs the existing SignalEngineConfig, and runs one
run_paper_trading_cycle() against a real AlpacaPaperAdapter built
from environment credentials.

Dry-run by default. --submit-paper is the only flag that enables a
real paper-account submission. Live trading is not reachable from
this command.

Usage:
    python -m src.tools.run_paper_cycle
    python -m src.tools.run_paper_cycle --submit-paper
    python -m src.tools.run_paper_cycle --max-position-fraction 0.05
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.broker.alpaca_paper_adapter import (
    AlpacaPaperAdapter,
    AlpacaPaperAdapterError,
)
from src.runtime.paper_trading_cycle import run_paper_trading_cycle
from src.strategy.signal_engine import Bar, SignalEngineConfig

_SYMBOL = "SPY"
_DEFAULT_CACHE_DIR = Path("data/cache")
_INTERVAL_TO_TIMEFRAME = {"60m": "1h"}
_REQUIRED_COLS = ("open", "high", "low", "close", "volume")

# Session-aware freshness thresholds.
#   - Open market: a 60m bar more than 2 hours old is stale.
#   - Closed market with no clock.next_open: conservative wall-clock fallback.
#   - Closed market with clock.next_open: any bar within 96 hours before
#     next_open is considered to belong to the most recent completed
#     session. 96h covers regular overnight (~17.5h), weekend (~65.5h),
#     and most US-equity holiday gaps with a comfortable buffer.
_OPEN_MARKET_MAX_AGE = timedelta(hours=2)
# 120h covers regular overnight (~17h), weekend (~65h), and US holiday gaps
# extending across a weekend (e.g. Thu close → Tue open ≈ 115h for July 4
# falling on Friday).
_CLOSED_NO_NEXT_OPEN_MAX_AGE = timedelta(hours=120)
_CLOSED_PRE_NEXT_OPEN_WINDOW = timedelta(hours=120)


def _default_now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_clock_dt(value: Any) -> datetime | None:
    """Parse a clock timestamp value into a tz-aware datetime.

    Returns None on any failure or naive value. Accepts ISO-8601 strings
    (with ``Z`` suffix or ``±HH:MM`` offset) or datetime instances.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else None


def _fmt_age(delta: timedelta) -> str:
    """Format a timedelta as ``"Hh Mm"`` (clamped to non-negative)."""
    total = max(0, int(delta.total_seconds()))
    hours = total // 3600
    minutes = (total % 3600) // 60
    return f"{hours}h {minutes}m"


def validate_bar_freshness(
    *,
    latest_ts: datetime,
    now: datetime,
    clock: Any,
    interval: str = "60m",
) -> str | None:
    """Return None when the latest bar is fresh enough; else a blocker string.

    Session-aware: while the market is open requires a 60m bar no more
    than 2h old; while closed, accepts any bar belonging to the most
    recent completed regular trading session (overnight, weekend,
    holiday gaps included) using ``clock["next_open"]`` as the anchor.
    """
    if latest_ts.tzinfo is None or now.tzinfo is None:
        return "freshness check requires timezone-aware timestamps"
    if latest_ts > now:
        return "cached latest bar is in the future relative to now"
    if not isinstance(clock, dict):
        return "clock is not a dict"
    is_open = clock.get("is_open")
    if not isinstance(is_open, bool):
        return "clock is_open must be exactly bool"

    next_open_raw = clock.get("next_open")
    next_close_raw = clock.get("next_close")

    next_open_dt = _parse_clock_dt(next_open_raw)
    if next_open_raw is not None and next_open_dt is None:
        return "clock next_open is malformed"
    next_close_dt = _parse_clock_dt(next_close_raw)
    if next_close_raw is not None and next_close_dt is None:
        return "clock next_close is malformed"

    age = now - latest_ts

    if is_open:
        if age > _OPEN_MARKET_MAX_AGE:
            return (
                f"cached latest bar is stale "
                f"({_fmt_age(age)} old, "
                f"threshold {_fmt_age(_OPEN_MARKET_MAX_AGE)} while market is open)"
            )
        return None

    # Market closed.
    if next_open_dt is None:
        if age > _CLOSED_NO_NEXT_OPEN_MAX_AGE:
            return (
                f"cached latest bar is stale "
                f"({_fmt_age(age)} old, threshold "
                f"{_fmt_age(_CLOSED_NO_NEXT_OPEN_MAX_AGE)} "
                f"while market is closed and next_open is unknown)"
            )
        return None

    earliest_allowed = next_open_dt - _CLOSED_PRE_NEXT_OPEN_WINDOW
    if latest_ts < earliest_allowed:
        return (
            f"cached latest bar is stale "
            f"({_fmt_age(age)} old, "
            f"predates the most recent completed session before next_open)"
        )
    return None


class _CliError(Exception):
    pass


def _validate_cache_file(path: Path) -> tuple[list[Bar], datetime]:
    """Fully validate a single cache file and return ``(bars, latest_ts)``.

    Raises :class:`_CliError` on any validation failure. This is the
    single source of validation truth — candidate scoring and final
    loading both go through this function, so a "valid candidate" by
    the scorer is guaranteed loadable.
    """
    try:
        import pandas as pd
    except ImportError as exc:
        raise _CliError(f"pandas is required to load cached bars: {exc}") from exc

    try:
        if path.suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
    except Exception as exc:
        raise _CliError(f"failed to read cache file {path.name}: {exc}") from exc

    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise _CliError(
            f"cache file {path.name} missing required columns: {missing}"
        )

    if not isinstance(df.index, pd.DatetimeIndex):
        raise _CliError(
            f"cache file {path.name} index is not a DatetimeIndex"
        )
    if df.index.isna().any():
        raise _CliError(
            f"cache file {path.name} has invalid or unparseable timestamps"
        )
    if df.index.tz is None:
        raise _CliError(
            f"cache file {path.name} timestamps are not timezone-aware"
        )
    if not df.index.is_unique:
        raise _CliError(
            f"cache file {path.name} has duplicate timestamps"
        )
    if not df.index.is_monotonic_increasing:
        raise _CliError(
            f"cache file {path.name} timestamps are not sorted ascending"
        )

    bars: list[Bar] = []
    for idx, (_, row) in enumerate(df.iterrows()):
        try:
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
            v = float(row["volume"])
        except (TypeError, ValueError):
            raise _CliError(
                f"cache file {path.name} has a malformed OHLCV row at index {idx}"
            )
        if any(math.isnan(x) or math.isinf(x) for x in (o, h, l, c, v)):
            raise _CliError(
                f"cache file {path.name} has a non-finite OHLCV value at index {idx}"
            )
        bars.append(Bar(open=o, high=h, low=l, close=c, volume=v))

    if not bars:
        raise _CliError(f"cache file {path.name} contained no rows")

    latest_ts_pd = df.index[-1]
    latest_ts = latest_ts_pd.to_pydatetime()
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.replace(tzinfo=timezone.utc)
    return bars, latest_ts


def _load_cached_bars(
    cache_dir: Path, symbol: str, interval: str,
) -> tuple[list[Bar], datetime]:
    """Return bars + latest_ts of the most recent fully valid cache file.

    Scores candidates by ``latest_ts`` from the shared validator so a
    malformed-but-newer file can never beat a fully valid older one.
    If at least one candidate is fully valid, that one wins. If no
    candidate is fully valid, surfaces the most recent candidate's
    validation error so the operator sees a precise blocker.
    """
    if not cache_dir.is_dir():
        raise _CliError(
            f"no cached bars found for {symbol}/{interval} under {cache_dir}"
        )
    safe_iv = re.sub(r"[^\w\-]", "_", interval)
    candidates = (
        list(cache_dir.glob(f"{symbol}_*_{safe_iv}.parquet"))
        + list(cache_dir.glob(f"{symbol}_*_{safe_iv}.csv"))
    )
    if not candidates:
        raise _CliError(
            f"no cached bars found for {symbol}/{interval} under {cache_dir}"
        )

    valid: list[tuple[datetime, list[Bar]]] = []
    last_error: str | None = None
    for path in candidates:
        try:
            bars, latest_ts = _validate_cache_file(path)
        except _CliError as exc:
            last_error = str(exc)
            continue
        valid.append((latest_ts, bars))

    if not valid:
        raise _CliError(
            last_error
            or f"no valid cached bars found for {symbol}/{interval} under {cache_dir}"
        )

    valid.sort(key=lambda item: item[0])
    latest_ts, bars = valid[-1]
    return bars, latest_ts


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_paper_cycle",
        description=(
            "Run one Alpaca paper trading cycle on cached SPY 60m bars. "
            "Dry-run by default."
        ),
    )
    p.add_argument("--interval", default="60m", choices=["60m"],
                   help="Bar interval (only 60m supported for now)")
    p.add_argument("--max-position-fraction", type=float, default=0.10,
                   help="Max fraction of equity per buy (default 0.10, max 0.25)")
    p.add_argument("--submit-paper", action="store_true",
                   help="Enable paper submission. Without this flag the cycle "
                        "is dry-run only and never calls submit_market_order.")
    p.add_argument("--client-order-id", default=None,
                   help="Optional client_order_id for the submitted order.")
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE_DIR),
                   help="Directory containing cached SPY bars "
                        "(default: data/cache).")
    return p


def _exit_code_for(result: str) -> int:
    if result == "PASS":
        return 0
    if result == "BLOCKED":
        return 1
    return 2


def _build_signal_config(interval: str) -> SignalEngineConfig:
    timeframe = _INTERVAL_TO_TIMEFRAME[interval]
    return SignalEngineConfig(
        strategy_name="sma_crossover",
        symbol=_SYMBOL,
        timeframe=timeframe,
        min_bars_required=20,
        short_window=10,
        long_window=20,
    )


def _print_mode(submit_enabled: bool) -> None:
    mode = "PAPER_SUBMIT" if submit_enabled else "DRY_RUN"
    sys.stdout.write(f"mode: {mode}\n")


def _print_result(result: dict[str, Any]) -> None:
    # Whitelist of keys we are willing to print — explicitly excludes any
    # credential-like field that a future change could add upstream.
    allowed = {
        "result", "action", "signal", "reason_codes",
        "order_plan", "order", "blocker",
    }
    safe = {k: result.get(k) for k in allowed if k in result}
    sys.stdout.write(json.dumps(safe, default=str) + "\n")


def _blocked_result(blocker: str) -> dict[str, Any]:
    return {
        "result": "BLOCKED",
        "action": "none",
        "signal": None,
        "reason_codes": [],
        "order_plan": None,
        "order": None,
        "blocker": blocker,
    }


def main(argv: list[str] | None = None, *, now_utc_fn=_default_now_utc) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    _print_mode(args.submit_paper)

    try:
        adapter = AlpacaPaperAdapter.from_environment()
    except AlpacaPaperAdapterError as exc:
        err = {
            "result": "ERROR",
            "action": "error",
            "signal": None,
            "reason_codes": [],
            "order_plan": None,
            "order": None,
            "blocker": f"adapter init failed: {exc}",
        }
        _print_result(err)
        return _exit_code_for("ERROR")

    # Fetch the Alpaca clock once so freshness validation can use real
    # session state (next_open/next_close) instead of a wall-clock fallback.
    try:
        clock = adapter.get_clock()
    except AlpacaPaperAdapterError as exc:
        err = {
            "result": "ERROR",
            "action": "error",
            "signal": None,
            "reason_codes": [],
            "order_plan": None,
            "order": None,
            "blocker": f"clock read failed: {exc}",
        }
        _print_result(err)
        return _exit_code_for("ERROR")

    cache_dir = Path(args.cache_dir)
    try:
        bars, latest_ts = _load_cached_bars(cache_dir, _SYMBOL, args.interval)
    except _CliError as exc:
        _print_result(_blocked_result(str(exc)))
        return _exit_code_for("BLOCKED")

    # Session-aware freshness: applied to both DRY_RUN and PAPER_SUBMIT.
    try:
        now = now_utc_fn()
    except Exception as exc:
        _print_result(_blocked_result(f"now_utc_fn failed: {exc}"))
        return _exit_code_for("BLOCKED")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    freshness_blocker = validate_bar_freshness(
        latest_ts=latest_ts, now=now, clock=clock, interval=args.interval,
    )
    if freshness_blocker is not None:
        _print_result(_blocked_result(freshness_blocker))
        return _exit_code_for("BLOCKED")

    signal_config = _build_signal_config(args.interval)
    result = run_paper_trading_cycle(
        adapter=adapter,
        bars=bars,
        signal_config=signal_config,
        max_position_fraction=args.max_position_fraction,
        client_order_id=args.client_order_id,
        submit_enabled=args.submit_paper,
    )

    _print_result(result)
    return _exit_code_for(result.get("result", "ERROR"))


if __name__ == "__main__":
    raise SystemExit(main())
