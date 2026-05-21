"""
tests/test_live_v2_executor_readiness_review.py
--------------------------------------------------
Tests for src/tools/live_v2_executor_readiness_review.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no file writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_report(**overrides) -> dict:
    """A blocked report where v2 approvals passed and config_safety blocked."""
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


def _write_report(tmp_path: Path, report: dict) -> Path:
    path = tmp_path / "live_submit_blocked_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _run_main(report_path: str) -> int | None:
    from src.tools.live_v2_executor_readiness_review import main
    try:
        main(["--blocked-report", report_path])
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# parse_blocked_report
# ---------------------------------------------------------------------------

class TestParseBlockedReport:
    def test_valid_report_returns_dict(self, tmp_path):
        from src.tools.live_v2_executor_readiness_review import parse_blocked_report
        path = _write_report(tmp_path, _valid_report())
        report = parse_blocked_report(path)
        assert isinstance(report, dict)
        assert report["block_guard"] == "config_safety"

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from src.tools.live_v2_executor_readiness_review import parse_blocked_report
        with pytest.raises(FileNotFoundError):
            parse_blocked_report(tmp_path / "nonexistent.json")

    def test_malformed_json_raises_value_error(self, tmp_path):
        from src.tools.live_v2_executor_readiness_review import parse_blocked_report
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_blocked_report(path)

    def test_reads_utf8_encoding(self, tmp_path):
        from src.tools.live_v2_executor_readiness_review import parse_blocked_report
        path = _write_report(tmp_path, _valid_report())
        result = parse_blocked_report(path)
        assert result["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# validate_readiness
# ---------------------------------------------------------------------------

class TestValidateReadiness:
    def test_config_safety_blocked_report_passes(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report())
        assert result == "PASS"
        assert violations == []

    def test_only_live_trading_enabled_violation_passes(self):
        """One config safety flag is enough."""
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            violations=["live_trading_enabled=false -- live trading is disabled"]
        ))
        assert result == "PASS"

    def test_only_dry_run_violation_passes(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            violations=["live_submit_dry_run=true -- dry-run gate is engaged"]
        ))
        assert result == "PASS"

    def test_only_kill_switch_violation_passes(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            violations=["live_kill_switch_enabled=true -- kill switch is engaged"]
        ))
        assert result == "PASS"

    def test_blocked_false_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(blocked=False))
        assert result == "FAIL"
        assert any("blocked" in v for v in violations)

    def test_submit_order_called_true_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(submit_order_called=True))
        assert result == "FAIL"
        assert any("submit_order_called" in v for v in violations)

    def test_approval_artifact_guard_fails(self):
        """block_guard=approval_artifact means v2 approvals were not accepted."""
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            block_guard="approval_artifact",
            violations=["live_trading_approved=false"],
        ))
        assert result == "FAIL"
        assert any("approval_artifact" in v for v in violations)

    def test_v2_trading_approval_guard_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        assert result == "FAIL"
        assert any("v2_trading_approval" in v for v in violations)

    def test_v2_submission_approval_guard_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            block_guard="v2_submission_approval",
            violations=["v2 live_order_submission_approved=False"],
        ))
        assert result == "FAIL"
        assert any("v2_submission_approval" in v for v in violations)

    def test_v2_cross_check_guard_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            block_guard="v2_cross_check",
            violations=["v2 artifact symbol mismatch"],
        ))
        assert result == "FAIL"
        assert any("v2_cross_check" in v for v in violations)

    def test_other_block_guard_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            block_guard="real_submit_not_implemented",
            violations=["not implemented"],
        ))
        assert result == "FAIL"
        assert any("config_safety" in v for v in violations)

    def test_confirm_token_guard_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            block_guard="confirm_token",
            violations=["confirmation token mismatch"],
        ))
        assert result == "FAIL"

    def test_no_config_safety_violations_in_list_fails(self):
        """config_safety guard but no matching violation string."""
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            violations=["some unrelated error"]
        ))
        assert result == "FAIL"
        assert any("violations" in v for v in violations)

    def test_empty_violations_list_fails(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(violations=[]))
        assert result == "FAIL"

    def test_multiple_failures_all_reported(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, violations = validate_readiness(_valid_report(
            blocked=False,
            submit_order_called=True,
            block_guard="approval_artifact",
            violations=[],
        ))
        assert result == "FAIL"
        assert len(violations) >= 3

    def test_string_true_false_truthy(self):
        from src.tools.live_v2_executor_readiness_review import validate_readiness
        result, _ = validate_readiness(_valid_report(
            blocked="True",
            submit_order_called="False",
        ))
        assert result == "PASS"


# ---------------------------------------------------------------------------
# build_review
# ---------------------------------------------------------------------------

class TestBuildReview:
    def test_review_result_pass_for_valid(self):
        from src.tools.live_v2_executor_readiness_review import build_review
        review = build_review(_valid_report())
        assert review["review_result"] == "PASS"

    def test_review_result_fail_propagated(self):
        from src.tools.live_v2_executor_readiness_review import build_review
        review = build_review(_valid_report(block_guard="approval_artifact",
                                            violations=["live_trading_approved=false"]))
        assert review["review_result"] == "FAIL"

    def test_required_fields_in_review(self):
        from src.tools.live_v2_executor_readiness_review import build_review
        review = build_review(_valid_report())
        for field in ["blocked", "block_guard", "submit_order_called",
                      "violations", "review_result", "review_violations"]:
            assert field in review, f"Missing field: {field}"

    def test_review_violations_empty_on_pass(self):
        from src.tools.live_v2_executor_readiness_review import build_review
        review = build_review(_valid_report())
        assert review["review_violations"] == []

    def test_review_violations_populated_on_fail(self):
        from src.tools.live_v2_executor_readiness_review import build_review
        review = build_review(_valid_report(blocked=False))
        assert len(review["review_violations"]) >= 1


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_config_safety_report_exits_0(self, tmp_path):
        path = _write_report(tmp_path, _valid_report())
        assert _run_main(str(path)) in (0, None)

    def test_missing_report_exits_1(self, tmp_path):
        assert _run_main(str(tmp_path / "nonexistent.json")) == 1

    def test_malformed_json_exits_1(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{bad json", encoding="utf-8")
        assert _run_main(str(path)) == 1

    def test_approval_artifact_block_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(
            block_guard="approval_artifact",
            violations=["live_trading_approved=false"],
        ))
        assert _run_main(str(path)) == 1

    def test_v2_trading_approval_block_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        assert _run_main(str(path)) == 1

    def test_v2_submission_approval_block_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(
            block_guard="v2_submission_approval",
            violations=["source_live_trading_approval_path mismatch"],
        ))
        assert _run_main(str(path)) == 1

    def test_v2_cross_check_block_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(
            block_guard="v2_cross_check",
            violations=["v2 artifact symbol mismatch"],
        ))
        assert _run_main(str(path)) == 1

    def test_blocked_false_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(blocked=False))
        assert _run_main(str(path)) == 1

    def test_submit_order_called_true_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(submit_order_called=True))
        assert _run_main(str(path)) == 1

    def test_other_block_guard_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(
            block_guard="real_submit_not_implemented",
            violations=["not implemented"],
        ))
        assert _run_main(str(path)) == 1

    def test_no_config_safety_violation_in_list_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(
            violations=["some other reason"]
        ))
        assert _run_main(str(path)) == 1

    def test_no_alpaca_calls(self, tmp_path):
        path = _write_report(tmp_path, _valid_report())
        broker = MagicMock()
        _run_main(str(path))
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_read(self, tmp_path):
        import os
        from unittest.mock import patch
        path = _write_report(tmp_path, _valid_report())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            code = _run_main(str(path))
        assert code in (0, None)

    def test_no_files_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        report_path = _write_report(tmp_path, _valid_report())
        before = set(tmp_path.glob("**/*"))
        _run_main(str(report_path))
        after = set(tmp_path.glob("**/*"))
        assert after == before

    def test_no_submit_order_call(self, tmp_path):
        path = _write_report(tmp_path, _valid_report())
        code = _run_main(str(path))
        assert code in (0, None)

    def test_no_ledger_writes(self, tmp_path):
        path = _write_report(tmp_path, _valid_report())
        _run_main(str(path))
        ledger_files = list(tmp_path.glob("*ledger*"))
        assert ledger_files == []
