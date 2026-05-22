# Single Manual Live Submit Attempt — Design Document

Design for a future one-time, manually approved, single live SPY buy attempt
with notional cap ≤ $100.

**This PR does NOT submit an order.**
**This PR does NOT implement live submit.**
**This PR does NOT call Alpaca.**
**This PR does NOT read credentials.**
**This PR does NOT write the live ledger.**
**This PR does NOT enable automated trading.**
**This PR does NOT bypass `config_safety`.**
**Real live submit remains unimplemented after this PR.**
**No live order can be submitted as a result of this design PR.**

---

## Purpose

This document designs the safest possible first live order: a single,
manually approved, market buy of SPY with notional cap ≤ $100, executed
once by a human operator. It does not design recurring trading, automated
trading, or any subsequent order logic.

---

## Explicit Non-Goals

The implementation of this design must never:

| Non-goal | Notes |
|----------|-------|
| Submit more than one order per operator run | One attempt, one order, hard stop |
| Recurring or automated trading | Not designed, not approved |
| Sell, short, options, crypto | SPY buy only |
| Order replacement | Not included |
| Order cancellation | Not included — emergency cancel requires its own future design |
| Bracket, OCO, trailing, or advanced order types | Market order only |
| Retry on failure | No retries; a BLOCKED result is final |
| Expose credential or sensitive account values | Credential values must never appear in output |
| Mutate `settings.yaml` or any shared config | Config overrides are local-only |
| Bypass `config_safety` via code | Only a local operator config override is allowed |
| Write raw broker response to any committed artifact | Broker order ID may be stored in redacted form only |
| Use paper endpoint as substitute for live | Paper does not prove live readiness |
| Run without explicit human approval | Operator approval artifact required at runtime |

---

## Design Scope

| Constraint | Value |
|-----------|-------|
| Occurrences | One per operator approval artifact — not recurring |
| Symbol | `SPY` (hardcoded — not configurable) |
| Side | `buy` (hardcoded — no sell, no short) |
| Order type | `market` |
| Notional cap | `> 0` and `≤ 100.0` USD |
| Advanced order types | None (no bracket, OCO, trailing, stop) |
| Cancel logic | None — not included in this design |
| Retry logic | None — a single attempt; BLOCKED is final |

---

## Required Prerequisites

All of the following must be satisfied before any future implementation may
proceed past the precondition gate. Any missing or non-PASS prerequisite
must produce `result="BLOCKED"` with zero broker mutation calls.

| Prerequisite | Required state |
|--------------|---------------|
| `output/live_credential_presence_guard.json` | `result="PASS"` |
| `output/live_operator_config_override_review.json` | `result="PASS"` |
| `output/live_broker_preflight_readonly.json` | `result="PASS"` and observed recently |
| Operator approval artifact for this exact attempt | Present, valid, signed |
| `config_safety` override | Local operator config only — never `settings.yaml` |
| Human confirmation | Exact symbol, side, notional cap, and account mode confirmed |

The `live_broker_preflight_readonly` PASS must be recent enough that market
conditions and account state can reasonably be assumed unchanged. The
implementation PR must define "recent" concretely (e.g., same trading session).

---

## Operator Approval Artifact Design

A new offline tool (`live_single_submit_approval`) must produce a
machine-readable approval artifact before any submit attempt. The artifact
must include:

```json
{
  "operator_name": "<non-empty>",
  "approval_note": "<non-empty>",
  "symbol": "SPY",
  "side": "buy",
  "notional_cap": "<float in (0, 100]>",
  "approval_scope": "AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE",
  "recurring_trading_approved": false,
  "automated_trading_approved": false,
  "one_attempt_only_acknowledged": true,
  "config_safety_override_is_local_only_acknowledged": true,
  "no_cancel_replace_acknowledged": true,
  "live_broker_preflight_pass_confirmed": true,
  "approved_at_utc": "<ISO-8601 timestamp>"
}
```

All boolean fields must be strict JSON booleans (Python `is True` / `is False`
— string values `"true"`, `"false"`, `"1"` are rejected).

`approval_scope` must be exactly `"AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"`.

This artifact is consumed by the submit tool at runtime. It is not a code
change. It is not committed to the repository.

---

## Submit Flow (Numbered Sequence)

```
 1. Load and validate all prerequisite artifacts
    (credential guard, operator override review, broker preflight, submit approval)
    → BLOCKED immediately if any is missing or non-PASS

 2. Validate operator approval artifact fields
    (scope, symbol, side, notional_cap, all boolean acknowledgements)
    → BLOCKED if any field is wrong or missing

 3. Validate config_safety override is present in local config only
    (live_trading_enabled=true, live_submit_dry_run=false,
     live_kill_switch_enabled=false must be set in local operator config,
     not in settings.yaml)
    → BLOCKED if local override is absent or settings.yaml was mutated

 4. Hard parameter checks
    → BLOCKED if symbol != "SPY"
    → BLOCKED if side != "buy"
    → BLOCKED if notional_cap <= 0 or > 100.0
    → BLOCKED if recurring_trading_approved != false
    → BLOCKED if automated_trading_approved != false

 5. Live account state checks (read-only, reuse AlpacaLiveReadOnlyBroker)
    → BLOCKED if account status != "ACTIVE"
    → BLOCKED if buying_power < notional_cap
    → BLOCKED if SPY tradable=false or fractionable=false
    → BLOCKED if market is closed (GET /v2/clock)
    [optional] → BLOCKED if open SPY order detected (if read-only check added)

 6. Write pre-submit ledger row
    (status="attempting", client_order_id, symbol, side, notional_cap)
    → BLOCKED if ledger write fails

 7. Write pre-submit output artifact
    (result="ATTEMPTING", all invariant fields, no credential values)

 8. Submit exactly one market buy order
    (submit_order called exactly once with SPY / buy / notional / market)
    → On exception: catch, redact broker exception text from all output,
      update ledger row to status="exception", write BLOCKED artifact, exit 1

 9. Update ledger row with outcome
    (status="submitted" or "rejected", broker_order_id redacted if needed)

10. Write final output artifact
    (result="SUBMITTED" or "BLOCKED", all fields, no credential values)
    → Always written regardless of outcome

11. Log non-sensitive summary to stdout
    (result, symbol, side, notional, submitted_at — no account ID, no balance,
     no credential values, broker order ID redacted or omitted)
```

No step may be skipped. Steps 1–6 are all pre-submit guards; `submit_order`
is called only at Step 8 and only once.

---

## Required Future Safety Gates

All of the following must be implemented as hard fails (raise / return BLOCKED
immediately) in the implementation PR. None may be softened, skipped, or
made configurable.

| Gate | Condition that triggers BLOCKED |
|------|--------------------------------|
| Symbol lock | `symbol != "SPY"` |
| Side lock | `side != "buy"` |
| Notional cap lower bound | `notional_cap <= 0` |
| Notional cap upper bound | `notional_cap > 100.0` |
| Recurring trading | `recurring_trading_approved != false` |
| Automated trading | `automated_trading_approved != false` |
| Market closed | `GET /v2/clock` returns `is_open=false` |
| Account inactive | Account status not `"ACTIVE"` |
| Insufficient buying power | `buying_power < notional_cap` |
| SPY not tradable | `GET /v2/assets/SPY` returns `tradable=false` |
| SPY not fractionable | `GET /v2/assets/SPY` returns `fractionable=false` |
| Credential in output | Any output field contains a credential-like value |
| Missing local config override | `config_safety` flags not overridden in local config |
| `settings.yaml` mutated | Any `config_safety` flag changed in shared config |
| Prerequisite artifact absent or non-PASS | Any of the four required artifacts missing or BLOCKED |
| Operator approval scope wrong | `approval_scope` is not exactly `"AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"` |
| Pre-submit ledger write fails | Cannot append pre-submit row — do not proceed to submit |

---

## Required Future Output Artifact

Path: `output/single_manual_live_submit_attempt.json`

Always written, regardless of SUBMITTED or BLOCKED outcome.

### Required fields

```json
{
  "result": "SUBMITTED | BLOCKED",
  "order_submitted": false,
  "broker_mutation_calls_made": false,
  "submit_order_called": false,
  "cancel_order_called": false,
  "replace_order_called": false,
  "live_submit_enabled": false,
  "automated_trading_enabled": false,
  "recurring_trading_enabled": false,
  "config_safety_overridden_by_local_operator_config": false,
  "symbol": "SPY",
  "side": "buy",
  "notional_cap": null,
  "client_order_id": null,
  "submitted_at_utc": null,
  "broker_order_id_redacted": null,
  "credential_values_exposed": false,
  "violations": [],
  "blocker": null
}
```

### Field invariants (hardcoded regardless of outcome)

| Field | Invariant value |
|-------|----------------|
| `cancel_order_called` | `false` always |
| `replace_order_called` | `false` always |
| `automated_trading_enabled` | `false` always |
| `recurring_trading_enabled` | `false` always |
| `credential_values_exposed` | `false` always |

On SUBMITTED: `order_submitted=true`, `broker_mutation_calls_made=true`,
`submit_order_called=true`, `submitted_at_utc` set, `broker_order_id_redacted`
set to a non-sensitive reference (not the raw broker string if it contains
account-identifying information).

On BLOCKED: `order_submitted=false`, `submit_order_called=false`,
`violations` and `blocker` populated, `submitted_at_utc=null`.

---

## Ledger Design

The live ledger write must follow the existing schema in
`src/execution/live_ledger.py`.

### Pre-submit row (Step 6)

Written before `submit_order` is called:

| Column | Value |
|--------|-------|
| `status` | `"attempting"` |
| `client_order_id` | Non-empty, unique |
| `symbol` | `"SPY"` |
| `side` | `"buy"` |
| `notional_cap` | The approved cap value |
| `broker_order_id` | `""` (empty — not yet known) |
| `submitted_at` | `""` (empty — not yet submitted) |
| `error` | `""` |

### Post-submit update (Step 9)

Overwrites the `attempting` row in place (same `client_order_id`):

| Outcome | `status` | `broker_order_id` | `error` |
|---------|----------|-------------------|---------|
| Order accepted | `"submitted"` | Redacted reference or `"<redacted>"` | `""` |
| Order rejected by broker | `"rejected"` | `""` | Redacted broker message |
| Exception during submit | `"exception"` | `""` | `"details redacted"` |

Ledger writes are append-only for the pre-submit row. The post-submit update
modifies only the row with the matching `client_order_id` and `status="attempting"`.
All other rows are preserved exactly.

The ledger must pass `live_ledger_verify` (without `--allow-attempting`) after
the post-submit update completes.

---

## Abort Conditions

Abort at any point before Step 8 if any safety gate fails. After Step 8, if
an exception occurs, catch it, redact exception text, update ledger to
`status="exception"`, write BLOCKED artifact, and exit 1. Never retry.

Abort immediately (before any broker contact) if:
- Any prerequisite artifact is missing or has `result != "PASS"`
- Operator approval artifact is missing, malformed, or has wrong scope
- Symbol, side, or notional cap is outside allowed values
- `recurring_trading_approved` or `automated_trading_approved` is not `false`
- `config_safety` is not overridden in a local operator config
- `settings.yaml` shows any `config_safety` flag was changed

A BLOCKED result is final. Do not retry, do not adjust parameters to work
around the block.

---

## Manual Operator Checklist

The human operator must verify each item before running the submit tool.
This checklist supplements — it does not replace — the automated safety gates.

- [ ] `live_credential_presence_guard` → `result="PASS"` (run same session)
- [ ] `live_operator_config_override_review` → `result="PASS"` (run same session)
- [ ] `live_broker_preflight_readonly` → `result="PASS"` (run same session)
- [ ] Operator approval artifact created for this exact attempt (symbol=SPY, side=buy, notional_cap confirmed)
- [ ] Local operator config has `live_trading_enabled=true`, `live_submit_dry_run=false`, `live_kill_switch_enabled=false`
- [ ] `settings.yaml` is unchanged — no `config_safety` flag modified
- [ ] Account is funded with buying power ≥ notional_cap
- [ ] Market is open (regular hours)
- [ ] No existing open SPY position or open SPY order (verify in broker UI)
- [ ] Understood: this is one attempt only — no retry if BLOCKED
- [ ] Understood: no cancel or replace logic exists in this implementation
- [ ] Understood: SUBMITTED is not a profit guarantee — it means the order reached the broker
- [ ] Understood: `config_safety` flags must be reset to safe defaults after the attempt

---

## Rollback / Non-Action Plan

If the tool produces `result="BLOCKED"` at any gate:

1. Do not retry with modified parameters.
2. Read `violations` and `blocker` in the output artifact.
3. Resolve the underlying condition (e.g., fund account, wait for market open).
4. Re-run the full prerequisite chain from `live_credential_presence_guard`
   before attempting again.
5. Reset `config_safety` flags to safe defaults in local operator config.

If the tool produces `result="SUBMITTED"` but the order status is unknown
(e.g., no poll result available): check the Alpaca dashboard directly.
Do not re-run the tool — a second run would require a new operator approval
artifact and would represent a second separate attempt.

After any run (SUBMITTED or BLOCKED), reset `config_safety` flags:

```bash
# In local operator config — reset to safe defaults
live_trading_enabled: false
live_submit_dry_run: true
live_kill_switch_enabled: true
```

---

## Integration With Existing Pipeline

The single submit attempt sits at the end of the existing guard chain:

```
live_credential_presence_guard          (PASS required)
live_operator_config_override_review    (PASS required)
live_broker_preflight_readonly          (PASS required, recent)
live_single_submit_approval             (operator approval artifact)
        ↓
[local config_safety override applied]
        ↓
live_single_manual_submit               (future — this design)
        ↓
[reset config_safety flags to safe defaults]
```

`config_safety` is overridden only in a local operator config for the
duration of the attempt and must be reset immediately after.

---

## Testing Plan

All unit tests must use a mock broker. No real Alpaca calls in any test.

| Test area | What to verify |
|-----------|---------------|
| Wrong symbol → BLOCKED | `symbol="AAPL"` → BLOCKED before broker call |
| Wrong side → BLOCKED | `side="sell"` → BLOCKED before broker call |
| Notional cap = 0 → BLOCKED | `notional_cap=0` → BLOCKED |
| Notional cap > 100 → BLOCKED | `notional_cap=200` → BLOCKED |
| Missing credential guard → BLOCKED | Prerequisite absent → BLOCKED |
| Non-PASS credential guard → BLOCKED | `result="BLOCKED"` in artifact → BLOCKED |
| Missing operator override → BLOCKED | Prerequisite absent → BLOCKED |
| Missing broker preflight → BLOCKED | Prerequisite absent → BLOCKED |
| Missing approval artifact → BLOCKED | Approval not provided → BLOCKED |
| Wrong approval scope → BLOCKED | Scope string differs → BLOCKED |
| `recurring_trading_approved=true` → BLOCKED | Hard fail |
| `automated_trading_approved=true` → BLOCKED | Hard fail |
| Missing local config override → BLOCKED | `config_safety` not overridden → BLOCKED |
| `settings.yaml` mutated → BLOCKED | Shared config modified → BLOCKED |
| Market closed → BLOCKED | Mock clock `is_open=false` → BLOCKED |
| Account inactive → BLOCKED | Mock `status="INACTIVE"` → BLOCKED |
| Insufficient buying power → BLOCKED | Mock `buying_power=0` → BLOCKED |
| SPY not tradable → BLOCKED | Mock `tradable=false` → BLOCKED |
| SPY not fractionable → BLOCKED | Mock `fractionable=false` → BLOCKED |
| Happy path — SUBMITTED | All mocks pass → exactly one `submit_order` call |
| No cancel_order call | Assert `cancel_order` never called in any test path |
| No replace_order call | Assert `replace_order` never called in any test path |
| No retry | Assert `submit_order` called at most once per run |
| No credential in output | Inject mock credential → assert absent from output JSON |
| No credential in stdout | Same injection → assert absent from captured stdout |
| Output artifact always written | Assert output JSON exists on BLOCKED and SUBMITTED |
| `cancel_order_called=false` always | Assert field invariant in all output |
| `replace_order_called=false` always | Assert field invariant in all output |
| `automated_trading_enabled=false` always | Assert field invariant in all output |
| `recurring_trading_enabled=false` always | Assert field invariant in all output |
| `credential_values_exposed=false` always | Assert field invariant in all output |
| Broker exception → BLOCKED | Mock raises exception → BLOCKED, text redacted |
| Pre-submit ledger row written before submit | Assert ledger row exists before mock submit |
| Post-submit ledger row updated | Assert row updated after mock submit |
| `settings.yaml` not mutated | Assert file unchanged after any run |

---

## Current Implementation Status

| Item | State |
|------|-------|
| This design document | Complete — this file |
| `src/tools/live_single_submit_approval_review.py` | **Complete** — offline read-only approval review |
| `tests/test_live_single_submit_approval_review.py` | **Complete** — 171 tests, all pass |
| `src/tools/live_single_manual_submit.py` | **Not implemented** |
| `tests/test_live_single_manual_submit.py` | **Not implemented** |
| Real live submit | **Not implemented** |
| Automated live trading | **Not implemented** |
| `submit_order` for live | Absent — no call path exists |
| `config_safety` | Still the hard blocker |

### `live_single_submit_approval_review` — what it does

Offline, read-only validation of the operator approval artifact for exactly
one future single live SPY market buy attempt.

| Property | Value |
|----------|-------|
| Calls Alpaca | No |
| Imports Alpaca SDK | No |
| Reads credentials | No |
| Calls `submit_order` / `cancel_order` / `replace_order` | No |
| Writes live ledger | No |
| Removes `config_safety` on PASS | No |
| Approves real trading on PASS | No |
| Approves automated/recurring trading on PASS | No |

PASS means the artifact is present, structurally valid, correctly scoped
(`approval_scope="AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"`), not expired,
and contains all required explicit acknowledgements.

PASS does not submit an order and does not enable live trading.

**No live order can be submitted as a result of this PR.**

---

## Warning

> **This design PR does not approve real trading.**
> **This design PR does not approve live order submission.**
> **This design PR does not implement any part of the submit flow.**
> Real live submit remains unimplemented after this PR.
> `config_safety` remains the hard blocker.
> `submit_order`, `cancel_order`, and `replace_order` remain absent for live.
> The implementation of this design requires its own dedicated PR, its own
> full test suite (mock broker only), and explicit operator approval at
> runtime — none of which are provided here.

---

## References

- [docs/live_broker_preflight_design.md](live_broker_preflight_design.md) — read-only preflight design and implementation
- [docs/live_readonly_preflight_runbook.md](live_readonly_preflight_runbook.md) — preflight operator runbook
- [docs/live_readonly_preflight_result_snapshot.md](live_readonly_preflight_result_snapshot.md) — preflight PASS snapshot
- [docs/live_readiness_status.md](live_readiness_status.md) — full readiness status and milestone history
- [docs/live_submit_enablement_gate.md](live_submit_enablement_gate.md) — enablement gate conditions
- [docs/live_submit_design.md](live_submit_design.md) — original proposed submit flow
- [docs/live_submit_enablement_v2.md](live_submit_enablement_v2.md) — v2 approval layer design
