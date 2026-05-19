"""
tools/live_shadow_review.py
-----------------------------
Read-only artifact review CLI for live shadow preflight outputs.

Usage::

    python -m src.tools.live_shadow_review \\
        --report  output/live_shadow_preflight/live_shadow_preflight_report.json \\
        --candidates output/live_shadow_preflight/live_shadow_candidates.csv

Goal
----
Read the JSON report and CSV candidates written by ``live_shadow_preflight
--write-report`` and produce a concise operator summary: final status,
blocking reasons, warnings, candidate sizing summary, and suggested next
actions.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes any file.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_report(path: Path) -> dict[str, Any]:
    """Read and return the JSON report.  Raises on missing file or bad JSON."""
    if not path.exists():
        raise FileNotFoundError(f"report not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def parse_candidates(path: Path) -> list[dict[str, str]]:
    """Read and return the candidates CSV as a list of row dicts.

    Raises on missing file or unreadable CSV.
    """
    if not path.exists():
        raise FileNotFoundError(f"candidates CSV not found: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        raise ValueError(f"could not read CSV {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def find_blockers(report: dict[str, Any], candidates: list[dict]) -> list[dict[str, Any]]:
    """Return a list of blocker dicts with a ``message`` and optional ``samples``.

    Candidate sizing failures are aggregated into a single compact entry with
    fail_count, min/max estimated_notional, and up to 3 sample rows.
    The raw ``live_sizing`` check detail is suppressed when candidate-level
    data is available (to avoid repeating the same information verbosely).
    """
    blockers: list[dict[str, Any]] = []

    checks_by_label = {c.get("label", ""): c for c in report.get("checks", [])}
    fail_rows = [r for r in candidates if r.get("sizing_status") == "FAIL"]

    # Check-level failures — skip live_sizing if we'll handle it via candidate aggregate
    skip_live_sizing = bool(fail_rows)
    for check in report.get("checks", []):
        if check.get("status") != "FAIL":
            continue
        label = check.get("label", "?")
        if label == "live_sizing" and skip_live_sizing:
            continue
        if label == "live_sizing":
            # Truncate at the " — " boundary so only the summary half is shown
            detail = check.get("detail", "")
            summary_part = detail.split(" — ")[0] if " — " in detail else detail
            msg = f"[live_sizing] {summary_part}" if summary_part else "[live_sizing] check failed"
        else:
            detail = check.get("detail", "")
            msg = f"[{label}] {detail}" if detail else f"[{label}] check failed"
        blockers.append({"message": msg, "samples": []})

    # Candidate sizing aggregate
    if fail_rows:
        try:
            notionals = [
                float(r["estimated_notional"])
                for r in fail_rows
                if r.get("estimated_notional") not in (None, "", "None")
            ]
        except (ValueError, TypeError):
            notionals = []

        live_max_notional = fail_rows[0].get("live_max_notional", "?")

        if notionals:
            msg = (
                f"[live_sizing] {len(fail_rows)} candidate(s) exceed "
                f"live_max_notional={live_max_notional}; "
                f"estimated_notional range={min(notionals):.2f}..{max(notionals):.2f}"
            )
        else:
            msg = (
                f"[live_sizing] {len(fail_rows)} candidate(s) failed sizing — "
                + "; ".join(r.get("sizing_reason", "?") for r in fail_rows[:3])
            )

        samples = [
            f"{r.get('client_order_id', '?')} {r.get('sizing_reason', '')}".strip()
            for r in fail_rows[:3]
        ]
        blockers.append({"message": msg, "samples": samples})

    return blockers


def find_warnings(report: dict[str, Any]) -> list[str]:
    """Return a list of human-readable warning strings."""
    warnings: list[str] = []
    for check in report.get("checks", []):
        if check.get("status") == "WARN":
            label  = check.get("label", "?")
            detail = check.get("detail", "")
            warnings.append(f"[{label}] {detail}" if detail else f"[{label}] warning")
    return warnings


def suggest_actions(
    report: dict[str, Any],
    candidates: list[dict],
    blockers: list[str],
    warnings: list[str],
) -> list[str]:
    """Generate a prioritised list of suggested next actions."""
    actions: list[str] = []

    checks_by_label: dict[str, dict] = {
        c.get("label", ""): c for c in report.get("checks", [])
    }

    # No candidates
    if report.get("candidate_count", 0) == 0:
        actions.append(
            "No buy+market candidates — wait for a strategy signal or rerun preview "
            "with an updated config/date range."
        )

    # Existing position
    if report.get("position_for_symbol"):
        symbol = report.get("selected_symbol", "the symbol")
        actions.append(
            f"Close existing live position in {symbol} manually before rerunning."
        )

    # Existing open order
    if report.get("open_orders_for_symbol"):
        symbol = report.get("selected_symbol", "the symbol")
        actions.append(
            f"Cancel existing live open order for {symbol} manually before rerunning."
        )

    # Sizing: notional cap exceeded
    sizing_check = checks_by_label.get("live_sizing", {})
    if sizing_check.get("status") == "FAIL":
        detail = sizing_check.get("detail", "")
        if "notional" in detail.lower():
            actions.append(
                "live_sizing notional cap exceeded — only increase live_max_notional "
                "after a full funding and risk review, or choose a lower-priced symbol."
            )
        elif "entry_price missing" in detail.lower():
            actions.append(
                "live_sizing failed: entry_price missing from candidate metadata. "
                "Ensure the strategy populates metadata['entry_price'] before sizing checks."
            )

    # Zero buying power / portfolio value
    live_acct = checks_by_label.get("live_account", {})
    bp  = report.get("buying_power")
    pv  = report.get("portfolio_value")
    zero_bp = _is_zero(bp)
    zero_pv = _is_zero(pv)
    if zero_bp or zero_pv:
        actions.append(
            "buying_power or portfolio_value is 0 — fund or activate the live account "
            "before any live submit work."
        )

    if not actions:
        if not blockers and not warnings:
            actions.append("All checks passed — review sizing_summary before any live work.")
        else:
            actions.append("Resolve all blockers listed above before proceeding.")

    return actions


def _is_zero(value: Any) -> bool:
    if value is None:
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Summary builder + printer
# ---------------------------------------------------------------------------

def build_summary(
    report: dict[str, Any],
    candidates: list[dict],
) -> dict[str, Any]:
    """Return a structured summary dict for printing and testing."""
    blockers = find_blockers(report, candidates)
    warnings = find_warnings(report)
    actions  = suggest_actions(report, candidates, blockers, warnings)

    pass_count = sum(1 for r in candidates if r.get("sizing_status") == "PASS")
    fail_count = sum(1 for r in candidates if r.get("sizing_status") == "FAIL")

    # Overall result: PASS only if report PASS and no candidate FAILs
    final_status = report.get("final_status", "UNKNOWN")
    if final_status == "PASS" and fail_count == 0:
        result = "PASS"
    elif final_status == "FAIL" or fail_count > 0:
        result = "FAIL"
    else:
        result = final_status  # WARN

    return {
        "final_status":    final_status,
        "result":          result,
        "selected_symbol": report.get("selected_symbol", "?"),
        "candidate_count": report.get("candidate_count", 0),
        "pass_count":      pass_count,
        "fail_count":      fail_count,
        "blockers":        blockers,
        "warnings":        warnings,
        "suggested_actions": actions,
    }


def print_summary(summary: dict[str, Any]) -> None:
    """Print the operator review summary."""
    print("\n=== Live Shadow Review ===")
    print(f"  final_status    : {summary['final_status']}")
    print(f"  selected_symbol : {summary['selected_symbol']}")
    print(f"  candidate_count : {summary['candidate_count']}")
    print(f"  pass_count      : {summary['pass_count']}")
    print(f"  fail_count      : {summary['fail_count']}")

    print()
    print("  Blockers:")
    if summary["blockers"]:
        for b in summary["blockers"]:
            print(f"    ! {b['message']}")
            if b.get("samples"):
                print("    ! sample failures:")
                for s in b["samples"]:
                    print(f"        {s}")
    else:
        print("    (none)")

    print()
    print("  Warnings:")
    if summary["warnings"]:
        for w in summary["warnings"]:
            print(f"    ~ {w}")
    else:
        print("    (none)")

    print()
    print("  Suggested actions:")
    for a in summary["suggested_actions"]:
        print(f"    > {a}")

    print()
    print(f"  RESULT: {summary['result']}")
    print("=" * 28)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_shadow_review",
        description=(
            "Read-only review of live shadow preflight artifacts. "
            "Summarises final status, blockers, warnings, and suggested actions. "
            "No credentials required. Never calls Alpaca."
        ),
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to live_shadow_preflight_report.json",
    )
    parser.add_argument(
        "--candidates",
        required=True,
        help="Path to live_shadow_candidates.csv",
    )
    args = parser.parse_args(argv)

    report_path    = Path(args.report)
    candidates_path = Path(args.candidates)

    try:
        report     = parse_report(report_path)
        candidates = parse_candidates(candidates_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[FAIL] {exc}")
        sys.exit(1)

    summary = build_summary(report, candidates)
    print_summary(summary)

    if summary["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
