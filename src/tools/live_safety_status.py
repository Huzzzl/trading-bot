"""
tools/live_safety_status.py
-----------------------------
Read-only live safety config baseline status CLI.

Usage::

    python -m src.tools.live_safety_status \\
        --config config/settings.paper.local.yaml

Goal
----
Print the current live safety config field values and evaluate whether
the configuration is in a safe baseline state before any live trading
design work begins.

Result
------
PASS — all safety locks are engaged:
  live_trading_enabled=false, live_kill_switch_enabled=true,
  live_submit_dry_run=true, live_require_human_confirm=true

WARN — live_trading_enabled=true but live_submit_dry_run is still true
  (dry-run gate still in place, but operator should be aware)

FAIL — live_trading_enabled=true and live_submit_dry_run=false
  (submit gate is open — not safe without the full live-trading safeguards)
  also FAIL if live_kill_switch_enabled=false or live_require_human_confirm=false

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes any file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from src.tools.paper_status import check_config


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def evaluate_safety(ex: Any) -> tuple[str, list[str]]:
    """Evaluate live safety config.  Returns (result, issues).

    result is "PASS", "WARN", or "FAIL".
    issues is a list of human-readable problem strings (empty on PASS).
    """
    issues: list[str] = []
    warnings: list[str] = []

    trading_enabled   = bool(getattr(ex, "live_trading_enabled",       False))
    kill_switch       = bool(getattr(ex, "live_kill_switch_enabled",    True))
    submit_dry_run    = bool(getattr(ex, "live_submit_dry_run",         True))
    human_confirm     = bool(getattr(ex, "live_require_human_confirm",  True))

    if not kill_switch:
        issues.append("live_kill_switch_enabled=false — kill switch must be enabled")

    if not human_confirm:
        issues.append("live_require_human_confirm=false — human confirmation must be required")

    if trading_enabled and not submit_dry_run:
        issues.append(
            "live_trading_enabled=true and live_submit_dry_run=false "
            "— submit gate is open without full live-trading safeguards"
        )

    if trading_enabled and submit_dry_run and not issues:
        warnings.append(
            "live_trading_enabled=true — dry-run gate still engaged, "
            "but live trading is enabled"
        )

    if issues:
        return "FAIL", issues
    if warnings:
        return "WARN", warnings
    return "PASS", []


def build_status(cfg: Any) -> dict[str, Any]:
    """Return a structured status dict for printing and testing."""
    ex = cfg.execution
    result, issues = evaluate_safety(ex)
    return {
        "live_trading_enabled":    bool(getattr(ex, "live_trading_enabled",       False)),
        "live_kill_switch_enabled": bool(getattr(ex, "live_kill_switch_enabled",   True)),
        "live_submit_dry_run":     bool(getattr(ex, "live_submit_dry_run",         True)),
        "live_require_human_confirm": bool(getattr(ex, "live_require_human_confirm", True)),
        "live_max_orders_per_day": int(getattr(ex, "live_max_orders_per_day",      1)),
        "live_max_notional_per_day": float(getattr(ex, "live_max_notional_per_day", 100.0)),
        "live_ledger_path":        str(getattr(ex, "live_ledger_path",
                                               "output/live_execution_ledger.csv")),
        "result":                  result,
        "issues":                  issues,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_status(status: dict[str, Any]) -> None:
    """Print the operator status summary."""
    print("\n=== Live Safety Status ===")
    print(f"  live_trading_enabled       : {status['live_trading_enabled']}")
    print(f"  live_kill_switch_enabled   : {status['live_kill_switch_enabled']}")
    print(f"  live_submit_dry_run        : {status['live_submit_dry_run']}")
    print(f"  live_require_human_confirm : {status['live_require_human_confirm']}")
    print(f"  live_max_orders_per_day    : {status['live_max_orders_per_day']}")
    print(f"  live_max_notional_per_day  : {status['live_max_notional_per_day']}")
    print(f"  live_ledger_path           : {status['live_ledger_path']}")
    print()
    if status["issues"]:
        for issue in status["issues"]:
            print(f"  {'!' if status['result'] == 'FAIL' else '~'} {issue}")
        print()
    print(f"  RESULT: {status['result']}")
    print("=" * 26)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_safety_status",
        description=(
            "Read-only live safety config baseline status. "
            "Checks that all live safety locks are engaged. "
            "No credentials required. Never calls Alpaca."
        ),
    )
    parser.add_argument("--config", required=True, help="Path to YAML config")
    args = parser.parse_args(argv)

    cfg_result, cfg = check_config(args.config)
    if cfg_result["status"] == "FAIL" or cfg is None:
        print(f"\n[FAIL] config: {cfg_result.get('detail', 'could not load config')}")
        sys.exit(1)

    status = build_status(cfg)
    print_status(status)

    if status["result"] == "FAIL":
        sys.exit(1)
    if status["result"] == "WARN":
        sys.exit(1)


if __name__ == "__main__":
    main()
