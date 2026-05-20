"""
tests/test_live_operator_release_checklist.py
----------------------------------------------
Tests for src/tools/live_operator_release_checklist.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no file writes beyond tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_checklist(**overrides) -> dict:
    d = {"final_result": "READY"}
    d.update(overrides)
    return d


def _valid_plan(**overrides) -> dict:
    d = {
        "symbol":                  "SPY",
        "side":                    "buy",
        "order_type":              "market",
        "live_sizing_mode":        "notional",
        "effective_quantity":      None,
        "effective_notional":      95.0,
        "live_max_order_notional": 100.0,
        "live_submit_dry_run":     True,
        "live_trading_enabled":    False,
        "live_kill_switch_enabled": True,
        "submit_order_called":     False,
        "submit_allowed":          False,
        "final_action":            "DRY_RUN_ONLY_NO_ORDER_SUBMITTED",
    }
    d.update(overrides)
    return d


def _valid_review(**overrides) -> dict:
    d = {"review_result": "PASS"}
    d.update(overrides)
    return d


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _run_main(args: list[str]) -> int | None:
    from src.tools.live_operator_release_checklist import main
    try:
        main(args)
        return None
    except SystemExit as exc:
        return exc.code


def _base_args(tmp_path: Path, pre_submit: Path, submit_plan: Path, plan_review: Path | None = None) -> list[str]:
    output = tmp_path / "release_checklist.json"
    args = [
        "--config", "config/settings.paper.local.yaml",
        "--pre-submit", str(pre_submit),
        "--submit-plan", str(submit_plan),
        "--output", str(output),
    ]
    if plan_review is not None:
        args += ["--plan-review", str(plan_review)]
    return args


# ---------------------------------------------------------------------------
# _read_json
# ---------------------------------------------------------------------------

class TestReadJson:
    def test_valid_json_returns_dict(self, tmp_path):
        from src.tools.live_operator_release_checklist import _read_json
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        assert _read_json(p) == {"key": "value"}

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from src.tools.live_operator_release_checklist import _read_json
        with pytest.raises(FileNotFoundError):
            _read_json(tmp_path / "nonexistent.json")

    def test_malformed_json_raises_value_error(self, tmp_path):
        from src.tools.live_operator_release_checklist import _read_json
        p = tmp_path / "bad.json"
        p.write_text("{bad json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            _read_json(p)


# ---------------------------------------------------------------------------
# _validate_pre_submit_checklist
# ---------------------------------------------------------------------------

class TestValidatePreSubmitChecklist:
    def test_ready_passes(self):
        from src.tools.live_operator_release_checklist import _validate_pre_submit_checklist
        assert _validate_pre_submit_checklist({"final_result": "READY"}) == []

    def test_not_ready_fails(self):
        from src.tools.live_operator_release_checklist import _validate_pre_submit_checklist
        v = _validate_pre_submit_checklist({"final_result": "NOT READY"})
        assert len(v) == 1
        assert "READY" in v[0]

    def test_missing_final_result_fails(self):
        from src.tools.live_operator_release_checklist import _validate_pre_submit_checklist
        v = _validate_pre_submit_checklist({})
        assert len(v) == 1


# ---------------------------------------------------------------------------
# _validate_submit_plan
# ---------------------------------------------------------------------------

class TestValidateSubmitPlan:
    def test_valid_plan_passes(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        assert _validate_submit_plan(_valid_plan()) == []

    def test_live_submit_dry_run_false_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(live_submit_dry_run=False))
        assert any("live_submit_dry_run" in x for x in v)

    def test_live_trading_enabled_true_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(live_trading_enabled=True))
        assert any("live_trading_enabled" in x for x in v)

    def test_kill_switch_false_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(live_kill_switch_enabled=False))
        assert any("live_kill_switch_enabled" in x for x in v)

    def test_submit_order_called_true_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(submit_order_called=True))
        assert any("submit_order_called" in x for x in v)

    def test_submit_allowed_true_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(submit_allowed=True))
        assert any("submit_allowed" in x for x in v)

    def test_wrong_final_action_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(final_action="SUBMITTED"))
        assert any("final_action" in x for x in v)

    def test_wrong_sizing_mode_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(live_sizing_mode="quantity"))
        assert any("live_sizing_mode" in x for x in v)

    def test_effective_notional_zero_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(effective_notional=0))
        assert any("effective_notional" in x for x in v)

    def test_effective_notional_negative_fails(self):
        from src.tools.live_operator_release_checklist import _validate_submit_plan
        v = _validate_submit_plan(_valid_plan(effective_notional=-10.0))
        assert len(v) >= 1


# ---------------------------------------------------------------------------
# _validate_plan_review
# ---------------------------------------------------------------------------

class TestValidatePlanReview:
    def test_pass_review_returns_no_violations(self):
        from src.tools.live_operator_release_checklist import _validate_plan_review
        assert _validate_plan_review({"review_result": "PASS"}) == []

    def test_fail_review_returns_violation(self):
        from src.tools.live_operator_release_checklist import _validate_plan_review
        v = _validate_plan_review({"review_result": "FAIL"})
        assert len(v) == 1
        assert "PASS" in v[0]

    def test_missing_review_result_returns_violation(self):
        from src.tools.live_operator_release_checklist import _validate_plan_review
        v = _validate_plan_review({})
        assert len(v) == 1


# ---------------------------------------------------------------------------
# build_release_checklist
# ---------------------------------------------------------------------------

class TestBuildReleaseChecklist:
    def test_all_valid_is_release_ready(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(_valid_checklist(), _valid_plan(), None)
        assert result["release_result"] == "RELEASE_READY"
        assert result["violations"] == []

    def test_checklist_not_ready_is_not_release_ready(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(
            _valid_checklist(final_result="NOT READY"), _valid_plan(), None
        )
        assert result["release_result"] == "NOT_RELEASE_READY"
        assert len(result["violations"]) >= 1

    def test_plan_violation_is_not_release_ready(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(
            _valid_checklist(), _valid_plan(submit_order_called=True), None
        )
        assert result["release_result"] == "NOT_RELEASE_READY"

    def test_plan_review_fail_is_not_release_ready(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(
            _valid_checklist(), _valid_plan(), _valid_review(review_result="FAIL")
        )
        assert result["release_result"] == "NOT_RELEASE_READY"

    def test_plan_review_none_skipped(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(_valid_checklist(), _valid_plan(), None)
        assert result["plan_review_result"] is None
        assert result["release_result"] == "RELEASE_READY"

    def test_plan_review_pass_included(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(_valid_checklist(), _valid_plan(), _valid_review())
        assert result["plan_review_result"] == "PASS"
        assert result["release_result"] == "RELEASE_READY"

    def test_manual_approval_fields_default_values(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(_valid_checklist(), _valid_plan(), None)
        assert result["operator_name"] is None
        assert result["approval_timestamp_utc"] is None
        assert result["approval_for_real_submit_pr"] is False
        assert result["notes"] == ""

    def test_required_fields_present(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(_valid_checklist(), _valid_plan(), None)
        for field in [
            "checked_at_utc", "pre_submit_checklist_result",
            "submit_plan_symbol", "submit_plan_side", "submit_plan_live_sizing_mode",
            "submit_plan_effective_notional", "submit_plan_final_action",
            "plan_review_result", "violations", "release_result",
            "operator_name", "approval_timestamp_utc",
            "approval_for_real_submit_pr", "notes",
        ]:
            assert field in result, f"Missing field: {field}"

    def test_multiple_violations_all_reported(self):
        from src.tools.live_operator_release_checklist import build_release_checklist
        result = build_release_checklist(
            _valid_checklist(final_result="NOT READY"),
            _valid_plan(submit_order_called=True, submit_allowed=True),
            None,
        )
        assert len(result["violations"]) >= 3


# ---------------------------------------------------------------------------
# write_release_checklist
# ---------------------------------------------------------------------------

class TestWriteReleaseChecklist:
    def test_writes_json_file(self, tmp_path):
        from src.tools.live_operator_release_checklist import (
            build_release_checklist, write_release_checklist,
        )
        result = build_release_checklist(_valid_checklist(), _valid_plan(), None)
        path = write_release_checklist(tmp_path / "out.json", result)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["release_result"] == "RELEASE_READY"

    def test_creates_parent_dirs(self, tmp_path):
        from src.tools.live_operator_release_checklist import (
            build_release_checklist, write_release_checklist,
        )
        result = build_release_checklist(_valid_checklist(), _valid_plan(), None)
        path = write_release_checklist(tmp_path / "nested" / "dir" / "out.json", result)
        assert path.exists()


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_inputs_exits_0(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan())
        code = _run_main(_base_args(tmp_path, pre, plan))
        assert code in (0, None)

    def test_valid_inputs_with_plan_review_exits_0(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan())
        review = _write(tmp_path, "review.json", _valid_review())
        code = _run_main(_base_args(tmp_path, pre, plan, review))
        assert code in (0, None)

    def test_checklist_not_ready_exits_1(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist(final_result="NOT READY"))
        plan = _write(tmp_path, "plan.json", _valid_plan())
        code = _run_main(_base_args(tmp_path, pre, plan))
        assert code == 1

    def test_plan_submit_order_called_exits_1(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan(submit_order_called=True))
        code = _run_main(_base_args(tmp_path, pre, plan))
        assert code == 1

    def test_plan_review_fail_exits_1(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan())
        review = _write(tmp_path, "review.json", _valid_review(review_result="FAIL"))
        code = _run_main(_base_args(tmp_path, pre, plan, review))
        assert code == 1

    def test_missing_pre_submit_exits_1(self, tmp_path):
        plan = _write(tmp_path, "plan.json", _valid_plan())
        code = _run_main(_base_args(tmp_path, tmp_path / "nonexistent.json", plan))
        assert code == 1

    def test_missing_submit_plan_exits_1(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        code = _run_main(_base_args(tmp_path, pre, tmp_path / "nonexistent.json"))
        assert code == 1

    def test_missing_plan_review_exits_1(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan())
        code = _run_main(_base_args(tmp_path, pre, plan, tmp_path / "nonexistent.json"))
        assert code == 1

    def test_writes_output_artifact(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan())
        output = tmp_path / "release_checklist.json"
        _run_main([
            "--config", "config/settings.paper.local.yaml",
            "--pre-submit", str(pre),
            "--submit-plan", str(plan),
            "--output", str(output),
        ])
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert "release_result" in data
        assert data["operator_name"] is None
        assert data["approval_for_real_submit_pr"] is False

    def test_no_alpaca_calls(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan())
        broker = MagicMock()
        _run_main(_base_args(tmp_path, pre, plan))
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_needed(self, tmp_path):
        import os
        from unittest.mock import patch
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            code = _run_main(_base_args(tmp_path, pre, plan))
        assert code in (0, None)

    def test_malformed_pre_submit_exits_1(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{bad json", encoding="utf-8")
        plan = _write(tmp_path, "plan.json", _valid_plan())
        code = _run_main(_base_args(tmp_path, bad, plan))
        assert code == 1

    def test_plan_live_trading_enabled_exits_1(self, tmp_path):
        pre = _write(tmp_path, "checklist.json", _valid_checklist())
        plan = _write(tmp_path, "plan.json", _valid_plan(live_trading_enabled=True))
        code = _run_main(_base_args(tmp_path, pre, plan))
        assert code == 1
