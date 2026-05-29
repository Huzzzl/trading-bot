"""
tools/cached_real_data_backtest_check.py
----------------------------------------
Offline TrendFollowing characterization tool using locally cached bar data.

Reads bar files from data/cache/ and runs run_backtest() for each
symbol × interval combination. No network. No credentials. No trading.

What it does:
- Calls cached_data_availability_check first (fail-fast if cache missing)
- Loads cached OHLCV files directly from disk (parquet or csv)
- Runs BacktestEngine via run_backtest() with trend_following strategy
- Reports metric summaries per scenario (no raw OHLCV values)
- Returns PASS if all scenarios complete; BLOCKED if cache missing or load fails

What it never does:
- No network requests (no yfinance, no requests, no urllib, no httpx)
- No Alpaca SDK or broker calls
- No credential reads (no os.environ, no API keys)
- No order submission or trading actions
- No file writes unless --output is specified
- No raw OHLCV bar values in output

Usage:
    python -m src.tools.cached_real_data_backtest_check
    python -m src.tools.cached_real_data_backtest_check --cache-dir data/cache --symbols SPY QQQ --intervals 1d 60m
    python -m src.tools.cached_real_data_backtest_check --output result.json

Result:
    PASS    — all scenarios ran successfully; characterisation metrics available
    BLOCKED — cache missing/invalid, or one or more scenarios failed
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import pandas as pd

from src.backtest.backtest_runner import BacktestRunConfig, run_backtest
from src.data.base import BaseDataProvider
from src.tools.cached_data_availability_check import check_cache

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_DIR = "data/cache"
_DEFAULT_SYMBOLS = ["SPY", "QQQ"]
_DEFAULT_INTERVALS = ["1d", "60m"]

_TREND_PARAMS: dict[str, Any] = {
    "fast_ema_period": 10,
    "slow_ema_period": 50,
    "atr_period": 14,
    "atr_stop_mult": 2.0,
    "volatility_lookback": 50,
    "breakout_lookback": 5,
}
# Warm-up = max(50, 14+50-1, 5+1) = 63 bars minimum before first signal

# Interval aliasing: 60m and 1h are equivalent
_INTERVAL_ALIASES: dict[str, str] = {
    "60m": "1h",
    "1h": "60m",
}

# Metrics keys pulled from BacktestRunResult.metrics
_METRIC_TOTAL_RETURN = "total_return_pct"
_METRIC_ANNUALIZED_RETURN = "annualized_return_pct"
_METRIC_MAX_DRAWDOWN = "max_drawdown_pct"
_METRIC_SHARPE = "sharpe_ratio"
_METRIC_NUM_TRADES = "num_trades"


# ---------------------------------------------------------------------------
# _LoadedFileProvider — in-memory BaseDataProvider backed by a pre-loaded df
# ---------------------------------------------------------------------------


class _LoadedFileProvider(BaseDataProvider):
    """Wraps a pre-loaded DataFrame as a BaseDataProvider.

    Never makes any network calls. All parameters to fetch_bars are ignored;
    the stored DataFrame is returned directly.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> pd.DataFrame:
        """Return the pre-loaded DataFrame, ignoring all parameters."""
        return self._df.copy()


# ---------------------------------------------------------------------------
# _load_cache_file — load a single cache file from disk
# ---------------------------------------------------------------------------


def _load_cache_file(cache_dir: pathlib.Path, file_name: str) -> pd.DataFrame:
    """Load a cache file from disk, cast OHLCV columns to numeric.

    Parameters
    ----------
    cache_dir:
        Directory containing the cache files.
    file_name:
        Name of the file (parquet or csv) within cache_dir.

    Returns
    -------
    pd.DataFrame
        OHLCV DataFrame with tz-aware DatetimeIndex (America/New_York).
    """
    file_path = cache_dir / file_name
    suffix = file_path.suffix.lower()

    if suffix == ".parquet":
        df = pd.read_parquet(file_path)
    elif suffix == ".csv":
        df = pd.read_csv(file_path, index_col=0)
        # Restore tz-aware DatetimeIndex
        df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    else:
        raise ValueError(f"Unsupported file extension: {suffix!r}")

    # Cast OHLCV columns to numeric
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# run_check — main entry point
# ---------------------------------------------------------------------------


def run_check(
    cache_dir: str | pathlib.Path = _DEFAULT_CACHE_DIR,
    symbols: list[str] | None = None,
    intervals: list[str] | None = None,
    *,
    _trend_params: dict | None = None,  # injectable for tests
) -> dict[str, Any]:
    """Run offline backtest characterization using locally cached bar data.

    Parameters
    ----------
    cache_dir:
        Path to the cache directory (default: ``data/cache``).
    symbols:
        List of symbols to check (default: ``["SPY", "QQQ"]``).
    intervals:
        List of intervals to check (default: ``["1d", "60m"]``).
    _trend_params:
        Optional override for trend-following parameters (for testing).

    Returns
    -------
    dict
        Result dict with ``result``, ``scenarios``, ``scenarios_run``, and
        safety flags.
    """
    # 1. Resolve defaults
    cache_dir = pathlib.Path(cache_dir)
    if symbols is None:
        symbols = list(_DEFAULT_SYMBOLS)
    if intervals is None:
        intervals = list(_DEFAULT_INTERVALS)
    trend_params: dict[str, Any] = dict(_trend_params) if _trend_params is not None else dict(_TREND_PARAMS)

    # 2. Check cache availability first (fail-fast)
    avail = check_cache(cache_dir, symbols, intervals)

    # 3. If cache not available, return BLOCKED immediately
    if avail["result"] == "BLOCKED":
        return {
            "result": "BLOCKED",
            "blocker": "cache not available for all requested scenarios",
            "availability_check_result": "BLOCKED",
            "scenarios_run": 0,
            "scenarios": [],
            "broker_calls_made": False,
            "credentials_read": False,
            "network_calls_made": False,
            "order_action_requested": False,
        }

    # 4. Build file_map from availability check results
    file_map: dict[tuple[str, str], str] = {}
    for entry in avail["files_checked"]:
        if entry.get("status") == "OK" and entry.get("file") is not None:
            key = (entry["symbol"], entry["interval"])
            # Only record first OK file per (symbol, interval)
            if key not in file_map:
                file_map[key] = entry["file"]

    # 5. Run backtest for each (symbol, interval) pair
    scenarios: list[dict[str, Any]] = []
    blocked_scenarios: list[tuple[str, str]] = []

    for symbol in symbols:
        for interval in intervals:
            key = (symbol, interval)

            # Look up file — try direct key first, then alias
            file_name: str | None = file_map.get(key)
            if file_name is None:
                alias = _INTERVAL_ALIASES.get(interval)
                if alias is not None:
                    file_name = file_map.get((symbol, alias))

            if file_name is None:
                blocked_scenarios.append(key)
                scenarios.append({
                    "symbol": symbol,
                    "interval": interval,
                    "rows": 0,
                    "status": "BLOCKED",
                })
                continue

            # Load the cache file
            try:
                df = _load_cache_file(cache_dir, file_name)
            except Exception:
                blocked_scenarios.append(key)
                scenarios.append({
                    "symbol": symbol,
                    "interval": interval,
                    "rows": 0,
                    "status": "BLOCKED",
                })
                continue

            # Validate that df has rows
            if len(df) == 0:
                blocked_scenarios.append(key)
                scenarios.append({
                    "symbol": symbol,
                    "interval": interval,
                    "rows": 0,
                    "status": "BLOCKED",
                })
                continue

            # Create in-memory provider
            provider = _LoadedFileProvider(df)

            # Compute date range from the data itself
            start_date = df.index[0].date().isoformat()
            end_date = df.index[-1].date().isoformat()

            # Build backtest config
            config = BacktestRunConfig(
                strategy_name="trend_following",
                strategy_params=trend_params,
                symbols=[symbol],
                start_date=start_date,
                end_date=end_date,
                bar_interval=interval,
                initial_capital=100_000.0,
                commission_per_share=0.005,
                slippage_per_share=0.010,
                position_size_pct=0.95,
                stop_execution="bar_close",
                force_exit_time="15:55",
            )

            # Run backtest
            try:
                result_bt = run_backtest(config, data_provider=provider)
            except Exception:
                blocked_scenarios.append(key)
                scenarios.append({
                    "symbol": symbol,
                    "interval": interval,
                    "rows": len(df),
                    "status": "BLOCKED",
                })
                continue

            # Extract metrics (no raw OHLCV values)
            metrics = result_bt.metrics
            scenario: dict[str, Any] = {
                "symbol": symbol,
                "interval": interval,
                "rows": len(df),
                "status": "OK",
                "total_return_pct": float(metrics.get(_METRIC_TOTAL_RETURN, 0.0)),
                "annualized_return_pct": float(metrics.get(_METRIC_ANNUALIZED_RETURN, 0.0)),
                "max_drawdown_pct": float(metrics.get(_METRIC_MAX_DRAWDOWN, 0.0)),
                "sharpe_ratio": float(metrics.get(_METRIC_SHARPE, 0.0)),
                "num_trades": int(metrics.get(_METRIC_NUM_TRADES, 0)),
            }
            scenarios.append(scenario)

    # 6. Determine overall result
    overall = "PASS" if not blocked_scenarios else "BLOCKED"
    blocker: str | None = None
    if blocked_scenarios:
        pairs = ", ".join(f"{s}/{i}" for s, i in blocked_scenarios)
        blocker = f"one or more scenarios failed or had missing files: {pairs}"

    scenarios_run = sum(1 for s in scenarios if s.get("status") == "OK")

    return {
        "result": overall,
        "blocker": blocker,
        "availability_check_result": avail["result"],
        "scenarios_run": scenarios_run,
        "scenarios": scenarios,
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
    print(f"Cached real-data backtest check — {status}")
    print(f"  Availability check: {result['availability_check_result']}")
    print(f"  Scenarios run     : {result['scenarios_run']}")
    print()

    for s in result["scenarios"]:
        if s["status"] == "OK":
            print(
                f"  OK  {s['symbol']}/{s['interval']}  "
                f"rows={s['rows']}  "
                f"return={s['total_return_pct']:.2f}%  "
                f"sharpe={s['sharpe_ratio']:.3f}  "
                f"trades={s['num_trades']}"
            )
        else:
            print(f"  BLOCKED  {s['symbol']}/{s['interval']}  rows={s['rows']}")

    print()
    if result.get("blocker"):
        print(f"  Blocker: {result['blocker']}")

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
        description="Offline cached real-data backtest characterization tool.",
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
        help="Symbols to backtest (default: SPY QQQ)",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=list(_DEFAULT_INTERVALS),
        help="Intervals to backtest (default: 1d 60m)",
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

    result = run_check(
        cache_dir=args.cache_dir,
        symbols=args.symbols,
        intervals=args.intervals,
    )

    _print_summary(result)

    if args.output:
        output_path = pathlib.Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove non-serializable items (scenarios list is already safe)
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"JSON written to: {args.output}")

    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
