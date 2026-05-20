"""
tools/live_submit_executor_check.py
--------------------------------------
CLI wrapper for the guarded live submit executor skeleton.

Invokes ``maybe_execute_live_submit()`` from
``src.execution.live_submit_executor`` and writes
``live_submit_blocked_report.json`` to ``--output-dir``.
With current config and approval artifacts the executor always returns
``blocked=true``.

Usage::

    python -m src.tools.live_submit_executor_check \\
        --config   config/settings.paper.local.yaml \\
        --symbol   SPY \\
        --confirm  "REAL-LIVE-SUBMIT-AUTHORIZED" \\
        --approval output/live_real_submit_pr_approval.json \\
        --pre-submit output/live_pre_submit_checklist/live_pre_submit_checklist.json \\
        --plan-review output/live_submit_dry_run/live_submit_plan_review.json \\
        --output-dir output/live_submit_executor

Optional::

    --plan output/live_submit_dry_run/live_submit_dry_run_plan.json

When ``--plan`` is omitted the tool uses ``config.live_max_order_notional``
as ``intended_notional`` and a stable dry-run sentinel as ``client_order_id``.

Exit codes
----------
0 — executor returned ``blocked=true`` and ``submit_order_called=false``;
    ``live_submit_blocked_report.json`` exists.
1 — any other outcome (unexpected ``blocked=false``, missing report, etc.).

What it never does
------------------
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes the live ledger.
* Never reads paper credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from src.config.loader import load_config
from src.execution.live_submit_executor import maybe_execute_live_submit

_FALLBACK_CLIENT_ORDER_ID = "DRY-RUN-EXECUTOR-CHECK-SENTINEL"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def _load_plan_fields(
    plan_path: Path | None,
    config_notional: float,
) -> tuple[float, float, str]:
    """Return (intended_notional, live_max_order_notional, client_order_id).

    Falls back to config values when plan_path is None or missing.
    """
    if plan_path is not None and plan_path.exists():
        try:
            plan = _read_json(plan_path)
            intended = float(plan.get("effective_notional") or config_notional)
            max_notional = float(plan.get("live_max_order_notional") or config_notional)
            coid = str(plan.get("client_order_id") or _FALLBACK_CLIENT_ORDER_ID).strip()
            if not coid:
                coid = _FALLBACK_CLIENT_ORDER_ID
            return intended, max_notional, coid
        except (ValueError, TypeError):
            pass
    return config_notional, config_notional, _FALLBACK_CLIENT_ORDER_ID


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_submit_executor_check",
        description=(
            "CLI wrapper for the guarded live submit executor skeleton. "
            "Calls maybe_execute_live_submit() and writes live_submit_blocked_report.json. "
            "Never calls submit_order. Never writes ledger."
        ),
    )
    parser.add_argument("--config", required=True, help="Path to settings YAML")
    parser.add_argument("--symbol", required=True, help="Target symbol (e.g. SPY)")
    parser.add_argument("--confirm", required=True, help="Confirmation token")
    parser.add_argument("--approval", required=True, help="Path to live_real_submit_pr_approval.json")
    parser.add_argument("--pre-submit", required=True, dest="pre_submit", help="Path to live_pre_submit_checklist.json")
    parser.add_argument("--plan-review", required=True, dest="plan_review", help="Path to live_submit_plan_review.json")
    parser.add_argument("--output-dir", required=True, dest="output_dir", help="Directory for live_submit_blocked_report.json")
    parser.add_argument("--plan", required=False, default=None, help="Optional path to live_submit_dry_run_plan.json")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
        ex = cfg.execution
    except Exception as exc:
        print(f"\n  [FAIL] config load error: {exc}")
        sys.exit(1)

    plan_path = Path(args.plan) if args.plan else None
    intended_notional, live_max_order_notional, client_order_id = _load_plan_fields(
        plan_path, ex.live_max_order_notional
    )

    output_dir = Path(args.output_dir)
    ledger_path = Path(ex.live_ledger_path)

    result = maybe_execute_live_submit(
        confirm_token=              args.confirm,
        approval_path=              Path(args.approval),
        checklist_path=             Path(args.pre_submit),
        review_path=                Path(args.plan_review),
        symbol=                     args.symbol,
        intended_notional=          intended_notional,
        client_order_id=            client_order_id,
        live_trading_enabled=       ex.live_trading_enabled,
        live_submit_dry_run=        ex.live_submit_dry_run,
        live_kill_switch_enabled=   ex.live_kill_switch_enabled,
        live_require_human_confirm= ex.live_require_human_confirm,
        live_max_order_notional=    live_max_order_notional,
        live_max_orders_per_day=    ex.live_max_orders_per_day,
        live_max_notional_per_day=  ex.live_max_notional_per_day,
        ledger_path=                ledger_path,
        output_dir=                 output_dir,
        broker_client=              None,
    )

    blocked = result.get("blocked", False)
    submit_order_called = result.get("submit_order_called", True)
    block_guard = result.get("block_guard", "")
    report_path = output_dir / "live_submit_blocked_report.json"

    print(f"\n  symbol              : {args.symbol}")
    print(f"  blocked             : {blocked}")
    print(f"  block_guard         : {block_guard}")
    print(f"  submit_order_called : {submit_order_called}")
    print(f"  report              : {report_path}")

    if blocked and not submit_order_called and report_path.exists():
        print("\n  [OK] executor blocked safely — blocked report written")
        sys.exit(0)
    else:
        print("\n  [FAIL] unexpected executor result")
        sys.exit(1)


if __name__ == "__main__":
    main()
