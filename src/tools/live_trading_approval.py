"""
tools/live_trading_approval.py
--------------------------------
Offline CLI that produces a human approval artifact authorising the
live trading gate for a single live order attempt.

Usage::

    python -m src.tools.live_trading_approval \\
        --operator-name "Huzzzl" \\
        --approval-note "Approve live trading gate for one SPY $100 notional attempt; order submission still requires separate approval." \\
        --risk-acknowledge \\
        --symbol SPY \\
        --max-notional 100.0 \\
        --output output/live_trading_approval.json

What the artifact approves
--------------------------
* approval_scope: "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
* live_trading_approved: true

What the artifact explicitly does NOT approve
---------------------------------------------
* live_order_submission_approved: false

This artifact authorises the live trading gate only.
It does NOT authorise live order submission.
live_order_submission_approval must be a separate artifact.
Combined approval artifacts are not allowed.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes the live ledger.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_NOTIONAL_LIMIT = 100.0
_APPROVAL_SCOPE = "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_inputs(
    operator_name: str,
    approval_note: str,
    risk_acknowledge: bool,
    symbol: str,
    max_notional: float,
) -> list[str]:
    violations: list[str] = []
    if not operator_name.strip():
        violations.append("--operator-name must not be empty")
    if not approval_note.strip():
        violations.append("--approval-note must not be empty")
    if not risk_acknowledge:
        violations.append("--risk-acknowledge must be set")
    if not symbol.strip():
        violations.append("--symbol must not be empty")
    if max_notional <= 0:
        violations.append(f"--max-notional must be > 0 (got {max_notional})")
    if max_notional > _MAX_NOTIONAL_LIMIT:
        violations.append(
            f"--max-notional must be <= {_MAX_NOTIONAL_LIMIT} (got {max_notional})"
        )
    return violations


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_approval(
    operator_name: str,
    approval_note: str,
    symbol: str,
    max_notional: float,
) -> dict[str, Any]:
    """Produce the live_trading_approval artifact dict."""
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "checked_at_utc":               now,
        "approval_timestamp_utc":       now,
        "operator_name":                operator_name.strip(),
        "approval_note":                approval_note.strip(),
        "risk_acknowledged":            True,
        "approval_scope":               _APPROVAL_SCOPE,
        "approved_symbol":              symbol.strip().upper(),
        "approved_max_notional":        max_notional,
        "live_trading_approved":        True,
        "live_order_submission_approved": False,
    }


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_approval(output_path: Path, artifact: dict[str, Any]) -> Path:
    """Write the approval artifact JSON to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_approval(artifact: dict[str, Any]) -> None:
    """Print the approval summary."""
    print("\n=== Live Trading Approval ===")
    print(f"  operator_name                 : {artifact['operator_name']}")
    print(f"  approval_timestamp_utc        : {artifact['approval_timestamp_utc']}")
    print(f"  approval_scope                : {artifact['approval_scope']}")
    print(f"  approved_symbol               : {artifact['approved_symbol']}")
    print(f"  approved_max_notional         : {artifact['approved_max_notional']}")
    print(f"  risk_acknowledged             : {artifact['risk_acknowledged']}")
    print(f"  live_trading_approved         : {artifact['live_trading_approved']}")
    print(f"  live_order_submission_approved: {artifact['live_order_submission_approved']}")
    print(f"  approval_note                 : {artifact['approval_note']!r}")
    print()
    print("  > This approval authorises the live trading gate only.")
    print("  > It does NOT authorise live order submission.")
    print("  > live_order_submission_approval must be a separate artifact.")
    print("=" * 40)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_trading_approval",
        description=(
            "Offline approval artifact CLI: produces live_trading_approval.json "
            "authorising the live trading gate for a single live order attempt. "
            "Does NOT approve order submission. No credentials. Never calls Alpaca."
        ),
    )
    parser.add_argument(
        "--operator-name", required=True, dest="operator_name",
        help="Name of the human operator approving live trading",
    )
    parser.add_argument(
        "--approval-note", required=True, dest="approval_note",
        help="Free-text note confirming scope of approval",
    )
    parser.add_argument(
        "--risk-acknowledge", action="store_true", dest="risk_acknowledge",
        help="Explicit risk acknowledgement (required)",
    )
    parser.add_argument(
        "--symbol", required=True,
        help="Target symbol for this approval (e.g. SPY); normalised to uppercase",
    )
    parser.add_argument(
        "--max-notional", required=True, type=float, dest="max_notional",
        help="Maximum notional for this approval; must be > 0 and <= 100.0",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write live_trading_approval.json",
    )
    args = parser.parse_args(argv)

    violations = _validate_inputs(
        args.operator_name,
        args.approval_note,
        args.risk_acknowledge,
        args.symbol,
        args.max_notional,
    )
    if violations:
        for v in violations:
            print(f"\n  [FAIL] {v}")
        sys.exit(1)

    artifact = build_approval(
        args.operator_name,
        args.approval_note,
        args.symbol,
        args.max_notional,
    )
    print_approval(artifact)

    output_path = write_approval(Path(args.output), artifact)
    print(f"\n  artifact: {output_path}")


if __name__ == "__main__":
    main()
