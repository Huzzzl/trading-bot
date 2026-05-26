"""
tools/live_position_reconciliation_readonly.py
-----------------------------------------------
Read-only live position / open-order reconciliation tool.

Real Alpaca adapter added.  CLI requires ``--allow-live-broker-api-readonly``
flag and valid ``ALPACA_LIVE_API_KEY`` / ``ALPACA_LIVE_SECRET_KEY`` env vars.
Without the flag the CLI returns BLOCKED ("readonly broker api flag not set").
PASS is reachable via the real adapter (CLI + flag + credentials) or an
injected mock broker (unit tests).

**No Alpaca SDK is imported at module level.**
**No network requests are made at import time.**
**No credentials are read until all gates pass and the flag is present.**
**No orders are submitted, sold, cancelled, or replaced.**
**No live ledger is written.**
**No config_safety is mutated.**
**No position decision is made — that remains a manual operator action.**

Usage::

    python -m src.tools.live_position_reconciliation_readonly \\
        --credential-guard output/live_credential_presence_guard.json \\
        --operator-override output/live_operator_config_override_review.json \\
        --symbol SPY \\
        --output output/live_position_reconciliation_readonly.json \\
        --allow-live-broker-api-readonly

Without ``--allow-live-broker-api-readonly`` the CLI always returns BLOCKED.
Credentials (``ALPACA_LIVE_API_KEY``, ``ALPACA_LIVE_SECRET_KEY``) are read
only after all gates pass and the flag is present.

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
                                       true only after read-only broker calls
                                       succeed)
    broker_mutation_calls_made      : false
    credential_values_exposed       : false
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
* Never imports Alpaca SDK at module level.
* Never imports requests, httpx, aiohttp, or urllib.request.
* Never reads ALPACA_LIVE_API_KEY or ALPACA_LIVE_SECRET_KEY before all gates
  pass and --allow-live-broker-api-readonly is present.
* Never writes or modifies a live ledger file.
* Never mutates config_safety or settings.yaml.
* Never makes any position management decision.
* Never raises — all errors are captured as violations → BLOCKED.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_REQUIRED_SYMBOL = "SPY"
_LIVE_API_KEY_ENV = "ALPACA_LIVE_API_KEY"
_LIVE_SECRET_KEY_ENV = "ALPACA_LIVE_SECRET_KEY"

_POSITION_NOT_FOUND_SIGNALS = ("404", "position does not exist", "no position")


def _is_position_not_found(exc: Exception) -> bool:
    """Return True if the exception signals that no position exists (not a real error)."""
    msg = str(exc).lower()
    return any(signal in msg for signal in _POSITION_NOT_FOUND_SIGNALS)


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
# Real Alpaca read-only adapter (lazy SDK import — never at module level)
# ---------------------------------------------------------------------------

class AlpacaLivePositionBroker:
    """Read-only Alpaca live position broker.

    Alpaca SDK is imported lazily inside ``__init__`` only — never at module
    level.  No submit_order, cancel_order, replace_order, close_position,
    close_all_positions, or any mutation method is present.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        _trading_client_cls: Any = None,
        _get_orders_request_cls: Any = None,
        _query_order_status_cls: Any = None,
    ) -> None:
        if _trading_client_cls is None:
            from alpaca.trading.client import TradingClient as _tc  # lazy import
            _trading_client_cls = _tc
        if _get_orders_request_cls is None:
            from alpaca.trading.requests import GetOrdersRequest as _gor  # lazy import
            _get_orders_request_cls = _gor
        if _query_order_status_cls is None:
            from alpaca.trading.enums import QueryOrderStatus as _qos  # lazy import
            _query_order_status_cls = _qos
        self._client = _trading_client_cls(
            api_key=api_key, secret_key=secret_key, paper=False
        )
        self._GetOrdersRequest = _get_orders_request_cls
        self._QueryOrderStatus = _query_order_status_cls

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Return minimal presence dict if position exists; None if no position.

        Alpaca raises a 404-like exception when no position exists — that is
        treated as ``None`` (position_observed=False), not as a broker error.
        Any other exception is re-raised for the caller to handle.
        """
        try:
            self._client.get_open_position(symbol)
            return {"position_exists": True}
        except Exception as exc:
            if _is_position_not_found(exc):
                return None
            raise

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return minimal presence list for open orders (no IDs or prices)."""
        req = self._GetOrdersRequest(
            status=self._QueryOrderStatus.OPEN, symbols=[symbol]
        )
        orders = self._client.get_orders(filter=req)
        return [{} for _ in orders]


def _build_alpaca_live_position_broker(
    violations: list[str],
    *,
    _trading_client_cls: Any = None,
    _get_orders_request_cls: Any = None,
    _query_order_status_cls: Any = None,
) -> tuple[AlpacaLivePositionBroker | None, bool]:
    """Read credentials from env and construct AlpacaLivePositionBroker.

    Returns ``(broker, credentials_read)``.  ``credentials_read`` is True
    whenever ``os.environ.get`` was called (even if credentials were absent
    or construction failed).  Exception details are always redacted.
    """
    api_key = os.environ.get(_LIVE_API_KEY_ENV, "").strip()
    secret_key = os.environ.get(_LIVE_SECRET_KEY_ENV, "").strip()
    missing = []
    if not api_key:
        missing.append(_LIVE_API_KEY_ENV)
    if not secret_key:
        missing.append(_LIVE_SECRET_KEY_ENV)
    if missing:
        violations.append("credentials not found in environment")
        return None, True
    try:
        broker = AlpacaLivePositionBroker(
            api_key=api_key,
            secret_key=secret_key,
            _trading_client_cls=_trading_client_cls,
            _get_orders_request_cls=_get_orders_request_cls,
            _query_order_status_cls=_query_order_status_cls,
        )
        return broker, True
    except Exception:
        violations.append("live broker construction failed (details redacted)")
        return None, True


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
    credentials_read: bool = False,
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
        "credentials_read":            credentials_read,
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
    allow_live_readonly: bool = False,
    _trading_client_cls: Any = None,
    _get_orders_request_cls: Any = None,
    _query_order_status_cls: Any = None,
) -> dict[str, Any]:
    """Run the read-only position reconciliation check.

    Never raises.  All errors are captured as violations → BLOCKED.

    No env vars are read until all artifact/symbol gates pass and
    ``allow_live_readonly`` is True (or an explicit broker is injected).

    Gate order
    ----------
    1. ``credential_guard`` artifact must exist and have ``result="PASS"``
    2. ``operator_override`` artifact must exist and have ``result="PASS"``
    3. ``symbol`` must be exactly ``"SPY"``
    4. If any gate above fails → BLOCKED immediately, no broker calls
    5. If ``broker is None`` and not ``allow_live_readonly`` → BLOCKED
       (``"readonly broker api flag not set"``)
    6. If ``broker is None`` → read credentials, construct
       ``AlpacaLivePositionBroker`` (credentials read ONLY here)
    7. Broker calls: ``get_position``, ``get_open_orders`` (each wrapped in
       try/except — exception detail is redacted)

    Parameters
    ----------
    broker:
        When ``None`` and ``allow_live_readonly`` is True, the real Alpaca
        adapter is constructed using credentials from the environment.
        When ``None`` and ``allow_live_readonly`` is False, returns BLOCKED.
        When provided (test path), the injected broker is used directly —
        no credentials are read.
    allow_live_readonly:
        Must be ``True`` to enable real broker API access.  Set by the CLI
        flag ``--allow-live-broker-api-readonly``.
    _trading_client_cls, _get_orders_request_cls, _query_order_status_cls:
        Injectable replacements for Alpaca SDK classes (unit tests only).
        ``_query_order_status_cls`` replaces ``QueryOrderStatus`` (not ``OrderStatus``).
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    broker_calls_made = False
    credentials_read = False
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
            credentials_read=credentials_read,
            symbol=safe_symbol,
            position_observed=position_observed,
            open_order_observed=open_order_observed,
        )

    # ------------------------------------------------------------------
    # Gate 4 — flag check (only when no broker is injected)
    # ------------------------------------------------------------------
    if broker is None and not allow_live_readonly:
        violations.append("readonly broker api flag not set")
        return _make_result(
            checked_at=checked_at,
            violations=violations,
            broker_calls_made=broker_calls_made,
            credentials_read=credentials_read,
            symbol=safe_symbol,
            position_observed=position_observed,
            open_order_observed=open_order_observed,
        )

    # ------------------------------------------------------------------
    # Gate 5 — build real adapter (credentials read ONLY here)
    # ------------------------------------------------------------------
    if broker is None:
        broker, credentials_read = _build_alpaca_live_position_broker(
            violations,
            _trading_client_cls=_trading_client_cls,
            _get_orders_request_cls=_get_orders_request_cls,
            _query_order_status_cls=_query_order_status_cls,
        )
        if violations:
            return _make_result(
                checked_at=checked_at,
                violations=violations,
                broker_calls_made=broker_calls_made,
                credentials_read=credentials_read,
                symbol=safe_symbol,
                position_observed=position_observed,
                open_order_observed=open_order_observed,
            )

    # ------------------------------------------------------------------
    # Broker calls — all wrapped in try/except; exception detail redacted.
    # ------------------------------------------------------------------
    broker_calls_made = True

    try:
        position = broker.get_position(_REQUIRED_SYMBOL)  # type: ignore[union-attr]
        position_observed = position is not None
    except Exception:
        violations.append("position check: broker error — details redacted")
        return _make_result(
            checked_at=checked_at,
            violations=violations,
            broker_calls_made=broker_calls_made,
            credentials_read=credentials_read,
            symbol=safe_symbol,
            position_observed=None,
            open_order_observed=None,
        )

    try:
        open_orders = broker.get_open_orders(_REQUIRED_SYMBOL)  # type: ignore[union-attr]
        open_order_observed = len(open_orders) > 0
    except Exception:
        violations.append("open orders check: broker error — details redacted")
        return _make_result(
            checked_at=checked_at,
            violations=violations,
            broker_calls_made=broker_calls_made,
            credentials_read=credentials_read,
            symbol=safe_symbol,
            position_observed=position_observed,
            open_order_observed=None,
        )

    return _make_result(
        checked_at=checked_at,
        violations=violations,
        broker_calls_made=broker_calls_made,
        credentials_read=credentials_read,
        symbol=safe_symbol,
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
            "Read-only live position / open-order reconciliation. "
            "Requires --allow-live-broker-api-readonly flag and valid credentials. "
            "Without the flag, CLI always returns BLOCKED. "
            "Never imports Alpaca SDK at module level. "
            "Never reads credentials without the flag. Never trades."
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
    parser.add_argument(
        "--allow-live-broker-api-readonly",
        action="store_true",
        dest="allow_live_readonly",
        default=False,
        help=(
            "Enable real Alpaca broker API read-only access. "
            "Without this flag, CLI always returns BLOCKED. "
            "Credentials are read from environment only after this flag is set "
            "and all prerequisite gates pass."
        ),
    )
    args = parser.parse_args(argv)

    result = run_reconciliation(
        credential_guard_path=Path(args.credential_guard),
        operator_override_path=Path(args.operator_override),
        symbol=args.symbol,
        broker=None,
        allow_live_readonly=args.allow_live_readonly,
    )

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_result(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
