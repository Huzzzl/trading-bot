# Manual Position Monitoring and Exit Framework

Design framework for manually monitoring the observed SPY position and planning
future manual exit workflows.

**This document does NOT approve trading.**
**This document does NOT submit, sell, cancel, replace, or close positions.**
**This document does NOT contact Alpaca.**
**This document does NOT read credentials.**
**This document does NOT implement automated monitoring.**
**This document does NOT implement stop-loss, take-profit, trailing stop,**
**recurring jobs, or a sell adapter.**
**This document does NOT approve holding or selling the observed position.**
**The current SPY position remains a manual operator decision.**

---

## 1. Current State

| Item | State |
|------|-------|
| First real single manual SPY buy | Complete — `result="SUBMITTED"` observed |
| Read-only reconciliation run | PASS observed — `--allow-live-broker-api-readonly` |
| `position_observed` | `true` (boolean flag only) |
| `open_order_observed` | `false` |
| Position size, fill price, quantity | Not recorded in repository |
| Cost basis, unrealized PnL | Not recorded in repository |
| Account ID, order ID, broker identifiers | Not recorded in repository |
| Raw broker response | Not recorded in repository |
| Emergency actions (cancel, close, replace) | Manual via Alpaca UI only |
| Automated position management | Not implemented |
| Bot hold/sell decision | Not implemented — remains manual |

`position_observed=true` is a boolean presence flag only. It confirms only
that a SPY position existed in the live account at the time of the reconciliation
run. It does not record size, price, quantity, cost basis, or any identifier.

Whether to hold or sell the position is entirely a manual operator decision.
The bot provides status checks only.

---

## 2. Manual Monitoring Principles

### Operator checks Alpaca UI directly

The operator monitors the live account by logging into the Alpaca broker UI
directly. No automated or scheduled monitoring is implemented or approved here.

### Bot must not decide hold or sell

No code path in this repository decides whether to hold or sell a position.
`position_observed=true` from `live_position_reconciliation_readonly` is a
boolean presence flag — it does not trigger, recommend, or imply any action.

### Bot must not infer PnL from committed docs

No committed document contains position size, fill price, quantity, cost basis,
unrealized gain/loss, or account balance. Any PnL inference from committed
docs would be based on incomplete or stale information and must not be acted on.

### Any future monitoring tool must be read-only first

If a future read-only position monitoring tool is implemented:

- It must be read-only (GET calls only)
- It must require an explicit CLI flag before any broker API contact
- It must require both prerequisite artifact gates (credential guard +
  operator override) before reading credentials or constructing a broker client
- It must not record or output position size, fill price, quantity, cost basis,
  PnL, account identifiers, order IDs, or raw broker responses
- It must be mock-only in all tests
- It must not trigger, recommend, or decide any position action

### Any future sell/close workflow requires separate design

A future engineered sell or close workflow is not designed or implemented here.
Before any such workflow can be implemented, the following are required
(each is a hard prerequisite — not a suggestion):

1. A separate design document reviewed in its own PR
2. A separate approval artifact (distinct from the buy approval)
3. A new explicit CLI flag (separate from `--allow-real-live-submit-once`)
4. Mock-only tests with no real Alpaca calls
5. Credentials read only after all gates pass and the explicit flag is present
6. Broker mutation exactly once per run if the workflow is implemented
7. Ledger behavior separately designed and reviewed
8. No automatic sell based on price thresholds, PnL targets, or stop conditions
9. No cancel/replace unless separately designed and reviewed

---

## 3. Manual Exit Options Outside Code

The following operator actions are available entirely outside this codebase:

| Option | How |
|--------|-----|
| Manually sell/close the SPY position | Alpaca broker UI — Market or limit sell order |
| Continue holding manually | No action required — position remains open |
| Set a manual alert in Alpaca UI | Price alert, not automated code |
| Engineer a future sell workflow | Requires separate design PR — not in this PR |

### What is not available (not implemented)

| Action | Status |
|--------|--------|
| Automated sell on price threshold | Not implemented |
| Stop-loss or take-profit logic | Not implemented |
| Trailing stop via code | Not implemented |
| Recurring position check job | Not implemented |
| Sell adapter or broker mutation method | Not implemented |
| Cancel or replace via code | Not implemented |
| Retry on sell failure | Not implemented |

Emergency actions (cancel, close, replace) remain manual via the Alpaca
broker UI only.

---

## 4. Future Read-Only Monitoring Design Ideas

The following are design ideas only. None are implemented in this document
or in any current code. Each would require its own implementation PR with
mock-only tests and safety review.

### 4.1 Read-only position status checker

A future tool could check position presence and open order presence for SPY,
returning boolean flags only — similar to `live_position_reconciliation_readonly`.

Non-sensitive output fields:
- `position_observed: bool` — presence flag only
- `open_order_observed: bool` — presence flag only
- `broker_calls_readonly: true` — always
- `broker_mutation_calls_made: false` — always

Fields that must never appear in output:
- Position size, quantity, fill price
- Cost basis, unrealized gain/loss
- Account ID, account number
- Raw order IDs, broker position IDs
- Raw broker response

### 4.2 Read-only market/session checker

A future tool could report current market session state (open, closed,
pre-market, after-hours) using a read-only clock endpoint, to help the
operator time a manual sell during market hours.

Output: boolean/status flags only — no account data, no position data,
no credential exposure.

### 4.3 Local operator checklist / reminder

A future offline checklist tool (no broker calls) could prompt the operator
through manual pre-sell checks:

- Has the position been confirmed in Alpaca UI?
- Are credentials available and ready to be cleared after use?
- Is the approval artifact for the sell fresh and unexpired?
- Has the output artifact from the last reconciliation run been reviewed?
- Is the market currently open?

This is a local reminder tool only — not an automated trading trigger.

### 4.4 Non-sensitive snapshot format

Any future snapshot written to this repository must contain only:

| Allowed field type | Example |
|--------------------|---------|
| Boolean flags | `position_observed`, `open_order_observed` |
| Status strings | `result`, `blocker` |
| Redacted placeholders | `"<redacted>"` |
| Timestamps (non-sensitive) | `run_at_utc` |

Must never appear in any committed snapshot:
- Position size or quantity
- Fill price or current price
- Cost basis or unrealized PnL
- Account ID or account number
- Raw broker order IDs or position IDs
- Buying power or account balance
- Raw broker response or API response body

---

## 5. Future Sell/Close Workflow Constraints

If a future engineered sell or close workflow is designed, the following
constraints are hard requirements — not suggestions. Each must be satisfied
before a real broker sell call can be made.

### 5.1 Design prerequisites

| Prerequisite | Required |
|--------------|---------|
| Separate design PR reviewed and merged | Yes |
| Separate approval artifact for sell | Yes |
| Separate explicit CLI flag | Yes |
| Mock-only tests (no real Alpaca calls) | Yes |
| Credential read only after all gates pass and flag present | Yes |

### 5.2 Execution constraints

| Constraint | Requirement |
|------------|------------|
| Symbol matching | Exact — `"SPY"` only, no case folding, no whitespace |
| Side | Exactly `"sell"` or `"close"` — as designed |
| Broker mutation calls | Exactly once per run if implemented |
| Retry on failure | Not implemented — BLOCKED is final |
| Cancel/replace | Not implemented unless separately designed |
| Automated sell on threshold | Prohibited — explicit operator action required |
| Recurring sell job | Prohibited |
| Automated trading | Prohibited |

### 5.3 Output constraints

| Constraint | Requirement |
|------------|------------|
| Broker exception text | Redacted in all output |
| Raw broker order/position IDs | Never in output |
| Account identifiers | Never in output |
| Credential values | Never in output |
| PnL or fill details | Never committed to repository |

### 5.4 Post-sell actions (required)

After any future real sell attempt:

1. Credentials cleared from environment immediately
2. Local operator config reset to safe defaults
3. Output artifact and ledger not committed to repository
4. Order/fill status verified manually in Alpaca UI
5. No automated follow-up sell, cancel, or replace via code

### 5.5 Emergency actions

Emergency actions (cancel, close, replace, force-close) remain manual
via the Alpaca broker UI only — regardless of whether a sell workflow
is implemented in code.

---

## 6. Abort Conditions

The operator must abort any planned sell or hold action and investigate
before proceeding if any of the following conditions is true:

| Condition | Action |
|-----------|--------|
| Any uncertainty about position status or identity | Abort — verify in Alpaca UI first |
| Prerequisite artifacts are stale or expired | Abort — re-run reconciliation |
| Any credential or output leakage detected | Abort immediately — rotate credentials |
| Open order ambiguity (unexpected open orders observed) | Abort — resolve in Alpaca UI |
| Unexpected broker response or API error | Abort — do not retry without investigation |
| Pressure to automate the position decision | Abort — all decisions remain manual |
| Any unreviewed code path that could mutate broker state | Abort — review before running |
| Output artifact from last run is missing or unreadable | Abort — re-run reconciliation |
| Local operator config is not at safe defaults | Abort — reset before running any live tool |
| Approval artifact expired | Abort — generate fresh approval |

---

## 7. Warnings

> **Holding SPY is a financial decision by the operator.**
> The bot does not decide whether holding is appropriate.

> **Selling SPY is a financial decision by the operator.**
> The bot does not decide when or whether to sell.

> **The bot currently provides status checks only.**
> `position_observed=true` confirms presence of a position at a point in time.
> It does not imply a hold or sell recommendation.

> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility. The tools in this repository are infrastructure
> utilities only.

> **This document does not approve holding or selling the observed position.**
> **This document does not approve future trading.**
> **This document does not approve future broker calls.**
> Any future live action requires fresh prerequisite artifacts, a fresh
> approval, fresh preflight, explicit operator action, and an explicit
> CLI flag — none of which are performed by this document.

---

## References

- `src/tools/live_position_reconciliation_readonly.py` — read-only reconciliation tool
- `docs/live_position_reconciliation_readonly_design.md` — reconciliation design
- `docs/position_reconciliation_readonly_pass_snapshot.md` — PASS run snapshot (PR #132)
- `docs/post_submit_manual_position_handling_runbook.md` — post-submit operator runbook
- `docs/real_submit_final_operator_runbook.md` — pre-submit operator runbook
- `docs/live_readiness_status.md` — full readiness status and milestone history

---

## Suggested Git Tag

```
manual-position-monitoring-exit-framework-designed
```
