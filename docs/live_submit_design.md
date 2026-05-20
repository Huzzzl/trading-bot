# Live Submit Design Document

**Status:** Design only. Not implemented. Requires a separate PR after this document.

**Last updated:** 2026-05-20. Dry-run skeleton (`live_submit.py`) implemented — see below.

---

## Overview

This document describes the proposed architecture for live order submission once
all preconditions are satisfied.  No live submit code exists today.  This design
must be reviewed and approved by a human operator before any implementation PR
is opened.

> **Nothing in this document authorises live trading.**
> Implementation requires a dedicated PR, its own test suite, and explicit
> human sign-off on every component listed here.

---

## 1. Scope

- Covers one future capability: submitting a single notional market order to
  Alpaca's live (non-paper) endpoint.
- Does **not** cover paper trading, backtesting, or simulation paths.
- Is **not** implemented — no `submit_order` call exists anywhere in the
  current codebase.
- Requires a separate PR that is distinct from this design document.  The
  implementation PR must not be merged until this design has been reviewed and
  explicitly approved.

---

## 2. Preconditions

All of the following must be true before the implementation PR is opened and
before any live submit is attempted at runtime.

| Precondition | How verified |
|---|---|
| `live_pre_submit_checklist` returns `READY` | Run `python -m src.tools.live_pre_submit_checklist` — all five checks must PASS |
| `live_safety_status` returns `PASS` | `live_trading_enabled=false`, `live_kill_switch_enabled=true`, `live_submit_dry_run=true`, `live_require_human_confirm=true` |
| `live_kill_switch_enabled=true` maintained | Must remain `true` at all times except under explicit human sign-off with a written justification |
| `live_submit_dry_run=true` until final approval | Dry-run gate must stay engaged until a human operator explicitly sets it to `false` for a specific run |
| `live_require_human_confirm=true` maintained | Human confirmation token must be required and validated at runtime for every submit attempt |
| `live_ledger_verify` returns `PASS` | No schema violations, no contradictory safety flags |
| `live_dry_run_review` returns `PASS` | Dry-run intent artifacts clean: `dry_run_only=true`, `submit_allowed=false`, no sizing FAIL rows |
| Live account funded | `buying_power > 0`, `portfolio_value > 0` |
| `live_readiness_gate` returns `GO` | All five gate stages PASS |

If any precondition is not met, no implementation work may proceed.

---

## 3. Proposed Submit Flow

The following is the intended runtime sequence.  Each step is a hard gate —
failure at any step aborts the run without submitting an order.

```
1.  Run live_pre_submit_checklist
        → All five checks must return PASS.
        → If NOT READY: abort. No order submitted.

2.  Run live_readiness_gate (inline or via CLI)
        → Decision must be GO.
        → If NO-GO: abort. No order submitted.

3.  Generate dry-run intents (live_dry_run_intents)
        → Confirm intent: symbol, side=buy, order_type=market,
          live_sizing_mode=notional, effective_notional ≤ live_max_order_notional.
        → dry_run_only=True and submit_allowed=False must be present in output.
        → If NO-GO or any sizing FAIL: abort. No order submitted.

4.  Review dry-run artifacts (live_dry_run_review)
        → review_result must be PASS.
        → Any safety flag violation (submit_allowed truthy, dry_run_only falsy): abort.

5.  Require human confirmation token
        → Operator must supply a confirmation token (e.g. a run-specific UUID
          printed by the dry-run step) via CLI argument or environment variable.
        → Token is validated against the dry-run summary artifact.
        → If token missing, blank, or mismatched: abort. No order submitted.

6.  Check live safety config
        → live_kill_switch_enabled must be True.
        → live_require_human_confirm must be True.
        → live_submit_dry_run must be False (operator has explicitly toggled).
        → If any field is in the unsafe state: abort. No order submitted.

7.  Check live daily limits
        → Count orders already in the live ledger for today.
        → today_order_count < live_max_orders_per_day (initially 1).
        → today_notional_total + intended_notional ≤ live_max_notional_per_day
          (initially $100.0 USD).
        → If either limit would be exceeded: abort. No order submitted.

8.  Check open positions and open orders
        → No existing live position in the target symbol.
        → No existing live open order for the target symbol.
        → If either check fails: abort. No order submitted.

9.  Submit order
        → Single notional market order via TradingClient(paper=False).submit_order().
        → client_order_id generated from dry-run intent (idempotency key).
        → client_order_id must not already exist in the live ledger (dedup guard).
        → Parameters: symbol, notional, side=buy, type=market.
        → No bracket, stop, limit, or complex orders.
        → No short selling (side must be buy).

10. Poll order status
        → Poll get_order_by_id() up to live_poll_timeout_seconds.
        → Record final status (filled, accepted, cancelled, rejected).

11. Write live ledger row
        → append_live_ledger_row(allow_write=True) must be called immediately
          after submit, regardless of poll outcome.
        → Row must include: run_id, client_order_id, alpaca_order_id, symbol,
          side, order_type, live_sizing_mode, quantity, notional, status,
          submitted_at, checked_at_utc, dry_run_only=False, submit_allowed=True,
          notes.
        → If ledger write fails: log error loudly. Do not retry the order.

12. Verify ledger
        → Run live_ledger_verify immediately after write.
        → Log result. A ledger FAIL after submit is an alert condition —
          do not proceed with any further action until ledger is clean.
```

---

## 4. Hard Safety Constraints

These constraints are non-negotiable for the initial implementation.  Each must
be enforced in code with a test that demonstrates the constraint blocking a
submit when violated.

| Constraint | Value / Rule |
|---|---|
| Max orders per day | 1 (hard-coded initial cap; configurable only via `live_max_orders_per_day`) |
| Max notional per day | $100.0 USD (hard-coded initial cap; configurable only via `live_max_notional_per_day`) |
| Sizing mode | Notional only (`live_sizing_mode=notional`) for initial implementation |
| Allowed symbols | Only symbols explicitly in `live_shadow_screen_symbols`; no ad-hoc symbols |
| Unattended execution | Prohibited — human confirmation token required for every run |
| Order complexity | Market orders only; no bracket, stop-loss, take-profit, or limit orders |
| Short selling | Prohibited — side must be `buy` |
| Asset classes | Equities only; no options, crypto, futures, or forex |
| Order duplication | Prohibited — `client_order_id` idempotency guard required; ledger checked before submit |
| Retry on failure | Prohibited — if submit fails, stop. Do not retry. Operator must investigate. |
| Ledger write | Required — must write ledger row immediately after every submit attempt, success or failure |

---

## 5. Required Implementation Components

The following components must be built in the implementation PR.  Each must have
its own tests.  No component may be silently added to an existing tool.

| Component | Description |
|---|---|
| `src/tools/live_submit.py` (or equivalent) | Main entry point. Runs the full submit flow (steps 1–12). Reads `--config`, `--symbol`, `--confirm-token`, `--output-dir`. |
| Live daily limit checker | Reads the live ledger and enforces `live_max_orders_per_day` and `live_max_notional_per_day`. Raises before submit if limits would be exceeded. |
| `client_order_id` idempotency guard | Checks the live ledger for the proposed `client_order_id` before submit. Raises if already present. |
| Live ledger append integration | Calls `append_live_ledger_row(allow_write=True)` with all required fields. Tested for both success and failure paths. |
| Human confirmation parser | Validates the `--confirm-token` argument against the dry-run summary artifact. Raises if missing, blank, or mismatched. |
| Kill switch enforcement | Reads `live_kill_switch_enabled` from config. Raises before submit if `false`. |
| Dry-run mode enforcement | Reads `live_submit_dry_run` from config. Raises before submit if `true` (i.e. dry-run gate still engaged). |
| Tests for every blocker | Unit test that each step (precondition, limit, guard) successfully blocks the submit when violated. All tests fully offline (mocked broker). |

---

## 6. Explicit Non-Goals

The following are explicitly out of scope for the initial live submit
implementation and must not be added without a new design review.

- **No multi-symbol submit** — one symbol per run, one order per day.
- **No automated scheduling** — no cron, no daemon, no continuous loop.
- **No adaptive sizing** — notional is fixed; no dynamic position sizing.
- **No raising the notional cap** — `live_max_notional_per_day` stays at $100
  until a separate risk review authorises an increase.
- **No bypassing the checklist** — `live_pre_submit_checklist` must run and
  return `READY` for every submit attempt.  There is no `--force` flag.
- **No multi-leg or complex orders** — single-leg market buy only.
- **No fractional share sizing** — notional mode only; quantity computed by
  Alpaca from the notional amount.

---

## 7. Rollback and Emergency Procedures

If a live order is submitted and something goes wrong, the following actions
are available without touching the codebase.

| Action | How |
|---|---|
| Disable further submits immediately | Set `live_trading_enabled=false` in config (tool reads config on each run) |
| Engage kill switch | Set `live_kill_switch_enabled=true` — the implementation must check this before every submit |
| Cancel open order | Log in to the Alpaca dashboard and cancel the order manually |
| Close open position | Log in to the Alpaca dashboard and close the position manually (or use the existing `paper_close` path adapted for live if available) |
| Audit what happened | Run `live_ledger_verify --ledger output/live_execution_ledger.csv` and inspect the JSON/CSV artifacts |
| Re-engage dry-run gate | Set `live_submit_dry_run=true` in config — blocks all future submit attempts until explicitly toggled again |

No automated rollback is planned for the initial implementation.  All recovery
is manual and operator-driven.

---

## 8. Open Questions (to resolve before implementation PR)

1. **Confirmation token format** — UUID from dry-run summary, or a separate
   operator-generated token?  Must be resistant to accidental replay.
2. **Poll timeout and retry** — What is the right timeout for a market order
   that has not filled?  Current config default is 30 seconds.
3. **Partial fill handling** — If the order is partially filled, how should
   the ledger row record quantity and notional?  (Likely: record the submitted
   notional and update status after polling.)
4. **Alpaca fractional notional rounding** — Does Alpaca round the submitted
   notional, and does the ledger need to record both submitted and filled
   notional?
5. **`live_dry_run_review` PASS gate** — Should `live_submit.py` call
   `live_dry_run_review` as a library call or re-run it as a subprocess?
   Library call is simpler and testable; subprocess is more isolated.

---

## Related Documents and Tools

| Document / Tool | Purpose |
|---|---|
| [docs/live_readiness.md](live_readiness.md) | Full reference for all live-readiness and audit tools |
| [docs/live_readiness_status.md](live_readiness_status.md) | Current GO/NO-GO status and active blockers |
| `python -m src.tools.live_pre_submit_checklist` | Run all five checks in one command |
| `python -m src.tools.live_safety_status` | Check live safety config locks |
| `python -m src.tools.live_readiness_gate` | Full GO/NO-GO gate |
| `python -m src.tools.live_dry_run_intents` | Generate dry-run intent artifacts |
| `python -m src.tools.live_dry_run_review` | Review dry-run artifacts |
| `python -m src.tools.live_ledger_verify` | Validate live ledger schema |
| `python -m src.tools.live_operator_release_checklist` | Offline release gate; reads 3 artifacts; produces RELEASE_READY verdict with manual approval fields |
| `python -m src.tools.live_real_submit_pr_approval` | Offline approval artifact CLI; reads release checklist; produces explicit human sign-off for opening the real-submit PR only; does NOT approve live trading |
| `src.execution.live_submit_executor` | Guarded executor skeleton; `maybe_execute_live_submit()` runs all 18 guards; `submit_order` is unreachable with current defaults; writes `live_submit_blocked_report.json` on blocked path |

---

## Approval Record

> This section must be completed by a human operator before the implementation
> PR is opened.

| Field | Value |
|---|---|
| Design reviewed by | _(not yet reviewed)_ |
| Review date | _(pending)_ |
| Implementation PR authorised | No |
| Notes | — |

---

## Implemented: Dry-Run Skeleton (`live_submit.py`)

`src/tools/live_submit.py` is the dry-run-only skeleton that validates all
preconditions and writes the plan artifact.  It will never call `submit_order`
while `live_submit_dry_run=true`.

```bash
python -m src.tools.live_submit \
    --config     config/settings.paper.local.yaml \
    --symbol     SPY \
    --confirm    "DRY-RUN-LIVE-SUBMIT" \
    --output-dir output/live_submit_dry_run
```

Optional: `--intents-dir <path>` to point at a previous `live_dry_run_intents`
artifact directory (defaults to `{output-dir}/live_pre_submit_checklist/live_dry_run_intents`).

### Preconditions enforced at runtime

| # | Precondition | Fails if |
|---|---|---|
| 1 | Confirmation token | Not exactly `DRY-RUN-LIVE-SUBMIT` |
| 2 | Config loads | Any YAML/validation error |
| 3 | `live_trading_enabled` | `true` |
| 4 | `live_submit_dry_run` | `false` |
| 5 | `live_kill_switch_enabled` | `false` |
| 6 | `live_require_human_confirm` | `false` |
| 7 | `live_pre_submit_checklist` | `NOT READY` |
| 8 | Intent available | No intent with `live_sizing_mode=notional`, `sizing_status=PASS` |
| 9 | Intent safety flags | `submit_allowed=true` or `dry_run_only=false` |

### Artifact written

`{output-dir}/live_submit_dry_run_plan.json` — includes all plan fields with
`submit_order_called=false`, `submit_allowed=false`, and
`final_action="DRY_RUN_ONLY_NO_ORDER_SUBMITTED"`.

### What the skeleton never does

- Never calls `submit_order` or `cancel_order`.
- Never writes the live ledger.
- Never reads paper credentials.
- Never submits or modifies any order or position.

---

## Implemented: Plan Review (`live_submit_plan_review.py`)

`src/tools/live_submit_plan_review.py` is a read-only CLI that parses the
plan artifact and verifies every safety field.

```bash
python -m src.tools.live_submit_plan_review \
    --plan   output/live_submit_dry_run/live_submit_dry_run_plan.json \
    [--output output/live_submit_dry_run/live_submit_plan_review.json]
```

`--output` is optional.  When provided, the review dict is written as JSON
so that `live_operator_release_checklist` can consume it.  When omitted,
no file is written (original behavior preserved).

### Fields verified (all must pass)

| Field | Required value |
|---|---|
| `live_submit_dry_run` | `true` |
| `live_trading_enabled` | `false` |
| `live_kill_switch_enabled` | `true` |
| `submit_order_called` | `false` |
| `submit_allowed` | `false` |
| `final_action` | `"DRY_RUN_ONLY_NO_ORDER_SUBMITTED"` |
| `live_sizing_mode` | `"notional"` |
| `effective_notional` | `> 0` and `≤ live_max_order_notional` |

PASS exits 0.  FAIL exits 1.  Never calls Alpaca.

---

## Implemented: Operator Release Checklist (`live_operator_release_checklist.py`)

`src/tools/live_operator_release_checklist.py` is an offline CLI that reads three
artifacts and produces a `RELEASE_READY` / `NOT_RELEASE_READY` verdict with
manual approval fields for operator sign-off.  It does not authorize live trading
by itself — it only states the project is ready to open a separate real-submit PR.

```bash
python -m src.tools.live_operator_release_checklist \
    --config   config/settings.paper.local.yaml \
    --pre-submit  output/live_pre_submit_checklist/live_pre_submit_checklist.json \
    --submit-plan output/live_submit_dry_run/live_submit_dry_run_plan.json \
    [--plan-review output/live_submit_dry_run/live_submit_plan_review.json] \
    --output   output/live_operator_release_checklist.json
```

### Inputs

| Argument | Required | Description |
|---|---|---|
| `--pre-submit` | Yes | `live_pre_submit_checklist.json` — must have `final_result=READY` |
| `--submit-plan` | Yes | `live_submit_dry_run_plan.json` — 8 safety fields checked |
| `--plan-review` | No | Plan review JSON — `review_result` must be `PASS` if provided |

### Conditions checked (all must pass for RELEASE_READY)

| # | Condition | Required value |
|---|---|---|
| 1 | `pre_submit_checklist.final_result` | `READY` |
| 2 | `submit_plan.live_submit_dry_run` | `true` |
| 3 | `submit_plan.live_trading_enabled` | `false` |
| 4 | `submit_plan.live_kill_switch_enabled` | `true` |
| 5 | `submit_plan.submit_order_called` | `false` |
| 6 | `submit_plan.submit_allowed` | `false` |
| 7 | `submit_plan.final_action` | `"DRY_RUN_ONLY_NO_ORDER_SUBMITTED"` |
| 8 | `submit_plan.live_sizing_mode` | `"notional"` |
| 9 | `submit_plan.effective_notional` | `> 0` |
| 10 | `plan_review.review_result` (if provided) | `PASS` |

### Artifact written

`{output}` — includes all summary fields plus manual approval placeholders:

```json
{
  "release_result": "RELEASE_READY",
  "operator_name": null,
  "approval_timestamp_utc": null,
  "approval_for_real_submit_pr": false,
  "notes": ""
}
```

A human operator must fill in `operator_name`, `approval_timestamp_utc`,
set `approval_for_real_submit_pr` to `true`, and add notes before opening
a real-submit implementation PR.

### What the checklist never does

- Never calls `submit_order` or `cancel_order`.
- Never reads credentials (`ALPACA_*` environment variables).
- Never writes the live ledger.
- Never calls any Alpaca endpoint.
- RELEASE_READY does not authorise live trading — it only clears the gate for
  a separate implementation PR.

---

## Implemented: Real-Submit PR Approval (`live_real_submit_pr_approval.py`)

`src/tools/live_real_submit_pr_approval.py` is an offline CLI that reads
`live_operator_release_checklist.json` and produces an explicit human approval
artifact for opening a real-submit implementation PR.

```bash
python -m src.tools.live_real_submit_pr_approval \
    --release-checklist output/live_operator_release_checklist.json \
    --operator-name "Huzzzl" \
    --approval-note "Approve opening real-submit implementation PR only; not approving live trading." \
    --output output/live_real_submit_pr_approval.json
```

### Preconditions

| Condition | Required value |
|---|---|
| `release_result` in checklist | `RELEASE_READY` |
| `--operator-name` | Non-empty |
| `--approval-note` | Non-empty |

### Artifact fields

| Field | Value |
|---|---|
| `approval_for_real_submit_pr` | `true` |
| `approval_scope` | `"OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY"` |
| `live_trading_approved` | `false` |
| `live_order_submission_approved` | `false` |
| `operator_name` | Supplied by operator |
| `approval_note` | Supplied by operator |
| `approval_timestamp_utc` | Set at run time |

### Scope of approval

This approval authorises **only** opening a separate real-submit implementation PR.
It does **not** authorise live trading or live order submission.
`live_trading_approved` and `live_order_submission_approved` are always `false`.

### What it never does

- Never calls `submit_order` or `cancel_order`.
- Never reads credentials (`ALPACA_*` environment variables).
- Never writes the live ledger.
- Never calls any Alpaca endpoint.
- Never mutates the source release checklist file.

---

## Implemented: Guarded Submit Executor (`live_submit_executor.py`)

`src/execution/live_submit_executor.py` contains `maybe_execute_live_submit()` —
the single guarded entry point for real order submission.  This PR proves
guarded evaluation only: there is **no return path with `blocked=False`**.
Even if all 18 guards pass, a final `real_submit_not_implemented` block is
returned instead of calling `submit_order`.  This PR does not contain
executable real submit.

### The 18 guards (in order)

| # | Guard | Current default | Result |
|---|---|---|---|
| 1 | Confirmation token == `REAL-LIVE-SUBMIT-AUTHORIZED` | Wrong token | BLOCK |
| 2 | Approval artifact exists | — | BLOCK if missing |
| 3 | `approval_for_real_submit_pr == true` | From artifact | BLOCK if false |
| 4 | `approval_scope == "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY"` | From artifact | BLOCK if wrong |
| 5 | `live_trading_approved == true` | Always `false` in current tooling | **ALWAYS BLOCK** |
| 6 | `live_order_submission_approved == true` | Always `false` in current tooling | **ALWAYS BLOCK** |
| 7 | `live_trading_enabled == true` | Default `false` | BLOCK |
| 8 | `live_submit_dry_run == false` | Default `true` | BLOCK |
| 9 | `live_kill_switch_enabled == false` | Default `true` | BLOCK |
| 10 | `live_require_human_confirm == true` | Default `true` | Pass |
| 11 | Pre-submit checklist `READY` | — | BLOCK if not READY |
| 12 | Plan review `PASS` | — | BLOCK if not PASS |
| 13 | Daily order count < `live_max_orders_per_day` | — | BLOCK if exceeded |
| 14 | Daily notional ≤ `live_max_notional_per_day` | — | BLOCK if exceeded |
| 15 | No open position in target symbol | — | BLOCK if exists |
| 16 | No open order for target symbol | — | BLOCK if exists |
| 17 | `client_order_id` not in live ledger | — | BLOCK if duplicate |
| 18 | `effective_notional ≤ live_max_order_notional` | — | BLOCK if exceeded |

Guards 5 and 6 are permanent blocks under current tooling:
`live_real_submit_pr_approval.py` always writes `live_trading_approved=false`
and `live_order_submission_approved=false`.

**Final fail-closed guard (guard 19):** if all 18 guards pass, execution still
returns `blocked=true` with `block_guard="real_submit_not_implemented"`.
There is no return path with `blocked=false` in this PR.

### Blocked-path artifact

On every exit path, `live_submit_blocked_report.json` is written with:

```json
{
  "submit_order_called": false,
  "blocked": true,
  "block_guard": "<guard name>",
  "violations": ["..."]
}
```

### What it never does

- Never calls `submit_order` with current defaults (unreachable).
- Never writes the live ledger on the blocked path.
- Never reads paper credentials.
- Never cancels orders.
