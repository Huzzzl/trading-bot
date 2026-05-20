"""
tools/live_dry_run_intents.py
------------------------------
Dry-run live order intent audit tool.

Usage::

    python -m src.tools.live_dry_run_intents \\
        --config config/settings.paper.local.yaml \\
        --output-dir output/live_dry_run_intents \\
        --symbol SPY

Goal
----
Produce hypothetical live order intent artifacts using the same live
readiness and notional sizing logic as live_readiness_gate and
live_shadow_preflight, but without ever submitting or cancelling an order.

Behaviour
---------
1. Runs all live readiness checks (credentials, account health, candidates,
   sizing, open positions, open orders).
2. If the gate decision is not GO: writes a NO-GO summary and exits 1.
3. If GO: writes dry-run intent artifacts (CSV + JSON) and a summary, exits 0.
4. Every artifact includes ``dry_run_only=true`` and ``submit_allowed=false``.

What it never does
------------------
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes a ledger row.
* Never reads paper credentials (``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``).
* Never modifies any position, order, or account state.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.live_account_check import _make_live_client, check_credentials
from src.tools.live_shadow_preflight import (
    _run_strategy_preview,
    _size_candidate,
    check_candidates,
    check_live_account,
    check_live_orders,
    check_live_position,
    check_live_sizing,
)
from src.tools.paper_status import check_config

# Fields written to CSV and JSON intent rows
_INTENT_COLUMNS = [
    "checked_at_utc",
    "symbol",
    "side",
    "order_type",
    "live_sizing_mode",
    "effective_quantity",
    "effective_notional",
    "entry_price",
    "live_max_order_notional",
    "live_max_notional",
    "readiness_decision",
    "dry_run_only",
    "submit_allowed",
    "sizing_status",
    "sizing_reason",
]

_DRY_RUN_ONLY   = True
_SUBMIT_ALLOWED = False


# ---------------------------------------------------------------------------
# Readiness gate (inline — same checks as live_readiness_gate, symbol-scoped)
# ---------------------------------------------------------------------------

def _run_readiness_checks(
    cfg: Any,
    client: Any,
    symbol: str,
    checked_at_utc: str,
) -> tuple[str, list[str], list]:
    """Run live readiness checks for *symbol*.  Returns (decision, top_blockers, intents).

    Decision is "GO" only when every check is PASS.
    Does not write any gate report files.
    """
    intents: list = []
    statuses: list[str] = []
    blockers: list[str] = []

    # Account health
    acct_result = check_live_account(client)
    statuses.append(acct_result["status"])
    if acct_result["status"] in ("WARN", "FAIL"):
        detail = acct_result.get("detail", "")
        if " — " in detail:
            detail = detail.split(" — ", 1)[-1]
        blockers.append(f"[live_account] {detail}")

    # Candidates + sizing
    try:
        intents = _run_strategy_preview(cfg, symbol)
    except Exception as exc:
        statuses.append("FAIL")
        blockers.append(f"[candidates] strategy error — {exc}")

    cand = check_candidates(intents, symbol)
    statuses.append(cand["status"])
    if cand["status"] == "FAIL":
        blockers.append(f"[candidates] {cand.get('detail', '')}")

    if cand["status"] != "FAIL" and intents:
        sizing = check_live_sizing(intents, cfg)
        statuses.append(sizing["status"])
        if sizing["status"] == "FAIL":
            blockers.append(f"[live_sizing] {sizing.get('detail', '')}")

    # Open position / open orders
    pos = check_live_position(client, symbol)
    statuses.append(pos["status"])
    if pos["status"] == "FAIL":
        blockers.append(f"[live_position] {pos.get('detail', '')}")

    orders_result = check_live_orders(client, symbol)
    statuses.append(orders_result["status"])
    if orders_result["status"] == "FAIL":
        blockers.append(f"[live_orders] {orders_result.get('detail', '')}")

    decision = "GO" if all(s == "PASS" for s in statuses) else "NO-GO"
    return decision, blockers, intents


# ---------------------------------------------------------------------------
# Intent builder
# ---------------------------------------------------------------------------

def build_intent_rows(
    intents: list,
    cfg: Any,
    checked_at_utc: str,
    decision: str,
) -> list[dict[str, Any]]:
    """Return one row dict per candidate intent with all required fields."""
    rows: list[dict[str, Any]] = []
    for intent in intents:
        sized = _size_candidate(intent, cfg)
        row: dict[str, Any] = {
            "checked_at_utc":      checked_at_utc,
            "symbol":              sized.get("symbol", ""),
            "side":                sized.get("side", ""),
            "order_type":          sized.get("order_type", ""),
            "live_sizing_mode":    sized.get("live_sizing_mode", ""),
            "effective_quantity":  sized.get("effective_quantity"),
            "effective_notional":  sized.get("effective_notional"),
            "entry_price":         sized.get("entry_price"),
            "live_max_order_notional": sized.get("live_max_order_notional"),
            "live_max_notional":   sized.get("live_max_notional"),
            "readiness_decision":  decision,
            "dry_run_only":        _DRY_RUN_ONLY,
            "submit_allowed":      _SUBMIT_ALLOWED,
            "sizing_status":       sized.get("sizing_status", ""),
            "sizing_reason":       sized.get("sizing_reason", ""),
        }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------

def write_artifacts(
    output_dir: Path,
    intent_rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> tuple[Path, Path, Path]:
    """Write CSV, JSON intents, and summary.  Returns (csv_path, json_path, summary_path)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path     = output_dir / "live_order_intents.csv"
    json_path    = output_dir / "live_order_intents.json"
    summary_path = output_dir / "live_dry_run_summary.json"

    # CSV
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_INTENT_COLUMNS)
        writer.writeheader()
        for row in intent_rows:
            writer.writerow({k: row.get(k, "") for k in _INTENT_COLUMNS})

    # JSON intents
    json_path.write_text(
        json.dumps(intent_rows, indent=2, default=str),
        encoding="utf-8",
    )

    # Summary
    summary_path.write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )

    return csv_path, json_path, summary_path


def build_summary(
    checked_at_utc: str,
    symbol: str,
    decision: str,
    blockers: list[str],
    intent_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a structured summary dict."""
    pass_rows = [r for r in intent_rows if r.get("sizing_status") == "PASS"]
    fail_rows = [r for r in intent_rows if r.get("sizing_status") == "FAIL"]
    return {
        "checked_at_utc":  checked_at_utc,
        "symbol":          symbol,
        "decision":        decision,
        "dry_run_only":    _DRY_RUN_ONLY,
        "submit_allowed":  _SUBMIT_ALLOWED,
        "intent_count":    len(intent_rows),
        "pass_count":      len(pass_rows),
        "fail_count":      len(fail_rows),
        "top_blockers":    blockers[:5],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_dry_run_intents",
        description=(
            "Dry-run live order intent audit. Runs all live readiness checks "
            "and produces hypothetical order intent artifacts. "
            "Never submits or cancels orders. Never writes a ledger."
        ),
    )
    parser.add_argument("--config",     required=True, help="Path to YAML config")
    parser.add_argument("--output-dir", required=True, help="Directory for output artifacts")
    parser.add_argument("--symbol",     default="SPY",  help="Symbol to check (default: SPY)")
    args = parser.parse_args(argv)

    output_dir     = Path(args.output_dir)
    symbol         = args.symbol.strip().upper()
    checked_at_utc = datetime.now(timezone.utc).isoformat()

    # Config
    cfg_result, cfg = check_config(args.config)
    if cfg_result["status"] == "FAIL" or cfg is None:
        print(f"\n[FAIL] config: {cfg_result.get('detail', 'could not load config')}")
        sys.exit(1)

    # Credentials
    cred_result, creds = check_credentials()
    if cred_result["status"] == "FAIL" or creds is None:
        print(f"\n[FAIL] credentials: {cred_result.get('detail', 'missing live credentials')}")
        sys.exit(1)

    api_key, secret_key = creds
    try:
        client = _make_live_client(api_key, secret_key)
    except Exception as exc:
        print(f"\n[FAIL] could not create live client: {exc}")
        sys.exit(1)

    # Readiness checks
    decision, blockers, intents = _run_readiness_checks(cfg, client, symbol, checked_at_utc)

    if decision != "GO":
        summary = build_summary(checked_at_utc, symbol, decision, blockers, [])
        _, _, summary_path = write_artifacts(output_dir, [], summary)
        print(f"\n=== Live Dry-Run Intents ===")
        print(f"  symbol   : {symbol}")
        print(f"  decision : {decision}")
        print(f"  blockers :")
        for b in blockers:
            print(f"    ! {b}")
        print(f"\n  dry_run_only   : {_DRY_RUN_ONLY}")
        print(f"  submit_allowed : {_SUBMIT_ALLOWED}")
        print(f"\n  NO-GO — no submit-ready intents written.")
        print(f"  summary written to: {summary_path}")
        print("=" * 30)
        sys.exit(1)

    # Build intent rows (GO path)
    intent_rows = build_intent_rows(intents, cfg, checked_at_utc, decision)
    summary     = build_summary(checked_at_utc, symbol, decision, blockers, intent_rows)
    csv_path, json_path, summary_path = write_artifacts(output_dir, intent_rows, summary)

    print(f"\n=== Live Dry-Run Intents ===")
    print(f"  symbol        : {symbol}")
    print(f"  decision      : {decision}")
    print(f"  intent_count  : {summary['intent_count']}")
    print(f"  pass_count    : {summary['pass_count']}")
    print(f"  fail_count    : {summary['fail_count']}")
    print(f"  dry_run_only  : {_DRY_RUN_ONLY}")
    print(f"  submit_allowed: {_SUBMIT_ALLOWED}")
    print(f"\n  Artifacts written:")
    print(f"    {csv_path}")
    print(f"    {json_path}")
    print(f"    {summary_path}")
    print("=" * 30)


if __name__ == "__main__":
    main()
