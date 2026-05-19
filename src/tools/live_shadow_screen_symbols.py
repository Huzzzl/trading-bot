"""
tools/live_shadow_screen_symbols.py
-------------------------------------
Read-only multi-symbol live shadow screening CLI.

Usage::

    python -m src.tools.live_shadow_screen_symbols \\
        --config  config/settings.paper.local.yaml \\
        --output-dir output/live_shadow_symbol_screen \\
        --symbols SPY,QQQ,IWM,DIA

Goal
----
Run read-only live shadow sizing checks across multiple symbols and
summarise which are suitable under current live sizing limits.

What it does
------------
1. Loads config and resolves live credentials.
2. Creates a live ``TradingClient`` (``paper=False``) and reads account
   state, open positions, and open orders — once for all symbols.
3. For each symbol, runs the local strategy preview and applies live sizing.
4. Prints a per-symbol table and an overall result.
5. Optionally writes ``live_shadow_symbol_screen_report.json`` and
   ``live_shadow_symbol_screen.csv`` when ``--write-report`` is set.

What it never does
------------------
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes a ledger row.
* Never reads paper credentials.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.live_account_check import (
    _bool,
    _get,
    _make_live_client,
    _normalize_enum_value,
    _str,
    check_credentials,
)
from src.tools.live_shadow_preflight import _run_strategy_preview, _size_candidate
from src.tools.paper_status import check_config


# ---------------------------------------------------------------------------
# Account reader (one-shot for all symbols)
# ---------------------------------------------------------------------------

def _normalize_symbols(raw: list) -> list[str]:
    """Uppercase, strip, deduplicate preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        s = str(item).strip().upper()
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return result


def _is_zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def read_account(client: Any) -> dict[str, Any]:
    """Read account, positions, and orders from the live client.

    Returns a flat dict; ``"ok"`` is True only when account is active and
    unblocked.  ``"warn"`` is True when active but buying_power/portfolio_value
    is zero.  Never raises.
    """
    try:
        raw = client.get_account()
    except Exception as exc:
        return {
            "ok": False, "warn": False, "error": str(exc),
            "status": "unknown", "trading_blocked": False, "account_blocked": False,
            "buying_power": "0", "portfolio_value": "0",
            "positions": [], "orders": [],
        }

    status          = _normalize_enum_value(_get(raw, "status", "unknown"))
    trading_blocked = _bool(raw, "trading_blocked")
    account_blocked = _bool(raw, "account_blocked")
    buying_power    = _str(raw, "buying_power", "0")
    portfolio_value = _str(raw, "portfolio_value", "0")

    ok   = status == "active" and not trading_blocked and not account_blocked
    warn = ok and (_is_zero(buying_power) or _is_zero(portfolio_value))

    try:
        positions = client.get_all_positions() or []
    except Exception:
        positions = []

    try:
        orders = client.get_orders() or []
    except Exception:
        orders = []

    return {
        "ok":               ok,
        "warn":             warn,
        "error":            None,
        "status":           status,
        "trading_blocked":  trading_blocked,
        "account_blocked":  account_blocked,
        "buying_power":     buying_power,
        "portfolio_value":  portfolio_value,
        "positions":        positions,
        "orders":           orders,
    }


# ---------------------------------------------------------------------------
# Per-symbol screening
# ---------------------------------------------------------------------------

_SYMBOL_COLUMNS = [
    "symbol", "candidate_count", "best_status",
    "min_estimated_notional", "max_estimated_notional",
    "pass_count", "fail_count",
    "position_for_symbol", "open_orders_for_symbol", "blocker_summary",
]


def screen_symbol(
    symbol: str,
    intents: list,
    account: dict[str, Any],
    cfg: Any,
) -> dict[str, Any]:
    """Return a screening result dict for one symbol."""
    positions = account.get("positions", [])
    orders    = account.get("orders",    [])

    position_for_symbol    = any(_str(p, "symbol") == symbol for p in positions)
    open_orders_for_symbol = any(_str(o, "symbol") == symbol for o in orders)

    def _fail(blocker: str) -> dict[str, Any]:
        return {
            "symbol":               symbol,
            "candidate_count":      len(intents),
            "best_status":          "FAIL",
            "min_estimated_notional": None,
            "max_estimated_notional": None,
            "pass_count":           0,
            "fail_count":           len(intents),
            "position_for_symbol":  position_for_symbol,
            "open_orders_for_symbol": open_orders_for_symbol,
            "blocker_summary":      blocker,
        }

    if not account.get("ok"):
        return _fail(f"account {account.get('status', 'unknown')} or blocked")
    if position_for_symbol:
        return _fail(f"live position exists for {symbol}")
    if open_orders_for_symbol:
        return _fail(f"live open order exists for {symbol}")
    if not intents:
        return _fail("no candidates")

    rows      = [_size_candidate(i, cfg) for i in intents]
    pass_rows = [r for r in rows if r["sizing_status"] == "PASS"]
    fail_rows = [r for r in rows if r["sizing_status"] == "FAIL"]
    notionals = [r["estimated_notional"] for r in rows if r["estimated_notional"] is not None]

    min_notional = min(notionals) if notionals else None
    max_notional = max(notionals) if notionals else None

    if not pass_rows:
        seen: list[str] = []
        for r in fail_rows[:2]:
            reason = r.get("sizing_reason", "?")
            if reason not in seen:
                seen.append(reason)
        blocker = f"all {len(fail_rows)} candidate(s) fail sizing — {'; '.join(seen)}"
        return {
            "symbol":               symbol,
            "candidate_count":      len(intents),
            "best_status":          "FAIL",
            "min_estimated_notional": min_notional,
            "max_estimated_notional": max_notional,
            "pass_count":           0,
            "fail_count":           len(fail_rows),
            "position_for_symbol":  position_for_symbol,
            "open_orders_for_symbol": open_orders_for_symbol,
            "blocker_summary":      blocker,
        }

    return {
        "symbol":               symbol,
        "candidate_count":      len(intents),
        "best_status":          "PASS",
        "min_estimated_notional": min_notional,
        "max_estimated_notional": max_notional,
        "pass_count":           len(pass_rows),
        "fail_count":           len(fail_rows),
        "position_for_symbol":  position_for_symbol,
        "open_orders_for_symbol": open_orders_for_symbol,
        "blocker_summary":      "",
    }


def compute_overall(creds_ok: bool, account: dict[str, Any],
                    symbol_results: list[dict]) -> str:
    """Compute overall PASS/WARN/FAIL from account state and per-symbol results."""
    if not creds_ok:
        return "FAIL"
    account_fail = (
        account.get("error") is not None
        or account.get("status", "unknown") != "active"
        or account.get("trading_blocked")
        or account.get("account_blocked")
    )
    if account_fail:
        return "FAIL"
    if any(r["best_status"] == "PASS" for r in symbol_results):
        return "PASS"
    if account.get("warn"):
        return "WARN"
    return "FAIL"


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _fmt(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def print_screen_report(
    account: dict[str, Any],
    symbol_results: list[dict],
    symbols: list[str],
    overall_result: str,
) -> None:
    """Print the multi-symbol screening table."""
    print("\n=== Live Shadow Symbol Screen ===")
    if account.get("error"):
        print(f"  account_error   : {account['error']}")
    else:
        print(f"  account_status  : {account.get('status', '?')}")
        print(f"  buying_power    : {account.get('buying_power', '?')}")
        print(f"  portfolio_value : {account.get('portfolio_value', '?')}")
    print(f"  symbols_checked : {','.join(symbols)}")
    print()

    # Table header
    print(f"  {'Symbol':<8}  {'Cands':>5}  {'Pass':>4}  {'Fail':>4}"
          f"  {'Min Notional':>12}  {'Max Notional':>12}  {'Status':<6}  Blocker")
    print(f"  {'------':<8}  {'-----':>5}  {'----':>4}  {'----':>4}"
          f"  {'------------':>12}  {'------------':>12}  {'------':<6}  -------")

    for r in symbol_results:
        blocker = r["blocker_summary"]
        if len(blocker) > 40:
            blocker = blocker[:37] + "..."
        print(
            f"  {r['symbol']:<8}  {r['candidate_count']:>5}  "
            f"{r['pass_count']:>4}  {r['fail_count']:>4}  "
            f"{_fmt(r['min_estimated_notional']):>12}  "
            f"{_fmt(r['max_estimated_notional']):>12}  "
            f"{r['best_status']:<6}  {blocker}"
        )

    suitable = [r["symbol"] for r in symbol_results if r["best_status"] == "PASS"]
    print()
    print(f"  RESULT: {overall_result} ({len(suitable)}/{len(symbols)} symbols suitable)")
    if suitable:
        print(f"  suitable: {', '.join(suitable)}")
    print("=" * 35)


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

def write_screen_report(
    output_dir: Path,
    symbols: list[str],
    account: dict[str, Any],
    symbol_results: list[dict],
    overall_result: str,
) -> tuple[Path, Path]:
    """Write JSON report and CSV summary.  Returns (json_path, csv_path)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbols":         symbols,
        "overall_result":  overall_result,
        "account": {
            k: account.get(k)
            for k in ("status", "trading_blocked", "account_blocked",
                      "buying_power", "portfolio_value", "error")
        },
        "symbol_results": symbol_results,
    }
    json_path = output_dir / "live_shadow_symbol_screen_report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    csv_path = output_dir / "live_shadow_symbol_screen.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_SYMBOL_COLUMNS)
        writer.writeheader()
        for row in symbol_results:
            writer.writerow({col: row.get(col, "") for col in _SYMBOL_COLUMNS})

    return json_path, csv_path


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_shadow_screen_symbols",
        description=(
            "Read-only multi-symbol live shadow sizing screen. "
            "Never submits or cancels orders. "
            "Requires ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY."
        ),
    )
    parser.add_argument("--config",     required=True, help="Path to settings YAML")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbols to screen (e.g. SPY,QQQ,IWM). "
             "Overrides execution.live_shadow_screen_symbols from config.",
    )
    parser.add_argument(
        "--write-report", action="store_true", default=False,
        help="Write JSON report and CSV to --output-dir",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. Config ---
    config_result, cfg = check_config(Path(args.config))
    if config_result["status"] == "FAIL":
        print(f"\n[FAIL] config: {config_result['detail']}")
        sys.exit(1)

    # --- 2. Resolve symbol list ---
    if args.symbols is not None:
        symbols = _normalize_symbols(args.symbols.split(","))
    else:
        symbols = _normalize_symbols(cfg.execution.live_shadow_screen_symbols)

    if not symbols:
        print("\n[FAIL] symbol list is empty — pass --symbols or set "
              "execution.live_shadow_screen_symbols in config")
        sys.exit(1)

    # --- 3. Credentials ---
    cred_result, creds = check_credentials()
    creds_ok = cred_result["status"] == "PASS"
    if not creds_ok:
        print(f"\n[FAIL] credentials: {cred_result['detail']}")
        overall = compute_overall(False, {}, [])
        print(f"\n  RESULT: {overall}")
        sys.exit(1)

    # --- 4. Live client + account ---
    try:
        client = _make_live_client(*creds)
    except Exception as exc:
        print(f"\n[FAIL] live client: {exc}")
        sys.exit(1)

    account = read_account(client)

    # --- 5. Per-symbol screening ---
    symbol_results: list[dict] = []
    for symbol in symbols:
        try:
            intents = _run_strategy_preview(cfg, symbol)
        except Exception as exc:
            intents = []
        result = screen_symbol(symbol, intents, account, cfg)
        symbol_results.append(result)

    # --- 6. Overall result ---
    overall = compute_overall(creds_ok, account, symbol_results)

    # --- 7. Print ---
    print_screen_report(account, symbol_results, symbols, overall)

    # --- 8. Optional artifacts ---
    if args.write_report:
        json_path, csv_path = write_screen_report(
            output_dir, symbols, account, symbol_results, overall,
        )
        print(f"  report : {json_path}")
        print(f"  csv    : {csv_path}")

    if overall != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
