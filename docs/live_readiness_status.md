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

### Step 6d — Optional: bundle command (regenerate all v2 artifacts at once)

```bash
python -m src.tools.live_v2_readiness_bundle \
    --live-trading-approval output/live_trading_approval.json \
    --live-order-submission-approval output/live_order_submission_approval.json \
    --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \
    --output-dir output/live_v2_bundle
```

Writes under `output/live_v2_bundle/`:
- `live_v2_approvals_review.json`
- `live_v2_executor_readiness_review.json`
- `live_v2_final_readiness_review.json`
- `live_v2_readiness_bundle.json` (top-level audit summary)

All artifacts written regardless of earlier step failures.
Exit 0 only when final readiness review PASSes.

### Step 6c — V2 final readiness review (combined summary artifact)

After Steps 6 and 6b pass, produce the combined summary:

```bash
python -m src.tools.live_v2_final_readiness_review \
    --v2-approvals-review \
    --live-trading-approval output/live_trading_approval.json \
    --live-order-submission-approval output/live_order_submission_approval.json \
    --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \
    --output output/live_v2_final_readiness_review.json
```

Output: `output/live_v2_final_readiness_review.json`

Expected result: `review_result=PASS`, `final_blocker=config_safety`,
`live_submit_enabled=false`, `real_submit_implemented=false`. Exit code 0.

FAIL if either sub-review fails or `--v2-approvals-review` flag is not given.

### Step 6b — V2 executor readiness review (after v2 approvals)

When v2 approval artifacts are supplied to `live_submit_executor_check` via
`--live-trading-approval` and `--live-order-submission-approval`, run the v2
executor readiness review to confirm the v2 approval guards passed and the
executor reached `config_safety`:

```bash
python -m src.tools.live_v2_executor_readiness_review \
    --blocked-report output/live_submit_executor/live_submit_blocked_report.json
```

Expected result: `PASS` with `block_guard=config_safety`. Exit code 0.

FAIL if `block_guard` is `approval_artifact`, `v2_trading_approval`,
`v2_submission_approval`, or `v2_cross_check` — indicating a v2 artifact
was rejected before reaching the config-safety guard.

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
| `live_trading_approval` | Offline approval artifact CLI; produces `live_trading_approval.json`; `live_trading_approved=true`, `live_order_submission_approved=false`; `approval_scope=AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY`; requires `--risk-acknowledge`; never calls Alpaca; never submits orders |
| `live_order_submission_approval` | Offline approval artifact CLI; reads `live_trading_approval.json`; produces `live_order_submission_approval.json`; `live_order_submission_approved=true`, `order_submission_approval_for_single_attempt=true`; validates trading approval fields and symbol/notional match; separate artifact from trading approval; never calls Alpaca; never submits orders |
| `live_v2_approvals_review` | Offline review of both v2 approval artifacts; PASS only if both artifacts are consistent, separate, correctly scoped, symbols match, and submission notional ≤ trading notional; never writes files; never calls Alpaca |
| `live_submit_executor` v2 guards | `maybe_execute_live_submit()` accepts optional `live_trading_approval_path` and `live_order_submission_approval_path`; validates both v2 artifacts (symbol, notional cap, scope, risk_acknowledged) after existing approval_artifact guard; omitting v2 paths preserves original behavior; all exit paths still `blocked=true`; `submit_order` never called |
| `live_v2_executor_readiness_review` | Offline review of executor blocked report after v2 approvals are provided; PASS only if `blocked=true`, `submit_order_called=false`, `block_guard=config_safety`, and violations include at least one default config-safety flag; FAIL if any v2 approval guard blocked; never writes files; never calls Alpaca |
| `live_v2_final_readiness_review` | Offline combined summary CLI; runs v2 approvals review and executor readiness review together; PASS only when both sub-reviews pass; writes `live_v2_final_readiness_review.json` with `review_result`, `final_blocker`, `live_submit_enabled=false`, `real_submit_implemented=false`; never calls Alpaca; never submits orders |
| `live_v2_readiness_bundle` | Offline bundle runner; runs all three v2 reviews in sequence and writes all artifacts under `--output-dir` including a top-level `live_v2_readiness_bundle.json` audit summary; always writes all available artifacts regardless of earlier failures; exit 0 only on final PASS; never calls Alpaca; never submits orders |
| `live_submit_enablement_gate` | Offline GO/NO_GO gate checker; reads readiness bundle, both v2 approval artifacts, and executor blocked report; GO only when all conditions are satisfied and `config_safety` is the only remaining blocker; GO does not submit an order; writes `live_submit_enablement_gate.json`; exit 0 on GO, exit 1 on NO_GO; never calls Alpaca; never reads credentials; never submits orders |
| `live_pre_submit_ledger_dry_run` | Offline pre-submit ledger dry-run writer; requires enablement gate decision="GO"; validates symbol, side (buy only), notional > 0, and client_order_id uniqueness; appends one ledger row with `status="attempting"` to live submit ledger CSV; proves required pre-submit ledger row can be written; does not submit orders; does not make broker calls; future real submit must update the same `client_order_id` row; exit 0 on `LEDGER_DRY_RUN_WRITTEN`, exit 1 on `BLOCKED` |
| `live_post_submit_ledger_update_dry_run` | Offline post-submit ledger update dry-run tool; reads existing ledger; finds exactly one `attempting` row matching `client_order_id`; updates it with hypothetical outcome (`submitted`, `rejected`, `exception`); rewrites CSV in place preserving all other rows and exact schema; proves the pre-submit row can be updated after a hypothetical outcome; does not submit orders; does not make broker calls; future real submit must update the same `client_order_id` row with actual `broker_order_id`, `error`, and outcome; exit 0 on `LEDGER_DRY_RUN_UPDATED`, exit 1 on `BLOCKED` |
| `live_ledger_verify` (with `--output`) | Offline live submit ledger verifier; validates dry-run submit ledger CSV against LEDGER_COLUMNS schema; checks non-empty unique client_order_id, valid status, submitted rows have broker_order_id and no error, rejected/exception rows have error; without `--allow-attempting` any attempting row is a violation; future real submit must leave PASS after every attempt; exit 0 on PASS, exit 1 on FAIL; always writes output JSON; never calls Alpaca |

---

## Live Submit Design

The proposed live submit architecture is documented in
**[docs/live_submit_design.md](live_submit_design.md)**.

The design covers the full proposed submit flow (steps 1–12), hard safety
constraints, required implementation components, rollback procedures, and
open questions.  **Live submit is not implemented.** The design document is
for planning purposes only — no `submit_order` call exists in the codebase.

The next-phase enablement design — required approvals, config changes, runtime
guards, ledger behavior, and first-submit constraints — is documented in
**[docs/live_submit_enablement_v2.md](live_submit_enablement_v2.md)**.
The exact conditions that must all be satisfied before the `config_safety`
blocker can be removed are defined in
**[docs/live_submit_enablement_gate.md](live_submit_enablement_gate.md)**.

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

---

## Milestone: Live V2 Enablement + Ledger Dry-Run Complete

### Recommended git tag

```
live-v2-enable-gate-ledger-dryrun-complete
```

### What this milestone means

| Item | State |
|------|-------|
| `live_v2_readiness_bundle` | `bundle_result="PASS"` |
| `live_submit_enablement_gate` | Decision: `GO` |
| `live_pre_submit_ledger_dry_run` | Result: `LEDGER_DRY_RUN_WRITTEN` |
| `live_ledger_verify --allow-attempting` | Result: `PASS` |
| `live_post_submit_ledger_update_dry_run` | Result: `LEDGER_DRY_RUN_UPDATED` |
| Final `live_ledger_verify` | Result: `PASS` |
| Current hard blocker | `config_safety` (`live_trading_enabled=false`, `live_submit_dry_run=true`, `live_kill_switch_enabled=true`) |
| `submit_order` | Unreachable — no call path exists in current codebase |
| Real live submit | **Not implemented** |
| Alpaca live endpoint | Not called |
| Credentials | Not read |
| Real order submitted | **No** |

The GO decision from `live_submit_enablement_gate` means only that all documented
preconditions are satisfied and `config_safety` is the sole remaining blocker.
It does not submit an order and does not authorize live trading.
The operator must still explicitly set `live_trading_enabled=true`,
`live_submit_dry_run=false`, and `live_kill_switch_enabled=false` in a local config
before any real order attempt can be made — and those changes are not made here.

### Verification command sequence

```bash
# 1. Generate v2 approval artifacts (offline — no Alpaca calls)
python -m src.tools.live_trading_approval \
    --operator-name "operator" \
    --approval-note "Authorizing live trading mode only" \
    --risk-acknowledge \
    --output output/live_trading_approval.json

python -m src.tools.live_order_submission_approval \
    --operator-name "operator" \
    --approval-note "Authorizing single live order attempt" \
    --risk-acknowledge \
    --source-live-trading-approval output/live_trading_approval.json \
    --output output/live_order_submission_approval.json

# 2. Run executor readiness check (produces blocked_report)
python -m src.tools.live_submit_executor_check \
    --live-trading-approval output/live_trading_approval.json \
    --live-order-submission-approval output/live_order_submission_approval.json \
    --output output/live_submit_executor/live_submit_blocked_report.json

# 3. Build v2 readiness bundle
python -m src.tools.live_v2_readiness_bundle \
    --live-trading-approval output/live_trading_approval.json \
    --live-order-submission-approval output/live_order_submission_approval.json \
    --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \
    --output-dir output/live_v2_bundle

# 4. Run enablement gate (GO/NO_GO)
python -m src.tools.live_submit_enablement_gate \
    --readiness-bundle output/live_v2_bundle/live_v2_readiness_bundle.json \
    --live-trading-approval output/live_trading_approval.json \
    --live-order-submission-approval output/live_order_submission_approval.json \
    --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \
    --output output/live_submit_enablement_gate.json
# Expected: decision="GO", exit 0

# 5. Pre-submit ledger dry-run
python -m src.tools.live_pre_submit_ledger_dry_run \
    --enablement-gate output/live_submit_enablement_gate.json \
    --symbol SPY \
    --side buy \
    --notional 100.0 \
    --client-order-id LIVE-TEST-000001 \
    --ledger output/live_submit_ledger.csv \
    --output output/live_pre_submit_ledger_dry_run.json
# Expected: result="LEDGER_DRY_RUN_WRITTEN", exit 0

# 6. Verify ledger with attempting row allowed
python -m src.tools.live_ledger_verify \
    --ledger output/live_submit_ledger.csv \
    --output output/live_ledger_verify.json \
    --allow-attempting
# Expected: result="PASS", exit 0

# 7. Post-submit ledger update dry-run (simulate submitted outcome)
python -m src.tools.live_post_submit_ledger_update_dry_run \
    --ledger output/live_submit_ledger.csv \
    --client-order-id LIVE-TEST-000001 \
    --outcome submitted \
    --broker-order-id ALPACA-ORDER-123 \
    --output output/live_post_submit_ledger_update_dry_run.json
# Expected: result="LEDGER_DRY_RUN_UPDATED", exit 0

# 8. Final ledger verify (no attempting rows)
python -m src.tools.live_ledger_verify \
    --ledger output/live_submit_ledger.csv \
    --output output/live_ledger_verify.json
# Expected: result="PASS", exit 0
```

### Safety invariants confirmed at this milestone

- `submit_order` was never called during any of the above commands
- No Alpaca endpoint was contacted
- No credentials were read
- No real order was submitted
- The live ledger CSV is a dry-run artifact only (`status` transitions: `attempting` → `submitted`)
- `config_safety` remains the final blocker; the three config flags retain their safe defaults

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> GO from `live_submit_enablement_gate` is a precondition check only.
> Real live trading requires explicitly overriding `live_trading_enabled`,
> `live_submit_dry_run`, and `live_kill_switch_enabled` in a local operator config —
> changes that are not made here and must not be made in `settings.yaml`.

---

## Milestone: Pre-Broker Live Readiness Guards Complete

### Recommended git tag

```
pre-broker-live-readiness-guards-complete
```

### What this milestone means

| Item | State |
|------|-------|
| v2 approvals | Complete |
| Enablement gate | Complete — `decision="GO"` |
| Ledger dry-run lifecycle | Complete — pre-submit write → verify → post-submit update → final verify |
| `live_operator_config_override_review` | Complete — `result="PASS"` |
| `live_credential_presence_guard` | Complete — `result="PASS"` |
| Broker live API preflight | **Not implemented** |
| Real submit | **Not implemented** |
| `submit_order` | Unreachable — no call path exists in current codebase |
| `config_safety` | Still the hard blocker |
| Alpaca live endpoint called by new guards | **No** |
| Credentials read or printed by new guards | **No** |
| Real order submitted | **No** |

### New guards completed in this phase

#### `live_operator_config_override_review`

Offline, read-only validator for a manually produced local operator override
artifact.  Requires strict JSON boolean acknowledgements (Python `is True` /
`is False` exactly — string values like `"true"`, `"false"`, `"1"` are
rejected).

| Property | Value |
|----------|-------|
| Calls Alpaca | No |
| Reads credentials | No |
| Calls `submit_order` or `cancel_order` | No |
| Writes live ledger | No |
| Removes `config_safety` on PASS | No |
| Approves real trading on PASS | No |

Required acknowledgements in the artifact:
- `config_safety_acknowledged: true` (JSON boolean exactly)
- `submit_order_unreachable_acknowledged: true` (JSON boolean exactly)
- `real_live_submit_unimplemented_acknowledged: true` (JSON boolean exactly)
- `recurring_trading_approved: false` (JSON boolean exactly, field must be present)
- `automated_trading_approved: false` (JSON boolean exactly, field must be present)
- `symbol: "SPY"`, `side: "buy"`, `notional_cap` in (0, 100.0]
- `approval_scope: "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"`
- Non-empty `operator_name` and `approval_note`

```bash
python -m src.tools.live_operator_config_override_review \
    --override-artifact output/live_operator_config_override.json \
    --output output/live_operator_config_override_review.json
# Expected: result="PASS", exit 0
```

#### `live_credential_presence_guard`

Offline, read-only presence checker.  Validates env var name format and checks
only that required variables exist and are non-empty.  Never reads, stores, or
exposes actual credential values.

| Property | Value |
|----------|-------|
| Validates credentials against Alpaca | No |
| Connects to Alpaca | No |
| Instantiates broker client | No |
| Exposes credential values | No |
| Calls `submit_order` or `cancel_order` | No |
| Writes live ledger | No |
| Removes `config_safety` on PASS | No |
| Approves real trading on PASS | No |

Key safety detail: `--required-env` arguments are validated against
`^[A-Z_][A-Z0-9_]*$` before any `os.environ` lookup.  Invalid names
(e.g. a shell-expanded secret value such as `sk-live-abc123`) are
sanitized to `<invalid-env-key>` in all output and never passed to
`os.environ.get()`.  `redacted_preview` is always the fixed literal
`"<redacted>"`, never derived from the actual secret.

```bash
python -m src.tools.live_credential_presence_guard \
    --required-env ALPACA_LIVE_API_KEY \
    --required-env ALPACA_LIVE_SECRET_KEY \
    --output output/live_credential_presence_guard.json
# Expected: result="PASS", exit 0
```

### Current state after this phase

| Component | Status |
|-----------|--------|
| v2 approvals (`live_trading_approval` + `live_order_submission_approval`) | Complete |
| `live_v2_approvals_review` | Complete |
| `live_v2_executor_readiness_review` | Complete |
| `live_v2_final_readiness_review` | Complete |
| `live_v2_readiness_bundle` | Complete |
| `live_submit_enablement_gate` | Complete |
| Ledger dry-run lifecycle | Complete |
| `live_operator_config_override_review` | Complete |
| `live_credential_presence_guard` | Complete |
| Broker live API preflight | **Not implemented** |
| Real submit | **Not implemented** |
| `submit_order` | Unreachable |
| `config_safety` | Still the hard blocker |

### Safety invariants confirmed at this milestone

- `submit_order` was never called by any guard in this phase
- No Alpaca endpoint was contacted by any guard in this phase
- No credential values were read, stored, printed, or written to any artifact
- No real order was submitted
- `config_safety` remains the final blocker with safe defaults:
  `live_trading_enabled=false`, `live_submit_dry_run=true`, `live_kill_switch_enabled=true`

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **Broker live API preflight is not implemented.**
> PASS from any guard in this phase is a precondition check only.
> Real live trading requires a funded account, a broker API preflight,
> and explicitly overriding the three `config_safety` flags in a local
> operator config — changes that must not be made in `settings.yaml`.

---

## Milestone: Broker Preflight Mock-Only Core Complete

### Recommended git tag

```
live-broker-preflight-readonly-core-mock-complete
```

### What this milestone means

| Item | State |
|------|-------|
| v2 approvals | Complete |
| Enablement gate | Complete |
| Ledger dry-run lifecycle | Complete |
| `live_operator_config_override_review` | Complete |
| `live_credential_presence_guard` | Complete |
| Broker preflight design | Complete — `docs/live_broker_preflight_design.md` |
| `live_broker_preflight_readonly` mock-only core | Complete |
| `AlpacaLiveReadOnlyBroker` real adapter | Complete — gated behind `--allow-live-broker-api-readonly` |
| Real broker preflight run | Not performed — requires explicit operator flag + live credentials |
| Real live submit | **Not implemented** |
| Automated live trading | **Not implemented** |
| `submit_order` | Unreachable — no call path exists in current codebase |
| `config_safety` | Still the hard blocker |
| Alpaca live endpoint called by this PR | **No** (tests use mocks only) |
| Credentials read or printed | **No** |
| Real order submitted | **No** |

### `live_broker_preflight_readonly` — current state

| Property | Value |
|----------|-------|
| CLI without `--allow-live-broker-api-readonly` | `result="BLOCKED"` — "live broker API access not enabled"; zero broker calls |
| CLI with flag + valid artifacts/params + credentials | Runs real read-only checks via `AlpacaLiveReadOnlyBroker`; PASS or BLOCKED |
| CLI with flag but missing/non-PASS artifacts | `result="BLOCKED"` — fails before env var read; zero broker calls |
| CLI with flag but invalid parameters | `result="BLOCKED"` — fails before env var read; zero broker calls |
| CLI with flag but missing credentials | `result="BLOCKED"` — env vars read after validation, adapter not constructed; zero broker calls |
| Alpaca SDK import | Lazy — inside `AlpacaLiveReadOnlyBroker.__init__` only; no module-level import |
| `requests` / `httpx` / `aiohttp` / `urllib.request` imported | No |
| `submit_order` reference | Absent — source-scanned in tests |
| `cancel_order` / `replace_order` reference | Absent |
| Orders endpoint (`/v2/orders`) | Not used — source-scanned in tests |
| POST / PATCH / DELETE methods | Not used — source-scanned in tests |
| Endpoint allowlist enforced | Yes — exact-match `/v2/account`, `/v2/clock`; prefix-match `/v2/assets/` |
| Broker exception messages in output | No — redacted to `"details redacted"` in all output fields |
| Writes live ledger | No |
| Removes `config_safety` | No |
| Approves real trading | No |

### Hardcoded output invariants (every result, PASS or BLOCKED)

| Field | Invariant value |
|-------|----------------|
| `broker_mutation_calls_made` | `false` always |
| `credential_values_exposed` | `false` always |
| `live_submit_enabled` | `false` always |
| `real_submit_implemented` | `false` always |
| `submit_order_reachable` | `false` always |
| `config_safety_still_blocks` | `true` always |
| `broker_calls_readonly` | `true` always |

### Fail-closed conditions confirmed working in mock tests

- Missing or malformed prerequisite artifact → BLOCKED before any broker call
- Prerequisite artifact `result != "PASS"` → BLOCKED before any broker call
- Symbol not `SPY` → BLOCKED
- Side not `buy` → BLOCKED
- `notional_cap > 100.0` → BLOCKED
- Account status not `"ACTIVE"` → BLOCKED
- Insufficient buying power → BLOCKED
- SPY not tradable → BLOCKED
- Any broker exception → BLOCKED, exception message redacted from all output
- Disallowed endpoint path → BLOCKED (allowlist enforced before request)

### Test coverage

107 unit tests in `tests/test_live_broker_preflight_readonly.py`.
All tests use a mock broker or mock `TradingClient` — no real Alpaca calls in any test.

### CLI (future — currently always BLOCKED)

```bash
python -m src.tools.live_broker_preflight_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --side buy \
    --notional-cap 100.0 \
    --output output/live_broker_preflight_readonly.json
```

Exit 0 on PASS; exit 1 on BLOCKED. Always writes output JSON.
Currently exits 1 — real Alpaca adapter not yet implemented.

### CLI (with real adapter)

```bash
python -m src.tools.live_broker_preflight_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --side buy \
    --notional-cap 100.0 \
    --output output/live_broker_preflight_readonly.json \
    --allow-live-broker-api-readonly
```

Requires `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` to be set.
Exit 0 on PASS; exit 1 on BLOCKED. Always writes output JSON.
Without `--allow-live-broker-api-readonly`, exits 1 with zero broker calls.

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **No real Alpaca endpoint was called in this PR (tests use mocks only).**
> The `--allow-live-broker-api-readonly` flag is required for any live API
> contact. A PASS from this tool is a precondition check only; it does not
> remove `config_safety` and does not authorize a live order.
> Real live trading requires a funded account, a PASS from this tool with
> live credentials, and explicitly overriding the three `config_safety` flags
> in a local operator config — changes that must not be made in `settings.yaml`.

---

## Milestone: Live Broker Preflight Read-Only Adapter Complete

### Recommended git tag

```
live-broker-preflight-readonly-adapter-complete
```

### What this milestone means

| Item | State |
|------|-------|
| v2 approvals | Complete |
| Enablement gate | Complete |
| Ledger dry-run lifecycle | Complete |
| `live_operator_config_override_review` | Complete |
| `live_credential_presence_guard` | Complete |
| Broker preflight design | Complete — `docs/live_broker_preflight_design.md` |
| `live_broker_preflight_readonly` mock-only core | Complete |
| `AlpacaLiveReadOnlyBroker` real adapter (PR #112) | **Complete — gated behind `--allow-live-broker-api-readonly`** |
| Real broker preflight run | **Not performed** — requires `--allow-live-broker-api-readonly` + live credentials |
| Real live submit | **Not implemented** |
| Automated live trading | **Not implemented** |
| `submit_order` / `cancel_order` / `replace_order` | Absent — no live call path exists |
| `config_safety` | **Still the hard blocker** |
| Alpaca live endpoint called by tests or this PR | **No** — tests use mocks only |
| Credentials read, stored, or printed | **No** |
| Real order submitted | **No** |
| Live ledger written | **No** |
| Orders endpoint / POST / PATCH / DELETE used | **No** |
| `config_safety` bypassed | **No** |

### What the `AlpacaLiveReadOnlyBroker` adapter provides

`AlpacaLiveReadOnlyBroker` wraps `alpaca-py` `TradingClient(paper=False)` and
exposes exactly three read-only SDK methods:

- `get_account()` — account status, buying power, pattern-day-trader flag
- `get_clock()` — market open/closed state
- `get_asset(symbol)` — tradability check for SPY

No write or mutation methods are used. `submit_order`, `cancel_order`, and
`replace_order` are absent from the adapter source. The orders endpoint
(`/v2/orders`) is never referenced. No POST/PATCH/DELETE calls exist.

The adapter enforces a strict call sequence before any env var is read or
`TradingClient` is constructed: credential guard artifact must exist with
`result="PASS"`, operator override artifact must exist with `result="PASS"`,
`symbol` must be `"SPY"`, `side` must be `"buy"`,
`0 < notional_cap ≤ 100.0`, and `--allow-live-broker-api-readonly` must be
present. Any failure before that point returns BLOCKED immediately with
`broker_calls_made=false`.

Exception details from broker calls are redacted — raw exception text never
appears in output JSON, `violations`, `blocker`, or stdout.

Alpaca SDK import is lazy: `from alpaca.trading.client import TradingClient`
is inside `__init__` only — no module-level Alpaca import.

### PASS from read-only preflight is not approval to trade

A PASS result from `live_broker_preflight_readonly` is a **precondition check
only**. It does NOT:

- Remove or weaken `config_safety`
- Enable live trading
- Authorize a live order or any order submission
- Bypass any existing guard

The `config_safety` guard remains the final blocker. Real live trading still
requires all three config flags to be explicitly overridden in a local operator
config (`live_trading_enabled=true`, `live_submit_dry_run=false`,
`live_kill_switch_enabled=false`) — changes that must not be made in
`settings.yaml`.

### No live API call was made

- No live API call was made by tests or by this PR.
- Tests use mocks only; the real adapter is exercised only under a mock
  `TradingClient` — no real Alpaca network contact in any test.
- The `--allow-live-broker-api-readonly` flag is required for any real API
  contact. Without the flag: `result="BLOCKED"`, `broker_calls_made=false`.

### Test coverage (PR #112)

121 targeted tests in `tests/test_live_broker_preflight_readonly.py`.
Full suite: 3,380 tests passed.
All tests use a mock broker or mock `TradingClient` — no real Alpaca calls.

### Safety invariants confirmed at this milestone

- `submit_order` was never called during any test or by any part of this PR
- No Alpaca live endpoint was contacted by tests or by this PR
- No credential values were read, stored, printed, or written to any artifact
- No real order was submitted
- No live ledger was written
- `config_safety` remains the final blocker with safe defaults:
  `live_trading_enabled=false`, `live_submit_dry_run=true`,
  `live_kill_switch_enabled=true`
- Source scan: `submit_order(`, `cancel_order(`, `replace_order(` are absent
  from the adapter source
- Source scan: orders endpoint, POST, PATCH, DELETE are not used

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **Real broker preflight has NOT been performed yet.**
> **No real Alpaca endpoint was called in this PR or by any test.**
> The `--allow-live-broker-api-readonly` flag is required for any live API contact.
> A PASS from `live_broker_preflight_readonly` is a precondition check only;
> it does not remove `config_safety` and does not authorize a live order.
> `submit_order`, `cancel_order`, and `replace_order` remain unimplemented for live.
> No orders endpoint, POST/PATCH/DELETE, live ledger writes, or `config_safety`
> bypass exist in the current codebase.
> Real live trading requires a funded account, a PASS from this tool with live
> credentials, and explicitly overriding the three `config_safety` flags in a
> local operator config — changes that must not be made in `settings.yaml`.

---

## Milestone: Live Read-Only Broker Preflight PASS Observed

### Recommended git tag

```
live-readonly-preflight-pass-observed
```

### What this milestone means

| Item | State |
|------|-------|
| v2 approvals | Complete |
| Enablement gate | Complete |
| Ledger dry-run lifecycle | Complete |
| `live_operator_config_override_review` | Complete |
| `live_credential_presence_guard` | Complete |
| Broker preflight design | Complete |
| `live_broker_preflight_readonly` mock-only core | Complete |
| `AlpacaLiveReadOnlyBroker` real adapter | Complete |
| Manual live read-only preflight run | **PASS observed** |
| Account status check | PASS — account active |
| Market clock check | PASS — market open |
| SPY asset check | PASS — tradable and fractionable |
| Real live submit | **Not implemented** |
| Automated live trading | **Not implemented** |
| `submit_order` / `cancel_order` / `replace_order` | Absent — no live call path |
| `config_safety` | **Still the hard blocker** |
| Order submitted | **No** |
| Live ledger written | **No** |
| Credential values exposed | **No** |
| `config_safety` bypassed | **No** |

### Read-only checks confirmed (PASS run)

Three read-only GET calls were made via `AlpacaLiveReadOnlyBroker`:

| Call | Endpoint | Result |
|------|----------|--------|
| `get_account()` | `GET /v2/account` | PASS — account active |
| `get_clock()` | `GET /v2/clock` | PASS — market open |
| `get_asset("SPY")` | `GET /v2/assets/SPY` | PASS — tradable and fractionable |

No other endpoints were contacted. No POST/PATCH/DELETE. No orders endpoint.

### Output invariants confirmed

All seven hardcoded invariants held in the PASS run:

| Field | Required | Observed |
|-------|---------|---------|
| `broker_mutation_calls_made` | `false` | `false` ✓ |
| `credential_values_exposed` | `false` | `false` ✓ |
| `live_submit_enabled` | `false` | `false` ✓ |
| `real_submit_implemented` | `false` | `false` ✓ |
| `submit_order_reachable` | `false` | `false` ✓ |
| `config_safety_still_blocks` | `true` | `true` ✓ |
| `broker_calls_readonly` | `true` | `true` ✓ |

### PASS is not approval to trade

PASS from `live_broker_preflight_readonly` confirms the broker API was
reachable and the account/clock/SPY checks passed at the time of the run.
It does NOT:

- Remove or weaken `config_safety`
- Enable live trading
- Authorize any live order submission
- Bypass any existing guard

`config_safety` remains the final blocker. No order submission path exists
in the current codebase.

### Sensitive data excluded

The following were not committed and do not appear in this doc or any PR artifact:

- Raw `output/live_broker_preflight_readonly.json`
- Account ID or account number
- Credential fragments
- Exact buying power or portfolio balance values
- Any other sensitive account metadata

Full non-sensitive snapshot: [docs/live_readonly_preflight_result_snapshot.md](live_readonly_preflight_result_snapshot.md)

### Safety invariants confirmed at this milestone

- `submit_order` was never called
- No order was submitted
- No order was cancelled or replaced
- No live ledger was written
- No credential values were exposed, stored, or printed
- `config_safety` was not bypassed or removed
- No POST/PATCH/DELETE broker calls were made
- The orders endpoint (`/v2/orders`) was not contacted

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **PASS is a precondition check only — not approval to submit a live order.**
> `config_safety` remains the hard blocker.
> `submit_order`, `cancel_order`, and `replace_order` remain unimplemented for live.
> No orders endpoint, POST/PATCH/DELETE, live ledger writes, or `config_safety`
> bypass exist in the current codebase.
> Real live trading requires a funded account, explicit operator config overrides
> in a local config (not `settings.yaml`), and a dedicated reviewed PR for
> real live submission — none of which are done here.

---

## Milestone: Single Submit Approval Review Complete

### Recommended git tag

```
live-single-submit-approval-review-complete
```

### What this milestone means

| Item | State |
|------|-------|
| v2 approvals | Complete |
| Enablement gate | Complete |
| Ledger dry-run lifecycle | Complete |
| `live_operator_config_override_review` | Complete |
| `live_credential_presence_guard` | Complete |
| Broker preflight design | Complete |
| `live_broker_preflight_readonly` mock-only core | Complete |
| `AlpacaLiveReadOnlyBroker` real adapter | Complete |
| Manual live read-only preflight run | PASS observed |
| Single submit attempt design | Complete — `docs/single_manual_live_submit_attempt_design.md` |
| `live_single_submit_approval_review` | **Complete** — offline read-only approval review |
| Real live submit | **Not implemented** |
| Automated live trading | **Not implemented** |
| `submit_order` / `cancel_order` / `replace_order` | Absent — no live call path |
| `config_safety` | **Still the hard blocker** |
| Alpaca endpoint called by this tool | **No** |
| Credentials read | **No** |
| Live ledger written | **No** |
| `config_safety` bypassed | **No** |

### `live_single_submit_approval_review` — what it does

`src/tools/live_single_submit_approval_review.py` is an offline, read-only CLI
that validates a local operator approval artifact for exactly one future single
live SPY market buy attempt.

| Property | Value |
|----------|-------|
| Calls Alpaca | No |
| Imports Alpaca SDK | No |
| Imports network libraries (requests/httpx/aiohttp/urllib.request) | No |
| Reads credentials | No |
| Calls `submit_order` / `cancel_order` / `replace_order` | No |
| Writes live ledger | No |
| Removes `config_safety` on PASS | No |
| Approves real trading on PASS | No |
| Approves automated or recurring trading on PASS | No |
| `run_review` raises | Never |

### Validation behavior

| Field | Required value |
|-------|---------------|
| `symbol` | Exactly `"SPY"` — no case folding, no whitespace stripping |
| `side` | Exactly `"buy"` — no case folding, no whitespace stripping |
| `order_type` | Exactly `"market"` — no case folding, no whitespace stripping |
| `notional_cap` | Numeric (not string/bool/null), `> 0` and `≤ 100.0` |
| `approval_scope` | Exactly `"AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"` |
| `recurring_trading_approved` | Strict JSON `false` only |
| `automated_trading_approved` | Strict JSON `false` only |
| `one_attempt_only_acknowledged` | Strict JSON `true` only |
| `config_safety_override_is_local_only_acknowledged` | Strict JSON `true` only |
| `no_cancel_replace_acknowledged` | Strict JSON `true` only |
| `live_broker_preflight_pass_confirmed` | Strict JSON `true` only |
| `approved_at_utc` / `approval_expires_at_utc` | Valid ISO-8601; expires > approved |
| Expiry | BLOCKED if current UTC ≥ `approval_expires_at_utc` |
| `operator_name` / `approval_note` / `operator_initials` | Non-empty strings |

Raw invalid values for all string fields (`symbol`, `side`, `order_type`,
`approval_scope`) and all strict-boolean fields are **never echoed** in
violations, blocker, stdout, or output JSON. Fixed safe messages are used
instead. Output fields are populated only when the raw value exactly matches
the required canonical value — otherwise `null`.

### Hardcoded output invariants (every result, PASS or BLOCKED)

| Field | Invariant value |
|-------|----------------|
| `live_submit_enabled` | `false` always |
| `real_submit_implemented` | `false` always |
| `submit_order_reachable` | `false` always |
| `broker_calls_made` | `false` always |
| `credentials_read` | `false` always |
| `broker_mutation_calls_made` | `false` always |
| `live_ledger_written` | `false` always |
| `cancel_order_called` | `false` always |
| `replace_order_called` | `false` always |
| `automated_trading_enabled` | `false` always |
| `recurring_trading_enabled` | `false` always |
| `credential_values_exposed` | `false` always |

### PASS does not submit an order

PASS from `live_single_submit_approval_review` confirms the artifact is
present, structurally valid, correctly scoped, not expired, and contains
all required explicit acknowledgements. It does NOT:

- Submit any order
- Remove or weaken `config_safety`
- Enable live trading
- Approve automated or recurring trading
- Bypass any existing guard

`config_safety` remains the final blocker. No live order submission path
exists in the current codebase.

### Test coverage (PR #117)

196 tests in `tests/test_live_single_submit_approval_review.py`.
Full suite: 3,576 tests passed.
All tests are offline — no real Alpaca calls, no credentials read.

### Safety invariants confirmed at this milestone

- `submit_order` was never called by this tool or any test
- No Alpaca endpoint was contacted
- No credential values were read, stored, or printed
- No live ledger was written
- `config_safety` was not bypassed or removed
- Raw invalid field values are never echoed in any output

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **PASS from `live_single_submit_approval_review` does not submit an order.**
> Real live submit remains unimplemented.
> `config_safety` remains the hard blocker.
> `submit_order`, `cancel_order`, and `replace_order` remain unimplemented for live.
> No orders endpoint, POST/PATCH/DELETE, live ledger writes, or `config_safety`
> bypass exist in the current codebase.

---

## Milestone: Single Manual Submit Mock-Only Core Complete

### Recommended git tag

```
live-single-manual-submit-mock-core-complete
```

### What this milestone means

| Item | State |
|------|-------|
| v2 approvals | Complete |
| Enablement gate | Complete |
| Ledger dry-run lifecycle | Complete |
| `live_operator_config_override_review` | Complete |
| `live_credential_presence_guard` | Complete |
| Broker preflight design | Complete |
| `live_broker_preflight_readonly` mock-only core | Complete |
| `AlpacaLiveReadOnlyBroker` real adapter | Complete |
| Manual live read-only preflight run | PASS observed |
| Single submit attempt design | Complete |
| `live_single_submit_approval_review` | Complete — 196 tests |
| `live_single_manual_submit` mock-only core | **Complete** — 193 tests |
| Real live submit adapter | **Not implemented** — CLI always BLOCKED |
| Automated live trading | **Not implemented** |
| Recurring live trading | **Not implemented** |
| Retry logic on submit failure | **Not implemented** — single attempt only |
| `cancel_order` / `replace_order` | Absent — no call path exists |
| `config_safety` | **Still the hard blocker** |
| Alpaca endpoint called by this tool | **No** |
| Credentials read | **No** |
| Real order submitted | **No** |
| Live ledger written (in production) | **No** — only via mock broker in unit tests |
| `config_safety` bypassed | **No** |

### `live_single_manual_submit` — what it does (mock-only core)

`src/tools/live_single_manual_submit.py` implements the complete gate
sequence for a future one-time single live SPY buy attempt, with a
`broker=None` placeholder instead of a real Alpaca submit adapter.

| Property | Value |
|----------|-------|
| CLI result | Always `BLOCKED` — "real live submit adapter not implemented" |
| SUBMITTED result | Reachable only via injected mock broker in unit tests |
| Calls Alpaca | No |
| Imports Alpaca SDK | No |
| Imports network libraries | No |
| Reads credentials | No |
| Calls `cancel_order` / `replace_order` | No |
| Retries failed submit | No |
| Removes `config_safety` | No |
| `run_submit` raises | Never |

### Gates implemented (all must pass before broker call)

| Gate | Blocker on failure |
|------|--------------------|
| Four prerequisite artifacts with `result="PASS"` | BLOCKED, no ledger, no broker |
| `symbol` exactly `"SPY"` | BLOCKED, no ledger, no broker |
| `side` exactly `"buy"` | BLOCKED, no ledger, no broker |
| `order_type` exactly `"market"` | BLOCKED, no ledger, no broker |
| `notional_cap` in (0, 100.0] — not bool, not string | BLOCKED, no ledger, no broker |
| Local operator YAML `live_trading_enabled: true` (strict boolean) | BLOCKED, no ledger, no broker |
| Local operator YAML `live_submit_dry_run: false` (strict boolean) | BLOCKED, no ledger, no broker |
| Local operator YAML `live_kill_switch_enabled: false` (strict boolean) | BLOCKED, no ledger, no broker |
| `broker` not None | BLOCKED ("real live submit adapter not implemented") |

### State table

| State | `order_submitted` | `submit_order_called` | `broker_mutation_calls_made` |
|-------|------------------|-----------------------|-----------------------------|
| BLOCKED (pre-broker gates) | `false` | `false` | `false` |
| BLOCKED (`broker=None`) | `false` | `false` | `false` |
| SUBMITTED | `true` | `true` | `true` |
| BLOCKED (exception) | `false` | `true` | `false` |

### Hardcoded output invariants (every result)

| Field | Invariant value |
|-------|----------------|
| `cancel_order_called` | `false` always |
| `replace_order_called` | `false` always |
| `automated_trading_enabled` | `false` always |
| `recurring_trading_enabled` | `false` always |
| `credential_values_exposed` | `false` always |

### Test coverage

193 unit tests in `tests/test_live_single_manual_submit.py`.
Full suite: 3,769 tests passed.
All tests use a mock broker — no real Alpaca calls, no credentials read.

### Safety invariants confirmed at this milestone

- CLI always exits with BLOCKED — real submit adapter not implemented
- `submit_order` is called only via an injected mock in unit tests
- No Alpaca endpoint was contacted by any test or tool
- No credential values were read, stored, or printed
- No real order was submitted
- No live ledger was written in production — only in unit test tmp dirs
- `cancel_order` and `replace_order` are absent from the source
- No retry logic exists — a single attempt; BLOCKED is final
- Automated and recurring live trading are not implemented
- Raw invalid field values are never echoed in output
- `config_safety` was not bypassed or removed

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **CLI mode is always BLOCKED — real live submit adapter not implemented.**
> SUBMITTED is reachable only through an injected mock broker in unit tests.
> A real broker adapter must be implemented in a future PR before any
> live order can be submitted.
> `config_safety` remains the hard blocker.
> `cancel_order` and `replace_order` remain unimplemented for live.
> No retry logic exists — BLOCKED is a final result, no re-attempt.
> Automated and recurring live trading remain unimplemented.
> No orders endpoint, POST/PATCH/DELETE, or `config_safety` bypass exist
> in the current codebase.

---

## Milestone: Real Live Submit Adapter Complete (Mock-Only Tests)

### Recommended git tag

```
live-single-manual-submit-real-adapter-complete
```

### What this milestone means

| Item | State |
|------|-------|
| v2 approvals | Complete |
| Enablement gate | Complete |
| Ledger dry-run lifecycle | Complete |
| `live_operator_config_override_review` | Complete |
| `live_credential_presence_guard` | Complete |
| Broker preflight design | Complete |
| `live_broker_preflight_readonly` mock-only core | Complete |
| `AlpacaLiveReadOnlyBroker` real adapter | Complete |
| Manual live read-only preflight run | PASS observed |
| Single submit attempt design | Complete |
| `live_single_submit_approval_review` | Complete — 196 tests |
| `live_single_manual_submit` mock-only core | Complete — 193 tests |
| `AlpacaLiveSubmitBroker` real adapter | **Complete** — `TradingClient(paper=False)`, lazy SDK import |
| `--allow-real-live-submit-once` CLI flag | **Complete** — required; without it CLI is always BLOCKED |
| Tests for real adapter | **Complete** — 255 total tests, all mock-only |
| Real live order submitted | **No** — not submitted as part of this PR |
| Real Alpaca calls in tests | **No** — all tests use mock broker and mock `TradingClient` |
| Automated live trading | **Not implemented** |
| Recurring live trading | **Not implemented** |
| `cancel_order` / `replace_order` | Absent — not implemented |
| Retry logic | Absent — not implemented |
| `config_safety` | **Still required** — local operator config must override all three flags |

### `AlpacaLiveSubmitBroker` — what it does

`AlpacaLiveSubmitBroker` wraps `alpaca-py` `TradingClient(paper=False)` and
exposes exactly one method: `submit_order`.

| Property | Value |
|----------|-------|
| `TradingClient` mode | `paper=False` — real live account |
| SDK import | Lazy — inside `__init__` only; no module-level import |
| `submit_order` | Called exactly once per run; market buy SPY with notional |
| `client_order_id` | Generated and passed to order request if SDK supports it |
| `broker_order_id_redacted` | Always `"<redacted>"` in output — raw ID never written |
| Broker exception text | Redacted in all output fields, violations, blocker, stdout, ledger |
| `cancel_order` | Absent |
| `replace_order` | Absent |
| Retry logic | Absent |
| Credentials read | Only after all gates pass and `--allow-real-live-submit-once` is present |
| Orders endpoint | Not used except the single `submit_order` SDK call |
| POST/PATCH/DELETE | No direct HTTP mutation calls except via broker SDK submit |

### Gate sequence (all must pass before `AlpacaLiveSubmitBroker` is constructed)

| Gate | Blocker on failure |
|------|--------------------|
| Four prerequisite artifacts with `result="PASS"` | BLOCKED, no credential read, no broker |
| `symbol` exactly `"SPY"` | BLOCKED, no credential read, no broker |
| `side` exactly `"buy"` | BLOCKED, no credential read, no broker |
| `order_type` exactly `"market"` | BLOCKED, no credential read, no broker |
| `notional_cap` in (0, 100.0] — not bool, not string | BLOCKED, no credential read, no broker |
| Local operator YAML `live_trading_enabled: true` (strict boolean) | BLOCKED, no credential read, no broker |
| Local operator YAML `live_submit_dry_run: false` (strict boolean) | BLOCKED, no credential read, no broker |
| Local operator YAML `live_kill_switch_enabled: false` (strict boolean) | BLOCKED, no credential read, no broker |
| `--allow-real-live-submit-once` flag present | Without flag: BLOCKED ("real live submit adapter not implemented") |
| `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` non-empty in env | BLOCKED ("credentials not found in environment") |

### Test coverage (this PR)

255 unit tests in `tests/test_live_single_manual_submit.py`.
Full suite: 3,831 tests passed.
All tests use a mock broker or mock `TradingClient` — no real Alpaca calls in any test.

New test classes added:
- `TestRealAdapterFlagAbsent` — without flag, no credential read, no TradingClient, no submit
- `TestRealAdapterMissingCredentials` — with flag, missing env vars → BLOCKED before broker
- `TestRealAdapterGatesFail` — with flag, pre-gates fail → no TradingClient constructed
- `TestRealAdapterHappyPath` — with flag, all gates pass, mocked TradingClient → SUBMITTED, `paper=False`
- `TestRealAdapterOrderRequest` — order request has symbol=SPY, notional, side=BUY, time_in_force=DAY, client_order_id
- `TestRealAdapterExceptionRedaction` — broker exception → secret absent from all output
- `TestRealAdapterBrokerConstructionFails` — lazy SDK import / TradingClient construction raises → BLOCKED, details redacted, no ledger write, no submit call, `run_submit()` never raises
- `TestCLIRealFlag` — CLI with/without flag behavior via `main()`

Source scan tests updated:
- `test_no_alpaca_import` → `test_no_module_level_alpaca_import` (allows lazy imports inside methods)
- Added `test_no_retry_loop` — no `while True` or `for _ in range` in source
- Added `test_no_module_level_os_environ_get` — no module-level env reads

### Safety invariants confirmed at this milestone

- CLI without `--allow-real-live-submit-once` is always BLOCKED — tested
- Credentials are read only after all gates pass and the flag is present — tested
- `TradingClient` is constructed only after all gates pass — tested with `_TrackingClientCls`
- `submit_order` is called exactly once per run — tested
- No real order was submitted during this PR
- No real Alpaca endpoint was contacted by any test or by this PR
- No credential values were read, stored, printed, or written to any artifact in this PR
- `cancel_order` and `replace_order` are absent from the adapter source — source-scanned
- No retry loop exists in source — source-scanned
- Broker exception text is redacted in all output — tested with secret injection
- `broker_order_id_redacted` is always `"<redacted>"` — tested
- Pre-submit ledger row written before `submit_order`; updated after — tested
- `config_safety` was not bypassed — operator must explicitly override all three flags in local config

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **No real order has been submitted.**
> **All tests are mock-only — no real Alpaca calls in any test.**
> CLI requires `--allow-real-live-submit-once` plus all prerequisite artifacts
> with `result="PASS"`, strict local operator config booleans
> (`live_trading_enabled=true`, `live_submit_dry_run=false`, `live_kill_switch_enabled=false`),
> and valid credentials in `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`.
> Without the flag, CLI is always BLOCKED.
> `cancel_order` and `replace_order` remain unimplemented.
> No retry logic exists — BLOCKED is a final result.
> Automated and recurring live trading remain unimplemented.
> The operator must satisfy all gates, set the explicit flag, and provide valid
> credentials before a real order attempt can be made.
> `config_safety` overrides must be reset to safe defaults immediately after any attempt.

---

## Milestone: Real Submit Without Flag — BLOCKED Observed

**Branch:** `claude/docs-snapshot-real-submit-without-flag-blocked`
**Status:** Complete

Dry-run snapshot taken after PR #122 merged to `main`, confirming the
BLOCKED gate operates correctly without the `--allow-real-live-submit-once` flag.

**No real order was submitted.**
**No Alpaca endpoint was contacted.**
**No credential values were read or exposed.**
**No live ledger was written.**

### What was confirmed

| Run | Tool | Result |
|-----|------|--------|
| 1 | `live_single_submit_approval_review` | PASS |
| 2 | `live_single_manual_submit` (without `--allow-real-live-submit-once`) | BLOCKED |

Run 1 (`live_single_submit_approval_review`) confirmed that the approval artifact
is structurally valid, correctly scoped
(`approval_scope="AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"`), not expired,
and contains all required strict-boolean acknowledgements.

Run 2 (`live_single_manual_submit`) confirmed that even with all prerequisite
artifacts present (`result="PASS"`) and a valid local operator config, the CLI
returns BLOCKED with `blocker="real live submit adapter not implemented"` when
the `--allow-real-live-submit-once` flag is absent — before reading any
credentials or constructing any broker client.

### Safety invariants confirmed

| Invariant | Confirmed |
|-----------|----------|
| No real order submitted | ✓ |
| No Alpaca endpoint contacted | ✓ |
| No credential values read or exposed | ✓ |
| No live ledger written | ✓ |
| `submit_order_called=false` | ✓ |
| `broker_mutation_calls_made=false` | ✓ |
| `--allow-real-live-submit-once` required | ✓ — BLOCKED without it |

### Reference

- `docs/real_submit_without_flag_blocked_snapshot.md` — full snapshot document
- Suggested git tag: `real-submit-without-flag-blocked-observed`

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **No real order has been submitted.**
> The `--allow-real-live-submit-once` flag is required for any real submit
> attempt. Without it, CLI is always BLOCKED.
> A real order attempt additionally requires a funded account, fresh PASS
> runs of all four prerequisite tools, valid credentials in environment
> variables, and explicit local operator config overrides — none of which
> are committed to this repository.
