"""
tools/paper_pre_submit_check.py
---------------------------------
Read-only pre-submit checklist CLI.

Run this before any real paper submit to confirm it is safe to proceed.

Usage::

    python -m src.tools.paper_pre_submit_check \\
        --config config/settings.paper.local.yaml \\
        --output-dir output/paper_smoke_check \\
        --ledger output/paper_execution_ledger.csv

Add ``--verify-ledger`` to also reconcile ledger rows against Alpaca
(requires ``ALPACA_API_KEY`` and ``ALPACA_SECRET_KEY``):

    python -m src.tools.paper_pre_submit_check \\
        --config config/settings.paper.local.yaml \\
        --output-dir output/paper_smoke_check \\
        --ledger output/paper_execution_ledger.csv \\
        --verify-ledger

Checks performed
----------------
1. Config load + mode classification.
2. Safety guard configuration summary.
3. Daily usage and remaining capacity from ledger.
4. Submit readiness (kill switch, daily limits, market hours).
5. Mode summary banner (submit vs preview).
6. Ledger presence and parseability.
7. (optional) Ledger verification against Alpaca — only when ``--verify-ledger``.

What it never does (unless ``--verify-ledger``)
------------------------------------------------
* Never instantiates AlpacaBrokerAdapter.
* Never reads Alpaca credentials.
* Never calls any network endpoint.
* Never submits or cancels orders.
* Never writes files.

Exit codes
----------
* 0 — all checks PASS.
* 1 — any check WARN or FAIL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from src.tools.paper_status import (
    _result,
    check_config,
    check_daily_usage,
    check_ledger,
    check_safety_config,
    check_submit_readiness,
    classify_mode,
    print_report,
)


# ---------------------------------------------------------------------------
# New checks specific to pre-submit
# ---------------------------------------------------------------------------

def check_mode_summary(cfg) -> dict[str, Any]:
    """Return WARN in submit modes, PASS in preview modes.

    The detail string carries the human-readable banner shown to the operator.
    """
    mode = classify_mode(cfg.execution)
    ex = cfg.execution
    if mode == "buy_submit":
        coid = ex.paper_selected_client_order_id
        return _result(
            "mode_summary",
            "WARN",
            f"SUBMIT MODE DETECTED — review selected client_order_id={coid!r}"
            " before running src.main",
        )
    if mode == "close_submit":
        coid = ex.paper_selected_close_client_order_id
        return _result(
            "mode_summary",
            "WARN",
            f"SUBMIT MODE DETECTED (close) — review selected_close_client_order_id={coid!r}"
            " before running src.main",
        )
    return _result(
        "mode_summary",
        "PASS",
        f"PREVIEW MODE ({mode}) — no order will be submitted by src.main",
    )


def check_verify_ledger(ledger_path: Path) -> dict[str, Any]:
    """Run read-only ledger verification against Alpaca paper account.

    Requires ``ALPACA_API_KEY`` and ``ALPACA_SECRET_KEY`` in the environment.
    Only called when the operator passes ``--verify-ledger``.
    """
    from src.tools.paper_ledger_verify import (
        _make_client,
        _resolve_credentials,
        verify_ledger,
    )

    try:
        api_key, secret_key = _resolve_credentials()
    except RuntimeError as exc:
        return _result("verify_ledger", "FAIL", f"credentials missing — {exc}")

    if not ledger_path.exists():
        return _result("verify_ledger", "FAIL", f"ledger not found: {ledger_path}")

    try:
        client = _make_client(api_key, secret_key)
        rows = verify_ledger(ledger_path, client)
    except Exception as exc:
        return _result("verify_ledger", "FAIL", f"verification error — {exc}")

    total      = len(rows)
    mismatches = sum(1 for r in rows if not r["status_match"] and not r.get("error"))
    errors     = sum(1 for r in rows if r.get("error") and r["error"] != "no alpaca_order_id")
    skipped    = sum(1 for r in rows if r.get("error") == "no alpaca_order_id")

    detail = (
        f"{total} row(s) checked; matched={total - mismatches - errors - skipped}"
        f" mismatched={mismatches} errors={errors} skipped={skipped}"
    )

    if mismatches or errors:
        return _result("verify_ledger", "WARN", detail)
    return _result("verify_ledger", "PASS", detail)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.paper_pre_submit_check",
        description=(
            "Read-only pre-submit checklist. "
            "Confirms safety guards, daily limits, and mode before src.main runs. "
            "No credentials required unless --verify-ledger is set."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to paper settings YAML",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Run output directory (used to resolve relative paths)",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="Path to the paper execution ledger CSV (default: from config)",
    )
    parser.add_argument(
        "--verify-ledger",
        action="store_true",
        default=False,
        help=(
            "Also verify ledger rows against Alpaca paper account "
            "(requires ALPACA_API_KEY and ALPACA_SECRET_KEY)."
        ),
    )
    parser.add_argument(
        "--last",
        type=int,
        default=3,
        metavar="N",
        help="Number of ledger rows to display (default: 3)",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)

    checks: list[dict[str, Any]] = []
    all_statuses: list[str] = []

    # --- 1. Config ---
    config_result, cfg = check_config(config_path)
    checks.append(config_result)
    all_statuses.append(config_result["status"])

    # Resolve ledger path: CLI flag > config > default
    if args.ledger:
        ledger_path = Path(args.ledger)
    elif cfg is not None:
        ledger_path = Path(cfg.execution.paper_ledger_path)
    else:
        ledger_path = Path("output/paper_execution_ledger.csv")

    # --- 2–5. Safety sections (only when config loaded) ---
    if cfg is not None:
        safety_result = check_safety_config(cfg)
        checks.append(safety_result)
        all_statuses.append(safety_result["status"])

        daily_result = check_daily_usage(ledger_path, cfg)
        checks.append(daily_result)
        all_statuses.append(daily_result["status"])

        readiness_result = check_submit_readiness(cfg, daily_result)
        checks.append(readiness_result)
        all_statuses.append(readiness_result["status"])

        mode_result = check_mode_summary(cfg)
        checks.append(mode_result)
        all_statuses.append(mode_result["status"])

    # --- 6. Ledger ---
    ledger_result = check_ledger(ledger_path, last_n=args.last)
    checks.append(ledger_result)
    all_statuses.append(ledger_result["status"])
    ledger_rows = ledger_result.get("rows") or []

    # --- 7. Optional ledger verification ---
    if args.verify_ledger:
        verify_result = check_verify_ledger(ledger_path)
        checks.append(verify_result)
        all_statuses.append(verify_result["status"])

    # --- Final status ---
    if "FAIL" in all_statuses:
        final_status = "FAIL"
    elif "WARN" in all_statuses:
        final_status = "WARN"
    else:
        final_status = "PASS"

    print_report(checks, ledger_rows=ledger_rows, final_status=final_status)

    if final_status != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
