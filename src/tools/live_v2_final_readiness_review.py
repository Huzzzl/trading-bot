"""
tools/live_v2_final_readiness_review.py
-----------------------------------------
Offline CLI that produces a single summary artifact proving the v2 approval
layer is complete, the executor accepted the v2 artifacts, and the remaining
blocker is the three default config-safety flags.

Usage::

    python -m src.tools.live_v2_final_readiness_review \\
        --v2-approvals-review \\
        --live-trading-approval output/live_trading_approval.json \\
        --live-order-submission-approval output/live_order_submission_approval.json \\
        --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \\
        --output output/live_v2_final_readiness_review.json

PASS conditions
---------------
All of the following must be true:

* ``--v2-approvals-review`` flag was given (approvals sub-review was requested)
* ``live_trading_approval.json`` and ``live_order_submission_approval.json``
  pass all ``live_v2_approvals_review`` validation rules
* ``live_submit_blocked_report.json`` passes all
  ``live_v2_executor_readiness_review`` validation rules:
    - blocked == true
    - submit_order_called == false
    - block_guard == "config_safety"
    - violations contain at least one expected config-safety flag

FAIL conditions
---------------
Any of the following cause FAIL:

* ``--v2-approvals-review`` was not given (approvals sub-review not run)
* Either approval artifact fails validation
* Executor report is missing or malformed
* blocked == false
* submit_order_called == true
* block_guard is approval_artifact, v2_trading_approval, v2_submission_approval,
  v2_cross_check, or anything other than config_safety

Output JSON
-----------
Written to ``--output`` path (required):

    checked_at_utc
    review_result              : "PASS" or "FAIL"
    approvals_review_result    : "PASS", "FAIL", or "NOT_RUN"
    executor_readiness_review_result : "PASS" or "FAIL"
    symbol
    approved_max_notional
    block_guard
    submit_order_called
    live_submit_enabled        : false  (always)
    real_submit_implemented    : false  (always)
    final_blocker              : "config_safety" on PASS, block_guard value on FAIL
    violations                 : combined violation strings

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

from src.tools.live_v2_approvals_review import validate_approvals, _read_json as _read_approval_json
from src.tools.live_v2_executor_readiness_review import (
    parse_blocked_report,
    validate_readiness,
)


# ---------------------------------------------------------------------------
# Review logic
# ---------------------------------------------------------------------------

def run_review(
    *,
    ta_path: Path | None,
    sa_path: Path | None,
    executor_report_path: Path,
    run_approvals_review: bool,
) -> dict[str, Any]:
    """Run both sub-reviews and return the combined result dict.

    Parameters
    ----------
    ta_path
        Path to live_trading_approval.json. Required when run_approvals_review=True.
    sa_path
        Path to live_order_submission_approval.json. Required when run_approvals_review=True.
    executor_report_path
        Path to live_submit_blocked_report.json.
    run_approvals_review
        True when --v2-approvals-review flag was given.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    all_violations: list[str] = []

    # --- Sub-review 1: v2 approvals ---
    if not run_approvals_review:
        approvals_result = "NOT_RUN"
        all_violations.append(
            "--v2-approvals-review flag not given; approvals sub-review was not run"
        )
        symbol = None
        approved_max_notional = None
    else:
        assert ta_path is not None and sa_path is not None
        try:
            ta = _read_approval_json(ta_path)
        except (FileNotFoundError, ValueError) as exc:
            return _fail_early(checked_at, f"trading approval: {exc}")
        try:
            sa = _read_approval_json(sa_path)
        except (FileNotFoundError, ValueError) as exc:
            return _fail_early(checked_at, f"submission approval: {exc}")

        a_result, a_violations = validate_approvals(ta, sa, ta_path, sa_path)
        approvals_result = a_result
        if a_violations:
            all_violations += [f"[approvals] {v}" for v in a_violations]

        symbol = str(ta.get("approved_symbol", "")).strip().upper() or None
        try:
            approved_max_notional = float(ta.get("approved_max_notional", 0))
        except (TypeError, ValueError):
            approved_max_notional = None

    # --- Sub-review 2: executor readiness ---
    try:
        executor_report = parse_blocked_report(executor_report_path)
    except (FileNotFoundError, ValueError) as exc:
        # Approvals sub-review already ran; preserve its result rather than
        # collapsing everything to _fail_early which would report FAIL for approvals.
        return {
            "checked_at_utc":                    checked_at,
            "review_result":                     "FAIL",
            "approvals_review_result":           approvals_result,
            "executor_readiness_review_result":  "FAIL",
            "symbol":                            symbol,
            "approved_max_notional":             approved_max_notional,
            "block_guard":                       None,
            "submit_order_called":               None,
            "live_submit_enabled":               False,
            "real_submit_implemented":           False,
            "final_blocker":                     "unknown",
            "violations":                        all_violations + [f"executor report: {exc}"],
        }

    e_result, e_violations = validate_readiness(executor_report)
    executor_result = e_result
    if e_violations:
        all_violations += [f"[executor] {v}" for v in e_violations]

    block_guard = str(executor_report.get("block_guard") or "").strip()
    submit_order_called = executor_report.get("submit_order_called")

    # --- Combined result ---
    overall = "PASS" if (approvals_result == "PASS" and executor_result == "PASS") else "FAIL"
    final_blocker = "config_safety" if overall == "PASS" else (block_guard or "unknown")

    return {
        "checked_at_utc":                    checked_at,
        "review_result":                     overall,
        "approvals_review_result":           approvals_result,
        "executor_readiness_review_result":  executor_result,
        "symbol":                            symbol,
        "approved_max_notional":             approved_max_notional,
        "block_guard":                       block_guard,
        "submit_order_called":               submit_order_called,
        "live_submit_enabled":               False,
        "real_submit_implemented":           False,
        "final_blocker":                     final_blocker,
        "violations":                        all_violations,
    }


def _fail_early(
    checked_at: str,
    reason: str,
    symbol: Any = None,
    approved_max_notional: Any = None,
) -> dict[str, Any]:
    return {
        "checked_at_utc":                    checked_at,
        "review_result":                     "FAIL",
        "approvals_review_result":           "FAIL",
        "executor_readiness_review_result":  "FAIL",
        "symbol":                            symbol,
        "approved_max_notional":             approved_max_notional,
        "block_guard":                       None,
        "submit_order_called":               None,
        "live_submit_enabled":               False,
        "real_submit_implemented":           False,
        "final_blocker":                     "unknown",
        "violations":                        [reason],
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_review(review: dict[str, Any]) -> None:
    print("\n=== Live V2 Final Readiness Review ===")
    print(f"  approvals_review_result           : {review['approvals_review_result']}")
    print(f"  executor_readiness_review_result  : {review['executor_readiness_review_result']}")
    print(f"  symbol                            : {review['symbol']}")
    print(f"  approved_max_notional             : {review['approved_max_notional']}")
    print(f"  block_guard                       : {review['block_guard']}")
    print(f"  submit_order_called               : {review['submit_order_called']}")
    print(f"  live_submit_enabled               : {review['live_submit_enabled']}")
    print(f"  real_submit_implemented           : {review['real_submit_implemented']}")
    print(f"  final_blocker                     : {review['final_blocker']}")
    print()
    if review["violations"]:
        for v in review["violations"]:
            print(f"  ! {v}")
        print()
    print(f"  review_result: {review['review_result']}")
    print("=" * 38)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_v2_final_readiness_review",
        description=(
            "Offline final readiness review for the v2 approval layer. "
            "Verifies both v2 approval artifacts pass validation and the "
            "executor is blocked only by config_safety. "
            "Writes a JSON summary artifact. "
            "No credentials required. Never calls Alpaca. Never submits orders."
        ),
    )
    parser.add_argument(
        "--v2-approvals-review", action="store_true", default=False,
        dest="v2_approvals_review",
        help=(
            "Include the v2 approvals sub-review. "
            "Requires --live-trading-approval and --live-order-submission-approval. "
            "Must be set for a PASS result."
        ),
    )
    parser.add_argument(
        "--live-trading-approval", dest="live_trading_approval", default=None,
        help="Path to live_trading_approval.json (required with --v2-approvals-review)",
    )
    parser.add_argument(
        "--live-order-submission-approval", dest="live_order_submission_approval", default=None,
        help="Path to live_order_submission_approval.json (required with --v2-approvals-review)",
    )
    parser.add_argument(
        "--executor-readiness-report", required=True, dest="executor_readiness_report",
        help="Path to live_submit_blocked_report.json",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write the live_v2_final_readiness_review.json artifact",
    )
    args = parser.parse_args(argv)

    if args.v2_approvals_review:
        if not args.live_trading_approval:
            parser.error("--live-trading-approval is required when --v2-approvals-review is set")
        if not args.live_order_submission_approval:
            parser.error(
                "--live-order-submission-approval is required when --v2-approvals-review is set"
            )

    ta_path = Path(args.live_trading_approval) if args.live_trading_approval else None
    sa_path = Path(args.live_order_submission_approval) if args.live_order_submission_approval else None
    executor_path = Path(args.executor_readiness_report)
    output_path = Path(args.output)

    review = run_review(
        ta_path=ta_path,
        sa_path=sa_path,
        executor_report_path=executor_path,
        run_approvals_review=args.v2_approvals_review,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(review, indent=2, default=str), encoding="utf-8")

    print_review(review)
    print(f"\n  output written: {output_path}")

    if review["review_result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
