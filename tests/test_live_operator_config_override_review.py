"""
Tests for src/tools/live_operator_config_override_review.py

Coverage:
- Missing override file → BLOCKED
- Malformed JSON → BLOCKED
- Non-dict JSON → BLOCKED
- Missing required acknowledgement fields → BLOCKED (each individually)
- Wrong symbol / notional / side / scope → BLOCKED
- recurring_trading_approved=true → BLOCKED
- automated_trading_approved=true → BLOCKED
- Empty operator_name / approval_note → BLOCKED
- Correct offline override artifact → PASS
- Output structure invariants (live_submit_enabled=False, etc.)
- Exit codes via main()
- Security properties: no broker calls, no submit_order path
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.tools.live_operator_config_override_review import (
    _read_artifact,
    validate_override,
    run_review,
    main,
    _REQUIRED_SCOPE,
    _REQUIRED_SYMBOL,
    _REQUIRED_SIDE,
    _MAX_NOTIONAL_CAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_artifact(tmp_path: Path, data: Any) -> Path:
    p = tmp_path / "live_operator_config_override.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _valid_artifact() -> dict[str, Any]:
    return {
        "config_safety_acknowledged": True,
        "submit_order_unreachable_acknowledged": True,
        "real_live_submit_unimplemented_acknowledged": True,
        "approval_scope": "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "symbol": "SPY",
        "side": "buy",
        "notional_cap": 100.0,
        "recurring_trading_approved": False,
        "automated_trading_approved": False,
        "operator_name": "test-operator",
        "approval_note": "Test single SPY buy attempt, $100 cap.",
    }


def _output_path(tmp_path: Path) -> Path:
    return tmp_path / "out" / "review.json"


# ---------------------------------------------------------------------------
# _read_artifact
# ---------------------------------------------------------------------------

class TestReadArtifact:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            _read_artifact(tmp_path / "missing.json")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            _read_artifact(p)

    def test_non_dict_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a JSON object"):
            _read_artifact(p)

    def test_valid_dict_returns_data(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        data = _read_artifact(p)
        assert data["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# validate_override — happy path
# ---------------------------------------------------------------------------

class TestValidateOverridePass:
    def test_valid_artifact_passes(self) -> None:
        result, violations, reviewed = validate_override(_valid_artifact())
        assert result == "PASS"
        assert violations == []
        assert reviewed is True

    def test_notional_cap_at_boundary(self) -> None:
        a = _valid_artifact()
        a["notional_cap"] = 100.0
        result, violations, _ = validate_override(a)
        assert result == "PASS"
        assert violations == []

    def test_notional_cap_below_max(self) -> None:
        a = _valid_artifact()
        a["notional_cap"] = 50.0
        result, violations, _ = validate_override(a)
        assert result == "PASS"
        assert violations == []

    def test_symbol_lowercase_accepted(self) -> None:
        a = _valid_artifact()
        a["symbol"] = "spy"
        result, violations, _ = validate_override(a)
        assert result == "PASS"

    def test_symbol_mixed_case_accepted(self) -> None:
        a = _valid_artifact()
        a["symbol"] = "Spy"
        result, violations, _ = validate_override(a)
        assert result == "PASS"

    def test_boolean_false_recurring_passes(self) -> None:
        a = _valid_artifact()
        a["recurring_trading_approved"] = False
        result, violations, _ = validate_override(a)
        assert result == "PASS"

    def test_boolean_false_automated_passes(self) -> None:
        a = _valid_artifact()
        a["automated_trading_approved"] = False
        result, violations, _ = validate_override(a)
        assert result == "PASS"


# ---------------------------------------------------------------------------
# validate_override — acknowledgement failures
# ---------------------------------------------------------------------------

class TestAcknowledgements:
    def test_config_safety_ack_missing(self) -> None:
        a = _valid_artifact()
        del a["config_safety_acknowledged"]
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert any("config_safety_acknowledged" in v for v in violations)
        assert reviewed is False

    def test_config_safety_ack_false(self) -> None:
        a = _valid_artifact()
        a["config_safety_acknowledged"] = False
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert any("config_safety_acknowledged" in v for v in violations)
        assert reviewed is False

    def test_submit_order_unreachable_ack_missing(self) -> None:
        a = _valid_artifact()
        del a["submit_order_unreachable_acknowledged"]
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert any("submit_order_unreachable_acknowledged" in v for v in violations)
        assert reviewed is False

    def test_submit_order_unreachable_ack_false(self) -> None:
        a = _valid_artifact()
        a["submit_order_unreachable_acknowledged"] = False
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    def test_real_live_submit_unimplemented_ack_missing(self) -> None:
        a = _valid_artifact()
        del a["real_live_submit_unimplemented_acknowledged"]
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert any("real_live_submit_unimplemented_acknowledged" in v for v in violations)
        assert reviewed is False

    def test_real_live_submit_unimplemented_ack_false(self) -> None:
        a = _valid_artifact()
        a["real_live_submit_unimplemented_acknowledged"] = False
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    def test_all_acks_false_all_reported(self) -> None:
        a = _valid_artifact()
        a["config_safety_acknowledged"] = False
        a["submit_order_unreachable_acknowledged"] = False
        a["real_live_submit_unimplemented_acknowledged"] = False
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert len([v for v in violations if "acknowledged" in v]) == 3
        assert reviewed is False


# ---------------------------------------------------------------------------
# validate_override — scope / symbol / side / notional failures
# ---------------------------------------------------------------------------

class TestFieldValidation:
    def test_wrong_approval_scope(self) -> None:
        a = _valid_artifact()
        a["approval_scope"] = "WRONG_SCOPE"
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("approval_scope" in v for v in violations)

    def test_missing_approval_scope(self) -> None:
        a = _valid_artifact()
        del a["approval_scope"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("approval_scope" in v for v in violations)

    def test_wrong_symbol(self) -> None:
        a = _valid_artifact()
        a["symbol"] = "AAPL"
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("symbol" in v for v in violations)

    def test_missing_symbol(self) -> None:
        a = _valid_artifact()
        del a["symbol"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    def test_wrong_side(self) -> None:
        a = _valid_artifact()
        a["side"] = "sell"
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("side" in v for v in violations)

    def test_side_buy_uppercase_rejected(self) -> None:
        a = _valid_artifact()
        a["side"] = "BUY"
        # "BUY".lower() == "buy" → should pass
        result, violations, _ = validate_override(a)
        assert result == "PASS"

    def test_missing_side(self) -> None:
        a = _valid_artifact()
        del a["side"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    def test_notional_cap_zero(self) -> None:
        a = _valid_artifact()
        a["notional_cap"] = 0.0
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("notional_cap" in v for v in violations)

    def test_notional_cap_negative(self) -> None:
        a = _valid_artifact()
        a["notional_cap"] = -10.0
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    def test_notional_cap_exceeds_max(self) -> None:
        a = _valid_artifact()
        a["notional_cap"] = 200.0
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("100" in v for v in violations)

    def test_notional_cap_missing(self) -> None:
        a = _valid_artifact()
        del a["notional_cap"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("notional_cap" in v for v in violations)

    def test_notional_cap_non_numeric(self) -> None:
        a = _valid_artifact()
        a["notional_cap"] = "one hundred"
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    def test_recurring_trading_approved_true(self) -> None:
        a = _valid_artifact()
        a["recurring_trading_approved"] = True
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("recurring_trading_approved" in v for v in violations)

    def test_automated_trading_approved_true(self) -> None:
        a = _valid_artifact()
        a["automated_trading_approved"] = True
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("automated_trading_approved" in v for v in violations)

    def test_empty_operator_name(self) -> None:
        a = _valid_artifact()
        a["operator_name"] = ""
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("operator_name" in v for v in violations)

    def test_missing_operator_name(self) -> None:
        a = _valid_artifact()
        del a["operator_name"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    def test_empty_approval_note(self) -> None:
        a = _valid_artifact()
        a["approval_note"] = "   "
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("approval_note" in v for v in violations)

    def test_missing_approval_note(self) -> None:
        a = _valid_artifact()
        del a["approval_note"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"

    # --- absent no-trading fields are now BLOCKED (not treated as false) ---

    def test_absent_recurring_is_blocked(self) -> None:
        a = _valid_artifact()
        del a["recurring_trading_approved"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("recurring_trading_approved" in v for v in violations)

    def test_absent_automated_is_blocked(self) -> None:
        a = _valid_artifact()
        del a["automated_trading_approved"]
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("automated_trading_approved" in v for v in violations)


# ---------------------------------------------------------------------------
# Boolean hardening — string values rejected for all five boolean fields
# ---------------------------------------------------------------------------

class TestBooleanHardening:
    """String representations of booleans must be rejected.
    Only JSON boolean true/false is accepted for safety-critical fields.
    """

    # acknowledgement fields — must be JSON boolean true exactly

    @pytest.mark.parametrize("value", ["true", "True", "yes", "1", 1, "false", "0"])
    def test_config_safety_ack_string_blocked(self, value: Any) -> None:
        a = _valid_artifact()
        a["config_safety_acknowledged"] = value
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert any("config_safety_acknowledged" in v for v in violations)
        assert reviewed is False

    @pytest.mark.parametrize("value", ["true", "True", "yes", "1", 1, "false", "0"])
    def test_submit_order_unreachable_ack_string_blocked(self, value: Any) -> None:
        a = _valid_artifact()
        a["submit_order_unreachable_acknowledged"] = value
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert any("submit_order_unreachable_acknowledged" in v for v in violations)
        assert reviewed is False

    @pytest.mark.parametrize("value", ["true", "True", "yes", "1", 1, "false", "0"])
    def test_real_live_submit_unimplemented_ack_string_blocked(self, value: Any) -> None:
        a = _valid_artifact()
        a["real_live_submit_unimplemented_acknowledged"] = value
        result, violations, reviewed = validate_override(a)
        assert result == "BLOCKED"
        assert any("real_live_submit_unimplemented_acknowledged" in v for v in violations)
        assert reviewed is False

    # no-trading fields — must be JSON boolean false exactly

    @pytest.mark.parametrize("value", ["false", "False", "no", "0", 0, "true", "1", None])
    def test_recurring_trading_approved_string_blocked(self, value: Any) -> None:
        a = _valid_artifact()
        a["recurring_trading_approved"] = value
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("recurring_trading_approved" in v for v in violations)

    @pytest.mark.parametrize("value", ["false", "False", "no", "0", 0, "true", "1", None])
    def test_automated_trading_approved_string_blocked(self, value: Any) -> None:
        a = _valid_artifact()
        a["automated_trading_approved"] = value
        result, violations, _ = validate_override(a)
        assert result == "BLOCKED"
        assert any("automated_trading_approved" in v for v in violations)


# ---------------------------------------------------------------------------
# run_review — integration
# ---------------------------------------------------------------------------

class TestRunReview:
    def test_missing_file_blocked(self, tmp_path: Path) -> None:
        result = run_review(override_path=tmp_path / "missing.json")
        assert result["result"] == "BLOCKED"
        assert result["blocker"] is not None
        assert "not found" in result["blocker"]

    def test_malformed_json_blocked(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{broken", encoding="utf-8")
        result = run_review(override_path=p)
        assert result["result"] == "BLOCKED"
        assert result["blocker"] is not None

    def test_valid_artifact_pass(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["result"] == "PASS"
        assert result["violations"] == []
        assert result["blocker"] is None

    def test_wrong_symbol_blocked(self, tmp_path: Path) -> None:
        a = _valid_artifact()
        a["symbol"] = "AAPL"
        p = _write_artifact(tmp_path, a)
        result = run_review(override_path=p)
        assert result["result"] == "BLOCKED"
        assert result["blocker"] is not None

    def test_missing_ack_blocked(self, tmp_path: Path) -> None:
        a = _valid_artifact()
        del a["config_safety_acknowledged"]
        p = _write_artifact(tmp_path, a)
        result = run_review(override_path=p)
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Output structure invariants
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_required_fields_present_on_pass(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        required = {
            "checked_at_utc",
            "result",
            "config_safety_override_reviewed",
            "live_submit_enabled",
            "real_submit_implemented",
            "submit_order_reachable",
            "broker_calls_made",
            "credentials_read",
            "violations",
            "blocker",
        }
        assert required.issubset(result.keys())

    def test_required_fields_present_on_blocked(self, tmp_path: Path) -> None:
        result = run_review(override_path=tmp_path / "missing.json")
        required = {
            "checked_at_utc",
            "result",
            "config_safety_override_reviewed",
            "live_submit_enabled",
            "real_submit_implemented",
            "submit_order_reachable",
            "broker_calls_made",
            "credentials_read",
            "violations",
            "blocker",
        }
        assert required.issubset(result.keys())

    def test_live_submit_enabled_always_false(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["live_submit_enabled"] is False

    def test_real_submit_implemented_always_false(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["real_submit_implemented"] is False

    def test_submit_order_reachable_always_false(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["submit_order_reachable"] is False

    def test_broker_calls_made_always_false(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["broker_calls_made"] is False

    def test_credentials_read_always_false(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["credentials_read"] is False

    def test_safety_flags_always_false_on_blocked(self, tmp_path: Path) -> None:
        result = run_review(override_path=tmp_path / "missing.json")
        assert result["live_submit_enabled"] is False
        assert result["real_submit_implemented"] is False
        assert result["submit_order_reachable"] is False
        assert result["broker_calls_made"] is False
        assert result["credentials_read"] is False

    def test_blocker_is_first_violation(self, tmp_path: Path) -> None:
        a = _valid_artifact()
        a["config_safety_acknowledged"] = False
        p = _write_artifact(tmp_path, a)
        result = run_review(override_path=p)
        assert result["blocker"] == result["violations"][0]

    def test_violations_is_list(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert isinstance(result["violations"], list)

    def test_config_safety_reviewed_true_on_pass(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["config_safety_override_reviewed"] is True

    def test_config_safety_reviewed_false_on_missing_acks(self, tmp_path: Path) -> None:
        a = _valid_artifact()
        a["config_safety_acknowledged"] = False
        p = _write_artifact(tmp_path, a)
        result = run_review(override_path=p)
        assert result["config_safety_override_reviewed"] is False


# ---------------------------------------------------------------------------
# Exit codes via main()
# ---------------------------------------------------------------------------

class TestExitCodes:
    def test_pass_returns_normally(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        out = _output_path(tmp_path)
        # PASS: main() returns normally, no SystemExit
        main([
            "--override-artifact", str(p),
            "--output", str(out),
        ])

    def test_blocked_exits_one(self, tmp_path: Path) -> None:
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--override-artifact", str(tmp_path / "missing.json"),
                "--output", str(out),
            ])
        assert exc_info.value.code == 1

    def test_always_writes_output_on_pass(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        out = _output_path(tmp_path)
        main(["--override-artifact", str(p), "--output", str(out)])
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["result"] == "PASS"

    def test_always_writes_output_on_blocked(self, tmp_path: Path) -> None:
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--override-artifact", str(tmp_path / "missing.json"),
                "--output", str(out),
            ])
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"

    def test_output_dir_created(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        out = tmp_path / "nested" / "deep" / "review.json"
        main(["--override-artifact", str(p), "--output", str(out)])
        assert out.exists()

    def test_output_json_fields_complete(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        out = _output_path(tmp_path)
        main(["--override-artifact", str(p), "--output", str(out)])
        data = json.loads(out.read_text())
        for field in (
            "checked_at_utc", "result", "config_safety_override_reviewed",
            "live_submit_enabled", "real_submit_implemented",
            "submit_order_reachable", "broker_calls_made", "credentials_read",
            "violations", "blocker",
        ):
            assert field in data, f"missing field: {field}"


# ---------------------------------------------------------------------------
# Security properties
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def test_no_alpaca_import(self) -> None:
        import src.tools.live_operator_config_override_review as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
            and not line.strip().startswith("#")
        ]
        for line in import_lines:
            assert "alpaca" not in line.lower(), (
                f"alpaca found in import line: {line!r}"
            )

    def test_no_submit_order_call(self) -> None:
        import src.tools.live_operator_config_override_review as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith(("#", '"""', "*"))
        ]
        for line in code_lines:
            assert "submit_order(" not in line, (
                f"submit_order( call found in: {line!r}"
            )

    def test_no_cancel_order_call(self) -> None:
        import src.tools.live_operator_config_override_review as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith(("#", '"""', "*"))
        ]
        for line in code_lines:
            assert "cancel_order(" not in line, (
                f"cancel_order( call found in: {line!r}"
            )

    def test_no_live_ledger_write(self) -> None:
        import src.tools.live_operator_config_override_review as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith(("#", '"""', "*"))
        ]
        for line in code_lines:
            assert "append_live_ledger_row" not in line, (
                f"live ledger write call found in: {line!r}"
            )

    def test_no_credential_reads(self) -> None:
        import src.tools.live_operator_config_override_review as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        for keyword in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY"):
            assert keyword not in source, (
                f"credential reference found: {keyword!r}"
            )

    def test_live_submit_enabled_always_false_in_output(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["live_submit_enabled"] is False

    def test_broker_calls_made_always_false(self, tmp_path: Path) -> None:
        p = _write_artifact(tmp_path, _valid_artifact())
        result = run_review(override_path=p)
        assert result["broker_calls_made"] is False

    def test_run_review_never_raises(self, tmp_path: Path) -> None:
        # Should never raise regardless of input
        for override_path in [
            tmp_path / "missing.json",
            tmp_path / "empty",
        ]:
            result = run_review(override_path=override_path)
            assert "result" in result

    def test_does_not_import_requests_or_httpx(self) -> None:
        import src.tools.live_operator_config_override_review as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            for net_lib in ("requests", "httpx", "urllib.request", "aiohttp"):
                assert net_lib not in line, (
                    f"network library {net_lib!r} imported in: {line!r}"
                )

    def test_constants_unchanged(self) -> None:
        assert _REQUIRED_SCOPE == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
        assert _REQUIRED_SYMBOL == "SPY"
        assert _REQUIRED_SIDE == "buy"
        assert _MAX_NOTIONAL_CAP == 100.0
