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
#   - Closed market: the latest bar must match the FINAL completed
#     60m bar of the most recent NYSE session (resolved via
#     pandas_market_calendars), so weekends and exchange holidays are
#     handled correctly. There is no wall-clock fallback.
_OPEN_MARKET_MAX_AGE = timedelta(hours=2)
# Yahoo's 60m bars are labeled by the start of the bar period. For a
# regular 13:30–20:00 UTC NYSE session the last bar is labeled
# 19:30 UTC (covering 19:30–20:00 as the final half-bar). For an
# early-close session ending 18:00 UTC the last bar is labeled
# 17:30 UTC (17:30–18:00). The expected label is therefore always
# ``session_close - 30 minutes`` for the 60m interval.
_FINAL_60M_BAR_OFFSET_BEFORE_CLOSE = timedelta(minutes=30)
# Tolerance around the expected final-bar label. A 30-minute window on
# either side accepts:
#   * Yahoo's start-of-bar convention (label = session_close - 30m)
#   * top-of-hour labeling (label = session_close - 60m)
#   * end-of-bar labeling (label = session_close)
# A morning bar (e.g. label = session_open) is well outside this window.
_FINAL_BAR_TOLERANCE = timedelta(minutes=30)
# Calendar lookback window when locating the most recent completed
# NYSE session. 30 days covers any holiday or weekend gap.
_CALENDAR_LOOKBACK_DAYS = 30
# Supported intervals — unsupported intervals must block the cycle
# rather than silently falling through to 60m logic.
_SUPPORTED_INTERVALS = frozenset({"60m"})


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


# Substrings whose presence in a sanitized error message will cause it
# to be redacted entirely. Calendar exceptions don't normally include
# secrets, but the redaction is defense-in-depth so that any future
# upstream change cannot leak credentials, prices, or account data
# through the freshness blocker.
_REDACT_SUBSTRINGS = (
    "api_key", "api-key", "apikey",
    "secret", "token", "password", "passwd",
    "authorization", "bearer ",
    "account_id", "account-id", "account_number",
    "/home/", "/root/", "/users/",
)
_MAX_SAFE_EXC_MESSAGE_LEN = 200


def _sanitize_exc_message(exc: BaseException) -> str:
    """Return a short, credential-safe representation of an exception.

    Truncated to a fixed length; entirely redacted when any forbidden
    substring is present.
    """
    raw = str(exc)
    short = raw[:_MAX_SAFE_EXC_MESSAGE_LEN]
    lower = short.lower()
    for sub in _REDACT_SUBSTRINGS:
        if sub in lower:
            return "<redacted>"
    return short


def _most_recent_completed_nyse_session(
    now: datetime,
) -> tuple[tuple[datetime, datetime] | None, str | None]:
    """Resolve the most recent completed NYSE session.

    Returns ``((open_dt, close_dt), None)`` on success or
    ``(None, error_reason)`` on failure. The error_reason is a short,
    credential-safe string distinguishing:

      * missing pandas_market_calendars dependency
      * NYSE calendar lookup failure
      * schedule query failure
      * no completed session found in the lookback window
      * malformed schedule (missing columns / invalid timestamps)
    """
    try:
        import pandas_market_calendars as mcal
        import pandas as pd
    except ImportError as exc:
        return None, (
            "pandas_market_calendars dependency is not installed "
            f"({exc.__class__.__name__})"
        )

    try:
        nyse = mcal.get_calendar("NYSE")
    except Exception as exc:
        return None, (
            f"NYSE calendar lookup failed: "
            f"{exc.__class__.__name__}: {_sanitize_exc_message(exc)}"
        )

    try:
        start = (now - timedelta(days=_CALENDAR_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        sched = nyse.schedule(start_date=start, end_date=end)
    except Exception as exc:
        return None, (
            f"NYSE schedule query failed: "
            f"{exc.__class__.__name__}: {_sanitize_exc_message(exc)}"
        )

    if sched is None or len(sched) == 0:
        return None, (
            f"no NYSE sessions found in {_CALENDAR_LOOKBACK_DAYS}-day "
            f"lookback window"
        )

    if "market_open" not in sched.columns or "market_close" not in sched.columns:
        return None, (
            "NYSE schedule is missing required market_open/market_close columns"
        )

    try:
        now_ts = pd.Timestamp(now)
        if now_ts.tz is None:
            now_ts = now_ts.tz_localize("UTC")
        else:
            now_ts = now_ts.tz_convert("UTC")
        completed = sched[sched["market_close"] <= now_ts]
        if completed.empty:
            return None, "no completed NYSE session found before now"
        last = completed.iloc[-1]
        open_dt = last["market_open"].to_pydatetime()
        close_dt = last["market_close"].to_pydatetime()
        if open_dt.tzinfo is None:
            open_dt = open_dt.replace(tzinfo=timezone.utc)
        else:
            open_dt = open_dt.astimezone(timezone.utc)
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        else:
            close_dt = close_dt.astimezone(timezone.utc)
    except Exception as exc:
        return None, (
            f"NYSE schedule contained invalid timestamps: "
            f"{exc.__class__.__name__}: {_sanitize_exc_message(exc)}"
        )

    return (open_dt, close_dt), None


def _expected_final_60m_bar_range(
    session_close: datetime,
) -> tuple[datetime, datetime]:
    """Return ``(earliest, latest)`` accepted labels for the final 60m bar
    of a session that closes at ``session_close``.
    """
    expected_label = session_close - _FINAL_60M_BAR_OFFSET_BEFORE_CLOSE
    return (
        expected_label - _FINAL_BAR_TOLERANCE,
        expected_label + _FINAL_BAR_TOLERANCE,
    )


def validate_bar_freshness(
    *,
    latest_ts: datetime,
    now: datetime,
    clock: Any,
    interval: str = "60m",
) -> str | None:
    """Return None when the latest bar is fresh enough; else a blocker string.

    Session-aware: while the market is open requires a 60m bar no more
    than 2h old; while closed, requires the latest bar to match the
    final 60m bar label of the most recent completed NYSE session
    (resolved via pandas_market_calendars). A morning or midday bar
    from the correct session still blocks. Unsupported intervals
    block immediately rather than silently using 60m logic.
    """
    if interval not in _SUPPORTED_INTERVALS:
        return (
            f"freshness validation does not support interval {interval!r}; "
            f"supported intervals: {sorted(_SUPPORTED_INTERVALS)}"
        )
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

    # Market closed. Use the NYSE exchange calendar to resolve the most
    # recent completed regular session, then require the latest cached
    # bar to match that session's FINAL 60m bar label (with a small
    # Yahoo-labeling tolerance). A morning or midday bar from the
    # correct session still blocks. Calendar failures surface a
    # structured diagnostic reason rather than a silent generic block.
    session, calendar_error = _most_recent_completed_nyse_session(now)
    if calendar_error is not None:
        return (
            f"freshness check could not determine the most recent "
            f"completed NYSE session: {calendar_error}"
        )
    if session is None:
        # Defensive: helper contract returns a reason whenever session
        # is None. This branch should be unreachable; keep a clear
        # fail-closed message just in case.
        return (
            "freshness check could not determine the most recent "
            "completed NYSE session"
        )
    _session_open, session_close = session
    earliest, latest_allowed = _expected_final_60m_bar_range(session_close)
    if latest_ts < earliest or latest_ts > latest_allowed:
        return (
            f"cached latest bar ({_fmt_age(age)} old) is not the final "
            f"60m bar of the most recent completed NYSE session "
            f"(session_close {session_close.isoformat()}; expected final "
            f"bar in [{earliest.isoformat()}, {latest_allowed.isoformat()}])"
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
        clock_snapshot=clock,
    )

    _print_result(result)
    return _exit_code_for(result.get("result", "ERROR"))


if __name__ == "__main__":
    raise SystemExit(main())
