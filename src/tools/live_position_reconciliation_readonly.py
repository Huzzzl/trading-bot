"""
tools/live_position_reconciliation_readonly.py
-----------------------------------------------
Mock-only core for a future read-only live position / open-order
reconciliation tool.

Inspects whether a live SPY position and/or open SPY orders exist, using
an injected mock broker in tests.  The real Alpaca adapter is NOT implemented
in this PR.

**CLI always returns BLOCKED ("real broker adapter not implemented").**
**PASS is only reachable through an injected mock broker in unit tests.**
**No Alpaca SDK is imported.**
**No network requests are made.**
**No credentials are read on any code path.**
**No orders are submitted, sold, cancelled, or replaced.**
**No live ledger is written.**
**No config_safety is mutated.**
**No position decision is made — that remains a manual operator action.**

Usage (mock-only core — always BLOCKED from CLI)::

    python -m src.tools.live_position_reconciliation_readonly \\
        --credential-guard output/live_credential_presence_guard.json \\
        --operator-override output/live_operator_config_override_review.json \\
        --symbol SPY \\
        --output output/live_position_reconciliation_readonly.json

The real adapter (``AlpacaLivePositionBroker``) and the
``--allow-live-broker-api-readonly`` flag will be added in a future PR.
Until that PR merges, the CLI always returns BLOCKED with
``"real broker adapter not implemented"``.

Broker client interface
-----------------------
Any broker client injected into ``run_reconciliation()`` must implement::

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        ...  # Returns position data dict if position exists, else None

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        ...  # Returns list of open order dicts (may be empty)

The tool consumes only boolean presence from these calls:
``position_observed = (get_position(...) is not None)``
``open_order_observed = (len(get_open_orders(...)) > 0)``

No fill prices, quantities, account IDs, order IDs, or raw broker response
fields are included in the output.

Output invariants
-----------------
The following fields are always hardcoded regardless of broker response:

    broker_calls_readonly           : mirrors broker_calls_made
                                      (false when no broker call was made;
                                       true only after mock broker read-only
                                       calls succeed)
    broker_mutation_calls_made      : false
    credential_values_exposed       : false
    credentials_read                : false
    live_submit_enabled             : false
    submit_order_reachable          : false
    cancel_order_reachable          : false
    replace_order_reachable         : false
    broker_ids_redacted             : true
    account_identifiers_redacted    : true
    raw_broker_response_included    : false

What it never does
------------------
* Never calls submit_order, cancel_order, or replace_order.
* Never calls any POST/PATCH/DELETE broker endpoint.
* Never imports or uses the Alpaca SDK.
* Never imports requests, httpx, aiohttp, or urllib.request.
* Never reads ALPACA_LIVE_API_KEY, ALPACA_LIVE_SECRET_KEY, or any env var.
* Never writes or modifies a live ledger file.
* Never mutates config_safety or settings.yaml.
* Never makes any position management decision.
* Never raises — all errors are captured as violations → BLOCKED.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_REQUIRED_SYMBOL = "SPY"


# ---------------------------------------------------------------------------
# Broker client protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class PositionBrokerClient(Protocol):
    """Minimal read-only position broker interface.

    Implementations must only issue GET-equivalent requests and must not
    expose fill prices, quantities, account IDs, order IDs, or raw broker
    response details.
    """

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Return a position dict if a position exists, otherwise None.

        The returned dict must not contain account IDs, raw order IDs,
        fill prices, or buying-power data.
        """
        ...

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return a list of open order dicts (may be empty).

        The returned list must not contain account IDs, raw order IDs,
        fill prices, or buying-power data.
        """
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_artifact(
    path: Path, label: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Read and parse a JSON prerequisite artifact.

    Returns ``(data, None)`` on success or ``(None, violation)`` on error.
    """
    if not path.exists():
        return None, f"{label}: artifact not found at {path}"
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{label}: malformed JSON — {exc}"
    except OSError as exc:
        return None, f"{label}: could not read file — {exc}"
    if not isinstance(data, dict):
        return None, f"{label}: expected JSON object, got {type(data).__name__}"
    return data, None


def _make_result(
    *,
    checked_at: str,
    violations: list[str],
    broker_calls_made: bool,
    symbol: str,
    position_observed: bool | None,
    open_order_observed: bool | None,
) -> dict[str, Any]:
    result = "PASS" if not violations else "BLOCKED"
    return {
        "checked_at_utc":              checked_at,
        "result":                      result,
        "broker_calls_made":           broker_calls_made,
        "broker_calls_readonly":       broker_calls_made,
        "broker_mutation_calls_made":  False,
        "credential_values_exposed":   False,
        "credentials_read":            False,
        "live_submit_enabled":         False,
        "submit_order_reachable":      False,
        "cancel_order_reachable":      False,
        "replace_order_reachable":     False,
        "symbol":                      symbol,
        "position_observed":           position_observed,
        "open_order_observed":         open_order_observed,
        "broker_ids_redacted":         True,
        "account_identifiers_redacted": True,
        "raw_broker_response_included": False,
        "violations":                  violations,
        "blocker":                     violations[0] if violations else None,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def print_result(result: dict[str, Any]) -> None:
    print("\n=== Live Position Reconciliation (Read-Only) ===")
    print(f"  checked_at_utc                : {result['checked_at_utc']}")
    print(f"  result                        : {result['result']}")
    print(f"  broker_calls_made             : {result['broker_calls_made']}")
    print(f"  broker_calls_readonly         : {result['broker_calls_readonly']}")
    print(f"  broker_mutation_calls_made    : {result['broker_mutation_calls_made']}")
    print(f"  credential_values_exposed     : {result['credential_values_exposed']}")
    print(f"  credentials_read              : {result['credentials_read']}")
    print(f"  live_submit_enabled           : {result['live_submit_enabled']}")
    print(f"  submit_order_reachable        : {result['submit_order_reachable']}")
    print(f"  cancel_order_reachable        : {result['cancel_order_reachable']}")
    print(f"  replace_order_reachable       : {result['replace_order_reachable']}")
    print(f"  symbol                        : {result['symbol']}")
    print(f"  position_observed             : {result['position_observed']}")
    print(f"  open_order_observed           : {result['open_order_observed']}")
    print(f"  broker_ids_redacted           : {result['broker_ids_redacted']}")
    print(f"  account_identifiers_redacted  : {result['account_identifiers_redacted']}")
    print(f"  raw_broker_response_included  : {result['raw_broker_response_included']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    if result["blocker"]:
        print(f"  blocker: {result['blocker']}")
    print("=" * 50)


# ---------------------------------------------------------------------------
# Main reconciliation runner
# ---------------------------------------------------------------------------

def run_reconciliation(
    *,
    credential_guard_path: Path,
    operator_override_path: Path,
    symbol: str,
    broker: PositionBrokerClient | None = None,
) -> dict[str, Any]:
    """Run the read-only position reconciliation check.

    Never raises.  All errors are captured as violations → BLOCKED.

    No env vars are ever read.  No credentials are ever accessed.

    Gate order
    ----------
    1. ``credential_guard`` artifact must exist and have ``result="PASS"``
    2. ``operator_override`` artifact must exist and have ``result="PASS"``
    3. ``symbol`` must be exactly ``"SPY"``
    4. If any gate above fails → BLOCKED immediately, no broker calls
    5. ``broker`` must not be None (real adapter not yet implemented)
    6. Broker calls: ``get_position``, ``get_open_orders`` (each wrapped in
       try/except — exception detail is redacted)

    Parameters
    ----------
    broker:
        When ``None`` (CLI default), returns BLOCKED with
        ``"real broker adapter not implemented"``.
        When provided (test path), the mock broker is called and its
        results are used to set ``position_observed`` / ``open_order_observed``.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    broker_calls_made = False
    position_observed: bool | None = None
    open_order_observed: bool | None = None

    # ------------------------------------------------------------------
    # Gate 1 — credential guard artifact
    # No env var reads, no broker construction, no broker calls.
    # ------------------------------------------------------------------
    cg_data, cg_err = _read_artifact(credential_guard_path, "credential_guard")
    if cg_err:
        violations.append(cg_err)
    elif cg_data.get("result") != "PASS":  # type: ignore[union-attr]
        violations.append("credential_guard result must be PASS")

    # ------------------------------------------------------------------
    # Gate 2 — operator override artifact
    # ------------------------------------------------------------------
    oo_data, oo_err = _read_artifact(operator_override_path, "operator_override")
    if oo_err:
        violations.append(oo_err)
    elif oo_data.get("result") != "PASS":  # type: ignore[union-attr]
        violations.append("operator_override result must be PASS")

    # ------------------------------------------------------------------
    # Gate 3 — symbol validation (exact match only, no folding)
    # Raw invalid symbol is never echoed in output.
    # ------------------------------------------------------------------
    symbol_valid = symbol == _REQUIRED_SYMBOL
    if not symbol_valid:
        violations.append("symbol must be exactly 'SPY'")
    safe_symbol = _REQUIRED_SYMBOL if symbol_valid else "(invalid)"

    # ------------------------------------------------------------------
    # Fail fast on any artifact/symbol violation before reaching broker.
    # ------------------------------------------------------------------
    if violations:
        return _make_result(
            checked_at=checked_at,
            violations=violations,
            broker_calls_made=broker_calls_made,
            symbol=safe_symbol,
            position_observed=position_observed,
            open_order_observed=open_order_observed,
        )

    # ------------------------------------------------------------------
    # Gate 4 — broker must be injected (real adapter not implemented yet)
    # ------------------------------------------------------------------
    if broker is None:
        blocker = "real broker adapter not implemented"
        return _make_result(
            checked_at=checked_at,
            violations=[blocker],
            broker_calls_made=broker_calls_made,
            symbol=safe_symbol,
            position_observed=position_observed,
            open_order_observed=open_order_observed,
        )

    # ------------------------------------------------------------------
    # Broker calls — all wrapped in try/except; exception detail redacted.
    # ------------------------------------------------------------------
    broker_calls_made = True

    try:
        position = broker.get_position(_REQUIRED_SYMBOL)
        position_observed = position is not None
    except Exception:
        violations.append("position check: broker error — details redacted")
        return _make_result(
            checked_at=checked_at,
            violations=violations,
            broker_calls_made=broker_calls_made,
            symbol=safe_symbol,
            position_observed=None,
            open_order_observed=None,
        )

    try:
        open_orders = broker.get_open_orders(_REQUIRED_SYMBOL)
        open_order_observed = len(open_orders) > 0
    except Exception:
        violations.append("open orders check: broker error — details redacted")
        return _make_result(
            checked_at=checked_at,
            violations=violations,
            broker_calls_made=broker_calls_made,
            symbol=safe_symbol,
            position_observed=position_observed,
            open_order_observed=None,
        )

    return _make_result(
        checked_at=checked_at,
        violations=violations,
        broker_calls_made=broker_calls_made,
        symbol=symbol,
        position_observed=position_observed,
        open_order_observed=open_order_observed,
    )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_position_reconciliation_readonly",
        description=(
            "Read-only live position / open-order reconciliation (mock-only core). "
            "Real Alpaca adapter not yet implemented — CLI always returns BLOCKED. "
            "PASS is only reachable through an injected mock broker in unit tests. "
            "Never imports Alpaca SDK. Never reads credentials. Never trades."
        ),
    )
    parser.add_argument(
        "--credential-guard", required=True, dest="credential_guard",
        help="Path to live_credential_presence_guard.json",
    )
    parser.add_argument(
        "--operator-override", required=True, dest="operator_override",
        help="Path to live_operator_config_override_review.json",
    )
    parser.add_argument(
        "--symbol", required=True,
        help="Symbol to inspect (must be SPY)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write live_position_reconciliation_readonly.json",
    )
    args = parser.parse_args(argv)

    result = run_reconciliation(
        credential_guard_path=Path(args.credential_guard),
        operator_override_path=Path(args.operator_override),
        symbol=args.symbol,
        broker=None,
    )

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_result(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
