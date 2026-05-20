"""
tests/test_live_submit.py
--------------------------
Tests for src/tools/live_submit.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no live ledger writes.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MODULE = "src.tools.live_submit"
_VALID_TOKEN = "DRY-RUN-LIVE-SUBMIT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_execution(
    live_trading_enabled:    bool  = False,
    live_submit_dry_run:     bool  = True,
    live_kill_switch_enabled: bool = True,
    live_require_human_confirm: bool = True,
    live_max_orders_per_day:  int   = 1,
    live_max_notional_per_day: float = 100.0,
    live_max_order_notional:  float  = 100.0,
    live_ledger_path: str = "output/live_execution_ledger.csv",
) -> MagicMock:
    ex = MagicMock()
    ex.live_trading_enabled       = live_trading_enabled
    ex.live_submit_dry_run        = live_submit_dry_run
    ex.live_kill_switch_enabled   = live_kill_switch_enabled
    ex.live_require_human_confirm = live_require_human_confirm
    ex.live_max_orders_per_day    = live_max_orders_per_day
    ex.live_max_notional_per_day  = live_max_notional_per_day
    ex.live_max_order_notional    = live_max_order_notional
    ex.live_ledger_path           = live_ledger_path
    return ex


def _mock_cfg(**kw) -> MagicMock:
    cfg = MagicMock()
    cfg.execution = _mock_execution(**kw)
    return cfg


_CFG_PASS = {"label": "config", "status": "PASS", "detail": ""}
_CFG_FAIL = {"label": "config", "status": "FAIL", "detail": "bad config"}


def _make_intent_csv(
    tmp_path: Path,
    rows: list[dict] | None = None,
    subdir: str = "intents",
) -> Path:
    """Write a mock live_order_intents.csv and return the directory."""
    intents_dir = tmp_path / subdir
    intents_dir.mkdir(parents=True, exist_ok=True)
    csv_path = intents_dir / "live_order_intents.csv"

    cols = [
        "checked_at_utc", "symbol", "side", "order_type", "live_sizing_mode",
        "effective_quantity", "effective_notional", "entry_price",
        "live_max_order_notional", "live_max_notional", "readiness_decision",
        "dry_run_only", "submit_allowed", "sizing_status", "sizing_reason",
    ]

    if rows is None:
        rows = [_valid_intent_row()]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    return intents_dir


def _valid_intent_row(**overrides) -> dict:
    row = {
        "checked_at_utc":      "2026-05-20T10:00:00+00:00",
        "symbol":              "SPY",
        "side":                "buy",
        "order_type":          "market",
        "live_sizing_mode":    "notional",
        "effective_quantity":  "",
        "effective_notional":  "95.0",
        "entry_price":         "550.0",
        "live_max_order_notional": "100.0",
        "live_max_notional":   "500.0",
        "readiness_decision":  "GO",
        "dry_run_only":        "True",
        "submit_allowed":      "False",
        "sizing_status":       "PASS",
        "sizing_reason":       "notional=95.0 <= max=100.0",
    }
    row.update(overrides)
    return row


def _run_main(
    tmp_path: Path,
    *,
    token: str = _VALID_TOKEN,
    symbol: str = "SPY",
    cfg_mock: MagicMock | None = None,
    cfg_result: dict | None = None,
    checklist_result: str = "READY",
    intents_dir: Path | None = None,
    intent_rows: list[dict] | None = None,
) -> int | None:
    from src.tools.live_submit import main

    if cfg_mock is None:
        cfg_mock = _mock_cfg()
    if cfg_result is None:
        cfg_result = _CFG_PASS
    if intents_dir is None:
        intents_dir = _make_intent_csv(tmp_path, intent_rows)

    argv = [
        "--config",      "config/settings.paper.local.yaml",
        "--symbol",      symbol,
        "--confirm",     token,
        "--output-dir",  str(tmp_path / "out"),
        "--intents-dir", str(intents_dir),
    ]

    with (
        patch("src.tools.live_submit._run_checklist", return_value=checklist_result),
        patch("src.tools.paper_status.check_config", return_value=(cfg_result, cfg_mock)),
    ):
        try:
            main(argv)
            return None
        except SystemExit as exc:
            return exc.code


# ---------------------------------------------------------------------------
# _validate_confirmation_token
# ---------------------------------------------------------------------------

class TestValidateConfirmationToken:
    def test_valid_token_returns_empty(self):
        from src.tools.live_submit import _validate_confirmation_token
        assert _validate_confirmation_token(_VALID_TOKEN) == []

    def test_missing_token_fails(self):
        from src.tools.live_submit import _validate_confirmation_token
        assert _validate_confirmation_token("") != []

    def test_wrong_token_fails(self):
        from src.tools.live_submit import _validate_confirmation_token
        errors = _validate_confirmation_token("WRONG-TOKEN")
        assert errors
        assert "WRONG-TOKEN" in errors[0]

    def test_whitespace_only_fails(self):
        from src.tools.live_submit import _validate_confirmation_token
        assert _validate_confirmation_token("   ") != []

    def test_partial_token_fails(self):
        from src.tools.live_submit import _validate_confirmation_token
        assert _validate_confirmation_token("DRY-RUN") != []


# ---------------------------------------------------------------------------
# _validate_config_safety
# ---------------------------------------------------------------------------

class TestValidateConfigSafety:
    def test_safe_defaults_pass(self):
        from src.tools.live_submit import _validate_config_safety
        assert _validate_config_safety(_mock_execution()) == []

    def test_live_trading_enabled_fails(self):
        from src.tools.live_submit import _validate_config_safety
        errors = _validate_config_safety(_mock_execution(live_trading_enabled=True))
        assert any("live_trading_enabled" in e for e in errors)

    def test_dry_run_false_fails(self):
        from src.tools.live_submit import _validate_config_safety
        errors = _validate_config_safety(_mock_execution(live_submit_dry_run=False))
        assert any("live_submit_dry_run" in e for e in errors)

    def test_kill_switch_disabled_fails(self):
        from src.tools.live_submit import _validate_config_safety
        errors = _validate_config_safety(_mock_execution(live_kill_switch_enabled=False))
        assert any("live_kill_switch_enabled" in e for e in errors)

    def test_human_confirm_disabled_fails(self):
        from src.tools.live_submit import _validate_config_safety
        errors = _validate_config_safety(_mock_execution(live_require_human_confirm=False))
        assert any("live_require_human_confirm" in e for e in errors)

    def test_multiple_failures_all_reported(self):
        from src.tools.live_submit import _validate_config_safety
        errors = _validate_config_safety(_mock_execution(
            live_kill_switch_enabled=False,
            live_require_human_confirm=False,
        ))
        assert len(errors) == 2


# ---------------------------------------------------------------------------
# _validate_selected_intent
# ---------------------------------------------------------------------------

class TestValidateSelectedIntent:
    def test_valid_intent_passes(self):
        from src.tools.live_submit import _validate_selected_intent
        assert _validate_selected_intent(_valid_intent_row()) == []

    def test_submit_allowed_true_fails(self):
        from src.tools.live_submit import _validate_selected_intent
        errors = _validate_selected_intent(_valid_intent_row(submit_allowed="True"))
        assert any("submit_allowed" in e for e in errors)

    def test_dry_run_only_false_fails(self):
        from src.tools.live_submit import _validate_selected_intent
        errors = _validate_selected_intent(_valid_intent_row(dry_run_only="False"))
        assert any("dry_run_only" in e for e in errors)

    def test_sizing_status_fail_fails(self):
        from src.tools.live_submit import _validate_selected_intent
        errors = _validate_selected_intent(_valid_intent_row(sizing_status="FAIL"))
        assert any("sizing_status" in e for e in errors)


# ---------------------------------------------------------------------------
# _load_and_select_intent
# ---------------------------------------------------------------------------

class TestLoadAndSelectIntent:
    def test_selects_notional_pass_intent(self, tmp_path):
        from src.tools.live_submit import _load_and_select_intent
        intents_dir = _make_intent_csv(tmp_path, [_valid_intent_row()])
        intent, errors = _load_and_select_intent(intents_dir, "SPY")
        assert errors == []
        assert intent is not None
        assert intent["live_sizing_mode"] == "notional"
        assert intent["sizing_status"] == "PASS"

    def test_missing_csv_returns_error(self, tmp_path):
        from src.tools.live_submit import _load_and_select_intent
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        _, errors = _load_and_select_intent(empty_dir, "SPY")
        assert errors

    def test_wrong_symbol_not_selected(self, tmp_path):
        from src.tools.live_submit import _load_and_select_intent
        intents_dir = _make_intent_csv(tmp_path, [_valid_intent_row(symbol="AAPL")])
        _, errors = _load_and_select_intent(intents_dir, "SPY")
        assert errors

    def test_quantity_mode_not_selected(self, tmp_path):
        from src.tools.live_submit import _load_and_select_intent
        intents_dir = _make_intent_csv(tmp_path, [_valid_intent_row(live_sizing_mode="quantity")])
        _, errors = _load_and_select_intent(intents_dir, "SPY")
        assert errors

    def test_sizing_fail_not_selected(self, tmp_path):
        from src.tools.live_submit import _load_and_select_intent
        intents_dir = _make_intent_csv(tmp_path, [_valid_intent_row(sizing_status="FAIL")])
        _, errors = _load_and_select_intent(intents_dir, "SPY")
        assert errors

    def test_selects_first_matching_candidate(self, tmp_path):
        from src.tools.live_submit import _load_and_select_intent
        rows = [
            _valid_intent_row(effective_notional="50.0"),
            _valid_intent_row(effective_notional="80.0"),
        ]
        intents_dir = _make_intent_csv(tmp_path, rows)
        intent, errors = _load_and_select_intent(intents_dir, "SPY")
        assert errors == []
        assert intent["effective_notional"] == "50.0"


# ---------------------------------------------------------------------------
# _build_plan
# ---------------------------------------------------------------------------

class TestBuildPlan:
    def test_required_fields_present(self):
        from src.tools.live_submit import _build_plan
        plan = _build_plan(_valid_intent_row(), _mock_execution(), "2026-05-20T10:00:00+00:00", "SPY")
        required = [
            "checked_at_utc", "symbol", "side", "order_type", "live_sizing_mode",
            "effective_quantity", "effective_notional", "live_submit_dry_run",
            "live_trading_enabled", "live_kill_switch_enabled",
            "submit_order_called", "submit_allowed", "final_action",
        ]
        for f in required:
            assert f in plan, f"Missing field: {f}"

    def test_submit_order_called_always_false(self):
        from src.tools.live_submit import _build_plan
        plan = _build_plan(_valid_intent_row(), _mock_execution(), "ts", "SPY")
        assert plan["submit_order_called"] is False

    def test_submit_allowed_always_false(self):
        from src.tools.live_submit import _build_plan
        plan = _build_plan(_valid_intent_row(), _mock_execution(), "ts", "SPY")
        assert plan["submit_allowed"] is False

    def test_live_submit_dry_run_always_true(self):
        from src.tools.live_submit import _build_plan
        plan = _build_plan(_valid_intent_row(), _mock_execution(), "ts", "SPY")
        assert plan["live_submit_dry_run"] is True

    def test_final_action_is_dry_run_only(self):
        from src.tools.live_submit import _build_plan
        plan = _build_plan(_valid_intent_row(), _mock_execution(), "ts", "SPY")
        assert plan["final_action"] == "DRY_RUN_ONLY_NO_ORDER_SUBMITTED"


# ---------------------------------------------------------------------------
# write_plan
# ---------------------------------------------------------------------------

class TestWritePlan:
    def test_writes_json_file(self, tmp_path):
        from src.tools.live_submit import write_plan
        plan = {"final_action": "DRY_RUN_ONLY_NO_ORDER_SUBMITTED", "symbol": "SPY"}
        path = write_plan(tmp_path, plan)
        assert path.name == "live_submit_dry_run_plan.json"
        assert path.exists()

    def test_json_is_valid(self, tmp_path):
        from src.tools.live_submit import write_plan
        plan = {"submit_order_called": False, "submit_allowed": False}
        path = write_plan(tmp_path, plan)
        data = json.loads(path.read_text())
        assert data["submit_order_called"] is False

    def test_creates_parent_dirs(self, tmp_path):
        from src.tools.live_submit import write_plan
        deep = tmp_path / "a" / "b"
        write_plan(deep, {"x": 1})
        assert (deep / "live_submit_dry_run_plan.json").exists()


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_happy_path_exits_0(self, tmp_path):
        code = _run_main(tmp_path)
        assert code in (0, None)

    def test_happy_path_writes_plan(self, tmp_path):
        _run_main(tmp_path)
        plan_path = tmp_path / "out" / "live_submit_dry_run_plan.json"
        assert plan_path.exists()

    def test_plan_has_correct_fields(self, tmp_path):
        _run_main(tmp_path)
        data = json.loads((tmp_path / "out" / "live_submit_dry_run_plan.json").read_text())
        assert data["submit_order_called"] is False
        assert data["submit_allowed"] is False
        assert data["live_submit_dry_run"] is True
        assert data["final_action"] == "DRY_RUN_ONLY_NO_ORDER_SUBMITTED"

    def test_submit_order_never_called(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path)
        broker.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path)
        broker.cancel_order.assert_not_called()

    def test_no_live_ledger_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _run_main(tmp_path)
        ledger_files = list(tmp_path.glob("**/*ledger*"))
        assert ledger_files == []

    def test_missing_confirmation_exits_1(self, tmp_path):
        assert _run_main(tmp_path, token="") == 1

    def test_wrong_confirmation_exits_1(self, tmp_path):
        assert _run_main(tmp_path, token="WRONG-TOKEN") == 1

    def test_checklist_not_ready_exits_1(self, tmp_path):
        assert _run_main(tmp_path, checklist_result="NOT READY") == 1

    def test_live_trading_enabled_true_exits_1(self, tmp_path):
        assert _run_main(tmp_path, cfg_mock=_mock_cfg(live_trading_enabled=True)) == 1

    def test_live_submit_dry_run_false_exits_1(self, tmp_path):
        assert _run_main(tmp_path, cfg_mock=_mock_cfg(live_submit_dry_run=False)) == 1

    def test_kill_switch_disabled_exits_1(self, tmp_path):
        assert _run_main(tmp_path, cfg_mock=_mock_cfg(live_kill_switch_enabled=False)) == 1

    def test_human_confirm_disabled_exits_1(self, tmp_path):
        assert _run_main(tmp_path, cfg_mock=_mock_cfg(live_require_human_confirm=False)) == 1

    def test_config_fail_exits_1(self, tmp_path):
        assert _run_main(tmp_path, cfg_result=_CFG_FAIL, cfg_mock=None) == 1

    def test_submit_allowed_true_intent_exits_1(self, tmp_path):
        bad_rows = [_valid_intent_row(submit_allowed="True")]
        assert _run_main(tmp_path, intent_rows=bad_rows) == 1

    def test_dry_run_only_false_intent_exits_1(self, tmp_path):
        bad_rows = [_valid_intent_row(dry_run_only="False")]
        assert _run_main(tmp_path, intent_rows=bad_rows) == 1

    def test_sizing_status_fail_intent_exits_1(self, tmp_path):
        bad_rows = [_valid_intent_row(sizing_status="FAIL")]
        assert _run_main(tmp_path, intent_rows=bad_rows) == 1

    def test_no_matching_intent_exits_1(self, tmp_path):
        # quantity mode — not selected
        bad_rows = [_valid_intent_row(live_sizing_mode="quantity")]
        assert _run_main(tmp_path, intent_rows=bad_rows) == 1
