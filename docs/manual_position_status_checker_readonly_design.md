# Manual Position Status Checker — Read-Only Design

Design document and implementation status for the manual read-only position
status checker. Mock-only core implemented in PR #135.
Real Alpaca adapter requires a future PR with `--allow-live-broker-api-readonly`.

**This document does NOT trade.**
**This document does NOT submit, sell, cancel, replace, or close positions.**
**This document does NOT contact Alpaca.**
**This document does NOT read credentials.**
**This document does NOT implement the checker.**
**This document does NOT implement automated monitoring.**
**This document does NOT implement recurring jobs, alerts, stop-loss,**
**take-profit, trailing stop, sell adapter, or close adapter.**
**This document does NOT approve holding or selling the observed position.**
**The current SPY position remains a manual operator decision.**

---

## 1. Purpose

The operator may need to re-check SPY position and open-order presence after
the initial PASS from `live_position_reconciliation_readonly` — for example,
before making a manual hold/sell decision, or after an unexpected event in
the Alpaca UI.

This checker provides a minimal, safe, read-only interface for that purpose:

- Returns boolean presence flags only — no size, price, quantity, or PnL
- Requires explicit operator action every run — no scheduling or automation
- Makes GET-only broker calls — no mutation
- Redacts all sensitive data from output — no IDs, no raw responses
- Never makes a position recommendation — hold/sell remains entirely manual

---

## 2. Design Scope

### What the checker checks

| Check | Output field | Type |
|-------|-------------|------|
| SPY position present in live account | `position_observed` | `bool \| null` |
| Open SPY orders present in live account | `open_order_observed` | `bool \| null` |
| Market session state (optional) | `market_session_status` | `str \| null` |

`null` means the check was not reached (BLOCKED before broker call).

### What the checker does NOT collect

The following are explicitly out of scope and must never appear in any
output field, violation message, stdout line, or committed artifact:

| Excluded field | Reason |
|----------------|--------|
| Position size or quantity | Not needed for presence check |
| Fill price or current market price | Not needed for presence check |
| Cost basis or unrealized PnL | Not needed for presence check |
| Account ID or account number | Sensitive identifier |
| Raw broker order IDs or position IDs | Sensitive identifier |
| Buying power or account balance | Sensitive financial data |
| Raw broker API response body | May contain sensitive data |
| Any broker exception message | Must be redacted |

### What the checker does NOT do

| Action | Status |
|--------|--------|
| Submit any order | Absent — no submit method |
| Cancel any order | Absent — no cancel method |
| Replace any order | Absent — no replace method |
| Close any position | Absent — no close_position method |
| Write to live ledger | Absent |
| Mutate config | Absent |
| Make POST/PATCH/DELETE broker calls | Absent |
| Make position recommendation | Absent |
| Trigger automated sell or hold | Absent |
| Run on a schedule | Absent — manual operator invocation only |
| Retry on failure | Absent — BLOCKED is final |

---

## 3. Relationship to Existing Tool

### Preferred approach: reuse or wrap `live_position_reconciliation_readonly`

`live_position_reconciliation_readonly` already implements:

- The exact gate sequence required (credential guard → operator override →
  symbol check → flag check → credential read → `TradingClient` construction)
- `get_position(symbol)` and `get_open_orders(symbol)` via
  `AlpacaLivePositionBroker` (read-only, GET only)
- All safety invariant fields (`broker_mutation_calls_made=false`,
  `credential_values_exposed=false`, `submit_order_reachable=false`, etc.)
- Redaction of broker exception text
- Output always written (PASS or BLOCKED)
- `run_reconciliation()` never raises

The position status checker should reuse this implementation where possible,
adding only:

- `market_session_status` (optional, from a read-only clock endpoint)
- `close_position_reachable=false` (new hardcoded safety field)
- `position_decision_made=false` (new hardcoded safety field)
- `checked_at_utc` timestamp

If implemented as a thin wrapper or extended version of the existing tool,
it must not duplicate or weaken any existing gate logic.

### Gate sequence must be preserved

Any implementation must preserve the existing gate order exactly:

| Gate | Order | Blocker on failure |
|------|-------|--------------------|
| `credential_guard` artifact present and `result="PASS"` | 1 | BLOCKED, `credentials_read=false` |
| `operator_override` artifact present and `result="PASS"` | 2 | BLOCKED, `credentials_read=false` |
| `symbol` exactly `"SPY"` | 3 | BLOCKED, `credentials_read=false` |
| `--allow-live-broker-api-readonly` flag present | 4 | BLOCKED, `credentials_read=false` |
| `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` non-empty | 5 | BLOCKED, `credentials_read=true` |
| `TradingClient` construction succeeds | 6 | BLOCKED, `broker_calls_made=false` |
| Broker read-only calls succeed | 7 | BLOCKED (on exception), redacted |

Credentials must be read only after all gates 1–4 pass.
`TradingClient` must be constructed only after credentials are confirmed non-empty.

---

## 4. Proposed Output Fields

All output fields below must be present in every result (PASS or BLOCKED).

### Safety invariant fields (hardcoded)

| Field | Required value | Notes |
|-------|---------------|-------|
| `broker_mutation_calls_made` | `false` | Always — no mutation calls |
| `credential_values_exposed` | `false` | Always — credentials never written to output |
| `live_submit_enabled` | `false` | Always |
| `submit_order_reachable` | `false` | Always |
| `cancel_order_reachable` | `false` | Always |
| `replace_order_reachable` | `false` | Always |
| `close_position_reachable` | `false` | Always — new field vs. reconciliation tool |
| `broker_ids_redacted` | `true` | Always |
| `account_identifiers_redacted` | `true` | Always |
| `raw_broker_response_included` | `false` | Always |
| `position_decision_made` | `false` | Always — no hold/sell recommendation |

### Dynamic fields

| Field | Type | Notes |
|-------|------|-------|
| `checked_at_utc` | `str` | ISO-8601 timestamp of run start |
| `result` | `str` | `"PASS"` or `"BLOCKED"` |
| `broker_calls_made` | `bool` | `false` on gate failures; `true` after broker calls |
| `broker_calls_readonly` | `bool` | Mirrors `broker_calls_made` |
| `credentials_read` | `bool` | `false` before gate 5; `true` after os.environ.get |
| `symbol` | `str \| null` | Echoed only if exactly `"SPY"` — otherwise `null` |
| `position_observed` | `bool \| null` | `null` if BLOCKED before broker call |
| `open_order_observed` | `bool \| null` | `null` if BLOCKED before broker call |
| `market_session_status` | `str \| null` | Allowlisted: `"open"`, `"closed"`, `"pre_market"`, `"after_hours"`, or `null`; any other broker return value → BLOCKED, raw value not echoed |
| `violations` | `list[str]` | Non-empty on BLOCKED |
| `blocker` | `str \| null` | Set on BLOCKED; `null` on PASS |

### What must never appear in any output field

- Raw broker exception text (must be redacted to fixed safe message)
- Credential values or fragments
- Account ID or account number
- Raw broker order IDs, position IDs, asset IDs
- Position size, quantity, fill price, current price
- Cost basis, unrealized PnL
- Buying power, account balance
- Raw broker API response body

---

## 5. Proposed CLI

```bash
python -m src.tools.manual_position_status_checker_readonly \
    --credential-guard  output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol            SPY \
    --output            output/manual_position_status_checker_readonly.json \
    --allow-live-broker-api-readonly
```

Exit 0 on PASS; exit 1 on BLOCKED. Output artifact always written.

Without `--allow-live-broker-api-readonly`:
- `result="BLOCKED"`, `blocker="readonly broker api flag not set"`
- `credentials_read=false`, `broker_calls_made=false`
- Output artifact written; exit 1

---

## 6. Testing Requirements for Future Implementation

All of the following are hard requirements for any implementation PR.
None are optional.

### Test isolation

- All tests must use a mock broker or mock `TradingClient`
- No real Alpaca calls in any test
- No real credentials in any test

### Functional test coverage (minimum)

| Scenario | Expected result |
|----------|----------------|
| Missing or non-PASS credential guard | BLOCKED, `credentials_read=false` |
| Missing or non-PASS operator override | BLOCKED, `credentials_read=false` |
| Wrong symbol | BLOCKED, `credentials_read=false` |
| Flag absent | BLOCKED, `credentials_read=false`, `broker_calls_made=false` |
| Flag present, credentials missing | BLOCKED, `credentials_read=true`, `broker_calls_made=false` |
| Flag present, `TradingClient` construction fails | BLOCKED, `credentials_read=true`, `broker_calls_made=false` |
| PASS with position present | `position_observed=true`, `open_order_observed=false/true` |
| PASS with no position | `position_observed=false` |
| Broker raises on `get_position` | BLOCKED, exception text absent from output |
| Broker raises on `get_open_orders` | BLOCKED, exception text absent from output |
| Output artifact always written | Confirmed for all BLOCKED paths |
| `run_status_check()` never raises | Confirmed on all exception paths |

### Output invariant tests

All safety invariant fields must be tested to confirm they are hardcoded
regardless of path:

- `broker_mutation_calls_made=false`
- `credential_values_exposed=false`
- `submit_order_reachable=false`
- `cancel_order_reachable=false`
- `replace_order_reachable=false`
- `close_position_reachable=false`
- `position_decision_made=false`
- `broker_ids_redacted=true`
- `account_identifiers_redacted=true`
- `raw_broker_response_included=false`

### Source scan tests (required)

Source scans must verify the absence of the following in the implementation
source file (non-comment lines only):

| Pattern | Must be absent |
|---------|---------------|
| `submit_order(` | Yes |
| `cancel_order(` | Yes |
| `replace_order(` | Yes |
| `close_position(` | Yes |
| `close_all_positions(` | Yes |
| `requests.` / `httpx.` / `aiohttp.` / `urllib.request` | Yes |
| `import alpaca` (module-level) | Yes — lazy import only |
| POST / PATCH / DELETE (as HTTP method references) | Yes |
| Ledger write calls | Yes |
| `os.environ` (module-level) | Yes — only inside functions after gates pass |

### Exception redaction test

A test must inject a secret string into the broker exception message and
confirm the secret is absent from all output fields, `violations` list,
`blocker` field, and stdout.

---

## 7. Warnings

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

> **This design does not approve implementation.**
> A future implementation requires its own PR, mock-only tests,
> safety review, and all gates confirmed in the implementation PR.

---

## 8. What Was Not Designed Here

The following are explicitly out of scope for this design and require
separate design documents before any implementation:

| Feature | Status |
|---------|--------|
| Automated or recurring position check | Out of scope — manual only |
| Price or PnL threshold alerting | Out of scope |
| Stop-loss or take-profit logic | Out of scope |
| Sell or close workflow | Out of scope — separate design required |
| Cancel or replace workflow | Out of scope — separate design required |
| Ledger write on status check | Out of scope |
| Position size or quantity reporting | Out of scope |
| Fill price or current price reporting | Out of scope |
| Multi-symbol support | Out of scope — SPY only |

---

## References

- `src/tools/live_position_reconciliation_readonly.py` — existing read-only tool to reuse/wrap
- `docs/live_position_reconciliation_readonly_design.md` — reconciliation design
- `docs/manual_position_monitoring_and_exit_framework.md` — broader monitoring framework (PR #133)
- `docs/position_reconciliation_readonly_pass_snapshot.md` — PASS run snapshot (PR #132)
- `docs/live_readiness_status.md` — full readiness status and milestone history

---

## Suggested Git Tag

```
manual-position-status-checker-readonly-designed
```
