# Live Submit Enablement Design v2

Design for the next phase after the `live-readiness-pre-submit-complete` milestone.
**This document describes how real live submit could be enabled in a future PR.
It does not implement it. No `submit_order` call exists in the current codebase.**

---

## Non-Goals (This Document)

> **This design does not authorize or implement real live trading.**
> No defaults are changed. No limits are raised. No order submission path is added.
> All items below are design requirements for a future, separately reviewed PR.

- No immediate live submit
- No changes to config defaults
- No raising of notional or order limits
- No multi-order support
- No autonomous trading, scheduling, or looping

---

## Current Baseline

Tag: `live-readiness-pre-submit-complete`

| Item | State |
|------|-------|
| `submit_order` | Unreachable — no call path exists in current codebase |
| Real submit | Not implemented |
| `live_trading_approved` | `false` in all approval artifacts |
| `live_order_submission_approved` | `false` in all approval artifacts |
| Executor state | Blocks at `approval_artifact` on every run |
| `live_trading_enabled` | `false` (config default) |
| `live_submit_dry_run` | `true` (config default) |
| `live_kill_switch_enabled` | `true` (config default) |

---

## Phase Gate: Prerequisites Before Implementation PR

All of the following must be true before a real-submit implementation PR is opened.
Each is a hard prerequisite — not a suggestion.

1. **Live account funded** — `buying_power` and `portfolio_value` both non-zero in `live_account_check`.
2. **`live_readiness_gate` returns GO** — all five stages must PASS.
3. **At least one suitable symbol** — at least one symbol passes live sizing.
4. **Both new approval artifacts present** — `live_trading_approval` and `live_order_submission_approval` (see below).
5. **Full pre-submit pipeline PASS** — all seven pipeline steps produce passing artifacts.
6. **Separate implementation PR** — real order submission must be designed and reviewed in its own dedicated PR, never silently added to an existing tool.

---

## Required New Approvals

Two new approval artifacts are required. These are **distinct from** `live_real_submit_pr_approval`,
which only authorized opening a PR. These artifacts authorize a single live order attempt.

### Artifact 1 — `live_trading_approval.json`

| Field | Required value |
|-------|---------------|
| `live_trading_approved` | `true` |
| `approval_scope` | `AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY` |
| `operator_name` | Non-empty string |
| `approval_timestamp_utc` | ISO-8601 UTC timestamp |
| `approval_note` | Non-empty string |
| `risk_acknowledged` | `true` |
| `live_order_submission_approved` | `false` — this artifact must not authorize order submission |

### Artifact 2 — `live_order_submission_approval.json`

| Field | Required value |
|-------|---------------|
| `live_order_submission_approved` | `true` |
| `approval_scope` | `AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY` |
| `operator_name` | Non-empty string |
| `approval_timestamp_utc` | ISO-8601 UTC timestamp |
| `approval_note` | Non-empty string |
| `risk_acknowledged` | `true` |

### Separation requirement

- `live_trading_approval.json` authorizes enabling live trading mode only. It must not authorize order submission. `live_order_submission_approved` must be `false` or absent.
- `live_order_submission_approval.json` is a separate artifact. It must be produced independently and consumed by the executor as a distinct input.
- Combined approval artifacts are not allowed. A single artifact may not set both `live_trading_approved=true` and `live_order_submission_approved=true`.

Both artifacts must:
- Be produced by offline CLI tools (no Alpaca calls during approval generation)
- Require explicit `--operator-name`, `--approval-timestamp-utc`, `--approval-note`, `--risk-acknowledge` arguments
- Be written to the output directory alongside other pipeline artifacts
- Be consumed by the executor as additional guard inputs

---

## Required Config Changes (Future PR Only)

These config fields must be explicitly set in the operator's local config file
before a real submit attempt. They must not become new defaults in `settings.yaml`.

| Field | Value required for real submit |
|-------|-------------------------------|
| `live_trading_enabled` | `true` |
| `live_submit_dry_run` | `false` |
| `live_kill_switch_enabled` | `false` |
| `live_require_human_confirm` | `true` (unchanged) |
| `live_max_orders_per_day` | `1` |
| `live_max_notional_per_day` | `100.0` |
| `live_max_order_notional` | `100.0` |

> **Do not change these defaults in `settings.yaml` or any shared config file.**
> They must be set only in a local operator config overriding the safe defaults.

---

## Required Runtime Guards

The executor must verify all of the following before any `submit_order` call.
Failure of any guard must result in `blocked=true` and no order submission.

| Guard | Description |
|-------|-------------|
| Exact confirm token | `confirm_token == "REAL-LIVE-SUBMIT-AUTHORIZED"` |
| `live_trading_approval` artifact | Present, `live_trading_approved=true`, `approval_scope=AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY` |
| `live_order_submission_approval` artifact | Present, `live_order_submission_approved=true`, `approval_scope=AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY` |
| Config safety | `live_trading_enabled=true`, `live_submit_dry_run=false`, `live_kill_switch_enabled=false` |
| Human confirm required | `live_require_human_confirm=true` |
| Pre-submit checklist | `final_result=READY` |
| Plan review | `review_result=PASS` |
| `live_readiness_gate` | Decision `GO` (all five stages PASS) |
| Live ledger verify | Schema valid, safety invariants pass |
| Notional cap | `intended_notional <= live_max_order_notional` |
| Daily order limit | Orders today `< live_max_orders_per_day` |
| Daily notional limit | Notional today `+ intended_notional <= live_max_notional_per_day` |
| No live position for symbol | Broker reports zero position for target symbol |
| No live open order for symbol | Broker reports no open order for target symbol |
| Client order ID idempotency | `client_order_id` not already in ledger |
| Market order notional only | Order type must be market, quantity expressed as notional |
| Single symbol | Only one symbol per invocation; no batch |
| No retry path | Executor must not retry after any exception or rejection |

---

## Required Ledger Behavior

The live ledger must record every submit attempt regardless of outcome.
A ledger row must be written **before** `submit_order` is called, and updated after.

| Event | Ledger action |
|-------|--------------|
| Attempt begins | Write row with `status=attempting`, all intent fields |
| `submit_order` returns response | Update row: `status=submitted`, record `broker_order_id` |
| Broker rejects order | Update row: `status=rejected`, record rejection reason |
| `submit_order` raises exception | Update row: `status=exception`, record exception message |
| Post-submit `live_ledger_verify` | Must PASS; if it fails, log error but do not retry |

The ledger must use `append_live_ledger_row(allow_write=True)` — the only path
that bypasses the write guard in `src/execution/live_ledger.py`.

---

## First Real-Submit Implementation Constraints

The first real-submit implementation PR must be strictly scoped:

**Allowed:**
- SPY only, or a single symbol from the configured symbol list
- Buy market order, notional sizing only
- Maximum `$100` notional per order

**Not allowed in this phase:**
- Sell orders
- Short positions
- Options
- Crypto
- Bracket orders or attached stop/limit legs
- Scheduled or looping execution
- Retries of any kind
- Auto-close logic
- Multi-symbol batching
- Any order type other than market

---

## Rollback Procedure

If a live order is submitted and any subsequent check fails, or if the operator
decides to abort:

1. Set `live_trading_enabled=false` in local config
2. Set `live_submit_dry_run=true` in local config
3. Set `live_kill_switch_enabled=true` in local config
4. Do **not** delete the ledger artifact — preserve it for audit
5. If an open position or order exists, close or cancel it manually via the Alpaca dashboard
6. Run `live_ledger_verify` to confirm the ledger is consistent

---

## Implementation Checklist (Future PR)

The real-submit implementation PR must include all of the following:

- [ ] New `live_trading_approval` CLI — produces `live_trading_approval.json` ✓ **implemented (PR #102)**
- [ ] New `live_order_submission_approval` CLI — produces `live_order_submission_approval.json` ✓ **implemented (PR #103)**
- [ ] `live_v2_approvals_review` — offline review of both artifacts ✓ **implemented (PR #104)**
- [ ] Updated executor guards — consume both new approval artifacts ✓ **implemented (PR #105)**
- [ ] `live_v2_executor_readiness_review` — verify executor reached config_safety after v2 approvals ✓ **implemented**
- [ ] `live_v2_final_readiness_review` — single offline summary artifact: v2 approval layer complete, executor accepts v2, config_safety is remaining blocker ✓ **implemented**
- [ ] `submit_order` call path — gated behind all guards listed above
- [ ] Pre-submit ledger write — row written before `submit_order`
- [ ] Post-submit ledger update — row updated for submitted / rejected / exception
- [ ] Post-submit `live_ledger_verify` — runs automatically after attempt
- [ ] Tests — all paths: approved, rejected, exception, each guard failing individually
- [ ] Updated `live_readiness_status.md` and `live_readiness.md`
- [ ] Updated `live_submit_design.md` with implementation notes

---

## What This Design Does Not Change

- Config defaults remain safe: `live_trading_enabled=false`, `live_submit_dry_run=true`, `live_kill_switch_enabled=true`
- `submit_order` remains unreachable until the implementation PR is merged and operator config is explicitly changed
- All existing pipeline artifacts and CLI tools remain unchanged
- `live_real_submit_pr_approval` scope remains `OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY` — it is not upgraded by this design
- This document has no effect on the running system
