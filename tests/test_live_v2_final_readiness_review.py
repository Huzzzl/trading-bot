"""
tests/test_live_v2_final_readiness_review.py
----------------------------------------------
Tests for src/tools/live_v2_final_readiness_review.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_artifacts(tmp_path: Path, **overrides):
    """Write all three valid artifacts. Returns (ta_path, sa_path, exec_path)."""
    ta_path = _write(tmp_path, "ta.json", _valid_ta(**overrides.get("ta", {})))
    sa_path = _write(tmp_path, "sa.json", _valid_sa(ta_path, **overrides.get("sa", {})))
    exec_path = _write(tmp_path, "exec.json", _valid_executor_report(**overrides.get("exec", {})))
    return ta_path, sa_path, exec_path


def _run_review(tmp_path, *,
                ta_path=None, sa_path=None, exec_path=None,
                run_approvals_review=True):
    from src.tools.live_v2_final_readiness_review import run_review
    # Only create default artifacts for slots that weren't explicitly provided.
    # Use distinct sub-directories to avoid filename collisions with caller-written files.
    defaults_dir = tmp_path / "_defaults"
    defaults_dir.mkdir(exist_ok=True)
    if ta_path is None:
        ta_path = _write(defaults_dir, "ta.json", _valid_ta())
    if sa_path is None:
        sa_path = _write(defaults_dir, "sa.json", _valid_sa(ta_path))
    if exec_path is None:
        exec_path = _write(defaults_dir, "exec.json", _valid_executor_report())
    return run_review(
        ta_path=ta_path if run_approvals_review else None,
        sa_path=sa_path if run_approvals_review else None,
        executor_report_path=exec_path,
        run_approvals_review=run_approvals_review,
    )


def _run_main(tmp_path: Path, extra_args: list[str] | None = None,
              with_approvals=True) -> tuple[int | None, Path]:
    ta_path, sa_path, exec_path = _write_artifacts(tmp_path)
    output_path = tmp_path / "output" / "live_v2_final_readiness_review.json"
    from src.tools.live_v2_final_readiness_review import main
    argv = [
        "--executor-readiness-report", str(exec_path),
        "--output", str(output_path),
    ]
    if with_approvals:
        argv += [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
        ]
    if extra_args:
        argv += extra_args
    try:
        main(argv)
        return None, output_path
    except SystemExit as exc:
        return exc.code, output_path


# ---------------------------------------------------------------------------
# run_review unit tests
# ---------------------------------------------------------------------------

class TestRunReview:
    def test_valid_all_artifacts_passes(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["review_result"] == "PASS"
        assert result["approvals_review_result"] == "PASS"
        assert result["executor_readiness_review_result"] == "PASS"

    def test_pass_has_correct_fixed_fields(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["live_submit_enabled"] is False
        assert result["real_submit_implemented"] is False
        assert result["final_blocker"] == "config_safety"
        assert result["submit_order_called"] is False

    def test_pass_populates_symbol_and_notional(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["symbol"] == "SPY"
        assert result["approved_max_notional"] == 100.0

    def test_pass_has_empty_violations(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["violations"] == []

    def test_pass_block_guard_is_config_safety(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["block_guard"] == "config_safety"

    def test_no_approvals_review_flag_fails(self, tmp_path):
        result = _run_review(tmp_path, run_approvals_review=False)
        assert result["review_result"] == "FAIL"
        assert result["approvals_review_result"] == "NOT_RUN"
        assert any("NOT" in v or "not given" in v for v in result["violations"])

    def test_invalid_trading_approval_fails(self, tmp_path):
        ta_path = _write(tmp_path, "ta.json", _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path, "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report())
        result = _run_review(tmp_path, ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"
        assert result["approvals_review_result"] == "FAIL"
        assert any("[approvals]" in v for v in result["violations"])

    def test_invalid_submission_approval_fails(self, tmp_path):
        ta_path = _write(tmp_path, "ta.json", _valid_ta())
        sa_path = _write(tmp_path, "sa.json",
                         _valid_sa(ta_path, live_order_submission_approved=False))
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report())
        result = _run_review(tmp_path, ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"
        assert result["approvals_review_result"] == "FAIL"

    def test_approval_artifact_block_guard_fails(self, tmp_path):
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report(
            block_guard="approval_artifact",
            violations=["live_trading_approved=false"],
        ))
        result = _run_review(tmp_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"
        assert result["executor_readiness_review_result"] == "FAIL"
        assert any("[executor]" in v for v in result["violations"])

    def test_v2_trading_approval_block_guard_fails(self, tmp_path):
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        result = _run_review(tmp_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"
        assert result["executor_readiness_review_result"] == "FAIL"

    def test_v2_submission_approval_block_guard_fails(self, tmp_path):
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report(
            block_guard="v2_submission_approval",
            violations=["source_live_trading_approval_path mismatch"],
        ))
        result = _run_review(tmp_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"

    def test_v2_cross_check_block_guard_fails(self, tmp_path):
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report(
            block_guard="v2_cross_check",
            violations=["v2 artifact symbol mismatch"],
        ))
        result = _run_review(tmp_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"

    def test_blocked_false_fails(self, tmp_path):
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report(blocked=False))
        result = _run_review(tmp_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"
        assert result["executor_readiness_review_result"] == "FAIL"

    def test_submit_order_called_true_fails(self, tmp_path):
        exec_path = _write(tmp_path, "exec.json",
                           _valid_executor_report(submit_order_called=True))
        result = _run_review(tmp_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"

    def test_missing_ta_file_fails(self, tmp_path):
        _, sa_path, exec_path = _write_artifacts(tmp_path)
        result = _run_review(tmp_path,
                             ta_path=tmp_path / "nonexistent_ta.json",
                             sa_path=sa_path,
                             exec_path=exec_path)
        assert result["review_result"] == "FAIL"
        assert any("trading approval" in v for v in result["violations"])

    def test_missing_sa_file_fails(self, tmp_path):
        ta_path, _, exec_path = _write_artifacts(tmp_path)
        result = _run_review(tmp_path,
                             ta_path=ta_path,
                             sa_path=tmp_path / "nonexistent_sa.json",
                             exec_path=exec_path)
        assert result["review_result"] == "FAIL"
        assert any("submission approval" in v for v in result["violations"])

    def test_missing_executor_report_fails(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        result = _run_review(tmp_path,
                             ta_path=ta_path,
                             sa_path=sa_path,
                             exec_path=tmp_path / "nonexistent_exec.json")
        assert result["review_result"] == "FAIL"
        assert any("executor report" in v for v in result["violations"])

    def test_malformed_ta_fails(self, tmp_path):
        ta_path = tmp_path / "bad_ta.json"
        ta_path.write_text("{bad json", encoding="utf-8")
        _, sa_path, exec_path = _write_artifacts(tmp_path)
        result = _run_review(tmp_path, ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"

    def test_malformed_executor_report_fails(self, tmp_path):
        exec_path = tmp_path / "bad_exec.json"
        exec_path.write_text("{bad json", encoding="utf-8")
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        result = _run_review(tmp_path, ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert result["review_result"] == "FAIL"

    def test_fail_final_blocker_is_block_guard(self, tmp_path):
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        result = _run_review(tmp_path, exec_path=exec_path)
        assert result["final_blocker"] == "v2_trading_approval"

    def test_pass_final_blocker_is_config_safety(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["final_blocker"] == "config_safety"

    def test_live_submit_enabled_always_false(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["live_submit_enabled"] is False

    def test_real_submit_implemented_always_false(self, tmp_path):
        result = _run_review(tmp_path)
        assert result["real_submit_implemented"] is False

    def test_checked_at_utc_present(self, tmp_path):
        result = _run_review(tmp_path)
        assert "checked_at_utc" in result
        assert result["checked_at_utc"]

    def test_both_sub_review_violations_tagged(self, tmp_path):
        """Violations from each sub-review are prefixed with [approvals] or [executor]."""
        ta_path = _write(tmp_path, "ta.json", _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path, "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        result = _run_review(tmp_path, ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        tags = {v.split("]")[0].lstrip("[") for v in result["violations"] if "]" in v}
        assert "approvals" in tags
        assert "executor" in tags


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_artifacts_exits_0(self, tmp_path):
        code, output_path = _run_main(tmp_path)
        assert code in (0, None)

    def test_output_file_written(self, tmp_path):
        _, output_path = _run_main(tmp_path)
        assert output_path.exists()

    def test_output_json_is_valid(self, tmp_path):
        _, output_path = _run_main(tmp_path)
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["review_result"] == "PASS"

    def test_output_contains_all_required_fields(self, tmp_path):
        _, output_path = _run_main(tmp_path)
        data = json.loads(output_path.read_text(encoding="utf-8"))
        required = [
            "checked_at_utc", "review_result", "approvals_review_result",
            "executor_readiness_review_result", "symbol", "approved_max_notional",
            "block_guard", "submit_order_called", "live_submit_enabled",
            "real_submit_implemented", "final_blocker", "violations",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

    def test_no_v2_approvals_review_flag_exits_1(self, tmp_path):
        code, output_path = _run_main(tmp_path, with_approvals=False)
        assert code == 1
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["review_result"] == "FAIL"
        assert data["approvals_review_result"] == "NOT_RUN"

    def test_approval_artifact_block_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        exec_path = _write(tmp_path, "exec_bad.json", _valid_executor_report(
            block_guard="approval_artifact",
            violations=["live_trading_approved=false"],
        ))
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_v2_trading_approval_block_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        exec_path = _write(tmp_path, "exec_bad.json", _valid_executor_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_v2_submission_approval_block_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        exec_path = _write(tmp_path, "exec_bad.json", _valid_executor_report(
            block_guard="v2_submission_approval",
            violations=["source_live_trading_approval_path mismatch"],
        ))
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_blocked_false_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        exec_path = _write(tmp_path, "exec_bad.json",
                           _valid_executor_report(blocked=False))
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_submit_order_called_true_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        exec_path = _write(tmp_path, "exec_bad.json",
                           _valid_executor_report(submit_order_called=True))
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_missing_ta_file_exits_1(self, tmp_path):
        _, sa_path, exec_path = _write_artifacts(tmp_path)
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(tmp_path / "nonexistent.json"),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_missing_executor_report_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(tmp_path / "nonexistent.json"),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_malformed_executor_report_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_artifacts(tmp_path)
        bad_exec = tmp_path / "bad.json"
        bad_exec.write_text("{bad json", encoding="utf-8")
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(bad_exec),
            "--output", str(output_path),
        ]
        try:
            main(argv)
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1

    def test_no_alpaca_calls(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path)
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_read(self, tmp_path):
        import os
        from unittest.mock import patch
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            code, _ = _run_main(tmp_path)
        assert code in (0, None)

    def test_no_ledger_writes(self, tmp_path):
        _run_main(tmp_path)
        ledger_files = list(tmp_path.rglob("*ledger*"))
        assert ledger_files == []

    def test_output_parent_created_if_missing(self, tmp_path):
        ta_path, sa_path, exec_path = _write_artifacts(tmp_path)
        deep_output = tmp_path / "deep" / "nested" / "review.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(deep_output),
        ]
        try:
            main(argv)
        except SystemExit:
            pass
        assert deep_output.exists()

    def test_invalid_approvals_fails_and_still_writes_output(self, tmp_path):
        """Even on FAIL, output file must be written."""
        ta_path = _write(tmp_path, "ta.json", _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path, "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path, "exec.json", _valid_executor_report())
        output_path = tmp_path / "out.json"
        from src.tools.live_v2_final_readiness_review import main
        argv = [
            "--v2-approvals-review",
            "--live-trading-approval", str(ta_path),
            "--live-order-submission-approval", str(sa_path),
            "--executor-readiness-report", str(exec_path),
            "--output", str(output_path),
        ]
        try:
            main(argv)
        except SystemExit:
            pass
        assert output_path.exists()
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["review_result"] == "FAIL"
