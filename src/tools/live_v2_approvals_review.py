"""
tools/live_v2_approvals_review.py
-----------------------------------
Offline CLI that reads both v2 approval artifacts and verifies they are
consistent, separate, and scoped to a single live order attempt.

Usage::

    python -m src.tools.live_v2_approvals_review \\
        --live-trading-approval output/live_trading_approval.json \\
        --live-order-submission-approval output/live_order_submission_approval.json

PASS conditions
---------------
live_trading_approval.json:
  * live_trading_approved == true
  * live_order_submission_approved == false
  * approval_scope == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
  * risk_acknowledged == true
  * approved_symbol non-empty
  * approved_max_notional > 0 and <= 100.0

live_order_submission_approval.json:
  * live_trading_approved == true
  * live_order_submission_approved == true
  * order_submission_approval_for_single_attempt == true
  * approval_scope == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
  * risk_acknowledged == true
  * source_live_trading_approval_path non-empty
  * approved_symbol non-empty
  * approved_max_notional > 0 and <= 100.0

Cross-checks:
  * symbols match
  * submission approved_max_notional <= trading approved_max_notional
  * source_live_trading_approval_path matches provided trading approval path
  * two artifacts are separate files (not the same path)

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes the live ledger.
* Never writes any file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_APPROVAL_SCOPE = "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
_MAX_NOTIONAL_LIMIT = 100.0


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

def _validate_trading_approval(ta: dict[str, Any]) -> list[str]:
    violations: list[str] = []

    if ta.get("live_trading_approved") is not True:
        violations.append(
            f"live_trading_approved={ta.get('live_trading_approved')!r} — must be true"
        )
    if ta.get("live_order_submission_approved") is not False:
        violations.append(
            f"live_order_submission_approved={ta.get('live_order_submission_approved')!r}"
            " — must be false in trading approval"
        )
    if ta.get("approval_scope") != _APPROVAL_SCOPE:
        violations.append(
            f"approval_scope={ta.get('approval_scope')!r} — must be {_APPROVAL_SCOPE!r}"
        )
    if ta.get("risk_acknowledged") is not True:
        violations.append(
            f"risk_acknowledged={ta.get('risk_acknowledged')!r} — must be true"
        )
    symbol = str(ta.get("approved_symbol", "")).strip()
    if not symbol:
        violations.append("approved_symbol must be non-empty")
    try:
        notional = float(ta.get("approved_max_notional", 0))
    except (TypeError, ValueError):
        notional = 0.0
    if notional <= 0 or notional > _MAX_NOTIONAL_LIMIT:
        violations.append(
            f"approved_max_notional={ta.get('approved_max_notional')!r}"
            f" — must be > 0 and <= {_MAX_NOTIONAL_LIMIT}"
        )

    return violations


def _validate_submission_approval(sa: dict[str, Any]) -> list[str]:
    violations: list[str] = []

    if sa.get("live_trading_approved") is not True:
        violations.append(
            f"live_trading_approved={sa.get('live_trading_approved')!r} — must be true"
        )
    if sa.get("live_order_submission_approved") is not True:
        violations.append(
            f"live_order_submission_approved={sa.get('live_order_submission_approved')!r}"
            " — must be true in submission approval"
        )
    if sa.get("order_submission_approval_for_single_attempt") is not True:
        violations.append(
            f"order_submission_approval_for_single_attempt="
            f"{sa.get('order_submission_approval_for_single_attempt')!r} — must be true"
        )
    if sa.get("approval_scope") != _APPROVAL_SCOPE:
        violations.append(
            f"approval_scope={sa.get('approval_scope')!r} — must be {_APPROVAL_SCOPE!r}"
        )
    if sa.get("risk_acknowledged") is not True:
        violations.append(
            f"risk_acknowledged={sa.get('risk_acknowledged')!r} — must be true"
        )
    src_path = str(sa.get("source_live_trading_approval_path", "")).strip()
    if not src_path:
        violations.append("source_live_trading_approval_path must be non-empty")
    symbol = str(sa.get("approved_symbol", "")).strip()
    if not symbol:
        violations.append("approved_symbol must be non-empty")
    try:
        notional = float(sa.get("approved_max_notional", 0))
    except (TypeError, ValueError):
        notional = 0.0
    if notional <= 0 or notional > _MAX_NOTIONAL_LIMIT:
        violations.append(
            f"approved_max_notional={sa.get('approved_max_notional')!r}"
            f" — must be > 0 and <= {_MAX_NOTIONAL_LIMIT}"
        )

    return violations


def _cross_check(
    ta: dict[str, Any],
    sa: dict[str, Any],
    ta_path: Path,
    sa_path: Path,
) -> list[str]:
    violations: list[str] = []

    ta_symbol = str(ta.get("approved_symbol", "")).strip().upper()
    sa_symbol = str(sa.get("approved_symbol", "")).strip().upper()
    if ta_symbol and sa_symbol and ta_symbol != sa_symbol:
        violations.append(
            f"symbol mismatch: trading_approval approved_symbol={ta_symbol!r}"
            f" != submission_approval approved_symbol={sa_symbol!r}"
        )

    try:
        ta_notional = float(ta.get("approved_max_notional", 0))
        sa_notional = float(sa.get("approved_max_notional", 0))
        if sa_notional > ta_notional:
            violations.append(
                f"submission approved_max_notional={sa_notional} exceeds"
                f" trading approved_max_notional={ta_notional}"
            )
    except (TypeError, ValueError):
        pass

    src_path_str = str(sa.get("source_live_trading_approval_path", "")).strip()
    if src_path_str:
        try:
            recorded = Path(src_path_str).resolve()
            provided = ta_path.resolve()
            if recorded != provided:
                violations.append(
                    f"source_live_trading_approval_path={src_path_str!r} does not match"
                    f" provided trading approval path={str(ta_path)!r}"
                )
        except Exception:
            if src_path_str != str(ta_path):
                violations.append(
                    f"source_live_trading_approval_path={src_path_str!r} does not match"
                    f" provided trading approval path={str(ta_path)!r}"
                )

    try:
        if ta_path.resolve() == sa_path.resolve():
            violations.append(
                "trading approval and submission approval must be separate files"
            )
    except Exception:
        if str(ta_path) == str(sa_path):
            violations.append(
                "trading approval and submission approval must be separate files"
            )

    return violations


# ---------------------------------------------------------------------------
# Review builder
# ---------------------------------------------------------------------------

def validate_approvals(
    ta: dict[str, Any],
    sa: dict[str, Any],
    ta_path: Path,
    sa_path: Path,
) -> tuple[str, list[str]]:
    """Return (result, violations) where result is 'PASS' or 'FAIL'."""
    violations: list[str] = []
    violations += _validate_trading_approval(ta)
    violations += _validate_submission_approval(sa)
    violations += _cross_check(ta, sa, ta_path, sa_path)
    result = "PASS" if not violations else "FAIL"
    return result, violations


def build_review(
    ta: dict[str, Any],
    sa: dict[str, Any],
    ta_path: Path,
    sa_path: Path,
) -> dict[str, Any]:
    result, violations = validate_approvals(ta, sa, ta_path, sa_path)
    return {
        "trading_approved":          ta.get("live_trading_approved"),
        "order_submission_approved": sa.get("live_order_submission_approved"),
        "symbol":                    str(ta.get("approved_symbol", "")).strip().upper() or None,
        "max_notional":              ta.get("approved_max_notional"),
        "approval_scope":            ta.get("approval_scope"),
        "review_result":             result,
        "violations":                violations,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_review(review: dict[str, Any]) -> None:
    print("\n=== Live V2 Approvals Review ===")
    print(f"  trading_approved         : {review['trading_approved']}")
    print(f"  order_submission_approved: {review['order_submission_approved']}")
    print(f"  symbol                   : {review['symbol']}")
    print(f"  max_notional             : {review['max_notional']}")
    print(f"  approval_scope           : {review['approval_scope']}")
    print(f"  review_result            : {review['review_result']}")
    if review["violations"]:
        print("  violations:")
        for v in review["violations"]:
            print(f"    ! {v}")
    print("=" * 40)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_v2_approvals_review",
        description=(
            "Offline review of live v2 approval artifacts. "
            "Verifies both artifacts are consistent, separate, and scoped to a single live order attempt. "
            "Never calls Alpaca. Never writes files."
        ),
    )
    parser.add_argument(
        "--live-trading-approval", required=True, dest="live_trading_approval",
        help="Path to live_trading_approval.json",
    )
    parser.add_argument(
        "--live-order-submission-approval", required=True, dest="live_order_submission_approval",
        help="Path to live_order_submission_approval.json",
    )
    args = parser.parse_args(argv)

    ta_path = Path(args.live_trading_approval)
    sa_path = Path(args.live_order_submission_approval)

    try:
        ta = _read_json(ta_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  [FAIL] trading approval: {exc}")
        sys.exit(1)

    try:
        sa = _read_json(sa_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  [FAIL] submission approval: {exc}")
        sys.exit(1)

    review = build_review(ta, sa, ta_path, sa_path)
    print_review(review)

    if review["review_result"] == "PASS":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
