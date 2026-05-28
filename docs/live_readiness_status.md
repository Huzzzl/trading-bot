# Live Readiness Status

Current operational status of the live-readiness gate baseline.
Last updated: 2026-05-28. Full pre-submit pipeline complete through PR #98.
Refactor PRs: 9A (design), 9B (inventory tests), 9C (scripts/ README), 9E (permanent-tools confirmation) complete.

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

---

## Milestone: Final Real Submit Operator Runbook Prepared

**Branch:** `claude/docs-real-submit-final-operator-runbook`
**Status:** Complete

Final manual operator runbook created at
`docs/real_submit_final_operator_runbook.md`.

**No real order was submitted.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No live ledger was written.**

### What was added

The runbook defines the complete step-by-step procedure an operator must follow
before, during, and after one real live SPY market buy attempt using
`--allow-real-live-submit-once`. It covers:

1. All preconditions (repo state, artifact freshness, local config, env vars,
   order parameters, broker/market state)
2. Safety dry checks — run all four prerequisite tools and a submit dry check
   (without the flag) confirming BLOCKED before the real attempt
3. Final pre-submit human checklist — explicit per-item confirmation
4. The exact real submit command (marked DANGEROUS)
5. Expected SUBMITTED and BLOCKED output fields
6. Immediate post-run actions — clear credentials, reset config flags, verify
   ledger, check Alpaca UI
7. What to report back — non-sensitive summary fields only, what not to report
8. Abort conditions — full table of pre-submit and post-submit abort triggers

### Safety invariants confirmed

- This milestone does not run the submit tool
- No real order submitted
- No Alpaca endpoint contacted
- No credentials read or written
- No live ledger written
- No code changes

### Reference

- `docs/real_submit_final_operator_runbook.md` — full runbook
- Suggested git tag: `real-submit-final-operator-runbook-prepared`

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **No real order has been submitted.**
> The runbook is a preparation document only. A real order attempt requires the
> operator to satisfy all preconditions at runtime, run all prerequisite tools,
> pass all safety dry checks, confirm every item in the pre-submit checklist,
> and explicitly pass `--allow-real-live-submit-once` — none of which are
> performed by this PR.

---

## Milestone: First Real Live Submit — SUBMITTED Observed

**Branch:** `claude/docs-snapshot-first-real-live-submit-success`
**Status:** Complete

The first single manual real live SPY market buy attempt was executed and
returned `result="SUBMITTED"`.

### Result

| Field | Observed |
|-------|---------|
| `result` | `"SUBMITTED"` |
| `order_submitted` | `true` |
| `submit_order_called` | `true` |
| `broker_mutation_calls_made` | `true` |
| `cancel_order_called` | `false` |
| `replace_order_called` | `false` |
| `credential_values_exposed` | `false` |
| `live_ledger_written` | `true` |
| `blocker` | empty / null |
| `notional_cap` | `50.0` |
| `automated_trading_enabled` | `false` |
| `recurring_trading_enabled` | `false` |

### Post-run actions completed

- Credentials cleared from environment
- Local operator config reset to safe defaults (`live_trading_enabled=false`,
  `live_submit_dry_run=true`, `live_kill_switch_enabled=true`)
- Output artifact and ledger not committed to repository
- Order/fill status to be verified manually in Alpaca UI

### Safety invariants confirmed

- Exactly one `submit_order` call
- No cancel/replace/retry through code
- No automated or recurring trading
- Credential values not exposed in any output field
- Broker order ID redacted
- No raw artifacts, ledger, credentials, account/order IDs, fill details, or
  broker response details committed to this repository

### Reference

- `docs/first_real_live_submit_success_snapshot.md` — full snapshot document
- Suggested git tag: `first-real-live-submit-success-observed`

### Warning

> **This milestone documents a single completed manual attempt only.**
> **It does not approve future trading.**
> **It does not approve automated or recurring trading.**
> Any future live submit attempt requires fresh prerequisite artifacts,
> a fresh unexpired approval, fresh preflight, explicit local operator config,
> and `--allow-real-live-submit-once`. SUBMITTED and BLOCKED outcomes are
> final for that attempt — do not retry without a new approval artifact.
> Emergency cancel and replace remain manual via the Alpaca broker UI only.

---

## Milestone: Post-Submit Manual Position Handling Runbook Prepared

**Branch:** `claude/docs-post-submit-manual-position-handling-runbook`
**Status:** Complete

Post-submit operator runbook created at
`docs/post_submit_manual_position_handling_runbook.md`.

**No trade was executed by this PR.**
**No order was submitted, sold, cancelled, or replaced.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No code changes were made.**

### What was added

The runbook documents what the operator should do after a real live submit
returns `result="SUBMITTED"`, covering:

1. Immediate checks — credentials cleared, config reset, artifacts not
   committed, order status confirmed in Alpaca UI
2. Manual position decision — hold vs. close, bot makes no automatic decision
3. Manual sell / close process — Alpaca UI only, no code, no reuse of buy
   approval artifact
4. What not to do — no repeat submit, no automated sell, no
   cancel/replace/sell implementation in this PR
5. Future engineering options — manual sell approval flow, read-only position
   reconciliation tool, position status snapshot (none implemented here)
6. Warning — first buy success does not approve future trading; emergency
   actions remain manual

### Reference

- `docs/post_submit_manual_position_handling_runbook.md` — full runbook
- Suggested git tag: `post-submit-manual-position-handling-runbook-prepared`

### Warning

> **This milestone does not approve future trading.**
> **No order was submitted, sold, cancelled, or replaced by this PR.**
> The runbook is a preparation and guidance document only.
> Automated position management is not implemented.
> Any future buy or sell attempt requires fresh design, fresh approval,
> fresh preflight, and explicit operator action.
> Emergency actions remain manual via the Alpaca broker UI only.

---

## Milestone: Live Position Reconciliation Read-Only Tool — Design Complete

**Branch:** `claude/docs-design-live-position-reconciliation-readonly`
**Status:** Complete

Design document created at
`docs/live_position_reconciliation_readonly_design.md`.

**No code was implemented.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No trading action was performed.**

### What was designed

A future read-only tool (`live_position_reconciliation_readonly`) that:

- Inspects current live account positions and open orders for SPY only
- Requires `--allow-live-broker-api-readonly` flag and both prerequisite
  artifact gates before reading credentials or constructing a broker client
- Returns boolean flags only (`position_observed`, `open_order_observed`) —
  no fill price, quantity, account balance, account ID, order ID, or raw
  broker response in any output field
- Has no `submit_order`, `cancel_order`, or `replace_order` methods
- Writes output artifact always (PASS or BLOCKED); never raises

The design does not decide whether to hold or sell any position — that
remains a manual operator decision.

### Reference

- `docs/live_position_reconciliation_readonly_design.md` — full design document
- Suggested git tag: `live-position-reconciliation-readonly-design-complete`

### Warning

> **This milestone does not approve real trading.**
> **No code is implemented.**
> **No Alpaca endpoint was contacted.**
> **No credentials were read.**
> The reconciliation tool is read-only by design — it does not submit,
> cancel, replace, or decide on positions.
> Any future implementation requires fresh design review, mock-only tests,
> and explicit operator action before any live broker API call.

---

## Milestone: Live Position Reconciliation Read-Only — Mock-Only Core Complete

**PRs:** `add-live-position-reconciliation-readonly-mock-core` (#128), snapshot (#this)
**Status:** Complete

Mock-only core implemented and merged:
- `src/tools/live_position_reconciliation_readonly.py` — mock-only core (exists on `main`)
- `tests/test_live_position_reconciliation_readonly.py` — 66 tests (exists on `main`)

**Mock-only core is complete.**
**CLI always returns BLOCKED ("real broker adapter not implemented").**
**PASS is only reachable through an injected mock broker in unit tests.**
**Real Alpaca adapter is NOT implemented.**
**No Alpaca SDK imported.**
**No network requests made.**
**No credentials read on any code path.**
**No `os.environ` access anywhere in source.**
**No orders submitted, sold, cancelled, or replaced.**
**No live ledger written.**
**No config_safety mutated.**
**No automated position decision made.**
**`broker_calls_readonly` mirrors `broker_calls_made`.**
**Raw invalid input values are redacted — never echoed in output, violations, or stdout.**

### Test coverage

66 unit tests in `tests/test_live_position_reconciliation_readonly.py`.
Full suite: 3,897 tests passed.
All tests mock-only — no real Alpaca calls.

Test classes:
- `TestArtifactGates` (7) — missing/non-PASS credential guard and operator override → BLOCKED, no broker call
- `TestSymbolValidation` (4) — wrong symbol, lowercase, whitespace, empty → BLOCKED
- `TestInputSecretRedaction` (9) — secret in cg/oo result or symbol → absent from output, violations, blocker, stdout
- `TestBrokerNone` (6) — CLI broker=None → BLOCKED, no credentials read, `position_observed=null`, `open_order_observed=null`, `broker_calls_readonly=false`
- `TestHappyPath` (8) — injected mock broker → PASS with `position_observed`/`open_order_observed`, `broker_calls_readonly=true`
- `TestBrokerException` (7) — broker raises with secret string → BLOCKED, secret absent from output and stdout
- `TestOutputInvariants` (5) — mutation/credential/submit/cancel/replace fields always false; `broker_calls_readonly` mirrors `broker_calls_made`; broker_ids/account_identifiers always redacted
- `TestOutputAlwaysWritten` (3) — output artifact written for all BLOCKED paths via CLI
- `TestNeverRaises` (4) — `run_reconciliation()` never raises on any exception path
- `TestNoRawIds` (2) — no raw IDs or fill details in output JSON or stdout
- `TestSourceScans` (11) — no Alpaca/network imports, no `os.environ`, no `submit_order(`/`cancel_order(`/`replace_order(`, no POST/PATCH/DELETE, no ledger writes

### Safety invariants confirmed

- No Alpaca SDK imported — source-scanned
- No network imports — source-scanned
- No `os.environ` access at any level — source-scanned
- No `submit_order(`/`cancel_order(`/`replace_order(` in source — source-scanned
- No POST/PATCH/DELETE mutation markers in source — source-scanned
- No ledger writes in source — source-scanned
- No config mutation
- No automated position decision
- CLI always BLOCKED — tested via `main()` with `SystemExit(1)`
- Broker exception text redacted — secret string absent from output and stdout
- Raw invalid input values (cg result, oo result, symbol) never echoed in output, violations, blocker, or stdout — `TestInputSecretRedaction` (9 tests)
- `broker_calls_readonly` mirrors `broker_calls_made` — `false` on gate failures, `true` only after broker calls
- All output invariant fields hardcoded safe regardless of path

### What remains

The real `AlpacaLivePositionBroker` adapter is NOT implemented.
A future PR must add:
- `AlpacaLivePositionBroker` with lazy SDK import, `get_position`, `get_open_orders` (read-only only)
- `--allow-live-broker-api-readonly` CLI flag
- Credential read only after all gates pass and flag present
- Real adapter tests (mock `TradingClient`, no real Alpaca calls)

### Reference

- `src/tools/live_position_reconciliation_readonly.py` — mock-only core
- `tests/test_live_position_reconciliation_readonly.py` — 66 tests
- `docs/live_position_reconciliation_readonly_design.md` — design document
- Suggested git tag: `live-position-reconciliation-readonly-mock-core-complete`

### Warning

> **This milestone does not approve real trading.**
> **CLI always returns BLOCKED.**
> **No Alpaca endpoint was contacted.**
> **No credentials were read.**
> **No orders were submitted, sold, cancelled, or replaced.**
> **No automated position decision is made.**
> The real broker adapter is not implemented — a future PR is required.
> Any future real adapter must be mock-only in tests, require explicit
> operator flag, and read credentials only after all gates pass.

---

## Milestone: Live Position Reconciliation Read-Only — Real Adapter Complete

**PR:** `add-live-position-reconciliation-readonly-alpaca-adapter`
**Status:** Complete

Real `AlpacaLivePositionBroker` adapter implemented and merged:
- `src/tools/live_position_reconciliation_readonly.py` — real adapter added (exists on `main`)
- `tests/test_live_position_reconciliation_readonly.py` — 106 tests (exists on `main`)

**CLI requires `--allow-live-broker-api-readonly` flag.**
**Without the flag: CLI always returns BLOCKED ("readonly broker api flag not set").**
**Without the flag: credentials are never read, TradingClient is never constructed.**
**With the flag + valid artifacts + valid credentials: real Alpaca read-only calls are made.**
**No Alpaca SDK imported at module level.**
**Alpaca SDK import is lazy — inside `AlpacaLivePositionBroker.__init__` only.**
**No orders submitted, sold, cancelled, or replaced.**
**No `cancel_order`, `replace_order`, `close_position`, `close_all_positions` methods.**
**No live ledger written.**
**No config_safety mutated.**
**No automated position decision made.**
**Broker exception text redacted — never in output, violations, blocker, or stdout.**
**All tests use mock `TradingClient` — no real Alpaca calls in any test.**

### `AlpacaLivePositionBroker` — what it does

`AlpacaLivePositionBroker` wraps `alpaca-py` `TradingClient(paper=False)` and
exposes exactly two read-only methods:

- `get_position(symbol)` — calls `get_open_position`; returns `{"position_exists": True}` or `None` (404 → `None`, not error)
- `get_open_orders(symbol)` — calls `get_orders` with `status=OPEN, symbols=[symbol]`; returns `[{}]` per order (no IDs/prices)

| Property | Value |
|----------|-------|
| `TradingClient` mode | `paper=False` — real live account |
| SDK import | Lazy — inside `__init__` only; no module-level import |
| `cancel_order` / `replace_order` / `close_position` / `close_all_positions` | Absent |
| Orders endpoint (POST/PATCH/DELETE) | Not used |
| Broker exception text | Redacted in all output — raw exception never written |
| `position_observed` / `open_order_observed` | Boolean flags only — no IDs/prices/quantities |
| Credentials read | Only after all gates pass and `--allow-live-broker-api-readonly` present |

### Gate sequence (all must pass before credentials read or TradingClient constructed)

| Gate | Blocker on failure |
|------|--------------------|
| `credential_guard` artifact present and `result="PASS"` | BLOCKED, `credentials_read=false` |
| `operator_override` artifact present and `result="PASS"` | BLOCKED, `credentials_read=false` |
| `symbol` exactly `"SPY"` | BLOCKED, `credentials_read=false` |
| `--allow-live-broker-api-readonly` flag present | BLOCKED ("readonly broker api flag not set"), `credentials_read=false` |
| `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` non-empty in env | BLOCKED ("credentials not found in environment"), `credentials_read=true` |

### Test coverage

106 unit tests in `tests/test_live_position_reconciliation_readonly.py`.
All tests use mock `_FakeTradingClient` / `_TrackingClientCls` — no real Alpaca calls in any test.

New test classes added:
- `TestRealAdapterFlagAbsent` (5) — without flag: BLOCKED, `credentials_read=false`, no TradingClient, no broker calls
- `TestRealAdapterGatesFail` (4) — flag present but artifact/symbol gates fail: `credentials_read=false`
- `TestRealAdapterCredentialsMissing` (4) — flag + gates pass but env vars absent: BLOCKED, `credentials_read=true`
- `TestRealAdapterConstruction` (5) — TradingClient constructed with `paper=False`, api_key passed, `credentials_read=true`, `QueryOrderStatus.OPEN` passed to `GetOrdersRequest`
- `TestRealAdapterHappyPath` (6) — PASS, `position_observed=true/false`, `open_order_observed=true/false`, mutation fields false
- `TestRealAdapterNoPositionSignal` (3) — 404-style exceptions → `position_observed=false`, `result="PASS"` (not BLOCKED)
- `TestRealAdapterExceptionRedaction` (5) — broker/construction exceptions with secret → BLOCKED, secret absent from output
- `TestRealAdapterBrokerConstructionFails` (3) — TradingClient raises → BLOCKED, `credentials_read=true`, `broker_calls_made=false`
- `TestCLIRealAdapterFlag` (3) — CLI with/without flag: correct blocker messages, output always written

Source scan tests added:
- `test_no_close_position_call` — no `close_position(` in non-comment source
- `test_no_close_all_positions_call` — no `close_all_positions(` in non-comment source

### Safety invariants confirmed at this milestone

- CLI without `--allow-live-broker-api-readonly` is always BLOCKED — tested
- Credentials are read only after all gates pass and the flag is present — tested
- `TradingClient` is constructed only after all gates pass — tested with `_TrackingClientCls`
- `paper=False` enforced — tested
- No real Alpaca endpoint was contacted by any test or by this PR
- No credential values were read, stored, printed, or written to any artifact in this PR
- `cancel_order`, `replace_order`, `close_position`, `close_all_positions` absent from source — source-scanned
- No POST/PATCH/DELETE calls in source — source-scanned
- Broker exception text is redacted in all output — tested with secret injection
- `position_observed` and `open_order_observed` are boolean flags only — no IDs/prices/quantities
- No automated position decision made

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **No real Alpaca endpoint was called in this PR (tests use mocks only).**
> The `--allow-live-broker-api-readonly` flag is required for any live API
> contact. A PASS from this tool is a status check only — it does not
> decide whether to hold or sell any position (that remains manual), does
> not remove `config_safety`, and does not authorize any order submission.
> `cancel_order`, `replace_order`, `close_position`, and `close_all_positions`
> are absent from the adapter source.
> Emergency actions remain manual via the Alpaca broker UI only.

---

## Milestone: Position Reconciliation Without Flag — BLOCKED Observed

**Branch:** `claude/docs-snapshot-position-reconciliation-without-flag-blocked`
**Status:** Complete

Dry-run snapshot taken after PR #130 merged to `main`, confirming the flag
gate fires correctly when `--allow-live-broker-api-readonly` is absent.

**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No TradingClient was constructed.**
**No orders were submitted, sold, cancelled, or replaced.**
**No live ledger was written.**
**No config was mutated.**
**No position decision was made.**

### What was confirmed

| Run | Tool | Result |
|-----|------|--------|
| 1 | `live_position_reconciliation_readonly` (without `--allow-live-broker-api-readonly`) | BLOCKED |

The tool returned BLOCKED at gate 4 (flag check) before reading any
environment variable, constructing any `TradingClient`, or making any
broker API call.

### Key observed fields

| Field | Observed |
|-------|---------|
| `result` | `"BLOCKED"` |
| `broker_calls_made` | `false` |
| `credentials_read` | `false` |
| `broker_mutation_calls_made` | `false` |
| `position_observed` | `null` |
| `open_order_observed` | `null` |
| `blocker` | `"readonly broker api flag not set"` |

### Safety invariants confirmed

| Invariant | Confirmed |
|-----------|----------|
| No Alpaca endpoint contacted | ✓ |
| No credentials read | ✓ (`credentials_read=false`) |
| No TradingClient constructed | ✓ |
| No submit/cancel/replace called | ✓ |
| No broker mutation calls | ✓ |
| No live ledger written | ✓ |
| No config mutated | ✓ |
| No position decision made | ✓ |
| `--allow-live-broker-api-readonly` required | ✓ — BLOCKED without it |

### Reference

- `docs/position_reconciliation_without_flag_blocked_snapshot.md` — full snapshot document
- Suggested git tag: `position-reconciliation-without-flag-blocked-observed`

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve future broker calls.**
> **No Alpaca endpoint was contacted.**
> **No credentials were read.**
> **No position decision was made.**
> The `--allow-live-broker-api-readonly` flag remains required for any live
> read-only broker contact. Any position decision remains a manual operator
> action. Emergency actions remain manual via the Alpaca broker UI only.

---

## Milestone: Position Reconciliation Read-Only — PASS Observed

**Branch:** `claude/docs-snapshot-position-reconciliation-readonly-pass`
**Status:** Complete

Read-only reconciliation run with `--allow-live-broker-api-readonly` returned
`result="PASS"` after PR #130 merged to `main`.

**Alpaca was contacted read-only only (GET calls only).**
**Credentials were read but never exposed, stored, or written to any output.**
**TradingClient was constructed only after all gates passed and the flag was present.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No broker mutation call was made.**
**No live ledger was written.**
**No config was mutated.**
**No position decision was made.**

### Key observed fields

| Field | Observed |
|-------|---------|
| `result` | `"PASS"` |
| `broker_calls_made` | `true` |
| `broker_calls_readonly` | `true` |
| `broker_mutation_calls_made` | `false` |
| `credentials_read` | `true` |
| `credential_values_exposed` | `false` |
| `live_submit_enabled` | `false` |
| `submit_order_reachable` | `false` |
| `cancel_order_reachable` | `false` |
| `replace_order_reachable` | `false` |
| `symbol` | `"SPY"` |
| `position_observed` | `true` |
| `open_order_observed` | `false` |
| `broker_ids_redacted` | `true` |
| `account_identifiers_redacted` | `true` |
| `raw_broker_response_included` | `false` |
| `violations` | `[]` |
| `blocker` | `null` |

### Read-only broker calls confirmed

Two read-only GET calls were made via `AlpacaLivePositionBroker`:

| Call | Method | Result |
|------|--------|--------|
| `get_position("SPY")` | `get_open_position` (GET) | Position exists |
| `get_open_orders("SPY")` | `get_orders` with `QueryOrderStatus.OPEN` (GET) | No open orders |

No POST, PATCH, or DELETE calls were made.

### position_observed and open_order_observed are presence flags only

`position_observed=true` means only that a SPY position was observed to
exist in the live account at the time of the run. It does not record
position size, fill price, quantity, cost basis, or any broker identifier.

`open_order_observed=false` means only that no open SPY orders were
observed at the time of the run.

**Neither field constitutes a position management decision.** Whether to
hold or sell the position remains a manual operator decision.

### Safety invariants confirmed

| Invariant | Confirmed |
|-----------|----------|
| Only GET-equivalent broker calls made | ✓ (`broker_calls_readonly=true`) |
| No broker mutation calls | ✓ (`broker_mutation_calls_made=false`) |
| No submit_order called | ✓ (`submit_order_reachable=false`) |
| No cancel_order called | ✓ (`cancel_order_reachable=false`) |
| No replace_order called | ✓ (`replace_order_reachable=false`) |
| No credential values exposed | ✓ (`credential_values_exposed=false`) |
| No raw broker IDs in output | ✓ (`broker_ids_redacted=true`) |
| No account identifiers in output | ✓ (`account_identifiers_redacted=true`) |
| No raw broker response in output | ✓ (`raw_broker_response_included=false`) |
| No live ledger written | ✓ |
| No config mutated | ✓ |
| No position decision made | ✓ |
| No automated or recurring trading | ✓ |

### Reference

- `docs/position_reconciliation_readonly_pass_snapshot.md` — full snapshot document
- Suggested git tag: `position-reconciliation-readonly-pass-observed`

### Warning

> **This milestone does not approve holding or selling the observed position.**
> **This milestone does not approve future trading.**
> **This milestone does not approve future broker calls.**
> `position_observed=true` is a boolean presence flag only — it does not
> record size, price, or any other position detail.
> Whether to hold or sell the position remains a manual operator decision.
> Emergency actions (cancel, close, replace) remain manual via the Alpaca
> broker UI only.
> Any future read-only reconciliation run requires the
> `--allow-live-broker-api-readonly` flag, both prerequisite artifacts with
> `result="PASS"`, and valid credentials in the environment.

---

## Milestone: Manual Position Monitoring and Exit Framework — Design Complete

**Branch:** `claude/docs-design-manual-position-monitoring-and-exit-framework`
**Status:** Complete

Design framework document created at
`docs/manual_position_monitoring_and_exit_framework.md`.

**No trade was executed.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No code was implemented.**
**No automated monitoring was implemented.**
**No stop-loss, take-profit, trailing stop, recurring job, or sell adapter was implemented.**
**The current SPY position remains a manual operator decision.**

### What was designed

A framework document defining:

1. **Current state** — `position_observed=true` (flag only), no size/price/PnL in
   repo, emergency actions manual via Alpaca UI
2. **Manual monitoring principles** — operator checks Alpaca UI directly; bot must
   not decide hold/sell; any future tool must be read-only first; any future
   sell/close workflow requires separate design, approval, flag, and mock-only tests
3. **Manual exit options** — sell via Alpaca UI, hold, or engineer future sell
   workflow in a separate PR; no automation implemented here
4. **Future read-only monitoring design ideas** — position status checker, market
   session checker, local operator checklist, non-sensitive snapshot format
   (boolean/status fields only; no size/price/PnL/IDs committed)
5. **Future sell/close workflow constraints** — separate design PR, separate approval
   artifact, explicit flag, mock-only tests, exact symbol, single broker mutation,
   no retry/cancel/replace/automation, broker exception text redacted, no IDs/PnL in output
6. **Abort conditions** — full table: uncertainty, stale artifacts, credential
   leakage, open order ambiguity, unexpected broker response, automation pressure,
   unreviewed mutation paths
7. **Warnings** — holding/selling are financial decisions by the operator; bot
   provides status checks only; nothing in this repository is financial advice

### Safety invariants confirmed

- No code changes
- No Alpaca endpoint contacted
- No credentials read or written
- No order submitted, sold, cancelled, or replaced
- No live ledger written
- No automated position decision made
- No stop-loss, take-profit, or trailing stop implemented
- No recurring or automated monitoring implemented
- No sell or close adapter implemented

### Reference

- `docs/manual_position_monitoring_and_exit_framework.md` — full framework document
- Suggested git tag: `manual-position-monitoring-exit-framework-designed`

### Warning

> **This milestone does not approve holding or selling the observed position.**
> **This milestone does not approve future trading.**
> **This milestone does not approve future broker calls.**
> **No code was implemented. No Alpaca endpoint was contacted. No credentials were read.**
> The framework document is a design and guidance document only.
> Automated position management, stop-loss, take-profit, trailing stop,
> recurring monitoring jobs, and sell/close adapters are not implemented.
> Any future live action requires fresh design, fresh approval, fresh preflight,
> explicit operator action, and an explicit CLI flag.
> The hold/sell decision for the current SPY position remains entirely manual.

---

## Milestone: Manual Position Status Checker Read-Only — Design Complete

**Branch:** `claude/docs-design-manual-position-status-checker-readonly`
**Status:** Complete

Design document created at
`docs/manual_position_status_checker_readonly_design.md`.

**No trade was executed.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No code was implemented.**
**No automated monitoring was implemented.**
**No stop-loss, take-profit, trailing stop, recurring job, sell adapter, or close adapter was implemented.**
**The current SPY position remains a manual operator decision.**

### What was designed

A future read-only manual position status checker that lets the operator
re-check SPY position and open-order presence on demand, without making
any position decision. Key design decisions:

- **Reuse or wrap `live_position_reconciliation_readonly`** — gate sequence,
  broker adapter, and safety invariant fields preserved exactly; no new mutation
  logic introduced
- **Gate sequence preserved** — credential guard → operator override → symbol →
  explicit flag → credential read → `TradingClient` construction → GET-only calls
- **Boolean flags only** — `position_observed`, `open_order_observed`,
  `market_session_status`; no size/price/quantity/PnL/IDs in any output field
- **New hardcoded safety fields** — `close_position_reachable=false`,
  `position_decision_made=false` in addition to all existing invariants
- **`--allow-live-broker-api-readonly` flag required** — BLOCKED without it;
  credentials never read without the flag
- **Manual-run only** — no scheduling, no automation, no recurring jobs
- **Mock-only tests required** — no real Alpaca calls in any test; source scans
  for mutation methods, network imports, and POST/PATCH/DELETE patterns

### Proposed output fields

| Field | Value |
|-------|-------|
| `broker_mutation_calls_made` | `false` always |
| `credential_values_exposed` | `false` always |
| `submit_order_reachable` | `false` always |
| `cancel_order_reachable` | `false` always |
| `replace_order_reachable` | `false` always |
| `close_position_reachable` | `false` always (new) |
| `position_decision_made` | `false` always (new) |
| `broker_ids_redacted` | `true` always |
| `account_identifiers_redacted` | `true` always |
| `raw_broker_response_included` | `false` always |
| `position_observed` | `bool \| null` |
| `open_order_observed` | `bool \| null` |
| `market_session_status` | `str \| null` |

### Safety invariants confirmed

- No code changes
- No Alpaca endpoint contacted
- No credentials read or written
- No order submitted, sold, cancelled, replaced, or closed
- No live ledger written
- No automated position decision made
- No stop-loss, take-profit, or trailing stop designed
- No recurring or automated monitoring designed
- No sell or close adapter designed

### Reference

- `docs/manual_position_status_checker_readonly_design.md` — full design document
- Suggested git tag: `manual-position-status-checker-readonly-designed`

### Warning

> **This milestone does not approve holding or selling the observed position.**
> **This milestone does not approve future trading.**
> **This milestone does not approve future broker calls.**
> **No code was implemented. No Alpaca endpoint was contacted. No credentials were read.**
> The design document is a planning document only.
> Any future implementation requires its own PR, mock-only tests, safety review,
> and all gates confirmed in the implementation PR.
> PASS from the future checker means only that the status check completed —
> it does not recommend hold or sell.
> All position decisions remain entirely manual.

---

## Milestone: Manual Position Status Checker Read-Only — Mock-Only Core Complete

**PR:** `add-manual-position-status-checker-readonly-mock-core` (#135)
**Status:** Complete

Mock-only core implemented and merged:
- `src/tools/manual_position_status_checker_readonly.py` — mock-only core
- `tests/test_manual_position_status_checker_readonly.py` — 88 tests

**CLI always returns BLOCKED ("real broker adapter not implemented").**
**PASS is only reachable through an injected mock broker in unit tests.**
**Real Alpaca adapter is NOT implemented.**
**No Alpaca SDK imported.**
**No network requests made.**
**No credentials read on any code path.**
**No os.environ access anywhere in source.**
**No orders submitted, sold, cancelled, replaced, or closed.**
**No live ledger written.**
**No config mutated.**
**No automated position decision made.**

### What was implemented

`run_status_check()` with gate sequence:

| Gate | Blocker on failure |
|------|--------------------|
| `credential_guard` artifact present and `result="PASS"` | BLOCKED, `broker_calls_made=false` |
| `operator_override` artifact present and `result="PASS"` | BLOCKED, `broker_calls_made=false` |
| `symbol` exactly `"SPY"` | BLOCKED, invalid symbol not echoed |
| `broker` injected (real adapter not implemented) | BLOCKED, `"real broker adapter not implemented"` |
| Broker read-only calls succeed | BLOCKED on exception, details redacted |

New output fields vs. `live_position_reconciliation_readonly`:
- `close_position_reachable=false` — always
- `position_decision_made=false` — always
- `market_session_status` — allowlisted string or null; allowed values: `"open"`, `"closed"`, `"pre_market"`, `"after_hours"`, `null`; any other return value → BLOCKED, raw value not echoed

### Test coverage

88 unit tests in `tests/test_manual_position_status_checker_readonly.py`.
Full suite: 4025 tests passed.
All tests use injected mock brokers — no real Alpaca calls.

Test classes:
- `TestArtifactGates` (7) — missing/non-PASS/malformed cg and oo → BLOCKED, no broker call
- `TestSymbolValidation` (4) — wrong/lowercase/whitespace/empty symbol → BLOCKED
- `TestInputSecretRedaction` (9) — secrets in cg result, oo result, symbol → absent from output, violations, blocker, stdout
- `TestBrokerNone` (7) — CLI broker=None → BLOCKED, correct blocker message, all null presence fields
- `TestHappyPath` (8) — injected mock broker → PASS with position/order/session flags; broker_calls_readonly=true; violations empty
- `TestMarketSessionStatus` (6) — open/closed/pre_market/after_hours/null return/no method → market_session_status field correct
- `TestMarketSessionStatusRedaction` (13) — invalid/secret/whitespace/case variants → BLOCKED; raw value absent from output JSON, violations, blocker, stdout
- `TestBrokerException` (7) — position/orders/session raises with secret → BLOCKED, secret absent from output and stdout
- `TestOutputInvariants` (5) — all hardcoded safety fields checked on gate failure, broker None, and PASS paths; broker_calls_readonly mirrors broker_calls_made
- `TestOutputAlwaysWritten` (3) — output artifact written for all BLOCKED paths via CLI; exit 1 confirmed
- `TestNeverRaises` (4) — `run_status_check()` never raises on any exception path
- `TestNoRawIds` (2) — position/order broker dict values not leaked into output JSON
- `TestSourceScans` (14) — no alpaca/network imports, no os.environ.get/[], no submit_order(/cancel_order(/replace_order(/close_position(/close_all_positions(, no POST/PATCH/DELETE mutation markers

### Safety invariants confirmed

- No Alpaca SDK imported — source-scanned
- No network imports — source-scanned
- No `os.environ` access — source-scanned
- No `submit_order(`/`cancel_order(`/`replace_order(`/`close_position(`/`close_all_positions(` in source — source-scanned
- No POST/PATCH/DELETE mutation markers in source — source-scanned
- No config mutation
- No automated position decision
- CLI always BLOCKED — tested via `main()` with `SystemExit(1)`
- Broker exception text redacted — secret string absent from output and stdout
- Raw invalid input values (cg result, oo result, symbol) never echoed — `TestInputSecretRedaction` (9 tests)
- `market_session_status` allowlisted — secret/invalid/whitespace/case return values → BLOCKED, raw value not echoed — `TestMarketSessionStatusRedaction` (13 tests)
- `broker_calls_readonly` mirrors `broker_calls_made` — false on gate failures, true only after broker calls
- All output invariant fields hardcoded safe regardless of path

### What remains

The real `AlpacaLivePositionBroker`-based adapter is NOT implemented.
A future PR must add:
- `--allow-live-broker-api-readonly` CLI flag
- Credential read only after all gates pass and flag present
- `TradingClient(paper=False)` constructed only after credentials confirmed
- Real adapter tests (mock `TradingClient`, no real Alpaca calls)

### Reference

- `src/tools/manual_position_status_checker_readonly.py` — mock-only core
- `tests/test_manual_position_status_checker_readonly.py` — 88 tests
- `docs/manual_position_status_checker_readonly_design.md` — design document
- Suggested git tag: `manual-position-status-checker-readonly-mock-core-complete`

### Warning

> **This milestone does not approve real trading.**
> **CLI always returns BLOCKED.**
> **No Alpaca endpoint was contacted.**
> **No credentials were read.**
> **No orders were submitted, sold, cancelled, replaced, or closed.**
> **No automated position decision is made.**
> The real broker adapter is not implemented — a future PR is required.
> Any future real adapter must be mock-only in tests, require explicit
> operator flag, and read credentials only after all gates pass.
> PASS from the checker means only that the status check completed —
> it does not recommend hold or sell.
> All position decisions remain entirely manual.

---

## Milestone: Manual Position Status Checker Read-Only — Real Adapter Complete

**Status:** Complete

Real `AlpacaManualPositionStatusBroker` adapter implemented:
- `src/tools/manual_position_status_checker_readonly.py` — real adapter added
- `tests/test_manual_position_status_checker_readonly.py` — 131 tests

**CLI requires `--allow-live-broker-api-readonly` flag.**
**Without the flag: CLI always returns BLOCKED ("readonly broker api flag not set").**
**Without the flag: credentials are never read, TradingClient is never constructed.**
**With the flag + valid artifacts + valid credentials: real Alpaca read-only calls are made.**
**No Alpaca SDK imported at module level.**
**Alpaca SDK import is lazy — inside `AlpacaManualPositionStatusBroker.__init__` only.**
**No orders submitted, sold, cancelled, replaced, or closed.**
**No `cancel_order`, `replace_order`, `close_position`, `close_all_positions` methods.**
**No live ledger written.**
**No config mutated.**
**No automated position decision made.**
**Broker exception text redacted — never in output, violations, blocker, or stdout.**
**All tests use mock `TradingClient` — no real Alpaca calls in any test.**

### `AlpacaManualPositionStatusBroker` — what it does

`AlpacaManualPositionStatusBroker` wraps `alpaca-py` `TradingClient(paper=False)` and
exposes exactly three read-only methods:

- `get_position(symbol)` — calls `get_open_position`; returns `{"position_exists": True}` or `None` (404-like signals → `None`, not error)
- `get_open_orders(symbol)` — calls `get_orders` with `status=OPEN, symbols=[symbol]`; returns `[{}]` per order (no IDs/prices)
- `get_market_session_status()` — calls `get_clock`; returns allowlisted session string or `None`

| Property | Value |
|----------|-------|
| `TradingClient` mode | `paper=False` — real live account |
| SDK import | Lazy — inside `__init__` only; no module-level import |
| `cancel_order` / `replace_order` / `close_position` / `close_all_positions` | Absent |
| Orders endpoint (POST/PATCH/DELETE) | Not used |
| Broker exception text | Redacted in all output — raw exception never written |
| `position_observed` / `open_order_observed` | Boolean flags only — no IDs/prices/quantities |
| `market_session_status` | Allowlisted: `"open"`, `"closed"`, `"pre_market"`, `"after_hours"`, or `null` |
| Credentials read | Only after all gates pass and `--allow-live-broker-api-readonly` present |

### Gate sequence (all must pass before credentials read or TradingClient constructed)

| Gate | Blocker on failure |
|------|--------------------|
| `credential_guard` artifact present and `result="PASS"` | BLOCKED, `credentials_read=false` |
| `operator_override` artifact present and `result="PASS"` | BLOCKED, `credentials_read=false` |
| `symbol` exactly `"SPY"` | BLOCKED, `credentials_read=false` |
| `--allow-live-broker-api-readonly` flag present | BLOCKED ("readonly broker api flag not set"), `credentials_read=false` |
| `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` non-empty in env | BLOCKED ("credentials not found in environment"), `credentials_read=true` |
| `TradingClient` construction succeeds | BLOCKED ("live broker construction failed (details redacted)"), `credentials_read=true`, `broker_calls_made=false` |
| Broker read-only calls succeed | BLOCKED (on exception), exception text redacted |

### Market session status logic

| `clock.is_open` | `hours_until_open` | Returned value |
|-----------------|-------------------|----------------|
| `True` | (any) | `"open"` |
| `False` | `<= 0` | `None` |
| `False` | `<= 5.75` | `"pre_market"` |
| `False` | `13.25 – 18.25` | `"after_hours"` |
| `False` | all other values | `"closed"` |

Any value outside the allowlist from a broker (including injected test brokers) → BLOCKED, raw value not echoed.

### Test coverage

131 unit tests in `tests/test_manual_position_status_checker_readonly.py`.
Full suite: 4068 tests passed.
All tests use mock `_FakeTradingClient` / `_make_fixed_client` — no real Alpaca calls in any test.

New test classes added:
- `TestRealAdapterFlagAbsent` (5) — without flag: BLOCKED, `credentials_read=false`, no TradingClient, no broker calls
- `TestRealAdapterGatesFail` (4) — flag present but artifact/symbol gates fail: `credentials_read=false`
- `TestRealAdapterCredentialsMissing` (4) — flag + gates pass but env vars absent: BLOCKED, `credentials_read=true`
- `TestRealAdapterConstruction` (5) — TradingClient constructed with `paper=False`, api_key passed, `credentials_read=true`, `QueryOrderStatus.OPEN` passed
- `TestRealAdapterHappyPath` (6) — PASS, `position_observed=true/false`, `open_order_observed=true/false`, mutation fields false
- `TestRealAdapterNoPositionSignal` (3) — 404-style exceptions → `position_observed=false`, `result="PASS"` (not BLOCKED)
- `TestRealAdapterMarketSession` (5) — clock open/pre_market/after_hours/closed/raises → correct session or BLOCKED/redacted
- `TestRealAdapterExceptionRedaction` (4) — position/orders/clock/construction exceptions with secret → BLOCKED, secret absent from all output
- `TestRealAdapterBrokerConstructionFails` (4) — TradingClient raises → BLOCKED, `credentials_read=true`, `broker_calls_made=false`, never raises
- `TestCLIRealAdapterFlag` (3) — CLI with/without flag: correct blocker messages, PASS on mocked success

Source scan tests updated:
- `test_no_alpaca_import` → `test_no_module_level_alpaca_import` (allows lazy imports inside indented bodies)
- `test_no_os_environ` → `test_no_module_level_os_environ_get` (allows `os.environ.get()` inside indented function bodies)

### Safety invariants confirmed at this milestone

- CLI without `--allow-live-broker-api-readonly` is always BLOCKED — tested
- Credentials are read only after all gates pass and the flag is present — tested
- `TradingClient` is constructed only after all gates pass — tested with tracking class
- `paper=False` enforced — tested
- No real Alpaca endpoint was contacted by any test or by this PR
- No credential values were read, stored, printed, or written to any artifact in this PR
- `cancel_order`, `replace_order`, `close_position`, `close_all_positions` absent from source — source-scanned
- No POST/PATCH/DELETE calls in source — source-scanned
- Broker exception text is redacted in all output — tested with secret injection
- `position_observed` and `open_order_observed` are boolean flags only — no IDs/prices/quantities
- `market_session_status` is allowlisted — any other return value → BLOCKED, raw value not echoed
- `close_position_reachable=false` and `position_decision_made=false` hardcoded always
- No automated position decision made
- Real run not yet performed — requires operator with live credentials and explicit flag

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve live order submission.**
> **No real Alpaca endpoint was called in this PR (tests use mocks only).**
> The `--allow-live-broker-api-readonly` flag is required for any live API
> contact. A PASS from this tool is a status check only — it does not
> decide whether to hold or sell any position (that remains manual), does
> not remove `config_safety`, and does not authorize any order submission.
> `cancel_order`, `replace_order`, `close_position`, and `close_all_positions`
> are absent from the adapter source.
> Emergency actions remain manual via the Alpaca broker UI only.

---

## Milestone: Manual Position Status Checker Without Flag — BLOCKED Observed

**Branch:** `claude/docs-snapshot-manual-position-status-checker-without-flag-blocked`
**Status:** Complete

Dry-run snapshot taken after PR #137 merged to `main`, confirming the flag
gate fires correctly when `--allow-live-broker-api-readonly` is absent.

**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No TradingClient was constructed.**
**No orders were submitted, sold, cancelled, replaced, or closed.**
**No broker mutation call was made.**
**No live ledger was written.**
**No config was mutated.**
**No position decision was made.**

### What was confirmed

| Run | Tool | Result |
|-----|------|--------|
| 1 | `manual_position_status_checker_readonly` (without `--allow-live-broker-api-readonly`) | BLOCKED |

The tool returned BLOCKED at gate 4 (flag check) before reading any
environment variable, constructing any `TradingClient`, or making any
broker API call.

### Key observed fields

| Field | Observed |
|-------|---------|
| `result` | `"BLOCKED"` |
| `broker_calls_made` | `false` |
| `broker_calls_readonly` | `false` |
| `broker_mutation_calls_made` | `false` |
| `credentials_read` | `false` |
| `credential_values_exposed` | `false` |
| `position_observed` | `null` |
| `open_order_observed` | `null` |
| `market_session_status` | `null` |
| `position_decision_made` | `false` |
| `blocker` | `"readonly broker api flag not set"` |

### Safety invariants confirmed

| Invariant | Confirmed |
|-----------|----------|
| No Alpaca endpoint contacted | ✓ |
| No credentials read | ✓ (`credentials_read=false`) |
| No TradingClient constructed | ✓ |
| No submit/cancel/replace called | ✓ |
| No broker mutation calls | ✓ |
| No live ledger written | ✓ |
| No config mutated | ✓ |
| No position decision made | ✓ |
| `--allow-live-broker-api-readonly` required | ✓ — BLOCKED without it |

### Reference

- `docs/manual_position_status_checker_without_flag_blocked_snapshot.md` — full snapshot document
- Suggested git tag: `manual-position-status-checker-without-flag-blocked-observed`

### Warning

> **This milestone does not approve real trading.**
> **This milestone does not approve future broker calls.**
> **No Alpaca endpoint was contacted.**
> **No credentials were read.**
> **No position decision was made.**
> The `--allow-live-broker-api-readonly` flag remains required for any live
> read-only broker contact. Any position decision remains a manual operator
> action. Emergency actions remain manual via the Alpaca broker UI only.

---

## Milestone: Automated Strategy Execution Roadmap — Designed

**Branch:** `claude/docs-design-automated-strategy-execution-roadmap`
**Status:** Complete

Roadmap document created at
`docs/automated_strategy_execution_roadmap.md`.

**No code was implemented.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No automated live trading was approved.**
**All position and trading decisions remain entirely manual.**

### What was designed

A staged roadmap from current manual infrastructure to fully automated
strategy execution, covering:

1. **Final target system** — strategy signal generator, risk gate, order
   executor, position manager, exit manager, scheduler, audit logger,
   kill switch, read-only monitor, paper/live separation
2. **Strategy scope** — SPY only, long only, 1h to 1d bars, one position
   at a time, deterministic rules only, no leverage/options/shorting
3. **Current foundation** — summarises all completed infrastructure PRs
   as the safety foundation for future automation
4. **Gap to final automation** — 12 missing components, each requiring
   its own design and implementation PR
5. **Staged roadmap** — Phases A–H from offline signal module through
   limited live automation; no phase may be skipped
6. **Required state machine** — 13 states; no live automation until
   fully designed and tested with a mock broker
7. **Risk rules** — hard rules enforced by automated risk gate;
   SPY only, long only, one position, notional cap, kill switch, stale
   data, order ambiguity, position ambiguity
8. **Strategy interface** — pure function contract; no broker calls;
   deterministic; cannot bypass risk gate
9. **Execution interface** — accepts approved action only; never computes
   strategy; one mutation per run; fail-closed
10. **Audit and safety requirements** — what to record and what to exclude
11. **Non-goals** — multi-symbol, options, leverage, ML, HFT all out of scope
12. **Next step** — design `strategy_signal_engine` offline-only first

### Safety invariants confirmed

- No code changes
- No Alpaca endpoint contacted
- No credentials read or written
- No order submitted, sold, cancelled, or replaced
- No live ledger written
- No automated trading approved
- No automated position decision made

### Reference

- `docs/automated_strategy_execution_roadmap.md` — full roadmap document
- Suggested git tag: `automated-strategy-execution-roadmap-designed`

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No code is implemented. No Alpaca endpoint was contacted. No credentials were read.**
> All automated live trading requires completing the full staged roadmap
> (Phases A–H), with each phase reviewed and approved in its own PR.
> Until automation is implemented, tested, and approved, all trading
> decisions remain entirely manual operator actions.
> Nothing in this repository is financial advice.

---

## Milestone: Phase A — Strategy Signal Engine Offline Core — Implemented

**Branch:** `claude/add-strategy-signal-engine-offline-core`
**Status:** Complete

Offline-only deterministic strategy signal engine implemented at
`src/strategy/signal_engine.py`. Tests at
`tests/test_strategy_signal_engine.py`.

**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No network library was imported.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No live ledger was written.**
**No config was mutated.**
**No scheduler was implemented.**
**No broker executor was implemented.**
**No live or paper trading was implemented.**
**No automated trading was approved.**
**All signals are recommendations only — risk gate and executor are not implemented.**
**All position and trading decisions remain entirely manual.**

### What was implemented

| File | Description |
|------|-------------|
| `src/strategy/signal_engine.py` | Pure offline signal engine: `evaluate_signal()` |
| `tests/test_strategy_signal_engine.py` | 96 tests covering all signal types, gates, edge cases, source scans |

### Signal engine contract

`evaluate_signal(bars, position_state, open_order_state, market_session, config) → SignalResult`

Pure function. Same inputs always produce same output. No side effects.

#### Inputs

| Input | Type |
|-------|------|
| `bars` | `list[Bar]` — OHLCV; most recent last; not mutated |
| `position_state` | `PositionState` — `has_position: bool`; no entry price |
| `open_order_state` | `OpenOrderState` — `has_open_order: bool` |
| `market_session` | `str \| None` — `"open"` / `"closed"` / `"pre_market"` / `"after_hours"` / `None` |
| `config` | `SignalEngineConfig` — strategy params, symbol, timeframe, windows |

#### Gate sequence (BLOCK on any failure)

| Gate | Reason code |
|------|-------------|
| 1 — insufficient bars | `INSUFFICIENT_BARS` |
| 2 — symbol != SPY | `INVALID_SYMBOL` |
| 3 — timeframe not in {1h, 1d} | `INVALID_TIMEFRAME` |
| 4 — market_session != "open" | `MARKET_NOT_OPEN` |
| 5 — open order present | `OPEN_ORDER_PRESENT` |

#### Signal logic (SMA crossover)

| Condition | Signal | Reason code |
|-----------|--------|-------------|
| short SMA > long SMA, no position | BUY | `SMA_CROSSOVER_BULLISH` |
| short SMA < long SMA, has position | SELL | `SMA_CROSSOVER_BEARISH` |
| bullish but already in position | HOLD | `HOLD_ALREADY_IN_POSITION` |
| bearish but flat | HOLD | `HOLD_NO_POSITION_TO_EXIT` |

#### Output invariants (always)

| Field | Value |
|-------|-------|
| `deterministic` | `true` |
| `broker_calls_made` | `false` |
| `credentials_read` | `false` |
| `live_submit_enabled` | `false` |
| `order_action_requested` | `false` |
| `position_decision_is_recommendation_only` | `true` |

### Test coverage

| Metric | Value |
|--------|-------|
| Targeted tests | 96 passed |
| Full suite | 4164 passed |
| Real broker calls | None |
| Credentials read | None |

### Test classes

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestBuySignal` | 6 | Bullish crossover, no position → BUY |
| `TestSellSignal` | 4 | Bearish crossover, has position → SELL |
| `TestHoldSignal` | 5 | Bullish+position and bearish+flat → HOLD |
| `TestBlockInsufficientBars` | 5 | Zero, one, and boundary bar counts → BLOCK |
| `TestBlockInvalidSymbol` | 5 | Non-SPY, lowercase, empty symbol → BLOCK |
| `TestBlockInvalidTimeframe` | 6 | 5m, empty, tick timeframes → BLOCK; 1h/1d pass |
| `TestBlockMarketSession` | 9 | closed/pre_market/after_hours/None → BLOCK; open passes |
| `TestBlockOpenOrder` | 4 | Open order present → BLOCK |
| `TestDeterminism` | 5 | Same input → same output; flag always true |
| `TestInputNotMutated` | 3 | bars list not modified by engine |
| `TestOutputFields` | 9 | All result fields present and correct types |
| `TestSafetyFields` | 12 | All safety invariants on every signal path |
| `TestGateOrder` | 4 | Gates checked in correct order |
| `TestSmaLogic` | 3 | Custom windows, equal SMAs |
| `TestSourceScans` | 16 | No Alpaca/network/os.environ/mutation markers |

### Safety invariants confirmed

| Invariant | Method | Result |
|-----------|--------|--------|
| No Alpaca SDK imported | `TestSourceScans::test_no_alpaca_import` | Confirmed absent |
| No network library imports | `TestSourceScans` (requests/httpx/aiohttp/urllib) | Confirmed absent |
| No environment variable access | `TestSourceScans::test_no_os_environ` | Confirmed absent |
| No `os` import | `TestSourceScans::test_no_os_import` | Confirmed absent |
| No submit/cancel/replace/close calls | `TestSourceScans` | Confirmed absent |
| No POST/PATCH/DELETE markers | `TestSourceScans` | Confirmed absent |
| No ledger writes | `TestSourceScans` (write_text/json.dump) | Confirmed absent |
| `broker_calls_made` always false | `TestSafetyFields` | Confirmed |
| `credentials_read` always false | `TestSafetyFields` | Confirmed |
| `position_decision_is_recommendation_only` always true | `TestSafetyFields` | Confirmed |
| Same input → same output | `TestDeterminism` | Confirmed |
| Input bars not mutated | `TestInputNotMutated` | Confirmed |

### What remains (not implemented, each requires its own PR)

| Component | Status |
|-----------|--------|
| Historical data ingestion / backtest (Phase B) | Not implemented |
| Paper trading executor (Phase C) | Not implemented |
| Automated risk gate (Phase D) | Not implemented |
| Mock automated buy/sell state machine (Phase E) | Not implemented |
| Paper broker integration (Phase F) | Not implemented |
| Live automation (Phase G) | Not implemented |
| Scheduler | Not implemented |
| Kill switch | Not implemented |
| Monitoring and alerting | Not implemented |

### Reference

- `src/strategy/signal_engine.py` — signal engine source
- `tests/test_strategy_signal_engine.py` — 96 tests
- `docs/automated_strategy_execution_roadmap.md` — full roadmap (Phase A marked complete)
- Suggested git tag: `strategy-signal-engine-offline-core-implemented`

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **BUY/SELL signals from this module do not execute anything.**
> **The risk gate, executor, and scheduler are not implemented.**
> All signals are recommendations only. Any position decision remains a manual
> operator action. Live automation requires completing Phases B–G, each reviewed
> in its own PR.
> Nothing in this repository is financial advice.

---

## Milestone: Phase A — Strategy Signal Engine Offline Core — Complete (Snapshot)

**Branch:** `claude/docs-snapshot-strategy-signal-engine-offline-core-complete`
**Status:** Complete

Snapshot document created at
`docs/strategy_signal_engine_offline_core_complete_snapshot.md`.

**No code was changed in this PR.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No automated trading was approved.**
**All position and trading decisions remain entirely manual.**

### What this snapshot records

- `src/strategy/signal_engine.py` exists and is offline-only and deterministic.
- `tests/test_strategy_signal_engine.py` exists with 96 tests; full suite 4164.
- `evaluate_signal()` is a pure function: same inputs always produce same output.
- BUY/SELL/HOLD/BLOCK are recommendations only — no execution occurs.
- All source scan invariants confirmed: no Alpaca SDK, no network libraries,
  no environment variable access, no submit/cancel/replace/close, no ledger writes.
- All safety fields confirmed always: `deterministic=true`, `broker_calls_made=false`,
  `credentials_read=false`, `live_submit_enabled=false`, `order_action_requested=false`,
  `position_decision_is_recommendation_only=true`.
- Risk gate, executor, scheduler, paper trading, and live trading remain not implemented.

### Reference

- `docs/strategy_signal_engine_offline_core_complete_snapshot.md` — full snapshot document
- Suggested git tag: `strategy-signal-engine-offline-core-complete`

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No code is implemented in this PR. No Alpaca endpoint was contacted.**
> **No credentials were read.**
> The risk gate, executor, and scheduler remain not implemented.
> All automated live trading requires completing the full staged roadmap
> (Phases A–H), with each phase reviewed and approved in its own PR.
> Until automation is implemented, tested, and approved, all trading
> decisions remain entirely manual operator actions.
> Nothing in this repository is financial advice.

---

## Milestone: Phase B — Backtest and Metrics Offline — Designed

**Branch:** `claude/docs-design-backtest-and-metrics-offline`
**Status:** Complete

Design document created at `docs/backtest_and_metrics_offline_design.md`.

**No code was implemented.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No automated trading was approved.**
**All position and trading decisions remain entirely manual.**

### What was designed

Offline backtest system for Phase B covering:

1. **Data scope** — local CSV/fixture only; SPY; 1h/1d; no live data API
2. **Backtest engine** — pure function `run_backtest(bars, config, starting_equity) → BacktestResult`; fill at next-bar open; no broker calls
3. **Simulation rules** — long only, one position, no pyramiding, no same-day re-entry
4. **Metrics** — total_return_pct, trade_count, win_rate, max_drawdown_pct, exposure_pct, average_hold_bars, signal_counts, blocked_reason_counts, and more
5. **Output safety** — no credentials, account IDs, broker IDs, live balances, or raw broker responses in output
6. **Determinism** — same bars/config → same output; no randomness; no wall-clock dependency
7. **Proposed files** — `src/backtest/offline_backtest_engine.py`, `tests/test_offline_backtest_engine.py`
8. **Testing requirements** — 20+ test scenarios including source scans
9. **Non-goals** — no paper/live trading, no broker, no scheduler, no risk gate, no ML
10. **Phase dependency** — Phase C cannot start until Phase B implementation and snapshot are reviewed

### Safety invariants confirmed

- No code changes
- No Alpaca endpoint contacted
- No credentials read or written
- No order submitted, sold, cancelled, or replaced
- No live ledger written
- No automated trading approved

### Reference

- `docs/backtest_and_metrics_offline_design.md` — full design document
- `docs/automated_strategy_execution_roadmap.md` — Phase B marked designed
- Suggested git tag: `backtest-and-metrics-offline-designed`

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No code is implemented. No Alpaca endpoint was contacted. No credentials were read.**
> A positive backtest does not approve live trading and does not guarantee
> future performance. All automated live trading requires completing the full
> staged roadmap (Phases A–H), with each phase reviewed and approved in its
> own PR. Until automation is implemented, tested, and approved, all trading
> decisions remain entirely manual operator actions.
> Nothing in this repository is financial advice.

---

## Milestone: Trend Bot Architecture Refactor Plan — Designed

**Branch:** `claude/docs-design-trend-bot-architecture-refactor-plan`
**Status:** Complete

Architecture refactor plan created at
`docs/trend_bot_architecture_refactor_plan.md`.

**No code was changed.**
**No files were moved.**
**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No automated trading was approved.**
**All position and trading decisions remain entirely manual.**

### What was designed

A 10-PR staged refactor plan to align the repository with the trend-following
MVP goal, covering:

1. **Refactor decision** — additive, no rewrite; preserve tests, no-look-ahead, safety, ORB, broker abstraction
2. **Target architecture** — data → indicators → analysis → strategy → risk → portfolio → backtest → broker → paper/live runner
3. **What to preserve** — all existing live-safety tools, broker abstractions, ORB strategy, data providers
4. **Current problems** — `main.py` too large, ORB-dominant, missing indicators/trend layers, metrics assume 5m bars
5. **Staged plan** — PR 1 (factory) → PR 2 (indicators) → PR 3 (trend analysis) → PR 4 (TrendFollowing) → PR 5 (position sizer) → PR 6 (metrics fix) → PR 7 (backtest runner) → PR 8 (slim main.py) → PR 9 (tools audit) → PR 10 (README)
6. **MVP definition** — SPY, long-only, 1h bars, trend-following, backtest first
7. **Safety rules** — fail-closed, no API keys stored, mock-only tests, strategy cannot call broker
8. **No-look-ahead requirements** — rolling breakout excludes current bar; indicators no future data; engine design preserved
9. **Relationship to Phase A/B** — complements, does not replace; Phase B implementation precedes PR 7
10. **Non-goals** — no code changes, no file moves, no trading, no broker calls in this PR

### Reference

- `docs/trend_bot_architecture_refactor_plan.md` — full plan
- `docs/automated_strategy_execution_roadmap.md` — architecture alignment note added
- Suggested git tag: `trend-bot-architecture-refactor-plan-designed`

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No code is implemented. No Alpaca endpoint was contacted. No credentials were read.**
> All refactor PRs must be individually reviewed before merging.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone: Strategy Factory Added — Refactor PR 1 Complete

**Branch:** `claude/add-strategy-factory`
**Status:** Complete

`src/strategy/factory.py` and `tests/test_strategy_factory.py` added.

**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No environment variables were accessed.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No live ledger was written.**
**No config was mutated.**
**No live or paper trading was implemented.**
**No automated trading was approved.**
**No main.py refactor was done.**
**No tools were moved.**
**No ORB behavior was changed.**

### What was added

| File | Description |
|------|-------------|
| `src/strategy/factory.py` | `build_strategy(name, params) → BaseStrategy`; `supported_strategy_names()` |
| `tests/test_strategy_factory.py` | 45 tests |

### Factory contract

- `build_strategy("opening_range_breakout", params)` → `OpeningRangeBreakout`
- `build_strategy("orb", params)` → `OpeningRangeBreakout` (alias)
- `params=None` treated as empty dict
- Caller's params dict never mutated
- Unknown name → `ValueError("unknown strategy name")` — raw name not echoed
- Invalid params → `ValueError("invalid strategy parameters")` — raw values not echoed
- No Alpaca import; no network; no environ; no execution layer imports

### Test coverage

| Metric | Value |
|--------|-------|
| Targeted tests | 45 passed |
| Full suite | 4209 passed |
| Real broker calls | None |
| Credentials read | None |

### Safety invariants confirmed

| Invariant | Confirmed |
|-----------|----------|
| No Alpaca SDK imported | ✓ (source scan) |
| No network library imports | ✓ (source scan) |
| No environment variable access | ✓ (source scan) |
| No execution layer imports | ✓ (source scan) |
| No submit/cancel/replace/close calls | ✓ (source scan) |
| No POST/PATCH/DELETE markers | ✓ (source scan) |
| Params dict not mutated | ✓ (TestParamsNotMutated) |
| Raw values not echoed in errors | ✓ (TestUnknownStrategyName, TestInvalidParams) |
| ORB behavior unchanged | ✓ (full suite 4209 passed) |

### Reference

- `src/strategy/factory.py` — factory source
- `tests/test_strategy_factory.py` — 45 tests
- `docs/trend_bot_architecture_refactor_plan.md` — PR 1 marked implemented

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> ORB behavior is unchanged. No live or paper execution was implemented.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone: Indicators Package Added — Refactor PR 2 Complete

**Branch:** `claude/add-indicators-package`
**Status:** Complete

`src/indicators/` package and `tests/test_indicators.py` added.

**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No environment variables were accessed.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No live ledger was written.**
**No config was mutated.**
**No live or paper trading was implemented.**
**No automated trading was approved.**
**No main.py refactor was done.**
**No tools were moved.**
**No strategy behavior was changed.**

### What was added

| File | Description |
|------|-------------|
| `src/indicators/__init__.py` | Package exports |
| `src/indicators/moving_average.py` | `sma()`, `ema()` |
| `src/indicators/volatility.py` | `true_range()`, `atr()` |
| `src/indicators/trend.py` | `rolling_high()`, `rolling_low()`, `breakout_above()`, `breakout_below()` |
| `tests/test_indicators.py` | 83 tests |

### Indicators contract

| Function | Signature | Notes |
|----------|-----------|-------|
| `sma` | `(values, window) → Series` | Rolling mean; NaN until window filled |
| `ema` | `(values, span) → Series` | EWM mean with `adjust=False` |
| `true_range` | `(high, low, close) → Series` | First row = H-L; subsequent rows use prev close |
| `atr` | `(high, low, close, window=14) → Series` | Rolling mean of true range |
| `rolling_high` | `(values, window, *, exclude_current=True) → Series` | Shift-before-roll when `exclude_current=True` |
| `rolling_low` | `(values, window, *, exclude_current=True) → Series` | Shift-before-roll when `exclude_current=True` |
| `breakout_above` | `(close, high, lookback) → Series[bool]` | close > rolling_high(high, lookback, exclude_current=True) |
| `breakout_below` | `(close, low, lookback) → Series[bool]` | close < rolling_low(low, lookback, exclude_current=True) |

### No-look-ahead guarantee

`rolling_high` and `rolling_low` default to `exclude_current=True`:
the series is shifted by one bar before the rolling window is applied.
This means a spike or crash on the current bar cannot affect the reference
level used for breakout detection at the same bar index.

### Test coverage

| Metric | Value |
|--------|-------|
| Targeted tests | 83 passed |
| Full suite | 4292 passed |
| Real broker calls | None |
| Credentials read | None |

### Test classes

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestPackageExports` | 8 | All symbols exported from `src/indicators` |
| `TestSma` | 9 | Known values; NaN before window; index; immutability; invalid window |
| `TestEma` | 8 | Matches pandas ewm; index; immutability; invalid span |
| `TestTrueRange` | 7 | First row; prev close; gap-up; gap-down; index; immutability |
| `TestAtr` | 8 | Known values; NaN until filled; default window; index; immutability; invalid window |
| `TestRollingHigh` | 9 | Include/exclude current; no-look-ahead spike; default; index; immutability; invalid window |
| `TestRollingLow` | 9 | Include/exclude current; no-look-ahead crash; default; index; immutability; invalid window |
| `TestBreakoutAbove` | 4 | True/false signals; current spike not counted; returns Series |
| `TestBreakoutBelow` | 3 | True/false signals; returns Series |
| `TestSourceScans` | 18 | No Alpaca/network/environ/execution/mutation markers in all 4 source files |

### Reference

- `src/indicators/` — indicators package
- `tests/test_indicators.py` — 83 tests
- `docs/trend_bot_architecture_refactor_plan.md` — PR 2 marked implemented

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> All indicator functions are pure, deterministic, and offline-only.
> No strategy behavior was changed. No live or paper execution was implemented.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone: Refactor PR 3 — Analysis / trend layer

**Date:** 2026-05-27
**Branch:** `claude/add-trend-analysis-layer`
**Files added:** `src/analysis/trend.py`, `src/analysis/__init__.py`, `tests/test_trend_analysis.py`
**Tests:** 106 new tests; full suite 4 398 passed

### What was implemented

`classify_trend(bars, *, symbol, timeframe, ...) → TrendState` — a pure,
offline, deterministic EMA-based trend classifier.

`TrendState` is a frozen dataclass with:
- `trend`: `"bullish"` / `"bearish"` / `"neutral"` / `"unknown"` — `"neutral"` = valid computed EMA relationship that is non-directional; `"unknown"` = validation failure or insufficient data (no indicators computed)
- `strength`: `"strong"` / `"weak"` / `"unknown"` (relative EMA spread vs 0.5% threshold)
- `volatility_regime`: `"high"` / `"low"` / `"normal"` / `"unknown"` (ATR ratio vs rolling median)
- `fast_ema`, `slow_ema`, `atr`: scalar floats
- `reason_codes`: tuple of string codes
- Safety fields: `deterministic=True`, `broker_calls_made=False`, `credentials_read=False`, `order_action_requested=False`

Validation gates (in order): INVALID_SYMBOL → INVALID_TIMEFRAME → INVALID_PERIOD →
INVALID_PERIOD_ORDER → MISSING_REQUIRED_COLUMNS → INSUFFICIENT_BARS.

### Test classes

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestTrendStateDefaults` | 9 | Frozen dataclass; safety fields; type checks |
| `TestValidationInvalidSymbol` | 8 | Empty; lowercase; too long; spaces; None; valid dot/hyphen |
| `TestValidationInvalidTimeframe` | 6 | Empty; unknown; uppercase; None; all valid; gate ordering |
| `TestValidationInvalidPeriod` | 6 | Zero/negative for each param; valid minimum |
| `TestValidationInvalidPeriodOrder` | 4 | Equal; fast > slow; valid; period checked before order |
| `TestValidationMissingColumns` | 5 | Missing each required column; empty df; all present |
| `TestValidationInsufficientBars` | 4 | Zero; one below min; exactly min; above min |
| `TestBullishTrend` | 8 | Trend/code/EMA ordering/close position on rising bars |
| `TestBearishTrend` | 6 | Trend/code/EMA ordering/close position on falling bars |
| `TestNeutralTrend` | 5 | Flat bars → neutral; no spurious codes; EMAs populated |
| `TestStrength` | 4 | Strong on wide spread; weak on flat; exclusivity |
| `TestVolatilityRegime` | 5 | High/low/normal detection; single code per result; ATR positive |
| `TestReasonCodes` | 5 | Exactly 3 codes on success; 1 on validation failure; string type |
| `TestGateOrder` | 5 | Each gate blocks the next |
| `TestDeterminism` | 3 | Same input → same output; different inputs → different outputs |
| `TestInputNotMutated` | 2 | Bars DataFrame and index unchanged after call |
| `TestSafetyFields` | 4 | All four safety fields across 5 different result states |
| `TestSymbolAndTimeframePassthrough` | 3 | Symbol/timeframe preserved in result and blocked state |
| `TestSourceScans` | 8 | No Alpaca/network/environ/broker-call patterns in source files |

### Reference

- `src/analysis/trend.py` — trend classification module
- `src/analysis/__init__.py` — package exports
- `tests/test_trend_analysis.py` — 106 tests
- `docs/trend_bot_architecture_refactor_plan.md` — PR 3 marked implemented

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> All analysis functions are pure, deterministic, and offline-only.
> No strategy behavior was changed. No live or paper execution was implemented.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone: Refactor PR 4 — TrendFollowing Strategy

**Date:** 2026-05-27
**Branch:** `claude/add-trend-following-strategy`
**Files added:** `src/strategy/trend_following.py`, `tests/test_trend_following_strategy.py`
**Files updated:** `src/strategy/factory.py`, `tests/test_strategy_factory.py`
**Tests:** 87 new (trend_following); 57 factory (12 new); full suite 4 497 passed

### What was implemented

`TrendFollowing(BaseStrategy)` — MVP long-only trend strategy.

**Entry:** `classify_trend().trend == "bullish"` AND `close > rolling_high(prior breakout_lookback bars, exclude_current=True)`.

**Exit:** `trend == "bearish"` OR `close < fast_ema`.

**ATR stop:** computed as `entry_price - atr_stop_mult * atr` and included in `Signal.stop_loss` and `Signal.meta["atr_stop_price"]`. No broker call is made.

**Factory:** `build_strategy("trend_following", params)` added. Existing ORB/alias routes unchanged.

**Signal metadata (all signals):**

| Field | Value |
|-------|-------|
| `strategy_name` | `"trend_following"` |
| `deterministic` | `True` |
| `broker_calls_made` | `False` |
| `credentials_read` | `False` |
| `order_action_requested` | `False` |
| `recommendation_only` | `True` |

**Reason strings (fixed, no raw values echoed):**

`BUY_BULLISH_BREAKOUT` · `SELL_BEARISH_TREND` · `SELL_CLOSE_BELOW_FAST_EMA`

### Test classes

| File | Class | Tests | What it covers |
|------|-------|-------|----------------|
| `test_trend_following_strategy.py` | `TestConstructor` | 5 | Default/None/empty/explicit params; no mutation |
| | `TestConstructorValidation` | 17 | Invalid symbol/periods/order/stop/risk/long_only; safe error messages |
| | `TestInsufficientBars` | 4 | Zero/below/exactly/above minimum bar count |
| | `TestBuySignal` | 12 | LONG direction; entry price; stop; all required meta fields; safety fields |
| | `TestNoBreakout` | 3 | No breakout → no BUY; neutral/bearish → no BUY |
| | `TestNoLookAhead` | 2 | Current-bar high spike excluded; truncated bars |
| | `TestExitSignal` | 4 | EXIT on bearish; correct reason; safety meta; no exit on flat |
| | `TestDeterminism` | 2 | Same input → same output; different bars → different signal |
| | `TestInputNotMutated` | 2 | Bars not mutated; index not mutated |
| | `TestStrategyIsBaseStrategy` | 4 | Subclass; generate_signal/reset callable; reset no-op |
| | `TestSourceScans` | 18 | No Alpaca/network/environ/execution/mutation/ledger patterns; no hardcoded timeframe |
| | `TestTimeframeParam` | 6 | Stored correctly; default "1h"; all valid values; invalid raises; secret not echoed; used in generate_signal |
| | `TestEntryCutoffDoesNotBlockExits` | 8 | EXIT emitted after cutoff for bearish and close_below_fast_ema; LONG blocked after cutoff; LONG allowed before cutoff; safety meta; no mutation |
| `test_strategy_factory.py` | `TestSupportedStrategyNames` | +1 | "trend_following" in supported names |
| | `TestBuildStrategyTrendFollowing` | 11 | Construction; None/empty params; param passthrough; invalid params; ORB preserved |

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no environment variables
- No order execution — signals are recommendation-only
- No live/paper trading — no scheduler, no live runner
- No position submission/cancellation/replacement/close
- No `src/main.py` or `src/tools/` modification
- ORB behavior unchanged — all 45 prior ORB factory tests still pass
- No look-ahead — `rolling_high(exclude_current=True)` always excludes the current bar's high from the breakout reference level

### Reference

- `src/strategy/trend_following.py` — TrendFollowing strategy
- `src/strategy/factory.py` — updated factory
- `tests/test_trend_following_strategy.py` — 87 tests
- `tests/test_strategy_factory.py` — 57 tests (12 new)
- `docs/trend_bot_architecture_refactor_plan.md` — PR 4 marked implemented

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> TrendFollowing signals are recommendations only — no execution layer was touched.
> ORB strategy behavior is unchanged.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 5: add-risk-position-sizer

**Date:** 2026-05-27
**Branch:** `claude/add-risk-position-sizer`
**Files added:** `src/risk/position_sizer.py`, `tests/test_position_sizer.py`
**Files changed:** `src/risk/__init__.py` (circular import fix)
**Tests:** 79 new; full suite 4 576 passed

### What was implemented

Pure, offline position-sizing helpers for long trades.

**`calculate_shares_by_risk(equity, risk_pct, entry_price, stop_price, *, max_notional=None) → int`**

Formula:
```
risk_amount    = equity * risk_pct / 100
per_share_risk = entry_price - stop_price
shares         = floor(risk_amount / per_share_risk)

# with optional hard cap:
shares = min(shares, floor(max_notional / entry_price))
```

Returns `max(0, shares)`. Sub-1 result returns 0 — trade not sized.

**`calculate_notional(shares, entry_price) → float`** — dollar value of a position (`shares * entry_price`).

**Validation** — all invalid inputs raise `ValueError("invalid position sizing parameters")`. Raw values are never echoed.
All numeric inputs are checked for finiteness (NaN and ±inf rejected before any math operation).
Fractional shares (e.g., 1.5) are rejected in `calculate_notional`; integer-like floats (e.g., 1.0) are accepted.

| Parameter | Constraint |
|-----------|------------|
| `equity` | finite `> 0` (float) |
| `risk_pct` | finite `> 0` (float) |
| `entry_price` | finite `> 0` (float) |
| `stop_price` | finite `> 0` AND `< entry_price` (float) |
| `max_notional` | `None` OR finite `> 0` (float) |
| `shares` (notional) | non-negative int or integer-like float |
| `entry_price` (notional) | finite `> 0` (float) |

**Internal helpers** (not part of public API):
- `_to_finite_float(val)` — converts to float, rejects non-finite values.
- `_to_non_negative_int(val)` — accepts int or integer-like float, rejects fractional/NaN/inf/negative.

**`src/risk/__init__.py`** — removed eager `from .risk_manager import RiskManager` re-export that caused a circular import when `position_sizer` was imported in isolation. No consumer imported `from src.risk import RiskManager`; all callers used `from src.risk.risk_manager import RiskManager` directly.

### Test classes

| File | Class | Tests | What it covers |
|------|-------|-------|----------------|
| `test_position_sizer.py` | `TestCalculateSharesByRisk` | 10 | Standard case; fractional floor; zero/one share; large equity; small/high risk pct; int return; non-negative; no max_notional |
| | `TestMaxNotional` | 6 | Caps shares; higher cap no-ops; exact match; zero shares; None ignored; fractional floor |
| | `TestValidationEquity` | 5 | Zero/negative/None/string equity; error message safe |
| | `TestValidationRiskPct` | 3 | Zero/negative/string risk_pct; secret not echoed |
| | `TestValidationEntryPrice` | 2 | Zero/negative entry price |
| | `TestValidationStopPrice` | 5 | Zero/negative/equal/above entry; string stop; secret not echoed |
| | `TestValidationMaxNotional` | 2 | Zero/negative max_notional |
| | `TestValidationNanInf` | 11 | NaN/inf rejected for all five numeric params; error message exact |
| | `TestDeterminism` | 3 | Same input → same output; different inputs → different outputs; no state between calls |
| | `TestCalculateNotional` | 8 | Standard; zero/one share; float return; negative shares; zero/negative price; string shares safe |
| | `TestCalculateNotionalEdgeCases` | 7 | Fractional shares raises; NaN/inf shares raises; NaN/inf entry_price raises; 1.0 accepted; secret not echoed |
| | `TestSourceScans` | 17 | No Alpaca/network/environ/execution/mutation markers; no ledger/config |

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no environment variables
- No order execution — pure math helpers with no side effects
- No live/paper trading — no scheduler, no live runner
- No position submission/cancellation/replacement/close
- No `src/main.py` or `src/tools/` modification
- No look-ahead — functions are stateless and deterministic
- NaN/inf values are rejected before any math operation — no raw OverflowError or floor(nan) leakage
- Fractional shares (1.5) are rejected; integer-like floats (1.0) are accepted
- `src/risk/risk_manager.py` behavior unchanged — no logic was touched

### Reference

- `src/risk/position_sizer.py` — position sizing helpers
- `tests/test_position_sizer.py` — 79 tests
- `docs/trend_bot_architecture_refactor_plan.md` — PR 5 marked implemented

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> Position sizing functions are pure math helpers — no execution layer was touched.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 6: fix-backtest-metrics-annualization

**Date:** 2026-05-27
**Branch:** `claude/fix-backtest-metrics-annualization`
**Files updated:** `src/backtest/metrics.py`, `src/backtest/engine.py`
**Files added:** `tests/test_backtest_metrics.py`
**Tests:** 41 new; full suite 4 617 passed

### What was implemented

Interval-aware Sharpe-ratio annualisation for backtest metrics, with Yahoo-compatible interval aliases.

**Root cause:** `compute_metrics()` hardcoded `bars_per_year = 252 * 78` (5-minute bars).
1h and 1d strategies silently used the wrong annualisation factor. Additionally, `BacktestEngine`
stores `bar_interval="60m"` when Yahoo data is requested with the 60-minute string, but
`bars_per_year_for_interval` did not accept `"60m"`, which would raise at metrics time.

**Fix:**
- `bars_per_year_for_interval(interval: str) → int` — new public helper. Returns the number
  of bars per trading year for each supported interval. Uses US equity regular-session
  assumptions: 252 trading days, 6.5 hours/day, 390 minutes/day.
  For hourly intervals, only complete bars within the session are counted.
  `"60m"` accepted as a Yahoo-compatible alias for `"1h"`.
  Unknown interval raises `ValueError("invalid interval")`; raw value never echoed.
- `compute_metrics(...)` gains `interval: str = "5m"` — default preserves all existing
  callers. Sharpe ratio now uses `bars_per_year_for_interval(interval)`.
- `BacktestEngine.run()` now passes `interval=self._bar_interval` to `compute_metrics()`.

| Interval | Bars/year | Basis | Note |
|----------|-----------|-------|------|
| `"1m"` | 98 280 | 252 × 390 min/day | |
| `"2m"` | 49 140 | 252 × 195 bars/day | Yahoo-supported |
| `"5m"` | 19 656 | 252 × 78 bars/day | |
| `"15m"` | 6 552 | 252 × 26 bars/day | |
| `"30m"` | 3 276 | 252 × 13 bars/day | |
| `"60m"` | 1 512 | 252 × 6 complete bars/day | Yahoo alias for `"1h"` |
| `"1h"` | 1 512 | 252 × 6 complete bars/day | |
| `"90m"` | 1 008 | 252 × 4 bars/day | Yahoo-supported; floor(390/90)=4 |
| `"2h"` | 756 | 252 × 3 complete bars/day | |
| `"4h"` | 252 | 252 × 1 complete bar/day | |
| `"1d"` | 252 | 252 trading days | |

**Unchanged metrics** — `total_return_pct`, `annualized_return_pct` (CAGR, calendar-based),
`max_drawdown_pct`, `num_trades`, win-rate, avg win/loss, commission.

### Test classes

| File | Class | Tests | What it covers |
|------|-------|-------|----------------|
| `test_backtest_metrics.py` | `TestBarsPerYearForInterval` | 13 | All 11 intervals (incl. `"2m"`, `"60m"`, `"90m"`); invalid raises; secret not echoed |
| | `TestComputeMetricsInterval` | 11 | Default=5m; Sharpe changes with interval; 1d uses 252 exactly; 1h uses 1512 exactly; 60m matches 1h Sharpe; total_return/max_dd/trade_count unchanged; deterministic; no mutation; empty curve |
| | `TestSourceScans` | 17 | No Alpaca/network/environ/execution/mutation/ledger markers |

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no environment variables
- No order execution — metrics are read-only computations on completed backtest data
- No live/paper trading — no scheduler, no live runner
- No `src/main.py`, `src/tools/`, `src/execution/`, `src/portfolio/`, `src/strategy/` changes
- No backtest execution behaviour changed — engine loop, trade logic, and portfolio unchanged
- No look-ahead — metrics compute on already-completed backtest output
- Existing `test_backtest.py` metrics tests all still pass

### Reference

- `src/backtest/metrics.py` — updated metrics module
- `src/backtest/engine.py` — passes `interval=self._bar_interval` to `compute_metrics`
- `tests/test_backtest_metrics.py` — 41 tests
- `docs/trend_bot_architecture_refactor_plan.md` — PR 6 marked implemented

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> Metrics functions are pure read-only computations — no execution layer was touched.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 7: add-backtest-runner-integration

**Date:** 2026-05-27
**Branch:** `claude/add-backtest-runner-integration`
**Files added:** `src/backtest/backtest_runner.py`, `tests/test_backtest_runner.py`
**Tests:** 67 new; full suite 4 684 passed

### What was implemented

Offline backtest runner that wires strategy factory + `BacktestEngine` into a single
callable entry point with typed, immutable configuration and result objects.

**Added:**
- `BacktestRunConfig` (frozen dataclass) — carries all inputs: `strategy_name`,
  `strategy_params`, `symbols`, `start_date`, `end_date`, `bar_interval="5m"`,
  `initial_capital=100_000.0`, `position_size_pct=0.95`, `stop_execution="bar_close"`.
- `BacktestRunResult` (frozen dataclass) — carries all outputs: `metrics`, `trades`,
  `equity_curve`, `order_intents`, echoed config fields, plus six read-only safety flags
  (`broker_calls_made=False`, `credentials_read=False`, `live_submit_enabled=False`,
  `order_action_requested=False`, `paper_trading_enabled=False`, `recommendation_only=True`).
- `run_backtest(config, *, data_provider)` — validates config (raises
  `ValueError("invalid backtest run config")` without echoing raw values); calls
  `build_strategy()` → constructs `Portfolio` + `RiskManager` → wires `BacktestEngine` →
  calls `engine.run()` → wraps into `BacktestRunResult`.
  All six safety flags are hardcoded `False`/`True`; the function contains no path to
  set them otherwise.
- `_validate_config` rejects non-finite `initial_capital` (`math.isfinite()`); rejects
  symbols that don't match `^[A-Z0-9.\-/]{1,10}$` (uppercase ticker regex); never echoes
  raw values in any error message.

**Not changed:** `BacktestEngine`, `compute_metrics`, `Portfolio`, `RiskManager`,
`BaseDataProvider`, `build_strategy`, or any existing test.

### Test classes

| File | Class | Tests | What it covers |
|------|-------|-------|----------------|
| `test_backtest_runner.py` | `TestBacktestRunConfig` | 7 | Frozen; defaults; custom values stored |
| | `TestBacktestRunResult` | 16 | Safety flags all False/True; frozen; echoed fields; metrics keys; types |
| | `TestRunBacktestValidation` | 23 | Invalid interval; zero/negative/inf/nan capital; bad pct; bad stop_execution; empty symbols; lowercase/space/invalid symbol; secret symbol not echoed; dot/dash symbol valid; unknown strategy; bad params; raw values not echoed; valid aliases |
| | `TestRunBacktestBehaviour` | 7 | Deterministic; empty data; capital matches; Sharpe uses interval; config not mutated; result copy independence; all safety flags |
| | `TestSourceScans` | 14 | No Alpaca/network/environ/execution-actions/mutation/ledger markers |

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no environment variables
- No order execution — `run_backtest` produces advisory output only; `order_intents` are
  audit records, never sent to a broker
- No live/paper trading — no scheduler, no live runner, no paper runner
- Six safety flags are immutable in `BacktestRunResult`; no code path can set them to `True`
- No `src/main.py`, `src/tools/`, `src/execution/broker*`, or live-gate file changes
- No backtest execution behaviour changed — engine loop, trade logic unchanged

### Reference

- `src/backtest/backtest_runner.py` — runner module
- `tests/test_backtest_runner.py` — 67 tests
- `docs/trend_bot_architecture_refactor_plan.md` — PR 7 marked implemented

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> `BacktestRunResult.broker_calls_made` is always `False`. No execution layer was triggered.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 8 Design: Main Dispatcher Slimdown Designed

**Date:** 2026-05-27
**Branch:** `claude/docs-design-main-dispatcher-slimdown`
**Files added:** `docs/main_dispatcher_slimdown_design.md`
**Files updated:** `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Type:** Docs-only. No `src/`, `tests/`, `output/`, or `config/` changes.

### What was designed

Design for slimming `src/main.py` (903 lines) into a thin dispatcher that delegates
to `backtest_runner`, `SweepRunner`, `WalkForwardRunner`, and fail-closed paper/live
gates.

**Current problem:**
- `main.py` constructs `BacktestEngine`, `Portfolio`, and `RiskManager` directly in
  `build_engine()`, duplicating logic now handled by `backtest_runner.py`.
- Mode dispatch is spread across 60+ lines with no single dispatch table.
- `engine._portfolio.positions` (private field access) is used on line 851.
- No explicit live-mode rejection — live is currently blocked only by argparse choices.

**Target (after all sub-PRs):**
- `main.py` is a dispatcher only: parse CLI, load config, call the appropriate runner.
- Backtest modes route through `run_backtest(BacktestRunConfig(...), data_provider=...)`.
- `build_engine()` removed from `main.py`.
- `--mode live` becomes an explicit `NotImplementedError` (fail-closed).
- Paper gate logic unchanged.

**Sub-PR sequence:**

| Sub-PR | Goal | Scope |
|--------|------|-------|
| 8A | CLI regression tests for current `main.py` | Add `tests/test_main_cli.py` |
| 8B | Route backtest modes through `backtest_runner` | Modify `src/main.py` |
| 8C | Remove `build_engine()` from `main.py` | Modify `src/main.py` |
| 8D | Paper/live fail-closed placeholders | Modify `src/main.py` |
| 8E | README usage update | Modify `README.md` |

### Safety confirmations

- No live trading designed, approved, or implemented
- No Alpaca SDK imported in any design path
- No credentials read
- No order submission
- Paper gate logic is explicitly documented as unchanged
- `--mode live` maps to `NotImplementedError` — not to any Alpaca or execution call
- This PR contains zero `src/`, `tests/`, `output/`, or `config/` changes
- Full test suite unchanged; no regression possible from a docs-only PR

### Validation

```bash
git diff origin/main...HEAD -- src tests output config
# Expected: empty
```

### Reference

- `docs/main_dispatcher_slimdown_design.md` — full design document
- `docs/trend_bot_architecture_refactor_plan.md` — PR 8 marked designed (sub-PRs 8A–8E listed)

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> This is a design document only. No code was changed.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 8A: add-main-characterization-tests

**Date:** 2026-05-27
**Branch:** `claude/add-main-characterization-tests`
**Files added:** `tests/test_main_characterization.py`
**Files updated:** `docs/main_dispatcher_slimdown_design.md`, `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** 42 new; full suite 4 726 passed
**Type:** Test + docs. No `src/main.py`, `src/backtest/`, `config/`, or `output/` changes.

### What was implemented

Characterization test suite locking current `src/main.py` behaviour before the PR 8
refactor.  No production code was changed.

### Test classes

| File | Class | Tests | What it covers |
|------|-------|-------|----------------|
| `test_main_characterization.py` | `TestMainImport` | 6 | Module has expected callables; `AlpacaBrokerAdapter` not a module-level name |
| | `TestParseArgs` | 13 | All 4 current modes accepted; `--mode live` and `--mode paper` rejected; defaults; custom args; `--help` exits 0 |
| | `TestCandidateBOverrides` | 4 | Hardcoded constants: `QQQ`, `09:45`, `close`, `0.50` |
| | `TestApplyCandidateB` | 6 | All 4 overrides applied; original config not mutated; returns different object |
| | `TestMainPaperGate` | 3 | Default `execution.mode` is `"backtest"`; `paper_trading_enabled` is `False`; `NotImplementedError` when paper enabled=False but mode=paper |
| | `TestMainModeDispatch` | 5 | `backtest` calls `build_engine`; `candidate-b` applies overrides first; `sweep` calls `SweepRunner`; `walk-forward` calls `WalkForwardRunner`; Alpaca not touched in backtest path |
| | `TestSourceCharacterization` | 5 | All 4 mode strings in source; `alpaca_broker` not imported at module top level |

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no env vars
- Paper execution path not triggered in any test (all tests use mocked `load_config`)
- No order submission — all dispatch tests mock `build_engine` and `ReportGenerator`
- No `src/main.py`, `src/backtest/`, `src/strategy/`, `src/execution/broker*`, config, or output changes
- `test_backtest_mode_does_not_touch_alpaca` explicitly asserts Alpaca is never instantiated

### Reference

- `tests/test_main_characterization.py` — 42 characterization tests
- `docs/main_dispatcher_slimdown_design.md` — PR 8A marked implemented
- `docs/trend_bot_architecture_refactor_plan.md` — sub-PR 8A ✓

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> These are characterization tests only — no production behaviour was changed.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 8B: route-main-backtest-through-runner

**Date:** 2026-05-27
**PR:** route-main-backtest-through-runner
**Branch:** claude/route-main-backtest-through-runner

### What was done

Routed `src/main.py` backtest and candidate-b dispatch through
`src/backtest/backtest_runner.run_backtest()`.  `build_engine()` is no longer
called for these modes.

### Files changed

| File | Change |
|------|--------|
| `src/backtest/backtest_runner.py` | Extended `BacktestRunConfig` with 6 new optional fields; updated `run_backtest()` to pass them to `Portfolio` and `RiskManager` |
| `src/main.py` | Replaced `build_engine()` dispatch with `BacktestRunConfig` + `run_backtest()` |
| `tests/test_main_characterization.py` | Updated dispatch tests to patch `run_backtest`; added `test_backtest_run_config_core_fields`; renamed two tests |
| `tests/test_paper_trading_readiness.py` | Updated startup-log helpers to abort at `run_backtest` instead of `build_engine` |
| `docs/main_dispatcher_slimdown_design.md` | PR 8B marked implemented |
| `docs/live_readiness_status.md` | This milestone appended |
| `docs/trend_bot_architecture_refactor_plan.md` | Sub-PR 8B ✓ |

### New `BacktestRunConfig` fields (all optional, defaults match previous behavior)

| Field | Default | Source in `AppConfig` |
|-------|---------|-----------------------|
| `commission_per_share` | `0.005` | `cfg.backtest.commission_per_share` |
| `slippage_per_share` | `0.01` | `cfg.backtest.slippage_per_share` |
| `force_exit_time` | `"15:55"` | `cfg.strategy.params.get("force_exit_time", "15:55")` |
| `max_open_positions` | `None` | `cfg.risk.max_open_positions` |
| `daily_loss_limit_pct` | `None` | `cfg.risk.daily_loss_limit_pct` |
| `daily_loss_action` | `"block_new_entries"` | `cfg.risk.daily_loss_action` |

### Test counts

- `tests/test_main_characterization.py` — 43 tests (+1 `test_backtest_run_config_core_fields`)
- `tests/test_backtest_runner.py` — 94 tests (+27 `TestNewFieldValidation`)
- `tests/test_paper_trading_readiness.py` — 24 tests (unchanged count)
- Full suite: **4 754 passed**

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no env vars
- Paper execution path not triggered — `_run_paper_close` and paper gate unchanged
- No order submission — `BacktestRunResult.broker_calls_made` always `False`
- `engine._portfolio.positions` private-field access eliminated; replaced with `open_positions_count=0`
- `build_engine()` remains in `main.py` (unused by dispatch; removed in PR 8C)
- No `src/backtest/engine.py`, `src/strategy/`, `src/execution/broker*`, config, or output changes

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> No production behaviour was changed — only the internal wiring of the backtest dispatch.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 8C: remove-main-build-engine

**Date:** 2026-05-27
**Branch:** `claude/remove-main-build-engine`
**Files changed:** `src/main.py`, `tests/test_main_characterization.py`, `tests/test_backtest.py`, `tests/test_alpaca_broker_skeleton.py`, `tests/test_paper_trading_readiness.py`, and paper-path test files
**Files updated (docs):** `docs/main_dispatcher_slimdown_design.md`, `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** 4 752 passed (−2 from PR 8B: `TestBuildEngineWiring` removed)
**Type:** Refactor + test update. No new features, no behavior change.

### What was done

Deleted `build_engine()` from `src/main.py` and removed all imports that were only used by it. Updated all tests that were patching `src.main.build_engine` to patch `src.backtest.backtest_runner.run_backtest` instead. `run_backtest` remains the sole dispatch path for backtest and candidate-b modes.

### Files changed

| File | Change |
|------|--------|
| `src/main.py` | `build_engine()` deleted; `Portfolio`, `RiskManager`, `OpeningRangeBreakout` top-level imports removed; `BacktestEngine` import retained for `plot_equity_curve` |
| `tests/test_main_characterization.py` | `test_build_engine_is_callable` renamed to `test_build_engine_is_not_present` |
| `tests/test_backtest.py` | `TestBuildEngineWiring` class and `_make_app_config` helper removed |
| `tests/test_alpaca_broker_skeleton.py` | All `mock.patch("src.main.build_engine", ...)` calls replaced with `mock.patch("src.backtest.backtest_runner.run_backtest", ...)` |
| `tests/test_paper_*.py` | Same `build_engine` → `run_backtest` patch replacement |
| `tests/test_paper_trading_readiness.py` | Stale comment updated (`build_engine` → `run_backtest`) |

### Test counts

| File | Targeted tests |
|------|---------------|
| `tests/test_main_characterization.py` | 43 (unchanged count; test renamed) |
| Full suite | **4 752 passed** |

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no env vars
- Paper execution path not triggered — `_run_paper_close` and paper gate unchanged
- No order submission — `BacktestRunResult.broker_calls_made` always `False`
- `build_engine` symbol confirmed absent: `not hasattr(src.main, "build_engine")` ✓
- `run_backtest` remains the sole dispatch path for backtest/candidate-b modes ✓
- No `src/backtest/engine.py`, `src/backtest/backtest_runner.py`, `src/strategy/`, `src/execution/`, config, or output changes

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> No production behaviour was changed — only `build_engine` removed; `run_backtest` was already the dispatch path since PR 8B.
> No strategy is connected to live or paper trading.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 8D: clarify-main-paper-live-placeholders

**Date:** 2026-05-28
**Branch:** `claude/clarify-main-paper-live-placeholders`
**Files changed:** `src/main.py`, `tests/test_main_characterization.py`
**Files updated (docs):** `docs/main_dispatcher_slimdown_design.md`, `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** 4 754 passed (+2 from PR 8C: two source-scan tests added)
**Type:** Clarification + test. No behavior change. No new features.

### What was done

Removed the stale `TODO (Alpaca integration)` comment from `src/main.py` that implied
live trading would be added as a simple `--mode live` flag swap. Replaced it with an
accurate note: paper/live are not valid `--mode` options; paper is gated via config;
live is not enabled. Updated the paper gate `NotImplementedError` message to remove the
phrase "not yet wired". Added two source-scan regression tests.

### Files changed

| File | Change |
|------|--------|
| `src/main.py` | Stale TODO removed; paper gate message clarified |
| `tests/test_main_characterization.py` | `test_no_stale_live_mode_todo`, `test_live_not_in_cli_choices` added to `TestSourceCharacterization` |

### Test counts

| File | Targeted tests |
|------|---------------|
| `tests/test_main_characterization.py` | 45 (+2) |
| `tests/test_paper_trading_readiness.py` | 24 (unchanged) |
| Full suite | **4 754 passed** |

### Safety confirmations

- No broker/API access — no Alpaca calls, no HTTP, no credentials, no env vars
- Paper gate logic unchanged — `_run_paper_close` and all guards unmodified
- Paper gate still raises `NotImplementedError` when `paper_trading_enabled=False` ✓
- `--mode live` remains argparse-rejected (not added to `choices`) ✓
- `run_backtest` remains the sole dispatch path for backtest/candidate-b modes ✓
- `build_engine` remains absent from `src.main` ✓
- No `src/backtest/`, `src/execution/`, `src/strategy/`, config, or output changes

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> No production behaviour was changed — only stale comments removed and error messages clarified.
> Paper/live remain fail-closed: paper requires explicit config gate; live is not enabled.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 8E: docs-update-readme-current-usage

**Date:** 2026-05-28
**Branch:** `claude/docs-update-readme-current-usage`
**Files changed:** `README.md`
**Files updated (docs):** `docs/main_dispatcher_slimdown_design.md`, `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** not run (docs-only PR; no src/tests/config/output changes)
**Type:** Docs-only. No code, test, config, or output changes.

### What was done

`README.md` fully rewritten to reflect the current state of the repository after PR
8A–8D. The previous README was written before the main-dispatcher refactor and contained
stale references to `build_engine()`, `python -m unittest discover`, and an incomplete
project structure. Key updates:

- Project structure updated to include `indicators/`, `analysis/`, `strategy/factory.py`,
  `backtest/backtest_runner.py`, `experiments/`, `reporting/`, `execution/paper_*.py`,
  `tools/live_*.py`, `risk/position_sizer.py`.
- CLI modes section: `backtest`, `candidate-b`, `sweep`, `walk-forward` documented with
  examples. `--mode live` and `--mode paper` explicitly documented as rejected.
- Strategies section: ORB (legacy/benchmark) and TrendFollowing (MVP) both documented.
  Strategy factory (`src/strategy/factory.py`) documented with accepted name strings.
- Architecture section: dispatcher model described; `run_backtest()` is the sole backtest
  dispatch path; `build_engine` noted as removed.
- Tests section: `python -m pytest` commands replace stale `python -m unittest discover`.
- Metrics section: Sharpe ratio annualisation noted as interval-aware (not hardcoded 252×78).
- Safety table: no live trading, no paper trading by default, no credentials required,
  no order submission in backtest, no look-ahead bias.
- Stale Roadmap/TODOs referencing `build_engine()` removed.

### Validation

```bash
git diff origin/main...HEAD -- src tests config output
# Expected: empty (no src/tests/config/output changes)
```

### Safety confirmations

- No `src/`, `tests/`, `config/`, or `output/` files changed
- No broker/API access — no Alpaca calls, no HTTP, no credentials
- No order submission
- Paper/live remain fail-closed and unchanged
- No automated trading approved or enabled

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> This is a docs-only PR — no production behaviour was changed.
> Paper/live remain fail-closed: paper requires explicit config gate; live is not enabled.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 9A: docs-design-tools-scripts-isolation

**Date:** 2026-05-28
**Branch:** `claude/docs-design-tools-scripts-isolation`
**Files added:** `docs/tools_scripts_isolation_design.md`
**Files updated:** `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** not run (docs-only PR; no src/tests/config/output/scripts changes)
**Type:** Docs-only. No code, test, config, output, or script changes.

### What was designed

Design document for PR 9 (`src/tools/` isolation), covering:

- Full inventory of all 40 tools across four categories:
  live safety/readiness (30), manual live/paper guard (4), paper diagnostics (6).
- Classification: 34 tools are permanent in `src/tools/`; 6 paper diagnostics
  are move candidates (conditional on preconditions).
- All 40 tools have existing test coverage — no tool has zero tests.
- Staged sub-PR plan: 9A design → 9B inventory tests → 9C scripts/ README →
  9D conditional paper tool moves → 9E live-tool confirmation → 9F doc update.
- Rules: no moves without passing tests; no deletions without coverage;
  live/paper tools remain fail-closed; no broker/API/credentials.

### Validation

```bash
git diff origin/main...HEAD -- src tests config output scripts
# Expected: empty
```

### Safety confirmations

- No `src/`, `tests/`, `config/`, `output/`, or `scripts/` files changed
- No broker/API access — no Alpaca calls, no HTTP, no credentials
- No order submission. No live or paper trading enabled or changed.
- All live/paper tools remain in `src/tools/` and fail-closed.
- No automated trading approved.

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> This is a design document only. No code was changed.
> All live-readiness and paper guard tools remain in `src/tools/` and untouched.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 9B: add-tools-inventory-tests

**Date:** 2026-05-28
**Branch:** `claude/add-tools-inventory-tests`
**Files added:** `tests/test_tools_inventory.py`
**Files updated:** `docs/tools_scripts_isolation_design.md`, `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** 363 new tests added; full suite 5 117 passed.
**Type:** Tests + docs. No src, config, output, or scripts changes.

### What was added

`tests/test_tools_inventory.py` locks the PR 9A classification of all 40 tools in `src/tools/`:

- **TestToolsInventory** — asserts 30/4/6/40 counts; file existence for all 40 tools;
  mutual exclusivity of categories; no unclassified tools on disk; no phantom tools in lists.
- **TestToolsTestCoverage** — asserts every tool has a `tests/test_{name}.py`.
- **TestLiveToolsHaveMain** — asserts every live safety + manual-guard tool defines `main()`.
- **TestToolsSourceScan** — AST-based checks: no module-level Alpaca imports; no module-level
  `os.environ` reads; no module-level order-mutation calls; no hardcoded secret literals;
  `live_submit_enablement_gate` does not set `LIVE_SUBMIT_ENABLED = True` at top level.
- **TestToolsImportSafety** — all 40 tools importable; no module-level
  `from src.main import build_engine`.

### Validation

```bash
python -m pytest tests/test_tools_inventory.py  # 363 passed
python -m pytest                                  # 5 117 passed
git diff origin/main...HEAD -- src config output scripts
# Expected: empty (no src/config/output/scripts changes)
```

### Safety confirmations

- No `src/`, `config/`, `output/`, or `scripts/` files changed
- No broker/API access — no Alpaca calls, no HTTP, no credentials
- No order submission. No live or paper trading enabled or changed.
- All live/paper tools remain in `src/tools/` and fail-closed.
- No automated trading approved.

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> This is a test/docs PR only. No tool source files were changed.
> All live-readiness and paper guard tools remain in `src/tools/` and untouched.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 9C: add-scripts-readme-tools-classification

**Date:** 2026-05-28
**Branch:** `claude/add-scripts-readme-tools-classification`
**Files added:** `scripts/README.md`
**Files updated:** `docs/tools_scripts_isolation_design.md`, `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** not run (docs-only PR; no src/tests/config/output changes)
**Type:** Docs-only. No code, test, config, or output changes.

### What was added

`scripts/README.md` documents the `scripts/` directory purpose and current tool classification:

- **Purpose section** — explains `scripts/` vs `src/tools/` distinction.
- **Permanent tools (30 + 4)** — full tables of live safety/readiness and manual-guard tools
  that must stay in `src/tools/` permanently (CLI surface, operator runbooks).
- **Conditional move candidates (6)** — paper diagnostic utilities with explicit preconditions
  required before any move (PR 9D, conditional).
- **Rules** — six rules governing any future additions to `scripts/`.
- **Safety guarantees table** — documents how each guarantee is enforced.

No files were moved. No tool source files were changed.
`tests/test_tools_inventory.py` (PR 9B) continues to enforce the 30/4/6/40 classification.

### Validation

```bash
git diff origin/main...HEAD -- src tests config output
# Expected: empty (no src/tests/config/output changes)
```

### Safety confirmations

- No `src/`, `tests/`, `config/`, or `output/` files changed
- No broker/API access — no Alpaca calls, no HTTP, no credentials
- No order submission. No live or paper trading enabled or changed.
- All live/paper tools remain in `src/tools/` and fail-closed.
- No automated trading approved.

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> This is a docs-only PR. No tool source files were changed.
> All live-readiness and paper guard tools remain in `src/tools/` and untouched.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.

---

## Milestone — Refactor PR 9E: confirm-live-tools-stay-in-src-tools

**Date:** 2026-05-28
**Branch:** `claude/confirm-live-tools-stay-in-src-tools`
**Files updated:** `tests/test_tools_inventory.py`, `docs/tools_scripts_isolation_design.md`, `docs/trend_bot_architecture_refactor_plan.md`, `docs/live_readiness_status.md`
**Tests:** 76 new tests added; targeted 439 passed; full suite 5 193 passed.
**Type:** Tests + docs. No src/tools, config, or output changes.

### What was added

`TestPermanentToolsLocation` class in `tests/test_tools_inventory.py` (76 tests):

- **counts** — asserts 34 permanent tools (30 live safety + 4 manual guard); constituent counts unchanged.
- **`test_permanent_tool_in_src_tools[*]`** — parametrised over 34 tools: each file exists in `src/tools/`.
- **`test_permanent_tool_not_in_scripts[*]`** — parametrised over 34 tools: no file exists in `scripts/`.
- **`test_no_live_tool_file_in_scripts`** — no `live_*.py` present in `scripts/`.
- **`test_no_manual_tool_file_in_scripts`** — no `manual_*.py` present in `scripts/`.
- **`test_scripts_readme_documents_permanent_tools`** — `scripts/README.md` uses "permanent" and references `src/tools/`.
- **`test_scripts_readme_lists_live_safety_count`** — `scripts/README.md` mentions count 30.
- **`test_scripts_readme_lists_manual_guard_count`** — `scripts/README.md` mentions count 4.

### Validation

```bash
python -m pytest tests/test_tools_inventory.py  # 439 passed
python -m pytest                                  # 5 193 passed
git diff origin/main...HEAD -- src/tools src/backtest src/config src/data src/execution src/experiments src/indicators src/portfolio src/reporting src/risk src/strategy src/utils config output
# Expected: empty
```

### 34 permanent tools confirmed in `src/tools/`

All 30 live safety/readiness gate tools and all 4 manual live/paper guard tools
are confirmed present in `src/tools/` and absent from `scripts/`.
No tool was moved in this PR.

### Safety confirmations

- No `src/tools/` or other `src/` files changed
- No `config/` or `output/` files changed
- No broker/API access — no Alpaca calls, no HTTP, no credentials
- No order submission. No live or paper trading enabled or changed.
- All live/paper tools remain in `src/tools/` and fail-closed.
- No automated trading approved.

### Warning

> **This milestone does not approve automated live trading.**
> **This milestone does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> No tool source files were changed. No files were moved.
> All live-readiness and paper guard tools remain in `src/tools/` and untouched.
> The Phase A–H safety roadmap remains unchanged and required.
> Nothing in this repository is financial advice.
