"""
tools/live_single_manual_submit.py
------------------------------------
One-time manually approved single live SPY market buy attempt via the Alpaca SDK.

Without --allow-real-live-submit-once, CLI is always BLOCKED -- real live submit
is not activated.  With the flag, credentials are read from environment variables
and an AlpacaLiveSubmitBroker is constructed only after ALL safety gates pass.

Usage (without flag -- always BLOCKED)::

    python -m src.tools.live_single_manual_submit \\
        --credential-guard output/live_credential_presence_guard.json \\
        --operator-override output/live_operator_config_override_review.json \\
        --broker-preflight output/live_broker_preflight_readonly.json \\
        --submit-approval output/live_single_submit_approval_review.json \\
        --local-operator-config path/to/local_operator_config.yaml \\
        --symbol SPY \\
        --side buy \\
        --order-type market \\
        --notional-cap 100.0 \\
        --ledger output/live_ledger.csv \\
        --output output/single_manual_live_submit_attempt.json

Usage (real submit -- one attempt only, credentials required in env)::

    ALPACA_LIVE_API_KEY=<key> ALPACA_LIVE_SECRET_KEY=<secret> \\
    python -m src.tools.live_single_manual_submit \\
        --credential-guard output/live_credential_presence_guard.json \\
        --operator-override output/live_operator_config_override_review.json \\
        --broker-preflight output/live_broker_preflight_readonly.json \\
        --submit-approval output/live_single_submit_approval_review.json \\
        --local-operator-config path/to/local_operator_config.yaml \\
        --symbol SPY \\
        --side buy \\
        --order-type market \\
        --notional-cap 100.0 \\
        --ledger output/live_ledger.csv \\
        --output output/single_manual_live_submit_attempt.json \\
        --allow-real-live-submit-once

Fail-closed design
------------------
Every gate failure returns result="BLOCKED" with no broker calls and no ledger
writes.  Credentials and TradingClient are only constructed after ALL gates pass
and --allow-real-live-submit-once is present.  Without the flag, broker=None is
passed and the run always exits BLOCKED
("real live submit adapter not implemented").

Required gates (all must pass before any broker call)
------------------------------------------------------
1. Four prerequisite artifacts must exist with result="PASS":
   - live_credential_presence_guard
   - live_operator_config_override_review
   - live_broker_preflight_readonly
   - live_single_submit_approval_review
2. symbol must be exactly "SPY" (no case folding, no stripping).
3. side must be exactly "buy" (no case folding, no stripping).
4. order_type must be exactly "market" (no case folding, no stripping).
5. notional_cap must be a number in (0, 100.0] -- not a bool, not a string.
6. Local operator YAML config must have:
   - live_trading_enabled: true  (strict boolean)
   - live_submit_dry_run: false  (strict boolean)
   - live_kill_switch_enabled: false  (strict boolean)
7. --allow-real-live-submit-once flag must be set; credentials must be present
   in ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY environment variables.

Output fields
-------------
    checked_at_utc                                    : ISO-8601 UTC
    result                                            : "SUBMITTED" or "BLOCKED"
    order_submitted                                   : bool
    broker_mutation_calls_made                        : bool
    submit_order_called                               : bool
    cancel_order_called                               : false  (always)
    replace_order_called                              : false  (always)
    live_submit_enabled                               : bool
    automated_trading_enabled                         : false  (always)
    recurring_trading_enabled                         : false  (always)
    config_safety_overridden_by_local_operator_config : bool
    symbol                                            : "SPY" or null
    side                                              : "buy" or null
    order_type                                        : "market" or null
    notional_cap                                      : number or null
    client_order_id                                   : string or null
    submitted_at_utc                                  : string or null
    broker_order_id_redacted                          : "<redacted>" or null
    credential_values_exposed                         : false  (always)
    live_ledger_written                               : bool
    violations                                        : list of strings
    blocker                                           : string or null

State table
-----------
    BLOCKED (pre-broker gates) : order_submitted=F, submit_order_called=F, broker_mutation_calls_made=F
    BLOCKED (broker=None)      : order_submitted=F, submit_order_called=F, broker_mutation_calls_made=F
    SUBMITTED                  : order_submitted=T, submit_order_called=T, broker_mutation_calls_made=T
    BLOCKED (exception)        : order_submitted=F, submit_order_called=T, broker_mutation_calls_made=F

What it never does
------------------
* Never calls cancel_order or replace_order.
* Never retries a failed submit.
* Never enables automated or recurring trading.
* Never mutates settings.yaml.
* Never bypasses config_safety.
* Never echoes raw invalid field values in output or stdout.
* Never exposes credentials, account IDs, or broker exception text in output.
* Imports the Alpaca SDK lazily (inside AlpacaLiveSubmitBroker.__init__ only --
  never at module level).
* Reads credentials from environment only after all gates pass and
  --allow-real-live-submit-once is present.
* run_submit never raises.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUIRED_SYMBOL     = "SPY"
_REQUIRED_SIDE       = "buy"
_REQUIRED_ORDER_TYPE = "market"
_MAX_NOTIONAL_CAP    = 100.0

_LIVE_API_KEY_ENV    = "ALPACA_LIVE_API_KEY"
_LIVE_SECRET_KEY_ENV = "ALPACA_LIVE_SECRET_KEY"


# ---------------------------------------------------------------------------
# Ledger schema
# ---------------------------------------------------------------------------

LEDGER_COLUMNS: list[str] = [
    "timestamp_utc",
    "client_order_id",
    "symbol",
    "side",
    "order_type",
    "notional_cap",
    "status",
    "broker_order_id",
    "error",
]


# ---------------------------------------------------------------------------
# Real Alpaca live submit broker
# ---------------------------------------------------------------------------

class AlpacaLiveSubmitBroker:
    """Real Alpaca live submit broker with lazy SDK import.

    All SDK imports are deferred to __init__ to allow test injection without
    importing alpaca at module level.  Credentials are passed in; never read
    here.  Only submit_order is implemented -- cancel_order and replace_order
    are intentionally absent.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        _trading_client_cls: Any = None,
        _market_order_request_cls: Any = None,
        _order_side_cls: Any = None,
        _time_in_force_cls: Any = None,
    ) -> None:
        if _trading_client_cls is None:
            from alpaca.trading.client import TradingClient as _tc
            _trading_client_cls = _tc
        if _market_order_request_cls is None:
            from alpaca.trading.requests import MarketOrderRequest as _mor
            _market_order_request_cls = _mor
        if _order_side_cls is None:
            from alpaca.trading.enums import OrderSide as _os
            _order_side_cls = _os
        if _time_in_force_cls is None:
            from alpaca.trading.enums import TimeInForce as _tif
            _time_in_force_cls = _tif

        self._client              = _trading_client_cls(
            api_key=api_key, secret_key=secret_key, paper=False
        )
        self._MarketOrderRequest  = _market_order_request_cls
        self._OrderSide           = _order_side_cls
        self._TimeInForce         = _time_in_force_cls

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        notional: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        req_kwargs: dict[str, Any] = {
            "symbol":        symbol,
            "notional":      notional,
            "side":          self._OrderSide.BUY,
            "time_in_force": self._TimeInForce.DAY,
        }
        if client_order_id is not None:
            req_kwargs["client_order_id"] = client_order_id
        req   = self._MarketOrderRequest(**req_kwargs)
        _order = self._client.submit_order(order_data=req)
        return {"id": "<redacted>"}


# ---------------------------------------------------------------------------
# Broker factory
# ---------------------------------------------------------------------------

def _build_live_submit_broker(
    violations: list[str],
    *,
    _trading_client_cls: Any = None,
    _market_order_request_cls: Any = None,
    _order_side_cls: Any = None,
    _time_in_force_cls: Any = None,
) -> Any:
    """Read credentials from environment and construct AlpacaLiveSubmitBroker.

    Returns None and appends a violation if any credential is missing or blank.
    Credentials are read here -- never before all gates pass.
    """
    api_key    = os.environ.get(_LIVE_API_KEY_ENV, "").strip()
    secret_key = os.environ.get(_LIVE_SECRET_KEY_ENV, "").strip()
    if not api_key or not secret_key:
        violations.append("credentials not found in environment")
        return None
    return AlpacaLiveSubmitBroker(
        api_key=api_key,
        secret_key=secret_key,
        _trading_client_cls=_trading_client_cls,
        _market_order_request_cls=_market_order_request_cls,
        _order_side_cls=_order_side_cls,
        _time_in_force_cls=_time_in_force_cls,
    )


# ---------------------------------------------------------------------------
# Prerequisite artifact reader
# ---------------------------------------------------------------------------

def _read_prereq_artifact(
    path: Path,
    label: str,
    violations: list[str],
) -> None:
    """Read a prerequisite artifact JSON and require result="PASS".

    Appends a violation string if the file is missing, malformed, or non-PASS.
    Never raises.
    """
    try:
        if not path.exists():
            violations.append(f"{label}: artifact not found")
            return
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            violations.append(f"{label}: artifact must be a JSON dict")
            return
        if data.get("result") != "PASS":
            violations.append(f"{label}: result is not PASS")
    except json.JSONDecodeError:
        violations.append(f"{label}: malformed JSON")
    except OSError:
        violations.append(f"{label}: could not read artifact")


# ---------------------------------------------------------------------------
# Local operator config loader
# ---------------------------------------------------------------------------

def _load_local_config(
    path: Path,
    violations: list[str],
) -> tuple[bool, bool, bool]:
    """Load the local operator YAML config and validate the three safety flags.

    Returns (trading_enabled_ok, dry_run_ok, kill_switch_ok).
    Each bool is True only if the corresponding flag has the exact required value.
    Appends violation strings for any missing or wrong flag.  Never raises.
    """
    ok_trading = False
    ok_dry_run = False
    ok_kill    = False
    try:
        if not path.exists():
            violations.append("local operator config not found")
            return ok_trading, ok_dry_run, ok_kill
        text = path.read_text(encoding="utf-8")
        cfg  = yaml.safe_load(text)
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            violations.append("local operator config must be a YAML mapping")
            return ok_trading, ok_dry_run, ok_kill

        if cfg.get("live_trading_enabled") is True:
            ok_trading = True
        else:
            violations.append(
                "local operator config: live_trading_enabled must be boolean true"
            )

        if cfg.get("live_submit_dry_run") is False:
            ok_dry_run = True
        else:
            violations.append(
                "local operator config: live_submit_dry_run must be boolean false"
            )

        if cfg.get("live_kill_switch_enabled") is False:
            ok_kill = True
        else:
            violations.append(
                "local operator config: live_kill_switch_enabled must be boolean false"
            )

    except yaml.YAMLError:
        violations.append("local operator config is not valid YAML")
    except OSError:
        violations.append("local operator config could not be read")

    return ok_trading, ok_dry_run, ok_kill


# ---------------------------------------------------------------------------
# Ledger helpers
# ---------------------------------------------------------------------------

def _append_ledger_row(
    path: Path,
    *,
    timestamp_utc: str,
    client_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    notional_cap: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    row: dict[str, str] = {
        "timestamp_utc":   timestamp_utc,
        "client_order_id": client_order_id,
        "symbol":          symbol,
        "side":            side,
        "order_type":      order_type,
        "notional_cap":    str(notional_cap),
        "status":          "attempting",
        "broker_order_id": "",
        "error":           "",
    }
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _update_ledger_row(
    path: Path,
    client_order_id: str,
    *,
    status: str,
    broker_order_id: str = "",
    error: str = "",
) -> None:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (
                row.get("client_order_id") == client_order_id
                and row.get("status") == "attempting"
            ):
                row["status"]          = status
                row["broker_order_id"] = broker_order_id
                row["error"]           = error
            rows.append(dict(row))
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in LEDGER_COLUMNS})


# ---------------------------------------------------------------------------
# Output builder
# ---------------------------------------------------------------------------

def _make_result(
    *,
    checked_at: str,
    result: str,
    order_submitted: bool,
    broker_mutation_calls_made: bool,
    submit_order_called: bool,
    live_submit_enabled: bool,
    config_safety_overridden: bool,
    out_symbol: str | None,
    out_side: str | None,
    out_order_type: str | None,
    out_notional: float | None,
    client_order_id: str | None,
    submitted_at_utc: str | None,
    broker_order_id_redacted: str | None,
    live_ledger_written: bool,
    violations: list[str],
    blocker: str | None,
) -> dict[str, Any]:
    return {
        "checked_at_utc":                               checked_at,
        "result":                                       result,
        "order_submitted":                              order_submitted,
        "broker_mutation_calls_made":                   broker_mutation_calls_made,
        "submit_order_called":                          submit_order_called,
        "cancel_order_called":                          False,
        "replace_order_called":                         False,
        "live_submit_enabled":                          live_submit_enabled,
        "automated_trading_enabled":                    False,
        "recurring_trading_enabled":                    False,
        "config_safety_overridden_by_local_operator_config": config_safety_overridden,
        "symbol":                                       out_symbol,
        "side":                                         out_side,
        "order_type":                                   out_order_type,
        "notional_cap":                                 out_notional,
        "client_order_id":                              client_order_id,
        "submitted_at_utc":                             submitted_at_utc,
        "broker_order_id_redacted":                     broker_order_id_redacted,
        "credential_values_exposed":                    False,
        "live_ledger_written":                          live_ledger_written,
        "violations":                                   violations,
        "blocker":                                      blocker,
    }


# ---------------------------------------------------------------------------
# JSON writer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_submit(
    *,
    credential_guard_path: Path,
    operator_override_path: Path,
    broker_preflight_path: Path,
    submit_approval_path: Path,
    local_operator_config_path: Path,
    symbol: str,
    side: str,
    order_type: str,
    notional_cap: Any,
    ledger_path: Path,
    broker: Any = None,
    allow_real_live_submit: bool = False,
    _trading_client_cls: Any = None,
    _market_order_request_cls: Any = None,
    _order_side_cls: Any = None,
    _time_in_force_cls: Any = None,
) -> dict[str, Any]:
    """Run all safety gates and, if the real adapter is enabled, submit once.

    Without allow_real_live_submit=True, always returns BLOCKED with
    blocker="real live submit adapter not implemented".
    With allow_real_live_submit=True and all gates passing, builds an
    AlpacaLiveSubmitBroker (reading credentials from environment) and calls
    submit_order exactly once.
    Never raises.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []

    # ------------------------------------------------------------------
    # Stage 1: four prerequisite artifacts
    # ------------------------------------------------------------------
    _read_prereq_artifact(credential_guard_path, "credential_guard", violations)
    _read_prereq_artifact(operator_override_path, "operator_override", violations)
    _read_prereq_artifact(broker_preflight_path,  "broker_preflight",  violations)
    _read_prereq_artifact(submit_approval_path,   "submit_approval",   violations)

    # ------------------------------------------------------------------
    # Stage 2: parameter validation (no raw echo on failure)
    # ------------------------------------------------------------------
    if symbol != _REQUIRED_SYMBOL:
        violations.append("symbol must be exactly 'SPY'")
    if side != _REQUIRED_SIDE:
        violations.append("side must be exactly 'buy'")
    if order_type != _REQUIRED_ORDER_TYPE:
        violations.append("order_type must be exactly 'market'")

    out_notional: float | None = None
    if isinstance(notional_cap, bool):
        violations.append("notional_cap must be a number, not a boolean")
    elif not isinstance(notional_cap, (int, float)):
        violations.append("notional_cap must be a number")
    else:
        nc = float(notional_cap)
        if nc <= 0:
            violations.append("notional_cap must be > 0")
        elif nc > _MAX_NOTIONAL_CAP:
            violations.append(f"notional_cap exceeds maximum of {_MAX_NOTIONAL_CAP}")
        else:
            out_notional = nc

    # ------------------------------------------------------------------
    # Stage 3: local operator YAML config
    # ------------------------------------------------------------------
    ok_trading, ok_dry_run, ok_kill = _load_local_config(
        local_operator_config_path, violations
    )
    config_safety_overridden = ok_trading and ok_dry_run and ok_kill
    live_submit_enabled      = config_safety_overridden

    # ------------------------------------------------------------------
    # Display-safe output fields: only populate when value is canonical
    # ------------------------------------------------------------------
    out_symbol     = _REQUIRED_SYMBOL     if symbol     == _REQUIRED_SYMBOL     else None
    out_side       = _REQUIRED_SIDE       if side       == _REQUIRED_SIDE       else None
    out_order_type = _REQUIRED_ORDER_TYPE if order_type == _REQUIRED_ORDER_TYPE else None

    # ------------------------------------------------------------------
    # Gate: any violation → BLOCKED, no ledger write, no broker call
    # ------------------------------------------------------------------
    if violations:
        return _make_result(
            checked_at=checked_at,
            result="BLOCKED",
            order_submitted=False,
            broker_mutation_calls_made=False,
            submit_order_called=False,
            live_submit_enabled=live_submit_enabled,
            config_safety_overridden=config_safety_overridden,
            out_symbol=out_symbol,
            out_side=out_side,
            out_order_type=out_order_type,
            out_notional=out_notional,
            client_order_id=None,
            submitted_at_utc=None,
            broker_order_id_redacted=None,
            live_ledger_written=False,
            violations=violations,
            blocker=violations[0],
        )

    # ------------------------------------------------------------------
    # Gate: broker=None → build real adapter or BLOCKED
    # ------------------------------------------------------------------
    if broker is None:
        if not allow_real_live_submit:
            blocker = "real live submit adapter not implemented"
            return _make_result(
                checked_at=checked_at,
                result="BLOCKED",
                order_submitted=False,
                broker_mutation_calls_made=False,
                submit_order_called=False,
                live_submit_enabled=live_submit_enabled,
                config_safety_overridden=config_safety_overridden,
                out_symbol=out_symbol,
                out_side=out_side,
                out_order_type=out_order_type,
                out_notional=out_notional,
                client_order_id=None,
                submitted_at_utc=None,
                broker_order_id_redacted=None,
                live_ledger_written=False,
                violations=[blocker],
                blocker=blocker,
            )
        # All pre-gates passed and flag is set: read credentials and build broker.
        try:
            broker = _build_live_submit_broker(
                violations,
                _trading_client_cls=_trading_client_cls,
                _market_order_request_cls=_market_order_request_cls,
                _order_side_cls=_order_side_cls,
                _time_in_force_cls=_time_in_force_cls,
            )
        except Exception:
            blocker = "live submit broker construction failed (details redacted)"
            return _make_result(
                checked_at=checked_at,
                result="BLOCKED",
                order_submitted=False,
                broker_mutation_calls_made=False,
                submit_order_called=False,
                live_submit_enabled=live_submit_enabled,
                config_safety_overridden=config_safety_overridden,
                out_symbol=out_symbol,
                out_side=out_side,
                out_order_type=out_order_type,
                out_notional=out_notional,
                client_order_id=None,
                submitted_at_utc=None,
                broker_order_id_redacted=None,
                live_ledger_written=False,
                violations=[blocker],
                blocker=blocker,
            )
        if broker is None:
            return _make_result(
                checked_at=checked_at,
                result="BLOCKED",
                order_submitted=False,
                broker_mutation_calls_made=False,
                submit_order_called=False,
                live_submit_enabled=live_submit_enabled,
                config_safety_overridden=config_safety_overridden,
                out_symbol=out_symbol,
                out_side=out_side,
                out_order_type=out_order_type,
                out_notional=out_notional,
                client_order_id=None,
                submitted_at_utc=None,
                broker_order_id_redacted=None,
                live_ledger_written=False,
                violations=violations,
                blocker=violations[-1],
            )

    # ------------------------------------------------------------------
    # All gates passed and broker is not None.
    # Generate client_order_id and write the pre-submit ledger row.
    # ------------------------------------------------------------------
    ts_str          = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    uid_fragment    = uuid.uuid4().hex[:8].upper()
    client_order_id = f"LIVE-SPY-BUY-{ts_str}-{uid_fragment}"

    try:
        _append_ledger_row(
            ledger_path,
            timestamp_utc=checked_at,
            client_order_id=client_order_id,
            symbol=_REQUIRED_SYMBOL,
            side=_REQUIRED_SIDE,
            order_type=_REQUIRED_ORDER_TYPE,
            notional_cap=out_notional,  # guaranteed non-None here
        )
    except Exception:
        blocker = "pre-submit ledger write failed"
        return _make_result(
            checked_at=checked_at,
            result="BLOCKED",
            order_submitted=False,
            broker_mutation_calls_made=False,
            submit_order_called=False,
            live_submit_enabled=live_submit_enabled,
            config_safety_overridden=config_safety_overridden,
            out_symbol=out_symbol,
            out_side=out_side,
            out_order_type=out_order_type,
            out_notional=out_notional,
            client_order_id=client_order_id,
            submitted_at_utc=None,
            broker_order_id_redacted=None,
            live_ledger_written=False,
            violations=[blocker],
            blocker=blocker,
        )

    # ------------------------------------------------------------------
    # Submit: call submit_order exactly once -- no retry.
    # ------------------------------------------------------------------
    try:
        broker.submit_order(
            symbol=_REQUIRED_SYMBOL,
            side=_REQUIRED_SIDE,
            order_type=_REQUIRED_ORDER_TYPE,
            notional=out_notional,
            client_order_id=client_order_id,
        )
        submitted_at_utc = datetime.now(tz=timezone.utc).isoformat()
        try:
            _update_ledger_row(
                ledger_path, client_order_id,
                status="submitted",
                broker_order_id="<redacted>",
                error="",
            )
        except Exception:
            pass  # best-effort; order was already placed

        return _make_result(
            checked_at=checked_at,
            result="SUBMITTED",
            order_submitted=True,
            broker_mutation_calls_made=True,
            submit_order_called=True,
            live_submit_enabled=live_submit_enabled,
            config_safety_overridden=config_safety_overridden,
            out_symbol=out_symbol,
            out_side=out_side,
            out_order_type=out_order_type,
            out_notional=out_notional,
            client_order_id=client_order_id,
            submitted_at_utc=submitted_at_utc,
            broker_order_id_redacted="<redacted>",
            live_ledger_written=True,
            violations=[],
            blocker=None,
        )

    except Exception:
        # Broker exception: redact all exception text from every output field.
        blocker = "broker exception during submit (details redacted)"
        try:
            _update_ledger_row(
                ledger_path, client_order_id,
                status="exception",
                broker_order_id="",
                error="details redacted",
            )
        except Exception:
            pass  # best-effort

        return _make_result(
            checked_at=checked_at,
            result="BLOCKED",
            order_submitted=False,
            broker_mutation_calls_made=False,
            submit_order_called=True,
            live_submit_enabled=live_submit_enabled,
            config_safety_overridden=config_safety_overridden,
            out_symbol=out_symbol,
            out_side=out_side,
            out_order_type=out_order_type,
            out_notional=out_notional,
            client_order_id=client_order_id,
            submitted_at_utc=None,
            broker_order_id_redacted=None,
            live_ledger_written=True,
            violations=[blocker],
            blocker=blocker,
        )


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_result(result: dict[str, Any]) -> None:
    print("\n=== Live Single Manual Submit ===")
    print(f"  checked_at_utc                                    : {result['checked_at_utc']}")
    print(f"  result                                            : {result['result']}")
    print(f"  order_submitted                                   : {result['order_submitted']}")
    print(f"  broker_mutation_calls_made                        : {result['broker_mutation_calls_made']}")
    print(f"  submit_order_called                               : {result['submit_order_called']}")
    print(f"  cancel_order_called                               : {result['cancel_order_called']}")
    print(f"  replace_order_called                              : {result['replace_order_called']}")
    print(f"  live_submit_enabled                               : {result['live_submit_enabled']}")
    print(f"  automated_trading_enabled                         : {result['automated_trading_enabled']}")
    print(f"  recurring_trading_enabled                         : {result['recurring_trading_enabled']}")
    print(f"  config_safety_overridden_by_local_operator_config : {result['config_safety_overridden_by_local_operator_config']}")
    print(f"  symbol                                            : {result['symbol']}")
    print(f"  side                                              : {result['side']}")
    print(f"  order_type                                        : {result['order_type']}")
    print(f"  notional_cap                                      : {result['notional_cap']}")
    print(f"  client_order_id                                   : {result['client_order_id']}")
    print(f"  submitted_at_utc                                  : {result['submitted_at_utc']}")
    print(f"  broker_order_id_redacted                          : {result['broker_order_id_redacted']}")
    print(f"  credential_values_exposed                         : {result['credential_values_exposed']}")
    print(f"  live_ledger_written                               : {result['live_ledger_written']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    if result["blocker"]:
        print(f"  blocker: {result['blocker']}")
    print("=" * 35)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_single_manual_submit",
        description=(
            "One-time manually approved single live SPY market buy via the Alpaca SDK. "
            "Without --allow-real-live-submit-once, CLI is always BLOCKED. "
            "With the flag, credentials are read from environment variables "
            "ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY only after all safety "
            "gates pass."
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
        "--broker-preflight", required=True, dest="broker_preflight",
        help="Path to live_broker_preflight_readonly.json",
    )
    parser.add_argument(
        "--submit-approval", required=True, dest="submit_approval",
        help="Path to live_single_submit_approval_review.json",
    )
    parser.add_argument(
        "--local-operator-config", required=True, dest="local_operator_config",
        help="Path to local operator YAML config with config_safety overrides",
    )
    parser.add_argument("--symbol",      required=True)
    parser.add_argument("--side",        required=True)
    parser.add_argument("--order-type",  required=True, dest="order_type")
    parser.add_argument(
        "--notional-cap", required=True, type=float, dest="notional_cap",
        help="Notional cap in USD -- must be > 0 and <= 100.0",
    )
    parser.add_argument(
        "--ledger", required=True,
        help="Path to live ledger CSV",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write single_manual_live_submit_attempt.json",
    )
    parser.add_argument(
        "--allow-real-live-submit-once",
        action="store_true",
        default=False,
        dest="allow_real_live_submit",
        help=(
            "Enable real Alpaca live submit.  Without this flag, CLI is always BLOCKED. "
            "Requires ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY in environment. "
            "Credentials are read only after all safety gates pass."
        ),
    )
    args = parser.parse_args(argv)

    result = run_submit(
        credential_guard_path=Path(args.credential_guard),
        operator_override_path=Path(args.operator_override),
        broker_preflight_path=Path(args.broker_preflight),
        submit_approval_path=Path(args.submit_approval),
        local_operator_config_path=Path(args.local_operator_config),
        symbol=args.symbol,
        side=args.side,
        order_type=args.order_type,
        notional_cap=args.notional_cap,
        ledger_path=Path(args.ledger),
        broker=None,
        allow_real_live_submit=args.allow_real_live_submit,
    )

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_result(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "SUBMITTED":
        sys.exit(1)


if __name__ == "__main__":
    main()
