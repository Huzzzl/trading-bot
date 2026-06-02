# ARCHIVED in PR R4g. Historical manual v2 readiness bundle tool. Not part of active automated runtime.
# Moved from src/tools/live_v2_readiness_bundle.py. Not importable as src.tools.live_v2_readiness_bundle.
"""
tools/live_v2_readiness_bundle.py
------------------------------------
Offline bundle runner that executes the full Live V2 readiness review
sequence in one command and writes all four review artifacts under a
single output directory.

Usage::

    python -m src.tools.live_v2_readiness_bundle \\
        --live-trading-approval output/live_trading_approval.json \\
        --live-order-submission-approval output/live_order_submission_approval.json \\
        --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \\
        --output-dir output/live_v2_bundle

Sequence
--------
1. ``live_v2_approvals_review``          -- validates both v2 approval artifacts
2. ``live_v2_executor_readiness_review`` -- verifies executor reached config_safety
3. ``live_v2_final_readiness_review``    -- combined summary

All three reviews run regardless of earlier failures, provided their input
files are accessible.  All available artifacts are written before exit.

Outputs
-------
Under ``--output-dir``:

    live_v2_approvals_review.json
    live_v2_executor_readiness_review.json
    live_v2_final_readiness_review.json
    live_v2_readiness_bundle.json          (top-level audit summary)

Also prints a human-readable summary to stdout.

Exit codes
----------
0 — final readiness review result is PASS
1 — any review fails, or any input file is missing/malformed

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

from src.tools.live_v2_approvals_review import (
    _read_json as _read_approval_json,
    build_review as build_approvals_review,
)
from src.tools.live_v2_executor_readiness_review import (
    parse_blocked_report,
    build_review as build_executor_review,
)
from src.tools.live_v2_final_readiness_review import run_review as run_final_review


# ---------------------------------------------------------------------------
# Per-step runners (each returns a dict; never raises)
# ---------------------------------------------------------------------------

def _step_approvals(ta_path: Path, sa_path: Path) -> dict[str, Any]:
    """Run the approvals sub-review; return a result dict on any error."""
    try:
        ta = _read_approval_json(ta_path)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "review_result": "FAIL",
            "violations": [f"trading approval: {exc}"],
            "trading_approved": None,
            "order_submission_approved": None,
            "symbol": None,
            "max_notional": None,
            "approval_scope": None,
        }
    try:
        sa = _read_approval_json(sa_path)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "review_result": "FAIL",
            "violations": [f"submission approval: {exc}"],
            "trading_approved": None,
            "order_submission_approved": None,
            "symbol": None,
            "max_notional": None,
            "approval_scope": None,
        }
    return build_approvals_review(ta, sa, ta_path, sa_path)


def _step_executor(exec_path: Path) -> dict[str, Any]:
    """Run the executor readiness sub-review; return a result dict on any error."""
    try:
        report = parse_blocked_report(exec_path)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "review_result": "FAIL",
            "blocked": None,
            "block_guard": None,
            "submit_order_called": None,
            "violations": [],
            "review_violations": [f"executor report: {exc}"],
        }
    return build_executor_review(report)


def _step_final(
    ta_path: Path,
    sa_path: Path,
    exec_path: Path,
) -> dict[str, Any]:
    """Run the final combined review; return a result dict on any error."""
    return run_final_review(
        ta_path=ta_path,
        sa_path=sa_path,
        executor_report_path=exec_path,
        run_approvals_review=True,
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Bundle runner
# ---------------------------------------------------------------------------

def run_bundle(
    *,
    ta_path: Path,
    sa_path: Path,
    exec_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run all three reviews, write artifacts, return bundle summary dict.

    Artifacts written under output_dir:
        live_v2_approvals_review.json
        live_v2_executor_readiness_review.json
        live_v2_final_readiness_review.json
        live_v2_readiness_bundle.json  (top-level audit summary)
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)

    approvals_out = output_dir / "live_v2_approvals_review.json"
    executor_out = output_dir / "live_v2_executor_readiness_review.json"
    final_out = output_dir / "live_v2_final_readiness_review.json"
    bundle_out = output_dir / "live_v2_readiness_bundle.json"

    # Step 1 — approvals review
    approvals_review = _step_approvals(ta_path, sa_path)
    _write_json(approvals_out, approvals_review)

    # Step 2 — executor readiness review
    executor_review = _step_executor(exec_path)
    _write_json(executor_out, executor_review)

    # Step 3 — final combined review (runs regardless of steps 1/2 outcome)
    final_review = _step_final(ta_path, sa_path, exec_path)
    _write_json(final_out, final_review)

    bundle_result = "PASS" if final_review.get("review_result") == "PASS" else "FAIL"

    summary = {
        "checked_at_utc":                 checked_at,
        "bundle_result":                  bundle_result,
        "approvals_review_path":          str(approvals_out),
        "executor_readiness_review_path": str(executor_out),
        "final_readiness_review_path":    str(final_out),
    }
    _write_json(bundle_out, summary)
    return summary


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def _result_tag(review: dict[str, Any], key: str = "review_result") -> str:
    r = str(review.get(key) or "FAIL").upper()
    return "PASS" if r == "PASS" else "FAIL"


def print_bundle(
    bundle: dict[str, Any],
    approvals_review: dict[str, Any],
    executor_review: dict[str, Any],
    final_review: dict[str, Any],
) -> None:
    print("\n=== Live V2 Readiness Bundle ===")
    print(f"  checked_at_utc : {bundle['checked_at_utc']}")
    print()
    print(f"  [1] approvals_review          : {_result_tag(approvals_review)}")
    print(f"      {bundle['approvals_review_path']}")
    print(f"  [2] executor_readiness_review : {_result_tag(executor_review)}")
    print(f"      {bundle['executor_readiness_review_path']}")
    print(f"  [3] final_readiness_review    : {_result_tag(final_review)}")
    print(f"      {bundle['final_readiness_review_path']}")
    print()
    print(f"  bundle_result: {bundle['bundle_result']}")
    print("=" * 32)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_v2_readiness_bundle",
        description=(
            "Offline Live V2 readiness bundle runner. "
            "Runs live_v2_approvals_review, live_v2_executor_readiness_review, "
            "and live_v2_final_readiness_review in sequence and writes all "
            "artifacts under --output-dir. "
            "No credentials required. Never calls Alpaca. Never submits orders."
        ),
    )
    parser.add_argument(
        "--live-trading-approval", required=True, dest="live_trading_approval",
        help="Path to live_trading_approval.json",
    )
    parser.add_argument(
        "--live-order-submission-approval", required=True,
        dest="live_order_submission_approval",
        help="Path to live_order_submission_approval.json",
    )
    parser.add_argument(
        "--executor-readiness-report", required=True, dest="executor_readiness_report",
        help="Path to live_submit_blocked_report.json",
    )
    parser.add_argument(
        "--output-dir", required=True, dest="output_dir",
        help="Directory to write all review artifacts",
    )
    args = parser.parse_args(argv)

    ta_path = Path(args.live_trading_approval)
    sa_path = Path(args.live_order_submission_approval)
    exec_path = Path(args.executor_readiness_report)
    output_dir = Path(args.output_dir)

    bundle = run_bundle(
        ta_path=ta_path,
        sa_path=sa_path,
        exec_path=exec_path,
        output_dir=output_dir,
    )

    # Read the written review files to pass their dicts to the printer.
    approvals_review = json.loads(
        Path(bundle["approvals_review_path"]).read_text(encoding="utf-8"))
    executor_review = json.loads(
        Path(bundle["executor_readiness_review_path"]).read_text(encoding="utf-8"))
    final_review = json.loads(
        Path(bundle["final_readiness_review_path"]).read_text(encoding="utf-8"))

    print_bundle(bundle, approvals_review, executor_review, final_review)

    if bundle["bundle_result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
