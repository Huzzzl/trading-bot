# ARCHIVED in PR R4. Historical manual/operator tool. Not part of active automated runtime.
# Moved from src/tools/live_single_submit_approval_review.py. Not importable as src.tools.live_single_submit_approval_review.
"""
tools/live_single_submit_approval_review.py
--------------------------------------------
Offline, read-only review of a local operator approval artifact for exactly
one future single live SPY market buy attempt.

Validates that the artifact is structurally present, correctly scoped, and
contains all required explicit acknowledgements before any future live submit
attempt.  This tool is a precondition check only — PASS does not submit an
order, does not remove ``config_safety``, and does not approve automated or
recurring trading.

Usage::

    python -m src.tools.live_single_submit_approval_review \\
        --approval-artifact output/live_single_submit_approval.json \\
        --output output/live_single_submit_approval_review.json

PASS conditions (all must hold)
---------------------------------
* Approval artifact file exists and is valid JSON dict.
* ``operator_name`` is a non-empty string.
* ``approval_note`` is a non-empty string.
* ``operator_initials`` is a non-empty string.
* ``symbol`` is exactly ``"SPY"`` (no case folding, no whitespace stripping).
* ``side`` is exactly ``"buy"`` (no case folding, no whitespace stripping).
* ``order_type`` is exactly ``"market"`` (no case folding, no whitespace stripping).
* ``notional_cap`` is a number in the range (0, 100.0] (inclusive).
* ``approval_scope`` == ``"AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"`` (exact).
* ``recurring_trading_approved`` is JSON boolean ``false`` exactly.
* ``automated_trading_approved`` is JSON boolean ``false`` exactly.
* ``one_attempt_only_acknowledged`` is JSON boolean ``true`` exactly.
* ``config_safety_override_is_local_only_acknowledged`` is JSON boolean ``true`` exactly.
* ``no_cancel_replace_acknowledged`` is JSON boolean ``true`` exactly.
* ``live_broker_preflight_pass_confirmed`` is JSON boolean ``true`` exactly.
* ``approved_at_utc`` is a non-empty string parseable as an ISO-8601 timestamp.
* ``approval_expires_at_utc`` is a non-empty string parseable as an ISO-8601
  timestamp and is strictly after ``approved_at_utc``.
* Current UTC time is before ``approval_expires_at_utc`` (not expired).

Output JSON
-----------
Always written to ``--output`` path:

    checked_at_utc                               : ISO-8601 UTC timestamp
    result                                       : "PASS" or "BLOCKED"
    approval_reviewed                            : true / false
    single_attempt_approved                      : true / false
    live_submit_enabled                          : false  (always)
    real_submit_implemented                      : false  (always)
    submit_order_reachable                       : false  (always)
    broker_calls_made                            : false  (always)
    credentials_read                             : false  (always)
    broker_mutation_calls_made                   : false  (always)
    live_ledger_written                          : false  (always)
    cancel_order_called                          : false  (always)
    replace_order_called                         : false  (always)
    automated_trading_enabled                    : false  (always)
    recurring_trading_enabled                    : false  (always)
    credential_values_exposed                    : false  (always)
    symbol                                       : "SPY" or null
    side                                         : "buy" or null
    order_type                                   : "market" or null
    notional_cap                                 : number or null
    approval_scope                               : string or null
    violations                                   : list of strings
    blocker                                      : first violation or null

Exit codes
----------
0 — PASS
1 — BLOCKED

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never imports the Alpaca SDK.
* Never imports requests, httpx, aiohttp, or urllib.request.
* Never reads credentials.
* Never calls submit_order, cancel_order, or replace_order.
* Never writes the live ledger.
* Never enables live trading.
* Never removes the config_safety blocker.
* Fails closed: any error (missing file, bad JSON, missing field) → BLOCKED.
* Never echoes raw malformed artifact content in output or stdout.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REQUIRED_SCOPE   = "AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"
_REQUIRED_SYMBOL  = "SPY"
_REQUIRED_SIDE    = "buy"
_REQUIRED_ORDER_TYPE = "market"
_MAX_NOTIONAL_CAP = 100.0


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def _read_artifact(path: Path) -> dict[str, Any]:
    """Read and parse the approval artifact JSON.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file cannot be parsed as JSON or is not a dict.
    """
    if not path.exists():
        raise FileNotFoundError(f"approval artifact not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read approval artifact {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Do not echo raw content — just report the structural failure.
        raise ValueError(f"malformed JSON in approval artifact: {path}")
    if not isinstance(data, dict):
        raise ValueError(
            f"approval artifact must be a JSON object (dict), "
            f"got {type(data).__name__}"
        )
    return data


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _parse_iso_utc(value: Any, field_name: str) -> tuple[datetime | None, str | None]:
    """Parse an ISO-8601 UTC timestamp field.

    Returns (datetime, None) on success, (None, error_string) on failure.
    Never echoes the raw value in the error string if it looks malformed.
    """
    if not isinstance(value, str) or not value.strip():
        return None, f"{field_name} must be a non-empty ISO-8601 UTC string"
    try:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt, None
    except ValueError:
        return None, f"{field_name} is not a valid ISO-8601 timestamp"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_approval(
    artifact: dict[str, Any],
    *,
    now_utc: datetime | None = None,
) -> tuple[str, list[str], bool, bool]:
    """Validate the operator approval artifact.

    Parameters
    ----------
    artifact:
        Parsed JSON dict from the approval file.
    now_utc:
        Current UTC time for expiry check.  Defaults to ``datetime.now(utc)``.

    Returns
    -------
    (result, violations, approval_reviewed, single_attempt_approved)
    """
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)

    violations: list[str] = []

    # --- Operator identity fields ---
    _op_name_raw = artifact.get("operator_name")
    operator_name = str(_op_name_raw).strip() if isinstance(_op_name_raw, str) else ""
    if not operator_name:
        violations.append("operator_name must be a non-empty string")

    _note_raw = artifact.get("approval_note")
    approval_note = str(_note_raw).strip() if isinstance(_note_raw, str) else ""
    if not approval_note:
        violations.append("approval_note must be a non-empty string")

    _initials_raw = artifact.get("operator_initials")
    operator_initials = str(_initials_raw).strip() if isinstance(_initials_raw, str) else ""
    if not operator_initials:
        violations.append("operator_initials must be a non-empty string")

    # --- Symbol (exact match — no case folding, no stripping) ---
    raw_symbol = artifact.get("symbol")
    if raw_symbol != _REQUIRED_SYMBOL:
        violations.append("symbol must be exactly 'SPY'")

    # --- Side (exact match) ---
    raw_side = artifact.get("side")
    if raw_side != _REQUIRED_SIDE:
        violations.append("side must be exactly 'buy'")

    # --- Order type (exact match) ---
    raw_order_type = artifact.get("order_type")
    if raw_order_type != _REQUIRED_ORDER_TYPE:
        violations.append("order_type must be exactly 'market'")

    # --- Notional cap ---
    notional_raw = artifact.get("notional_cap")
    notional_valid: float | None = None
    if notional_raw is None:
        violations.append("notional_cap is missing -- must be a number in (0, 100.0]")
    elif isinstance(notional_raw, bool):
        violations.append("notional_cap must be a number, not a boolean")
    elif isinstance(notional_raw, str):
        violations.append("notional_cap must be a number, not a string")
    else:
        try:
            notional_valid = float(notional_raw)
        except (TypeError, ValueError):
            violations.append(f"notional_cap is not a valid number")
            notional_valid = None
        if notional_valid is not None:
            if notional_valid <= 0:
                violations.append(f"notional_cap={notional_valid} must be > 0")
            elif notional_valid > _MAX_NOTIONAL_CAP:
                violations.append(
                    f"notional_cap={notional_valid} exceeds maximum of {_MAX_NOTIONAL_CAP}"
                )

    # --- Approval scope (exact match — do not echo raw value) ---
    scope = artifact.get("approval_scope")
    if scope != _REQUIRED_SCOPE:
        violations.append(
            f"approval_scope has invalid value -- must be "
            f"'{_REQUIRED_SCOPE}'"
        )

    # --- Strict boolean false fields (do not echo raw value) ---
    for field in ("recurring_trading_approved", "automated_trading_approved"):
        val = artifact.get(field)
        if val is not False:
            violations.append(
                f"{field} must be JSON boolean false "
                "(field must be present and explicitly false; "
                "no recurring or automated live trading is approved)"
            )

    # --- Strict boolean true acknowledgements (do not echo raw value) ---
    for field in (
        "one_attempt_only_acknowledged",
        "config_safety_override_is_local_only_acknowledged",
        "no_cancel_replace_acknowledged",
        "live_broker_preflight_pass_confirmed",
    ):
        val = artifact.get(field)
        if val is not True:
            violations.append(
                f"{field} must be JSON boolean true "
                "(operator must explicitly acknowledge this field)"
            )

    # --- Timestamps ---
    approved_at_raw = artifact.get("approved_at_utc")
    expires_at_raw  = artifact.get("approval_expires_at_utc")

    approved_dt, approved_err = _parse_iso_utc(approved_at_raw, "approved_at_utc")
    expires_dt,  expires_err  = _parse_iso_utc(expires_at_raw, "approval_expires_at_utc")

    if approved_err:
        violations.append(approved_err)
    if expires_err:
        violations.append(expires_err)

    if approved_dt is not None and expires_dt is not None:
        if expires_dt <= approved_dt:
            violations.append(
                "approval_expires_at_utc must be strictly after approved_at_utc"
            )
        elif now_utc >= expires_dt:
            violations.append(
                "approval artifact has expired (current UTC is after approval_expires_at_utc)"
            )

    result              = "PASS" if not violations else "BLOCKED"
    approval_reviewed   = len(violations) == 0
    single_attempt_approved = approval_reviewed

    return result, violations, approval_reviewed, single_attempt_approved


# ---------------------------------------------------------------------------
# Review runner
# ---------------------------------------------------------------------------

def run_review(
    *,
    approval_path: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Run the full approval review and return the result dict.  Never raises."""
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    approval_reviewed     = False
    single_attempt_approved = False
    result_str            = "BLOCKED"

    # Capture notional/scope/symbol/side/order_type from artifact for output
    out_symbol     : str | None = None
    out_side       : str | None = None
    out_order_type : str | None = None
    out_notional   : float | None = None
    out_scope      : str | None = None

    artifact: dict[str, Any] | None = None
    try:
        artifact = _read_artifact(approval_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(str(exc))

    if artifact is not None:
        # Extract display fields before validation.
        # Only populate when the raw value is exactly the required canonical value —
        # never echo raw operator-supplied values in the output artifact.
        if artifact.get("symbol") == _REQUIRED_SYMBOL:
            out_symbol = _REQUIRED_SYMBOL
        if artifact.get("side") == _REQUIRED_SIDE:
            out_side = _REQUIRED_SIDE
        if artifact.get("order_type") == _REQUIRED_ORDER_TYPE:
            out_order_type = _REQUIRED_ORDER_TYPE
        try:
            raw_notional = artifact.get("notional_cap")
            if raw_notional is not None and not isinstance(raw_notional, (bool, str)):
                candidate = float(raw_notional)
                if 0 < candidate <= _MAX_NOTIONAL_CAP:
                    out_notional = candidate
        except Exception:
            pass
        if artifact.get("approval_scope") == _REQUIRED_SCOPE:
            out_scope = _REQUIRED_SCOPE

        result_str, val_violations, approval_reviewed, single_attempt_approved = (
            validate_approval(artifact, now_utc=now_utc)
        )
        violations.extend(val_violations)
        if val_violations:
            result_str = "BLOCKED"

    blocker = violations[0] if violations else None

    return {
        "checked_at_utc":              checked_at,
        "result":                      result_str,
        "approval_reviewed":           approval_reviewed,
        "single_attempt_approved":     single_attempt_approved,
        # --- hardcoded invariants ---
        "live_submit_enabled":         False,
        "real_submit_implemented":     False,
        "submit_order_reachable":      False,
        "broker_calls_made":           False,
        "credentials_read":            False,
        "broker_mutation_calls_made":  False,
        "live_ledger_written":         False,
        "cancel_order_called":         False,
        "replace_order_called":        False,
        "automated_trading_enabled":   False,
        "recurring_trading_enabled":   False,
        "credential_values_exposed":   False,
        # --- artifact fields ---
        "symbol":                      out_symbol,
        "side":                        out_side,
        "order_type":                  out_order_type,
        "notional_cap":                out_notional,
        "approval_scope":              out_scope,
        # --- violations ---
        "violations":                  violations,
        "blocker":                     blocker,
    }


# ---------------------------------------------------------------------------
# Writer / printer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def print_review(result: dict[str, Any]) -> None:
    print("\n=== Live Single Submit Approval Review ===")
    print(f"  checked_at_utc              : {result['checked_at_utc']}")
    print(f"  result                      : {result['result']}")
    print(f"  approval_reviewed           : {result['approval_reviewed']}")
    print(f"  single_attempt_approved     : {result['single_attempt_approved']}")
    print(f"  symbol                      : {result['symbol']}")
    print(f"  side                        : {result['side']}")
    print(f"  order_type                  : {result['order_type']}")
    print(f"  notional_cap                : {result['notional_cap']}")
    print(f"  approval_scope              : {result['approval_scope']}")
    print(f"  live_submit_enabled         : {result['live_submit_enabled']}")
    print(f"  real_submit_implemented     : {result['real_submit_implemented']}")
    print(f"  submit_order_reachable      : {result['submit_order_reachable']}")
    print(f"  broker_calls_made           : {result['broker_calls_made']}")
    print(f"  credentials_read            : {result['credentials_read']}")
    print(f"  broker_mutation_calls_made  : {result['broker_mutation_calls_made']}")
    print(f"  live_ledger_written         : {result['live_ledger_written']}")
    print(f"  cancel_order_called         : {result['cancel_order_called']}")
    print(f"  replace_order_called        : {result['replace_order_called']}")
    print(f"  automated_trading_enabled   : {result['automated_trading_enabled']}")
    print(f"  recurring_trading_enabled   : {result['recurring_trading_enabled']}")
    print(f"  credential_values_exposed   : {result['credential_values_exposed']}")
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
        prog="python -m src.tools.live_single_submit_approval_review",
        description=(
            "Offline read-only review of a local operator approval artifact for "
            "exactly one future single live SPY market buy attempt. "
            "PASS does not submit an order, does not remove config_safety, "
            "and does not approve automated or recurring trading. "
            "Never calls Alpaca. Never reads credentials. Never writes the live ledger."
        ),
    )
    parser.add_argument(
        "--approval-artifact", required=True, dest="approval_artifact",
        help="Path to the operator approval artifact JSON",
    )
    parser.add_argument(
        "--output", required=True, dest="output",
        help="Path to write live_single_submit_approval_review.json",
    )
    args = parser.parse_args(argv)

    result = run_review(approval_path=Path(args.approval_artifact))

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_review(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
