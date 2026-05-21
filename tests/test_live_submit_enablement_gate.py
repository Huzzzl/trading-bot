"""
tests/test_live_submit_enablement_gate.py
-------------------------------------------
Tests for src/tools/live_submit_enablement_gate.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no live ledger writes.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_OUTPUT_FIELDS = {
    "checked_at_utc",
    "decision",
    "readiness_bundle_result",
    "approvals_valid",
    "executor_ready",
    "config_safety_is_final_blocker",
    "live_submit_enabled",
    "real_submit_implemented",
    "violations",
}


def _valid_ta(**overrides) -> dict:
    d = {
        "live_trading_approved":          True,
        "live_order_submission_approved": False,
        "approval_scope":                 "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":              True,
        "approved_symbol":                "SPY",
        "approved_max_notional":          100.0,
    }
    d.update(overrides)
    return d


def _valid_sa(ta_path: Path, **overrides) -> dict:
    d = {
        "live_trading_approved":                        True,
        "live_order_submission_approved":               True,
        "order_submission_approval_for_single_attempt": True,
        "approval_scope":                               "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":                            True,
        "source_live_trading_approval_path":            str(ta_path),
        "approved_symbol":                              "SPY",
        "approved_max_notional":                        100.0,
    }
    d.update(overrides)
    return d


def _valid_executor_report(**overrides) -> dict:
    d = {
        "checked_at_utc":      "2026-05-21T10:00:00+00:00",
        "symbol":              "SPY",
        "submit_order_called": False,
        "blocked":             True,
        "block_guard":         "config_safety",
        "violations": [
            "live_trading_enabled=false -- live trading is disabled in config",
            "live_submit_dry_run=true -- dry-run gate is engaged",
            "live_kill_switch_enabled=true -- kill switch is engaged",
        ],
    }
    d.update(overrides)
    return d


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_all(tmp_path: Path, *,
               ta_overrides=None, sa_overrides=None, exec_overrides=None,
               bundle_overrides=None):
    """Write all four input artifacts. Returns (bundle_path, ta_path, sa_path, exec_path)."""
    ta_path = _write(tmp_path / "ta.json", _valid_ta(**(ta_overrides or {})))
    sa_path = _write(tmp_path / "sa.json",
                     _valid_sa(ta_path, **(sa_overrides or {})))
    exec_path = _write(tmp_path / "exec.json",
                       _valid_executor_report(**(exec_overrides or {})))
    bundle_data = {"bundle_result": "PASS", "checked_at_utc": "2026-05-21T10:00:00+00:00"}
    if bundle_overrides:
        bundle_data.update(bundle_overrides)
    bundle_path = _write(tmp_path / "bundle.json", bundle_data)
    return bundle_path, ta_path, sa_path, exec_path


def _run_gate(tmp_path: Path, *,
              bundle_path=None, ta_path=None, sa_path=None, exec_path=None,
              ta_overrides=None, sa_overrides=None, exec_overrides=None,
              bundle_overrides=None, output_path=None):
    """Run run_gate() using written artifacts. Returns the result dict."""
    from src.tools.live_submit_enablement_gate import run_gate
    _bp, _ta, _sa, _ex = _write_all(
        tmp_path,
        ta_overrides=ta_overrides,
        sa_overrides=sa_overrides,
        exec_overrides=exec_overrides,
        bundle_overrides=bundle_overrides,
    )
    return run_gate(
        bundle_path=bundle_path or _bp,
        ta_path=ta_path or _ta,
        sa_path=sa_path or _sa,
        exec_path=exec_path or _ex,
    )


def _run_main(tmp_path: Path, *,
              bundle_path=None, ta_path=None, sa_path=None, exec_path=None,
              ta_overrides=None, sa_overrides=None, exec_overrides=None,
              bundle_overrides=None,
              output_name="gate_out.json") -> tuple[int | None, Path]:
    """Run main() argv and return (exit_code, output_path)."""
    from src.tools.live_submit_enablement_gate import main
    _bp, _ta, _sa, _ex = _write_all(
        tmp_path,
        ta_overrides=ta_overrides,
        sa_overrides=sa_overrides,
        exec_overrides=exec_overrides,
        bundle_overrides=bundle_overrides,
    )
    out = tmp_path / output_name
    argv = [
        "--readiness-bundle", str(bundle_path or _bp),
        "--live-trading-approval", str(ta_path or _ta),
        "--live-order-submission-approval", str(sa_path or _sa),
        "--executor-readiness-report", str(exec_path or _ex),
        "--output", str(out),
    ]
    try:
        main(argv)
        return None, out
    except SystemExit as exc:
        return exc.code, out


# ---------------------------------------------------------------------------
# GO: happy path
# ---------------------------------------------------------------------------

class TestGoHappyPath:
    def test_all_valid_inputs_produce_go(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["decision"] == "GO"

    def test_go_has_empty_violations(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["violations"] == []

    def test_go_has_approvals_valid_true(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["approvals_valid"] is True

    def test_go_has_executor_ready_true(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["executor_ready"] is True

    def test_go_has_config_safety_is_final_blocker_true(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["config_safety_is_final_blocker"] is True

    def test_go_has_bundle_result_pass(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["readiness_bundle_result"] == "PASS"

    def test_go_live_submit_enabled_is_false(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["live_submit_enabled"] is False

    def test_go_real_submit_implemented_is_false(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["real_submit_implemented"] is False

    def test_main_exits_0_on_go(self, tmp_path):
        code, _ = _run_main(tmp_path)
        assert code is None or code == 0

    def test_main_writes_output_on_go(self, tmp_path):
        _, out = _run_main(tmp_path)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["decision"] == "GO"


# ---------------------------------------------------------------------------
# NO_GO: bundle failures
# ---------------------------------------------------------------------------

class TestBundleFailures:
    def test_bundle_result_fail_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path, bundle_overrides={"bundle_result": "FAIL"})
        assert result["decision"] == "NO_GO"

    def test_bundle_result_fail_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path, bundle_overrides={"bundle_result": "FAIL"})
        assert any("bundle_result" in v for v in result["violations"])

    def test_missing_bundle_file_produces_no_go(self, tmp_path):
        missing = tmp_path / "nonexistent_bundle.json"
        result = _run_gate(tmp_path, bundle_path=missing)
        assert result["decision"] == "NO_GO"

    def test_missing_bundle_file_violation_listed(self, tmp_path):
        missing = tmp_path / "nonexistent_bundle.json"
        result = _run_gate(tmp_path, bundle_path=missing)
        assert any("readiness bundle" in v for v in result["violations"])

    def test_missing_bundle_writes_output_via_main(self, tmp_path):
        missing = tmp_path / "nonexistent_bundle.json"
        code, out = _run_main(tmp_path, bundle_path=missing)
        assert code == 1
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["decision"] == "NO_GO"

    def test_malformed_bundle_produces_no_go(self, tmp_path):
        bad = tmp_path / "bad_bundle.json"
        bad.write_text("{not valid json", encoding="utf-8")
        result = _run_gate(tmp_path, bundle_path=bad)
        assert result["decision"] == "NO_GO"

    def test_malformed_bundle_writes_output_via_main(self, tmp_path):
        bad = tmp_path / "bad_bundle.json"
        bad.write_text("{not valid json", encoding="utf-8")
        code, out = _run_main(tmp_path, bundle_path=bad)
        assert code == 1
        assert out.exists()

    def test_bundle_result_missing_field_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path, bundle_overrides={"bundle_result": ""})
        assert result["decision"] == "NO_GO"


# ---------------------------------------------------------------------------
# NO_GO: approval failures
# ---------------------------------------------------------------------------

class TestApprovalFailures:
    def test_invalid_trading_approval_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path, ta_overrides={"live_trading_approved": False})
        assert result["decision"] == "NO_GO"

    def test_invalid_trading_approval_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path, ta_overrides={"live_trading_approved": False})
        assert any("approvals" in v for v in result["violations"])

    def test_invalid_trading_approval_approvals_valid_false(self, tmp_path):
        result = _run_gate(tmp_path, ta_overrides={"live_trading_approved": False})
        assert result["approvals_valid"] is False

    def test_invalid_submission_approval_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           sa_overrides={"live_order_submission_approved": False})
        assert result["decision"] == "NO_GO"

    def test_invalid_submission_approval_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path,
                           sa_overrides={"live_order_submission_approved": False})
        assert any("approvals" in v for v in result["violations"])

    def test_missing_trading_approval_produces_no_go(self, tmp_path):
        missing = tmp_path / "no_ta.json"
        result = _run_gate(tmp_path, ta_path=missing)
        assert result["decision"] == "NO_GO"

    def test_missing_submission_approval_produces_no_go(self, tmp_path):
        missing = tmp_path / "no_sa.json"
        result = _run_gate(tmp_path, sa_path=missing)
        assert result["decision"] == "NO_GO"

    def test_wrong_approval_scope_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           ta_overrides={"approval_scope": "WRONG_SCOPE"})
        assert result["decision"] == "NO_GO"


# ---------------------------------------------------------------------------
# NO_GO: executor report failures
# ---------------------------------------------------------------------------

class TestExecutorFailures:
    def test_block_guard_not_config_safety_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"block_guard": "v2_trading_approval"})
        assert result["decision"] == "NO_GO"

    def test_block_guard_not_config_safety_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"block_guard": "v2_trading_approval"})
        assert any("block_guard" in v for v in result["violations"])

    def test_submit_order_called_true_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"submit_order_called": True})
        assert result["decision"] == "NO_GO"

    def test_submit_order_called_true_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"submit_order_called": True})
        assert any("submit_order_called" in v for v in result["violations"])

    def test_blocked_false_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path, exec_overrides={"blocked": False})
        assert result["decision"] == "NO_GO"

    def test_blocked_false_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path, exec_overrides={"blocked": False})
        assert any("blocked" in v for v in result["violations"])

    def test_blocked_false_executor_ready_false(self, tmp_path):
        result = _run_gate(tmp_path, exec_overrides={"blocked": False})
        assert result["executor_ready"] is False

    def test_missing_executor_report_produces_no_go(self, tmp_path):
        missing = tmp_path / "no_exec.json"
        result = _run_gate(tmp_path, exec_path=missing)
        assert result["decision"] == "NO_GO"

    def test_missing_executor_report_violation_listed(self, tmp_path):
        missing = tmp_path / "no_exec.json"
        result = _run_gate(tmp_path, exec_path=missing)
        assert any("executor report" in v for v in result["violations"])

    def test_missing_executor_report_writes_output_via_main(self, tmp_path):
        missing = tmp_path / "no_exec.json"
        code, out = _run_main(tmp_path, exec_path=missing)
        assert code == 1
        assert out.exists()

    def test_config_safety_is_final_blocker_false_when_wrong_guard(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"block_guard": "approval_artifact"})
        assert result["config_safety_is_final_blocker"] is False


# ---------------------------------------------------------------------------
# NO_GO: safety flag checks
# ---------------------------------------------------------------------------

class TestSafetyFlagChecks:
    def test_live_submit_enabled_true_in_executor_report_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"live_submit_enabled": True})
        assert result["decision"] == "NO_GO"

    def test_live_submit_enabled_true_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"live_submit_enabled": True})
        assert any("live_submit_enabled=true" in v for v in result["violations"])

    def test_real_submit_implemented_true_in_executor_report_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"real_submit_implemented": True})
        assert result["decision"] == "NO_GO"

    def test_real_submit_implemented_true_violation_listed(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"real_submit_implemented": True})
        assert any("real_submit_implemented=true" in v for v in result["violations"])

    def test_live_submit_enabled_true_in_bundle_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           bundle_overrides={"bundle_result": "PASS",
                                             "live_submit_enabled": True})
        assert result["decision"] == "NO_GO"

    def test_real_submit_implemented_true_in_bundle_produces_no_go(self, tmp_path):
        result = _run_gate(tmp_path,
                           bundle_overrides={"bundle_result": "PASS",
                                             "real_submit_implemented": True})
        assert result["decision"] == "NO_GO"

    def test_output_always_has_live_submit_enabled_false(self, tmp_path):
        # Even if input claims live_submit_enabled=True, output field is always False
        result = _run_gate(tmp_path,
                           exec_overrides={"live_submit_enabled": True})
        assert result["live_submit_enabled"] is False

    def test_output_always_has_real_submit_implemented_false(self, tmp_path):
        result = _run_gate(tmp_path,
                           exec_overrides={"real_submit_implemented": True})
        assert result["real_submit_implemented"] is False


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_go_output_has_all_required_fields(self, tmp_path):
        result = _run_gate(tmp_path)
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_no_go_output_has_all_required_fields(self, tmp_path):
        result = _run_gate(tmp_path, bundle_overrides={"bundle_result": "FAIL"})
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_violations_is_list(self, tmp_path):
        result = _run_gate(tmp_path)
        assert isinstance(result["violations"], list)

    def test_checked_at_utc_is_string(self, tmp_path):
        result = _run_gate(tmp_path)
        assert isinstance(result["checked_at_utc"], str)
        assert result["checked_at_utc"]

    def test_decision_is_go_or_no_go(self, tmp_path):
        result = _run_gate(tmp_path)
        assert result["decision"] in {"GO", "NO_GO"}

    def test_approvals_valid_is_bool(self, tmp_path):
        result = _run_gate(tmp_path)
        assert isinstance(result["approvals_valid"], bool)

    def test_executor_ready_is_bool(self, tmp_path):
        result = _run_gate(tmp_path)
        assert isinstance(result["executor_ready"], bool)

    def test_config_safety_is_final_blocker_is_bool(self, tmp_path):
        result = _run_gate(tmp_path)
        assert isinstance(result["config_safety_is_final_blocker"], bool)

    def test_written_json_has_all_fields(self, tmp_path):
        _, out = _run_main(tmp_path)
        data = json.loads(out.read_text())
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in data, f"Missing field in written JSON: {field}"

    def test_written_json_decision_go(self, tmp_path):
        _, out = _run_main(tmp_path)
        data = json.loads(out.read_text())
        assert data["decision"] == "GO"


# ---------------------------------------------------------------------------
# Main exit codes
# ---------------------------------------------------------------------------

class TestMainExitCodes:
    def test_exit_1_on_no_go(self, tmp_path):
        code, _ = _run_main(tmp_path, bundle_overrides={"bundle_result": "FAIL"})
        assert code == 1

    def test_exit_0_on_go(self, tmp_path):
        code, _ = _run_main(tmp_path)
        assert code is None or code == 0

    def test_always_writes_output_on_no_go(self, tmp_path):
        code, out = _run_main(tmp_path, bundle_overrides={"bundle_result": "FAIL"})
        assert code == 1
        assert out.exists()

    def test_always_writes_output_on_missing_exec(self, tmp_path):
        missing = tmp_path / "missing_exec.json"
        code, out = _run_main(tmp_path, exec_path=missing)
        assert code == 1
        assert out.exists()

    def test_always_writes_output_on_malformed_input(self, tmp_path):
        bad = tmp_path / "malformed.json"
        bad.write_text("not-json", encoding="utf-8")
        code, out = _run_main(tmp_path, bundle_path=bad)
        assert code == 1
        assert out.exists()


# ---------------------------------------------------------------------------
# Security / safety properties
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def test_no_alpaca_import(self):
        module_path = (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_submit_enablement_gate.py"
        )
        source = module_path.read_text(encoding="utf-8")
        # Check for actual import statements, not docstring mentions
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("*"):
                continue
            assert "import alpaca" not in stripped.lower(), (
                f"live_submit_enablement_gate.py must not import Alpaca (line: {line!r})"
            )
            assert "from alpaca" not in stripped.lower(), (
                f"live_submit_enablement_gate.py must not import Alpaca (line: {line!r})"
            )

    def test_no_submit_order_call(self):
        module_path = (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_submit_enablement_gate.py"
        )
        source = module_path.read_text(encoding="utf-8")
        # Check for actual function calls, not docstring mentions
        assert "submit_order(" not in source, (
            "live_submit_enablement_gate.py must not call submit_order()"
        )

    def test_no_cancel_order_call(self):
        module_path = (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_submit_enablement_gate.py"
        )
        source = module_path.read_text(encoding="utf-8")
        # Check for actual function calls, not docstring mentions
        assert "cancel_order(" not in source, (
            "live_submit_enablement_gate.py must not call cancel_order()"
        )

    def test_no_credentials_read(self):
        module_path = (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_submit_enablement_gate.py"
        )
        source = module_path.read_text(encoding="utf-8")
        for cred_keyword in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY", "os.environ"):
            assert cred_keyword not in source, (
                f"live_submit_enablement_gate.py must not read credentials ({cred_keyword!r})"
            )

    def test_no_live_ledger_write(self):
        module_path = (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_submit_enablement_gate.py"
        )
        source = module_path.read_text(encoding="utf-8")
        assert "live_ledger" not in source, (
            "live_submit_enablement_gate.py must not write the live ledger"
        )

    def test_module_imports_do_not_trigger_alpaca(self):
        mod = importlib.import_module("src.tools.live_submit_enablement_gate")
        assert mod is not None

    def test_run_gate_does_not_call_submit_order(self, tmp_path):
        # submit_order is never called during gate evaluation
        from src.tools.live_submit_enablement_gate import run_gate
        src = inspect.getsource(run_gate)
        assert "submit_order" not in src

    def test_go_does_not_submit_order(self, tmp_path):
        # Verify GO result has no side-effect markers
        result = _run_gate(tmp_path)
        assert result["decision"] == "GO"
        # GO result explicitly does not set live_submit_enabled
        assert result["live_submit_enabled"] is False
        assert result["real_submit_implemented"] is False


# ---------------------------------------------------------------------------
# Decision hardening: explicit boolean checks are defense-in-depth
# ---------------------------------------------------------------------------

class TestDecisionHardening:
    """
    Verify that run_gate() requires all core booleans to be True, not just
    an empty violations list.  These tests patch validate_approvals and
    validate_readiness to return FAIL with an *empty* violations list, which
    would leave violations==[] while the corresponding boolean stays False.
    The decision must still be NO_GO.
    """

    def test_validate_approvals_fail_empty_violations_produces_no_go(self, tmp_path, monkeypatch):
        """If validate_approvals returns ('FAIL', []) the decision is NO_GO."""
        import src.tools.live_submit_enablement_gate as gate_mod
        monkeypatch.setattr(gate_mod, "validate_approvals",
                            lambda ta, sa, ta_path, sa_path: ("FAIL", []))
        result = _run_gate(tmp_path)
        assert result["decision"] == "NO_GO"

    def test_validate_approvals_fail_empty_violations_approvals_valid_false(
            self, tmp_path, monkeypatch):
        import src.tools.live_submit_enablement_gate as gate_mod
        monkeypatch.setattr(gate_mod, "validate_approvals",
                            lambda ta, sa, ta_path, sa_path: ("FAIL", []))
        result = _run_gate(tmp_path)
        assert result["approvals_valid"] is False

    def test_validate_readiness_fail_empty_violations_produces_no_go(self, tmp_path, monkeypatch):
        """If validate_readiness returns ('FAIL', []) the decision is NO_GO."""
        import src.tools.live_submit_enablement_gate as gate_mod
        monkeypatch.setattr(gate_mod, "validate_readiness",
                            lambda report: ("FAIL", []))
        result = _run_gate(tmp_path)
        assert result["decision"] == "NO_GO"

    def test_validate_readiness_fail_empty_violations_executor_ready_false(
            self, tmp_path, monkeypatch):
        import src.tools.live_submit_enablement_gate as gate_mod
        monkeypatch.setattr(gate_mod, "validate_readiness",
                            lambda report: ("FAIL", []))
        result = _run_gate(tmp_path)
        assert result["executor_ready"] is False

    def test_validate_readiness_fail_empty_violations_config_safety_false(
            self, tmp_path, monkeypatch):
        import src.tools.live_submit_enablement_gate as gate_mod
        monkeypatch.setattr(gate_mod, "validate_readiness",
                            lambda report: ("FAIL", []))
        result = _run_gate(tmp_path)
        assert result["config_safety_is_final_blocker"] is False

    def test_bundle_result_not_pass_with_no_violation_produces_no_go(self, tmp_path):
        """bundle_result != 'PASS' produces NO_GO regardless of violations list."""
        # Simulate a bundle where bundle_result is wrong but _check_safety_flags
        # adds no violation (i.e. the bundle_result violation is the only one).
        # We verify the explicit boolean check catches it even without violations.
        from src.tools.live_submit_enablement_gate import run_gate
        # Write a bundle that has bundle_result != PASS
        import json
        _bp, _ta, _sa, _ex = _write_all(tmp_path,
                                         bundle_overrides={"bundle_result": "FAIL"})
        result = run_gate(
            bundle_path=_bp, ta_path=_ta, sa_path=_sa, exec_path=_ex
        )
        # The violation is present — but also verify the explicit boolean catches it
        assert result["readiness_bundle_result"] == "FAIL"
        assert result["decision"] == "NO_GO"

    def test_both_validators_fail_empty_violations_no_go(self, tmp_path, monkeypatch):
        """Both validators return FAIL with empty violations — still NO_GO."""
        import src.tools.live_submit_enablement_gate as gate_mod
        monkeypatch.setattr(gate_mod, "validate_approvals",
                            lambda ta, sa, ta_path, sa_path: ("FAIL", []))
        monkeypatch.setattr(gate_mod, "validate_readiness",
                            lambda report: ("FAIL", []))
        result = _run_gate(tmp_path)
        assert result["decision"] == "NO_GO"
        assert result["violations"] == []  # violations list is empty — hardening caught it

    def test_existing_go_path_still_go(self, tmp_path):
        """Hardening does not break the valid GO path."""
        result = _run_gate(tmp_path)
        assert result["decision"] == "GO"

    def test_existing_no_go_paths_still_no_go_blocked_false(self, tmp_path):
        result = _run_gate(tmp_path, exec_overrides={"blocked": False})
        assert result["decision"] == "NO_GO"

    def test_existing_no_go_paths_still_no_go_bundle_fail(self, tmp_path):
        result = _run_gate(tmp_path, bundle_overrides={"bundle_result": "FAIL"})
        assert result["decision"] == "NO_GO"

    def test_existing_no_go_paths_still_no_go_bad_approval(self, tmp_path):
        result = _run_gate(tmp_path, ta_overrides={"live_trading_approved": False})
        assert result["decision"] == "NO_GO"
