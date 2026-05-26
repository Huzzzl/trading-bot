# Live Position Reconciliation Read-Only Tool — Design

Design document for the read-only position / open-order reconciliation tool.

**Mock-only core implemented in PR `add-live-position-reconciliation-readonly-mock-core`.**
**CLI always returns BLOCKED ("real broker adapter not implemented").**
**PASS is only reachable through an injected mock broker in unit tests.**
**55 unit tests — all mock-only, no real Alpaca calls.**
**Real Alpaca adapter not yet implemented — separate future PR required.**

**No Alpaca SDK is imported.**
**No network requests are made.**
**No credentials are read on any code path.**
**No orders are submitted, sold, cancelled, or replaced.**
**No live ledger is written.**
**No config_safety is mutated.**
**Any position decision remains manual.**

---

## Purpose

After a real live buy returns `result="SUBMITTED"`, the operator needs a
safe, read-only way to confirm:

- Whether a SPY position currently exists in the live account.
- Whether any open SPY orders are present.

This tool provides that capability without exposing fill prices, quantities,
account balances, account IDs, order IDs, or raw broker responses — and
without any ability to mutate positions or orders.

---

## Non-Goals

The following are explicitly **out of scope** for this tool:

| Non-goal | Reason |
|----------|--------|
| Submit, sell, cancel, or replace any order | Read-only tool only |
| Decide whether to hold or sell a position | Manual operator decision only |
| Write a live ledger | Read-only — no ledger mutation |
| Expose fill price, fill quantity, account balance, buying power | Redacted |
| Expose account ID, order ID, or raw broker response | Redacted |
| Mutate `config_safety` or `settings.yaml` | Read-only |
| Replace the post-submit manual position handling runbook | Supplementary tool only |
| Automate position management | Not approved, not implemented |

---

## Proposed Tool

### Module

```
src/tools/live_position_reconciliation_readonly.py
```

### CLI

```sh
python -m src.tools.live_position_reconciliation_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --output output/live_position_reconciliation_readonly.json \
    --allow-live-broker-api-readonly
```

> **Note on shell syntax:** Commands use Unix `\` line continuations.
> On Windows PowerShell replace `\` with `` ` `` or write as a single line.

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--credential-guard` | yes | Path to `live_credential_presence_guard.json` (`result="PASS"`) |
| `--operator-override` | yes | Path to `live_operator_config_override_review.json` (`result="PASS"`) |
| `--symbol` | yes | Symbol to inspect — must be exactly `SPY` |
| `--output` | yes | Path to write `live_position_reconciliation_readonly.json` |
| `--allow-live-broker-api-readonly` | yes (flag) | Required flag — without it, CLI is always BLOCKED |

---

## Required Gates

All of the following must pass before credentials are read or any broker
client is constructed.  Any gate failure must return `result="BLOCKED"` with
`credentials_read=false` and `broker_calls_made=false`.

| Gate | Failure result |
|------|---------------|
| `--credential-guard` artifact present and `result="PASS"` | BLOCKED |
| `--operator-override` artifact present and `result="PASS"` | BLOCKED |
| `--symbol` is exactly `"SPY"` | BLOCKED |
| `--allow-live-broker-api-readonly` flag present | BLOCKED (`"readonly broker api flag not set"`) |
| `ALPACA_LIVE_API_KEY` non-empty in environment | BLOCKED (`"credentials not found in environment"`) |
| `ALPACA_LIVE_SECRET_KEY` non-empty in environment | BLOCKED (`"credentials not found in environment"`) |

Credentials are read **only after** all gates pass and the flag is present —
never at import time or CLI startup.

The broker client is constructed **only after** all gates pass — never before.

---

## Required Output Fields

The output artifact must always be written regardless of `PASS` or `BLOCKED`
outcome.  The tool must never raise — all exceptions must be caught and
converted to `result="BLOCKED"` with a redacted message.

| Field | Type | Notes |
|-------|------|-------|
| `checked_at_utc` | string (ISO-8601) | Timestamp of the check |
| `result` | `"PASS"` or `"BLOCKED"` | PASS = read completed without error; BLOCKED = gate or exception |
| `broker_calls_made` | boolean | `true` if any broker API call was attempted |
| `broker_calls_readonly` | boolean | `true` — all calls are GET-only |
| `broker_mutation_calls_made` | boolean | Always `false` — no mutation endpoints reachable |
| `credential_values_exposed` | boolean | Always `false` — never written to output |
| `credentials_read` | boolean | `true` only if env vars were read (after all gates pass) |
| `live_submit_enabled` | boolean | Always `false` — no submit path in this tool |
| `submit_order_reachable` | boolean | Always `false` |
| `cancel_order_reachable` | boolean | Always `false` |
| `replace_order_reachable` | boolean | Always `false` |
| `symbol` | string | The symbol that was inspected |
| `position_observed` | boolean or null | `true` if a position exists; `false` if not; `null` if check could not complete |
| `open_order_observed` | boolean or null | `true` if an open order exists; `false` if not; `null` if check could not complete |
| `broker_ids_redacted` | boolean | Always `true` — no raw broker IDs in output |
| `account_identifiers_redacted` | boolean | Always `true` — no account IDs in output |
| `raw_broker_response_included` | boolean | Always `false` |
| `violations` | list of strings | Gate failures or exception messages (redacted) |
| `blocker` | string or null | First blocking reason, or null if PASS |

### Example non-sensitive output (PASS)

```json
{
  "checked_at_utc": "2026-05-26T14:30:00Z",
  "result": "PASS",
  "broker_calls_made": true,
  "broker_calls_readonly": true,
  "broker_mutation_calls_made": false,
  "credential_values_exposed": false,
  "credentials_read": true,
  "live_submit_enabled": false,
  "submit_order_reachable": false,
  "cancel_order_reachable": false,
  "replace_order_reachable": false,
  "symbol": "SPY",
  "position_observed": true,
  "open_order_observed": false,
  "broker_ids_redacted": true,
  "account_identifiers_redacted": true,
  "raw_broker_response_included": false,
  "violations": [],
  "blocker": null
}
```

### Example non-sensitive output (BLOCKED — flag absent)

```json
{
  "checked_at_utc": "2026-05-26T14:30:00Z",
  "result": "BLOCKED",
  "broker_calls_made": false,
  "broker_calls_readonly": false,
  "broker_mutation_calls_made": false,
  "credential_values_exposed": false,
  "credentials_read": false,
  "live_submit_enabled": false,
  "submit_order_reachable": false,
  "cancel_order_reachable": false,
  "replace_order_reachable": false,
  "symbol": "SPY",
  "position_observed": null,
  "open_order_observed": null,
  "broker_ids_redacted": true,
  "account_identifiers_redacted": true,
  "raw_broker_response_included": false,
  "violations": ["readonly broker api flag not set"],
  "blocker": "readonly broker api flag not set"
}
```

---

## Implementation Constraints

All of the following must be satisfied by the implementation PR. None may
be softened, skipped, or made configurable.

### Broker adapter

- Read-only broker adapter (e.g. `AlpacaReadOnlyBroker`) may only be
  constructed after all gates pass
- Lazy Alpaca SDK import — inside `__init__` only, never at module level
- Only GET-equivalent broker methods are accessible (`get_all_positions`,
  `get_all_orders` or equivalent read-only calls)
- `submit_order`, `cancel_order`, `replace_order`, and all mutation methods
  must be absent from the adapter source
- No POST, PATCH, or DELETE calls in the adapter

### Output safety

- All broker exception text redacted — raw exception message must not appear
  in output JSON, `violations`, `blocker`, or stdout
- No credential values, account IDs, position IDs, or order IDs in any
  output field, log line, or stdout
- `position_observed` and `open_order_observed` are boolean flags only —
  no quantities, prices, or broker identifiers

### Tool behaviour

- Output artifact always written regardless of PASS or BLOCKED outcome
- Tool never raises — all exceptions caught and converted to BLOCKED
- `config_safety` is not mutated
- `settings.yaml` is not mutated
- No ledger written

---

## Testing Plan

All tests must be mock-only by default. No real Alpaca calls in any test.
No real broker client constructed in tests.

### Source scans (automated)

- [ ] No `submit_order(` in adapter source (non-comment lines)
- [ ] No `cancel_order(` in adapter source (non-comment lines)
- [ ] No `replace_order(` in adapter source (non-comment lines)
- [ ] No POST/PATCH/DELETE endpoint strings in adapter source
- [ ] No module-level Alpaca SDK import in adapter source
- [ ] No credential value printed, logged, or stored in any test-observable path

### Behavioral tests

- [ ] **Happy path PASS**: all gates pass, mock broker returns position and
  order data → `result="PASS"`, `position_observed` set, `broker_calls_made=true`,
  `broker_mutation_calls_made=false`
- [ ] **Flag absent → BLOCKED**: without `--allow-live-broker-api-readonly`,
  `result="BLOCKED"`, `credentials_read=false`, no broker construction
- [ ] **Missing credential guard artifact → BLOCKED**: before credential read
- [ ] **Non-PASS credential guard → BLOCKED**: before credential read
- [ ] **Missing operator override artifact → BLOCKED**: before credential read
- [ ] **Wrong symbol → BLOCKED**: before credential read
- [ ] **Credentials absent from env → BLOCKED**: after flag check, before
  broker construction
- [ ] **Broker exception → BLOCKED**: mock raises → exception text absent from
  all output fields, `result="BLOCKED"`
- [ ] **Broker construction exception → BLOCKED**: lazy import or client
  constructor raises → BLOCKED with redacted message, tool never raises
- [ ] **Output always written**: PASS and all BLOCKED paths each produce output
  artifact
- [ ] **No mutation fields reachable**: assert `submit_order_reachable=false`,
  `cancel_order_reachable=false`, `replace_order_reachable=false` in all paths
- [ ] **No raw IDs in output**: assert position/order output contains no broker
  IDs, account IDs, or raw response fields
- [ ] **Tool never raises**: assert `run_reconciliation()` does not propagate
  exceptions in any scenario

---

## Safety Invariants

| Invariant | Mechanism |
|-----------|-----------|
| No order submission | `submit_order` absent from adapter source |
| No order cancellation | `cancel_order` absent from adapter source |
| No order replacement | `replace_order` absent from adapter source |
| No mutation broker calls | Only read-only broker methods accessible |
| Credentials read only after all gates pass | Gate ordering enforced in `run_reconciliation()` |
| Broker client constructed only after all gates pass | Factory function called only inside gate block |
| Broker exception text redacted | try/except with fixed message |
| No raw IDs or account data in output | Output fields are boolean flags only |
| Tool never raises | Top-level try/except in `run_reconciliation()` |
| Output always written | Write occurs in finally-equivalent block |

---

## Abort Conditions

| Condition | Result |
|-----------|--------|
| Any prerequisite artifact missing or `result != "PASS"` | BLOCKED — no credential read |
| `symbol` not exactly `"SPY"` | BLOCKED |
| `--allow-live-broker-api-readonly` flag absent | BLOCKED |
| Credentials absent from environment | BLOCKED — no broker construction |
| Broker client construction raises | BLOCKED — redacted message |
| Broker read call raises | BLOCKED — redacted message |
| Any credential-like value detected in output | Redact; do not write to output |

---

## Suggested Git Tag

```
live-position-reconciliation-readonly-design-complete
```

---

## References

- `docs/post_submit_manual_position_handling_runbook.md` — post-submit operator runbook (PR #126)
- `docs/first_real_live_submit_success_snapshot.md` — first live submit success snapshot (PR #125)
- `docs/live_readiness_status.md` — full readiness status and milestone history
- `src/tools/live_broker_preflight_readonly.py` — analogous read-only preflight tool (design reference)
- `src/tools/live_single_manual_submit.py` — real adapter implementation (PR #122)

---

## Warning

> **This design document does not approve real trading.**
> **This document does not approve live order submission.**
> **No code is implemented by this PR.**
> **No Alpaca endpoint is contacted by this PR.**
> **No credentials are read by this PR.**
>
> The reconciliation tool described here is read-only only.  It does not
> decide whether to hold or sell any position — that decision remains manual.
> Any future implementation requires fresh design review, mock-only tests
> before any real adapter, and explicit operator action before any live
> broker API call.
> Emergency actions (cancel, close, replace) remain manual via the Alpaca
> broker UI only.
