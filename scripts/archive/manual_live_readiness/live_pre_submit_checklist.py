# ARCHIVED in PR R4e. Historical manual operator checklist. Not part of active automated runtime.
# Moved from src/tools/live_pre_submit_checklist.py. Not importable as src.tools.live_pre_submit_checklist.
"""
tools/live_pre_submit_checklist.py
------------------------------------
Unified live pre-submit operator checklist.

Usage::

    python -m src.tools.live_pre_submit_checklist \\
        --config config/settings.paper.local.yaml \\
        --symbol SPY \\
        --output-dir output/live_pre_submit_checklist

Goal
----
Run all existing live-readiness and dry-run audit checks in one place
and produce a final operator checklist before any future live submit design.

Checks (in order)
-----------------
1. live_safety_status   — config-only, no credentials required
2. live_readiness_gate  — requires ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY
3. live_dry_run_intents — requires ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY
4. live_dry_run_review  — reads artifacts written by step 3, no credentials
5. live_ledger_verify   — reads live ledger CSV (PASS if absent), no credentials

Final result
------------
READY     — all five checks PASS
NOT READY — any check is WARN or FAIL

Artifacts written (under --output-dir)
---------------------------------------
live_pre_submit_checklist.json          — this checklist report
live_readiness_gate/                    — gate sub-artifacts
live_dry_run_intents/                   — intent sub-artifacts

What it never does
------------------
* Never calls submit_order or cancel_order.
* Never writes a live ledger row.
* Never reads paper credentials (ALPACA_API_KEY / ALPACA_SECRET_KEY).
* Never modifies any position, order, or account state.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Individual check runners
# Separated into distinct functions so tests can mock each independently.
# ---------------------------------------------------------------------------

def _check_safety_status(config_path: str) -> str:
    """Return PASS/WARN/FAIL from live_safety_status."""
    from src.tools.live_safety_status import build_status
    from src.tools.paper_status import check_config

    cfg_result, cfg = check_config(config_path)
    if cfg_result["status"] == "FAIL" or cfg is None:
        return "FAIL"
    return build_status(cfg)["result"]


def _check_readiness_gate(config_path: str, symbol: str, output_dir: Path) -> str:
    """Return PASS/FAIL from live_readiness_gate (GO→PASS, NO-GO→FAIL)."""
    from src.tools.live_account_check import _make_live_client, check_credentials
    from src.tools.live_readiness_gate import (
        _stage_account_check,
        _stage_preflight,
        _stage_preflight_review,
        _stage_symbol_screen,
        _stage_symbol_screen_review,
        collect_top_blockers,
        compute_decision,
        write_gate_report,
    )
    from src.tools.paper_status import check_config

    cfg_result, cfg = check_config(config_path)
    if cfg_result["status"] == "FAIL" or cfg is None:
        return "FAIL"

    cred_result, creds = check_credentials()
    if cred_result["status"] == "FAIL" or creds is None:
        return "FAIL"

    try:
        client = _make_live_client(*creds)
    except Exception:
        return "FAIL"

    output_dir.mkdir(parents=True, exist_ok=True)
    checked_at_utc = datetime.now(timezone.utc).isoformat()

    r1 = _stage_account_check(client)
    r2 = _stage_preflight(cfg, client, symbol, output_dir, config_path)
    r3 = _stage_preflight_review(r2.get("json_path"), r2.get("csv_path"))
    r4 = _stage_symbol_screen(cfg, client, output_dir)
    r5 = _stage_symbol_screen_review(r4.get("json_path"), r4.get("csv_path"))

    stage_results = {
        "account_check":        r1,
        "shadow_preflight":     r2,
        "shadow_review":        r3,
        "symbol_screen":        r4,
        "symbol_screen_review": r5,
    }
    stages     = {k: v["status"] for k, v in stage_results.items()}
    decision   = compute_decision(stages)
    top_blockers = collect_top_blockers(stage_results)
    write_gate_report(output_dir, stages, decision, top_blockers, checked_at_utc)

    return "PASS" if decision == "GO" else "FAIL"


def _check_dry_run_intents(config_path: str, symbol: str, output_dir: Path) -> str:
    """Return PASS/FAIL from live_dry_run_intents (GO→PASS, NO-GO→FAIL)."""
    from src.tools.live_account_check import _make_live_client, check_credentials
    from src.tools.live_dry_run_intents import (
        _run_readiness_checks,
        _write_no_go_summary,
        build_intent_rows,
        build_summary,
        write_artifacts,
    )
    from src.tools.paper_status import check_config

    checked_at_utc = datetime.now(timezone.utc).isoformat()

    cfg_result, cfg = check_config(config_path)
    if cfg_result["status"] == "FAIL" or cfg is None:
        _write_no_go_summary(
            output_dir, checked_at_utc, symbol,
            [f"[config] {cfg_result.get('detail', 'load failed')}"],
        )
        return "FAIL"

    cred_result, creds = check_credentials()
    if cred_result["status"] == "FAIL" or creds is None:
        _write_no_go_summary(
            output_dir, checked_at_utc, symbol,
            [f"[credentials] {cred_result.get('detail', 'missing live credentials')}"],
        )
        return "FAIL"

    try:
        client = _make_live_client(*creds)
    except Exception as exc:
        _write_no_go_summary(output_dir, checked_at_utc, symbol, [f"[client] {exc}"])
        return "FAIL"

    decision, blockers, intents = _run_readiness_checks(cfg, client, symbol, checked_at_utc)

    if decision != "GO":
        _write_no_go_summary(output_dir, checked_at_utc, symbol, blockers)
        return "FAIL"

    intent_rows = build_intent_rows(intents, cfg, checked_at_utc, decision)
    summary     = build_summary(checked_at_utc, symbol, decision, blockers, intent_rows)
    write_artifacts(output_dir, intent_rows, summary)
    return "PASS"


def _check_dry_run_review(dry_run_dir: Path) -> str:
    """Return PASS/FAIL from live_dry_run_review artifacts."""
    from src.tools.live_dry_run_review import build_review, parse_intents, parse_summary

    summary_path = dry_run_dir / "live_dry_run_summary.json"
    intents_path = dry_run_dir / "live_order_intents.csv"

    try:
        summary = parse_summary(summary_path)
    except (FileNotFoundError, ValueError):
        return "FAIL"

    try:
        intents = parse_intents(intents_path)
    except (FileNotFoundError, ValueError):
        intents = []

    return build_review(summary, intents)["review_result"]


def _check_ledger_verify(ledger_path: str) -> str:
    """Return PASS/WARN/FAIL from live_ledger_verify."""
    from src.tools.live_ledger_verify import parse_ledger, validate_ledger

    rows, missing_msg = parse_ledger(Path(ledger_path))
    if rows is None:
        return "FAIL" if "could not read" in missing_msg else "PASS"
    result, _, _ = validate_ledger(rows)
    return result


def _get_ledger_path(config_path: str) -> str:
    """Resolve live ledger path from config, falling back to the default."""
    from src.tools.paper_status import check_config

    _, cfg = check_config(config_path)
    if cfg is not None:
        return str(getattr(cfg.execution, "live_ledger_path",
                           "output/live_execution_ledger.csv"))
    return "output/live_execution_ledger.csv"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_checklist(
    config_path: str,
    symbol: str,
    output_dir: Path,
    ledger_path: str | None = None,
) -> dict[str, Any]:
    """Run all five checks and return the structured checklist dict."""
    checked_at_utc  = datetime.now(timezone.utc).isoformat()
    gate_dir        = output_dir / "live_readiness_gate"
    intents_dir     = output_dir / "live_dry_run_intents"
    resolved_ledger = ledger_path or _get_ledger_path(config_path)

    checks: dict[str, str] = {
        "live_safety_status":   _check_safety_status(config_path),
        "live_readiness_gate":  _check_readiness_gate(config_path, symbol, gate_dir),
        "live_dry_run_intents": _check_dry_run_intents(config_path, symbol, intents_dir),
        "live_dry_run_review":  _check_dry_run_review(intents_dir),
        "live_ledger_verify":   _check_ledger_verify(resolved_ledger),
    }

    final_result = "READY" if all(v == "PASS" for v in checks.values()) else "NOT READY"

    return {
        "checked_at_utc": checked_at_utc,
        "symbol":         symbol,
        "checks":         checks,
        "final_result":   final_result,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_checklist(checklist: dict[str, Any]) -> None:
    """Print the operator checklist summary."""
    print("\n=== Live Pre-Submit Checklist ===")
    for name, status in checklist["checks"].items():
        print(f"  {name:<24}: {status}")
    print()
    print(f"  final_result: {checklist['final_result']}")
    print("=" * 34)


def write_checklist_report(output_dir: Path, checklist: dict[str, Any]) -> Path:
    """Write live_pre_submit_checklist.json to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "live_pre_submit_checklist.json"
    path.write_text(json.dumps(checklist, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_pre_submit_checklist",
        description=(
            "Unified live pre-submit operator checklist. "
            "Runs all live-readiness and dry-run audit checks. "
            "Never submits or cancels orders. Never writes a ledger."
        ),
    )
    parser.add_argument("--config",     required=True, help="Path to YAML config")
    parser.add_argument("--symbol",     required=True, help="Symbol to evaluate")
    parser.add_argument("--output-dir", required=True, help="Directory for output artifacts")
    parser.add_argument(
        "--ledger", default=None,
        help="Path to live ledger CSV (default: from config, or output/live_execution_ledger.csv)",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    symbol     = args.symbol.strip().upper()

    checklist   = run_checklist(args.config, symbol, output_dir, args.ledger)
    report_path = write_checklist_report(output_dir, checklist)
    print_checklist(checklist)
    print(f"\n  report written → {report_path}")

    if checklist["final_result"] != "READY":
        sys.exit(1)


if __name__ == "__main__":
    main()
