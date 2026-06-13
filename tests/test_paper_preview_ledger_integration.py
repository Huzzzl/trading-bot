"""
tests/test_paper_preview_ledger_integration.py
-----------------------------------------------
S38: Integration tests for the full pure offline preview + ledger chain

    S30 create_paper_order_plan()
    -> S27 validate_paper_order_plan()
    -> S28 evaluate_paper_order_safety_gate()
    -> S32 create_lifecycle_from_plan()
    -> S32 apply_lifecycle_event(SAFETY_GATE_PASSED)
    -> S36 render_paper_dry_run_preview()
    -> S34 create_empty_audit_ledger() / append_audit_entry()

Every scenario uses the REAL planner, S27 validator, S28 gate, S32
lifecycle, S36 preview renderer, and S34 ledger functions. No mocked
components are used for core integration tests.

CURRENT BOUNDARY (expected, not a bug): the S34 ledger defines exactly six
entry types (PLANNER_RESULT_RECORDED, VALIDATION_RESULT_RECORDED,
SAFETY_GATE_RESULT_RECORDED, LIFECYCLE_TRANSITION_RECORDED,
BLOCKED_CHAIN_RECORDED, ERROR_RECORDED) and five allowed sources (planner,
validator, safety_gate, lifecycle, chain). There is NO dedicated preview
entry type and NO "preview" source. The rendered preview therefore remains
a separate, display-only in-memory dict alongside the ledger and is NOT
appended as a ledger entry in this PR. Forcing the preview into
LIFECYCLE_TRANSITION_RECORDED (or any other type) would misclassify it;
the tests below prove the ledger structurally refuses that. A future S39
schema extension should add a dedicated PREVIEW_RESULT_RECORDED entry type
(with its own required payload keys and source) before preview results are
recorded in the ledger.

All fixtures are plain in-memory dicts. No real approval artifact, order
plan, lifecycle record, preview artifact, ledger artifact, config, or any
other artifact is created. No files are read or written. No broker,
network, credential, environment-variable, or order access is made.

The preview is display-only: not an order, not a broker payload, never
order approval, and rendering never advances the lifecycle. Ledger entries
are audit bookkeeping only, never order actions. S28 PASS_DRY_RUN_ONLY
remains only offline clearance for a future dry-run/no-submit rendering
step. Paper trading remains not approved; live trading remains blocked.
"""

from __future__ import annotations

import builtins
import copy
import inspect
from typing import NamedTuple

import pytest

import src.research.paper_approval_validator as _approval_mod
import src.research.paper_audit_ledger as _ledger_mod
import src.research.paper_dry_run_preview as _preview_mod
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
from src.research.paper_dry_run_preview import (
    PaperDryRunPreviewStatus,
    render_paper_dry_run_preview,
)
from src.research.paper_order_lifecycle import (
    PaperOrderLifecycleEventType,
    PaperOrderLifecycleState,
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
    PaperOrderSafetyGateResult,
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
_PREVIEW_ID   = "preview-001"
_CREATED_AT   = "2025-01-15T09:05:00Z"
_EVENT_AT     = "2025-01-15T09:10:00Z"
_RENDERED_AT  = "2025-01-15T09:20:00Z"
_TS           = "2025-01-15T09:00:00Z"

_EID_PLANNER         = "eid-planner"
_EID_VALIDATOR       = "eid-validator"
_EID_GATE            = "eid-gate"
_EID_LC_PLAN_CREATED = "eid-lc-plan-created"
_EID_LC_GATE_PASSED  = "eid-lc-gate-passed"
_EID_BLOCKED         = "eid-blocked"

_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

PreviewStatus = PaperDryRunPreviewStatus
LcStatus = PaperOrderLifecycleStatus
LcEvent = PaperOrderLifecycleEventType
EType = PaperAuditLedgerEntryType


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


def _gate_result_to_snapshot(gate_result: PaperOrderSafetyGateResult) -> dict:
    return {
        "result":                       gate_result.result,
        "gate_status":                  gate_result.gate_status.value,
        "plan_id":                      gate_result.plan_id,
        "candidate_id":                 gate_result.candidate_id,
        "run_id":                       gate_result.run_id,
        "dry_run_required":             gate_result.dry_run_required,
        "human_confirmation_required":  gate_result.human_confirmation_required,
        "kill_switch_required":         gate_result.kill_switch_required,
        "broker_calls_made":            gate_result.broker_calls_made,
        "credentials_read":             gate_result.credentials_read,
        "network_calls_made":           gate_result.network_calls_made,
        "order_action_requested":       gate_result.order_action_requested,
        "live_trading_allowed":         gate_result.live_trading_allowed,
    }


def _lifecycle_state_to_snapshot(state: PaperOrderLifecycleState) -> dict:
    return {
        "lifecycle_id":           state.lifecycle_id,
        "plan_id":                state.plan_id,
        "candidate_id":           state.candidate_id,
        "run_id":                 state.run_id,
        "status":                 state.status.value,
        "broker_calls_made":      state.broker_calls_made,
        "credentials_read":       state.credentials_read,
        "network_calls_made":     state.network_calls_made,
        "order_action_requested": state.order_action_requested,
        "live_trading_allowed":   state.live_trading_allowed,
    }


class PreviewLedgerChainResults(NamedTuple):
    planner_result:       object
    validation_result:    object
    gate_result:          object
    lifecycle_creation:   object
    lifecycle_after_gate: object
    preview_result:       object
    ledger:               PaperAuditLedgerState | None
    ledger_results:       list


def _run_chain_to_preview_ledger(
    approval=None,
    signal=None,
    sizing=None,
    state=None,
    *,
    plan_id: str = _PLAN_ID,
    ledger_id: str = _LEDGER_ID,
    **planner_kwargs,
) -> PreviewLedgerChainResults:
    """Run the full real chain, record every stage in the ledger, and render
    the dry-run preview after lifecycle SAFETY_GATE_PASSED.

    1.  create_empty_audit_ledger()
    2.  create_paper_order_plan() -> append PLANNER_RESULT_RECORDED
    3.  planner blocked -> append BLOCKED_CHAIN_RECORDED; stop
    4.  validate_paper_order_plan() -> append VALIDATION_RESULT_RECORDED
    5.  validation blocked -> append BLOCKED_CHAIN_RECORDED; stop
    6.  evaluate_paper_order_safety_gate() -> append SAFETY_GATE_RESULT_RECORDED
    7.  gate blocked -> append BLOCKED_CHAIN_RECORDED; stop (no lifecycle,
        no preview)
    8.  create_lifecycle_from_plan() -> append LIFECYCLE_TRANSITION_RECORDED
        for PLAN_CREATED
    9.  apply SAFETY_GATE_PASSED -> append LIFECYCLE_TRANSITION_RECORDED for
        SAFETY_GATE_PASSED
    10. render_paper_dry_run_preview()

    The preview is NOT appended to the ledger: S34 has no dedicated preview
    entry type or "preview" source, so the rendered preview remains a
    separate display-only in-memory dict (current expected boundary; a
    future S39 schema extension should add PREVIEW_RESULT_RECORDED).
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

    lr = create_empty_audit_ledger(ledger_id=ledger_id)
    assert lr.result == "PASS", f"create_empty_audit_ledger blocked: {lr.blocker}"
    ledger = lr.state
    ledger_results: list = []

    def _append(entry_id, entry_type, source, payload):
        nonlocal ledger
        r = append_audit_entry(
            ledger,
            entry_id=entry_id,
            entry_type=entry_type,
            recorded_at_utc=_TS,
            source=source,
            payload=payload,
        )
        ledger_results.append(r)
        assert r.result == "PASS", f"{entry_id} append blocked: {r.blocker}"
        ledger = r.state

    planner_result = create_paper_order_plan(
        approval, signal_snapshot=signal, sizing_snapshot=sizing, **planner_kwargs
    )
    _append(
        _EID_PLANNER, EType.PLANNER_RESULT_RECORDED, "planner",
        {
            "planner_status": planner_result.planner_status.value,
            "result":         planner_result.result,
            "plan_id":        plan_id,
        },
    )
    if planner_result.plan is None:
        _append(
            _EID_BLOCKED, EType.BLOCKED_CHAIN_RECORDED, "chain",
            {
                "blocked_stage": "planner",
                "blocker": "planner_status=" + planner_result.planner_status.value,
            },
        )
        return PreviewLedgerChainResults(
            planner_result, None, None, None, None, None, ledger, ledger_results
        )

    validation_result = validate_paper_order_plan(planner_result.plan)
    _append(
        _EID_VALIDATOR, EType.VALIDATION_RESULT_RECORDED, "validator",
        {"validation_result": validation_result.result, "plan_id": plan_id},
    )
    if validation_result.result != "PASS":
        _append(
            _EID_BLOCKED, EType.BLOCKED_CHAIN_RECORDED, "chain",
            {
                "blocked_stage": "validator",
                "blocker": "validation_result=" + str(validation_result.result),
            },
        )
        return PreviewLedgerChainResults(
            planner_result, validation_result, None, None, None, None,
            ledger, ledger_results,
        )

    gate_result = evaluate_paper_order_safety_gate(
        approval, planner_result.plan, current_state=state
    )
    _append(
        _EID_GATE, EType.SAFETY_GATE_RESULT_RECORDED, "safety_gate",
        {
            "gate_status": gate_result.gate_status.value,
            "result":      gate_result.result,
            "plan_id":     plan_id,
        },
    )
    if gate_result.gate_status is not PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY:
        _append(
            _EID_BLOCKED, EType.BLOCKED_CHAIN_RECORDED, "chain",
            {
                "blocked_stage": "safety_gate",
                "blocker": "gate_status=" + gate_result.gate_status.value,
            },
        )
        return PreviewLedgerChainResults(
            planner_result, validation_result, gate_result, None, None, None,
            ledger, ledger_results,
        )

    lifecycle_creation = create_lifecycle_from_plan(
        planner_result.plan, lifecycle_id=_LIFECYCLE_ID, created_at_utc=_CREATED_AT
    )
    assert lifecycle_creation.result == "PASS"
    _append(
        _EID_LC_PLAN_CREATED, EType.LIFECYCLE_TRANSITION_RECORDED, "lifecycle",
        {
            "previous_status": lifecycle_creation.previous_status.value,
            "new_status":      lifecycle_creation.new_status.value,
            "event_type":      lifecycle_creation.event_type.value,
            "lifecycle_id":    _LIFECYCLE_ID,
            "plan_id":         plan_id,
        },
    )

    lifecycle_after_gate = apply_lifecycle_event(
        lifecycle_creation.state,
        event_type=LcEvent.SAFETY_GATE_PASSED,
        event_at_utc=_EVENT_AT,
    )
    assert lifecycle_after_gate.result == "PASS"
    _append(
        _EID_LC_GATE_PASSED, EType.LIFECYCLE_TRANSITION_RECORDED, "lifecycle",
        {
            "previous_status": lifecycle_after_gate.previous_status.value,
            "new_status":      lifecycle_after_gate.new_status.value,
            "event_type":      lifecycle_after_gate.event_type.value,
            "lifecycle_id":    _LIFECYCLE_ID,
            "plan_id":         plan_id,
        },
    )

    preview_result = render_paper_dry_run_preview(
        planner_result.plan,
        gate_snapshot=_gate_result_to_snapshot(gate_result),
        lifecycle_snapshot=_lifecycle_state_to_snapshot(lifecycle_after_gate.state),
        preview_id=_PREVIEW_ID,
        rendered_at_utc=_RENDERED_AT,
    )
    return PreviewLedgerChainResults(
        planner_result, validation_result, gate_result,
        lifecycle_creation, lifecycle_after_gate, preview_result,
        ledger, ledger_results,
    )


def _entry_types(chain: PreviewLedgerChainResults) -> list[str]:
    return [e["entry_type"] for e in chain.ledger.entries]


def _limit_signal(**overrides) -> dict:
    return _valid_signal(order_type="limit", **overrides)


def _limit_sizing(**overrides) -> dict:
    return _valid_sizing(limit_price=150.0, **overrides)


_EXPECTED_PASS_ENTRY_TYPES = [
    "PLANNER_RESULT_RECORDED",
    "VALIDATION_RESULT_RECORDED",
    "SAFETY_GATE_RESULT_RECORDED",
    "LIFECYCLE_TRANSITION_RECORDED",  # PLAN_CREATED
    "LIFECYCLE_TRANSITION_RECORDED",  # SAFETY_GATE_PASSED
]


# ---------------------------------------------------------------------------
# 1-2, 4: full chain renders preview and records ledger entries in order
# ---------------------------------------------------------------------------

class TestFullChainPreviewAndLedger:
    def test_market_chain_renders_preview_and_records_ledger(self):
        chain = _run_chain_to_preview_ledger()
        assert chain.preview_result.result == "PASS"
        assert chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED
        assert _entry_types(chain) == _EXPECTED_PASS_ENTRY_TYPES
        assert chain.ledger.status is PaperAuditLedgerStatus.UPDATED

    def test_limit_chain_renders_preview_and_records_ledger(self):
        chain = _run_chain_to_preview_ledger(
            signal=_limit_signal(), sizing=_limit_sizing()
        )
        assert chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED
        assert chain.preview_result.preview["order_type"] == "limit"
        assert chain.preview_result.preview["limit_price"] == 150.0
        assert _entry_types(chain) == _EXPECTED_PASS_ENTRY_TYPES

    def test_ledger_entry_order_exact(self):
        chain = _run_chain_to_preview_ledger()
        types = _entry_types(chain)
        assert types == _EXPECTED_PASS_ENTRY_TYPES
        # The two lifecycle entries are PLAN_CREATED then SAFETY_GATE_PASSED.
        lc_entries = [
            e for e in chain.ledger.entries
            if e["entry_type"] == "LIFECYCLE_TRANSITION_RECORDED"
        ]
        assert [e["payload"]["event_type"] for e in lc_entries] == [
            "PLAN_CREATED", "SAFETY_GATE_PASSED",
        ]


# ---------------------------------------------------------------------------
# 3: preview is separate from the ledger (current expected boundary)
# ---------------------------------------------------------------------------

class TestPreviewSeparateFromLedger:
    def test_s34_has_no_preview_entry_type(self):
        # Current expected boundary, not a bug: S34 defines exactly six entry
        # types and none of them is a preview type. A future S39 schema
        # extension should add a dedicated PREVIEW_RESULT_RECORDED type.
        values = {m.value for m in PaperAuditLedgerEntryType}
        assert values == {
            "PLANNER_RESULT_RECORDED",
            "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED",
            "LIFECYCLE_TRANSITION_RECORDED",
            "BLOCKED_CHAIN_RECORDED",
            "ERROR_RECORDED",
        }
        assert not any("PREVIEW" in v for v in values)

    def test_s34_has_no_preview_source(self):
        assert "preview" not in _ledger_mod._ALLOWED_SOURCES
        assert _ledger_mod._ALLOWED_SOURCES == frozenset(
            {"planner", "validator", "safety_gate", "lifecycle", "chain"}
        )

    def test_preview_rendered_but_not_in_ledger(self):
        chain = _run_chain_to_preview_ledger()
        assert chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED
        # Exactly the five chain entries -- the preview is not one of them.
        assert len(chain.ledger.entries) == 5
        for entry in chain.ledger.entries:
            assert entry["entry_type"] != "PREVIEW_RESULT_RECORDED"
            assert "preview_id" not in entry["payload"]
            assert "preview_status" not in entry["payload"]

    def test_preview_remains_plain_display_only_dict(self):
        chain = _run_chain_to_preview_ledger()
        p = chain.preview_result.preview
        assert type(p) is dict
        assert p["display_only"] is True
        assert p["no_submit"] is True

    def test_ledger_refuses_preview_as_lifecycle_entry(self):
        # Misclassifying the preview as LIFECYCLE_TRANSITION_RECORDED is
        # structurally refused: the preview payload lacks the required
        # lifecycle keys (previous_status/new_status/event_type/lifecycle_id).
        chain = _run_chain_to_preview_ledger()
        preview_payload = {
            "preview_id":     chain.preview_result.preview["preview_id"],
            "preview_status": chain.preview_result.preview["preview_status"],
            "plan_id":        _PLAN_ID,
        }
        r = append_audit_entry(
            chain.ledger,
            entry_id="eid-preview-as-lifecycle",
            entry_type=EType.LIFECYCLE_TRANSITION_RECORDED,
            recorded_at_utc=_TS,
            source="lifecycle",
            payload=preview_payload,
        )
        assert r.result == "BLOCKED"
        assert "payload.required_keys" in r.criteria_failed
        assert r.state is chain.ledger
        assert len(r.state.entries) == 5


# ---------------------------------------------------------------------------
# 5: preview identity matches ledger payloads
# ---------------------------------------------------------------------------

class TestPreviewMatchesLedgerPayloads:
    def test_plan_id_consistent_across_preview_and_ledger(self):
        chain = _run_chain_to_preview_ledger()
        p = chain.preview_result.preview
        assert p["plan_id"] == _PLAN_ID
        for entry in chain.ledger.entries:
            assert entry["payload"]["plan_id"] == p["plan_id"]

    def test_lifecycle_id_consistent_across_preview_and_ledger(self):
        chain = _run_chain_to_preview_ledger()
        p = chain.preview_result.preview
        assert p["lifecycle_id"] == _LIFECYCLE_ID
        lc_entries = [
            e for e in chain.ledger.entries
            if e["entry_type"] == "LIFECYCLE_TRANSITION_RECORDED"
        ]
        for entry in lc_entries:
            assert entry["payload"]["lifecycle_id"] == p["lifecycle_id"]

    def test_candidate_and_run_id_in_preview(self):
        chain = _run_chain_to_preview_ledger()
        p = chain.preview_result.preview
        assert p["candidate_id"] == "cand-001"
        assert p["run_id"] == "run-001"


# ---------------------------------------------------------------------------
# 6-7: preview fixed values and notes
# ---------------------------------------------------------------------------

class TestPreviewFixedValues:
    def test_preview_fixed_values(self):
        p = _run_chain_to_preview_ledger().preview_result.preview
        assert p["display_only"] is True
        assert p["no_submit"] is True
        assert p["broker_payload_created"] is False
        for flag in _SAFETY_FLAG_NAMES:
            assert p[flag] is False

    def test_preview_notes_disclaimers(self):
        notes = _run_chain_to_preview_ledger().preview_result.preview["notes"]
        assert "not an order" in notes
        assert "broker payload" in notes
        assert "cannot be submitted" in notes


# ---------------------------------------------------------------------------
# 8-9: no ledger entry claims preview is an order or carries broker fields
# ---------------------------------------------------------------------------

class TestLedgerEntriesClean:
    def test_no_ledger_entry_claims_order_or_preview(self):
        chain = _run_chain_to_preview_ledger()
        for entry in chain.ledger.entries:
            payload = entry["payload"]
            assert "order_id" not in payload
            assert "broker_order_id" not in payload
            assert payload.get("order_action_requested") is None

    def test_no_ledger_entry_contains_broker_or_action_fields(self):
        # Phrases assembled from fragments so they never appear contiguously
        # in this module's own source.
        forbidden = (
            "broker_" + "account",
            "live_" + "account",
            "end" + "point",
            "api_" + "key",
            "submit_" + "order",
            "place_" + "order",
            "cancel_" + "order",
            "modify_" + "order",
            "live_" + "submit",
        )
        chain = _run_chain_to_preview_ledger()
        for entry in chain.ledger.entries:
            for key, value in entry["payload"].items():
                key_lower = key.lower()
                for phrase in forbidden:
                    assert phrase not in key_lower
                if isinstance(value, str):
                    value_lower = value.lower()
                    for phrase in forbidden:
                        assert phrase not in value_lower


# ---------------------------------------------------------------------------
# 10-12: planner blocks -> planner + blocked chain entries; preview None
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
class TestPlannerBlockedChains:
    def test_planner_block_ledger_and_no_preview(self, kwargs, expected_status):
        chain = _run_chain_to_preview_ledger(**kwargs)
        assert chain.planner_result.planner_status is expected_status
        assert _entry_types(chain) == [
            "PLANNER_RESULT_RECORDED", "BLOCKED_CHAIN_RECORDED",
        ]
        assert chain.validation_result is None
        assert chain.gate_result is None
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate is None
        assert chain.preview_result is None


# ---------------------------------------------------------------------------
# 13-16: gate blocks -> 4 entries; no lifecycle; preview None
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
class TestGateBlockedChains:
    def test_gate_block_ledger_and_no_preview(
        self, state_overrides, expected_gate_status
    ):
        chain = _run_chain_to_preview_ledger(state=_clean_state(**state_overrides))
        assert chain.gate_result.gate_status is expected_gate_status
        assert _entry_types(chain) == [
            "PLANNER_RESULT_RECORDED",
            "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED",
            "BLOCKED_CHAIN_RECORDED",
        ]
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate is None
        assert chain.preview_result is None


# ---------------------------------------------------------------------------
# 17: preview cannot render before SAFETY_GATE_PASSED
# ---------------------------------------------------------------------------

class TestPreviewSequencing:
    def test_preview_blocked_before_safety_gate_passed(self):
        chain = _run_chain_to_preview_ledger()
        planned_snapshot = _lifecycle_state_to_snapshot(
            chain.lifecycle_creation.state
        )
        assert planned_snapshot["status"] == "PLANNED"
        result = render_paper_dry_run_preview(
            chain.planner_result.plan,
            gate_snapshot=_gate_result_to_snapshot(chain.gate_result),
            lifecycle_snapshot=planned_snapshot,
            preview_id=_PREVIEW_ID,
            rendered_at_utc=_RENDERED_AT,
        )
        assert result.result == "BLOCKED"
        assert result.preview_status is PreviewStatus.BLOCKED_LIFECYCLE
        assert result.preview is None

    def test_preview_renders_after_safety_gate_passed(self):
        chain = _run_chain_to_preview_ledger()
        assert (
            chain.lifecycle_after_gate.state.status
            is LcStatus.GATE_PASSED_DRY_RUN_ONLY
        )
        assert chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED


# ---------------------------------------------------------------------------
# 18-19: neither preview render nor ledger append advances the lifecycle
# ---------------------------------------------------------------------------

class TestNothingAdvancesLifecycle:
    def test_preview_render_does_not_advance_lifecycle(self):
        chain = _run_chain_to_preview_ledger()
        state = chain.lifecycle_after_gate.state
        assert state.status is LcStatus.GATE_PASSED_DRY_RUN_ONLY
        assert len(state.events) == 2  # PLAN_CREATED + SAFETY_GATE_PASSED only

    def test_ledger_append_does_not_advance_lifecycle(self):
        chain = _run_chain_to_preview_ledger()
        state = chain.lifecycle_after_gate.state
        events_snapshot = copy.deepcopy(state.events)
        append_audit_entry(
            chain.ledger,
            entry_id="eid-extra",
            entry_type=EType.ERROR_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={"error_stage": "none", "blocker": "bookkeeping-only check"},
        )
        assert state.status is LcStatus.GATE_PASSED_DRY_RUN_ONLY
        assert state.events == events_snapshot


# ---------------------------------------------------------------------------
# 20-21: safety flags always False everywhere
# ---------------------------------------------------------------------------

def _scenario_chains():
    return [
        ("pass_market", _run_chain_to_preview_ledger()),
        ("pass_limit", _run_chain_to_preview_ledger(
            signal=_limit_signal(), sizing=_limit_sizing()
        )),
        (
            "planner_blocked",
            _run_chain_to_preview_ledger(
                approval=_valid_approval(approval_status="DRAFT")
            ),
        ),
        (
            "gate_blocked_kill_switch",
            _run_chain_to_preview_ledger(state=_clean_state(kill_switch_open=False)),
        ),
    ]


class TestSafetyFlagsAlwaysFalse:
    def test_preview_and_ledger_flags_false(self):
        for label, chain in _scenario_chains():
            if chain.preview_result is not None:
                for flag in _SAFETY_FLAG_NAMES:
                    assert getattr(chain.preview_result, flag) is False, (
                        f"preview {flag} should be False in {label}"
                    )
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.ledger, flag) is False, (
                    f"ledger state {flag} should be False in {label}"
                )
            for r in chain.ledger_results:
                for flag in _SAFETY_FLAG_NAMES:
                    assert getattr(r, flag) is False, (
                        f"ledger result {flag} should be False in {label}"
                    )

    def test_upstream_flags_false(self):
        for label, chain in _scenario_chains():
            for stage_result in (
                chain.planner_result, chain.validation_result, chain.gate_result,
                chain.lifecycle_creation, chain.lifecycle_after_gate,
            ):
                if stage_result is None:
                    continue
                for flag in _SAFETY_FLAG_NAMES:
                    assert getattr(stage_result, flag) is False, (
                        f"{flag} should be False in {label}"
                    )


# ---------------------------------------------------------------------------
# 22: inputs not mutated
# ---------------------------------------------------------------------------

class TestInputsNotMutated:
    def test_full_pass_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        state = _clean_state()
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal),
            copy.deepcopy(sizing), copy.deepcopy(state),
        )
        _run_chain_to_preview_ledger(
            approval=approval, signal=signal, sizing=sizing, state=state
        )
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]

    def test_blocked_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        state = _clean_state(kill_switch_open=False)
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal),
            copy.deepcopy(sizing), copy.deepcopy(state),
        )
        _run_chain_to_preview_ledger(
            approval=approval, signal=signal, sizing=sizing, state=state
        )
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]


# ---------------------------------------------------------------------------
# 23: determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_results_at_every_stage(self):
        first = _run_chain_to_preview_ledger()
        second = _run_chain_to_preview_ledger()
        assert first.planner_result == second.planner_result
        assert first.validation_result == second.validation_result
        assert first.gate_result == second.gate_result
        assert first.lifecycle_creation == second.lifecycle_creation
        assert first.lifecycle_after_gate == second.lifecycle_after_gate
        assert first.preview_result == second.preview_result
        assert first.ledger == second.ledger

    def test_same_blocked_inputs_same_results(self):
        first = _run_chain_to_preview_ledger(
            state=_clean_state(kill_switch_open=False)
        )
        second = _run_chain_to_preview_ledger(
            state=_clean_state(kill_switch_open=False)
        )
        assert first.gate_result == second.gate_result
        assert first.ledger == second.ledger
        assert first.preview_result is None and second.preview_result is None


# ---------------------------------------------------------------------------
# 24: duplicate ledger entry id still blocks
# ---------------------------------------------------------------------------

class TestDuplicateEntryIdBlocked:
    def test_duplicate_entry_id_blocks_and_preserves_ledger(self):
        chain = _run_chain_to_preview_ledger()
        existing_id = chain.ledger.entries[0]["entry_id"]
        r = append_audit_entry(
            chain.ledger,
            entry_id=existing_id,
            entry_type=EType.ERROR_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={"error_stage": "test", "blocker": "duplicate-check"},
        )
        assert r.result == "BLOCKED"
        assert existing_id in r.blocker
        assert r.entry is None
        assert r.state is chain.ledger
        assert len(r.state.entries) == 5


# ---------------------------------------------------------------------------
# 25: forbidden preview-like payload content blocked by the ledger
# ---------------------------------------------------------------------------

class TestForbiddenPayloadBlockedByLedger:
    @pytest.mark.parametrize(
        "bad_value",
        [
            "submit_" + "order",
            "api_" + "key",
            "auth_" + "token",
            "ht" + "tp://example",
            "end" + "point=/api",
        ],
        ids=["order_verb", "api_key", "token", "network", "endpoint"],
    )
    def test_preview_like_payload_with_forbidden_content_blocked(self, bad_value):
        chain = _run_chain_to_preview_ledger()
        r = append_audit_entry(
            chain.ledger,
            entry_id="eid-bad-preview",
            entry_type=EType.ERROR_RECORDED,
            recorded_at_utc=_TS,
            source="chain",
            payload={
                "error_stage": "preview",
                "blocker":     "preview rendering note: " + bad_value,
            },
        )
        assert r.result == "BLOCKED"
        assert "forbidden" in r.blocker
        assert r.entry is None
        assert r.state is chain.ledger
        assert len(r.state.entries) == 5


# ---------------------------------------------------------------------------
# 26: no real artifacts created
# ---------------------------------------------------------------------------

class TestNoArtifactsCreated:
    def test_full_chain_never_opens_a_file_at_runtime(self, monkeypatch):
        calls: list = []
        _real_ctor = builtins.open

        def _recording_ctor(*args, **kwargs):
            calls.append(args)
            return _real_ctor(*args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(builtins, "open", _recording_ctor)
            pass_chain = _run_chain_to_preview_ledger()
            blocked_chain = _run_chain_to_preview_ledger(
                state=_clean_state(kill_switch_open=False)
            )
        assert calls == [], f"unexpected file opens: {calls}"
        assert pass_chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED
        assert len(pass_chain.ledger.entries) == 5
        assert blocked_chain.preview_result is None
        assert len(blocked_chain.ledger.entries) == 4


# ---------------------------------------------------------------------------
# 27: this test module itself is clean
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
            assert pattern not in source, f"forbidden file I/O: {pattern!r}"

    def test_no_write_operations(self):
        source = self._source()
        for pattern in (
            "write_by" + "tes(",
            "make" + "dirs(",
            "mk" + "dir(",
        ):
            assert pattern not in source, f"forbidden write op: {pattern!r}"

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
            assert pattern not in source, f"forbidden import: {pattern!r}"

    def test_no_assembled_order_verbs(self):
        source = self._source()
        for pattern in (
            "submit_or" + "der",
            "place_or" + "der",
            "cancel_or" + "der",
            "modify_or" + "der",
            "live_su" + "bmit",
        ):
            assert pattern not in source, (
                f"order verb appears contiguously: {pattern!r}"
            )

    def test_no_env_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
        ):
            assert pattern not in source, f"forbidden call: {pattern!r}"

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
# 28: source modules in the chain are clean
# ---------------------------------------------------------------------------

_CHAIN_MODULES = [
    _planner_mod, _plan_mod, _gate_mod, _lifecycle_mod,
    _preview_mod, _ledger_mod, _approval_mod,
]
_CHAIN_MODULE_IDS = [
    "s30_planner", "s27_plan_validator", "s28_safety_gate", "s32_lifecycle",
    "s36_dry_run_preview", "s34_audit_ledger", "s25_approval_validator",
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
