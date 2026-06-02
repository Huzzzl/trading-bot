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
| **R1** | This inventory plan | **Implemented** |
| **R2** | Update tool inventory tests: separate `ACTIVE_TOOLS` vs `ARCHIVE_MANUAL` / `DELETE_CANDIDATE` (5-group model, 384 tests) | **Implemented** |
| **R3** | Archive old snapshot docs into `docs/archive/snapshots/` | **Implemented** |
| **R4** | Archive / delete `ARCHIVE_MANUAL` and `DELETE_CANDIDATE` tools after dependency scan | High |
| **R5** | Extract paper execution path from `src/main.py` → `src/execution/paper_runner.py` | High |
| **R6** | Extract paper close path from `src/main.py` → `src/execution/paper_close_runner.py` | High |

**PR R3 implementation summary:**
Created `docs/archive/snapshots/`. Moved 5 superseded snapshot docs:
- `docs/archive/snapshots/first_cached_real_data_backtest_results_snapshot.md` (PR 10J)
- `docs/archive/snapshots/calibrated_sharpe_diagnostics_real_data_snapshot.md` (PR 10O)
- `docs/archive/snapshots/trade_diagnostics_real_data_snapshot.md` (PR 10T)
- `docs/archive/snapshots/post_phase1_daily_guard_cached_checker_snapshot.md` (PR 10X)
- `docs/archive/snapshots/automated_trading_architecture_readiness_snapshot.md` (PR 9F area)
Archive notes added to each file. Path references updated in 7 active docs.
No tests modified. Full suite: 5 701 passed (unchanged from R2).

**Test cleanup audit (PR R3):**
- `tests/test_daily_bar_session_end_behavior.py` — `KEEP_ACTIVE_TEST`: references "PR 10T snapshot" in a comment only; tests live engine behavior, not the doc file.
- `tests/test_live_single_manual_submit.py` — `KEEP_ACTIVE_TEST`: uses "snapshot" as a local variable name for a mock; no file path reference.
- No `DELETE_TEST_CANDIDATE` tests identified.

**PR R2 implementation summary:**
`tests/test_tools_inventory.py` rewritten from 363-test 4-group model to
384-test 5-group cleanup-aware model.
- `ACTIVE_RESEARCH_TOOLS` (3): offline cache / characterization
- `ACTIVE_RUNTIME_CANDIDATE_TOOLS` (15): FREEZE_DEFERRED; may feed automated runtime
- `ARCHIVE_MANUAL_TOOLS` (14): manual-operator workflow; eligible for archive in PR R4
- `DELETE_CANDIDATE_TOOLS` (10): likely redundant; eligible for deletion in PR R4 after dep scan
- `PRESERVE_RUNTIME_SUPPORT_TOOLS` (1): `paper_ledger_verify`; keep pending runtime review
- `ALL_TOOLS` (43) = no change — all still in `src/tools/`, no moves in R2
- `ACTIVE_TOOLS` (19) = research (3) + runtime candidates (15) + preserve (1)
- `main()` requirement now scoped to `ACTIVE_TOOLS` only (not ARCHIVE/DELETE)
- Safety scans (Alpaca/env/mutation/secrets) still cover `ALL_TOOLS`
- `TestPermanentToolsLocation` removed; replaced by `TestCleanupEligibility`

### Phase A2 — Automated runtime skeleton

| PR | Scope | Priority | Status |
|----|-------|---------|--------|
| **A2-1** | Automated runtime state machine skeleton (`src/runtime/state_machine.py`) | High | **Implemented** |
| **A2-2** | Automated risk gate skeleton (`src/runtime/risk_gate.py`) | High | **Implemented** |
| **A2-3** | Order lifecycle manager skeleton (`src/runtime/order_lifecycle.py`) | Medium | Pending |

**A2-1 — Automated runtime state machine skeleton — implemented**
`src/runtime/state_machine.py` created. Injectable `risk_gate`, `paper_buy_runner`,
`paper_close_runner`. Default fail-closed: no `risk_gate` → BLOCKED.
No broker/credential/Alpaca access inside state machine.
57 new tests in `tests/test_runtime_state_machine.py`. Full suite: 4 224 passed.

**A2-2 — Automated risk gate skeleton — implemented**
`src/runtime/risk_gate.py` created. `AutomatedRiskGate(enabled=False, rules=None)`.
Default fail-closed: `enabled=False` → BLOCKED. Local rules: `max_order_quantity`,
`allowed_symbols`, `allowed_sides`. Callable — injectable into state machine.
`live_trading_allowed` always False. All safety flags always False (offline-only).
62 new tests in `tests/test_runtime_risk_gate.py`. Full suite: 4 286 passed.

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

---

## 9. PR R4 Implementation Record

PR R4 executed the first real codebase cleanup against the plan defined in
this document. The dependency scan over all 43 tools produced the final
classification below.

### Reclassifications after dependency scan

| Tool | Original class | Final class | Reason |
|------|---------------|-------------|--------|
| `live_dry_run_review` | `ARCHIVE_MANUAL` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `live_pre_submit_checklist` |
| `live_pre_submit_checklist` | `ARCHIVE_MANUAL` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `live_submit` |
| `paper_smoke_check` | `ARCHIVE_MANUAL` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `test_paper_ledger.py` (immutable) |
| `paper_status` | `ARCHIVE_MANUAL` | `ACTIVE_RUNTIME_CANDIDATE` | imported by 5 active FREEZE_DEFERRED tools |
| `live_shadow_review` | `DELETE_CANDIDATE` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `live_readiness_gate` |
| `live_shadow_screen_review` | `DELETE_CANDIDATE` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `live_readiness_gate` |
| `live_v2_approvals_review` | `DELETE_CANDIDATE` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `live_submit_enablement_gate` |
| `live_v2_executor_readiness_review` | `DELETE_CANDIDATE` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `live_submit_enablement_gate` |
| `live_v2_final_readiness_review` | `DELETE_CANDIDATE` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `live_v2_readiness_bundle` |
| `live_v2_readiness_bundle` | `DELETE_CANDIDATE` | `ACTIVE_RUNTIME_CANDIDATE` | v2 chain; imported transitively |
| `replay_order_reconciliation` | `DELETE_CANDIDATE` | `ACTIVE_RUNTIME_CANDIDATE` | imported by `paper_status` and `test_paper_ledger.py` |

### Archived tools (10) — moved to `scripts/archive/manual_live_readiness/`

Archive header prepended; not importable as `src.tools.<name>`.

| Tool |
|------|
| `live_operator_config_override_review` |
| `live_operator_release_checklist` |
| `live_order_submission_approval` |
| `live_position_reconciliation_readonly` |
| `live_real_submit_pr_approval` |
| `live_single_manual_submit` |
| `live_single_submit_approval_review` |
| `live_submit_blocked_review` |
| `live_submit_plan_review` |
| `manual_position_status_checker_readonly` |

### Deleted tools (3) — removed from repo

| Tool | Reason |
|------|--------|
| `live_readiness_history_review` | No active import references; no archive value |
| `paper_ledger_import` | No active import references; no archive value |
| `paper_pre_submit_check` | No active import references; no archive value |

### Test cleanup audit

| Test file | Action | Reason |
|-----------|--------|--------|
| `test_live_operator_config_override_review.py` | Deleted | Tool archived |
| `test_live_operator_release_checklist.py` | Deleted | Tool archived |
| `test_live_order_submission_approval.py` | Deleted | Tool archived |
| `test_live_position_reconciliation_readonly.py` | Deleted | Tool archived |
| `test_live_real_submit_pr_approval.py` | Deleted | Tool archived |
| `test_live_single_manual_submit.py` | Deleted | Tool archived |
| `test_live_single_submit_approval_review.py` | Deleted | Tool archived |
| `test_live_submit_blocked_review.py` | Deleted | Tool archived |
| `test_live_submit_plan_review.py` | Deleted | Tool archived |
| `test_manual_position_status_checker_readonly.py` | Deleted | Tool archived |
| `test_live_readiness_history_review.py` | Deleted | Tool deleted |
| `test_paper_ledger_import.py` | Deleted | Tool deleted |
| `test_paper_pre_submit_check.py` | Deleted | Tool deleted |

1 113 tests removed across 13 deleted test files.

`tests/conftest.py`: removed `"test_paper_ledger_import.py"` from `_LEDGER_TEST_FILES`.

`tests/test_live_submit_executor_check.py`: removed `test_report_consumable_by_blocked_review`
(the only test that imported now-archived `live_submit_blocked_review`).

### Final post-R4 counts

| Classification | Count |
|----------------|-------|
| `ACTIVE_TOOLS` (in `src/tools/`) | 30 |
| `ARCHIVED_TOOLS` (in `scripts/archive/manual_live_readiness/`) | 10 |
| `DELETED_TOOLS_R4` (removed from repo) | 3 |
| Full suite | 4 513 passed |

### Safety invariants confirmed

- No `src/backtest/`, `src/strategy/`, `src/risk/`, `src/execution/` runtime files changed
- No `src/main.py` changed
- No `config/`, `output/`, `data/cache/` changes
- No broker calls, no credentials read, no orders, no trading behavior change
- All archived files preserved in `scripts/archive/manual_live_readiness/` (not deleted)

> **This document does not approve automated live trading.**
> **This document does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> **Nothing in this repository is financial advice.**

---

## 10. PR R4b — Legacy Dependency Reduction Plan (docs-only)

Adds `docs/legacy_active_tool_dependency_reduction_plan.md`.

### Problem documented

PR R4 forced 11 tools into `ACTIVE_RUNTIME_CANDIDATE` due to old import chains,
raising `ACTIVE_TOOLS` from 19 → 30. Those 11 tools are not active because they
serve automated runtime — they are active because legacy tool code imports them.

### Plan documented

Five dependency clusters (A–E) and reduction PRs R4c–R4g:

| Cluster | Tools in chain | Reduction PR |
|---------|---------------|--------------|
| A — live submit checklist | `live_pre_submit_checklist`, `live_dry_run_review` | R4e |
| B — readiness shadow | `live_shadow_review`, `live_shadow_screen_review` | R4f |
| C — v2 approval bundle | `live_v2_approvals_review`, `live_v2_executor_readiness_review`, `live_v2_final_readiness_review`, `live_v2_readiness_bundle` | R4g |
| D — paper status / replay | `replay_order_reconciliation` | R4d |
| E — paper smoke check | `paper_smoke_check` | R4c |

### No code changes in PR R4b

No `src/`, `tests/`, `scripts/`, `config/`, `output/`, or `data/` changes.
Full suite: 4 513 passed (unchanged from R4)

---

## 11. PR R4c Implementation Record

PR R4c broke the `test_paper_ledger.py` → `paper_smoke_check` import chain
and archived the tool.

### Change

| File | Action |
|------|--------|
| `tests/test_paper_ledger.py` | `TestSmokeCheckDoesNotAppendLedger` rewritten — uses `AlpacaBrokerAdapter` fake client + `OrderIntent` CSV write directly |
| `src/tools/paper_smoke_check.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `tests/test_paper_smoke_check.py` | Deleted |
| `tests/test_tools_inventory.py` | `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 26→25; `ACTIVE_TOOLS` 30→29; `ARCHIVED_TOOLS` 10→11 |

### Final post-R4c counts

| Classification | Count |
|----------------|-------|
| `ACTIVE_TOOLS` (in `src/tools/`) | 29 |
| `ARCHIVED_TOOLS` (in `scripts/archive/manual_live_readiness/`) | 11 |
| `DELETED_TOOLS_R4` | 3 |

### Safety invariants confirmed

- Behavioral assertion preserved: paper preview path (fake broker preflight + CSV write) must not call `append_ledger_row`
- No runtime trading behavior changed
- No broker calls, no credentials read, no orders.

---

## 12. PR R4d Implementation Record

PR R4d extracted reusable reconciliation logic from `src/tools/replay_order_reconciliation.py`
into `src/reporting/replay_reconciliation.py` and archived the CLI tool.

### Change

| File | Action |
|------|--------|
| `src/reporting/replay_reconciliation.py` | Created — library module with `_normalize_side`, `_normalize_status`, `replay()` |
| `src/tools/replay_order_reconciliation.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `src/tools/paper_status.py` | `check_replay` lazy import updated to `src.reporting.replay_reconciliation` |
| `tests/test_paper_ledger.py` | Section 7 import updated to `src.reporting.replay_reconciliation` |
| `tests/test_paper_status.py` | Patch target updated to `src.reporting.replay_reconciliation.replay` |
| `tests/test_replay_order_reconciliation.py` | Imports updated; `TestCLIMain` (5 tests) removed |
| `tests/test_tools_inventory.py` | `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 25→24; `ACTIVE_TOOLS` 29→28; `ARCHIVED_TOOLS` 11→12 |

### Final post-R4d counts

| Classification | Count |
|----------------|-------|
| `ACTIVE_TOOLS` (in `src/tools/`) | 28 |
| `ARCHIVED_TOOLS` (in `scripts/archive/manual_live_readiness/`) | 12 |
| `DELETED_TOOLS_R4` | 3 |

### Safety invariants confirmed

- Reconciliation correctness tests remain in `test_replay_order_reconciliation.py`
- No runtime trading behavior changed
- No broker calls, no credentials read, no orders.

---

## 13. PR R4e Implementation Record

PR R4e removed `live_submit`'s manual checklist chain dependency and archived both tools.

### Change

| File | Action |
|------|--------|
| `src/tools/live_submit.py` | `_run_checklist` replaced by `_check_automated_risk_gate()`; `_AUTOMATED_RISK_GATE_IMPLEMENTED = False` constant added; main() is fail-closed |
| `src/tools/live_pre_submit_checklist.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `src/tools/live_dry_run_review.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `tests/test_live_pre_submit_checklist.py` | Deleted (manual CLI checklist tests only) |
| `tests/test_live_dry_run_review.py` | Deleted (manual CLI review tests only) |
| `tests/test_live_submit.py` | `_run_checklist` mock removed; happy-path tests removed; `TestCheckAutomatedRiskGate` added |
| `tests/test_tools_inventory.py` | `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 24→22; `ACTIVE_TOOLS` 28→26; `ARCHIVED_TOOLS` 12→14 |
| 5 docs | Updated with R4e record |

### Final post-R4e counts

| Classification | Count |
|----------------|-------|
| `ACTIVE_TOOLS` (in `src/tools/`) | 26 |
| `ARCHIVED_TOOLS` (in `scripts/archive/manual_live_readiness/`) | 14 |
| `DELETED_TOOLS_R4` | 3 |

### Safety invariants confirmed

- `live_submit` remains fail-closed: `_AUTOMATED_RISK_GATE_IMPLEMENTED = False` blocks all live submit paths
- No runtime trading behavior changed
- No broker calls, no credentials read, no orders.

---

## 14. PR R4f Implementation Record

PR R4f removed `live_readiness_gate`'s manual shadow review chain dependencies and archived both tools.

### Change

| File | Action |
|------|--------|
| `src/tools/live_readiness_gate.py` | Module-level imports of `live_shadow_review` and `live_shadow_screen_review` removed; `_AUTOMATED_RUNTIME_STATE_GATE_IMPLEMENTED = False` added; `_stage_preflight_review` and `_stage_symbol_screen_review` replaced with fail-closed stubs |
| `src/tools/live_shadow_review.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `src/tools/live_shadow_screen_review.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `tests/test_live_shadow_review.py` | Deleted |
| `tests/test_live_shadow_screen_review.py` | Deleted |
| `tests/test_live_readiness_gate.py` | GO-path tests replaced with NO-GO tests; `TestAutomatedRuntimeStateGate` class added |
| `tests/test_tools_inventory.py` | `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 22→20; `ACTIVE_TOOLS` 26→24; `ARCHIVED_TOOLS` 14→16 |
| 5 docs | Updated with R4f record |

---

## PR R4g — Decouple live_submit_enablement_gate from v2 approval bundle

### Files changed

| File | Action |
|------|--------|
| `src/tools/live_submit_enablement_gate.py` | Removed imports from `live_v2_approvals_review` and `live_v2_executor_readiness_review`; inlined `_read_json`; added `_AUTOMATED_SUBMIT_ENABLEMENT_GATE_IMPLEMENTED = False`; replaced `validate_approvals` and `validate_readiness` calls with fail-closed stubs |
| `src/tools/live_v2_approvals_review.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `src/tools/live_v2_executor_readiness_review.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `src/tools/live_v2_final_readiness_review.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `src/tools/live_v2_readiness_bundle.py` | Archived to `scripts/archive/manual_live_readiness/` |
| `tests/test_live_v2_approvals_review.py` | Deleted |
| `tests/test_live_v2_executor_readiness_review.py` | Deleted |
| `tests/test_live_v2_final_readiness_review.py` | Deleted |
| `tests/test_live_v2_readiness_bundle.py` | Deleted |
| `tests/test_live_submit_enablement_gate.py` | `TestGoHappyPath` (10) and `TestDecisionHardening` (11) removed; 3 executor violation-string tests removed; `TestAutomatedSubmitEnablementGate` class (7 tests) added |
| `tests/test_tools_inventory.py` | `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 20→16; `ACTIVE_TOOLS` 24→20; `ARCHIVED_TOOLS` 16→20 |
| 5 docs | Updated with R4g record |

### Final post-R4g counts

| Classification | Count |
|----------------|-------|
| `ACTIVE_TOOLS` (in `src/tools/`) | 20 |
| `ARCHIVED_TOOLS` (in `scripts/archive/manual_live_readiness/`) | 20 |
| `DELETED_TOOLS_R4` | 3 |

### Safety invariants confirmed

- `live_readiness_gate` is fail-closed: stages 3+5 always FAIL until `_AUTOMATED_RUNTIME_STATE_GATE_IMPLEMENTED = True`
- `live_submit_enablement_gate` is fail-closed: `run_gate()` always returns NO_GO until `_AUTOMATED_SUBMIT_ENABLEMENT_GATE_IMPLEMENTED = True`
- No runtime trading behavior changed
- No broker calls, no credentials read, no orders.
- Full suite: 4 021 passed.

---

## PR R5 — Extract paper buy/submit execution path to src/execution/paper_runner.py

### Files changed

| File | Action |
|------|--------|
| `src/execution/paper_runner.py` | Created — two-phase paper buy/submit runner with `PaperRunResult` dataclass and `run_paper_execution()` function; injectable `_broker` and `_data_provider` for offline testing; all guards remain as lazy imports |
| `src/main.py` | Paper buy/submit block (~320 lines) replaced by 3-line thin dispatch (`from src.execution.paper_runner import run_paper_execution; run_paper_execution(cfg, output_dir=output_dir); return`); `_run_paper_close` and gates unchanged |
| `tests/test_paper_runner.py` | Created — 12 test classes; injected-broker tests are fully offline; covers PaperRunResult dataclass, preview mode, submit mode, safety flags, safety constraints, position safety, quantity override, guard delegation, output artifacts, main() delegation, default-broker-path flag semantics |
| `tests/test_main_characterization.py` | Added `test_run_paper_execution_is_not_a_top_level_name` to `TestMainImport`; added `test_paper_preview_delegates_to_run_paper_execution` to `TestMainPaperGate` |
| 2 docs | Updated with R5 record |

### Safety invariants confirmed

- `src/execution/paper_runner.py` classified `KEEP_RUNTIME` (was `CONVERT_TO_RUNTIME` in Section 3.11)
- `_run_paper_close` stays in `src/main.py` (R6 scope)
- No runtime trading behavior changed
- No live trading behavior changed. Injected-broker tests remain offline. Default paper AlpacaBrokerAdapter path may read paper credentials and make broker/account/position preflight calls, matching existing behavior. Preview mode submits no orders; submit mode may request exactly one paper order after all guards pass.
- All guards (kill switch, market hours, daily limits, open orders, ledger) behavior preserved exactly — same lazy import pattern, same check order, same error messages.
- Full suite: 4 103 passed.

---

## PR R6 — Extract paper close/flatten runner to src/execution/paper_close_runner.py

### Files changed

| File | Action |
|------|--------|
| `src/execution/paper_close_runner.py` | Created — two-phase paper close/flatten runner with `PaperCloseRunResult` dataclass and `run_paper_close()` function; injectable `_broker` for offline testing; all guards remain as lazy imports |
| `src/main.py` | `_run_paper_close` function (~325 lines) removed; close path replaced by 3-line thin dispatch (`from src.execution.paper_close_runner import run_paper_close; run_paper_close(cfg, output_dir=output_dir); return`) |
| `tests/test_paper_close_runner.py` | Created — 10 test classes; injected-broker tests are fully offline; covers PaperCloseRunResult dataclass, preview mode, submit mode, safety flags, safety constraints, quantity override, guard delegation, output artifacts, default-broker-path flag semantics |
| `tests/test_paper_runner.py` | Updated `test_paper_close_path_does_not_call_run_paper_execution` to patch `src.execution.paper_close_runner.run_paper_close` instead of `src.main._run_paper_close` |
| `tests/test_main_characterization.py` | 2 comment updates; added `test_paper_close_delegates_to_run_paper_close` to `TestMainPaperGate`; added `test_run_paper_close_not_defined_in_main` to `TestSourceCharacterization` |
| 2 docs | Updated with R6 record |

### Safety invariants confirmed

- `src/execution/paper_close_runner.py` classified `KEEP_RUNTIME` (was `CONVERT_TO_RUNTIME` in Section 3.11)
- `_run_paper_close` removed from `src/main.py` — extracted to `paper_close_runner.py`
- No runtime trading behavior changed
- No live trading behavior changed. Injected-broker tests remain offline. Default paper AlpacaBrokerAdapter path may read paper credentials and make broker/account/position preflight calls, matching existing behavior. Preview mode submits no orders; submit mode may request exactly one paper order after all guards pass.
- All guards (kill switch, market hours, daily limits, open orders, ledger) behavior preserved exactly — same lazy import pattern, same check order, same error messages.
- Full suite: 4 167 passed.
