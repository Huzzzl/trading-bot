"""
tools/live_submit_enablement_gate.py
--------------------------------------
Offline GO/NO_GO gate checker.  Evaluates whether all conditions for
removing the final ``config_safety`` blocker are satisfied.  Read-only.
Never submits orders.

Usage::

    python -m src.tools.live_submit_enablement_gate \\
        --readiness-bundle output/live_v2_bundle/live_v2_readiness_bundle.json \\
        --live-trading-approval output/live_trading_approval.json \\
        --live-order-submission-approval output/live_order_submission_approval.json \\
        --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \\
        --output output/live_submit_enablement_gate.json

Decision
--------
GO   — all conditions satisfied; ``config_safety`` is the only remaining
       blocker.  GO does **not** submit an order.  The operator must still
       explicitly change the three config flags to enable live trading.
NO_GO — one or more conditions are not satisfied.

GO conditions (all must be true)
---------------------------------
* readiness bundle has ``bundle_result="PASS"``
* trading approval passes ``validate_approvals``
* submission approval passes ``validate_approvals``
* executor report has ``blocked=true``
* executor report has ``submit_order_called=false``
* executor report has ``block_guard="config_safety"``
* ``live_submit_enabled`` is not true in any of the input artifacts
* ``real_submit_implemented`` is not true in any of the input artifacts

Output JSON
-----------
Written to ``--output`` path (required):

    checked_at_utc
    decision                       : "GO" or "NO_GO"
    readiness_bundle_result        : "PASS", "FAIL", or "UNKNOWN"
    approvals_valid                : true / false
    executor_ready                 : true / false
    config_safety_is_final_blocker : true / false
    live_submit_enabled            : false  (always)
    real_submit_implemented        : false  (always)
    violations                     : list of strings

Exit codes
----------
0 — GO
1 — NO_GO

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes the live ledger.
* Never removes config_safety.
* Never changes config defaults.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.live_v2_approvals_review import (
    _read_json,
    validate_approvals,
)
from src.tools.live_v2_executor_readiness_review import (
    parse_blocked_report,
    validate_readiness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _check_safety_flags(artifact: dict[str, Any], label: str, violations: list[str]) -> None:
    """Fail if artifact claims live_submit_enabled or real_submit_implemented are true."""
    if _is_truthy(artifact.get("live_submit_enabled", False)):
        violations.append(
            f"live_submit_enabled=true found in {label} -- "
            "real submit must not be enabled"
        )
    if _is_truthy(artifact.get("real_submit_implemented", False)):
        violations.append(
            f"real_submit_implemented=true found in {label} -- "
            "real submit is not implemented and must not be flagged as implemented"
        )


# ---------------------------------------------------------------------------
# Gate runner
# ---------------------------------------------------------------------------

def run_gate(
    *,
    bundle_path: Path,
    ta_path: Path,
    sa_path: Path,
    exec_path: Path,
) -> dict[str, Any]:
    """Evaluate all GO/NO_GO conditions and return the gate result dict.

    Never raises.  All errors are captured as violations and produce NO_GO.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []

    bundle_result_str: str = "UNKNOWN"
    approvals_valid: bool = False
    executor_ready: bool = False
    config_safety_is_final_blocker: bool = False

    # ------------------------------------------------------------------
    # Step 1: readiness bundle
    # ------------------------------------------------------------------
    bundle: dict[str, Any] | None = None
    try:
        bundle = _read_json(bundle_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(f"readiness bundle: {exc}")

    if bundle is not None:
        bundle_result_str = str(bundle.get("bundle_result", "")).strip() or "UNKNOWN"
        if bundle_result_str != "PASS":
            violations.append(
                f"bundle_result={bundle_result_str!r} -- must be 'PASS'"
            )
        _check_safety_flags(bundle, "readiness bundle", violations)

    # ------------------------------------------------------------------
    # Step 2: approval artifacts
    # ------------------------------------------------------------------
    ta: dict[str, Any] | None = None
    sa: dict[str, Any] | None = None

    try:
        ta = _read_json(ta_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(f"trading approval: {exc}")

    try:
        sa = _read_json(sa_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(f"submission approval: {exc}")

    if ta is not None:
        _check_safety_flags(ta, "trading approval", violations)

    if sa is not None:
        _check_safety_flags(sa, "submission approval", violations)

    if ta is not None and sa is not None:
        a_result, a_violations = validate_approvals(ta, sa, ta_path, sa_path)
        approvals_valid = (a_result == "PASS")
        for v in a_violations:
            violations.append(f"[approvals] {v}")
    else:
        approvals_valid = False

    # ------------------------------------------------------------------
    # Step 3: executor blocked report
    # ------------------------------------------------------------------
    exec_report: dict[str, Any] | None = None
    try:
        exec_report = parse_blocked_report(exec_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(f"executor report: {exc}")

    if exec_report is not None:
        _check_safety_flags(exec_report, "executor report", violations)

        e_result, e_violations = validate_readiness(exec_report)
        executor_ready = (e_result == "PASS")
        for v in e_violations:
            violations.append(f"[executor] {v}")

        block_guard = str(exec_report.get("block_guard") or "").strip()
        config_safety_is_final_blocker = executor_ready and (block_guard == "config_safety")

    # ------------------------------------------------------------------
    # Decision — explicit AND of all core booleans plus empty violations.
    # Defense-in-depth: a validator that returns FAIL with an empty
    # violations list would still produce NO_GO here.
    # ------------------------------------------------------------------
    decision = (
        "GO"
        if (
            bundle_result_str == "PASS"
            and approvals_valid is True
            and executor_ready is True
            and config_safety_is_final_blocker is True
            and not violations
        )
        else "NO_GO"
    )

    return {
        "checked_at_utc":                checked_at,
        "decision":                      decision,
        "readiness_bundle_result":       bundle_result_str,
        "approvals_valid":               approvals_valid,
        "executor_ready":                executor_ready,
        "config_safety_is_final_blocker": config_safety_is_final_blocker,
        "live_submit_enabled":           False,
        "real_submit_implemented":       False,
        "violations":                    violations,
    }


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_gate(result: dict[str, Any]) -> None:
    print("\n=== Live Submit Enablement Gate ===")
    print(f"  checked_at_utc                : {result['checked_at_utc']}")
    print(f"  decision                      : {result['decision']}")
    print(f"  readiness_bundle_result       : {result['readiness_bundle_result']}")
    print(f"  approvals_valid               : {result['approvals_valid']}")
    print(f"  executor_ready                : {result['executor_ready']}")
    print(f"  config_safety_is_final_blocker: {result['config_safety_is_final_blocker']}")
    print(f"  live_submit_enabled           : {result['live_submit_enabled']}")
    print(f"  real_submit_implemented       : {result['real_submit_implemented']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    print("=" * 36)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_submit_enablement_gate",
        description=(
            "Offline Live Submit Enablement Gate checker. "
            "Returns GO only when all conditions for removing the config_safety "
            "blocker are satisfied. "
            "GO does not submit an order. "
            "Never calls Alpaca. Never reads credentials. Never writes the live ledger."
        ),
    )
    parser.add_argument(
        "--readiness-bundle", required=True, dest="readiness_bundle",
        help="Path to live_v2_readiness_bundle.json",
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
        "--output", required=True, dest="output",
        help="Path to write live_submit_enablement_gate.json",
    )
    args = parser.parse_args(argv)

    result = run_gate(
        bundle_path=Path(args.readiness_bundle),
        ta_path=Path(args.live_trading_approval),
        sa_path=Path(args.live_order_submission_approval),
        exec_path=Path(args.executor_readiness_report),
    )

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_gate(result)
    print(f"\n  Output written to: {output_path}")

    if result["decision"] != "GO":
        sys.exit(1)


if __name__ == "__main__":
    main()
