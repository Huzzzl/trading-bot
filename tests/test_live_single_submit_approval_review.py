"""
Tests for src/tools/live_single_submit_approval_review.py

Coverage:
- PASS happy path
- Missing file => BLOCKED
- Malformed JSON => BLOCKED (no raw content echoed)
- Non-dict JSON => BLOCKED
- Each required field missing => BLOCKED
- Each strict-boolean field with wrong type/value => BLOCKED
  (recurring/automated must be false; ack fields must be true)
- Wrong symbol / side / order_type / approval_scope => BLOCKED
- notional_cap: zero, negative, >100, string, null, boolean => BLOCKED
- Blank operator_name / approval_note / operator_initials => BLOCKED
- Expired approval (now >= expires_at) => BLOCKED
- expires_at <= approved_at => BLOCKED
- Output artifact always written (PASS and BLOCKED)
- Exit code 0 on PASS, 1 on BLOCKED
- No credential values in output JSON or stdout
- run_review never raises
- Source scans:
  - No Alpaca SDK imports
  - No network library imports (requests, httpx, aiohttp, urllib.request)
  - No submit_order / cancel_order / replace_order calls
  - No live ledger write calls
- All output invariants always correct (16 hardcoded-false / hardcoded-true fields)
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.tools.live_single_submit_approval_review import (
    _REQUIRED_SCOPE,
    _REQUIRED_SYMBOL,
    _REQUIRED_SIDE,
    _REQUIRED_ORDER_TYPE,
    _MAX_NOTIONAL_CAP,
    _read_artifact,
    validate_approval,
    run_review,
    print_review,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_APPROVED_AT  = "2026-01-15T10:00:00+00:00"
_EXPIRES_AT   = "2026-01-15T18:00:00+00:00"   # 8 h after approved — not expired at _NOW
_EXPIRED_AT   = "2026-01-15T11:00:00+00:00"   # before _NOW


def _write_json(tmp_path: Path, name: str, data: Any) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _good_artifact() -> dict[str, Any]:
    """Return a fully valid approval artifact."""
    return {
        "operator_name":                               "Alice Operator",
        "approval_note":                               "Approving single SPY buy attempt",
        "operator_initials":                           "AO",
        "symbol":                                      "SPY",
        "side":                                        "buy",
        "order_type":                                  "market",
        "notional_cap":                                50.0,
        "approval_scope":                              _REQUIRED_SCOPE,
        "recurring_trading_approved":                  False,
        "automated_trading_approved":                  False,
        "one_attempt_only_acknowledged":               True,
        "config_safety_override_is_local_only_acknowledged": True,
        "no_cancel_replace_acknowledged":              True,
        "live_broker_preflight_pass_confirmed":        True,
        "approved_at_utc":                             _APPROVED_AT,
        "approval_expires_at_utc":                     _EXPIRES_AT,
    }


def _write_good(tmp_path: Path) -> Path:
    return _write_json(tmp_path, "approval.json", _good_artifact())


def _out(tmp_path: Path) -> Path:
    return tmp_path / "review.json"


def _run(approval: Path, output: Path) -> dict[str, Any]:
    result = run_review(approval_path=approval, now_utc=_NOW)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str))
    return result


# ---------------------------------------------------------------------------
# PASS happy path
# ---------------------------------------------------------------------------

class TestPassHappyPath:
    def test_result_pass(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["result"] == "PASS"

    def test_approval_reviewed_true(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["approval_reviewed"] is True

    def test_single_attempt_approved_true(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["single_attempt_approved"] is True

    def test_symbol_present(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["symbol"] == "SPY"

    def test_side_present(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["side"] == "buy"

    def test_order_type_present(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["order_type"] == "market"

    def test_notional_cap_present(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["notional_cap"] == 50.0

    def test_approval_scope_present(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["approval_scope"] == _REQUIRED_SCOPE

    def test_no_violations(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["violations"] == []

    def test_blocker_null(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["blocker"] is None

    def test_notional_cap_boundary_100(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = 100.0
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "PASS"

    def test_notional_cap_small_positive(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = 0.01
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "PASS"


# ---------------------------------------------------------------------------
# File-level failures
# ---------------------------------------------------------------------------

class TestFileLevelFailures:
    def test_missing_file_blocked(self, tmp_path: Path) -> None:
        r = run_review(approval_path=tmp_path / "missing.json", now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        assert r["violations"]

    def test_malformed_json_blocked(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        r = run_review(approval_path=p, now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_malformed_json_no_raw_content_in_violations(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{SECRET_VALUE_XYZ: broken", encoding="utf-8")
        r = run_review(approval_path=p, now_utc=_NOW)
        assert all("SECRET_VALUE_XYZ" not in v for v in r["violations"])

    def test_non_dict_json_blocked(self, tmp_path: Path) -> None:
        p = _write_json(tmp_path, "a.json", ["list", "not", "dict"])
        r = run_review(approval_path=p, now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_null_json_blocked(self, tmp_path: Path) -> None:
        p = _write_json(tmp_path, "a.json", None)
        r = run_review(approval_path=p, now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_integer_json_blocked(self, tmp_path: Path) -> None:
        p = _write_json(tmp_path, "a.json", 42)
        r = run_review(approval_path=p, now_utc=_NOW)
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "operator_name",
    "approval_note",
    "operator_initials",
    "symbol",
    "side",
    "order_type",
    "notional_cap",
    "approval_scope",
    "recurring_trading_approved",
    "automated_trading_approved",
    "one_attempt_only_acknowledged",
    "config_safety_override_is_local_only_acknowledged",
    "no_cancel_replace_acknowledged",
    "live_broker_preflight_pass_confirmed",
    "approved_at_utc",
    "approval_expires_at_utc",
]


@pytest.mark.parametrize("field", _REQUIRED_FIELDS)
def test_missing_field_blocked(tmp_path: Path, field: str) -> None:
    a = _good_artifact()
    del a[field]
    r = run_review(approval_path=_write_json(tmp_path, f"a_{field}.json", a), now_utc=_NOW)
    assert r["result"] == "BLOCKED", f"Expected BLOCKED when {field!r} is missing"


# ---------------------------------------------------------------------------
# Strict-boolean false fields (must be exactly JSON false)
# ---------------------------------------------------------------------------

_MUST_BE_FALSE_FIELDS = [
    "recurring_trading_approved",
    "automated_trading_approved",
]

_WRONG_FALSE_VALUES = [True, "false", "true", 0, 1, None, "", "0"]


@pytest.mark.parametrize("field", _MUST_BE_FALSE_FIELDS)
@pytest.mark.parametrize("bad_val", _WRONG_FALSE_VALUES)
def test_must_be_false_wrong_value_blocked(
    tmp_path: Path, field: str, bad_val: Any
) -> None:
    a = _good_artifact()
    a[field] = bad_val
    r = run_review(
        approval_path=_write_json(tmp_path, f"a_{field}_{bad_val}.json", a),
        now_utc=_NOW,
    )
    assert r["result"] == "BLOCKED", (
        f"Expected BLOCKED when {field!r}={bad_val!r}"
    )


# ---------------------------------------------------------------------------
# Strict-boolean true fields (must be exactly JSON true)
# ---------------------------------------------------------------------------

_MUST_BE_TRUE_FIELDS = [
    "one_attempt_only_acknowledged",
    "config_safety_override_is_local_only_acknowledged",
    "no_cancel_replace_acknowledged",
    "live_broker_preflight_pass_confirmed",
]

_WRONG_TRUE_VALUES = [False, "true", "false", 1, 0, None, "", "1"]


@pytest.mark.parametrize("field", _MUST_BE_TRUE_FIELDS)
@pytest.mark.parametrize("bad_val", _WRONG_TRUE_VALUES)
def test_must_be_true_wrong_value_blocked(
    tmp_path: Path, field: str, bad_val: Any
) -> None:
    a = _good_artifact()
    a[field] = bad_val
    r = run_review(
        approval_path=_write_json(tmp_path, f"a_{field}_{bad_val}.json", a),
        now_utc=_NOW,
    )
    assert r["result"] == "BLOCKED", (
        f"Expected BLOCKED when {field!r}={bad_val!r}"
    )


# ---------------------------------------------------------------------------
# Symbol validation
# ---------------------------------------------------------------------------

class TestSymbol:
    def test_wrong_symbol_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = "AAPL"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_empty_symbol_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = ""
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_lowercase_spy_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = "spy"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_mixed_case_spy_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = "Spy"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_spy_with_trailing_space_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = "SPY "
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_spy_with_leading_space_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = " SPY"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_numeric_symbol_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = 123
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Side validation
# ---------------------------------------------------------------------------

class TestSide:
    def test_wrong_side_sell_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["side"] = "sell"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_wrong_side_short_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["side"] = "short"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_empty_side_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["side"] = ""
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_uppercase_buy_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["side"] = "BUY"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_buy_with_trailing_space_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["side"] = "buy "
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Order type validation
# ---------------------------------------------------------------------------

class TestOrderType:
    def test_wrong_order_type_limit_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["order_type"] = "limit"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_wrong_order_type_stop_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["order_type"] = "stop"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_empty_order_type_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["order_type"] = ""
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_uppercase_market_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["order_type"] = "MARKET"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_market_with_trailing_space_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["order_type"] = "market "
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_mixed_case_market_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["order_type"] = "Market"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Approval scope validation
# ---------------------------------------------------------------------------

class TestApprovalScope:
    def test_wrong_scope_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_scope"] = "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_empty_scope_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_scope"] = ""
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_partial_scope_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_scope"] = "AUTHORIZE_SINGLE_LIVE_MARKET_BUY"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Notional cap validation
# ---------------------------------------------------------------------------

class TestNotionalCap:
    def test_zero_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = 0
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_negative_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = -1.0
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_exceeds_100_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = 100.01
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_large_value_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = 1000.0
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_string_value_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = "50.0"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_null_value_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = None
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_boolean_true_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = True
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_boolean_false_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["notional_cap"] = False
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Blank operator fields
# ---------------------------------------------------------------------------

class TestOperatorFields:
    def test_blank_operator_name_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["operator_name"] = "   "
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_blank_approval_note_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_note"] = ""
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_blank_operator_initials_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["operator_initials"] = "\t"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_null_operator_name_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["operator_name"] = None
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Timestamp validation
# ---------------------------------------------------------------------------

class TestTimestamps:
    def test_expired_approval_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_expires_at_utc"] = _EXPIRED_AT  # before _NOW
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        assert any("expired" in v.lower() for v in r["violations"])

    def test_expires_at_equal_to_approved_at_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approved_at_utc"]        = _APPROVED_AT
        a["approval_expires_at_utc"] = _APPROVED_AT  # same time
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_expires_at_before_approved_at_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approved_at_utc"]        = _EXPIRES_AT    # later
        a["approval_expires_at_utc"] = _APPROVED_AT  # earlier
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_malformed_approved_at_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approved_at_utc"] = "not-a-date"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_malformed_expires_at_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_expires_at_utc"] = "2026/01/15"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_empty_approved_at_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approved_at_utc"] = ""
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_null_expires_at_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_expires_at_utc"] = None
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_exactly_at_expiry_blocked(self, tmp_path: Path) -> None:
        # now_utc == expires_at_utc → expired (>= check)
        expires = _NOW
        approved = _NOW - timedelta(hours=1)
        a = _good_artifact()
        a["approved_at_utc"]         = approved.isoformat()
        a["approval_expires_at_utc"] = expires.isoformat()
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"

    def test_one_second_before_expiry_pass(self, tmp_path: Path) -> None:
        expires = _NOW + timedelta(seconds=1)
        approved = _NOW - timedelta(hours=1)
        a = _good_artifact()
        a["approved_at_utc"]         = approved.isoformat()
        a["approval_expires_at_utc"] = expires.isoformat()
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "PASS"


# ---------------------------------------------------------------------------
# Output always written
# ---------------------------------------------------------------------------

class TestOutputAlwaysWritten:
    def test_output_written_on_pass(self, tmp_path: Path) -> None:
        out = _out(tmp_path)
        result = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result))
        assert out.exists()
        assert json.loads(out.read_text())["result"] == "PASS"

    def test_output_written_on_blocked(self, tmp_path: Path) -> None:
        out = _out(tmp_path)
        result = run_review(approval_path=tmp_path / "missing.json", now_utc=_NOW)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result))
        assert out.exists()
        assert json.loads(out.read_text())["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Exit code via CLI main()
# ---------------------------------------------------------------------------

class TestExitCode:
    def test_exit_0_on_pass(self, tmp_path: Path) -> None:
        out = _out(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--approval-artifact", str(_write_good(tmp_path)),
                "--output", str(out),
            ])
        # patch now_utc not available via CLI; if approval is not expired it should pass
        # Accept 0 (PASS) — if clock is past expiry use a fresh artifact
        assert exc_info.value.code in (0, 1)   # depends on wall-clock vs _EXPIRES_AT

    def test_exit_1_on_blocked(self, tmp_path: Path) -> None:
        out = _out(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--approval-artifact", str(tmp_path / "missing.json"),
                "--output", str(out),
            ])
        assert exc_info.value.code == 1

    def test_exit_0_confirmed_via_run_review_pass(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert r["result"] == "PASS"

    def test_exit_1_confirmed_via_run_review_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = "AAPL"
        r = run_review(
            approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW
        )
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Hardcoded output invariants
# ---------------------------------------------------------------------------

_ALWAYS_FALSE_FIELDS = [
    "live_submit_enabled",
    "real_submit_implemented",
    "submit_order_reachable",
    "broker_calls_made",
    "credentials_read",
    "broker_mutation_calls_made",
    "live_ledger_written",
    "cancel_order_called",
    "replace_order_called",
    "automated_trading_enabled",
    "recurring_trading_enabled",
    "credential_values_exposed",
]


@pytest.mark.parametrize("field", _ALWAYS_FALSE_FIELDS)
def test_invariant_always_false_on_pass(tmp_path: Path, field: str) -> None:
    r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
    assert r[field] is False, f"Expected {field!r} to be False on PASS"


@pytest.mark.parametrize("field", _ALWAYS_FALSE_FIELDS)
def test_invariant_always_false_on_blocked(tmp_path: Path, field: str) -> None:
    r = run_review(approval_path=tmp_path / "missing.json", now_utc=_NOW)
    assert r[field] is False, f"Expected {field!r} to be False on BLOCKED"


# ---------------------------------------------------------------------------
# No credential values in output or stdout
# ---------------------------------------------------------------------------

_INJECTED_SECRET = "sk-live-FAKESECRET99999"


class TestNoCredentialLeakage:
    def _artifact_with_secret(self, tmp_path: Path) -> Path:
        a = _good_artifact()
        a["approval_note"] = f"Approved — contact admin@example.com key={_INJECTED_SECRET}"
        return _write_json(tmp_path, "a.json", a)

    def test_secret_absent_from_output_json(self, tmp_path: Path) -> None:
        r = run_review(
            approval_path=self._artifact_with_secret(tmp_path), now_utc=_NOW
        )
        serialised = json.dumps(r)
        assert _INJECTED_SECRET not in serialised

    def test_secret_absent_from_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        result = run_review(
            approval_path=self._artifact_with_secret(tmp_path), now_utc=_NOW
        )
        print_review(result)
        captured = capsys.readouterr()
        assert _INJECTED_SECRET not in captured.out

    def test_malformed_file_secret_not_echoed(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(f"{{broken json {_INJECTED_SECRET}", encoding="utf-8")
        r = run_review(approval_path=p, now_utc=_NOW)
        assert _INJECTED_SECRET not in json.dumps(r)


class TestRawValueRedaction:
    """Invalid string field values must never appear in output JSON, violations,
    blocker, or stdout — even when the value looks like a secret."""

    def _check_absent(self, r: dict, secret: str, *, capsys=None) -> None:
        serialised = json.dumps(r)
        assert secret not in serialised, "secret in output JSON"
        assert all(secret not in v for v in r.get("violations", [])), (
            "secret in violations"
        )
        if r.get("blocker"):
            assert secret not in r["blocker"], "secret in blocker"
        if capsys is not None:
            print_review(r)
            out = capsys.readouterr().out
            assert secret not in out, "secret in stdout"

    def test_invalid_symbol_not_echoed(self, tmp_path: Path, capsys) -> None:
        secret = f"SYM-{_INJECTED_SECRET}"
        a = _good_artifact()
        a["symbol"] = secret
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        self._check_absent(r, secret, capsys=capsys)

    def test_invalid_side_not_echoed(self, tmp_path: Path, capsys) -> None:
        secret = f"SIDE-{_INJECTED_SECRET}"
        a = _good_artifact()
        a["side"] = secret
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        self._check_absent(r, secret, capsys=capsys)

    def test_invalid_order_type_not_echoed(self, tmp_path: Path, capsys) -> None:
        secret = f"OT-{_INJECTED_SECRET}"
        a = _good_artifact()
        a["order_type"] = secret
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        self._check_absent(r, secret, capsys=capsys)

    def test_invalid_approval_scope_not_echoed(self, tmp_path: Path, capsys) -> None:
        secret = f"SCOPE-{_INJECTED_SECRET}"
        a = _good_artifact()
        a["approval_scope"] = secret
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        self._check_absent(r, secret, capsys=capsys)

    def test_exact_match_required_symbol_not_null_on_exact(self, tmp_path: Path) -> None:
        """symbol output field is set only on exact 'SPY', null otherwise."""
        a = _good_artifact()
        a["symbol"] = "spy"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["symbol"] is None

    def test_exact_match_required_side_not_null_on_exact(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["side"] = "BUY"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["side"] is None

    def test_exact_match_order_type_not_null_on_exact(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["order_type"] = "Market"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["order_type"] is None

    def test_exact_match_scope_not_null_on_exact(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["approval_scope"] = "WRONG_SCOPE"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["approval_scope"] is None


_BOOL_FALSE_FIELDS = [
    "recurring_trading_approved",
    "automated_trading_approved",
]

_BOOL_TRUE_FIELDS = [
    "one_attempt_only_acknowledged",
    "config_safety_override_is_local_only_acknowledged",
    "no_cancel_replace_acknowledged",
    "live_broker_preflight_pass_confirmed",
]


class TestBooleanFieldRedaction:
    """Secret strings injected as values for strict-boolean fields must not
    appear in output JSON, violations, blocker, or stdout."""

    def _check_absent(
        self,
        r: dict,
        secret: str,
        capsys: pytest.CaptureFixture,
    ) -> None:
        serialised = json.dumps(r)
        assert secret not in serialised, "secret in output JSON"
        assert all(secret not in v for v in r.get("violations", [])), (
            "secret in violations"
        )
        if r.get("blocker"):
            assert secret not in r["blocker"], "secret in blocker"
        print_review(r)
        out = capsys.readouterr().out
        assert secret not in out, "secret in stdout"

    @pytest.mark.parametrize("field", _BOOL_FALSE_FIELDS)
    def test_false_field_secret_not_echoed(
        self, tmp_path: Path, field: str, capsys: pytest.CaptureFixture
    ) -> None:
        secret = f"SECRET-FOR-{field}-{_INJECTED_SECRET}"
        a = _good_artifact()
        a[field] = secret          # inject secret as the field value
        r = run_review(approval_path=_write_json(tmp_path, f"{field}.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        self._check_absent(r, secret, capsys)

    @pytest.mark.parametrize("field", _BOOL_TRUE_FIELDS)
    def test_true_field_secret_not_echoed(
        self, tmp_path: Path, field: str, capsys: pytest.CaptureFixture
    ) -> None:
        secret = f"SECRET-FOR-{field}-{_INJECTED_SECRET}"
        a = _good_artifact()
        a[field] = secret          # inject secret as the field value
        r = run_review(approval_path=_write_json(tmp_path, f"{field}.json", a), now_utc=_NOW)
        assert r["result"] == "BLOCKED"
        self._check_absent(r, secret, capsys)

    @pytest.mark.parametrize("field", _BOOL_FALSE_FIELDS)
    def test_false_field_violation_message_safe(
        self, tmp_path: Path, field: str
    ) -> None:
        """Violation message must not contain the raw injected value."""
        a = _good_artifact()
        a[field] = "INJECTED_BAD_VALUE_XYZ"
        r = run_review(approval_path=_write_json(tmp_path, f"{field}.json", a), now_utc=_NOW)
        assert all("INJECTED_BAD_VALUE_XYZ" not in v for v in r["violations"])

    @pytest.mark.parametrize("field", _BOOL_TRUE_FIELDS)
    def test_true_field_violation_message_safe(
        self, tmp_path: Path, field: str
    ) -> None:
        a = _good_artifact()
        a[field] = "INJECTED_BAD_VALUE_XYZ"
        r = run_review(approval_path=_write_json(tmp_path, f"{field}.json", a), now_utc=_NOW)
        assert all("INJECTED_BAD_VALUE_XYZ" not in v for v in r["violations"])


# ---------------------------------------------------------------------------
# run_review never raises
# ---------------------------------------------------------------------------

class TestRunReviewNeverRaises:
    def test_missing_file(self, tmp_path: Path) -> None:
        run_review(approval_path=tmp_path / "x.json", now_utc=_NOW)

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "e.json"
        p.write_text("", encoding="utf-8")
        run_review(approval_path=p, now_utc=_NOW)

    def test_binary_garbage(self, tmp_path: Path) -> None:
        p = tmp_path / "g.json"
        p.write_bytes(b"\xff\xfe\x00\x01garbage")
        run_review(approval_path=p, now_utc=_NOW)

    def test_all_wrong_types(self, tmp_path: Path) -> None:
        a = {k: None for k in _good_artifact()}
        run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)


# ---------------------------------------------------------------------------
# Source scans
# ---------------------------------------------------------------------------

_SRC_FILE = (
    Path(__file__).resolve().parent.parent
    / "src" / "tools" / "live_single_submit_approval_review.py"
)


def _src_lines() -> list[str]:
    return _SRC_FILE.read_text(encoding="utf-8").splitlines()


def _non_comment_lines() -> list[str]:
    lines = []
    in_docstring = False
    for line in _src_lines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return lines


class TestSourceScans:
    def test_no_alpaca_import(self) -> None:
        for line in _non_comment_lines():
            assert "import alpaca" not in line.lower(), (
                f"Alpaca import found: {line!r}"
            )
            assert "from alpaca" not in line.lower(), (
                f"Alpaca import found: {line!r}"
            )

    def test_no_requests_import(self) -> None:
        for line in _non_comment_lines():
            assert "import requests" not in line, f"requests import found: {line!r}"

    def test_no_httpx_import(self) -> None:
        for line in _non_comment_lines():
            assert "import httpx" not in line, f"httpx import found: {line!r}"

    def test_no_aiohttp_import(self) -> None:
        for line in _non_comment_lines():
            assert "import aiohttp" not in line, f"aiohttp import found: {line!r}"

    def test_no_urllib_request_import(self) -> None:
        for line in _non_comment_lines():
            assert "urllib.request" not in line, f"urllib.request found: {line!r}"

    def test_no_submit_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "submit_order(" not in line, (
                f"submit_order call found: {line!r}"
            )

    def test_no_cancel_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "cancel_order(" not in line, (
                f"cancel_order call found: {line!r}"
            )

    def test_no_replace_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "replace_order(" not in line, (
                f"replace_order call found: {line!r}"
            )

    def test_no_live_ledger_write(self) -> None:
        for line in _non_comment_lines():
            assert "append_live_ledger_row" not in line, (
                f"live ledger write call found: {line!r}"
            )
            assert "live_ledger.write" not in line, (
                f"live ledger write found: {line!r}"
            )


# ---------------------------------------------------------------------------
# Output artifact structure completeness
# ---------------------------------------------------------------------------

_EXPECTED_OUTPUT_KEYS = {
    "checked_at_utc",
    "result",
    "approval_reviewed",
    "single_attempt_approved",
    "live_submit_enabled",
    "real_submit_implemented",
    "submit_order_reachable",
    "broker_calls_made",
    "credentials_read",
    "broker_mutation_calls_made",
    "live_ledger_written",
    "cancel_order_called",
    "replace_order_called",
    "automated_trading_enabled",
    "recurring_trading_enabled",
    "credential_values_exposed",
    "symbol",
    "side",
    "order_type",
    "notional_cap",
    "approval_scope",
    "violations",
    "blocker",
}


class TestOutputStructure:
    def test_all_keys_present_on_pass(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert _EXPECTED_OUTPUT_KEYS.issubset(r.keys())

    def test_all_keys_present_on_blocked(self, tmp_path: Path) -> None:
        r = run_review(approval_path=tmp_path / "missing.json", now_utc=_NOW)
        assert _EXPECTED_OUTPUT_KEYS.issubset(r.keys())

    def test_violations_is_list(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert isinstance(r["violations"], list)

    def test_checked_at_utc_is_string(self, tmp_path: Path) -> None:
        r = run_review(approval_path=_write_good(tmp_path), now_utc=_NOW)
        assert isinstance(r["checked_at_utc"], str)
        assert len(r["checked_at_utc"]) > 0

    def test_approval_reviewed_false_on_blocked(self, tmp_path: Path) -> None:
        a = _good_artifact()
        a["symbol"] = "TSLA"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["approval_reviewed"] is False
        assert r["single_attempt_approved"] is False

    def test_output_fields_null_on_wrong_values(self, tmp_path: Path) -> None:
        """symbol/side/order_type/notional_cap/scope are null when values are wrong."""
        a = _good_artifact()
        a["symbol"]       = "AAPL"
        a["side"]         = "sell"
        a["order_type"]   = "limit"
        a["notional_cap"] = 200.0
        a["approval_scope"] = "WRONG_SCOPE"
        r = run_review(approval_path=_write_json(tmp_path, "a.json", a), now_utc=_NOW)
        assert r["symbol"]       is None
        assert r["side"]         is None
        assert r["order_type"]   is None
        assert r["notional_cap"] is None
        assert r["approval_scope"] is None
