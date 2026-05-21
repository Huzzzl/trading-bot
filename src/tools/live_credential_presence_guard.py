"""
tools/live_credential_presence_guard.py
-----------------------------------------
Offline, read-only credential presence guard for future live readiness.

Checks only that the required environment variables exist and are non-empty.
It does NOT validate the values against Alpaca or any other broker, does NOT
instantiate a broker client, does NOT print or log secret values, and does NOT
enable live trading or remove the ``config_safety`` blocker.

Usage::

    python -m src.tools.live_credential_presence_guard \\
        --required-env ALPACA_LIVE_API_KEY \\
        --required-env ALPACA_LIVE_SECRET_KEY \\
        --output output/live_credential_presence_guard.json

PASS conditions (all must hold)
---------------------------------
* At least one ``--required-env`` key is provided
* Every named environment variable is present in ``os.environ``
* Every named environment variable has a non-empty, non-whitespace value

Output JSON
-----------
Written to ``--output`` path (required):

    checked_at_utc             : ISO-8601 UTC timestamp
    result                     : "PASS" or "BLOCKED"
    credentials_present        : true / false
    credentials_read           : false  (always — values are never read)
    credential_values_exposed  : false  (always)
    broker_calls_made          : false  (always)
    live_submit_enabled        : false  (always)
    real_submit_implemented    : false  (always)
    submit_order_reachable     : false  (always)
    required_keys              : list of key names provided via --required-env
    checks                     : list of per-key dicts (see below)
    violations                 : list of human-readable failure strings
    blocker                    : first violation string, or null

Per-key check dict::

    key              : environment variable name
    present          : true / false
    non_empty        : true / false
    redacted_preview : "<redacted>" when present and non-empty, else null

``redacted_preview`` is always the fixed literal string ``"<redacted>"``.
It is never derived from, prefixed from, or suffixed from the real secret value.

Exit codes
----------
0 — PASS
1 — BLOCKED

What it never does
------------------
* Never reads or stores credential values.
* Never prints credential values to stdout.
* Never includes credential values in output JSON.
* Never calls any Alpaca endpoint.
* Never instantiates a broker client.
* Never calls submit_order or cancel_order.
* Never modifies or writes live ledger files.
* Never enables live trading.
* Never removes the config_safety blocker.
* Fails closed: no keys provided, invalid key name, missing key, or
  empty value → BLOCKED.
* Invalid key names are sanitized to ``"<invalid-env-key>"`` in all
  output — the raw invalid string is never written to stdout or JSON.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REDACTED_PREVIEW = "<redacted>"
_INVALID_KEY_PLACEHOLDER = "<invalid-env-key>"

# Env var names must match the POSIX convention: uppercase letters, digits,
# and underscores only, starting with a letter or underscore.
# This rejects shell-expanded secret values that appear as key arguments.
_VALID_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _is_valid_key_name(key: str) -> bool:
    """Return True only when key looks like a safe env var name."""
    return bool(_VALID_KEY_RE.match(key))


# ---------------------------------------------------------------------------
# Per-key check
# ---------------------------------------------------------------------------

def _check_key(key: str) -> tuple[dict[str, Any], bool]:
    """Return (per-key check dict, key_was_valid).

    When the key name is invalid the raw value is never passed to
    os.environ.get() and is never written to the returned dict.
    """
    if not _is_valid_key_name(key):
        return {
            "key":              _INVALID_KEY_PLACEHOLDER,
            "present":          False,
            "non_empty":        False,
            "redacted_preview": None,
        }, False

    raw = os.environ.get(key)
    present = raw is not None
    non_empty = present and bool(raw.strip())
    return {
        "key":              key,
        "present":          present,
        "non_empty":        non_empty,
        "redacted_preview": _REDACTED_PREVIEW if non_empty else None,
    }, True


# ---------------------------------------------------------------------------
# Guard runner
# ---------------------------------------------------------------------------

def run_guard(*, required_keys: list[str]) -> dict[str, Any]:
    """Run the credential presence guard and return the result dict.

    Never raises.  All errors are captured as violations → BLOCKED.
    Never reads, stores, or exposes credential values.
    Invalid key names are sanitized and never echoed into output.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    checks: list[dict[str, Any]] = []

    if not required_keys:
        violations.append(
            "no required credential keys provided -- "
            "at least one --required-env key must be specified"
        )

    for key in required_keys:
        check, valid = _check_key(key)
        checks.append(check)
        if not valid:
            violations.append(
                f"{_INVALID_KEY_PLACEHOLDER}: invalid environment variable name -- "
                "must match ^[A-Z_][A-Z0-9_]*$ "
                "(possible shell expansion of a secret value)"
            )
        elif not check["present"]:
            violations.append(
                f"{key}: environment variable is not set"
            )
        elif not check["non_empty"]:
            violations.append(
                f"{key}: environment variable is set but empty or whitespace-only"
            )

    credentials_present = bool(required_keys) and not violations
    result = "PASS" if not violations else "BLOCKED"
    blocker = violations[0] if violations else None

    return {
        "checked_at_utc":           checked_at,
        "result":                   result,
        "credentials_present":      credentials_present,
        "credentials_read":         False,
        "credential_values_exposed": False,
        "broker_calls_made":        False,
        "live_submit_enabled":      False,
        "real_submit_implemented":  False,
        "submit_order_reachable":   False,
        "required_keys":            [
            k if _is_valid_key_name(k) else _INVALID_KEY_PLACEHOLDER
            for k in required_keys
        ],
        "checks":                   checks,
        "violations":               violations,
        "blocker":                  blocker,
    }


# ---------------------------------------------------------------------------
# Writer / printer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def print_guard(result: dict[str, Any]) -> None:
    print("\n=== Live Credential Presence Guard ===")
    print(f"  checked_at_utc            : {result['checked_at_utc']}")
    print(f"  result                    : {result['result']}")
    print(f"  credentials_present       : {result['credentials_present']}")
    print(f"  credentials_read          : {result['credentials_read']}")
    print(f"  credential_values_exposed : {result['credential_values_exposed']}")
    print(f"  broker_calls_made         : {result['broker_calls_made']}")
    print(f"  live_submit_enabled       : {result['live_submit_enabled']}")
    print(f"  real_submit_implemented   : {result['real_submit_implemented']}")
    print(f"  submit_order_reachable    : {result['submit_order_reachable']}")
    print(f"  required_keys             : {result['required_keys']}")
    for check in result["checks"]:
        preview = check["redacted_preview"] or "null"
        print(
            f"    {check['key']}: present={check['present']} "
            f"non_empty={check['non_empty']} "
            f"redacted_preview={preview}"
        )
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    if result["blocker"]:
        print(f"  blocker: {result['blocker']}")
    print("=" * 38)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_credential_presence_guard",
        description=(
            "Offline read-only credential presence guard. "
            "Checks that required environment variables exist and are non-empty. "
            "Never reads, stores, or exposes credential values. "
            "Never calls Alpaca. Never enables live trading. "
            "PASS does not validate credentials and does not remove config_safety."
        ),
    )
    parser.add_argument(
        "--required-env", dest="required_env", action="append", default=[],
        metavar="KEY",
        help=(
            "Environment variable name to require (repeatable). "
            "Example: --required-env ALPACA_LIVE_API_KEY "
            "--required-env ALPACA_LIVE_SECRET_KEY"
        ),
    )
    parser.add_argument(
        "--output", required=True, dest="output",
        help="Path to write live_credential_presence_guard.json",
    )
    args = parser.parse_args(argv)

    result = run_guard(required_keys=args.required_env)

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_guard(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
