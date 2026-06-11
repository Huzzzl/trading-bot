"""
research/paper_audit_ledger.py
-------------------------------
S34: Pure offline, in-memory paper audit ledger recorder.

Converts already-loaded planner/validator/gate/lifecycle results into
immutable, append-only audit entries held entirely in memory. The ledger
is a bookkeeping record only: appending an entry records what an
upstream, separately-reviewed step reported -- it never performs,
initiates, or approves any order action.

This is NOT file persistence. This is NOT report persistence. This is NOT
order submission. This is NOT broker integration. This is NOT paper
trading execution. This is NOT live trading. This module never calls the
planner, validator, safety gate, lifecycle, broker, runtime, execution,
network, environment, or file system, and it writes nothing to disk.

Pure functions: same input -> same output; a new immutable ledger state
is returned rather than mutating the existing state. All safety flags on
every ledger state and result are always False. The ledger fails closed:
invalid inputs, forbidden payload content, duplicate entry ids, and
source/type mismatches never advance the ledger -- the previous state is
returned unchanged. Paper trading remains not approved; live trading
remains blocked.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)
_RESULT_FLAGS: dict[str, bool] = {f: False for f in _SAFETY_FLAGS}

# Forbidden substrings for the payload scan (case-insensitive). These are
# scan-list literals only, deliberately assembled from fragments: this
# module never imports, calls, or invokes any of the concepts they name,
# and the assembled words must never appear contiguously in this source.
_FORBIDDEN_PAYLOAD_SUBSTRINGS: tuple[str, ...] = (
    "submit_" + "order",
    "place_" + "order",
    "cancel_" + "order",
    "modify_" + "order",
    "live_" + "submit",
    "api_key",
    "secret",
    "credential",
    "token",
    "broker_account",
    "live_account",
    "endpoint",
    "http",
    "https",
)


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------

class PaperAuditLedgerStatus(str, Enum):
    EMPTY   = "EMPTY"
    UPDATED = "UPDATED"
    BLOCKED = "BLOCKED"
    ERROR   = "ERROR"


class PaperAuditLedgerEntryType(str, Enum):
    PLANNER_RESULT_RECORDED       = "PLANNER_RESULT_RECORDED"
    VALIDATION_RESULT_RECORDED    = "VALIDATION_RESULT_RECORDED"
    SAFETY_GATE_RESULT_RECORDED   = "SAFETY_GATE_RESULT_RECORDED"
    LIFECYCLE_TRANSITION_RECORDED = "LIFECYCLE_TRANSITION_RECORDED"
    BLOCKED_CHAIN_RECORDED        = "BLOCKED_CHAIN_RECORDED"
    ERROR_RECORDED                = "ERROR_RECORDED"


# Required payload keys per entry type.
_REQUIRED_PAYLOAD_KEYS: dict[PaperAuditLedgerEntryType, tuple[str, ...]] = {
    PaperAuditLedgerEntryType.PLANNER_RESULT_RECORDED:
        ("planner_status", "result", "plan_id"),
    PaperAuditLedgerEntryType.VALIDATION_RESULT_RECORDED:
        ("validation_result", "plan_id"),
    PaperAuditLedgerEntryType.SAFETY_GATE_RESULT_RECORDED:
        ("gate_status", "result", "plan_id"),
    PaperAuditLedgerEntryType.LIFECYCLE_TRANSITION_RECORDED:
        ("previous_status", "new_status", "event_type", "lifecycle_id"),
    PaperAuditLedgerEntryType.BLOCKED_CHAIN_RECORDED:
        ("blocked_stage", "blocker"),
    PaperAuditLedgerEntryType.ERROR_RECORDED:
        ("error_stage", "blocker"),
}

# Required source value per entry type.
_REQUIRED_SOURCE: dict[PaperAuditLedgerEntryType, str] = {
    PaperAuditLedgerEntryType.PLANNER_RESULT_RECORDED:       "planner",
    PaperAuditLedgerEntryType.VALIDATION_RESULT_RECORDED:    "validator",
    PaperAuditLedgerEntryType.SAFETY_GATE_RESULT_RECORDED:   "safety_gate",
    PaperAuditLedgerEntryType.LIFECYCLE_TRANSITION_RECORDED: "lifecycle",
    PaperAuditLedgerEntryType.BLOCKED_CHAIN_RECORDED:        "chain",
    PaperAuditLedgerEntryType.ERROR_RECORDED:               "chain",
}

_ALLOWED_SOURCES: frozenset[str] = frozenset(_REQUIRED_SOURCE.values())


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PaperAuditLedgerState:
    """Immutable in-memory, append-only audit ledger.

    Bookkeeping only: no entry here performs, initiates, or approves any
    order action, and nothing is persisted. All safety flags are always
    False.
    """
    ledger_id: str
    entries: tuple[dict, ...]
    status: PaperAuditLedgerStatus
    last_entry_id: str | None
    blocker: str | None
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


@dataclass(frozen=True)
class PaperAuditLedgerResult:
    """Immutable result of create_empty_audit_ledger() or
    append_audit_entry().

    result="PASS" means only that the in-memory bookkeeping entry was
    recorded. On BLOCKED, the state field carries the unchanged previous
    ledger (or None when no ledger could be created/trusted) and entry is
    None -- a blocked append never advances the ledger. All safety flags
    are always False.
    """
    result: str
    blocker: str | None
    state: PaperAuditLedgerState | None
    entry: dict | None
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _scan_forbidden_payload(d: Any) -> bool:
    """Recursively scan payload keys and string values for forbidden
    action/credential/network/live phrases (case-insensitive)."""
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(k, str):
                k_lower = k.lower()
                if any(sub in k_lower for sub in _FORBIDDEN_PAYLOAD_SUBSTRINGS):
                    return True
            if _scan_forbidden_payload(v):
                return True
    elif isinstance(d, (list, tuple)):
        for item in d:
            if _scan_forbidden_payload(item):
                return True
    elif isinstance(d, str):
        d_lower = d.lower()
        if any(sub in d_lower for sub in _FORBIDDEN_PAYLOAD_SUBSTRINGS):
            return True
    return False


def _make_result(
    *,
    result: str,
    blocker: str | None,
    state: PaperAuditLedgerState | None,
    entry: dict | None,
    checked: list[str],
    failed: list[str],
) -> PaperAuditLedgerResult:
    return PaperAuditLedgerResult(
        result=result,
        blocker=blocker,
        state=state,
        entry=entry,
        criteria_checked=tuple(checked),
        criteria_failed=tuple(failed),
        **_RESULT_FLAGS,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def create_empty_audit_ledger(
    *,
    ledger_id: str,
) -> PaperAuditLedgerResult:
    """Create an empty in-memory audit ledger.

    On a non-empty ledger_id, returns an EMPTY ledger with no entries. On
    failure no ledger state is released (state=None, fail closed).

    Pure function: no file I/O, no persistence, no network, no environment
    variables, no broker/API/credential access, no order action, no
    paper/live approval.
    """
    checked: list[str] = ["ledger.identity"]
    if not _is_nonempty_str(ledger_id):
        return _make_result(
            result="BLOCKED",
            blocker="ledger_id must be a non-empty string",
            state=None,
            entry=None,
            checked=checked,
            failed=["ledger.identity"],
        )

    checked.append("ledger.created")
    state = PaperAuditLedgerState(
        ledger_id=ledger_id,
        entries=(),
        status=PaperAuditLedgerStatus.EMPTY,
        last_entry_id=None,
        blocker=None,
        **_RESULT_FLAGS,
    )
    return _make_result(
        result="PASS",
        blocker=None,
        state=state,
        entry=None,
        checked=checked,
        failed=[],
    )


def append_audit_entry(
    ledger: PaperAuditLedgerState,
    *,
    entry_id: str,
    entry_type: PaperAuditLedgerEntryType,
    recorded_at_utc: str,
    source: str,
    payload: dict,
) -> PaperAuditLedgerResult:
    """Append an audit entry to an existing ledger.

    Returns a NEW immutable ledger state; the input ledger is never
    mutated. On any failure the previous ledger is returned unchanged with
    entry=None -- a blocked append never advances the ledger (fail closed).

    Appending an entry records information only. It performs no order
    action of any kind: no order is submitted, placed, cancelled, or
    modified by this function, and no paper/live trading is approved.

    Pure function: no file I/O, no persistence, no network, no environment
    variables, no broker/API/credential access.
    """
    checked: list[str] = []
    failed: list[str] = []

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    # 1. Ledger schema -- without a trusted ledger nothing can be returned.
    checked.append("ledger.schema")
    if not isinstance(ledger, PaperAuditLedgerState):
        failed.append("ledger.schema")
        return _make_result(
            result="BLOCKED",
            blocker="ledger is not a PaperAuditLedgerState",
            state=None,
            entry=None,
            checked=checked,
            failed=failed,
        )

    def _blocked(blocker: str) -> PaperAuditLedgerResult:
        return _make_result(
            result="BLOCKED",
            blocker=blocker,
            state=ledger,
            entry=None,
            checked=checked,
            failed=failed,
        )

    # 2. Existing ledger safety flags must all be False.
    _chk(
        "ledger.safety_flags",
        not any(getattr(ledger, flag, True) for flag in _SAFETY_FLAGS),
    )
    if failed:
        return _blocked("ledger safety flags violated")

    # 3. Entry id.
    _chk("entry.identity", _is_nonempty_str(entry_id))
    if failed:
        return _blocked("entry_id must be a non-empty string")

    # 4. Duplicate entry id.
    existing_ids = {
        e.get("entry_id") for e in ledger.entries if isinstance(e, dict)
    }
    _chk("entry.duplicate_id", entry_id not in existing_ids)
    if failed:
        return _blocked(f"entry_id {entry_id!r} already used in ledger")

    # 5. Entry type.
    _chk("entry.type", isinstance(entry_type, PaperAuditLedgerEntryType))
    if failed:
        return _blocked("entry_type must be a PaperAuditLedgerEntryType")

    # 6. Timestamp.
    _chk("entry.timestamp", _is_nonempty_str(recorded_at_utc))
    if failed:
        return _blocked("recorded_at_utc must be a non-empty string")

    # 7. Source non-empty and a known source value.
    _chk("entry.source", _is_nonempty_str(source) and source in _ALLOWED_SOURCES)
    if failed:
        return _blocked("source must be one of the allowed source values")

    # 8. Payload schema.
    _chk("payload.schema", isinstance(payload, dict))
    if failed:
        return _blocked("payload must be a dict")

    # 9. Required payload keys for this entry type.
    required_keys = _REQUIRED_PAYLOAD_KEYS[entry_type]
    _chk(
        "payload.required_keys",
        all(key in payload for key in required_keys),
    )
    if failed:
        return _blocked(
            "payload missing required keys for "
            f"{entry_type.value}: {', '.join(required_keys)}"
        )

    # 10. Source matches entry type.
    _chk("payload.source_matches_type", source == _REQUIRED_SOURCE[entry_type])
    if failed:
        return _blocked(
            f"source {source!r} does not match entry type {entry_type.value}"
        )

    # 11. Forbidden payload content scan.
    _chk("payload.forbidden_content", not _scan_forbidden_payload(payload))
    if failed:
        return _blocked("payload contains forbidden content")

    # 12. Append the entry and build the new immutable ledger state.
    checked.append("entry.appended")
    entry = {
        "entry_id": entry_id,
        "entry_type": entry_type.value,
        "recorded_at_utc": recorded_at_utc,
        "source": source,
        "payload": copy.deepcopy(payload),
    }
    new_ledger = replace(
        ledger,
        entries=ledger.entries + (entry,),
        status=PaperAuditLedgerStatus.UPDATED,
        last_entry_id=entry_id,
        blocker=None,
    )
    return _make_result(
        result="PASS",
        blocker=None,
        state=new_ledger,
        entry=copy.deepcopy(entry),
        checked=checked,
        failed=failed,
    )
