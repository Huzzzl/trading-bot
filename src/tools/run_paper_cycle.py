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
from datetime import datetime, timezone
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
# A 60m bar older than this is considered stale for trading-decision purposes.
_STALE_BAR_THRESHOLD_SECONDS = 4 * 3600


def _default_now_utc() -> datetime:
    return datetime.now(timezone.utc)


class _CliError(Exception):
    pass


def _peek_latest_timestamp(path: Path):
    """Best-effort: return the largest tz-aware timestamp in a cache file.

    Returns None if the file is unreadable, has no DatetimeIndex, or has
    no valid tz-aware timestamps. Used only to rank candidate files; the
    strict loader (_load_cached_bars) revalidates the chosen file.
    """
    try:
        import pandas as pd
        if path.suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
        if not isinstance(df.index, pd.DatetimeIndex):
            return None
        if df.index.empty or df.index.isna().all():
            return None
        if df.index.tz is None:
            return None
        return df.index.max()
    except Exception:
        return None


def _find_cache_file(cache_dir: Path, symbol: str, interval: str) -> Path | None:
    """Select the cache file with the greatest latest-bar timestamp.

    Considers both .parquet and .csv together; does not prefer one
    extension or filename order over the other. If no candidate has a
    valid latest timestamp, falls back to the first candidate so the
    strict loader can emit a precise blocker.
    """
    if not cache_dir.is_dir():
        return None
    safe_iv = re.sub(r"[^\w\-]", "_", interval)
    candidates = (
        list(cache_dir.glob(f"{symbol}_*_{safe_iv}.parquet"))
        + list(cache_dir.glob(f"{symbol}_*_{safe_iv}.csv"))
    )
    if not candidates:
        return None
    scored = []
    for path in candidates:
        ts = _peek_latest_timestamp(path)
        if ts is not None:
            scored.append((ts, path))
    if not scored:
        return sorted(candidates)[0]
    scored.sort(key=lambda item: item[0])
    return scored[-1][1]


def _load_cached_bars(
    cache_dir: Path, symbol: str, interval: str,
) -> tuple[list[Bar], datetime]:
    """Load cached bars and the latest bar's timestamp.

    Validates strictly: every row must parse to finite OHLCV; timestamps
    must be timezone-aware, unique, and sorted ascending. Any malformed
    row blocks rather than being silently skipped.
    """
    path = _find_cache_file(cache_dir, symbol, interval)
    if path is None:
        raise _CliError(
            f"no cached bars found for {symbol}/{interval} under {cache_dir}"
        )
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
    # Convert to a stdlib UTC datetime — the index is guaranteed tz-aware above.
    latest_ts = latest_ts_pd.to_pydatetime()
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.replace(tzinfo=timezone.utc)
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

    cache_dir = Path(args.cache_dir)
    try:
        bars, latest_ts = _load_cached_bars(cache_dir, _SYMBOL, args.interval)
    except _CliError as exc:
        _print_result(_blocked_result(str(exc)))
        return _exit_code_for("BLOCKED")

    # Staleness check: latest bar must be recent enough to base a decision on.
    # Applied to both DRY_RUN and PAPER_SUBMIT — stale cache must never
    # produce a trading plan or a submission.
    try:
        now = now_utc_fn()
    except Exception as exc:
        _print_result(_blocked_result(f"now_utc_fn failed: {exc}"))
        return _exit_code_for("BLOCKED")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_seconds = (now - latest_ts).total_seconds()
    if age_seconds < 0:
        _print_result(_blocked_result(
            "cached latest bar is in the future relative to now"
        ))
        return _exit_code_for("BLOCKED")
    if age_seconds > _STALE_BAR_THRESHOLD_SECONDS:
        # Blocker message names hours only — never raw prices, symbols, or
        # credentials.
        _print_result(_blocked_result(
            f"cached latest bar is stale "
            f"({int(age_seconds // 3600)}h old, "
            f"threshold {_STALE_BAR_THRESHOLD_SECONDS // 3600}h)"
        ))
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
