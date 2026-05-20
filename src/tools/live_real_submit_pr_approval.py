"""
tools/live_real_submit_pr_approval.py
---------------------------------------
Offline CLI that reads a live_operator_release_checklist.json artifact and
produces a human approval artifact authorising the opening of a separate
real-submit implementation PR.

Usage::

    python -m src.tools.live_real_submit_pr_approval \\
        --release-checklist output/live_operator_release_checklist.json \\
        --operator-name "Huzzzl" \\
        --approval-note "Approve opening real-submit implementation PR only; not approving live trading." \\
        --output output/live_real_submit_pr_approval.json

What the artifact approves
--------------------------
* approval_scope: "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY"
* approval_for_real_submit_pr: true

What the artifact explicitly does NOT approve
---------------------------------------------
* live_trading_approved: false
* live_order_submission_approved: false

This approval allows only opening a future implementation PR.
It does not authorise real live trading or live order submission.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes the live ledger.
* Never mutates the source release checklist file.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file contains malformed JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def _validate_release_checklist(checklist: dict[str, Any]) -> list[str]:
    """Return violations found in the release checklist artifact."""
    violations: list[str] = []
    result = str(checklist.get("release_result", "")).strip().upper()
    if result != "RELEASE_READY":
        violations.append(
            f"release_result={checklist.get('release_result')!r} — must be 'RELEASE_READY'"
        )
    return violations


def _validate_inputs(operator_name: str, approval_note: str) -> list[str]:
    """Return violations for CLI inputs."""
    violations: list[str] = []
    if not operator_name.strip():
        violations.append("--operator-name must not be empty")
    if not approval_note.strip():
        violations.append("--approval-note must not be empty")
    return violations


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_approval(
    checklist: dict[str, Any],
    checklist_path: Path,
    operator_name: str,
    approval_note: str,
) -> dict[str, Any]:
    """Produce the approval artifact dict."""
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "checked_at_utc":                  now,
        "source_release_checklist_path":   str(checklist_path),
        "release_result":                  checklist.get("release_result"),
        "operator_name":                   operator_name.strip(),
        "approval_timestamp_utc":          now,
        "approval_for_real_submit_pr":     True,
        "approval_scope":                  "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY",
        "live_trading_approved":           False,
        "live_order_submission_approved":  False,
        "approval_note":                   approval_note.strip(),
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
    print("\n=== Live Real-Submit PR Approval ===")
    print(f"  release_result                : {artifact['release_result']}")
    print(f"  operator_name                 : {artifact['operator_name']}")
    print(f"  approval_timestamp_utc        : {artifact['approval_timestamp_utc']}")
    print(f"  approval_for_real_submit_pr   : {artifact['approval_for_real_submit_pr']}")
    print(f"  approval_scope                : {artifact['approval_scope']}")
    print(f"  live_trading_approved         : {artifact['live_trading_approved']}")
    print(f"  live_order_submission_approved: {artifact['live_order_submission_approved']}")
    print(f"  approval_note                 : {artifact['approval_note']!r}")
    print()
    print("  > This approval authorises opening a real-submit implementation PR only.")
    print("  > It does NOT authorise live trading or live order submission.")
    print("=" * 40)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_real_submit_pr_approval",
        description=(
            "Offline approval artifact CLI: reads release checklist and produces "
            "a human approval for opening a real-submit implementation PR only. "
            "Does NOT approve live trading. No credentials. Never calls Alpaca."
        ),
    )
    parser.add_argument(
        "--release-checklist", required=True, dest="release_checklist",
        help="Path to live_operator_release_checklist.json",
    )
    parser.add_argument(
        "--operator-name", required=True, dest="operator_name",
        help="Name of the human operator approving the PR",
    )
    parser.add_argument(
        "--approval-note", required=True, dest="approval_note",
        help="Free-text note confirming scope of approval",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write live_real_submit_pr_approval.json",
    )
    args = parser.parse_args(argv)

    input_violations = _validate_inputs(args.operator_name, args.approval_note)
    if input_violations:
        for v in input_violations:
            print(f"\n  [FAIL] {v}")
        sys.exit(1)

    checklist_path = Path(args.release_checklist)
    try:
        checklist = _read_json(checklist_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  [FAIL] release checklist: {exc}")
        sys.exit(1)

    checklist_violations = _validate_release_checklist(checklist)
    if checklist_violations:
        for v in checklist_violations:
            print(f"\n  [FAIL] {v}")
        sys.exit(1)

    artifact = build_approval(checklist, checklist_path, args.operator_name, args.approval_note)
    print_approval(artifact)

    output_path = write_approval(Path(args.output), artifact)
    print(f"\n  artifact: {output_path}")


if __name__ == "__main__":
    main()
