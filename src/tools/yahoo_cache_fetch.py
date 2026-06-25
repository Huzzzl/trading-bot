"""
tools/yahoo_cache_fetch.py
--------------------------
Guarded Yahoo/yfinance cache fetch tool.

Fetches historical bar data for SPY/QQQ from Yahoo Finance via
YahooDataProvider and writes results to the local data/cache/ directory.

Default: BLOCKED — network fetch is disabled by default.
Requires: --allow-network flag to enable any network call.

What it does (with --allow-network):
- Fetches bars via YahooDataProvider + CachedMarketDataProvider
- Writes cache files to data/cache/ (gitignored)
- Applies conservative rate-limiting (≥1s between fetches)
- Retries on transient failures (max 3 retries, exponential backoff)
- Validates cache after fetch via cached_data_availability_check
- Reports row counts and date ranges; never raw prices

What it never does:
- No network without --allow-network (default BLOCKED)
- No Alpaca SDK or broker calls
- No credential reads (no os.environ, no API keys)
- No order submission or trading actions
- No raw bar data committed to the repository

Usage:
    python -m src.tools.yahoo_cache_fetch                         # BLOCKED
    python -m src.tools.yahoo_cache_fetch --allow-network         # fetches
    python -m src.tools.yahoo_cache_fetch --allow-network --symbols SPY --intervals 1d
    python -m src.tools.yahoo_cache_fetch --allow-network --output report.json

Result:
    PASS    — all requested (symbol, interval) combinations cached and validated
    BLOCKED — network flag absent, or one or more fetches failed
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_DIR = "data/cache"
_DEFAULT_SYMBOLS = ["SPY", "QQQ"]
_DEFAULT_INTERVALS = ["1d", "60m"]


# ---------------------------------------------------------------------------
# Date range helper
# ---------------------------------------------------------------------------


def _default_date_range(interval: str) -> tuple[str, str]:
    """Return (start, end) ISO date strings for the given interval."""
    today = datetime.date.today()
    end = today.isoformat()
    if interval in ("1d",):
        start = "2020-01-01"
    else:  # 60m, 1h, and others treated as intraday
        start = (today - datetime.timedelta(days=700)).isoformat()
    return start, end


# ---------------------------------------------------------------------------
# Fetch with retry
# ---------------------------------------------------------------------------


def _fetch_one_with_retry(
    provider,
    symbol: str,
    start: str,
    end: str,
    interval: str,
    retry_delays: tuple[float, ...] = (2.0, 4.0, 8.0),
) -> tuple[pd.DataFrame | None, str | None]:
    """Fetch with exponential backoff. Returns (df, error_msg)."""
    last_error: str | None = None
    max_attempts = len(retry_delays) + 1
    for attempt in range(max_attempts):
        try:
            df = provider.fetch_bars(symbol, start, end, interval)
            return df, None
        except Exception as exc:
            last_error = str(exc)
            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt])
    return None, last_error


def _detect_cache_path(provider, symbol: str, start: str, end: str, interval: str):
    """Return the cache file Path used by *provider* for this key, or None.

    Probes only the public-ish ``_cache_path`` helper on the project's
    ``CachedMarketDataProvider``. Raw injected providers return None and
    are treated as always-fetched.
    """
    try:
        from src.data.cached_provider import CachedMarketDataProvider
    except ImportError:
        return None
    if isinstance(provider, CachedMarketDataProvider):
        return provider._cache_path(symbol, start, end, interval)
    return None


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def run_fetch(
    cache_dir: str | pathlib.Path = "data/cache",
    symbols: list[str] | None = None,
    intervals: list[str] | None = None,
    *,
    allow_network: bool = False,
    force_refresh: bool = False,
    _provider=None,                          # injectable for tests (BaseDataProvider)
    _inter_fetch_delay: float = 1.0,         # seconds between fetches (0 in tests)
    _retry_delays: tuple[float, ...] = (2.0, 4.0, 8.0),  # backoff (all 0 in tests)
) -> dict[str, Any]:
    """
    Fetch historical bar data for SPY/QQQ from Yahoo Finance and write to cache.

    Parameters
    ----------
    cache_dir:
        Path to write cache files.
    symbols:
        Symbols to fetch (default: SPY, QQQ).
    intervals:
        Bar intervals to fetch (default: 1d, 60m).
    allow_network:
        Must be True to perform any network call. Default False (BLOCKED).
    _provider:
        Injectable provider for testing. If None, creates YahooDataProvider
        wrapped in CachedMarketDataProvider (only when allow_network=True).
    _inter_fetch_delay:
        Seconds to sleep between fetches. Use 0 in tests.
    _retry_delays:
        Tuple of backoff delays in seconds. Use (0, 0, 0) in tests.

    Returns
    -------
    dict
        Result dict with result, entries, safety flags.
    """
    if symbols is None:
        symbols = list(_DEFAULT_SYMBOLS)
    if intervals is None:
        intervals = list(_DEFAULT_INTERVALS)

    cache_dir = pathlib.Path(cache_dir)
    fetched_at = datetime.datetime.now().isoformat()

    # -----------------------------------------------------------------------
    # BLOCKED path — no network flag
    # -----------------------------------------------------------------------
    if not allow_network:
        return {
            "result": "BLOCKED",
            "blocker": "network fetch not enabled: pass --allow-network to proceed",
            "fetched_at": fetched_at,
            "cache_dir": str(cache_dir),
            "symbols_requested": list(symbols),
            "intervals_requested": list(intervals),
            "entries": [],
            "files_written": 0,
            "fetched_count": 0,
            "cache_hit_count": 0,
            "force_refresh": bool(force_refresh),
            "availability_check_result": "SKIPPED",
            "network_calls_made": False,
            "broker_calls_made": False,
            "credentials_read": False,
            "order_action_requested": False,
        }

    # -----------------------------------------------------------------------
    # Allowed path — perform fetches
    # -----------------------------------------------------------------------

    # Lazy import of providers only when allow_network=True and no injectable provider
    if _provider is None:
        from src.data.yahoo_provider import YahooDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        provider = CachedMarketDataProvider(YahooDataProvider(), cache_dir=cache_dir)
    else:
        provider = _provider

    pairs = [(symbol, interval) for symbol in symbols for interval in intervals]
    entries: list[dict[str, Any]] = []
    blocked_entries: list[dict[str, Any]] = []

    for idx, (symbol, interval) in enumerate(pairs):
        start, end = _default_date_range(interval)

        # Probe whether this exact cache key already exists. Only the
        # CachedMarketDataProvider exposes the path; raw injected
        # providers always count as fetched.
        cache_path = _detect_cache_path(provider, symbol, start, end, interval)
        file_existed = cache_path is not None and cache_path.exists()

        # If the operator asked for a forced refresh, atomically displace
        # ONLY this exact file so the wrapped provider sees a cache miss
        # and issues a real Yahoo request. We rename to a sibling .bak
        # so a fetch failure can restore the previous good data.
        backup_path = None
        if force_refresh and file_existed:
            backup_path = cache_path.with_suffix(cache_path.suffix + ".bak")
            if backup_path.exists():
                backup_path.unlink()
            cache_path.rename(backup_path)
            file_existed = False  # cache key is now absent for the fetch

        df, error = _fetch_one_with_retry(
            provider, symbol, start, end, interval,
            retry_delays=_retry_delays,
        )

        if df is not None and len(df) > 0:
            # Successful fetch (or successful cache hit). Discard the
            # backup — the freshly-written file replaces it atomically.
            if backup_path is not None and backup_path.exists():
                backup_path.unlink()
            if force_refresh:
                status = "FETCHED"
            elif cache_path is None:
                # Raw provider — no cache probe possible; treat as fetched.
                status = "FETCHED"
            elif file_existed:
                status = "CACHE_HIT"
            else:
                status = "FETCHED"
            entry: dict[str, Any] = {
                "symbol": symbol,
                "interval": interval,
                "status": status,
                "rows": int(len(df)),
                "inferred_start": df.index[0].date().isoformat(),
                "inferred_end": df.index[-1].date().isoformat(),
            }
            entries.append(entry)
        else:
            # Fetch failed. Restore the backup if we had displaced one
            # so the previous good cache is preserved.
            if backup_path is not None and backup_path.exists():
                if cache_path is not None and cache_path.exists():
                    cache_path.unlink()
                backup_path.rename(cache_path)
            reason = error if error else "empty dataframe returned"
            blocked_entry: dict[str, Any] = {
                "symbol": symbol,
                "interval": interval,
                "status": "BLOCKED",
                "reason": reason,
            }
            entries.append(blocked_entry)
            blocked_entries.append(blocked_entry)

        # Rate-limit between fetches (but not after the last pair)
        if idx < len(pairs) - 1:
            time.sleep(_inter_fetch_delay)

    # -----------------------------------------------------------------------
    # Post-fetch availability check
    # -----------------------------------------------------------------------
    from src.tools.cached_data_availability_check import check_cache
    availability = check_cache(
        cache_dir=cache_dir,
        symbols=symbols,
        intervals=intervals,
    )
    availability_check_result = availability["result"]

    # -----------------------------------------------------------------------
    # Overall result
    # -----------------------------------------------------------------------
    # files_written counts successful entries (FETCHED + CACHE_HIT) — a
    # CACHE_HIT did not write a new file but did produce a usable cache
    # row, consistent with the previous "OK" semantic.
    success_statuses = {"FETCHED", "CACHE_HIT"}
    files_written = sum(1 for e in entries if e.get("status") in success_statuses)
    fetched_count = sum(1 for e in entries if e.get("status") == "FETCHED")
    cache_hit_count = sum(1 for e in entries if e.get("status") == "CACHE_HIT")
    network_calls_made = fetched_count > 0
    overall = (
        "PASS"
        if (not blocked_entries and availability_check_result == "PASS")
        else "BLOCKED"
    )

    result: dict[str, Any] = {
        "result": overall,
        "fetched_at": fetched_at,
        "cache_dir": str(cache_dir),
        "symbols_requested": list(symbols),
        "intervals_requested": list(intervals),
        "entries": entries,
        "files_written": files_written,
        "fetched_count": fetched_count,
        "cache_hit_count": cache_hit_count,
        "force_refresh": bool(force_refresh),
        "availability_check_result": availability_check_result,
        "network_calls_made": network_calls_made,
        "broker_calls_made": False,
        "credentials_read": False,
        "order_action_requested": False,
    }

    if overall == "BLOCKED":
        if blocked_entries:
            reasons = [e.get("reason", "unknown") for e in blocked_entries]
            result["blocker"] = f"fetch failed for {len(blocked_entries)} entries: {'; '.join(reasons)}"
        else:
            result["blocker"] = f"availability check returned {availability_check_result}"
    else:
        result["blocker"] = None

    return result


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------


def _print_summary(result: dict[str, Any]) -> None:
    """Print a human-readable summary to stdout."""
    status = result["result"]
    print(f"Yahoo cache fetch — {status}")
    print(f"  Cache dir     : {result['cache_dir']}")
    print(f"  Symbols       : {', '.join(result['symbols_requested'])}")
    print(f"  Intervals     : {', '.join(result['intervals_requested'])}")
    print(f"  Fetched at    : {result['fetched_at']}")
    print()

    fetched_entries = [e for e in result["entries"] if e.get("status") == "FETCHED"]
    cache_hit_entries = [e for e in result["entries"] if e.get("status") == "CACHE_HIT"]
    blocked = [e for e in result["entries"] if e.get("status") == "BLOCKED"]

    if fetched_entries:
        print("  Fetched from Yahoo:")
        for e in fetched_entries:
            print(
                f"    FETCHED   {e['symbol']}/{e['interval']}  "
                f"rows={e['rows']}  "
                f"{e['inferred_start']} → {e['inferred_end']}"
            )

    if cache_hit_entries:
        print("  Served from local cache (no network call):")
        for e in cache_hit_entries:
            print(
                f"    CACHE_HIT {e['symbol']}/{e['interval']}  "
                f"rows={e['rows']}  "
                f"{e['inferred_start']} → {e['inferred_end']}"
            )

    if blocked:
        print("  Failed:")
        for e in blocked:
            print(f"    BLOCKED   {e['symbol']}/{e['interval']}  {e.get('reason', '')}")

    print()
    print(f"  files_written            : {result['files_written']}")
    print(f"  fetched_count            : {result.get('fetched_count', 0)}")
    print(f"  cache_hit_count          : {result.get('cache_hit_count', 0)}")
    print(f"  force_refresh            : {result.get('force_refresh', False)}")
    print(f"  availability_check_result: {result['availability_check_result']}")
    print(f"  network_calls_made       : {result['network_calls_made']}")
    print(f"  broker_calls_made        : {result['broker_calls_made']}")
    print(f"  credentials_read         : {result['credentials_read']}")
    print(f"  order_action_requested   : {result['order_action_requested']}")
    if result.get("blocker"):
        print(f"  blocker                  : {result['blocker']}")
    print()
    print(f"Result: {status}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Guarded Yahoo/yfinance cache fetch tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        default=False,
        help="Enable network fetch (default: BLOCKED)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=False,
        help=(
            "Atomically replace the cache file for each requested "
            "(symbol, start, end, interval) key before fetching, so an "
            "existing cache hit is bypassed and Yahoo is called. "
            "Unrelated cache files are never touched."
        ),
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(_DEFAULT_SYMBOLS),
        help="Symbols to fetch (default: SPY QQQ)",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=list(_DEFAULT_INTERVALS),
        help="Intervals to fetch (default: 1d 60m)",
    )
    parser.add_argument(
        "--cache-dir",
        default=_DEFAULT_CACHE_DIR,
        help=f"Path to cache directory (default: {_DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="If given, write JSON result to this path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI use."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    result = run_fetch(
        cache_dir=args.cache_dir,
        symbols=args.symbols,
        intervals=args.intervals,
        allow_network=args.allow_network,
        force_refresh=args.force_refresh,
    )

    _print_summary(result)

    if args.output:
        output_path = pathlib.Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"JSON written to: {args.output}")

    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
