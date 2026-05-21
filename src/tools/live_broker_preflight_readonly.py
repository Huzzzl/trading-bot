"""
tools/live_broker_preflight_readonly.py
-----------------------------------------
Read-only live broker preflight framework.

Validates prerequisite artifacts and — when a broker client is provided —
performs a set of read-only checks against the live broker API before any
future single live order attempt.

**Real Alpaca live API access requires ``--allow-live-broker-api-readonly``.**
**Without this flag, the CLI returns BLOCKED and makes zero broker calls.**
**No real trading is approved by this tool.**
**submit_order is unreachable.**
**config_safety remains the final blocker.**

Usage (flag required for live API access)::

    python -m src.tools.live_broker_preflight_readonly \\
        --credential-guard output/live_credential_presence_guard.json \\
        --operator-override output/live_operator_config_override_review.json \\
        --symbol SPY \\
        --side buy \\
        --notional-cap 100.0 \\
        --output output/live_broker_preflight_readonly.json \\
        --allow-live-broker-api-readonly

Without ``--allow-live-broker-api-readonly`` the tool always returns BLOCKED
with ``"live broker API access not enabled"`` and makes zero broker calls.

With the flag, and after all prerequisite and parameter checks pass, the
``AlpacaLiveReadOnlyBroker`` adapter is constructed using credentials from
``ALPACA_LIVE_API_KEY`` and ``ALPACA_LIVE_SECRET_KEY`` environment variables.
If either variable is absent or empty, the tool returns BLOCKED without
contacting Alpaca.

Broker client interface
-----------------------
Any broker client injected into ``run_preflight()`` must implement::

    def read_only_get(self, path: str) -> dict[str, Any]: ...

The tool enforces an explicit endpoint allowlist before calling
``read_only_get``.  Only the following paths are permitted:

    /v2/account  (exact)
    /v2/clock    (exact)
    /v2/assets/  (prefix — any asset symbol)

Any path outside this list raises ``ValueError`` and produces BLOCKED.
Only GET-equivalent calls are made; no mutation methods exist.

Allowed checks (in order)
--------------------------
1. Account status — ``GET /v2/account``
   * ``status == "ACTIVE"`` required
   * ``buying_power >= notional_cap`` required
   * ``pattern_day_trader == true`` → BLOCKED
2. Market clock — ``GET /v2/clock``
   * ``is_open == true`` required
3. Asset metadata — ``GET /v2/assets/SPY``
   * ``tradable == true`` required
   * ``fractionable == true`` required (notional order path)

Fail-closed conditions
-----------------------
* ``live_credential_presence_guard.json`` missing or ``result != "PASS"``
* ``live_operator_config_override_review.json`` missing or ``result != "PASS"``
* Either prerequisite artifact malformed
* ``symbol`` is not ``"SPY"``
* ``side`` is not ``"buy"``
* ``notional_cap`` is missing, ≤ 0, or > 100.0
* ``--allow-live-broker-api-readonly`` not passed
* Credentials not set or empty
* ``account.status != "ACTIVE"``
* ``buying_power < notional_cap``
* ``pattern_day_trader == true``
* ``clock.is_open == false``
* ``asset.tradable == false``
* ``asset.fractionable == false``
* Any exception from the broker client (exception details are redacted from
  all output — raw exception text must not appear in violations, checks,
  blocker, stdout, or output JSON)

Output invariants
-----------------
The following fields are always hardcoded regardless of broker response:

    broker_mutation_calls_made  : false
    credential_values_exposed   : false
    live_submit_enabled         : false
    real_submit_implemented     : false
    submit_order_reachable      : false
    config_safety_still_blocks  : true
    broker_calls_readonly       : true

What it never does
------------------
* Never calls submit_order, cancel_order, or replace_order.
* Never calls any POST/PATCH/DELETE broker endpoint.
* Never calls any order-preview endpoint.
* Never calls the orders endpoint.
* Never writes or modifies live ledger files.
* Never enables live trading.
* Never removes the config_safety blocker.
* Never reads, stores, prints, or includes real credential values in output.
* Never retries automatically after any failure.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_REQUIRED_SYMBOL = "SPY"
_REQUIRED_SIDE = "buy"
_MAX_NOTIONAL_CAP = 100.0

_LIVE_API_KEY_ENV = "ALPACA_LIVE_API_KEY"
_LIVE_SECRET_KEY_ENV = "ALPACA_LIVE_SECRET_KEY"

_ALLOWED_ENDPOINT_PREFIXES: frozenset[str] = frozenset({
    "/v2/account",
    "/v2/clock",
    "/v2/assets/",
})

# Paths that must match exactly (no sub-paths permitted).
_EXACT_ALLOWED_PATHS: frozenset[str] = frozenset({
    "/v2/account",
    "/v2/clock",
})

# Paths that are allowed as a prefix (any sub-path is permitted).
_PREFIX_ALLOWED_PATHS: frozenset[str] = frozenset({
    "/v2/assets/",
})

_ENDPOINT_ALLOWLIST: list[str] = sorted(_ALLOWED_ENDPOINT_PREFIXES)


# ---------------------------------------------------------------------------
# Broker client protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class BrokerClient(Protocol):
    """Minimal read-only broker client interface.

    Implementations must only issue GET-equivalent requests.
    The tool enforces the endpoint allowlist before calling this method.
    """

    def read_only_get(self, path: str) -> dict[str, Any]:
        """Perform a read-only GET-equivalent request and return parsed JSON."""
        ...


# ---------------------------------------------------------------------------
# Real Alpaca read-only adapter
# ---------------------------------------------------------------------------

class AlpacaLiveReadOnlyBroker:
    """Read-only live broker adapter using alpaca-py TradingClient.

    Only constructed when ``--allow-live-broker-api-readonly`` is explicitly
    passed on the CLI and all prerequisite/parameter checks pass.

    Credential values are consumed during construction and never stored
    as attributes on this object. They are never printed, logged, or
    included in any output artifact.

    Only read-only SDK methods are called: ``get_account``, ``get_clock``,
    ``get_asset``. No write or mutation methods are invoked.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        _trading_client_cls: Any = None,
    ) -> None:
        if _trading_client_cls is None:
            from alpaca.trading.client import TradingClient as _trading_client_cls
        self._client = _trading_client_cls(
            api_key=api_key,
            secret_key=secret_key,
            paper=False,
            raw_data=False,
        )

    def read_only_get(self, path: str) -> dict[str, Any]:
        """Map an allowlisted path to the corresponding read-only SDK call."""
        if path == "/v2/account":
            return self._get_account()
        if path == "/v2/clock":
            return self._get_clock()
        if path.startswith("/v2/assets/"):
            symbol = path[len("/v2/assets/"):]
            return self._get_asset(symbol)
        raise ValueError(
            f"unsupported path for AlpacaLiveReadOnlyBroker: {path!r}"
        )

    def _get_account(self) -> dict[str, Any]:
        account = self._client.get_account()
        raw_status = account.status
        status_str = (
            raw_status.value
            if hasattr(raw_status, "value")
            else str(raw_status)
        )
        return {
            "status": status_str,
            "buying_power": str(account.buying_power) if account.buying_power is not None else "0",
            "pattern_day_trader": bool(account.pattern_day_trader),
        }

    def _get_clock(self) -> dict[str, Any]:
        clock = self._client.get_clock()
        return {"is_open": bool(clock.is_open)}

    def _get_asset(self, symbol: str) -> dict[str, Any]:
        asset = self._client.get_asset(symbol)
        return {
            "tradable": bool(asset.tradable),
            "fractionable": bool(asset.fractionable),
        }


# ---------------------------------------------------------------------------
# Credential / broker construction helper
# ---------------------------------------------------------------------------

def _build_alpaca_live_broker(
    *,
    _trading_client_cls: Any = None,
) -> tuple[AlpacaLiveReadOnlyBroker | None, str | None]:
    """Read credentials from env and construct the live read-only broker.

    Returns ``(broker, None)`` on success or ``(None, error_message)`` on
    failure.  The error message never includes actual credential values.
    """
    api_key = os.environ.get(_LIVE_API_KEY_ENV, "").strip()
    secret_key = os.environ.get(_LIVE_SECRET_KEY_ENV, "").strip()

    missing: list[str] = []
    if not api_key:
        missing.append(_LIVE_API_KEY_ENV)
    if not secret_key:
        missing.append(_LIVE_SECRET_KEY_ENV)

    if missing:
        return None, (
            f"required environment variable(s) not set or empty: "
            f"{', '.join(missing)} — no broker calls were made"
        )

    try:
        broker = AlpacaLiveReadOnlyBroker(
            api_key=api_key,
            secret_key=secret_key,
            _trading_client_cls=_trading_client_cls,
        )
        return broker, None
    except Exception:
        return None, (
            "failed to construct live broker adapter — details redacted"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_artifact(path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
    """Read and parse a JSON artifact.

    Returns
    -------
    (data, None)     on success
    (None, violation) on missing file, bad JSON, or non-dict
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


def _is_allowed_path(path: str) -> bool:
    """Return True when path is on the read-only endpoint allowlist.

    Exact paths (/v2/account, /v2/clock) must match exactly.
    Prefix paths (/v2/assets/) allow any sub-path.
    """
    if path in _EXACT_ALLOWED_PATHS:
        return True
    return any(path.startswith(p) for p in _PREFIX_ALLOWED_PATHS)


def _guarded_get(broker: BrokerClient, path: str) -> dict[str, Any]:
    """Call broker.read_only_get only if path is on the allowlist.

    Raises
    ------
    ValueError
        When the path is not on the allowlist.
    """
    if not _is_allowed_path(path):
        raise ValueError(
            f"endpoint {path!r} is not on the read-only allowlist "
            f"(allowed prefixes: {sorted(_ALLOWED_ENDPOINT_PREFIXES)})"
        )
    return broker.read_only_get(path)


def _make_check(name: str, result: str, detail: str) -> dict[str, Any]:
    return {"name": name, "result": result, "detail": detail}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_account(
    broker: BrokerClient,
    notional_cap: float,
    violations: list[str],
    checks: list[dict[str, Any]],
) -> bool:
    """Run account checks.  Returns True if all pass."""
    try:
        account = _guarded_get(broker, "/v2/account")
    except Exception:
        violation = "account check: broker error — details redacted"
        violations.append(violation)
        checks.append(_make_check("account", "BLOCKED", violation))
        return False

    all_pass = True

    status = str(account.get("status", "")).strip()
    if status != "ACTIVE":
        msg = f"account status={status!r} — must be 'ACTIVE'"
        violations.append(msg)
        checks.append(_make_check("account_status", "BLOCKED", msg))
        all_pass = False
    else:
        checks.append(_make_check("account_status", "PASS", "status=ACTIVE"))

    try:
        buying_power = float(account.get("buying_power", 0))
    except (TypeError, ValueError):
        buying_power = 0.0

    if buying_power < notional_cap:
        msg = (
            f"buying_power={buying_power} is less than "
            f"notional_cap={notional_cap}"
        )
        violations.append(msg)
        checks.append(_make_check("buying_power", "BLOCKED", msg))
        all_pass = False
    else:
        checks.append(_make_check(
            "buying_power", "PASS",
            f"buying_power={buying_power} >= notional_cap={notional_cap}",
        ))

    pdt = account.get("pattern_day_trader", False)
    if pdt is True or str(pdt).lower() in ("true", "1"):
        msg = "account is flagged as pattern_day_trader — BLOCKED"
        violations.append(msg)
        checks.append(_make_check("pattern_day_trader", "BLOCKED", msg))
        all_pass = False
    else:
        checks.append(_make_check("pattern_day_trader", "PASS", "pattern_day_trader not flagged"))

    return all_pass


def _check_clock(
    broker: BrokerClient,
    violations: list[str],
    checks: list[dict[str, Any]],
) -> bool:
    """Run market clock check.  Returns True if market is open."""
    try:
        clock = _guarded_get(broker, "/v2/clock")
    except Exception:
        violation = "clock check: broker error — details redacted"
        violations.append(violation)
        checks.append(_make_check("market_clock", "BLOCKED", violation))
        return False

    is_open = clock.get("is_open", False)
    if is_open is not True and str(is_open).lower() not in ("true", "1"):
        msg = "market is not open (is_open=false) — preflight requires open market"
        violations.append(msg)
        checks.append(_make_check("market_clock", "BLOCKED", msg))
        return False

    checks.append(_make_check("market_clock", "PASS", "market is open"))
    return True


def _check_asset(
    broker: BrokerClient,
    symbol: str,
    violations: list[str],
    checks: list[dict[str, Any]],
) -> bool:
    """Run asset metadata checks.  Returns True if all pass."""
    path = f"/v2/assets/{symbol}"
    try:
        asset = _guarded_get(broker, path)
    except Exception:
        violation = "asset check: broker error — details redacted"
        violations.append(violation)
        checks.append(_make_check("asset_metadata", "BLOCKED", violation))
        return False

    all_pass = True

    tradable = asset.get("tradable", False)
    if tradable is not True and str(tradable).lower() not in ("true", "1"):
        msg = f"asset {symbol} is not tradable"
        violations.append(msg)
        checks.append(_make_check("asset_tradable", "BLOCKED", msg))
        all_pass = False
    else:
        checks.append(_make_check("asset_tradable", "PASS", f"{symbol} is tradable"))

    fractionable = asset.get("fractionable", False)
    if fractionable is not True and str(fractionable).lower() not in ("true", "1"):
        msg = f"asset {symbol} is not fractionable — required for notional order path"
        violations.append(msg)
        checks.append(_make_check("asset_fractionable", "BLOCKED", msg))
        all_pass = False
    else:
        checks.append(_make_check("asset_fractionable", "PASS", f"{symbol} is fractionable"))

    return all_pass


# ---------------------------------------------------------------------------
# Main preflight runner
# ---------------------------------------------------------------------------

def run_preflight(
    *,
    credential_guard_path: Path,
    operator_override_path: Path,
    symbol: str,
    side: str,
    notional_cap: float,
    broker: BrokerClient | None = None,
    allow_live_readonly: bool = False,
) -> dict[str, Any]:
    """Run the full read-only broker preflight.

    Never raises.  All errors are captured as violations → BLOCKED.

    When ``broker`` is None and ``allow_live_readonly`` is False the tool
    returns BLOCKED with ``"live broker API access not enabled"``.

    When ``broker`` is None and ``allow_live_readonly`` is True the tool
    returns BLOCKED with ``"live broker credentials not available"``.

    Never reads, stores, or exposes credential values.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    checks: list[dict[str, Any]] = []
    broker_calls_made = False

    # ------------------------------------------------------------------
    # Step 1 — prerequisite artifacts (no broker call)
    # ------------------------------------------------------------------
    cg_data, cg_err = _read_artifact(credential_guard_path, "credential_guard")
    if cg_err:
        violations.append(cg_err)
    elif cg_data.get("result") != "PASS":
        violations.append(
            f"credential_guard result={cg_data.get('result')!r} — must be 'PASS'"
        )

    oo_data, oo_err = _read_artifact(operator_override_path, "operator_override")
    if oo_err:
        violations.append(oo_err)
    elif oo_data.get("result") != "PASS":
        violations.append(
            f"operator_override result={oo_data.get('result')!r} — must be 'PASS'"
        )

    # ------------------------------------------------------------------
    # Step 2 — parameter validation (no broker call)
    # ------------------------------------------------------------------
    if str(symbol).strip().upper() != _REQUIRED_SYMBOL:
        violations.append(
            f"symbol={symbol!r} — only {_REQUIRED_SYMBOL!r} is permitted"
        )

    if str(side).strip().lower() != _REQUIRED_SIDE:
        violations.append(
            f"side={side!r} — only {_REQUIRED_SIDE!r} is permitted"
        )

    try:
        notional_f = float(notional_cap)
    except (TypeError, ValueError):
        notional_f = 0.0
        violations.append(f"notional_cap={notional_cap!r} is not a valid number")

    if notional_f <= 0:
        violations.append(f"notional_cap={notional_f} must be > 0")
    elif notional_f > _MAX_NOTIONAL_CAP:
        violations.append(
            f"notional_cap={notional_f} exceeds maximum {_MAX_NOTIONAL_CAP}"
        )

    # ------------------------------------------------------------------
    # Step 3 — broker client availability
    # ------------------------------------------------------------------
    if broker is None:
        if not allow_live_readonly:
            violations.append(
                "live broker API access not enabled — "
                "pass --allow-live-broker-api-readonly to use the live "
                "read-only adapter; no broker calls will be made without "
                "this explicit opt-in flag"
            )
        else:
            violations.append(
                "live broker credentials not available — "
                f"{_LIVE_API_KEY_ENV} and {_LIVE_SECRET_KEY_ENV} must be "
                "set and non-empty; no broker calls were made"
            )

    # ------------------------------------------------------------------
    # Fail early if any prerequisite or parameter violation
    # ------------------------------------------------------------------
    if violations:
        return _build_result(
            checked_at=checked_at,
            violations=violations,
            checks=checks,
            broker_calls_made=broker_calls_made,
        )

    # ------------------------------------------------------------------
    # Step 4 — broker checks (real or mock)
    # ------------------------------------------------------------------
    broker_calls_made = True

    _check_account(broker, notional_f, violations, checks)  # type: ignore[arg-type]
    _check_clock(broker, violations, checks)
    _check_asset(broker, _REQUIRED_SYMBOL, violations, checks)

    return _build_result(
        checked_at=checked_at,
        violations=violations,
        checks=checks,
        broker_calls_made=broker_calls_made,
    )


def _build_result(
    *,
    checked_at: str,
    violations: list[str],
    checks: list[dict[str, Any]],
    broker_calls_made: bool,
) -> dict[str, Any]:
    result = "PASS" if not violations else "BLOCKED"
    return {
        "checked_at_utc":             checked_at,
        "result":                     result,
        "broker_calls_made":          broker_calls_made,
        "broker_calls_readonly":      True,
        "broker_mutation_calls_made": False,
        "credential_values_exposed":  False,
        "live_submit_enabled":        False,
        "real_submit_implemented":    False,
        "submit_order_reachable":     False,
        "config_safety_still_blocks": True,
        "endpoint_allowlist_used":    _ENDPOINT_ALLOWLIST,
        "checks":                     checks,
        "violations":                 violations,
        "blocker":                    violations[0] if violations else None,
    }


# ---------------------------------------------------------------------------
# Writer / printer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def print_preflight(result: dict[str, Any]) -> None:
    print("\n=== Live Broker Preflight (Read-Only) ===")
    print(f"  checked_at_utc             : {result['checked_at_utc']}")
    print(f"  result                     : {result['result']}")
    print(f"  broker_calls_made          : {result['broker_calls_made']}")
    print(f"  broker_calls_readonly      : {result['broker_calls_readonly']}")
    print(f"  broker_mutation_calls_made : {result['broker_mutation_calls_made']}")
    print(f"  credential_values_exposed  : {result['credential_values_exposed']}")
    print(f"  live_submit_enabled        : {result['live_submit_enabled']}")
    print(f"  real_submit_implemented    : {result['real_submit_implemented']}")
    print(f"  submit_order_reachable     : {result['submit_order_reachable']}")
    print(f"  config_safety_still_blocks : {result['config_safety_still_blocks']}")
    print(f"  endpoint_allowlist_used    : {result['endpoint_allowlist_used']}")
    for check in result["checks"]:
        print(f"    [{check['result']}] {check['name']}: {check['detail']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    if result["blocker"]:
        print(f"  blocker: {result['blocker']}")
    print("=" * 42)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_broker_preflight_readonly",
        description=(
            "Read-only live broker preflight. "
            "Requires --allow-live-broker-api-readonly to contact Alpaca. "
            "Without that flag, always returns BLOCKED with zero broker calls. "
            "Never calls submit_order. Never enables live trading. "
            "Never removes config_safety."
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
        help="Target symbol (must be SPY)",
    )
    parser.add_argument(
        "--side", required=True,
        help="Order side (must be buy)",
    )
    parser.add_argument(
        "--notional-cap", required=True, dest="notional_cap", type=float,
        help="Notional cap in USD (must be > 0 and <= 100.0)",
    )
    parser.add_argument(
        "--output", required=True, dest="output",
        help="Path to write live_broker_preflight_readonly.json",
    )
    parser.add_argument(
        "--allow-live-broker-api-readonly",
        action="store_true",
        dest="allow_live_broker_api_readonly",
        default=False,
        help=(
            "Explicitly opt in to contacting the Alpaca live API. "
            "Without this flag the tool returns BLOCKED with zero broker calls. "
            "Requires ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY env vars."
        ),
    )
    args = parser.parse_args(argv)

    broker: BrokerClient | None = None
    if args.allow_live_broker_api_readonly:
        broker, broker_err = _build_alpaca_live_broker()
        if broker_err:
            # broker remains None; run_preflight emits "credentials not available"
            pass

    result = run_preflight(
        credential_guard_path=Path(args.credential_guard),
        operator_override_path=Path(args.operator_override),
        symbol=args.symbol,
        side=args.side,
        notional_cap=args.notional_cap,
        broker=broker,
        allow_live_readonly=args.allow_live_broker_api_readonly,
    )

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_preflight(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
