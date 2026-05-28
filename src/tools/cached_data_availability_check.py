"""
tools/cached_data_availability_check.py
---------------------------------------
Offline cache availability checker for SPY/QQQ backtest data.

Scans the local cache directory to verify that cached bar files exist for the
expected symbol × interval combinations.

What it does:
- Scans cache directory for files matching {symbol}_*_{interval}.(parquet|csv)
- For 60m/1h: treats both as equivalent and accepts either file
- Validates required OHLCV columns are present
- Reports row counts (never raw prices or market data values)
- Returns PASS if all expected symbol × interval combinations are cached

What it never does:
- No network requests (no yfinance, no requests, no urllib, no httpx)
- No Alpaca SDK or broker calls
- No credential reads (no os.environ, no API keys)
- No order submission or trading actions
- No data download or fetch
- No file writes unless --output is specified

Usage:
    python -m src.tools.cached_data_availability_check
    python -m src.tools.cached_data_availability_check --cache-dir data/cache --symbols SPY QQQ --intervals 1d 60m
    python -m src.tools.cached_data_availability_check --output cache_status.json

Result:
    PASS   — all expected (symbol, interval) combinations are cached and valid
    BLOCKED — one or more combinations are missing or have invalid columns
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_DIR = "data/cache"
_DEFAULT_SYMBOLS = ["SPY", "QQQ"]
_DEFAULT_INTERVALS = ["1d", "60m"]
_OHLCV_COLUMNS = {"open", "high", "low", "close", "volume"}

# Equivalent interval aliases (60m ↔ 1h)
_INTERVAL_ALIASES: dict[str, list[str]] = {
    "60m": ["60m", "1h"],
    "1h": ["1h", "60m"],
}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _get_interval_aliases(interval: str) -> list[str]:
    """Return list of equivalent interval strings to search for."""
    return _INTERVAL_ALIASES.get(interval, [interval])


def _find_files_for_pair(
    cache_dir: pathlib.Path,
    symbol: str,
    interval: str,
) -> list[pathlib.Path]:
    """Find all cache files matching the given symbol and interval (including aliases)."""
    found: list[pathlib.Path] = []
    aliases = _get_interval_aliases(interval)
    for alias in aliases:
        for ext in ("parquet", "csv"):
            pattern = str(cache_dir / f"{symbol}_*_{alias}.{ext}")
            matched = glob.glob(pattern)
            found.extend(pathlib.Path(p) for p in matched)
    # Deduplicate while preserving order
    seen: set[pathlib.Path] = set()
    unique: list[pathlib.Path] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _validate_file(file_path: pathlib.Path) -> dict[str, Any]:
    """
    Read a cache file and validate its OHLCV columns.

    Returns a dict with keys: status, rows, columns_valid, error (optional).
    """
    try:
        if file_path.suffix == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, index_col=0)

        cols = set(str(c).lower() for c in df.columns)
        columns_valid = _OHLCV_COLUMNS.issubset(cols)
        if columns_valid:
            return {
                "status": "OK",
                "rows": len(df),
                "columns_valid": True,
            }
        else:
            return {
                "status": "INVALID_COLUMNS",
                "columns_valid": False,
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "INVALID_COLUMNS",
            "columns_valid": False,
            "error": str(exc),
        }


def check_cache(
    cache_dir: str | pathlib.Path = _DEFAULT_CACHE_DIR,
    symbols: list[str] | None = None,
    intervals: list[str] | None = None,
) -> dict[str, Any]:
    """
    Check the local cache directory for expected bar files.

    Parameters
    ----------
    cache_dir:
        Path to the cache directory (default: ``data/cache``).
    symbols:
        List of symbols to check (default: ``["SPY", "QQQ"]``).
    intervals:
        List of intervals to check (default: ``["1d", "60m"]``).

    Returns
    -------
    dict
        Result dict with ``result``, ``files_checked``, ``missing_entries``,
        ``blocked_entries``, and safety flags.
    """
    cache_dir = pathlib.Path(cache_dir)
    symbols = symbols if symbols is not None else list(_DEFAULT_SYMBOLS)
    intervals = intervals if intervals is not None else list(_DEFAULT_INTERVALS)

    files_checked: list[dict[str, Any]] = []
    missing_entries: list[dict[str, str]] = []
    blocked_entries: list[dict[str, str]] = []

    for symbol in symbols:
        for interval in intervals:
            if not cache_dir.exists():
                # Cache directory missing entirely — mark as NOT_FOUND
                files_checked.append({
                    "symbol": symbol,
                    "interval": interval,
                    "file": None,
                    "status": "NOT_FOUND",
                    "columns_valid": False,
                })
                missing_entries.append({"symbol": symbol, "interval": interval})
                blocked_entries.append({
                    "symbol": symbol,
                    "interval": interval,
                    "reason": f"cache directory not found: {cache_dir}",
                })
                continue

            found_files = _find_files_for_pair(cache_dir, symbol, interval)

            if not found_files:
                files_checked.append({
                    "symbol": symbol,
                    "interval": interval,
                    "file": None,
                    "status": "NOT_FOUND",
                    "columns_valid": False,
                })
                missing_entries.append({"symbol": symbol, "interval": interval})
                blocked_entries.append({
                    "symbol": symbol,
                    "interval": interval,
                    "reason": "no matching cache file found",
                })
                continue

            # Validate each found file; accept the pair if at least one is OK
            pair_ok = False
            for file_path in found_files:
                validation = _validate_file(file_path)
                entry: dict[str, Any] = {
                    "symbol": symbol,
                    "interval": interval,
                    "file": file_path.name,
                    "status": validation["status"],
                    "columns_valid": validation.get("columns_valid", False),
                }
                if validation["status"] == "OK":
                    entry["rows"] = validation["rows"]
                    pair_ok = True
                files_checked.append(entry)

            if not pair_ok:
                missing_entries.append({"symbol": symbol, "interval": interval})
                blocked_entries.append({
                    "symbol": symbol,
                    "interval": interval,
                    "reason": "no valid file found (OHLCV columns missing or unreadable)",
                })

    overall = "PASS" if not blocked_entries else "BLOCKED"

    return {
        "result": overall,
        "cache_dir": str(cache_dir),
        "symbols_checked": list(symbols),
        "intervals_checked": list(intervals),
        "files_checked": files_checked,
        "missing_entries": missing_entries,
        "blocked_entries": blocked_entries,
        "broker_calls_made": False,
        "credentials_read": False,
        "network_calls_made": False,
        "order_action_requested": False,
    }


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------


def _print_summary(result: dict[str, Any]) -> None:
    """Print a human-readable summary to stdout."""
    status = result["result"]
    cache_dir = result["cache_dir"]
    print(f"Cache availability check — {status}")
    print(f"  Cache dir : {cache_dir}")
    print(f"  Symbols   : {', '.join(result['symbols_checked'])}")
    print(f"  Intervals : {', '.join(result['intervals_checked'])}")
    print()

    ok_files = [f for f in result["files_checked"] if f["status"] == "OK"]
    bad_files = [f for f in result["files_checked"] if f["status"] != "OK"]

    if ok_files:
        print("  Valid files found:")
        for f in ok_files:
            rows_info = f"  ({f.get('rows', '?')} rows)" if "rows" in f else ""
            print(f"    OK  {f['symbol']}/{f['interval']}  {f['file']}{rows_info}")

    if bad_files:
        print("  Missing or invalid files:")
        for f in bad_files:
            fname = f["file"] if f["file"] else "(none)"
            print(f"    {f['status']}  {f['symbol']}/{f['interval']}  {fname}")

    print()
    if result["missing_entries"]:
        print("  Missing entries:")
        for e in result["missing_entries"]:
            print(f"    - {e['symbol']} / {e['interval']}")
    else:
        print("  All expected entries are present and valid.")

    print()
    print(f"  broker_calls_made    : {result['broker_calls_made']}")
    print(f"  credentials_read     : {result['credentials_read']}")
    print(f"  network_calls_made   : {result['network_calls_made']}")
    print(f"  order_action_requested: {result['order_action_requested']}")
    print()
    print(f"Result: {status}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline cache availability checker for SPY/QQQ backtest data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cache-dir",
        default=_DEFAULT_CACHE_DIR,
        help=f"Path to cache directory (default: {_DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(_DEFAULT_SYMBOLS),
        help="Symbols to check (default: SPY QQQ)",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=list(_DEFAULT_INTERVALS),
        help="Intervals to check (default: 1d 60m)",
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

    result = check_cache(
        cache_dir=args.cache_dir,
        symbols=args.symbols,
        intervals=args.intervals,
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
