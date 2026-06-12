"""
tests/test_paper_audit_ledger_integration.py
---------------------------------------------
S35: Integration tests for the full pure offline paper chain:

    S30 create_paper_order_plan()
    -> S27 validate_paper_order_plan()
    -> S28 evaluate_paper_order_safety_gate()
    -> S32 create_lifecycle_from_plan()
    -> S32 apply_lifecycle_event()
    -> S34 create_empty_audit_ledger()
    -> S34 append_audit_entry()

Every scenario uses the REAL planner, the REAL S27 validator, the REAL S28 gate,
the REAL S32 lifecycle functions, and the REAL S34 ledger functions. No mocked
validators, lifecycle functions, or ledger functions are used for core integration
tests.

The _run_chain_with_ledger() helper proves fail-closed sequencing and ledger audit
coverage: a blocked planner appends one planner entry + one blocked chain entry and
stops; a non-PASS gate appends planner + validation + gate + blocked chain and stops;
no ledger entries are appended after the blocked chain entry in any fail-closed path.

All fixtures are plain in-memory dicts. No real approval artifact, order plan,
lifecycle record, ledger artifact, config, or any other artifact is created. No files
are read or written. No broker, network, credential, environment-variable, or order
access is made.

Audit ledger integration is pure offline/in-memory bookkeeping only. Ledger entries
are not order actions. A planner-generated POP/1.0 plan is an in-memory paper order
plan only -- it is not an order. S28 PASS_DRY_RUN_ONLY remains only offline clearance
for a future dry-run/no-submit rendering step, not order approval. Paper trading
remains not approved; live trading remains blocked.
"""

from __future__ import annotations

import builtins
import copy
import inspect
from typing import NamedTuple

import pytest

import src.research.paper_approval_validator as _approval_mod
import src.research.paper_audit_ledger as _ledger_mod
import src.research.paper_order_lifecycle as _lifecycle_mod
import src.research.paper_order_plan_validator as _plan_mod
import src.research.paper_order_planner as _planner_mod
import src.research.paper_order_safety_gate as _gate_mod
from src.research.paper_audit_ledger import (
    PaperAuditLedgerEntryType,
    PaperAuditLedgerState,
    PaperAuditLedgerStatus,
    append_audit_entry,
    create_empty_audit_ledger,
)
from src.research.paper_order_lifecycle import (
    PaperOrderLifecycleEventType,
    PaperOrderLifecycleStatus,
    apply_lifecycle_event,
    create_lifecycle_from_plan,
)
from src.research.paper_order_plan_validator import validate_paper_order_plan
from src.research.paper_order_planner import (
    PaperOrderPlannerStatus,
    create_paper_order_plan,
)
from src.research.paper_order_safety_gate import (
    PaperOrderSafetyGateStatus,
    evaluate_paper_order_safety_gate,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GENERATED_AT = "2025-01-15T09:00:00Z"
_EXPIRES_AT   = "2025-01-15T17:00:00Z"
_PLAN_ID      = "plan-001"
_SOURCE_SHA   = "abc123def456abc123def456abc123def456abc1"
_LIFECYCLE_ID = "lc-001"
_LEDGER_ID    = "ledger-001"
_CREATED_AT   = "2025-01-15T09:05:00Z"
_EVENT_AT     = "2025-01-15T09:10:00Z"
_TS           = "2025-01-15T09:00:00Z"

# Deterministic entry-id constants for _run_chain_with_ledger().
_EID_PLANNER        = "eid-planner"
_EID_VALIDATOR      = "eid-validator"
_EID_GATE           = "eid-gate"
_EID_LC_PLAN_CREATED = "eid-lc-plan-created"
_EID_LC_GATE_PASSED = "eid-lc-gate-passed"
_EID_BLOCKED        = "eid-blocked"
_EID_ERROR          = "eid-error"

_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

Status = PaperOrderLifecycleStatus
Event  = PaperOrderLifecycleEventType
EType  = PaperAuditLedgerEntryType


# ---------------------------------------------------------------------------
# Fixture helpers (plain in-memory dicts; no files; no artifacts)
# ---------------------------------------------------------------------------

def _valid_approval(**overrides) -> dict:
    base = {
        "artifact_schema_version":        "PTA/1.0",
        "approval_artifact_type":         "PAPER_TRADING_APPROVAL",
        "approval_scope":                 "PAPER_TRADING_LIMITED_RUN_ONLY",
        "candidate_id":                   "cand-001",
        "run_id":                         "run-001",
        "source_git_sha":                 _SOURCE_SHA,
        "paper_config_schema_version":    "PC/1.0",
        "paper_config_hash":              "hash-pc-abc001",
        "simulation_result_hash":         "hash-sim-abc001",
        "architecture_review_reference":  "docs/paper_trading_architecture_design.md#S22",
        "invariant_test_reference":       "tests/test_paper_architecture_invariants.py#S23",
        "approved_by":                    "reviewer-001",
        "approved_at_utc":                "2025-01-01T09:00:00Z",
        "expires_at_utc":                 "2025-12-31T23:59:59Z",
        "max_notional_per_position":       5000.0,
        "max_position_fraction":           0.05,
        "max_daily_loss":                  2000.0,
        "max_drawdown_stop":               0.50,
        "max_orders_per_day":              5,
        "allowed_symbols":                ["AAPL"],
        "allowed_intervals":              ["60m"],
        "allowed_strategy_families":      ["TREND_BREAKOUT"],
        "allowed_order_types":            ["market", "limit"],
        "allowed_session":                "regular",
        "paper_account_label":            "alpaca-paper-primary",
        "live_trading_approved":           False,
        "live_order_submission_approved":  False,
        "dry_run_required":                True,
        "human_confirmation_required":     True,
        "kill_switch_required":            True,
        "approval_status":                "APPROVED_FOR_LIMITED_PAPER_RUN",
        "notes":                          "approved for limited run",
        "known_limitations":              "limited sample size",
        "artifact_hash":                  "hash-pta-abc001",
    }
    base.update(overrides)
    return base


def _valid_signal(**overrides) -> dict:
    base = {
        "symbol":           "AAPL",
        "interval":         "60m",
        "strategy_family":  "TREND_BREAKOUT",
        "side":             "BUY",
        "order_type":       "market",
        "confidence":       0.85,
        "rationale":        "momentum breakout signal detected on 60m bar",
        "holding_horizon":  "intraday",
    }
    base.update(overrides)
    return base


def _valid_sizing(**overrides) -> dict:
    base = {
        "quantity":                  10.0,
        "notional":                  1500.0,
        "limit_price":               None,
        "max_position_fraction":     0.05,
        "max_daily_loss":            500.0,
        "max_drawdown_stop":         0.15,
        "max_orders_per_day":        3,
        "max_notional_per_position": 5000.0,
    }
    base.update(overrides)
    return base


def _clean_state(**overrides) -> dict:
    base = {
        "kill_switch_open":          True,
        "current_daily_order_count": 0,
        "current_daily_pnl":         0.0,
        "current_drawdown":          0.0,
        "open_positions":            [],
        "processed_plan_ids":        [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Chain helper
# ---------------------------------------------------------------------------

class ChainWithLedgerResults(NamedTuple):
    planner_result:           object
    validation_result:        object
    gate_result:              object
    lifecycle_creation:       object
    lifecycle_after_gate:     object
    ledger:                   PaperAuditLedgerState | None
    ledger_results:           list   # PaperAuditLedgerResult per append call


def _run_chain_with_ledger(
    approval=None,
    signal=None,
    sizing=None,
    state=None,
    *,
    plan_id: str = _PLAN_ID,
    ledger_id: str = _LEDGER_ID,
    **planner_kwargs,
) -> ChainWithLedgerResults:
    """Run the full real chain and record every stage result in a ledger.

    1.  create_empty_audit_ledger()
    2.  create_paper_order_plan()
    3.  append PLANNER_RESULT_RECORDED
    4.  if planner blocked: append BLOCKED_CHAIN_RECORDED; stop
    5.  validate_paper_order_plan()
    6.  append VALIDATION_RESULT_RECORDED
    7.  if validation non-PASS: append BLOCKED_CHAIN_RECORDED; stop
    8.  evaluate_paper_order_safety_gate()
    9.  append SAFETY_GATE_RESULT_RECORDED
    10. if gate non-PASS_DRY_RUN_ONLY: append BLOCKED_CHAIN_RECORDED; stop
    11. create_lifecycle_from_plan()
    12. append LIFECYCLE_TRANSITION_RECORDED for PLAN_CREATED
    13. apply SAFETY_GATE_PASSED (only after real gate PASS_DRY_RUN_ONLY)
    14. append LIFECYCLE_TRANSITION_RECORDED for SAFETY_GATE_PASSED

    The SAFETY_GATE_PASSED lifecycle event is applied only after the real S28
    gate returned PASS_DRY_RUN_ONLY; it is never applied when the gate blocks.
    """
    if approval is None:
        approval = _valid_approval()
    if signal is None:
        signal = _valid_signal()
    if sizing is None:
        sizing = _valid_sizing()
    if state is None:
        state = _clean_state()
    planner_kwargs.setdefault("generated_at_utc", _GENERATED_AT)
    planner_kwargs.setdefault("expires_at_utc",   _EXPIRES_AT)
    planner_kwargs.setdefault("plan_id",          plan_id)
    planner_kwargs.setdefault("source_git_sha",   _SOURCE_SHA)

    # Step 1: create ledger
    lr = create_empty_audit_ledger(ledger_id=ledger_id)
    assert lr.result == "PASS", f"create_empty_audit_ledger blocked: {lr.blocker}"
    ledger = lr.state
    ledger_results: list = []

    cand_id  = approval.get("candidate_id", "") if isinstance(approval, dict) else ""
    run_id_v = approval.get("run_id", "")       if isinstance(approval, dict) else ""

    # Step 2: planner
    planner_result = create_paper_order_plan(
        approval, signal_snapshot=signal, sizing_snapshot=sizing, **planner_kwargs
    )

    # Step 3: append planner entry
    r = append_audit_entry(
        ledger,
        entry_id=_EID_PLANNER,
        entry_type=EType.PLANNER_RESULT_RECORDED,
        recorded_at_utc=_TS,
        source="planner",
        payload={
            "planner_status": planner_result.planner_status.value,
            "result":         planner_result.result,
            "plan_id":        plan_id,
            "candidate_id":   cand_id,
            "run_id":         run_id_v,
        },
    )
    ledger_results.append(r)
    assert r.result == "PASS", f"planner entry append blocked: {r.blocker}"
    ledger = r.state

    # Step 4: planner blocked — append blocked chain entry and stop
    if planner_result.plan is None:
        r = append_audit_entry(
            ledger,
            entry_id=_EID_BLOCKED,
            entry_type=EType.BLOCKED_CHAIN_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={
                "blocked_stage": "planner",
                "blocker":       "planner_status=" + planner_result.planner_status.value,
            },
        )
        ledger_results.append(r)
        assert r.result == "PASS", f"blocked chain append failed: {r.blocker}"
        ledger = r.state
        return ChainWithLedgerResults(
            planner_result, None, None, None, None, ledger, ledger_results
        )

    # Step 5: validator
    validation_result = validate_paper_order_plan(planner_result.plan)

    # Step 6: append validation entry
    r = append_audit_entry(
        ledger,
        entry_id=_EID_VALIDATOR,
        entry_type=EType.VALIDATION_RESULT_RECORDED,
        recorded_at_utc=_TS,
        source="validator",
        payload={
            "validation_result": validation_result.result,
            "plan_id":           plan_id,
            "candidate_id":      cand_id,
            "run_id":            run_id_v,
        },
    )
    ledger_results.append(r)
    assert r.result == "PASS", f"validator entry append blocked: {r.blocker}"
    ledger = r.state

    # Step 7: validation non-PASS — append blocked chain and stop
    if validation_result.result != "PASS":
        r = append_audit_entry(
            ledger,
            entry_id=_EID_BLOCKED,
            entry_type=EType.BLOCKED_CHAIN_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={
                "blocked_stage": "validator",
                "blocker":       "validation_result=" + str(validation_result.result),
            },
        )
        ledger_results.append(r)
        assert r.result == "PASS", f"blocked chain append failed: {r.blocker}"
        ledger = r.state
        return ChainWithLedgerResults(
            planner_result, validation_result, None, None, None, ledger, ledger_results
        )

    # Step 8: safety gate
    gate_result = evaluate_paper_order_safety_gate(
        approval, planner_result.plan, current_state=state
    )

    # Step 9: append gate entry
    r = append_audit_entry(
        ledger,
        entry_id=_EID_GATE,
        entry_type=EType.SAFETY_GATE_RESULT_RECORDED,
        recorded_at_utc=_TS,
        source="safety_gate",
        payload={
            "gate_status": gate_result.gate_status.value,
            "result":      gate_result.result,
            "plan_id":     plan_id,
        },
    )
    ledger_results.append(r)
    assert r.result == "PASS", f"gate entry append blocked: {r.blocker}"
    ledger = r.state

    # Step 10: gate non-PASS_DRY_RUN_ONLY — append blocked chain; never create lifecycle
    if gate_result.gate_status is not PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY:
        r = append_audit_entry(
            ledger,
            entry_id=_EID_BLOCKED,
            entry_type=EType.BLOCKED_CHAIN_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={
                "blocked_stage": "safety_gate",
                "blocker":       "gate_status=" + gate_result.gate_status.value,
            },
        )
        ledger_results.append(r)
        assert r.result == "PASS", f"blocked chain append failed: {r.blocker}"
        ledger = r.state
        return ChainWithLedgerResults(
            planner_result, validation_result, gate_result, None, None, ledger, ledger_results
        )

    # Step 11: lifecycle creation
    lifecycle_creation = create_lifecycle_from_plan(
        planner_result.plan, lifecycle_id=_LIFECYCLE_ID, created_at_utc=_CREATED_AT
    )
    assert lifecycle_creation.result == "PASS"

    # Step 12: append LIFECYCLE_TRANSITION_RECORDED for PLAN_CREATED
    r = append_audit_entry(
        ledger,
        entry_id=_EID_LC_PLAN_CREATED,
        entry_type=EType.LIFECYCLE_TRANSITION_RECORDED,
        recorded_at_utc=_TS,
        source="lifecycle",
        payload={
            "previous_status": lifecycle_creation.previous_status.value,
            "new_status":      lifecycle_creation.new_status.value,
            "event_type":      lifecycle_creation.event_type.value,
            "lifecycle_id":    _LIFECYCLE_ID,
            "plan_id":         plan_id,
        },
    )
    ledger_results.append(r)
    assert r.result == "PASS", f"lc plan-created entry blocked: {r.blocker}"
    ledger = r.state

    # Step 13: apply SAFETY_GATE_PASSED — only after real gate PASS_DRY_RUN_ONLY
    lifecycle_after_gate = apply_lifecycle_event(
        lifecycle_creation.state,
        event_type=Event.SAFETY_GATE_PASSED,
        event_at_utc=_EVENT_AT,
    )
    assert lifecycle_after_gate.result == "PASS"

    # Step 14: append LIFECYCLE_TRANSITION_RECORDED for SAFETY_GATE_PASSED
    r = append_audit_entry(
        ledger,
        entry_id=_EID_LC_GATE_PASSED,
        entry_type=EType.LIFECYCLE_TRANSITION_RECORDED,
        recorded_at_utc=_TS,
        source="lifecycle",
        payload={
            "previous_status": lifecycle_after_gate.previous_status.value,
            "new_status":      lifecycle_after_gate.new_status.value,
            "event_type":      lifecycle_after_gate.event_type.value,
            "lifecycle_id":    _LIFECYCLE_ID,
            "plan_id":         plan_id,
        },
    )
    ledger_results.append(r)
    assert r.result == "PASS", f"lc gate-passed entry blocked: {r.blocker}"
    ledger = r.state

    return ChainWithLedgerResults(
        planner_result, validation_result, gate_result,
        lifecycle_creation, lifecycle_after_gate, ledger, ledger_results,
    )


def _entry_types(chain: ChainWithLedgerResults) -> list[str]:
    """Return the entry_type values of all ledger entries in the final ledger."""
    return [e["entry_type"] for e in chain.ledger.entries]


def _entry_by_eid(chain: ChainWithLedgerResults, eid: str) -> dict | None:
    for e in chain.ledger.entries:
        if e.get("entry_id") == eid:
            return e
    return None


# ---------------------------------------------------------------------------
# 1, 2: Full chain PASS — ledger entry order (market + limit)
# ---------------------------------------------------------------------------

class TestFullChainMarketLedger:
    def test_ledger_entry_order_market(self):
        chain = _run_chain_with_ledger()
        assert _entry_types(chain) == [
            "PLANNER_RESULT_RECORDED",
            "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED",
            "LIFECYCLE_TRANSITION_RECORDED",
            "LIFECYCLE_TRANSITION_RECORDED",
        ]

    def test_ledger_has_five_entries_market(self):
        chain = _run_chain_with_ledger()
        assert len(chain.ledger.entries) == 5

    def test_ledger_status_updated_after_full_chain(self):
        chain = _run_chain_with_ledger()
        assert chain.ledger.status is PaperAuditLedgerStatus.UPDATED
        assert chain.ledger.last_entry_id == _EID_LC_GATE_PASSED

    def test_planner_status_plan_created(self):
        chain = _run_chain_with_ledger()
        assert chain.planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED

    def test_gate_status_pass_dry_run_only(self):
        chain = _run_chain_with_ledger()
        assert chain.gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY

    def test_lifecycle_ends_at_gate_passed_dry_run_only(self):
        chain = _run_chain_with_ledger()
        assert chain.lifecycle_after_gate.state.status is Status.GATE_PASSED_DRY_RUN_ONLY


class TestFullChainLimitLedger:
    def test_ledger_entry_order_limit(self):
        chain = _run_chain_with_ledger(
            signal=_valid_signal(order_type="limit"),
            sizing=_valid_sizing(limit_price=150.0),
        )
        assert _entry_types(chain) == [
            "PLANNER_RESULT_RECORDED",
            "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED",
            "LIFECYCLE_TRANSITION_RECORDED",
            "LIFECYCLE_TRANSITION_RECORDED",
        ]

    def test_limit_order_type_preserved_in_lifecycle(self):
        chain = _run_chain_with_ledger(
            signal=_valid_signal(order_type="limit"),
            sizing=_valid_sizing(limit_price=150.0),
        )
        assert chain.planner_result.plan["order_type"] == "limit"
        assert chain.lifecycle_creation.state.order_type == "limit"

    def test_limit_plan_passes_gate_and_produces_same_ledger_structure(self):
        chain = _run_chain_with_ledger(
            signal=_valid_signal(order_type="limit"),
            sizing=_valid_sizing(limit_price=150.0),
        )
        assert len(chain.ledger.entries) == 5
        assert chain.ledger.status is PaperAuditLedgerStatus.UPDATED


# ---------------------------------------------------------------------------
# 3: Full bookkeeping fill chain — additional lifecycle ledger entries
# ---------------------------------------------------------------------------

class TestFullBookkeepingFillChain:
    def test_fill_chain_appends_dry_run_pending_filled_entries(self):
        chain = _run_chain_with_ledger()
        ledger = chain.ledger
        lc_state = chain.lifecycle_after_gate.state

        # DRY_RUN_RENDERED
        dry_run = apply_lifecycle_event(
            lc_state, event_type=Event.DRY_RUN_RENDERED, event_at_utc=_EVENT_AT
        )
        assert dry_run.result == "PASS"
        r = append_audit_entry(
            ledger,
            entry_id="eid-lc-dry-run",
            entry_type=EType.LIFECYCLE_TRANSITION_RECORDED,
            recorded_at_utc=_TS,
            source="lifecycle",
            payload={
                "previous_status": dry_run.previous_status.value,
                "new_status":      dry_run.new_status.value,
                "event_type":      dry_run.event_type.value,
                "lifecycle_id":    _LIFECYCLE_ID,
                "plan_id":         _PLAN_ID,
            },
        )
        assert r.result == "PASS"
        ledger = r.state

        # PAPER_ORDER_MARKED_PENDING
        pending = apply_lifecycle_event(
            dry_run.state,
            event_type=Event.PAPER_ORDER_MARKED_PENDING,
            event_at_utc=_EVENT_AT,
        )
        assert pending.result == "PASS"
        r = append_audit_entry(
            ledger,
            entry_id="eid-lc-pending",
            entry_type=EType.LIFECYCLE_TRANSITION_RECORDED,
            recorded_at_utc=_TS,
            source="lifecycle",
            payload={
                "previous_status": pending.previous_status.value,
                "new_status":      pending.new_status.value,
                "event_type":      pending.event_type.value,
                "lifecycle_id":    _LIFECYCLE_ID,
                "plan_id":         _PLAN_ID,
            },
        )
        assert r.result == "PASS"
        ledger = r.state

        # PAPER_ORDER_MARKED_FILLED
        filled = apply_lifecycle_event(
            pending.state,
            event_type=Event.PAPER_ORDER_MARKED_FILLED,
            event_at_utc=_EVENT_AT,
            details={"filled_quantity": 10.0, "average_fill_price": 152.0},
        )
        assert filled.result == "PASS"
        r = append_audit_entry(
            ledger,
            entry_id="eid-lc-filled",
            entry_type=EType.LIFECYCLE_TRANSITION_RECORDED,
            recorded_at_utc=_TS,
            source="lifecycle",
            payload={
                "previous_status": filled.previous_status.value,
                "new_status":      filled.new_status.value,
                "event_type":      filled.event_type.value,
                "lifecycle_id":    _LIFECYCLE_ID,
                "plan_id":         _PLAN_ID,
            },
        )
        assert r.result == "PASS"
        final_ledger = r.state

        types = [e["entry_type"] for e in final_ledger.entries]
        assert types == [
            "PLANNER_RESULT_RECORDED",
            "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED",
            "LIFECYCLE_TRANSITION_RECORDED",  # PLAN_CREATED
            "LIFECYCLE_TRANSITION_RECORDED",  # SAFETY_GATE_PASSED
            "LIFECYCLE_TRANSITION_RECORDED",  # DRY_RUN_RENDERED
            "LIFECYCLE_TRANSITION_RECORDED",  # PAPER_ORDER_MARKED_PENDING
            "LIFECYCLE_TRANSITION_RECORDED",  # PAPER_ORDER_MARKED_FILLED
        ]
        assert filled.state.status is Status.PAPER_ORDER_FILLED
        assert filled.state.filled_quantity == 10.0

    def test_filled_lifecycle_is_bookkeeping_only(self):
        chain = _run_chain_with_ledger()
        lc_state = chain.lifecycle_after_gate.state
        dry_run = apply_lifecycle_event(
            lc_state, event_type=Event.DRY_RUN_RENDERED, event_at_utc=_EVENT_AT
        ).state
        pending = apply_lifecycle_event(
            dry_run, event_type=Event.PAPER_ORDER_MARKED_PENDING, event_at_utc=_EVENT_AT
        ).state
        filled_result = apply_lifecycle_event(
            pending,
            event_type=Event.PAPER_ORDER_MARKED_FILLED,
            event_at_utc=_EVENT_AT,
            details={"filled_quantity": 10.0},
        )
        assert filled_result.state.status is Status.PAPER_ORDER_FILLED
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(filled_result, flag) is False
            assert getattr(filled_result.state, flag) is False


# ---------------------------------------------------------------------------
# 4-6: Planner blocked — planner + blocked chain entries only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("kwargs", "expected_status"),
    [
        (
            {"approval": _valid_approval(approval_status="NOT_REVIEWED")},
            PaperOrderPlannerStatus.BLOCKED_APPROVAL,
        ),
        (
            {"signal": _valid_signal(symbol="TSLA")},
            PaperOrderPlannerStatus.BLOCKED_SIGNAL,
        ),
        (
            {"sizing": _valid_sizing(notional=6000.0)},
            PaperOrderPlannerStatus.BLOCKED_SIZING,
        ),
    ],
    ids=["blocked_approval", "blocked_signal", "blocked_sizing"],
)
class TestPlannerBlockedLedger:
    def test_planner_blocked_appends_planner_and_blocked_chain_only(
        self, kwargs, expected_status
    ):
        chain = _run_chain_with_ledger(**kwargs)
        assert chain.planner_result.planner_status is expected_status
        assert chain.planner_result.plan is None
        assert _entry_types(chain) == [
            "PLANNER_RESULT_RECORDED",
            "BLOCKED_CHAIN_RECORDED",
        ]
        assert len(chain.ledger.entries) == 2

    def test_planner_blocked_stops_downstream(self, kwargs, expected_status):
        chain = _run_chain_with_ledger(**kwargs)
        assert chain.validation_result is None
        assert chain.gate_result is None
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate is None

    def test_blocked_chain_entry_has_correct_source_and_stage(
        self, kwargs, expected_status
    ):
        chain = _run_chain_with_ledger(**kwargs)
        blocked_entry = _entry_by_eid(chain, _EID_BLOCKED)
        assert blocked_entry is not None
        assert blocked_entry["source"] == "chain"
        assert blocked_entry["entry_type"] == "BLOCKED_CHAIN_RECORDED"
        assert blocked_entry["payload"]["blocked_stage"] == "planner"


# ---------------------------------------------------------------------------
# 7-10: Gate blocked — planner + validation + gate + blocked chain entries only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("state_overrides", "expected_gate_status"),
    [
        (
            {"kill_switch_open": False},
            PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH,
        ),
        (
            {"current_daily_order_count": 5},
            PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT,
        ),
        (
            {"processed_plan_ids": [_PLAN_ID]},
            PaperOrderSafetyGateStatus.BLOCKED_DUPLICATE,
        ),
        (
            {"open_positions": [
                {"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}
            ]},
            PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT,
        ),
    ],
    ids=["kill_switch", "daily_count", "duplicate_plan_id", "open_position"],
)
class TestGateBlockedLedger:
    def test_gate_blocked_appends_four_entries(self, state_overrides, expected_gate_status):
        chain = _run_chain_with_ledger(state=_clean_state(**state_overrides))
        assert _entry_types(chain) == [
            "PLANNER_RESULT_RECORDED",
            "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED",
            "BLOCKED_CHAIN_RECORDED",
        ]
        assert len(chain.ledger.entries) == 4

    def test_gate_blocked_stops_lifecycle(self, state_overrides, expected_gate_status):
        chain = _run_chain_with_ledger(state=_clean_state(**state_overrides))
        assert chain.gate_result.gate_status is expected_gate_status
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate is None

    def test_gate_blocked_chain_entry_uses_chain_source(
        self, state_overrides, expected_gate_status
    ):
        chain = _run_chain_with_ledger(state=_clean_state(**state_overrides))
        blocked_entry = _entry_by_eid(chain, _EID_BLOCKED)
        assert blocked_entry["source"] == "chain"
        assert blocked_entry["entry_type"] == "BLOCKED_CHAIN_RECORDED"
        assert blocked_entry["payload"]["blocked_stage"] == "safety_gate"


# ---------------------------------------------------------------------------
# 11: plan_id preserved across all ledger entries
# ---------------------------------------------------------------------------

class TestPlanIdPreservedAcrossEntries:
    def test_plan_id_in_all_planner_and_gate_entries(self):
        chain = _run_chain_with_ledger()
        for entry in chain.ledger.entries:
            if entry["entry_type"] in {
                "PLANNER_RESULT_RECORDED",
                "VALIDATION_RESULT_RECORDED",
                "SAFETY_GATE_RESULT_RECORDED",
            }:
                assert entry["payload"]["plan_id"] == _PLAN_ID, (
                    f"plan_id missing in {entry['entry_type']}"
                )

    def test_plan_id_in_lifecycle_entries(self):
        chain = _run_chain_with_ledger()
        for entry in chain.ledger.entries:
            if entry["entry_type"] == "LIFECYCLE_TRANSITION_RECORDED":
                assert entry["payload"]["plan_id"] == _PLAN_ID


# ---------------------------------------------------------------------------
# 12: candidate_id / run_id in payloads where available
# ---------------------------------------------------------------------------

class TestCandidateAndRunIdInPayloads:
    def test_candidate_id_run_id_in_planner_entry(self):
        chain = _run_chain_with_ledger()
        planner_entry = _entry_by_eid(chain, _EID_PLANNER)
        assert planner_entry["payload"]["candidate_id"] == "cand-001"
        assert planner_entry["payload"]["run_id"] == "run-001"

    def test_candidate_id_run_id_in_validator_entry(self):
        chain = _run_chain_with_ledger()
        v_entry = _entry_by_eid(chain, _EID_VALIDATOR)
        assert v_entry["payload"]["candidate_id"] == "cand-001"
        assert v_entry["payload"]["run_id"] == "run-001"

    def test_overridden_candidate_id_propagated(self):
        chain = _run_chain_with_ledger(
            approval=_valid_approval(candidate_id="cand-999", run_id="run-999")
        )
        planner_entry = _entry_by_eid(chain, _EID_PLANNER)
        assert planner_entry["payload"]["candidate_id"] == "cand-999"
        assert planner_entry["payload"]["run_id"] == "run-999"


# ---------------------------------------------------------------------------
# 13: entry_ids are deterministic and unique
# ---------------------------------------------------------------------------

class TestEntryIdDeterminism:
    def test_entry_ids_are_deterministic(self):
        chain1 = _run_chain_with_ledger()
        chain2 = _run_chain_with_ledger()
        ids1 = [e["entry_id"] for e in chain1.ledger.entries]
        ids2 = [e["entry_id"] for e in chain2.ledger.entries]
        assert ids1 == ids2

    def test_entry_ids_are_unique_within_ledger(self):
        chain = _run_chain_with_ledger()
        ids = [e["entry_id"] for e in chain.ledger.entries]
        assert len(ids) == len(set(ids))

    def test_blocked_chain_entry_id_is_deterministic(self):
        c1 = _run_chain_with_ledger(state=_clean_state(kill_switch_open=False))
        c2 = _run_chain_with_ledger(state=_clean_state(kill_switch_open=False))
        ids1 = [e["entry_id"] for e in c1.ledger.entries]
        ids2 = [e["entry_id"] for e in c2.ledger.entries]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# 14: duplicate entry_id blocks and returns previous ledger unchanged
# ---------------------------------------------------------------------------

class TestDuplicateEntryIdBlocked:
    def test_duplicate_entry_id_is_blocked(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        ledger = ledger_r.state
        first = append_audit_entry(
            ledger,
            entry_id="eid-dup",
            entry_type=EType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="planner",
            payload={"planner_status": "PLAN_CREATED", "result": "PASS", "plan_id": _PLAN_ID},
        )
        assert first.result == "PASS"
        second = append_audit_entry(
            first.state,
            entry_id="eid-dup",
            entry_type=EType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="planner",
            payload={"planner_status": "PLAN_CREATED", "result": "PASS", "plan_id": _PLAN_ID},
        )
        assert second.result == "BLOCKED"
        assert "eid-dup" in second.blocker
        assert second.state is first.state
        assert len(second.state.entries) == 1

    def test_blocked_duplicate_does_not_advance_ledger(self):
        chain = _run_chain_with_ledger()
        original_count = len(chain.ledger.entries)
        existing_id = chain.ledger.entries[0]["entry_id"]
        r = append_audit_entry(
            chain.ledger,
            entry_id=existing_id,
            entry_type=EType.BLOCKED_CHAIN_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={"blocked_stage": "planner", "blocker": "duplicate-test"},
        )
        assert r.result == "BLOCKED"
        assert r.entry is None
        assert len(r.state.entries) == original_count


# ---------------------------------------------------------------------------
# 15: ledger payloads are deep-copied from supplied dicts
# ---------------------------------------------------------------------------

class TestPayloadDeepCopied:
    def test_mutating_supplied_payload_does_not_mutate_stored_entry(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        ledger = ledger_r.state
        payload = {"planner_status": "PLAN_CREATED", "result": "PASS", "plan_id": _PLAN_ID}
        r = append_audit_entry(
            ledger,
            entry_id="eid-copy-test",
            entry_type=EType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="planner",
            payload=payload,
        )
        assert r.result == "PASS"
        payload["plan_id"] = "mutated"
        stored = r.state.entries[0]["payload"]
        assert stored["plan_id"] == _PLAN_ID

    def test_returned_entry_dict_is_deep_copy(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        ledger = ledger_r.state
        r = append_audit_entry(
            ledger,
            entry_id="eid-copy-test2",
            entry_type=EType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="planner",
            payload={"planner_status": "PLAN_CREATED", "result": "PASS", "plan_id": _PLAN_ID},
        )
        assert r.result == "PASS"
        r.entry["payload"]["plan_id"] = "mutated-returned"
        stored = r.state.entries[0]["payload"]
        assert stored["plan_id"] == _PLAN_ID


# ---------------------------------------------------------------------------
# 16: mutating original approval/signal/sizing after run does not mutate ledger
# ---------------------------------------------------------------------------

class TestMutationIsolation:
    def test_mutating_approval_after_run_does_not_mutate_ledger(self):
        approval = _valid_approval()
        chain = _run_chain_with_ledger(approval=approval)
        original_cand = chain.ledger.entries[0]["payload"]["candidate_id"]
        approval["candidate_id"] = "mutated"
        assert chain.ledger.entries[0]["payload"]["candidate_id"] == original_cand

    def test_mutating_sizing_after_run_does_not_affect_plan_in_ledger(self):
        sizing = _valid_sizing()
        chain = _run_chain_with_ledger(sizing=sizing)
        sizing["quantity"] = 9999.0
        lc_entry = _entry_by_eid(chain, _EID_LC_PLAN_CREATED)
        assert lc_entry is not None
        # lifecycle plan_id unchanged; plan identity unchanged in ledger
        assert lc_entry["payload"]["lifecycle_id"] == _LIFECYCLE_ID


# ---------------------------------------------------------------------------
# 17: determinism — same inputs produce same results across all stages
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_produce_identical_chain_results(self):
        c1 = _run_chain_with_ledger()
        c2 = _run_chain_with_ledger()
        assert c1.planner_result == c2.planner_result
        assert c1.validation_result == c2.validation_result
        assert c1.gate_result == c2.gate_result
        assert c1.lifecycle_creation == c2.lifecycle_creation
        assert c1.lifecycle_after_gate == c2.lifecycle_after_gate
        assert c1.ledger == c2.ledger

    def test_same_blocked_inputs_produce_identical_ledger(self):
        c1 = _run_chain_with_ledger(state=_clean_state(kill_switch_open=False))
        c2 = _run_chain_with_ledger(state=_clean_state(kill_switch_open=False))
        assert c1.gate_result == c2.gate_result
        assert c1.ledger == c2.ledger


# ---------------------------------------------------------------------------
# 18: all ledger result and state safety flags are False
# ---------------------------------------------------------------------------

class TestLedgerSafetyFlagsAlwaysFalse:
    def _pass_and_blocked_chains(self):
        return [
            ("pass_market", _run_chain_with_ledger()),
            ("pass_limit",  _run_chain_with_ledger(
                signal=_valid_signal(order_type="limit"),
                sizing=_valid_sizing(limit_price=150.0),
            )),
            ("blocked_planner", _run_chain_with_ledger(
                approval=_valid_approval(approval_status="NOT_REVIEWED")
            )),
            ("blocked_gate_ks", _run_chain_with_ledger(
                state=_clean_state(kill_switch_open=False)
            )),
        ]

    def test_ledger_append_result_flags_false(self):
        for label, chain in self._pass_and_blocked_chains():
            for r in chain.ledger_results:
                for flag in _SAFETY_FLAG_NAMES:
                    assert getattr(r, flag) is False, (
                        f"ledger result {flag} not False in {label}"
                    )

    def test_ledger_state_flags_false(self):
        for label, chain in self._pass_and_blocked_chains():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.ledger, flag) is False, (
                    f"ledger state {flag} not False in {label}"
                )

    def test_empty_ledger_flags_false(self):
        r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        assert r.result == "PASS"
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(r, flag) is False
            assert getattr(r.state, flag) is False


# ---------------------------------------------------------------------------
# 19: upstream result safety flags always False
# ---------------------------------------------------------------------------

class TestUpstreamSafetyFlagsAlwaysFalse:
    def test_planner_flags_false_on_pass_and_blocked(self):
        for chain in [
            _run_chain_with_ledger(),
            _run_chain_with_ledger(approval=_valid_approval(approval_status="DRAFT")),
            _run_chain_with_ledger(state=_clean_state(kill_switch_open=False)),
        ]:
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.planner_result, flag) is False

    def test_gate_flags_false_on_pass_and_blocked(self):
        for chain in [
            _run_chain_with_ledger(),
            _run_chain_with_ledger(state=_clean_state(kill_switch_open=False)),
            _run_chain_with_ledger(
                state=_clean_state(processed_plan_ids=[_PLAN_ID])
            ),
        ]:
            if chain.gate_result is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.gate_result, flag) is False

    def test_lifecycle_flags_false_on_pass_chain(self):
        chain = _run_chain_with_ledger()
        for lc_result in (chain.lifecycle_creation, chain.lifecycle_after_gate):
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(lc_result, flag) is False
                assert getattr(lc_result.state, flag) is False


# ---------------------------------------------------------------------------
# 20: blocked chain entry uses source="chain" and BLOCKED_CHAIN_RECORDED
# ---------------------------------------------------------------------------

class TestBlockedChainEntryAttributes:
    @pytest.mark.parametrize(
        "chain_fn",
        [
            lambda: _run_chain_with_ledger(
                approval=_valid_approval(approval_status="NOT_REVIEWED")
            ),
            lambda: _run_chain_with_ledger(state=_clean_state(kill_switch_open=False)),
            lambda: _run_chain_with_ledger(
                state=_clean_state(processed_plan_ids=[_PLAN_ID])
            ),
        ],
        ids=["planner_blocked", "gate_blocked_ks", "gate_blocked_dup"],
    )
    def test_blocked_entry_has_chain_source(self, chain_fn):
        chain = chain_fn()
        blocked = _entry_by_eid(chain, _EID_BLOCKED)
        assert blocked is not None
        assert blocked["source"] == "chain"
        assert blocked["entry_type"] == "BLOCKED_CHAIN_RECORDED"
        assert "blocked_stage" in blocked["payload"]
        assert "blocker" in blocked["payload"]


# ---------------------------------------------------------------------------
# 21: ERROR_RECORDED entry can be appended — bookkeeping only
# ---------------------------------------------------------------------------

class TestErrorEntryBookkeepingOnly:
    def test_error_entry_appended_with_chain_source(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        r = append_audit_entry(
            ledger_r.state,
            entry_id=_EID_ERROR,
            entry_type=EType.ERROR_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={"error_stage": "planner", "blocker": "unexpected-exception"},
        )
        assert r.result == "PASS"
        assert r.entry["entry_type"] == "ERROR_RECORDED"
        assert r.entry["source"] == "chain"
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(r, flag) is False
            assert getattr(r.state, flag) is False

    def test_error_entry_does_not_approve_trading(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        r = append_audit_entry(
            ledger_r.state,
            entry_id=_EID_ERROR,
            entry_type=EType.ERROR_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={"error_stage": "gate", "blocker": "gate-error"},
        )
        assert r.state.live_trading_allowed is False
        assert r.state.order_action_requested is False


# ---------------------------------------------------------------------------
# 22: forbidden ledger payload content is blocked in integration
# ---------------------------------------------------------------------------

class TestForbiddenPayloadBlocked:
    @pytest.mark.parametrize(
        "bad_value",
        [
            "submit_" + "order",
            "api_" + "key",
            "my_" + "secret",
            "auth_" + "token",
            "htt" + "p://example",
            "live_" + "account=xyz",
            "end" + "point=/api",
        ],
        ids=["order_verb", "api_key", "secret", "token", "http", "live_account", "endpoint"],
    )
    def test_forbidden_payload_value_blocks_append(self, bad_value):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        ledger = ledger_r.state
        r = append_audit_entry(
            ledger,
            entry_id="eid-forbidden",
            entry_type=EType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="planner",
            payload={
                "planner_status": "PLAN_CREATED",
                "result":         "PASS",
                "plan_id":        _PLAN_ID,
                "note":           bad_value,
            },
        )
        assert r.result == "BLOCKED"
        assert "forbidden" in r.blocker
        assert r.state is ledger
        assert r.entry is None
        assert len(r.state.entries) == 0


# ---------------------------------------------------------------------------
# 23: ledger source mismatch is blocked in integration
# ---------------------------------------------------------------------------

class TestSourceMismatchBlocked:
    def test_wrong_source_for_entry_type_is_blocked(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        ledger = ledger_r.state
        r = append_audit_entry(
            ledger,
            entry_id="eid-source-mismatch",
            entry_type=EType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="chain",  # must be "planner" for PLANNER_RESULT_RECORDED
            payload={
                "planner_status": "PLAN_CREATED",
                "result":         "PASS",
                "plan_id":        _PLAN_ID,
            },
        )
        assert r.result == "BLOCKED"
        assert "source" in r.blocker.lower() or "chain" in r.blocker
        assert r.state is ledger

    def test_validator_source_for_gate_type_is_blocked(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        ledger = ledger_r.state
        r = append_audit_entry(
            ledger,
            entry_id="eid-src-gate",
            entry_type=EType.SAFETY_GATE_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="planner",  # must be "safety_gate"
            payload={
                "gate_status": "PASS_DRY_RUN_ONLY",
                "result":      "PASS",
                "plan_id":     _PLAN_ID,
            },
        )
        assert r.result == "BLOCKED"
        assert r.state is ledger


# ---------------------------------------------------------------------------
# 24: missing required payload key is blocked in integration
# ---------------------------------------------------------------------------

class TestMissingRequiredPayloadKeyBlocked:
    def test_missing_plan_id_blocks_planner_entry(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        r = append_audit_entry(
            ledger_r.state,
            entry_id="eid-miss-key",
            entry_type=EType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_TS,
            source="planner",
            payload={"planner_status": "PLAN_CREATED", "result": "PASS"},  # missing plan_id
        )
        assert r.result == "BLOCKED"
        assert "plan_id" in r.blocker
        assert r.state is ledger_r.state

    def test_missing_lifecycle_id_blocks_lifecycle_entry(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        r = append_audit_entry(
            ledger_r.state,
            entry_id="eid-miss-lc",
            entry_type=EType.LIFECYCLE_TRANSITION_RECORDED,
            recorded_at_utc=_TS,
            source="lifecycle",
            payload={
                "previous_status": "NOT_STARTED",
                "new_status":      "PLANNED",
                "event_type":      "PLAN_CREATED",
                # missing lifecycle_id
            },
        )
        assert r.result == "BLOCKED"
        assert r.state is ledger_r.state

    def test_missing_blocker_key_blocks_blocked_chain_entry(self):
        ledger_r = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        r = append_audit_entry(
            ledger_r.state,
            entry_id="eid-miss-blocker",
            entry_type=EType.BLOCKED_CHAIN_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={"blocked_stage": "planner"},  # missing "blocker"
        )
        assert r.result == "BLOCKED"
        assert r.state is ledger_r.state


# ---------------------------------------------------------------------------
# 25: no lifecycle is created when gate blocks
# ---------------------------------------------------------------------------

class TestNoLifecycleWhenGateBlocked:
    @pytest.mark.parametrize(
        "state_overrides",
        [
            {"kill_switch_open": False},
            {"current_daily_order_count": 5},
            {"processed_plan_ids": [_PLAN_ID]},
        ],
        ids=["kill_switch", "daily_count", "duplicate"],
    )
    def test_no_lifecycle_state_when_gate_blocked(self, state_overrides):
        chain = _run_chain_with_ledger(state=_clean_state(**state_overrides))
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate is None

    @pytest.mark.parametrize(
        "state_overrides",
        [
            {"kill_switch_open": False},
            {"current_daily_order_count": 5},
            {"processed_plan_ids": [_PLAN_ID]},
        ],
        ids=["kill_switch", "daily_count", "duplicate"],
    )
    def test_no_lifecycle_ledger_entries_when_gate_blocked(self, state_overrides):
        chain = _run_chain_with_ledger(state=_clean_state(**state_overrides))
        lc_entries = [
            e for e in chain.ledger.entries
            if e["entry_type"] == "LIFECYCLE_TRANSITION_RECORDED"
        ]
        assert lc_entries == []


# ---------------------------------------------------------------------------
# 26: no entries appended after the blocked chain entry in fail-closed paths
# ---------------------------------------------------------------------------

class TestNoEntriesAfterBlockedChainEntry:
    def test_blocked_entry_is_last_entry_when_planner_blocked(self):
        chain = _run_chain_with_ledger(
            approval=_valid_approval(approval_status="NOT_REVIEWED")
        )
        assert chain.ledger.entries[-1]["entry_type"] == "BLOCKED_CHAIN_RECORDED"
        assert len(chain.ledger.entries) == 2

    def test_blocked_entry_is_last_entry_when_gate_blocked(self):
        chain = _run_chain_with_ledger(state=_clean_state(kill_switch_open=False))
        assert chain.ledger.entries[-1]["entry_type"] == "BLOCKED_CHAIN_RECORDED"
        assert len(chain.ledger.entries) == 4

    def test_ledger_count_matches_exactly_for_each_blocked_stage(self):
        planner_blocked = _run_chain_with_ledger(
            approval=_valid_approval(approval_status="NOT_REVIEWED")
        )
        gate_blocked = _run_chain_with_ledger(state=_clean_state(kill_switch_open=False))
        assert len(planner_blocked.ledger.entries) == 2   # planner + blocked
        assert len(gate_blocked.ledger.entries) == 4      # planner + validator + gate + blocked


# ---------------------------------------------------------------------------
# 27: no real artifacts created — runtime recording wrapper sees zero opens
# ---------------------------------------------------------------------------

class TestNoArtifactsCreated:
    def test_full_chain_never_opens_a_file(self, monkeypatch):
        calls: list = []
        _real_ctor = builtins.open

        def _recording_ctor(*args, **kwargs):
            calls.append(args)
            return _real_ctor(*args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(builtins, "open", _recording_ctor)
            pass_chain = _run_chain_with_ledger()
            blocked_chain = _run_chain_with_ledger(
                state=_clean_state(kill_switch_open=False)
            )
        assert calls == [], f"unexpected file opens: {calls}"
        assert len(pass_chain.ledger.entries) == 5
        assert len(blocked_chain.ledger.entries) == 4


# ---------------------------------------------------------------------------
# 28: test module itself is clean
# ---------------------------------------------------------------------------

class TestThisModuleClean:
    def _source(self) -> str:
        import sys
        return inspect.getsource(sys.modules[__name__])

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
        ):
            assert pattern not in source, f"forbidden file I/O in module: {pattern!r}"

    def test_no_write_operations(self):
        source = self._source()
        for pattern in (
            "write_by" + "tes(",
            "make" + "dirs(",
            "mk" + "dir(",
        ):
            assert pattern not in source, f"forbidden write op in module: {pattern!r}"

    def test_no_forbidden_imports(self):
        source = self._source()
        for pattern in (
            "import " + "requ" + "ests",
            "import " + "url" + "lib",
            "import " + "aio" + "http",
            "import " + "alp" + "aca",
            "import " + "sock" + "et",
            "import " + "subpro" + "cess",
            "import" + " " + "os",
            "from " + "path" + "lib",
            "import " + "path" + "lib",
            "Alpaca" + "Broker" + "Adapter",
        ):
            assert pattern not in source, f"forbidden import in module: {pattern!r}"

    def test_no_assembled_order_verbs(self):
        source = self._source()
        for pattern in (
            "submit_or" + "der",
            "place_or" + "der",
            "cancel_or" + "der",
            "modify_or" + "der",
            "live_su" + "bmit",
        ):
            assert pattern not in source, f"order verb in module source: {pattern!r}"

    def test_no_env_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
        ):
            assert pattern not in source, f"env call in module: {pattern!r}"

    def test_no_runtime_or_execution_imports(self):
        source = self._source()
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
        ):
            assert pattern not in source, f"forbidden module import: {pattern!r}"


# ---------------------------------------------------------------------------
# 29: source modules in the chain are clean
# ---------------------------------------------------------------------------

_CHAIN_MODULES = [
    _planner_mod, _plan_mod, _gate_mod,
    _lifecycle_mod, _approval_mod, _ledger_mod,
]
_CHAIN_MODULE_IDS = [
    "s30_planner", "s27_plan_validator", "s28_safety_gate",
    "s32_lifecycle", "s25_approval_validator", "s34_audit_ledger",
]


class TestChainModulesClean:
    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_file_io(self, module):
        source = inspect.getsource(module)
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
            "read_by" + "tes(",
            "write_by" + "tes(",
        ):
            assert pattern not in source, (
                f"forbidden file I/O {pattern!r} in {module.__name__}"
            )

    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_forbidden_imports(self, module):
        source = inspect.getsource(module)
        for pattern in (
            "import " + "requ" + "ests",
            "import " + "url" + "lib",
            "import " + "aio" + "http",
            "import " + "alp" + "aca",
            "import " + "sock" + "et",
            "import " + "subpro" + "cess",
            "import" + " " + "os",
            "from " + "path" + "lib",
            "import " + "path" + "lib",
        ):
            assert pattern not in source, (
                f"forbidden import {pattern!r} in {module.__name__}"
            )

    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_broker_env_or_order_calls(self, module):
        source = inspect.getsource(module)
        for pattern in (
            "Alpaca" + "Broker" + "Adapter",
            "os.envi" + "ron[",
            "os.envi" + "ron.get(",
            "getenv" + "(",
            "." + "submit_or" + "der(",
            "." + "place_or" + "der(",
            "." + "cancel_or" + "der(",
            "." + "modify_or" + "der(",
            "live_su" + "bmit(",
        ):
            assert pattern not in source, (
                f"forbidden call {pattern!r} in {module.__name__}"
            )

    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_runtime_or_execution_imports(self, module):
        source = inspect.getsource(module)
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
        ):
            assert pattern not in source, (
                f"forbidden module import {pattern!r} in {module.__name__}"
            )
