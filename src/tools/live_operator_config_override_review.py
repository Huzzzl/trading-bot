"""
tools/live_operator_config_override_review.py
----------------------------------------------
Offline, read-only review of a local operator config override artifact.

Validates whether the artifact is structurally present and explicitly
acknowledges all required preconditions before any future live submit
attempt.  This tool is a precondition check only — it does not remove
the ``config_safety`` blocker, does not approve real trading, and does
not make the actual config changes itself.

Usage::

    python -m src.tools.live_operator_config_override_review \\
        --override-artifact output/live_operator_config_override.json \\
        --output output/live_operator_config_override_review.json

PASS conditions (all must hold)
---------------------------------
* Override artifact file exists and is valid JSON
* ``config_safety_acknowledged`` is ``true``
* ``submit_order_unreachable_acknowledged`` is ``true``
* ``real_live_submit_unimplemented_acknowledged`` is ``true``
* ``approval_scope`` == ``"AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"``
* ``symbol`` == ``"SPY"``
* ``side`` == ``"buy"``
* ``notional_cap`` is a number in the range (0, 100.0] (inclusive of 100.0)
* ``recurring_trading_approved`` is ``false`` (or absent, treated as false)
* ``automated_trading_approved`` is ``false`` (or absent, treated as false)
* ``operator_name`` is a non-empty string
* ``approval_note`` is a non-empty string

Output JSON
-----------
Written to ``--output`` path (required):

    checked_at_utc                             : ISO-8601 UTC timestamp
    result                                     : "PASS" or "BLOCKED"
    config_safety_override_reviewed            : true / false
    live_submit_enabled                        : false  (always)
    real_submit_implemented                    : false  (always)
    submit_order_reachable                     : false  (always)
    broker_calls_made                          : false  (always)
    credentials_read                           : false  (always)
    violations                                 : list of strings
    blocker                                    : first violation string, or null

Exit codes
----------
0 — PASS
1 — BLOCKED

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never modifies or writes live ledger files.
* Never enables live trading.
* Never removes the config_safety blocker.
* Never changes any config file — read-only inspection only.
* Fails closed: any error (missing file, bad JSON, missing field) → BLOCKED.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REQUIRED_SCOPE = "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
_REQUIRED_SYMBOL = "SPY"
_REQUIRED_SIDE = "buy"
_MAX_NOTIONAL_CAP = 100.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _is_falsy(value: Any) -> bool:
    """Return True when value is explicitly false/0/no or absent (None)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return not value
    return str(value).strip().lower() in ("false", "0", "no", "")


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def _read_artifact(path: Path) -> dict[str, Any]:
    """Read and parse the override artifact JSON.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file cannot be parsed as JSON or is not a dict.
    """
    if not path.exists():
        raise FileNotFoundError(f"override artifact not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read override artifact {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in override artifact {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"override artifact must be a JSON object (dict), got {type(data).__name__}"
        )
    return data


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_override(artifact: dict[str, Any]) -> tuple[str, list[str], bool]:
    """Validate the operator override artifact.

    Returns
    -------
    (result, violations, config_safety_reviewed)
        result                  : "PASS" or "BLOCKED"
        violations              : list of human-readable failure strings
        config_safety_reviewed  : True only when all three ack fields are true
    """
    violations: list[str] = []

    # --- Explicit acknowledgements ---
    ack_config_safety = _is_truthy(artifact.get("config_safety_acknowledged"))
    ack_unreachable   = _is_truthy(artifact.get("submit_order_unreachable_acknowledged"))
    ack_unimplemented = _is_truthy(artifact.get("real_live_submit_unimplemented_acknowledged"))

    if not ack_config_safety:
        violations.append(
            "config_safety_acknowledged must be true -- "
            "operator must explicitly acknowledge that config_safety is the current hard blocker"
        )
    if not ack_unreachable:
        violations.append(
            "submit_order_unreachable_acknowledged must be true -- "
            "operator must explicitly acknowledge that submit_order remains unreachable"
        )
    if not ack_unimplemented:
        violations.append(
            "real_live_submit_unimplemented_acknowledged must be true -- "
            "operator must explicitly acknowledge that real live submit is not implemented"
        )

    config_safety_reviewed = ack_config_safety and ack_unreachable and ack_unimplemented

    # --- Approval scope ---
    scope = str(artifact.get("approval_scope", "")).strip()
    if scope != _REQUIRED_SCOPE:
        violations.append(
            f"approval_scope={scope!r} -- must be {_REQUIRED_SCOPE!r}"
        )

    # --- Symbol ---
    symbol = str(artifact.get("symbol", "")).strip().upper()
    if symbol != _REQUIRED_SYMBOL:
        raw = artifact.get("symbol", "")
        violations.append(
            f"symbol={raw!r} -- only {_REQUIRED_SYMBOL!r} is permitted"
        )

    # --- Side ---
    side = str(artifact.get("side", "")).strip().lower()
    if side != _REQUIRED_SIDE:
        raw = artifact.get("side", "")
        violations.append(
            f"side={raw!r} -- only {_REQUIRED_SIDE!r} is permitted"
        )

    # --- Notional cap ---
    notional_raw = artifact.get("notional_cap")
    if notional_raw is None:
        violations.append("notional_cap is missing -- must be a number in (0, 100.0]")
    else:
        try:
            notional = float(notional_raw)
        except (TypeError, ValueError):
            violations.append(
                f"notional_cap={notional_raw!r} is not a valid number"
            )
            notional = None
        if notional is not None:
            if notional <= 0:
                violations.append(
                    f"notional_cap={notional} must be > 0"
                )
            elif notional > _MAX_NOTIONAL_CAP:
                violations.append(
                    f"notional_cap={notional} exceeds maximum permitted value of {_MAX_NOTIONAL_CAP}"
                )

    # --- No recurring or automated trading ---
    recurring = artifact.get("recurring_trading_approved")
    if not _is_falsy(recurring):
        violations.append(
            f"recurring_trading_approved={recurring!r} -- must be false; "
            "no recurring live trading is approved"
        )

    automated = artifact.get("automated_trading_approved")
    if not _is_falsy(automated):
        violations.append(
            f"automated_trading_approved={automated!r} -- must be false; "
            "no automated live trading is approved"
        )

    # --- Operator identity ---
    operator_name = str(artifact.get("operator_name", "")).strip()
    if not operator_name:
        violations.append("operator_name must be a non-empty string")

    approval_note = str(artifact.get("approval_note", "")).strip()
    if not approval_note:
        violations.append("approval_note must be a non-empty string")

    result = "PASS" if not violations else "BLOCKED"
    return result, violations, config_safety_reviewed


# ---------------------------------------------------------------------------
# Review runner
# ---------------------------------------------------------------------------

def run_review(*, override_path: Path) -> dict[str, Any]:
    """Run the full override review and return the result dict.  Never raises."""
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    config_safety_reviewed = False
    result_str = "BLOCKED"

    artifact: dict[str, Any] | None = None
    try:
        artifact = _read_artifact(override_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(str(exc))

    if artifact is not None:
        result_str, val_violations, config_safety_reviewed = validate_override(artifact)
        violations.extend(val_violations)
        if val_violations:
            result_str = "BLOCKED"

    blocker = violations[0] if violations else None

    return {
        "checked_at_utc":               checked_at,
        "result":                        result_str,
        "config_safety_override_reviewed": config_safety_reviewed,
        "live_submit_enabled":           False,
        "real_submit_implemented":       False,
        "submit_order_reachable":        False,
        "broker_calls_made":             False,
        "credentials_read":              False,
        "violations":                    violations,
        "blocker":                       blocker,
    }


# ---------------------------------------------------------------------------
# Writer / printer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def print_review(result: dict[str, Any]) -> None:
    print("\n=== Live Operator Config Override Review ===")
    print(f"  checked_at_utc                  : {result['checked_at_utc']}")
    print(f"  result                          : {result['result']}")
    print(f"  config_safety_override_reviewed : {result['config_safety_override_reviewed']}")
    print(f"  live_submit_enabled             : {result['live_submit_enabled']}")
    print(f"  real_submit_implemented         : {result['real_submit_implemented']}")
    print(f"  submit_order_reachable          : {result['submit_order_reachable']}")
    print(f"  broker_calls_made               : {result['broker_calls_made']}")
    print(f"  credentials_read                : {result['credentials_read']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    if result["blocker"]:
        print(f"  blocker: {result['blocker']}")
    print("=" * 44)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_operator_config_override_review",
        description=(
            "Offline read-only review of a local operator config override artifact. "
            "Validates that the artifact explicitly acknowledges all required "
            "preconditions for a future single live order attempt. "
            "PASS does not remove config_safety and does not approve real trading. "
            "Never calls Alpaca. Never reads credentials. Never writes the live ledger."
        ),
    )
    parser.add_argument(
        "--override-artifact", required=True, dest="override_artifact",
        help="Path to the local operator config override artifact JSON",
    )
    parser.add_argument(
        "--output", required=True, dest="output",
        help="Path to write live_operator_config_override_review.json",
    )
    args = parser.parse_args(argv)

    result = run_review(override_path=Path(args.override_artifact))

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_review(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
