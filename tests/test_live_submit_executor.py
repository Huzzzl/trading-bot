"""
tests/test_live_submit_executor.py
------------------------------------
Tests for src/execution/live_submit_executor.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no real ledger writes.
Every test asserts submit_order_called=False.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MODULE = "src.execution.live_submit_executor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_approval(**overrides) -> dict:
    d = {
        "approval_for_real_submit_pr":    True,
        "approval_scope":                 "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY",
        "live_trading_approved":          False,
        "live_order_submission_approved": False,
        "operator_name":                  "Huzzzl",
    }
    d.update(overrides)
    return d


def _valid_checklist(**overrides) -> dict:
    d = {"final_result": "READY"}
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


def _default_kwargs(tmp_path: Path, **overrides) -> dict:
    """Build default kwargs for maybe_execute_live_submit.

    All config flags are set to the PERMISSIVE state so tests can isolate
    individual guards.  The approval artifact still carries
    live_trading_approved=false and live_order_submission_approved=false,
    which means submit_order remains unreachable even with permissive config.
    """
    approval = _write(tmp_path, "_default_approval.json", _valid_approval())
    checklist = _write(tmp_path, "_default_checklist.json", _valid_checklist())
    review = _write(tmp_path, "_default_review.json", _valid_review())
    output_dir = tmp_path / "output"

    base = dict(
        confirm_token=          "REAL-LIVE-SUBMIT-AUTHORIZED",
        approval_path=          approval,
        checklist_path=         checklist,
        review_path=            review,
        symbol=                 "SPY",
        intended_notional=      95.0,
        client_order_id=        "test-order-001",
        live_trading_enabled=   True,
        live_submit_dry_run=    False,
        live_kill_switch_enabled=False,
        live_require_human_confirm=True,
        live_max_order_notional=100.0,
        live_max_orders_per_day=1,
        live_max_notional_per_day=100.0,
        ledger_path=            tmp_path / "ledger.csv",
        output_dir=             output_dir,
        broker_client=          None,
    )
    base.update(overrides)
    return base


def _run(**kwargs) -> dict:
    from src.execution.live_submit_executor import maybe_execute_live_submit
    return maybe_execute_live_submit(**kwargs)


# ---------------------------------------------------------------------------
# Guard unit tests — pure check functions
# ---------------------------------------------------------------------------

class TestCheckConfirmToken:
    def test_correct_token_passes(self):
        from src.execution.live_submit_executor import _check_confirm_token
        assert _check_confirm_token("REAL-LIVE-SUBMIT-AUTHORIZED") == []

    def test_wrong_token_fails(self):
        from src.execution.live_submit_executor import _check_confirm_token
        v = _check_confirm_token("DRY-RUN-LIVE-SUBMIT")
        assert len(v) == 1

    def test_empty_token_fails(self):
        from src.execution.live_submit_executor import _check_confirm_token
        v = _check_confirm_token("")
        assert len(v) == 1


class TestCheckApprovalArtifact:
    def test_current_approval_artifact_always_blocked(self):
        """Current tooling always produces live_trading_approved=false → blocked."""
        from src.execution.live_submit_executor import _check_approval_artifact
        v = _check_approval_artifact(_valid_approval())
        # Must have violations for live_trading_approved and live_order_submission_approved
        assert any("live_trading_approved" in x for x in v)
        assert any("live_order_submission_approved" in x for x in v)

    def test_missing_approval_for_real_submit_pr_fails(self):
        from src.execution.live_submit_executor import _check_approval_artifact
        art = _valid_approval(approval_for_real_submit_pr=False)
        v = _check_approval_artifact(art)
        assert any("approval_for_real_submit_pr" in x for x in v)

    def test_wrong_scope_fails(self):
        from src.execution.live_submit_executor import _check_approval_artifact
        art = _valid_approval(approval_scope="WRONG_SCOPE")
        v = _check_approval_artifact(art)
        assert any("approval_scope" in x for x in v)

    def test_live_trading_approved_false_always_blocked(self):
        from src.execution.live_submit_executor import _check_approval_artifact
        art = _valid_approval(live_trading_approved=False)
        v = _check_approval_artifact(art)
        assert any("live_trading_approved" in x for x in v)

    def test_live_order_submission_approved_false_always_blocked(self):
        from src.execution.live_submit_executor import _check_approval_artifact
        art = _valid_approval(live_order_submission_approved=False)
        v = _check_approval_artifact(art)
        assert any("live_order_submission_approved" in x for x in v)


class TestCheckConfigSafety:
    def test_all_permissive_passes(self):
        from src.execution.live_submit_executor import _check_config_safety
        v = _check_config_safety(
            live_trading_enabled=True,
            live_submit_dry_run=False,
            live_kill_switch_enabled=False,
            live_require_human_confirm=True,
        )
        assert v == []

    def test_default_config_blocks(self):
        """Default config values all block real submit."""
        from src.execution.live_submit_executor import _check_config_safety
        v = _check_config_safety(
            live_trading_enabled=False,   # default
            live_submit_dry_run=True,     # default
            live_kill_switch_enabled=True, # default
            live_require_human_confirm=True,
        )
        assert len(v) >= 3

    def test_live_trading_disabled_blocks(self):
        from src.execution.live_submit_executor import _check_config_safety
        v = _check_config_safety(False, False, False, True)
        assert any("live_trading_enabled" in x for x in v)

    def test_dry_run_true_blocks(self):
        from src.execution.live_submit_executor import _check_config_safety
        v = _check_config_safety(True, True, False, True)
        assert any("live_submit_dry_run" in x for x in v)

    def test_kill_switch_engaged_blocks(self):
        from src.execution.live_submit_executor import _check_config_safety
        v = _check_config_safety(True, False, True, True)
        assert any("live_kill_switch_enabled" in x for x in v)

    def test_human_confirm_false_blocks(self):
        from src.execution.live_submit_executor import _check_config_safety
        v = _check_config_safety(True, False, False, False)
        assert any("live_require_human_confirm" in x for x in v)


class TestCheckDailyLimits:
    def test_within_limits_passes(self):
        from src.execution.live_submit_executor import _check_daily_limits
        assert _check_daily_limits(0, 0.0, 95.0, 1, 100.0) == []

    def test_order_count_at_max_blocks(self):
        from src.execution.live_submit_executor import _check_daily_limits
        v = _check_daily_limits(1, 0.0, 95.0, 1, 100.0)
        assert any("daily order limit" in x for x in v)

    def test_notional_would_exceed_blocks(self):
        from src.execution.live_submit_executor import _check_daily_limits
        v = _check_daily_limits(0, 50.0, 60.0, 1, 100.0)
        assert any("notional" in x for x in v)


class TestCheckNoOpenConflict:
    def test_no_conflict_passes(self):
        from src.execution.live_submit_executor import _check_no_open_conflict
        assert _check_no_open_conflict(False, False, "SPY") == []

    def test_open_position_blocks(self):
        from src.execution.live_submit_executor import _check_no_open_conflict
        v = _check_no_open_conflict(True, False, "SPY")
        assert any("position" in x for x in v)

    def test_open_order_blocks(self):
        from src.execution.live_submit_executor import _check_no_open_conflict
        v = _check_no_open_conflict(False, True, "SPY")
        assert any("order" in x for x in v)


class TestCheckIdempotency:
    def test_not_used_passes(self):
        from src.execution.live_submit_executor import _check_idempotency
        assert _check_idempotency(False, "order-001") == []

    def test_already_used_blocks(self):
        from src.execution.live_submit_executor import _check_idempotency
        v = _check_idempotency(True, "order-001")
        assert any("order-001" in x for x in v)


class TestCheckNotionalCap:
    def test_within_cap_passes(self):
        from src.execution.live_submit_executor import _check_notional_cap
        assert _check_notional_cap(95.0, 100.0) == []

    def test_at_cap_passes(self):
        from src.execution.live_submit_executor import _check_notional_cap
        assert _check_notional_cap(100.0, 100.0) == []

    def test_exceeds_cap_blocks(self):
        from src.execution.live_submit_executor import _check_notional_cap
        v = _check_notional_cap(101.0, 100.0)
        assert len(v) == 1


# ---------------------------------------------------------------------------
# maybe_execute_live_submit — always blocked with current approval artifact
# ---------------------------------------------------------------------------

class TestMaybeExecuteLiveSubmit:
    def test_submit_order_never_called_default_config(self, tmp_path):
        """Default config (3 blocking flags) → submit_order never called."""
        kwargs = _default_kwargs(tmp_path,
            live_trading_enabled=False,
            live_submit_dry_run=True,
            live_kill_switch_enabled=True,
        )
        result = _run(**kwargs)
        assert result["submit_order_called"] is False
        assert result["blocked"] is True

    def test_live_trading_approved_false_blocks(self, tmp_path):
        """Approval artifact with live_trading_approved=false always blocks."""
        # Even with permissive config, approval artifact blocks
        result = _run(**_default_kwargs(tmp_path))
        assert result["submit_order_called"] is False
        assert result["blocked"] is True
        assert result["block_guard"] == "approval_artifact"

    def test_live_order_submission_approved_false_blocks(self, tmp_path):
        """Same block; live_order_submission_approved=false is always in artifact."""
        result = _run(**_default_kwargs(tmp_path))
        assert result["submit_order_called"] is False
        violations = result["violations"]
        assert any("live_order_submission_approved" in v for v in violations)

    def test_dry_run_true_blocks(self, tmp_path):
        """live_submit_dry_run=true in config blocks at config_safety guard."""
        # Use approval with approved flags to isolate config guard
        approval = _write(tmp_path, "approval.json", _valid_approval(
            live_trading_approved=True,
            live_order_submission_approved=True,
        ))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_submit_dry_run=True,
        ))
        assert result["submit_order_called"] is False
        assert result["blocked"] is True
        assert result["block_guard"] == "config_safety"
        assert any("live_submit_dry_run" in v for v in result["violations"])

    def test_kill_switch_true_blocks(self, tmp_path):
        """live_kill_switch_enabled=true blocks at config_safety guard."""
        approval = _write(tmp_path, "approval.json", _valid_approval(
            live_trading_approved=True,
            live_order_submission_approved=True,
        ))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_kill_switch_enabled=True,
        ))
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "config_safety"
        assert any("live_kill_switch_enabled" in v for v in result["violations"])

    def test_wrong_confirmation_token_blocks(self, tmp_path):
        result = _run(**_default_kwargs(tmp_path, confirm_token="wrong-token"))
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "confirm_token"

    def test_missing_approval_artifact_blocks(self, tmp_path):
        result = _run(**_default_kwargs(tmp_path,
            approval_path=tmp_path / "nonexistent.json",
        ))
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "approval_artifact_load"

    def test_daily_limit_exceeded_blocks(self, tmp_path):
        approval = _write(tmp_path, "approval.json", _valid_approval(
            live_trading_approved=True,
            live_order_submission_approved=True,
        ))
        with patch(f"{_MODULE}._count_today_orders", return_value=(1, 0.0)):
            result = _run(**_default_kwargs(tmp_path,
                approval_path=approval,
                live_max_orders_per_day=1,
            ))
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "daily_limits"

    def test_idempotency_duplicate_blocks(self, tmp_path):
        approval = _write(tmp_path, "approval.json", _valid_approval(
            live_trading_approved=True,
            live_order_submission_approved=True,
        ))
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=True):
            result = _run(**_default_kwargs(tmp_path,
                approval_path=approval,
                client_order_id="dup-order",
            ))
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "idempotency"

    def test_open_position_blocks(self, tmp_path):
        approval = _write(tmp_path, "approval.json", _valid_approval(
            live_trading_approved=True,
            live_order_submission_approved=True,
        ))
        broker = MagicMock()
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False), \
             patch(f"{_MODULE}._check_broker_position", return_value=True), \
             patch(f"{_MODULE}._check_broker_open_order", return_value=False):
            result = _run(**_default_kwargs(tmp_path,
                approval_path=approval,
                broker_client=broker,
            ))
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "open_conflict"
        broker.submit_order.assert_not_called()

    def test_open_order_blocks(self, tmp_path):
        approval = _write(tmp_path, "approval.json", _valid_approval(
            live_trading_approved=True,
            live_order_submission_approved=True,
        ))
        broker = MagicMock()
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False), \
             patch(f"{_MODULE}._check_broker_position", return_value=False), \
             patch(f"{_MODULE}._check_broker_open_order", return_value=True):
            result = _run(**_default_kwargs(tmp_path,
                approval_path=approval,
                broker_client=broker,
            ))
        assert result["submit_order_called"] is False
        broker.submit_order.assert_not_called()

    def test_blocked_report_written_on_every_block(self, tmp_path):
        result = _run(**_default_kwargs(tmp_path))
        report_path = tmp_path / "output" / "live_submit_blocked_report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["submit_order_called"] is False
        assert data["blocked"] is True

    def test_blocked_report_includes_guard_name(self, tmp_path):
        result = _run(**_default_kwargs(tmp_path, confirm_token="bad"))
        report_path = tmp_path / "output" / "live_submit_blocked_report.json"
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["block_guard"] == "confirm_token"

    def test_ledger_not_written_on_blocked_path(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(**_default_kwargs(tmp_path, ledger_path=ledger))
        assert not ledger.exists()

    def test_no_alpaca_calls(self, tmp_path):
        broker = MagicMock()
        _run(**_default_kwargs(tmp_path, broker_client=broker))
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_needed(self, tmp_path):
        import os
        from unittest.mock import patch as _patch
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with _patch.dict(os.environ, env, clear=True):
            result = _run(**_default_kwargs(tmp_path))
        assert result["submit_order_called"] is False


# ---------------------------------------------------------------------------
# All-guards-passing path still fails closed
# ---------------------------------------------------------------------------

class TestAllGuardsPassingFailsClosed:
    """Prove that even when all 18 guards pass, blocked=True and submit_order
    is never called.  This verifies the fail-closed invariant for this PR."""

    def _all_passing_kwargs(self, tmp_path: Path) -> dict:
        """Build kwargs where every guard would pass — except the final
        real_submit_not_implemented guard which always blocks."""
        approval = _write(tmp_path, "full_approval.json", _valid_approval(
            live_trading_approved=True,
            live_order_submission_approved=True,
        ))
        checklist = _write(tmp_path, "full_checklist.json", _valid_checklist())
        review = _write(tmp_path, "full_review.json", _valid_review())
        output_dir = tmp_path / "full_output"
        return dict(
            confirm_token=              "REAL-LIVE-SUBMIT-AUTHORIZED",
            approval_path=              approval,
            checklist_path=             checklist,
            review_path=                review,
            symbol=                     "SPY",
            intended_notional=          95.0,
            client_order_id=            "all-pass-order-001",
            live_trading_enabled=       True,
            live_submit_dry_run=        False,
            live_kill_switch_enabled=   False,
            live_require_human_confirm= True,
            live_max_order_notional=    100.0,
            live_max_orders_per_day=    1,
            live_max_notional_per_day=  100.0,
            ledger_path=                tmp_path / "full_ledger.csv",
            output_dir=                 output_dir,
            broker_client=              None,
        )

    def test_all_guards_pass_still_blocked(self, tmp_path):
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**self._all_passing_kwargs(tmp_path))
        assert result["blocked"] is True

    def test_all_guards_pass_block_guard_is_not_implemented(self, tmp_path):
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**self._all_passing_kwargs(tmp_path))
        assert result["block_guard"] == "real_submit_not_implemented"

    def test_all_guards_pass_submit_order_called_false(self, tmp_path):
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**self._all_passing_kwargs(tmp_path))
        assert result["submit_order_called"] is False

    def test_all_guards_pass_broker_submit_order_not_called(self, tmp_path):
        broker = MagicMock()
        kwargs = self._all_passing_kwargs(tmp_path)
        kwargs["broker_client"] = broker
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False), \
             patch(f"{_MODULE}._check_broker_position", return_value=False), \
             patch(f"{_MODULE}._check_broker_open_order", return_value=False):
            _run(**kwargs)
        broker.submit_order.assert_not_called()

    def test_all_guards_pass_blocked_report_written(self, tmp_path):
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            _run(**self._all_passing_kwargs(tmp_path))
        report_path = tmp_path / "full_output" / "live_submit_blocked_report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["submit_order_called"] is False
        assert data["blocked"] is True
        assert data["block_guard"] == "real_submit_not_implemented"

    def test_all_guards_pass_no_ledger_write(self, tmp_path):
        ledger = tmp_path / "full_ledger.csv"
        kwargs = self._all_passing_kwargs(tmp_path)
        kwargs["ledger_path"] = ledger
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            _run(**kwargs)
        assert not ledger.exists()


# ---------------------------------------------------------------------------
# V2 approval artifact guards
# ---------------------------------------------------------------------------

def _valid_v2_ta(**overrides) -> dict:
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


def _valid_v2_sa(**overrides) -> dict:
    d = {
        "live_trading_approved":                        True,
        "live_order_submission_approved":               True,
        "order_submission_approval_for_single_attempt": True,
        "approval_scope":                               "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":                            True,
        "approved_symbol":                              "SPY",
        "approved_max_notional":                        100.0,
        # source_live_trading_approval_path must be overridden with the real ta file
        # path in integration tests; the placeholder satisfies the non-empty check in
        # unit tests where ta_path is not passed to _check_v2_submission_approval.
        "source_live_trading_approval_path":            "/placeholder/ta.json",
    }
    d.update(overrides)
    return d


def _v2_kwargs(tmp_path: Path, **extra) -> dict:
    """Build kwargs for v2 mode tests using a real current-style approval artifact.

    The old PR-approval has live_trading_approved=False (as current tooling always
    produces).  In v2 mode the executor uses _check_approval_artifact_v2_mode,
    which does not require those fields, so this naturally passes the approval guard.
    """
    approval = _write(tmp_path, "_real_approval.json", _valid_approval())
    ta_path = _write(tmp_path, "_v2_ta.json", _valid_v2_ta())
    sa_path = _write(tmp_path, "_v2_sa.json",
                     _valid_v2_sa(source_live_trading_approval_path=str(ta_path)))
    base = _default_kwargs(tmp_path, approval_path=approval,
                           live_trading_approval_path=ta_path,
                           live_order_submission_approval_path=sa_path)
    base.update(extra)
    return base


def _permissive_approval_kwargs(tmp_path: Path, **extra) -> dict:
    """Build kwargs with a permissive approval_artifact (live_trading_approved=true).

    Kept for legacy-mode tests.  In v2 mode _permissive_approval_kwargs is also
    still valid because _check_approval_artifact_v2_mode ignores those fields.
    """
    approval = _write(tmp_path, "_perm_approval.json", _valid_approval(
        live_trading_approved=True,
        live_order_submission_approved=True,
    ))
    ta_path = _write(tmp_path, "_v2_ta.json", _valid_v2_ta())
    sa_path = _write(tmp_path, "_v2_sa.json",
                     _valid_v2_sa(source_live_trading_approval_path=str(ta_path)))
    base = _default_kwargs(tmp_path, approval_path=approval,
                           live_trading_approval_path=ta_path,
                           live_order_submission_approval_path=sa_path)
    base.update(extra)
    return base


class TestCheckV2TradingApproval:
    def _check(self, **overrides):
        from src.execution.live_submit_executor import _check_v2_trading_approval
        ta = _valid_v2_ta(**overrides)
        return _check_v2_trading_approval(ta, "SPY", 95.0)

    def test_valid_passes(self):
        assert self._check() == []

    def test_live_trading_approved_false_fails(self):
        assert self._check(live_trading_approved=False) != []

    def test_live_order_submission_approved_true_fails(self):
        assert self._check(live_order_submission_approved=True) != []

    def test_wrong_scope_fails(self):
        assert self._check(approval_scope="WRONG") != []

    def test_risk_acknowledged_false_fails(self):
        assert self._check(risk_acknowledged=False) != []

    def test_symbol_mismatch_fails(self):
        from src.execution.live_submit_executor import _check_v2_trading_approval
        ta = _valid_v2_ta(approved_symbol="QQQ")
        assert _check_v2_trading_approval(ta, "SPY", 95.0) != []

    def test_notional_over_cap_fails(self):
        from src.execution.live_submit_executor import _check_v2_trading_approval
        ta = _valid_v2_ta(approved_max_notional=50.0)
        assert _check_v2_trading_approval(ta, "SPY", 95.0) != []

    def test_notional_at_cap_passes(self):
        from src.execution.live_submit_executor import _check_v2_trading_approval
        ta = _valid_v2_ta(approved_max_notional=95.0)
        assert _check_v2_trading_approval(ta, "SPY", 95.0) == []


class TestCheckV2SubmissionApproval:
    def _check(self, **overrides):
        from src.execution.live_submit_executor import _check_v2_submission_approval
        sa = _valid_v2_sa(**overrides)
        # ta_path=None: source path is validated for non-empty only
        return _check_v2_submission_approval(sa, "SPY", 95.0)

    def test_valid_passes(self):
        assert self._check() == []

    def test_order_submission_approved_false_fails(self):
        assert self._check(live_order_submission_approved=False) != []

    def test_single_attempt_false_fails(self):
        assert self._check(order_submission_approval_for_single_attempt=False) != []

    def test_wrong_scope_fails(self):
        assert self._check(approval_scope="WRONG") != []

    def test_risk_acknowledged_false_fails(self):
        assert self._check(risk_acknowledged=False) != []

    def test_symbol_mismatch_fails(self):
        from src.execution.live_submit_executor import _check_v2_submission_approval
        sa = _valid_v2_sa(approved_symbol="QQQ")
        assert _check_v2_submission_approval(sa, "SPY", 95.0) != []

    def test_notional_over_cap_fails(self):
        from src.execution.live_submit_executor import _check_v2_submission_approval
        sa = _valid_v2_sa(approved_max_notional=50.0)
        assert _check_v2_submission_approval(sa, "SPY", 95.0) != []

    def test_missing_source_path_fails(self):
        v = self._check(source_live_trading_approval_path="")
        assert any("source_live_trading_approval_path" in x for x in v)

    def test_source_path_mismatch_fails(self, tmp_path):
        from pathlib import Path
        from src.execution.live_submit_executor import _check_v2_submission_approval
        ta_path = tmp_path / "ta.json"
        ta_path.write_text("{}", encoding="utf-8")
        sa = _valid_v2_sa(source_live_trading_approval_path="/different/path/ta.json")
        v = _check_v2_submission_approval(sa, "SPY", 95.0, ta_path=ta_path)
        assert any("source_live_trading_approval_path" in x for x in v)

    def test_source_path_match_passes(self, tmp_path):
        from src.execution.live_submit_executor import _check_v2_submission_approval
        ta_path = tmp_path / "ta.json"
        ta_path.write_text("{}", encoding="utf-8")
        sa = _valid_v2_sa(source_live_trading_approval_path=str(ta_path))
        v = _check_v2_submission_approval(sa, "SPY", 95.0, ta_path=ta_path)
        assert not any("source_live_trading_approval_path" in x for x in v)


class TestV2ApprovalIntegration:
    def test_no_v2_paths_keeps_old_blocking_behavior(self, tmp_path):
        """Without v2 paths, approval_artifact guard still blocks as before."""
        result = _run(**_default_kwargs(tmp_path))
        assert result["blocked"] is True
        assert result["block_guard"] == "approval_artifact"
        assert result["submit_order_called"] is False

    def test_real_current_approval_with_v2_reaches_not_implemented(self, tmp_path):
        """Real current-style approval (live_trading_approved=False) + valid v2
        artifacts + permissive config must reach real_submit_not_implemented.

        This is the core semantic fix: in v2 mode the approval_artifact guard
        only checks approval_for_real_submit_pr and approval_scope, so the
        always-false live_trading_approved on the legacy artifact no longer blocks.
        """
        kwargs = _v2_kwargs(tmp_path)
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**kwargs)
        assert result["blocked"] is True
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "real_submit_not_implemented"

    def test_valid_v2_approvals_pass_approval_artifact_guard(self, tmp_path):
        """Valid v2 approvals (with any approval artifact) allow executor past
        the approval_artifact guard and reach real_submit_not_implemented."""
        kwargs = _v2_kwargs(tmp_path)
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**kwargs)
        assert result["blocked"] is True
        assert result["submit_order_called"] is False
        assert result["block_guard"] == "real_submit_not_implemented"

    def test_invalid_v2_trading_approval_blocks(self, tmp_path):
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        bad_ta = _write(tmp_path, "_bad_ta.json",
                        _valid_v2_ta(live_trading_approved=False))
        sa_path = _write(tmp_path, "_v2_sa.json",
                         _valid_v2_sa(source_live_trading_approval_path=str(bad_ta)))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=bad_ta,
            live_order_submission_approval_path=sa_path,
        ))
        assert result["blocked"] is True
        assert result["block_guard"] == "v2_trading_approval"
        assert result["submit_order_called"] is False

    def test_invalid_v2_submission_approval_blocks(self, tmp_path):
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        ta_path = _write(tmp_path, "_v2_ta.json", _valid_v2_ta())
        bad_sa = _write(tmp_path, "_bad_sa.json", _valid_v2_sa(
            live_order_submission_approved=False,
            source_live_trading_approval_path=str(ta_path),
        ))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=bad_sa,
        ))
        assert result["blocked"] is True
        assert result["block_guard"] == "v2_submission_approval"
        assert result["submit_order_called"] is False

    def test_v2_symbol_mismatch_blocks(self, tmp_path):
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        ta_path = _write(tmp_path, "_v2_ta.json",
                         _valid_v2_ta(approved_symbol="QQQ"))
        sa_path = _write(tmp_path, "_v2_sa.json", _valid_v2_sa(
            approved_symbol="QQQ",
            source_live_trading_approval_path=str(ta_path),
        ))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=sa_path,
            symbol="SPY",
        ))
        assert result["blocked"] is True
        assert result["block_guard"] == "v2_trading_approval"

    def test_v2_notional_over_cap_blocks(self, tmp_path):
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        ta_path = _write(tmp_path, "_v2_ta.json",
                         _valid_v2_ta(approved_max_notional=50.0))
        sa_path = _write(tmp_path, "_v2_sa.json", _valid_v2_sa(
            approved_max_notional=50.0,
            source_live_trading_approval_path=str(ta_path),
        ))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=sa_path,
            intended_notional=95.0,
        ))
        assert result["blocked"] is True
        assert result["block_guard"] == "v2_trading_approval"

    def test_all_guards_with_v2_still_blocked_at_not_implemented(self, tmp_path):
        """Even with v2 approvals + all other guards passing, fail closed."""
        ta_path = _write(tmp_path, "_v2_ta.json", _valid_v2_ta())
        sa_path = _write(tmp_path, "_v2_sa.json",
                         _valid_v2_sa(source_live_trading_approval_path=str(ta_path)))
        approval = _write(tmp_path, "_full_approval.json", _valid_approval())
        checklist = _write(tmp_path, "_full_checklist.json", {"final_result": "READY"})
        review = _write(tmp_path, "_full_review.json", {"review_result": "PASS"})
        kwargs = dict(
            confirm_token="REAL-LIVE-SUBMIT-AUTHORIZED",
            approval_path=approval,
            checklist_path=checklist,
            review_path=review,
            symbol="SPY",
            intended_notional=95.0,
            client_order_id="v2-all-pass-001",
            live_trading_enabled=True,
            live_submit_dry_run=False,
            live_kill_switch_enabled=False,
            live_require_human_confirm=True,
            live_max_order_notional=100.0,
            live_max_orders_per_day=1,
            live_max_notional_per_day=100.0,
            ledger_path=tmp_path / "ledger.csv",
            output_dir=tmp_path / "output",
            broker_client=None,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=sa_path,
        )
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**kwargs)
        assert result["blocked"] is True
        assert result["block_guard"] == "real_submit_not_implemented"
        assert result["submit_order_called"] is False

    def test_submit_order_never_called_with_v2(self, tmp_path):
        broker = MagicMock()
        kwargs = _v2_kwargs(tmp_path, broker_client=broker)
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**kwargs)
        broker.submit_order.assert_not_called()
        assert result["submit_order_called"] is False

    def test_cancel_order_never_called_with_v2(self, tmp_path):
        broker = MagicMock()
        kwargs = _v2_kwargs(tmp_path, broker_client=broker)
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            _run(**kwargs)
        broker.cancel_order.assert_not_called()

    def test_no_ledger_writes_with_v2(self, tmp_path):
        ledger = tmp_path / "v2_ledger.csv"
        kwargs = _v2_kwargs(tmp_path, ledger_path=ledger)
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            _run(**kwargs)
        assert not ledger.exists()

    def test_missing_v2_trading_approval_file_blocks(self, tmp_path):
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        sa_path = _write(tmp_path, "_v2_sa.json",
                         _valid_v2_sa(source_live_trading_approval_path="/placeholder/ta.json"))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=tmp_path / "nonexistent_ta.json",
            live_order_submission_approval_path=sa_path,
        ))
        assert result["blocked"] is True
        assert result["block_guard"] == "v2_trading_approval_load"

    def test_source_path_mismatch_blocks(self, tmp_path):
        """sa.source_live_trading_approval_path pointing to wrong file blocks."""
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        ta_path = _write(tmp_path, "_v2_ta.json", _valid_v2_ta())
        wrong_path = tmp_path / "other_ta.json"
        wrong_path.write_text("{}", encoding="utf-8")
        sa_path = _write(tmp_path, "_v2_sa.json",
                         _valid_v2_sa(source_live_trading_approval_path=str(wrong_path)))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=sa_path,
        ))
        assert result["blocked"] is True
        assert result["block_guard"] == "v2_submission_approval"
        assert result["submit_order_called"] is False

    def test_same_file_for_ta_and_sa_blocks(self, tmp_path):
        """Providing the same file for both v2 approval paths blocks at cross-check."""
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        ta_path = _write(tmp_path, "_v2_combined.json", _valid_v2_ta())
        # sa with correct source path but same file as ta
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=ta_path,  # same file
        ))
        assert result["blocked"] is True
        # blocked either at v2_submission_approval (missing fields) or v2_cross_check
        assert result["block_guard"] in ("v2_submission_approval", "v2_cross_check")
        assert result["submit_order_called"] is False

    def test_submission_notional_exceeds_trading_notional_blocks(self, tmp_path):
        """sa.approved_max_notional > ta.approved_max_notional blocks at cross-check."""
        approval = _write(tmp_path, "_real_approval.json", _valid_approval())
        ta_path = _write(tmp_path, "_v2_ta.json",
                         _valid_v2_ta(approved_max_notional=80.0))
        sa_path = _write(tmp_path, "_v2_sa.json", _valid_v2_sa(
            approved_max_notional=100.0,  # higher than trading cap
            source_live_trading_approval_path=str(ta_path),
        ))
        result = _run(**_default_kwargs(tmp_path,
            approval_path=approval,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=sa_path,
            intended_notional=70.0,
        ))
        assert result["blocked"] is True
        assert result["block_guard"] == "v2_cross_check"
        assert result["submit_order_called"] is False

    def test_no_return_path_with_blocked_false(self, tmp_path):
        """Prove there is no blocked=False return path even with v2 approvals."""
        ta_path = _write(tmp_path, "_v2_ta2.json", _valid_v2_ta())
        sa_path = _write(tmp_path, "_v2_sa2.json",
                         _valid_v2_sa(source_live_trading_approval_path=str(ta_path)))
        approval = _write(tmp_path, "_full2_approval.json", _valid_approval())
        checklist = _write(tmp_path, "_full2_checklist.json", {"final_result": "READY"})
        review = _write(tmp_path, "_full2_review.json", {"review_result": "PASS"})
        kwargs = dict(
            confirm_token="REAL-LIVE-SUBMIT-AUTHORIZED",
            approval_path=approval,
            checklist_path=checklist,
            review_path=review,
            symbol="SPY",
            intended_notional=95.0,
            client_order_id="v2-no-false-001",
            live_trading_enabled=True,
            live_submit_dry_run=False,
            live_kill_switch_enabled=False,
            live_require_human_confirm=True,
            live_max_order_notional=100.0,
            live_max_orders_per_day=1,
            live_max_notional_per_day=100.0,
            ledger_path=tmp_path / "ledger2.csv",
            output_dir=tmp_path / "output2",
            broker_client=None,
            live_trading_approval_path=ta_path,
            live_order_submission_approval_path=sa_path,
        )
        with patch(f"{_MODULE}._count_today_orders", return_value=(0, 0.0)), \
             patch(f"{_MODULE}._is_client_order_id_used", return_value=False):
            result = _run(**kwargs)
        assert result.get("blocked") is not False
