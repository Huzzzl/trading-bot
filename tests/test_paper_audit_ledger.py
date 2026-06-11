"""
tests/test_paper_audit_ledger.py
---------------------------------
S34: Tests for the pure offline paper audit ledger recorder.

All fixtures are plain in-memory dicts. No real artifacts are created.
No files are read or written. No persistence is performed. No broker,
network, credential, environment-variable, or order access is made.

Audit ledger entries are bookkeeping only -- recording a planner/gate/
lifecycle result changes an in-memory record and never performs,
initiates, or approves any order action. Paper trading remains not
approved; live trading remains blocked.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import FrozenInstanceError, fields as dataclass_fields, replace

import pytest

import src.research.paper_audit_ledger as _ledger_mod
from src.research.paper_audit_ledger import (
    PaperAuditLedgerEntryType,
    PaperAuditLedgerResult,
    PaperAuditLedgerState,
    PaperAuditLedgerStatus,
    append_audit_entry,
    create_empty_audit_ledger,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_LEDGER_ID = "ledger-001"
_RECORDED_AT = "2025-01-15T09:10:00Z"

Status = PaperAuditLedgerStatus
EntryType = PaperAuditLedgerEntryType


def _empty_ledger() -> PaperAuditLedgerState:
    result = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
    assert result.result == "PASS"
    return result.state


# (entry_type, source, payload) tuples that are individually valid.
_VALID_ENTRIES: dict[PaperAuditLedgerEntryType, tuple[str, dict]] = {
    EntryType.PLANNER_RESULT_RECORDED: (
        "planner",
        {"planner_status": "PLAN_CREATED", "result": "PASS", "plan_id": "plan-001"},
    ),
    EntryType.VALIDATION_RESULT_RECORDED: (
        "validator",
        {"validation_result": "PASS", "plan_id": "plan-001"},
    ),
    EntryType.SAFETY_GATE_RESULT_RECORDED: (
        "safety_gate",
        {"gate_status": "PASS_DRY_RUN_ONLY", "result": "PASS", "plan_id": "plan-001"},
    ),
    EntryType.LIFECYCLE_TRANSITION_RECORDED: (
        "lifecycle",
        {
            "previous_status": "PLANNED",
            "new_status": "GATE_PASSED_DRY_RUN_ONLY",
            "event_type": "SAFETY_GATE_PASSED",
            "lifecycle_id": "lc-001",
        },
    ),
    EntryType.BLOCKED_CHAIN_RECORDED: (
        "chain",
        {"blocked_stage": "safety_gate", "blocker": "kill switch closed"},
    ),
    EntryType.ERROR_RECORDED: (
        "chain",
        {"error_stage": "planner", "blocker": "validator raised"},
    ),
}


# Sentinel so callers can pass an explicit None for source/payload (the
# default-substitution only applies when the argument is omitted entirely).
_UNSET = object()


def _append(
    ledger, entry_type, *, entry_id="entry-001", source=_UNSET, payload=_UNSET, at=_RECORDED_AT
):
    default_source, default_payload = _VALID_ENTRIES[entry_type]
    return append_audit_entry(
        ledger,
        entry_id=entry_id,
        entry_type=entry_type,
        recorded_at_utc=at,
        source=default_source if source is _UNSET else source,
        payload=default_payload if payload is _UNSET else payload,
    )


_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

# Forbidden words assembled from fragments so the joined words never appear
# contiguously in this module's source.
_FORBIDDEN_ACTION_WORDS: tuple[str, ...] = (
    "submit_" + "order",
    "place_" + "order",
    "cancel_" + "order",
    "modify_" + "order",
    "live_" + "submit",
)
_FORBIDDEN_CREDENTIAL_WORDS: tuple[str, ...] = (
    "api_key", "secret", "credential", "token",
)
_FORBIDDEN_NETWORK_WORDS: tuple[str, ...] = (
    "http", "https", "endpoint",
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestStatusEnum:
    def test_all_expected_values_present(self):
        assert {m.value for m in Status} == {"EMPTY", "UPDATED", "BLOCKED", "ERROR"}

    def test_status_enum_is_str_subclass(self):
        for member in Status:
            assert isinstance(member, str)
            assert member == member.value


class TestEntryTypeEnum:
    def test_all_expected_values_present(self):
        expected = {
            "PLANNER_RESULT_RECORDED", "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED", "LIFECYCLE_TRANSITION_RECORDED",
            "BLOCKED_CHAIN_RECORDED", "ERROR_RECORDED",
        }
        assert {m.value for m in EntryType} == expected

    def test_entry_enum_is_str_subclass(self):
        for member in EntryType:
            assert isinstance(member, str)
            assert member == member.value


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_state_is_frozen(self):
        ledger = _empty_ledger()
        with pytest.raises(FrozenInstanceError):
            ledger.status = Status.ERROR  # type: ignore[misc]

    def test_result_is_frozen(self):
        result = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        with pytest.raises(FrozenInstanceError):
            result.result = "tampered"  # type: ignore[misc]

    def test_state_has_expected_fields(self):
        field_names = {f.name for f in dataclass_fields(PaperAuditLedgerState)}
        expected = {
            "ledger_id", "entries", "status", "last_entry_id", "blocker",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_result_has_expected_fields(self):
        field_names = {f.name for f in dataclass_fields(PaperAuditLedgerResult)}
        expected = {
            "result", "blocker", "state", "entry",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected


# ---------------------------------------------------------------------------
# create_empty_audit_ledger
# ---------------------------------------------------------------------------

class TestCreateEmptyLedger:
    def test_valid_creation(self):
        result = create_empty_audit_ledger(ledger_id=_LEDGER_ID)
        assert result.result == "PASS"
        assert result.blocker is None
        assert result.entry is None
        assert result.criteria_checked == ("ledger.identity", "ledger.created")
        assert result.criteria_failed == ()

    def test_empty_ledger_shape(self):
        ledger = _empty_ledger()
        assert ledger.ledger_id == _LEDGER_ID
        assert ledger.entries == ()
        assert ledger.status is Status.EMPTY
        assert ledger.last_entry_id is None
        assert ledger.blocker is None

    @pytest.mark.parametrize("ledger_id", ["", "   ", None, 123])
    def test_invalid_ledger_id_blocked(self, ledger_id):
        result = create_empty_audit_ledger(ledger_id=ledger_id)
        assert result.result == "BLOCKED"
        assert result.state is None
        assert "ledger.identity" in result.criteria_failed


# ---------------------------------------------------------------------------
# Appending valid entries
# ---------------------------------------------------------------------------

class TestAppendValidEntries:
    @pytest.mark.parametrize("entry_type", list(EntryType))
    def test_each_entry_type_appends(self, entry_type):
        result = _append(_empty_ledger(), entry_type)
        assert result.result == "PASS"
        assert result.state.status is Status.UPDATED
        assert result.state.last_entry_id == "entry-001"
        assert len(result.state.entries) == 1
        entry = result.state.entries[0]
        assert entry["entry_id"] == "entry-001"
        assert entry["entry_type"] == entry_type.value
        assert entry["recorded_at_utc"] == _RECORDED_AT
        source, payload = _VALID_ENTRIES[entry_type]
        assert entry["source"] == source
        assert entry["payload"] == payload

    def test_append_criteria_checked_order(self):
        result = _append(_empty_ledger(), EntryType.PLANNER_RESULT_RECORDED)
        assert result.criteria_checked == (
            "ledger.schema", "ledger.safety_flags", "entry.identity",
            "entry.duplicate_id", "entry.type", "entry.timestamp",
            "entry.source", "payload.schema", "payload.required_keys",
            "payload.source_matches_type", "payload.forbidden_content",
            "entry.appended",
        )
        assert result.criteria_failed == ()

    def test_result_entry_mirrors_appended_entry(self):
        result = _append(_empty_ledger(), EntryType.PLANNER_RESULT_RECORDED)
        assert result.entry == result.state.entries[-1]

    def test_entries_append_in_order(self):
        ledger = _empty_ledger()
        ledger = _append(
            ledger, EntryType.PLANNER_RESULT_RECORDED, entry_id="e1"
        ).state
        ledger = _append(
            ledger, EntryType.VALIDATION_RESULT_RECORDED, entry_id="e2"
        ).state
        ledger = _append(
            ledger, EntryType.SAFETY_GATE_RESULT_RECORDED, entry_id="e3"
        ).state
        assert [e["entry_id"] for e in ledger.entries] == ["e1", "e2", "e3"]
        assert [e["entry_type"] for e in ledger.entries] == [
            "PLANNER_RESULT_RECORDED",
            "VALIDATION_RESULT_RECORDED",
            "SAFETY_GATE_RESULT_RECORDED",
        ]
        assert ledger.last_entry_id == "e3"


# ---------------------------------------------------------------------------
# Immutability and purity
# ---------------------------------------------------------------------------

class TestImmutabilityAndPurity:
    def test_old_ledger_not_mutated(self):
        ledger = _empty_ledger()
        _append(ledger, EntryType.PLANNER_RESULT_RECORDED)
        assert ledger.entries == ()
        assert ledger.status is Status.EMPTY
        assert ledger.last_entry_id is None

    def test_old_ledger_entries_not_mutated_on_second_append(self):
        ledger = _append(_empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, entry_id="e1").state
        snapshot = copy.deepcopy(ledger.entries)
        _append(ledger, EntryType.VALIDATION_RESULT_RECORDED, entry_id="e2")
        assert ledger.entries == snapshot
        assert len(ledger.entries) == 1

    def test_payload_deep_copied_into_entry(self):
        payload = {
            "planner_status": "PLAN_CREATED", "result": "PASS", "plan_id": "plan-001",
            "nested": {"detail": "original"},
        }
        result = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, payload=payload
        )
        payload["nested"]["detail"] = "mutated"
        payload["plan_id"] = "plan-999"
        assert result.state.entries[0]["payload"]["nested"]["detail"] == "original"
        assert result.state.entries[0]["payload"]["plan_id"] == "plan-001"

    def test_result_entry_deep_copied_from_state_entry(self):
        result = _append(_empty_ledger(), EntryType.PLANNER_RESULT_RECORDED)
        result.entry["payload"]["plan_id"] = "mutated"
        assert result.state.entries[-1]["payload"]["plan_id"] == "plan-001"

    def test_same_input_same_output(self):
        ledger = _empty_ledger()
        r1 = _append(ledger, EntryType.PLANNER_RESULT_RECORDED)
        r2 = _append(ledger, EntryType.PLANNER_RESULT_RECORDED)
        assert r1 == r2

    def test_same_create_input_same_output(self):
        assert create_empty_audit_ledger(ledger_id=_LEDGER_ID) == \
            create_empty_audit_ledger(ledger_id=_LEDGER_ID)


# ---------------------------------------------------------------------------
# Duplicate entry id
# ---------------------------------------------------------------------------

class TestDuplicateEntryId:
    def test_duplicate_entry_id_blocked(self):
        ledger = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, entry_id="dup"
        ).state
        result = _append(
            ledger, EntryType.VALIDATION_RESULT_RECORDED, entry_id="dup"
        )
        assert result.result == "BLOCKED"
        assert "entry.duplicate_id" in result.criteria_failed
        assert result.entry is None

    def test_duplicate_returns_old_ledger_unchanged(self):
        ledger = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, entry_id="dup"
        ).state
        result = _append(
            ledger, EntryType.VALIDATION_RESULT_RECORDED, entry_id="dup"
        )
        assert result.state is ledger
        assert len(result.state.entries) == 1


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_non_ledger_object_blocked_with_state_none(self):
        result = append_audit_entry(
            "not-a-ledger",
            entry_id="e1",
            entry_type=EntryType.PLANNER_RESULT_RECORDED,
            recorded_at_utc=_RECORDED_AT,
            source="planner",
            payload={"planner_status": "X", "result": "PASS", "plan_id": "p"},
        )
        assert result.result == "BLOCKED"
        assert result.state is None
        assert "ledger.schema" in result.criteria_failed

    def test_tainted_ledger_safety_flag_blocked(self):
        ledger = replace(_empty_ledger(), order_action_requested=True)
        result = _append(ledger, EntryType.PLANNER_RESULT_RECORDED)
        assert result.result == "BLOCKED"
        assert "ledger.safety_flags" in result.criteria_failed
        assert result.state is ledger

    @pytest.mark.parametrize("entry_id", ["", "   ", None, 123])
    def test_invalid_entry_id_blocked(self, entry_id):
        result = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, entry_id=entry_id
        )
        assert result.result == "BLOCKED"
        assert "entry.identity" in result.criteria_failed

    def test_invalid_entry_type_blocked(self):
        result = append_audit_entry(
            _empty_ledger(),
            entry_id="e1",
            entry_type="PLANNER_RESULT_RECORDED",  # raw string, not enum
            recorded_at_utc=_RECORDED_AT,
            source="planner",
            payload={"planner_status": "X", "result": "PASS", "plan_id": "p"},
        )
        assert result.result == "BLOCKED"
        assert "entry.type" in result.criteria_failed

    @pytest.mark.parametrize("recorded_at", ["", "   ", None])
    def test_empty_recorded_at_blocked(self, recorded_at):
        result = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, at=recorded_at
        )
        assert result.result == "BLOCKED"
        assert "entry.timestamp" in result.criteria_failed

    @pytest.mark.parametrize("source", ["", "   ", None, "unknown_source"])
    def test_invalid_source_blocked(self, source):
        result = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, source=source
        )
        assert result.result == "BLOCKED"
        assert "entry.source" in result.criteria_failed

    def test_non_dict_payload_blocked(self):
        result = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, payload="not-a-dict"
        )
        assert result.result == "BLOCKED"
        assert "payload.schema" in result.criteria_failed


# ---------------------------------------------------------------------------
# Required payload keys and source/type match
# ---------------------------------------------------------------------------

class TestPayloadRequirements:
    @pytest.mark.parametrize("entry_type", list(EntryType))
    def test_missing_required_keys_blocked(self, entry_type):
        source, payload = _VALID_ENTRIES[entry_type]
        # Drop one required key.
        incomplete = dict(payload)
        dropped = next(iter(incomplete))
        del incomplete[dropped]
        result = _append(
            _empty_ledger(), entry_type, source=source, payload=incomplete
        )
        assert result.result == "BLOCKED"
        assert "payload.required_keys" in result.criteria_failed
        assert result.state.entries == ()

    @pytest.mark.parametrize("entry_type", list(EntryType))
    def test_source_mismatch_blocked(self, entry_type):
        _, payload = _VALID_ENTRIES[entry_type]
        # Choose a wrong-but-allowed source.
        correct_source = _VALID_ENTRIES[entry_type][0]
        wrong_source = "validator" if correct_source != "validator" else "planner"
        result = _append(
            _empty_ledger(), entry_type, source=wrong_source, payload=payload
        )
        assert result.result == "BLOCKED"
        assert "payload.source_matches_type" in result.criteria_failed

    def test_blocked_and_error_both_require_chain_source(self):
        for entry_type in (
            EntryType.BLOCKED_CHAIN_RECORDED, EntryType.ERROR_RECORDED
        ):
            source, payload = _VALID_ENTRIES[entry_type]
            assert source == "chain"
            result = _append(_empty_ledger(), entry_type)
            assert result.result == "PASS"


# ---------------------------------------------------------------------------
# Forbidden payload content
# ---------------------------------------------------------------------------

class TestForbiddenPayload:
    def _assert_blocked_unchanged(self, ledger, result):
        assert result.result == "BLOCKED"
        assert "payload.forbidden_content" in result.criteria_failed
        assert result.state is ledger
        assert result.entry is None
        assert len(result.state.entries) == len(ledger.entries)

    @pytest.mark.parametrize("word", _FORBIDDEN_ACTION_WORDS)
    def test_action_word_in_value_blocked(self, word):
        ledger = _empty_ledger()
        payload = {
            "planner_status": "PLAN_CREATED", "result": "PASS",
            "plan_id": "plan-001", "note": f"next step calls {word}",
        }
        result = _append(
            ledger, EntryType.PLANNER_RESULT_RECORDED, payload=payload
        )
        self._assert_blocked_unchanged(ledger, result)

    @pytest.mark.parametrize("word", _FORBIDDEN_ACTION_WORDS)
    def test_action_word_in_key_blocked(self, word):
        ledger = _empty_ledger()
        payload = {
            "planner_status": "PLAN_CREATED", "result": "PASS",
            "plan_id": "plan-001", word: True,
        }
        result = _append(
            ledger, EntryType.PLANNER_RESULT_RECORDED, payload=payload
        )
        self._assert_blocked_unchanged(ledger, result)

    @pytest.mark.parametrize("word", _FORBIDDEN_CREDENTIAL_WORDS)
    def test_credential_word_blocked(self, word):
        ledger = _empty_ledger()
        payload = {
            "validation_result": "PASS", "plan_id": "plan-001",
            "context": {"value": f"uses {word} internally"},
        }
        result = _append(
            ledger, EntryType.VALIDATION_RESULT_RECORDED, payload=payload
        )
        self._assert_blocked_unchanged(ledger, result)

    @pytest.mark.parametrize("word", _FORBIDDEN_NETWORK_WORDS)
    def test_network_word_blocked(self, word):
        ledger = _empty_ledger()
        payload = {
            "gate_status": "PASS_DRY_RUN_ONLY", "result": "PASS",
            "plan_id": "plan-001", "target": f"{word}-bound destination",
        }
        result = _append(
            ledger, EntryType.SAFETY_GATE_RESULT_RECORDED, payload=payload
        )
        self._assert_blocked_unchanged(ledger, result)

    def test_forbidden_word_in_nested_list_blocked(self):
        ledger = _empty_ledger()
        payload = {
            "planner_status": "PLAN_CREATED", "result": "PASS",
            "plan_id": "plan-001",
            "steps": ["render", "then " + "live_" + "submit"],
        }
        result = _append(
            ledger, EntryType.PLANNER_RESULT_RECORDED, payload=payload
        )
        self._assert_blocked_unchanged(ledger, result)

    def test_scan_is_case_insensitive(self):
        ledger = _empty_ledger()
        payload = {
            "planner_status": "PLAN_CREATED", "result": "PASS",
            "plan_id": "plan-001", "note": "API_KEY rotation pending",
        }
        result = _append(
            ledger, EntryType.PLANNER_RESULT_RECORDED, payload=payload
        )
        self._assert_blocked_unchanged(ledger, result)

    def test_harmless_payload_passes(self):
        payload = {
            "planner_status": "PLAN_CREATED", "result": "PASS",
            "plan_id": "plan-001", "note": "recorded for audit trail",
        }
        result = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, payload=payload
        )
        assert result.result == "PASS"


# ---------------------------------------------------------------------------
# Blocked append leaves ledger unchanged
# ---------------------------------------------------------------------------

class TestBlockedAppendLeavesLedgerUnchanged:
    def test_blocked_append_returns_old_ledger(self):
        ledger = _append(
            _empty_ledger(), EntryType.PLANNER_RESULT_RECORDED, entry_id="e1"
        ).state
        # Missing required key.
        result = _append(
            ledger, EntryType.VALIDATION_RESULT_RECORDED,
            entry_id="e2", payload={"plan_id": "plan-001"},
        )
        assert result.result == "BLOCKED"
        assert result.state is ledger
        assert len(result.state.entries) == 1
        assert result.state.last_entry_id == "e1"


# ---------------------------------------------------------------------------
# Safety flags always False
# ---------------------------------------------------------------------------

class TestSafetyFlagsAlwaysFalse:
    def _scenarios(self):
        empty = _empty_ledger()
        return [
            ("create_pass", create_empty_audit_ledger(ledger_id=_LEDGER_ID)),
            ("create_blocked", create_empty_audit_ledger(ledger_id="")),
            ("append_pass", _append(empty, EntryType.PLANNER_RESULT_RECORDED)),
            (
                "append_blocked_keys",
                _append(empty, EntryType.PLANNER_RESULT_RECORDED, payload={}),
            ),
            (
                "append_blocked_forbidden",
                _append(
                    empty, EntryType.PLANNER_RESULT_RECORDED,
                    payload={
                        "planner_status": "X", "result": "PASS",
                        "plan_id": "p", "k": "api_key",
                    },
                ),
            ),
        ]

    def test_result_flags_always_false(self):
        for label, result in self._scenarios():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(result, flag) is False, (
                    f"{flag} should be False in scenario {label}"
                )

    def test_state_flags_always_false(self):
        for label, result in self._scenarios():
            if result.state is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(result.state, flag) is False, (
                    f"state {flag} should be False in scenario {label}"
                )


# ---------------------------------------------------------------------------
# Source hygiene: ledger module
# ---------------------------------------------------------------------------

class TestNoForbiddenPatternsInSource:
    """Confirm the ledger module performs no I/O, no persistence, and
    contains no actual broker/network/credential/env/order imports or
    calls. The forbidden payload-scan words inside the module are assembled
    from fragments, so the joined words must never appear contiguously."""

    def _source(self) -> str:
        return inspect.getsource(_ledger_mod)

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
            assert pattern not in source, f"forbidden file I/O found: {pattern!r}"

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
            assert pattern not in source, f"forbidden import found: {pattern!r}"

    def test_no_assembled_order_verbs_in_source(self):
        source = self._source()
        for pattern in (
            "submit_or" + "der",
            "place_or" + "der",
            "cancel_or" + "der",
            "modify_or" + "der",
            "live_su" + "bmit",
        ):
            assert pattern not in source, (
                f"order verb appears contiguously in source: {pattern!r}"
            )

    def test_no_env_or_network_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
            "requ" + "ests.",
            "url" + "lib.",
            "sock" + "et.",
        ):
            assert pattern not in source, f"forbidden call found: {pattern!r}"

    def test_no_runtime_execution_or_research_chain_imports(self):
        # The ledger must not call the planner, validator, gate, or lifecycle.
        source = self._source()
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
            "import main",
            "paper_order_pl" + "anner",
            "paper_order_plan_vali" + "dator",
            "paper_order_safety_g" + "ate",
            "paper_order_life" + "cycle",
            "paper_approval_vali" + "dator",
        ):
            assert pattern not in source, f"forbidden module import: {pattern!r}"


# ---------------------------------------------------------------------------
# Source hygiene: this test module
# ---------------------------------------------------------------------------

class TestThisModuleClean:
    """Every pattern is built from split fragments so the joined pattern
    never appears literally in this module's own source."""

    def _source(self) -> str:
        import sys
        return inspect.getsource(sys.modules[__name__])

    def test_no_write_operations(self):
        source = self._source()
        for pattern in (
            "write_te" + "xt(",
            "write_by" + "tes(",
            "make" + "dirs(",
            "mk" + "dir(",
        ):
            assert pattern not in source, f"module contains write pattern {pattern!r}"

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
        ):
            assert pattern not in source, f"forbidden file I/O found: {pattern!r}"

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
            assert pattern not in source, f"forbidden import found: {pattern!r}"

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
                f"order verb appears contiguously in source: {pattern!r}"
            )

    def test_no_env_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
        ):
            assert pattern not in source, f"forbidden call found: {pattern!r}"

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
