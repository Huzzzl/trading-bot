"""
tests/test_paper_dry_run_preview.py
------------------------------------
S36: Tests for the pure offline dry-run/no-submit preview renderer.

All fixtures are plain in-memory dicts. No real preview, order plan, gate,
lifecycle, ledger, config, or any other artifact is created. No files are
read or written. No broker, network, credential, environment-variable, or
order access is made.

The rendered preview is a display-only in-memory dict. It is not an order
and not a broker payload. PREVIEW_RENDERED is never order approval or
paper/live trading approval. S28 PASS_DRY_RUN_ONLY remains only offline
clearance for a future dry-run/no-submit rendering step. Paper trading
remains not approved; live trading remains blocked.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect

import pytest

import src.research.paper_dry_run_preview as _preview_mod
from src.research.paper_dry_run_preview import (
    PaperDryRunPreviewResult,
    PaperDryRunPreviewStatus,
    render_paper_dry_run_preview,
)

# ---------------------------------------------------------------------------
# Constants and fixtures (plain in-memory dicts; no files; no artifacts)
# ---------------------------------------------------------------------------

_PLAN_ID      = "plan-001"
_CANDIDATE_ID = "cand-001"
_RUN_ID       = "run-001"
_LIFECYCLE_ID = "lc-001"
_PREVIEW_ID   = "preview-001"
_RENDERED_AT  = "2025-01-15T09:20:00Z"

_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

Status = PaperDryRunPreviewStatus


def _valid_plan(**overrides) -> dict:
    base = {
        "plan_schema_version":          "POP/1.0",
        "plan_type":                    "PAPER_ORDER_PLAN",
        "plan_status":                  "PLAN_READY_FOR_SAFETY_GATE",
        "plan_id":                      _PLAN_ID,
        "candidate_id":                 _CANDIDATE_ID,
        "run_id":                       _RUN_ID,
        "symbol":                       "AAPL",
        "side":                         "BUY",
        "order_type":                   "market",
        "quantity":                     10.0,
        "notional":                     1500.0,
        "limit_price":                  None,
        "time_in_force":                "day",
        "allowed_session":              "regular",
        "rationale":                    "momentum breakout signal detected on 60m bar",
        "dry_run_required":             True,
        "human_confirmation_required":  True,
        "kill_switch_required":         True,
        "safety_gate_required":         True,
        "broker_calls_made":            False,
        "credentials_read":             False,
        "network_calls_made":           False,
        "order_action_requested":       False,
        "live_trading_allowed":         False,
    }
    base.update(overrides)
    return base


def _valid_gate(**overrides) -> dict:
    base = {
        "result":                       "PASS",
        "gate_status":                  "PASS_DRY_RUN_ONLY",
        "plan_id":                      _PLAN_ID,
        "candidate_id":                 _CANDIDATE_ID,
        "run_id":                       _RUN_ID,
        "dry_run_required":             True,
        "human_confirmation_required":  True,
        "kill_switch_required":         True,
        "broker_calls_made":            False,
        "credentials_read":             False,
        "network_calls_made":           False,
        "order_action_requested":       False,
        "live_trading_allowed":         False,
    }
    base.update(overrides)
    return base


def _valid_lifecycle(**overrides) -> dict:
    base = {
        "lifecycle_id":           _LIFECYCLE_ID,
        "plan_id":                _PLAN_ID,
        "candidate_id":           _CANDIDATE_ID,
        "run_id":                 _RUN_ID,
        "status":                 "GATE_PASSED_DRY_RUN_ONLY",
        "broker_calls_made":      False,
        "credentials_read":       False,
        "network_calls_made":     False,
        "order_action_requested": False,
        "live_trading_allowed":   False,
    }
    base.update(overrides)
    return base


def _render(plan=None, gate=None, lifecycle=None, *,
            preview_id=_PREVIEW_ID, rendered_at_utc=_RENDERED_AT):
    return render_paper_dry_run_preview(
        _valid_plan() if plan is None else plan,
        gate_snapshot=_valid_gate() if gate is None else gate,
        lifecycle_snapshot=_valid_lifecycle() if lifecycle is None else lifecycle,
        preview_id=preview_id,
        rendered_at_utc=rendered_at_utc,
    )


_EXPECTED_PREVIEW_KEYS: frozenset[str] = frozenset({
    "preview_schema_version", "preview_type", "preview_id", "rendered_at_utc",
    "preview_status", "display_only", "no_submit", "broker_payload_created",
    "plan_id", "candidate_id", "run_id", "lifecycle_id",
    "symbol", "side", "order_type", "quantity", "notional", "limit_price",
    "time_in_force", "allowed_session", "rationale",
    "gate_status", "lifecycle_status",
    "dry_run_required", "human_confirmation_required", "kill_switch_required",
    "broker_calls_made", "credentials_read", "network_calls_made",
    "order_action_requested", "live_trading_allowed", "notes",
})


# ---------------------------------------------------------------------------
# Enum and dataclass shape
# ---------------------------------------------------------------------------

class TestEnumAndDataclass:
    def test_enum_members(self):
        assert {m.value for m in PaperDryRunPreviewStatus} == {
            "NOT_RENDERED", "PREVIEW_RENDERED", "BLOCKED_PLAN",
            "BLOCKED_GATE", "BLOCKED_LIFECYCLE", "BLOCKED_SAFETY",
            "ERROR_RENDERER",
        }

    def test_enum_is_str_enum(self):
        assert isinstance(PaperDryRunPreviewStatus.PREVIEW_RENDERED, str)

    def test_result_dataclass_is_frozen(self):
        result = _render()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.result = "MUTATED"

    def test_result_dataclass_fields(self):
        names = {f.name for f in dataclasses.fields(PaperDryRunPreviewResult)}
        assert names == {
            "result", "blocker", "preview_status", "preview",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }


# ---------------------------------------------------------------------------
# Valid market and limit previews render
# ---------------------------------------------------------------------------

class TestValidPreviewRenders:
    def test_market_preview_renders(self):
        result = _render()
        assert result.result == "PASS"
        assert result.preview_status is Status.PREVIEW_RENDERED
        assert result.blocker is None
        assert result.preview is not None
        assert result.criteria_failed == ()

    def test_limit_preview_renders(self):
        result = _render(plan=_valid_plan(order_type="limit", limit_price=150.0))
        assert result.result == "PASS"
        assert result.preview_status is Status.PREVIEW_RENDERED
        assert result.preview["order_type"] == "limit"
        assert result.preview["limit_price"] == 150.0

    def test_all_criteria_checked_on_pass(self):
        result = _render()
        assert result.criteria_checked == (
            "plan.schema", "plan.identity", "plan.intent", "plan.sizing",
            "plan.fixed_booleans", "plan.safety_flags",
            "gate.schema", "gate.status", "gate.identity",
            "gate.fixed_booleans", "gate.safety_flags",
            "lifecycle.schema", "lifecycle.status", "lifecycle.identity",
            "lifecycle.safety_flags",
            "preview.identity", "preview.rendered",
        )

    def test_preview_carries_plan_values(self):
        result = _render()
        p = result.preview
        assert p["plan_id"] == _PLAN_ID
        assert p["candidate_id"] == _CANDIDATE_ID
        assert p["run_id"] == _RUN_ID
        assert p["lifecycle_id"] == _LIFECYCLE_ID
        assert p["symbol"] == "AAPL"
        assert p["side"] == "BUY"
        assert p["order_type"] == "market"
        assert p["quantity"] == 10.0
        assert p["notional"] == 1500.0
        assert p["limit_price"] is None
        assert p["time_in_force"] == "day"
        assert p["allowed_session"] == "regular"
        assert p["gate_status"] == "PASS_DRY_RUN_ONLY"
        assert p["lifecycle_status"] == "GATE_PASSED_DRY_RUN_ONLY"
        assert p["preview_id"] == _PREVIEW_ID
        assert p["rendered_at_utc"] == _RENDERED_AT


# ---------------------------------------------------------------------------
# Preview shape: exact key set and fixed values
# ---------------------------------------------------------------------------

class TestPreviewShape:
    def test_preview_has_exact_key_set(self):
        result = _render()
        assert set(result.preview.keys()) == _EXPECTED_PREVIEW_KEYS

    def test_preview_fixed_values(self):
        p = _render().preview
        assert p["preview_schema_version"] == "PDRP/1.0"
        assert p["preview_type"] == "PAPER_DRY_RUN_NO_SUBMIT_PREVIEW"
        assert p["preview_status"] == "PREVIEW_RENDERED"
        assert p["dry_run_required"] is True
        assert p["human_confirmation_required"] is True
        assert p["kill_switch_required"] is True

    def test_display_only_true(self):
        assert _render().preview["display_only"] is True

    def test_no_submit_true(self):
        assert _render().preview["no_submit"] is True

    def test_broker_payload_created_false(self):
        assert _render().preview["broker_payload_created"] is False

    def test_preview_safety_flags_all_false(self):
        p = _render().preview
        for flag in _SAFETY_FLAG_NAMES:
            assert p[flag] is False

    def test_preview_notes_say_not_an_order(self):
        notes = _render().preview["notes"]
        assert "display-only" in notes
        assert "not an order" in notes

    def test_preview_notes_say_cannot_be_submitted(self):
        notes = _render().preview["notes"]
        assert "cannot be submitted" in notes
        assert "live trading remains blocked" in notes


# ---------------------------------------------------------------------------
# Preview contains no broker/credential/network/action fields
# ---------------------------------------------------------------------------

class TestPreviewNoForbiddenFields:
    # Forbidden key phrases assembled from fragments. The five safety-flag
    # keys (value exactly False) are the only allowed keys touching these
    # concept families and are exempted by exact name.
    _FORBIDDEN_KEY_PHRASES = (
        "broker_" + "account",
        "live_" + "account",
        "end" + "point",
        "credential",
        "token",
        "api_" + "key",
        "secret",
        "submit_" + "order",
        "place_" + "order",
        "cancel_" + "order",
        "modify_" + "order",
        "live_" + "submit",
        "ht" + "tp",
    )

    def test_preview_keys_contain_no_forbidden_phrases(self):
        p = _render().preview
        for key in p:
            if key in _SAFETY_FLAG_NAMES:
                assert p[key] is False
                continue
            key_lower = key.lower()
            for phrase in self._FORBIDDEN_KEY_PHRASES:
                assert phrase not in key_lower, (
                    f"forbidden phrase {phrase!r} in preview key {key!r}"
                )

    def test_preview_is_plain_dict(self):
        assert type(_render().preview) is dict


# ---------------------------------------------------------------------------
# Plan blocks (BLOCKED_PLAN)
# ---------------------------------------------------------------------------

class TestPlanBlocks:
    @pytest.mark.parametrize(
        ("overrides", "expected_criterion"),
        [
            ({"plan_schema_version": "POP/2.0"}, "plan.schema"),
            ({"plan_type": "ORDER"}, "plan.schema"),
            ({"plan_status": "PLAN_DRAFT"}, "plan.schema"),
            ({"plan_id": ""}, "plan.identity"),
            ({"candidate_id": None}, "plan.identity"),
            ({"run_id": "   "}, "plan.identity"),
            ({"symbol": ""}, "plan.identity"),
            ({"side": "HOLD"}, "plan.intent"),
            ({"order_type": "stop"}, "plan.intent"),
            ({"quantity": 0.0}, "plan.sizing"),
            ({"quantity": -1.0}, "plan.sizing"),
            ({"quantity": float("inf")}, "plan.sizing"),
            ({"notional": 0.0}, "plan.sizing"),
            ({"notional": None}, "plan.sizing"),
            ({"limit_price": 150.0}, "plan.sizing"),  # market with limit_price
            ({"dry_run_required": False}, "plan.fixed_booleans"),
            ({"human_confirmation_required": False}, "plan.fixed_booleans"),
            ({"kill_switch_required": False}, "plan.fixed_booleans"),
            ({"safety_gate_required": False}, "plan.fixed_booleans"),
        ],
        ids=[
            "schema_version", "plan_type", "plan_status",
            "empty_plan_id", "none_candidate_id", "blank_run_id", "empty_symbol",
            "bad_side", "bad_order_type",
            "zero_quantity", "negative_quantity", "inf_quantity",
            "zero_notional", "none_notional", "market_with_limit_price",
            "dry_run_false", "human_confirm_false", "kill_switch_false",
            "gate_required_false",
        ],
    )
    def test_plan_failure_blocks_as_blocked_plan(self, overrides, expected_criterion):
        result = _render(plan=_valid_plan(**overrides))
        assert result.result == "BLOCKED"
        assert result.preview_status is Status.BLOCKED_PLAN
        assert result.preview is None
        assert expected_criterion in result.criteria_failed

    def test_limit_without_limit_price_blocks(self):
        result = _render(plan=_valid_plan(order_type="limit", limit_price=None))
        assert result.preview_status is Status.BLOCKED_PLAN
        assert "plan.sizing" in result.criteria_failed

    def test_limit_with_negative_limit_price_blocks(self):
        result = _render(plan=_valid_plan(order_type="limit", limit_price=-5.0))
        assert result.preview_status is Status.BLOCKED_PLAN
        assert "plan.sizing" in result.criteria_failed

    def test_non_dict_plan_blocks(self):
        result = _render(plan="not-a-dict")
        assert result.result == "BLOCKED"
        assert result.preview_status is Status.BLOCKED_PLAN
        assert result.preview is None

    @pytest.mark.parametrize("flag", _SAFETY_FLAG_NAMES)
    def test_plan_safety_flag_true_blocks_as_blocked_safety(self, flag):
        result = _render(plan=_valid_plan(**{flag: True}))
        assert result.result == "BLOCKED"
        assert result.preview_status is Status.BLOCKED_SAFETY
        assert result.preview is None
        assert "plan.safety_flags" in result.criteria_failed

    @pytest.mark.parametrize("flag", _SAFETY_FLAG_NAMES)
    def test_plan_safety_flag_missing_blocks_as_blocked_safety(self, flag):
        plan = _valid_plan()
        del plan[flag]
        result = _render(plan=plan)
        assert result.preview_status is Status.BLOCKED_SAFETY


# ---------------------------------------------------------------------------
# Gate blocks (BLOCKED_GATE)
# ---------------------------------------------------------------------------

class TestGateBlocks:
    @pytest.mark.parametrize(
        ("overrides", "expected_criterion"),
        [
            ({"result": "BLOCKED"}, "gate.status"),
            ({"gate_status": "BLOCKED_KILL_SWITCH"}, "gate.status"),
            ({"gate_status": "NOT_EVALUATED"}, "gate.status"),
            ({"plan_id": "other-plan"}, "gate.identity"),
            ({"candidate_id": "other-cand"}, "gate.identity"),
            ({"run_id": "other-run"}, "gate.identity"),
            ({"dry_run_required": False}, "gate.fixed_booleans"),
            ({"human_confirmation_required": False}, "gate.fixed_booleans"),
            ({"kill_switch_required": False}, "gate.fixed_booleans"),
        ],
        ids=[
            "result_blocked", "gate_status_blocked", "gate_status_not_evaluated",
            "plan_id_mismatch", "candidate_id_mismatch", "run_id_mismatch",
            "dry_run_false", "human_confirm_false", "kill_switch_false",
        ],
    )
    def test_gate_failure_blocks_as_blocked_gate(self, overrides, expected_criterion):
        result = _render(gate=_valid_gate(**overrides))
        assert result.result == "BLOCKED"
        assert result.preview_status is Status.BLOCKED_GATE
        assert result.preview is None
        assert expected_criterion in result.criteria_failed

    def test_non_dict_gate_blocks(self):
        result = _render(gate="not-a-dict")
        assert result.preview_status is Status.BLOCKED_GATE
        assert "gate.schema" in result.criteria_failed

    @pytest.mark.parametrize("flag", _SAFETY_FLAG_NAMES)
    def test_gate_safety_flag_true_blocks_as_blocked_safety(self, flag):
        result = _render(gate=_valid_gate(**{flag: True}))
        assert result.result == "BLOCKED"
        assert result.preview_status is Status.BLOCKED_SAFETY
        assert result.preview is None
        assert "gate.safety_flags" in result.criteria_failed


# ---------------------------------------------------------------------------
# Lifecycle blocks (BLOCKED_LIFECYCLE)
# ---------------------------------------------------------------------------

class TestLifecycleBlocks:
    @pytest.mark.parametrize(
        ("overrides", "expected_criterion"),
        [
            ({"status": "PLANNED"}, "lifecycle.status"),
            ({"status": "DRY_RUN_RENDERED"}, "lifecycle.status"),
            ({"status": "PAPER_ORDER_FILLED"}, "lifecycle.status"),
            ({"lifecycle_id": ""}, "lifecycle.identity"),
            ({"plan_id": "other-plan"}, "lifecycle.identity"),
            ({"candidate_id": "other-cand"}, "lifecycle.identity"),
            ({"run_id": "other-run"}, "lifecycle.identity"),
        ],
        ids=[
            "status_planned", "status_dry_run_rendered", "status_filled",
            "empty_lifecycle_id", "plan_id_mismatch",
            "candidate_id_mismatch", "run_id_mismatch",
        ],
    )
    def test_lifecycle_failure_blocks_as_blocked_lifecycle(
        self, overrides, expected_criterion
    ):
        result = _render(lifecycle=_valid_lifecycle(**overrides))
        assert result.result == "BLOCKED"
        assert result.preview_status is Status.BLOCKED_LIFECYCLE
        assert result.preview is None
        assert expected_criterion in result.criteria_failed

    def test_non_dict_lifecycle_blocks(self):
        result = _render(lifecycle=42)
        assert result.preview_status is Status.BLOCKED_LIFECYCLE
        assert "lifecycle.schema" in result.criteria_failed

    @pytest.mark.parametrize("flag", _SAFETY_FLAG_NAMES)
    def test_lifecycle_safety_flag_true_blocks_as_blocked_safety(self, flag):
        result = _render(lifecycle=_valid_lifecycle(**{flag: True}))
        assert result.result == "BLOCKED"
        assert result.preview_status is Status.BLOCKED_SAFETY
        assert result.preview is None
        assert "lifecycle.safety_flags" in result.criteria_failed


# ---------------------------------------------------------------------------
# Preview identity blocks
# ---------------------------------------------------------------------------

class TestPreviewIdentityBlocks:
    @pytest.mark.parametrize("bad", ["", "   ", None, 42], ids=["empty", "blank", "none", "int"])
    def test_invalid_preview_id_blocks(self, bad):
        result = _render(preview_id=bad)
        assert result.result == "BLOCKED"
        assert result.preview is None
        assert "preview.identity" in result.criteria_failed

    @pytest.mark.parametrize("bad", ["", "   ", None, 42], ids=["empty", "blank", "none", "int"])
    def test_invalid_rendered_at_blocks(self, bad):
        result = _render(rendered_at_utc=bad)
        assert result.result == "BLOCKED"
        assert result.preview is None
        assert "preview.identity" in result.criteria_failed


# ---------------------------------------------------------------------------
# ERROR_RENDERER on unexpected exception
# ---------------------------------------------------------------------------

class _ExplodingDict(dict):
    """Passes .get()-based checks but raises on item access in render."""
    def __getitem__(self, key):
        raise RuntimeError("boom")


class TestErrorRenderer:
    def test_unexpected_exception_returns_error(self):
        result = _render(plan=_ExplodingDict(_valid_plan()))
        assert result.result == "ERROR"
        assert result.preview_status is Status.ERROR_RENDERER
        assert result.preview is None
        assert "renderer raised" in result.blocker

    def test_error_result_safety_flags_false(self):
        result = _render(plan=_ExplodingDict(_valid_plan()))
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(result, flag) is False


# ---------------------------------------------------------------------------
# Inputs not mutated
# ---------------------------------------------------------------------------

class TestInputsNotMutated:
    def test_pass_render_does_not_mutate_inputs(self):
        plan = _valid_plan()
        gate = _valid_gate()
        lifecycle = _valid_lifecycle()
        snapshots = (
            copy.deepcopy(plan), copy.deepcopy(gate), copy.deepcopy(lifecycle)
        )
        _render(plan=plan, gate=gate, lifecycle=lifecycle)
        assert plan == snapshots[0]
        assert gate == snapshots[1]
        assert lifecycle == snapshots[2]

    def test_blocked_render_does_not_mutate_inputs(self):
        plan = _valid_plan(side="HOLD")
        gate = _valid_gate()
        lifecycle = _valid_lifecycle()
        snapshots = (
            copy.deepcopy(plan), copy.deepcopy(gate), copy.deepcopy(lifecycle)
        )
        _render(plan=plan, gate=gate, lifecycle=lifecycle)
        assert plan == snapshots[0]
        assert gate == snapshots[1]
        assert lifecycle == snapshots[2]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output_on_pass(self):
        assert _render() == _render()

    def test_same_input_same_output_on_blocked(self):
        first = _render(gate=_valid_gate(gate_status="BLOCKED_KILL_SWITCH"))
        second = _render(gate=_valid_gate(gate_status="BLOCKED_KILL_SWITCH"))
        assert first == second

    def test_same_preview_dict_contents(self):
        assert _render().preview == _render().preview


# ---------------------------------------------------------------------------
# Result safety flags always False
# ---------------------------------------------------------------------------

class TestResultSafetyFlagsAlwaysFalse:
    def _scenarios(self):
        return [
            ("pass_market", _render()),
            ("pass_limit", _render(
                plan=_valid_plan(order_type="limit", limit_price=150.0)
            )),
            ("blocked_plan", _render(plan=_valid_plan(side="HOLD"))),
            ("blocked_gate", _render(gate=_valid_gate(result="BLOCKED"))),
            ("blocked_lifecycle", _render(
                lifecycle=_valid_lifecycle(status="PLANNED")
            )),
            ("blocked_safety", _render(
                plan=_valid_plan(live_trading_allowed=True)
            )),
            ("error", _render(plan=_ExplodingDict(_valid_plan()))),
        ]

    def test_result_flags_false_in_every_scenario(self):
        for label, result in self._scenarios():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(result, flag) is False, (
                    f"result {flag} should be False in {label}"
                )

    def test_blocked_safety_never_releases_preview(self):
        for flag in _SAFETY_FLAG_NAMES:
            for target in ("plan", "gate", "lifecycle"):
                kwargs = {
                    "plan": _valid_plan(**{flag: True}) if target == "plan" else None,
                    "gate": _valid_gate(**{flag: True}) if target == "gate" else None,
                    "lifecycle": (
                        _valid_lifecycle(**{flag: True})
                        if target == "lifecycle" else None
                    ),
                }
                result = _render(**kwargs)
                assert result.preview_status is Status.BLOCKED_SAFETY
                assert result.preview is None


# ---------------------------------------------------------------------------
# Production module source is clean
# ---------------------------------------------------------------------------

class TestProductionModuleClean:
    def _source(self) -> str:
        return inspect.getsource(_preview_mod)

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
            "read_by" + "tes(",
            "write_by" + "tes(",
        ):
            assert pattern not in source, f"forbidden file I/O: {pattern!r}"

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

    def test_no_broker_env_or_order_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
            "submit_or" + "der",
            "place_or" + "der",
            "cancel_or" + "der",
            "modify_or" + "der",
            "live_su" + "bmit",
        ):
            assert pattern not in source, f"forbidden call/word: {pattern!r}"

    def test_no_runtime_or_execution_imports(self):
        source = self._source()
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
            "from src." + "main",
            "import src." + "main",
        ):
            assert pattern not in source, f"forbidden module import: {pattern!r}"

    def test_no_chain_module_imports(self):
        # The renderer must not call the planner/validator/gate/lifecycle/
        # ledger -- it only checks already-loaded dict snapshots.
        source = self._source()
        for pattern in (
            "paper_order_" + "planner",
            "paper_order_plan_" + "validator",
            "paper_approval_" + "validator",
            "paper_order_safety_" + "gate",
            "paper_order_" + "lifecycle",
            "paper_audit_" + "ledger",
        ):
            assert pattern not in source, (
                f"renderer must not import chain module: {pattern!r}"
            )

    def test_only_stdlib_imports(self):
        tree_lines = [
            line.strip() for line in self._source().splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        allowed_prefixes = (
            "import math",
            "from __future__ import",
            "from dataclasses import",
            "from enum import",
            "from typing import",
        )
        for line in tree_lines:
            assert line.startswith(allowed_prefixes), (
                f"unexpected import in production module: {line!r}"
            )


# ---------------------------------------------------------------------------
# This test module itself is clean
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
