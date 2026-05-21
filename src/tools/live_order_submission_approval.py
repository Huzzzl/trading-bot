"""
tools/live_order_submission_approval.py
-----------------------------------------
Offline CLI that reads a live_trading_approval.json artifact and produces a
human approval artifact authorising exactly one live order submission attempt.

Usage::

    python -m src.tools.live_order_submission_approval \\
        --live-trading-approval output/live_trading_approval.json \\
        --operator-name "Huzzzl" \\
        --approval-note "Approve one SPY $100 notional live order submission attempt only." \\
        --risk-acknowledge \\
        --symbol SPY \\
        --max-notional 100.0 \\
        --output output/live_order_submission_approval.json

What the artifact approves
--------------------------
* approval_scope: "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
* live_order_submission_approved: true
* order_submission_approval_for_single_attempt: true

What the artifact also records
------------------------------
* live_trading_approved: true (carried from source trading approval)
* source_live_trading_approval_path: path to the trading approval artifact

Prerequisites
-------------
The source live_trading_approval.json must have:
* live_trading_approved == true
* live_order_submission_approved == false  (not already granted)
* approval_scope == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
* risk_acknowledged == true
* approved_symbol matches requested --symbol (case-insensitive)
* approved_max_notional >= requested --max-notional

Separation requirement
----------------------
live_trading_approval and live_order_submission_approval are distinct artifacts.
Combined approval artifacts are not allowed.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes the live ledger.
* Never mutates the source trading approval file.
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
# Reader
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_trading_approval(
    trading_approval: dict[str, Any],
    symbol: str,
    max_notional: float,
) -> list[str]:
    """Return violations found in the trading approval artifact."""
    violations: list[str] = []

    if trading_approval.get("live_trading_approved") is not True:
        violations.append(
            f"live_trading_approved={trading_approval.get('live_trading_approved')!r}"
            " — must be true in trading approval"
        )
    if trading_approval.get("live_order_submission_approved") is not False:
        violations.append(
            f"live_order_submission_approved={trading_approval.get('live_order_submission_approved')!r}"
            " — must be false in trading approval (separate artifact required)"
        )
    scope = trading_approval.get("approval_scope", "")
    if scope != _APPROVAL_SCOPE:
        violations.append(
            f"approval_scope={scope!r} — must be {_APPROVAL_SCOPE!r}"
        )
    if trading_approval.get("risk_acknowledged") is not True:
        violations.append(
            f"risk_acknowledged={trading_approval.get('risk_acknowledged')!r}"
            " — must be true in trading approval"
        )

    approved_symbol = str(trading_approval.get("approved_symbol", "")).strip().upper()
    requested_symbol = symbol.strip().upper()
    if approved_symbol != requested_symbol:
        violations.append(
            f"approved_symbol={approved_symbol!r} in trading approval does not match"
            f" requested symbol={requested_symbol!r}"
        )

    try:
        approved_max = float(trading_approval.get("approved_max_notional", 0))
    except (TypeError, ValueError):
        approved_max = 0.0
    if approved_max < max_notional:
        violations.append(
            f"approved_max_notional={approved_max} in trading approval is less than"
            f" requested max_notional={max_notional}"
        )

    return violations


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
    trading_approval: dict[str, Any],
    trading_approval_path: Path,
    operator_name: str,
    approval_note: str,
    symbol: str,
    max_notional: float,
) -> dict[str, Any]:
    """Produce the live_order_submission_approval artifact dict."""
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "checked_at_utc":                          now,
        "approval_timestamp_utc":                  now,
        "operator_name":                           operator_name.strip(),
        "approval_note":                           approval_note.strip(),
        "risk_acknowledged":                       True,
        "approval_scope":                          _APPROVAL_SCOPE,
        "source_live_trading_approval_path":       str(trading_approval_path),
        "approved_symbol":                         symbol.strip().upper(),
        "approved_max_notional":                   max_notional,
        "live_trading_approved":                   True,
        "live_order_submission_approved":          True,
        "order_submission_approval_for_single_attempt": True,
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
    print("\n=== Live Order Submission Approval ===")
    print(f"  operator_name                          : {artifact['operator_name']}")
    print(f"  approval_timestamp_utc                 : {artifact['approval_timestamp_utc']}")
    print(f"  approval_scope                         : {artifact['approval_scope']}")
    print(f"  approved_symbol                        : {artifact['approved_symbol']}")
    print(f"  approved_max_notional                  : {artifact['approved_max_notional']}")
    print(f"  risk_acknowledged                      : {artifact['risk_acknowledged']}")
    print(f"  live_trading_approved                  : {artifact['live_trading_approved']}")
    print(f"  live_order_submission_approved         : {artifact['live_order_submission_approved']}")
    print(f"  order_submission_approval_for_single_attempt: {artifact['order_submission_approval_for_single_attempt']}")
    print(f"  approval_note                          : {artifact['approval_note']!r}")
    print()
    print("  > This approval authorises one live order submission attempt only.")
    print("  > It is a separate artifact from live_trading_approval.")
    print("  > Combined approval artifacts are not allowed.")
    print("=" * 40)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_order_submission_approval",
        description=(
            "Offline approval artifact CLI: reads live_trading_approval.json and produces "
            "live_order_submission_approval.json authorising one live order submission attempt. "
            "Does NOT submit orders. No credentials. Never calls Alpaca."
        ),
    )
    parser.add_argument(
        "--live-trading-approval", required=True, dest="live_trading_approval",
        help="Path to live_trading_approval.json",
    )
    parser.add_argument(
        "--operator-name", required=True, dest="operator_name",
        help="Name of the human operator approving order submission",
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
        help="Target symbol; must match trading approval; normalised to uppercase",
    )
    parser.add_argument(
        "--max-notional", required=True, type=float, dest="max_notional",
        help="Maximum notional; must be > 0, <= 100.0, and <= trading approval max",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write live_order_submission_approval.json",
    )
    args = parser.parse_args(argv)

    input_violations = _validate_inputs(
        args.operator_name,
        args.approval_note,
        args.risk_acknowledge,
        args.symbol,
        args.max_notional,
    )
    if input_violations:
        for v in input_violations:
            print(f"\n  [FAIL] {v}")
        sys.exit(1)

    trading_approval_path = Path(args.live_trading_approval)
    try:
        trading_approval = _read_json(trading_approval_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  [FAIL] trading approval: {exc}")
        sys.exit(1)

    ta_violations = _validate_trading_approval(
        trading_approval, args.symbol, args.max_notional
    )
    if ta_violations:
        for v in ta_violations:
            print(f"\n  [FAIL] {v}")
        sys.exit(1)

    artifact = build_approval(
        trading_approval,
        trading_approval_path,
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
