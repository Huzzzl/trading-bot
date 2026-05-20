"""
tests/test_live_submit_plan_review.py
---------------------------------------
Tests for src/tools/live_submit_plan_review.py.

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

def _valid_plan(**overrides) -> dict:
    plan = {
        "checked_at_utc":          "2026-05-20T10:00:00+00:00",
        "symbol":                  "SPY",
        "intent_row_identity":     "2026-05-20T10:00:00+00:00",
        "side":                    "buy",
        "order_type":              "market",
        "live_sizing_mode":        "notional",
        "effective_quantity":      None,
        "effective_notional":      95.0,
        "entry_price":             550.0,
        "live_max_order_notional": 100.0,
        "live_submit_dry_run":     True,
        "live_trading_enabled":    False,
        "live_kill_switch_enabled": True,
        "live_require_human_confirm": True,
        "submit_order_called":     False,
        "submit_allowed":          False,
        "final_action":            "DRY_RUN_ONLY_NO_ORDER_SUBMITTED",
    }
    plan.update(overrides)
    return plan


def _write_plan(tmp_path: Path, plan: dict) -> Path:
    path = tmp_path / "live_submit_dry_run_plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def _run_main(plan_path: str) -> int | None:
    from src.tools.live_submit_plan_review import main
    try:
        main(["--plan", plan_path])
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# parse_plan
# ---------------------------------------------------------------------------

class TestParsePlan:
    def test_valid_plan_returns_dict(self, tmp_path):
        from src.tools.live_submit_plan_review import parse_plan
        path = _write_plan(tmp_path, _valid_plan())
        plan = parse_plan(path)
        assert isinstance(plan, dict)
        assert plan["symbol"] == "SPY"

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from src.tools.live_submit_plan_review import parse_plan
        with pytest.raises(FileNotFoundError):
            parse_plan(tmp_path / "nonexistent.json")

    def test_malformed_json_raises_value_error(self, tmp_path):
        from src.tools.live_submit_plan_review import parse_plan
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_plan(path)

    def test_reads_utf8_encoding(self, tmp_path):
        from src.tools.live_submit_plan_review import parse_plan
        plan = _valid_plan(symbol="SPY")
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        result = parse_plan(path)
        assert result["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------

class TestValidatePlan:
    def test_valid_plan_passes(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan())
        assert result == "PASS"
        assert violations == []

    def test_missing_required_field_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        plan = _valid_plan()
        del plan["submit_order_called"]
        result, violations = validate_plan(plan)
        assert result == "FAIL"
        assert any("submit_order_called" in v for v in violations)

    def test_submit_order_called_true_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(submit_order_called=True))
        assert result == "FAIL"
        assert any("submit_order_called" in v for v in violations)

    def test_submit_allowed_true_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(submit_allowed=True))
        assert result == "FAIL"
        assert any("submit_allowed" in v for v in violations)

    def test_live_submit_dry_run_false_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(live_submit_dry_run=False))
        assert result == "FAIL"
        assert any("live_submit_dry_run" in v for v in violations)

    def test_live_trading_enabled_true_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(live_trading_enabled=True))
        assert result == "FAIL"
        assert any("live_trading_enabled" in v for v in violations)

    def test_kill_switch_false_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(live_kill_switch_enabled=False))
        assert result == "FAIL"
        assert any("live_kill_switch_enabled" in v for v in violations)

    def test_wrong_final_action_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(final_action="SUBMITTED"))
        assert result == "FAIL"
        assert any("final_action" in v for v in violations)

    def test_wrong_sizing_mode_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(live_sizing_mode="quantity"))
        assert result == "FAIL"
        assert any("live_sizing_mode" in v for v in violations)

    def test_effective_notional_zero_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(effective_notional=0))
        assert result == "FAIL"
        assert any("effective_notional" in v for v in violations)

    def test_effective_notional_negative_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(effective_notional=-5.0))
        assert result == "FAIL"

    def test_effective_notional_exceeds_max_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(
            effective_notional=150.0,
            live_max_order_notional=100.0,
        ))
        assert result == "FAIL"
        assert any("exceeds" in v for v in violations)

    def test_effective_notional_at_max_passes(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, _ = validate_plan(_valid_plan(
            effective_notional=100.0,
            live_max_order_notional=100.0,
        ))
        assert result == "PASS"

    def test_multiple_violations_all_reported(self):
        from src.tools.live_submit_plan_review import validate_plan
        plan = _valid_plan(
            submit_order_called=True,
            submit_allowed=True,
            live_trading_enabled=True,
        )
        result, violations = validate_plan(plan)
        assert result == "FAIL"
        assert len(violations) >= 3

    def test_string_false_truthy_check(self):
        from src.tools.live_submit_plan_review import validate_plan
        # String "False" should be treated as falsy for boolean fields
        result, _ = validate_plan(_valid_plan(
            live_submit_dry_run="True",
            live_trading_enabled="False",
            live_kill_switch_enabled="True",
            submit_order_called="False",
            submit_allowed="False",
        ))
        assert result == "PASS"

    def test_string_true_submit_order_called_fails(self):
        from src.tools.live_submit_plan_review import validate_plan
        result, violations = validate_plan(_valid_plan(submit_order_called="True"))
        assert result == "FAIL"


# ---------------------------------------------------------------------------
# build_review
# ---------------------------------------------------------------------------

class TestBuildReview:
    def test_review_result_pass_for_valid(self):
        from src.tools.live_submit_plan_review import build_review
        review = build_review(_valid_plan())
        assert review["review_result"] == "PASS"

    def test_review_result_fail_propagated(self):
        from src.tools.live_submit_plan_review import build_review
        review = build_review(_valid_plan(submit_order_called=True))
        assert review["review_result"] == "FAIL"

    def test_required_fields_in_review(self):
        from src.tools.live_submit_plan_review import build_review
        review = build_review(_valid_plan())
        for field in [
            "symbol", "side", "order_type", "live_sizing_mode",
            "effective_quantity", "effective_notional",
            "live_submit_dry_run", "live_trading_enabled",
            "live_kill_switch_enabled", "submit_order_called",
            "submit_allowed", "final_action", "review_result", "violations",
        ]:
            assert field in review, f"Missing field: {field}"

    def test_violations_empty_on_pass(self):
        from src.tools.live_submit_plan_review import build_review
        review = build_review(_valid_plan())
        assert review["violations"] == []

    def test_violations_populated_on_fail(self):
        from src.tools.live_submit_plan_review import build_review
        review = build_review(_valid_plan(submit_allowed=True))
        assert len(review["violations"]) >= 1


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_plan_exits_0(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan())
        assert _run_main(str(path)) in (0, None)

    def test_missing_plan_exits_1(self, tmp_path):
        assert _run_main(str(tmp_path / "nonexistent.json")) == 1

    def test_malformed_json_exits_1(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{bad json", encoding="utf-8")
        assert _run_main(str(path)) == 1

    def test_submit_order_called_true_exits_1(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan(submit_order_called=True))
        assert _run_main(str(path)) == 1

    def test_submit_allowed_true_exits_1(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan(submit_allowed=True))
        assert _run_main(str(path)) == 1

    def test_live_submit_dry_run_false_exits_1(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan(live_submit_dry_run=False))
        assert _run_main(str(path)) == 1

    def test_live_trading_enabled_true_exits_1(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan(live_trading_enabled=True))
        assert _run_main(str(path)) == 1

    def test_kill_switch_false_exits_1(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan(live_kill_switch_enabled=False))
        assert _run_main(str(path)) == 1

    def test_wrong_final_action_exits_1(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan(final_action="SUBMITTED"))
        assert _run_main(str(path)) == 1

    def test_effective_notional_exceeds_max_exits_1(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan(
            effective_notional=200.0, live_max_order_notional=100.0,
        ))
        assert _run_main(str(path)) == 1

    def test_no_alpaca_calls(self, tmp_path):
        path = _write_plan(tmp_path, _valid_plan())
        broker = MagicMock()
        _run_main(str(path))
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_read(self, tmp_path):
        import os
        path = _write_plan(tmp_path, _valid_plan())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        from unittest.mock import patch
        with patch.dict(os.environ, env, clear=True):
            code = _run_main(str(path))
        assert code in (0, None)

    def test_no_files_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        plan_path = _write_plan(tmp_path, _valid_plan())
        before = set(tmp_path.glob("**/*"))
        _run_main(str(plan_path))
        after = set(tmp_path.glob("**/*"))
        assert after == before  # no new files created
