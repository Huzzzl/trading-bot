# Live Readiness Status

Current operational status of the live-readiness gate baseline.
Last updated: 2026-05-20. Full pre-submit pipeline complete through PR #98.

---

## Current Status

| Item | Value |
|------|-------|
| Gate baseline | Complete |
| Current decision | **NO-GO** (account not funded) |
| Approx readiness | ~99% |
| Pre-submit checklist | READY |
| Operator release checklist | RELEASE_READY |
| Real-submit PR approval artifact | Complete — `approval_scope=OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY` |
| Executor check | Blocks safely — `blocked=true`, `submit_order_called=false` |
| Blocked report review | PASS |
| `submit_order` | Unreachable — no call path exists in current codebase |
| Real live submit | **Not implemented. Not approved.** |

`live_trading_approved=false` and `live_order_submission_approved=false` in all
approval artifacts. The executor blocks at `approval_artifact` on every run.
No live order submission is possible or planned in the current codebase.

---

## Safety State

| Safety field | Current value | Meaning |
|---|---|---|
| `live_trading_enabled` | `false` | Live trading disabled |
| `live_kill_switch_enabled` | `true` | Kill switch engaged |
| `live_submit_dry_run` | `true` | Submit path in dry-run mode |
| `live_require_human_confirm` | `true` | Human confirm token required |
| `live_trading_approved` | `false` | Not approved in approval artifact |
| `live_order_submission_approved` | `false` | Not approved in approval artifact |
| `approval_scope` | `OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY` | Authorizes opening a PR only — not live trading |
| `submit_order_called` | `false` | Never called on any path |

---

## Pre-Submit Pipeline (Current)

All seven steps below are implemented, tested, and offline-only.
Running them end-to-end produces a blocked report confirming safe state.

### Step 1 — Pre-submit checklist

```bash
python -m src.tools.live_pre_submit_checklist \
    --config     config/settings.paper.local.yaml \
    --output-dir output/live_pre_submit_checklist
```

Output: `output/live_pre_submit_checklist/live_pre_submit_checklist.json`

### Step 2 — Dry-run submit plan

```bash
python -m src.tools.live_submit \
    --config     config/settings.paper.local.yaml \
    --symbol     SPY \
    --confirm    "DRY-RUN-LIVE-SUBMIT" \
    --output-dir output/live_submit_dry_run
```

Output: `output/live_submit_dry_run/live_submit_dry_run_plan.json`

### Step 3 — Plan review

```bash
python -m src.tools.live_submit_plan_review \
    --plan   output/live_submit_dry_run/live_submit_dry_run_plan.json \
    --output output/live_submit_dry_run/live_submit_plan_review.json
```

Output: `output/live_submit_dry_run/live_submit_plan_review.json`

### Step 4 — Operator release checklist

```bash
python -m src.tools.live_operator_release_checklist \
    --config      config/settings.paper.local.yaml \
    --pre-submit  output/live_pre_submit_checklist/live_pre_submit_checklist.json \
    --submit-plan output/live_submit_dry_run/live_submit_dry_run_plan.json \
    --plan-review output/live_submit_dry_run/live_submit_plan_review.json \
    --output      output/live_operator_release_checklist.json
```

Output: `output/live_operator_release_checklist.json`

### Step 5 — Real-submit PR approval artifact

```bash
python -m src.tools.live_real_submit_pr_approval \
    --release-checklist output/live_operator_release_checklist.json \
    --operator-name "Operator Name" \
    --approval-note "Approving to open real-submit implementation PR only" \
    --output        output/live_real_submit_pr_approval.json
```

Output: `output/live_real_submit_pr_approval.json`

> This approval authorizes **opening a future implementation PR only**.
> It does not authorize real live trading. `live_trading_approved=false`
> and `live_order_submission_approved=false` are always written.

### Step 6 — Executor check

```bash
python -m src.tools.live_submit_executor_check \
    --config      config/settings.paper.local.yaml \
    --symbol      SPY \
    --confirm     "REAL-LIVE-SUBMIT-AUTHORIZED" \
    --approval    output/live_real_submit_pr_approval.json \
    --pre-submit  output/live_pre_submit_checklist/live_pre_submit_checklist.json \
    --plan-review output/live_submit_dry_run/live_submit_plan_review.json \
    --plan        output/live_submit_dry_run/live_submit_dry_run_plan.json \
    --output-dir  output/live_submit_executor
```

Output: `output/live_submit_executor/live_submit_blocked_report.json`

Expected result: `blocked=true`, `submit_order_called=false`,
`block_guard=approval_artifact`. Exit code 0.

### Step 7 — Blocked report review

```bash
python -m src.tools.live_submit_blocked_review \
    --report output/live_submit_executor/live_submit_blocked_report.json
```

Expected result: `PASS`. Exit code 0.

---

## Artifact Summary

| Artifact | Path |
|----------|------|
| Pre-submit checklist | `output/live_pre_submit_checklist/live_pre_submit_checklist.json` |
| Dry-run submit plan | `output/live_submit_dry_run/live_submit_dry_run_plan.json` |
| Plan review | `output/live_submit_dry_run/live_submit_plan_review.json` |
| Operator release checklist | `output/live_operator_release_checklist.json` |
| Real-submit PR approval | `output/live_real_submit_pr_approval.json` |
| Executor blocked report | `output/live_submit_executor/live_submit_blocked_report.json` |

---

## Latest Gate Result

```
=== Live Readiness Gate ===
  account_check           : WARN
  shadow_preflight        : FAIL
  shadow_review           : FAIL
  symbol_screen           : WARN
  symbol_screen_review    : FAIL

  decision: NO-GO
  top_blockers:
    ! [account_check] buying_power=0, portfolio_value=0
    ! [shadow_review] [live_sizing] SPY candidates exceed live_max_notional=500.0
    ! [symbol_screen_review] No symbols currently suitable under current live sizing limits.
============================
```

---

## Current Blockers

| Blocker | Stage |
|---------|-------|
| `buying_power=0` | account_check |
| `portfolio_value=0` | account_check |
| SPY candidates exceed `live_max_notional=500.0` | shadow_preflight / shadow_review |
| No suitable symbols under current universe / sizing limits | symbol_screen / symbol_screen_review |

These blockers are **expected** at this stage. The live account has not been funded.
Do not raise `live_max_notional` or adjust sizing limits to artificially force GO.

---

## Safety Baseline Completed

The following read-only checks and guards are implemented and tested:

| Component | Notes |
|-----------|-------|
| Paper canary buy/close validated | End-to-end paper order lifecycle |
| Paper ledger verify | Ledger row written and verified |
| Paper daily limits | `paper_daily_max_orders`, `paper_daily_max_buy_orders`, `paper_daily_max_close_orders` |
| Market-hours guard | `paper_require_market_hours` |
| Open-order guard | `paper_block_if_open_orders` |
| Kill switch | `paper_kill_switch_enabled` |
| `live_account_check` | Credentials + account health (read-only) |
| `live_shadow_preflight` | Strategy preview + live account state (read-only) |
| `live_shadow_review` | Artifact review of preflight output (read-only) |
| `live_shadow_screen_symbols` | Multi-symbol live sizing screen (read-only) |
| `live_shadow_screen_review` | Artifact review of symbol screen output (read-only) |
| `live_readiness_gate` | Unified GO/NO-GO gate across all five checks (read-only) |
| `live_readiness_gate --append-history` | Optional per-run CSV snapshot for trend tracking (read-only) |
| `live_readiness_history_review` | Trend review of history CSV: GO/NO-GO counts, recurring blockers (read-only) |
| Fractional/notional shadow sizing | `live_sizing_mode=notional` + `live_order_notional_override` — shadow check only, no submit |
| `live_dry_run_intents` | Dry-run intent audit: runs readiness checks, writes hypothetical intent artifacts, never submits |
| `live_dry_run_review` | Read-only artifact review of dry-run intent outputs; detects safety flag violations (read-only) |
| `live_safety_status` | Read-only live safety config baseline status; checks safety locks are engaged (read-only) |
| Live safety config fields | `live_trading_enabled=false`, `live_kill_switch_enabled=true`, `live_submit_dry_run=true`, `live_require_human_confirm=true` — all defaulting safe |
| `live_ledger_verify` | Read-only live ledger schema validator; checks required columns and safety invariants; PASS on missing ledger (not yet created) |
| Live ledger schema | 16-column schema defined in `src/execution/live_ledger.py`; `append_live_ledger_row()` write-guarded — raises unless `allow_write=True` explicitly passed |
| `live_pre_submit_checklist` | Unified operator checklist; runs all 5 checks in sequence and produces READY/NOT READY; no live submit, no credentials for offline checks |
| `live_submit` (dry-run skeleton) | Validates all preconditions; writes `live_submit_dry_run_plan.json`; never calls `submit_order`; enforces `live_submit_dry_run=true` |
| `live_submit_plan_review` | Read-only review of dry-run plan artifact; verifies all 8 safety fields; optional `--output` writes review JSON for downstream tools; never calls Alpaca |
| `live_operator_release_checklist` | Offline release gate; reads pre-submit checklist, dry-run plan, and optional plan-review artifacts; produces RELEASE_READY / NOT_RELEASE_READY; includes manual approval fields for operator sign-off; never calls Alpaca; never submits orders |
| `live_real_submit_pr_approval` | Offline approval artifact CLI; reads release checklist (must be RELEASE_READY); produces explicit human sign-off for opening real-submit PR only; `live_trading_approved=false`, `live_order_submission_approved=false`; never calls Alpaca; never submits orders |
| `live_submit_executor` (skeleton) | Fail-closed guarded executor; `maybe_execute_live_submit()` runs 18 guards; no return path with `blocked=false`; all 18 guards passing still ends at `real_submit_not_implemented`; writes `live_submit_blocked_report.json` on every exit path; never submits orders |
| `live_submit_blocked_review` | Read-only review of `live_submit_blocked_report.json`; PASS only if `blocked=true`, `submit_order_called=false`, non-empty `block_guard` and `violations`; never writes files; never calls Alpaca |
| `live_submit_executor_check` | CLI wrapper for guarded executor; invokes `maybe_execute_live_submit()`; exits 0 only when `blocked=true` and `submit_order_called=false` and report exists; never calls `submit_order`; never writes ledger |

---

## Live Submit Design

The proposed live submit architecture is documented in
**[docs/live_submit_design.md](live_submit_design.md)**.

The design covers the full proposed submit flow (steps 1–12), hard safety
constraints, required implementation components, rollback procedures, and
open questions.  **Live submit is not implemented.** The design document is
for planning purposes only — no `submit_order` call exists in the codebase.

---

## Required Conditions Before Considering Live Submit Design

All of the following must be true before any live submit design work begins.
Each is a hard prerequisite — not a suggestion.

1. **Live account funded and activated** — `buying_power` and `portfolio_value` both non-zero.
2. **`live_readiness_gate` returns GO** — all five stages must PASS.
3. **At least one suitable symbol** — at least one symbol in the configured universe passes live sizing under current `live_max_notional` (or `live_max_order_notional` in notional mode).
4. **Explicit human approval** — a human operator reviews the gate output and approves proceeding.
5. **Separate PR for live submit design** — live order submission must be designed and reviewed in its own dedicated PR, never silently added to an existing tool.
6. **Live safeguards must exist first** — a live kill switch, live ledger, and live dry-run mode must be implemented and verified before any submit path is added.

---

## Gate Command

```bash
export ALPACA_LIVE_API_KEY="your-live-api-key"
export ALPACA_LIVE_SECRET_KEY="your-live-secret-key"

python -m src.tools.live_readiness_gate \
    --config     config/settings.paper.local.yaml \
    --output-dir output/live_readiness_gate
```

### Audit artifact locations

| Artifact | Path |
|----------|------|
| Gate report | `output/live_readiness_gate/live_readiness_gate_report.json` |
| Preflight report | `output/live_readiness_gate/live_shadow_preflight_report.json` |
| Preflight candidates | `output/live_readiness_gate/live_shadow_candidates.csv` |
| Symbol screen report | `output/live_readiness_gate/live_shadow_symbol_screen_report.json` |
| Symbol screen summary | `output/live_readiness_gate/live_shadow_symbol_screen.csv` |

### Optional history log

To track gate results over time, add `--append-history`:

```bash
python -m src.tools.live_readiness_gate \
    --config         config/settings.paper.local.yaml \
    --output-dir     output/live_readiness_gate \
    --append-history output/live_readiness_history.csv
```

Each run appends one row to the CSV (header written on first use).
The CSV is never read by the gate — it is a plain audit trail only.
History logging never causes the gate to fail.

### Clear credentials when done

```bash
unset ALPACA_LIVE_API_KEY
unset ALPACA_LIVE_SECRET_KEY
```

```powershell
Remove-Item Env:\ALPACA_LIVE_API_KEY    -ErrorAction SilentlyContinue
Remove-Item Env:\ALPACA_LIVE_SECRET_KEY -ErrorAction SilentlyContinue
```

---

## Warnings

> **Do not raise `live_max_notional` or `live_max_order_notional` to force GO.**
> The current notional caps exist for safety. Only raise them after a full funding
> and risk review, and only after the live account carries real buying power.

> **`live_sizing_mode=notional` is shadow sizing only.**
> Switching to notional mode changes how the *preflight check* computes effective
> notional. It does not add any live order submission path. No live orders are
> ever submitted by any tool in this repository.

> **`live_dry_run_intents` is an audit tool only.**
> Every artifact it produces includes `dry_run_only=true` and `submit_allowed=false`.
> Adding a live order submission path requires its own dedicated PR and explicit
> human sign-off as listed in the Prerequisites section above.

> **Do not bypass NO-GO.**
> A NO-GO decision is not a configuration problem to be worked around.
> It means the account or sizing conditions are not ready for live trading.

> **Do not add live submit in this phase.**
> The current codebase has no live order submission path. Adding one requires
> its own PR, its own safeguards, and explicit human sign-off as listed above.

> **`live_real_submit_pr_approval` authorizes opening a PR only.**
> `approval_scope=OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY`. It does not authorize
> real live trading. `live_trading_approved` and `live_order_submission_approved`
> are always written as `false`. The executor blocks at `approval_artifact` on
> every run. `submit_order` is never called.

---

## Milestone: live-readiness-pre-submit-complete

### Recommended git tag

```
live-readiness-pre-submit-complete
```

### What this milestone means

| Item | State |
|------|-------|
| Approx readiness | ~99% |
| Full pre-submit pipeline | Implemented and tested |
| Dry-run submit plan | Implemented — writes plan artifact, never calls `submit_order` |
| Guarded executor | Implemented — `maybe_execute_live_submit()` runs 18 guards, all paths return `blocked=true` |
| Blocked report review | PASS — `blocked=true`, `submit_order_called=false`, non-empty `block_guard` and `violations` |
| `submit_order` | Unreachable — no call path exists in current codebase |
| Real live submit | Not implemented |
| Live trading | Not approved — `live_trading_approved=false` |
| Live order submission | Not approved — `live_order_submission_approved=false` |

### Required verification before tagging

All of the following must pass on a clean checkout of `main`:

- [ ] `python -m pytest` — full suite passes, zero failures
- [ ] `live_pre_submit_checklist` → `final_result=READY`
- [ ] `live_submit_plan_review` → `review_result=PASS`
- [ ] `live_operator_release_checklist` → `release_result=RELEASE_READY`
- [ ] `live_submit_executor_check` → `blocked=true`, `submit_order_called=false`, exit 0
- [ ] `live_submit_blocked_review` → `PASS`, exit 0

### Commands to create the tag

```bash
git checkout main
git pull
python -m pytest
git tag -a live-readiness-pre-submit-complete \
    -m "Live readiness pre-submit pipeline complete; submit_order unreachable"
git push origin live-readiness-pre-submit-complete
```

### Warning

> **This tag does not approve real trading.**
> **This tag does not approve live order submission.**
> It only marks the pre-submit safety baseline complete.
> Real live trading requires its own dedicated PR, explicit human sign-off,
> a funded live account, and a GO decision from `live_readiness_gate`.
