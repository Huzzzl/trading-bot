# Manual Position Status Checker Read-Only — Mock-Only Core Complete

Snapshot document for the completion of the mock-only core for
`manual_position_status_checker_readonly`. Implemented in PR #135.
Real Alpaca adapter requires a future PR with `--allow-live-broker-api-readonly`.

**This document does NOT trade.**
**This document does NOT submit, sell, cancel, replace, or close positions.**
**This document does NOT contact Alpaca.**
**This document does NOT read credentials.**
**This document does NOT implement the real broker adapter.**
**This document does NOT implement automated monitoring.**
**This document does NOT implement recurring jobs, alerts, stop-loss,**
**take-profit, trailing stop, sell adapter, or close adapter.**
**This document does NOT approve holding or selling any position.**
**All position decisions remain manual operator decisions.**

---

## What Was Implemented (PR #135)

### Source files

| File | Status |
|------|--------|
| `src/tools/manual_position_status_checker_readonly.py` | Complete — mock-only core |
| `tests/test_manual_position_status_checker_readonly.py` | Complete — 88 tests |

### Core behavior

`run_status_check()` implements the full gate sequence with mock-only broker injection:

| Gate | Blocker on failure |
|------|--------------------|
| `credential_guard` artifact present and `result="PASS"` | BLOCKED, `broker_calls_made=false` |
| `operator_override` artifact present and `result="PASS"` | BLOCKED, `broker_calls_made=false` |
| `symbol` exactly `"SPY"` | BLOCKED, invalid symbol not echoed |
| `broker` injected (real adapter not implemented) | BLOCKED, `"real broker adapter not implemented"` |
| Broker read-only calls succeed | BLOCKED on exception, details redacted |

CLI always returns `result="BLOCKED"` with `blocker="real broker adapter not implemented"`.
PASS is reachable only through an injected mock broker in unit tests.

---

## New Output Fields vs. `live_position_reconciliation_readonly`

| Field | Value | Notes |
|-------|-------|-------|
| `close_position_reachable` | `false` always | New field — no close path |
| `position_decision_made` | `false` always | New field — no hold/sell recommendation |
| `market_session_status` | allowlisted `str \| null` | New field — allowlisted only |

### `market_session_status` allowlist

Allowed values: `"open"`, `"closed"`, `"pre_market"`, `"after_hours"`, `null`.

Any other broker return value (including secret strings, whitespace variants, wrong-case
strings, or unexpected values) → BLOCKED with `violation="market session status invalid"`.
The raw invalid value is never echoed in output JSON, `violations`, `blocker`, or stdout.

---

## Hardcoded Output Invariants (Every Result, PASS or BLOCKED)

| Field | Required value |
|-------|---------------|
| `broker_mutation_calls_made` | `false` |
| `credentials_read` | `false` (mock-only core — no env reads) |
| `credential_values_exposed` | `false` |
| `live_submit_enabled` | `false` |
| `submit_order_reachable` | `false` |
| `cancel_order_reachable` | `false` |
| `replace_order_reachable` | `false` |
| `close_position_reachable` | `false` |
| `broker_ids_redacted` | `true` |
| `account_identifiers_redacted` | `true` |
| `raw_broker_response_included` | `false` |
| `position_decision_made` | `false` |
| `broker_calls_readonly` | mirrors `broker_calls_made` |

---

## Test Coverage

### Summary

| Metric | Value |
|--------|-------|
| Targeted tests | 88 passed |
| Full suite | 4025 passed |
| Real Alpaca calls | None — all tests use injected mock brokers |

### Test classes

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestArtifactGates` | 7 | Missing/non-PASS/malformed cg and oo → BLOCKED, no broker call |
| `TestSymbolValidation` | 4 | Wrong/lowercase/whitespace/empty symbol → BLOCKED |
| `TestInputSecretRedaction` | 9 | Secrets in cg result, oo result, symbol → absent from output, violations, blocker, stdout |
| `TestBrokerNone` | 7 | CLI broker=None → BLOCKED, correct blocker message, all null presence fields |
| `TestHappyPath` | 8 | Injected mock broker → PASS; position/order/session flags; `broker_calls_readonly=true` |
| `TestMarketSessionStatus` | 6 | open/closed/pre_market/after_hours/null/no-method → `market_session_status` correct |
| `TestMarketSessionStatusRedaction` | 13 | Invalid/secret/whitespace/case variants → BLOCKED; raw value absent from output JSON, violations, blocker, stdout |
| `TestBrokerException` | 7 | position/orders/session raises with secret → BLOCKED, secret absent from output and stdout |
| `TestOutputInvariants` | 5 | All hardcoded safety fields checked on gate failure, broker None, and PASS paths |
| `TestOutputAlwaysWritten` | 3 | Output artifact written for all BLOCKED paths via CLI; exit 1 confirmed |
| `TestNeverRaises` | 4 | `run_status_check()` never raises on any exception path |
| `TestNoRawIds` | 2 | Position/order broker dict values not leaked into output JSON |
| `TestSourceScans` | 14 | No alpaca/network imports, no `os.environ.get`/`os.environ[`, no `submit_order(`/`cancel_order(`/`replace_order(`/`close_position(`/`close_all_positions(`, no POST/PATCH/DELETE markers |

---

## Safety Invariants Confirmed

| Invariant | Method | Result |
|-----------|--------|--------|
| No Alpaca SDK imported | Source scan (`TestSourceScans`) | Confirmed absent |
| No network library imports | Source scan (`TestSourceScans`) | Confirmed absent |
| No `os.environ.get(` or `os.environ[` | Source scan (`TestSourceScans`) | Confirmed absent |
| No `submit_order(` in source | Source scan (`TestSourceScans`) | Confirmed absent |
| No `cancel_order(` in source | Source scan (`TestSourceScans`) | Confirmed absent |
| No `replace_order(` in source | Source scan (`TestSourceScans`) | Confirmed absent |
| No `close_position(` in source | Source scan (`TestSourceScans`) | Confirmed absent |
| No `close_all_positions(` in source | Source scan (`TestSourceScans`) | Confirmed absent |
| No POST/PATCH/DELETE markers | Source scan (`TestSourceScans`) | Confirmed absent |
| CLI always BLOCKED | `TestBrokerNone`, `TestOutputAlwaysWritten` | `SystemExit(1)` confirmed |
| Broker exception text redacted | `TestBrokerException` | Secret absent from all output |
| `market_session_status` allowlist enforced | `TestMarketSessionStatusRedaction` (13) | Raw invalid value absent from output, violations, blocker, stdout |
| Raw invalid input values not echoed | `TestInputSecretRedaction` (9) | Secret absent from output, violations, blocker, stdout |
| `broker_calls_readonly` mirrors `broker_calls_made` | `TestOutputInvariants` | Confirmed |
| `run_status_check()` never raises | `TestNeverRaises` | Confirmed |
| Output always written | `TestOutputAlwaysWritten` | Confirmed for all BLOCKED paths |

---

## What Remains

The real broker adapter is **NOT implemented**.
A future PR must add:

| Item | Status |
|------|--------|
| `--allow-live-broker-api-readonly` CLI flag | Not implemented — required |
| Credential read only after all gates pass and flag present | Not implemented |
| `TradingClient(paper=False)` constructed only after credentials confirmed | Not implemented |
| `get_position` / `get_open_orders` via real `AlpacaLivePositionBroker` | Not implemented |
| `get_market_session_status` via real clock endpoint | Not implemented |
| Real adapter tests (mock `TradingClient`, no real Alpaca calls) | Not implemented |

Without the flag, the CLI always returns BLOCKED with zero broker calls and
zero credential reads — regardless of whether artifacts are present.

---

## CLI (Future — Currently Always BLOCKED)

```bash
python -m src.tools.manual_position_status_checker_readonly \
    --credential-guard  output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol            SPY \
    --output            output/manual_position_status_checker_readonly.json
```

Currently exits 1 with `result="BLOCKED"`, `blocker="real broker adapter not implemented"`.
Always writes output JSON. Never contacts Alpaca.

---

## Suggested Git Tag

```
manual-position-status-checker-readonly-mock-core-complete
```

---

## Warnings

> **PASS from this tool means only that the status check completed without error.**
> It does not recommend holding or selling.

> **`position_observed=true` does not mean hold.**
> It is a boolean presence flag only.
> The decision to hold is entirely the operator's.

> **`position_observed=false` does not mean sell.**
> It may reflect a filled close, a data delay, or a different symbol.
> The decision to act on an absent position is entirely the operator's.

> **`open_order_observed=true` requires manual Alpaca UI review.**
> The tool does not cancel, replace, or act on open orders.
> The operator must review open orders directly in the Alpaca broker UI.

> **All position decisions remain entirely manual.**
> No tool in this repository decides whether to hold, sell, close, or
> act on any position. These are financial decisions by the operator.

> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility.

> **The real broker adapter is not implemented.**
> A future PR is required before any live API contact can be made.
> That PR must use mock-only tests, require the explicit operator flag,
> and read credentials only after all gates pass.
