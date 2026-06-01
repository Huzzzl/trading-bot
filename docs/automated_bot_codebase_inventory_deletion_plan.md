# Automated Bot Codebase Inventory and Deletion Plan

Design document for PR R1: audit the existing codebase against the target of a
fully automated online trading bot and produce a concrete classification and
deletion/archive plan.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**

---

## 1. Direction Reset

### 1.1 PR 10Z closes the manual-runbook chain

PR 10Z (`docs/real_data_60m_only_cached_checker_runbook.md`) is the last
deliverable of the 10-series diagnostic chain (10U–10Z). That chain produced:

- Policy design and characterization tests for the daily-bar session_end artifact
- A fail-closed guard blocking the broken daily+force_exit config
- A post-guard snapshot confirming 60m results are unchanged
- A formal 60m-only evaluation scope
- An operator runbook for running the 60m checker

**The 10-series runbook chain is complete. No further 10-series runbook or
snapshot PRs should be opened unless directly required by automated runtime.**

### 1.2 New primary objective

The project target is a **fully automated online trading bot** with staged rollout:

```
offline validation
    → automated paper state machine
        → paper forward observation
            → limited live automation
```

This is not a manual-submit + operator-checklist system. The existing manual
safety infrastructure, live-readiness checklists, and paper submit runbooks
served a purpose during the manual phase, but they are not the final system.

### 1.3 What must stop

The following work must **not** continue unless directly required by automated
runtime:

- New manual safety runbooks
- New operator checklist docs
- New snapshot docs for manually-run tools
- Expanding the live-readiness checklist
- New paper diagnostic utilities used only by human operators

### 1.4 What must start

- Extracting the paper execution path from `src/main.py` into a proper runtime
  module (`src/execution/paper_runner.py`)
- Building the automated state machine skeleton
- Building the automated risk gate skeleton
- Archiving manual-only tools that block codebase clarity
- Reducing tool inventory to ACTIVE_TOOLS (runtime + research) only

---

## 2. Classification Labels

| Label | Meaning |
|-------|---------|
| `KEEP_RUNTIME` | Directly needed by automated bot runtime; must not be deleted |
| `KEEP_RESEARCH` | Needed for strategy research, backtest, or OOS validation |
| `CONVERT_TO_RUNTIME` | Useful logic exists but must be moved into runtime modules |
| `ARCHIVE_MANUAL` | Manual/operator workflow not part of final automated bot |
| `DELETE_CANDIDATE` | Likely redundant after dependency scan |
| `FREEZE_DEFERRED` | Valid but not current priority; do not build further |

---

## 3. Module Classification

### 3.1 `src/backtest/` — KEEP_RESEARCH

| File | Label | Notes |
|------|-------|-------|
| `backtest_runner.py` | `KEEP_RESEARCH` | Core offline backtest engine driver |
| `engine.py` | `KEEP_RESEARCH` | Bar-by-bar execution engine |
| `metrics.py` | `KEEP_RESEARCH` | Performance metric computation |
| `metrics_diagnostics.py` | `KEEP_RESEARCH` | Sharpe/vol diagnostics |
| `trade.py` | `KEEP_RESEARCH` | Trade schema |
| `trade_diagnostics.py` | `KEEP_RESEARCH` | Trade-level diagnostics |

### 3.2 `src/strategy/` — KEEP_RESEARCH

| File | Label | Notes |
|------|-------|-------|
| `trend_following.py` | `KEEP_RESEARCH` | Primary strategy under evaluation |
| `opening_range_breakout.py` | `KEEP_RESEARCH` | Candidate B; retain for comparison |
| `signal_engine.py` | `KEEP_RESEARCH` | Signal abstraction |
| `base.py` | `KEEP_RESEARCH` | Strategy base class |
| `factory.py` | `KEEP_RESEARCH` | Strategy builder |

### 3.3 `src/indicators/`, `src/analysis/`, `src/experiments/`, `src/portfolio/` — KEEP_RESEARCH

All files in these directories: `KEEP_RESEARCH`. They serve offline validation
and OOS design (PR 11A, 11B) and do not require runtime integration now.

### 3.4 `src/data/` — KEEP_RESEARCH

| File | Label | Notes |
|------|-------|-------|
| `base.py` | `KEEP_RESEARCH` | Data provider interface |
| `cached_provider.py` | `KEEP_RESEARCH` | Offline cache reader |
| `yahoo_provider.py` | `KEEP_RESEARCH` | Yahoo fetch; used for cache population only |

### 3.5 `src/risk/` — KEEP_RUNTIME

| File | Label | Notes |
|------|-------|-------|
| `risk_manager.py` | `KEEP_RUNTIME` | Force-exit / stop-loss runtime logic |
| `position_sizer.py` | `KEEP_RUNTIME` | Position sizing runtime logic |

### 3.6 `src/execution/` — KEEP_RUNTIME

| File | Label | Notes |
|------|-------|-------|
| `broker.py` | `KEEP_RUNTIME` | Broker interface |
| `alpaca_broker.py` | `KEEP_RUNTIME` | Alpaca broker adapter |
| `fake_broker.py` | `KEEP_RUNTIME` | Mock broker for testing |
| `order_intent.py` | `KEEP_RUNTIME` | Order intent schema |
| `live_ledger.py` | `KEEP_RUNTIME` | Live trade ledger |
| `live_submit_executor.py` | `KEEP_RUNTIME` | Low-level submit executor |
| `paper_kill_switch.py` | `KEEP_RUNTIME` | Kill switch — critical runtime guard |
| `paper_daily_limits.py` | `KEEP_RUNTIME` | Daily loss/order limits — runtime guard |
| `paper_market_hours_guard.py` | `KEEP_RUNTIME` | Market hours check — runtime guard |
| `paper_open_order_guard.py` | `KEEP_RUNTIME` | Duplicate order guard — runtime guard |
| `paper_ledger.py` | `KEEP_RUNTIME` | Paper trade ledger |
| `paper_order_poller.py` | `KEEP_RUNTIME` | Order fill polling |

### 3.7 `src/reporting/` — mixed

| File | Label | Notes |
|------|-------|-------|
| `reconciliation.py` | `KEEP_RUNTIME` | Position reconciliation |
| `report_generator.py` | `KEEP_RESEARCH` | Backtest report artifacts |

### 3.8 `src/tools/` — cached/research tools: KEEP_RESEARCH

| File | Label | Notes |
|------|-------|-------|
| `cached_data_availability_check.py` | `KEEP_RESEARCH` | Cache pre-check |
| `yahoo_cache_fetch.py` | `KEEP_RESEARCH` | Cache population |
| `cached_real_data_backtest_check.py` | `KEEP_RESEARCH` | 60m offline characterization |

### 3.9 `src/tools/` — live runtime tools: KEEP_RUNTIME or FREEZE_DEFERRED

| File | Label | Notes |
|------|-------|-------|
| `live_readiness_gate.py` | `FREEZE_DEFERRED` | Readiness gate; fold into automated risk gate later |
| `live_safety_status.py` | `FREEZE_DEFERRED` | Safety status summary |
| `live_submit.py` | `FREEZE_DEFERRED` | Core live submit; needed for automated path but not yet wired |
| `live_submit_enablement_gate.py` | `FREEZE_DEFERRED` | Enablement gate |
| `live_submit_executor_check.py` | `FREEZE_DEFERRED` | Executor pre-check |
| `live_trading_approval.py` | `FREEZE_DEFERRED` | Approval gate |
| `live_credential_presence_guard.py` | `FREEZE_DEFERRED` | Credential guard |
| `live_account_check.py` | `FREEZE_DEFERRED` | Account status check |
| `live_broker_preflight_readonly.py` | `FREEZE_DEFERRED` | Broker preflight |
| `live_shadow_preflight.py` | `FREEZE_DEFERRED` | Shadow-mode preflight |
| `live_shadow_screen_symbols.py` | `FREEZE_DEFERRED` | Shadow screening |
| `live_ledger_verify.py` | `FREEZE_DEFERRED` | Ledger integrity |
| `live_pre_submit_ledger_dry_run.py` | `FREEZE_DEFERRED` | Ledger dry run |
| `live_post_submit_ledger_update_dry_run.py` | `FREEZE_DEFERRED` | Post-submit ledger |
| `live_pre_submit_checklist.py` | `FREEZE_DEFERRED` | Pre-submit checks |
| `live_dry_run_intents.py` | `FREEZE_DEFERRED` | Dry-run intent generator |

### 3.10 `src/tools/` — manual-only tools: ARCHIVE_MANUAL or DELETE_CANDIDATE

These tools are designed for a human operator manually running a single trade.
They are not part of an automated runtime pipeline.

| File | Label | Notes |
|------|-------|-------|
| `live_single_manual_submit.py` | `ARCHIVE_MANUAL` | Manual single-trade submit; not automated |
| `live_single_submit_approval_review.py` | `ARCHIVE_MANUAL` | Manual approval review |
| `manual_position_status_checker_readonly.py` | `ARCHIVE_MANUAL` | Manual position check |
| `live_position_reconciliation_readonly.py` | `ARCHIVE_MANUAL` | Manual reconciliation view |
| `live_operator_config_override_review.py` | `ARCHIVE_MANUAL` | Operator config review |
| `live_operator_release_checklist.py` | `ARCHIVE_MANUAL` | Manual release checklist |
| `live_real_submit_pr_approval.py` | `ARCHIVE_MANUAL` | PR-based approval workflow |
| `live_submit_plan_review.py` | `ARCHIVE_MANUAL` | Manual plan review |
| `live_submit_blocked_review.py` | `ARCHIVE_MANUAL` | Manual BLOCKED review |
| `live_dry_run_review.py` | `ARCHIVE_MANUAL` | Manual dry-run review |
| `live_v2_approvals_review.py` | `DELETE_CANDIDATE` | v2 approval review; dependency check needed |
| `live_v2_executor_readiness_review.py` | `DELETE_CANDIDATE` | v2 executor review |
| `live_v2_final_readiness_review.py` | `DELETE_CANDIDATE` | v2 final review |
| `live_v2_readiness_bundle.py` | `DELETE_CANDIDATE` | v2 readiness bundle |
| `live_readiness_history_review.py` | `DELETE_CANDIDATE` | History review |
| `live_shadow_review.py` | `DELETE_CANDIDATE` | Shadow review |
| `live_shadow_screen_review.py` | `DELETE_CANDIDATE` | Shadow screen review |
| `paper_smoke_check.py` | `ARCHIVE_MANUAL` | Manual smoke check |
| `paper_status.py` | `ARCHIVE_MANUAL` | Manual paper status |
| `paper_ledger_import.py` | `ARCHIVE_MANUAL` | Manual ledger import |
| `paper_ledger_verify.py` | `FREEZE_DEFERRED` | May be needed by runtime |
| `paper_pre_submit_check.py` | `CONVERT_TO_RUNTIME` | Logic useful; should become automated gate input |
| `replay_order_reconciliation.py` | `FREEZE_DEFERRED` | May be needed by runtime reconciliation |

### 3.11 `src/main.py` — CONVERT_TO_RUNTIME

`src/main.py` currently contains:

- `_run_paper_close()`: full paper close/flatten flow (~300 lines)
- `main()`: dispatches `mode=paper` / `mode=live` / backtest; contains paper
  execution path inline

Both the paper execution path and the paper close path must be extracted into
dedicated runtime modules before an automated state machine can be built on
top of them.

| Target | Label | Notes |
|--------|-------|-------|
| Paper execution path in `main()` | `CONVERT_TO_RUNTIME` | Extract to `src/execution/paper_runner.py` |
| `_run_paper_close()` in `main.py` | `CONVERT_TO_RUNTIME` | Extract to `src/execution/paper_close_runner.py` |
| CLI dispatch / arg parsing | `KEEP_RUNTIME` | Retain as thin dispatcher |

---

## 4. Deletion / Archive Rules

**Do not delete blindly.** Every `DELETE_CANDIDATE` must first pass a
dependency scan:

```bash
# Check for imports of the target module
grep -r "from src.tools.live_v2_approvals_review\|import live_v2_approvals_review" src/ tests/ scripts/

# Check for test references
grep -r "live_v2_approvals_review" tests/

# Check for doc references
grep -r "live_v2_approvals_review" docs/

# Check for config references
grep -r "live_v2_approvals_review" config/
```

If no references are found, the file is safe to delete. If references exist,
reclassify to `ARCHIVE_MANUAL` until references are cleaned up.

### Archive paths

| Category | Archive path |
|----------|-------------|
| Manual live submit/review scripts | `scripts/archive/manual_live_readiness/` |
| Old snapshot docs | `docs/archive/snapshots/` |

### Rules

- Active runtime code must not import from archived paths
- Archived scripts must not be counted by active tool inventory tests
- Tool inventory tests (e.g., `test_tools_scripts_isolation.py`) must be
  updated to distinguish `ACTIVE_TOOLS` from `ARCHIVED_TOOLS` before archiving

---

## 5. Proposed Next PR Chain

### Phase R — Codebase simplification and automated-runtime alignment

| PR | Scope | Priority |
|----|-------|---------|
| **R1** | This inventory plan | **This PR** |
| **R2** | Update tool inventory tests: separate `ACTIVE_TOOLS` vs `ARCHIVED_TOOLS` | High |
| **R3** | Archive old snapshot docs into `docs/archive/snapshots/` | High |
| **R4** | Archive / delete `ARCHIVE_MANUAL` and `DELETE_CANDIDATE` tools after dependency scan | High |
| **R5** | Extract paper execution path from `src/main.py` → `src/execution/paper_runner.py` | High |
| **R6** | Extract paper close path from `src/main.py` → `src/execution/paper_close_runner.py` | High |

### Phase A2 — Automated runtime skeleton

| PR | Scope | Priority |
|----|-------|---------|
| **A2-1** | Automated runtime state machine skeleton (`src/execution/state_machine.py`) | High |
| **A2-2** | Automated risk gate skeleton (`src/execution/risk_gate.py`) | High |
| **A2-3** | Order lifecycle manager skeleton (`src/execution/order_lifecycle.py`) | Medium |

### Deferred

| Item | Status |
|------|--------|
| Phase 2 / Policy A daily-bar engine fix | `FREEZE_DEFERRED` — separate track |
| PR 11A: 60m metrics threshold design | `FREEZE_DEFERRED` — after R-chain |
| PR 11B: 60m OOS / walk-forward design | `FREEZE_DEFERRED` — after R-chain |
| Expanding live-readiness checklist | **Do not proceed** |

---

## 6. Direction Guard

Before opening any future PR, answer all four questions:

| Question | Required answer |
|----------|----------------|
| Does this PR move us toward automated runtime? | Yes |
| Does this PR reduce manual-only complexity? | Yes or neutral |
| Does this PR improve 60m strategy validation? | Yes or neutral |
| Is this another manual safety/runbook loop? | **No** |

If the answer to the last question is **Yes**, do not proceed without explicit
approval. The 10-series runbook chain is closed. Adding more operator checklists,
snapshot docs, or manual submit runbooks does not advance the automated bot.

---

## 7. Validation

```bash
git diff origin/main...HEAD -- src tests config output scripts data
# Expected: empty
```

No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` files are
changed in this PR. `pytest` not run for docs-only PRs.

---

## 8. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | No code changes |
| No order submission | No code changes |

> **This document does not approve automated live trading.**
> **This document does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> **Nothing in this repository is financial advice.**
