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
import re
import sys
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


class _CliError(Exception):
    pass


def _find_cache_file(cache_dir: Path, symbol: str, interval: str) -> Path | None:
    if not cache_dir.is_dir():
        return None
    safe_iv = re.sub(r"[^\w\-]", "_", interval)
    parquets = sorted(cache_dir.glob(f"{symbol}_*_{safe_iv}.parquet"))
    csvs = sorted(cache_dir.glob(f"{symbol}_*_{safe_iv}.csv"))
    candidates = parquets + csvs
    if not candidates:
        return None
    return candidates[-1]


def _load_cached_bars(cache_dir: Path, symbol: str, interval: str) -> list[Bar]:
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
    except Exception as exc:
        raise _CliError(f"failed to read cache file {path.name}: {exc}") from exc

    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise _CliError(
            f"cache file {path.name} missing required columns: {missing}"
        )

    bars: list[Bar] = []
    for _, row in df.iterrows():
        try:
            bars.append(
                Bar(
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
        except (TypeError, ValueError):
            continue
    if not bars:
        raise _CliError(f"cache file {path.name} contained no valid bars")
    return bars


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


def main(argv: list[str] | None = None) -> int:
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
        bars = _load_cached_bars(cache_dir, _SYMBOL, args.interval)
    except _CliError as exc:
        err = {
            "result": "BLOCKED",
            "action": "none",
            "signal": None,
            "reason_codes": [],
            "order_plan": None,
            "order": None,
            "blocker": str(exc),
        }
        _print_result(err)
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
