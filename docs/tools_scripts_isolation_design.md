# Tools / Scripts Isolation Design

Design document for PR 9 of the trend-bot architecture refactor:
audit `src/tools/` and plan a safe, staged isolation of any non-core
one-off scripts into a future `scripts/` directory.

**No code is implemented in this document.**
**No files are moved in this document.**
**No files are deleted in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live record is written.**
**No paper trading is implemented.**
**No live trading is implemented.**
**No automated trading is approved.**
**This document plans the refactor only — each sub-PR requires its own review.**

---

## 1. Current Problem

`src/tools/` contains 40 Python files spanning four distinct concerns:

| Concern | Count | Examples |
|---------|-------|---------|
| Live safety / readiness gate | 28 | `live_readiness_gate.py`, `live_submit_enablement_gate.py`, `live_pre_submit_checklist.py` |
| Manual live/paper guard | 5 | `live_single_manual_submit.py`, `live_single_submit_approval_review.py`, `manual_position_status_checker_readonly.py` |
| Paper reporting / diagnostic | 6 | `paper_status.py`, `paper_smoke_check.py`, `paper_pre_submit_check.py`, `paper_ledger_verify.py`, `paper_ledger_import.py`, `replay_order_reconciliation.py` |
| Live reporting / diagnostic | 1 | `live_position_reconciliation_readonly.py` |

All 40 tools are covered by test files in `tests/`.
**No tool currently has zero test coverage.**

Structural problem: the `src/tools/` namespace is large and flat, mixing
safety-critical live gates with paper diagnostics and one-off utilities.
This makes auditing harder and will become worse as more tools are added.

---

## 2. Inventory

### 2.1 Live Safety / Readiness Gate Tools

These tools implement the pre-submit safety pipeline and the live-readiness
gate. They must **stay in `src/tools/`** permanently. Moving them would
break the `python -m src.tools.live_*` CLI surface used in operator runbooks
and documented in `docs/live_readiness_status.md`.

| Tool | Purpose | Move? |
|------|---------|-------|
| `live_account_check.py` | Read-only account health check | **No** |
| `live_broker_preflight_readonly.py` | Read-only broker preflight | **No** |
| `live_credential_presence_guard.py` | Env var presence check | **No** |
| `live_dry_run_intents.py` | Dry-run intent audit | **No** |
| `live_dry_run_review.py` | Dry-run artifact review | **No** |
| `live_ledger_verify.py` | Live ledger schema validator | **No** |
| `live_operator_config_override_review.py` | Operator config override review | **No** |
| `live_operator_release_checklist.py` | Operator release checklist | **No** |
| `live_order_submission_approval.py` | Order submission approval artifact | **No** |
| `live_post_submit_ledger_update_dry_run.py` | Post-submit ledger dry-run | **No** |
| `live_pre_submit_checklist.py` | Unified pre-submit checklist | **No** |
| `live_pre_submit_ledger_dry_run.py` | Pre-submit ledger dry-run | **No** |
| `live_readiness_gate.py` | GO/NO-GO readiness gate | **No** |
| `live_readiness_history_review.py` | Readiness history trend review | **No** |
| `live_real_submit_pr_approval.py` | PR approval artifact | **No** |
| `live_safety_status.py` | Read-only safety config status | **No** |
| `live_shadow_preflight.py` | Shadow preflight (read-only) | **No** |
| `live_shadow_review.py` | Shadow preflight artifact review | **No** |
| `live_shadow_screen_review.py` | Symbol screen artifact review | **No** |
| `live_shadow_screen_symbols.py` | Multi-symbol live sizing screen | **No** |
| `live_submit.py` | Dry-run submit plan writer | **No** |
| `live_submit_blocked_review.py` | Blocked report reviewer | **No** |
| `live_submit_enablement_gate.py` | GO/NO-GO enablement gate | **No** |
| `live_submit_executor_check.py` | Executor gate check | **No** |
| `live_submit_plan_review.py` | Submit plan artifact reviewer | **No** |
| `live_trading_approval.py` | Live trading approval artifact | **No** |
| `live_v2_approvals_review.py` | V2 dual-approval reviewer | **No** |
| `live_v2_executor_readiness_review.py` | V2 executor readiness reviewer | **No** |
| `live_v2_final_readiness_review.py` | V2 combined readiness review | **No** |
| `live_v2_readiness_bundle.py` | V2 readiness bundle runner | **No** |

### 2.2 Manual Live / Paper Guard Tools

These tools implement manual operator gates for live and paper submission.
They must **stay in `src/tools/`** — they are part of the live-readiness
safety pipeline and are referenced in operator runbooks.

| Tool | Purpose | Move? |
|------|---------|-------|
| `live_position_reconciliation_readonly.py` | Read-only live position reconciliation | **No** |
| `live_single_manual_submit.py` | One-time manual live submit gate | **No** |
| `live_single_submit_approval_review.py` | Single-submit approval review | **No** |
| `manual_position_status_checker_readonly.py` | Manual position status checker | **No** |

### 2.3 Paper Reporting / Diagnostic Utilities

These tools support paper trading operations and diagnostics.
They **may be candidates** for `scripts/` in a future PR — but only after
tests confirm the move is safe (import paths updated, CLI surface preserved).

| Tool | Purpose | Move candidate? |
|------|---------|----------------|
| `paper_ledger_import.py` | Offline paper ledger backfill | Maybe (PR 9D) |
| `paper_ledger_verify.py` | Paper ledger schema validator | Maybe (PR 9D) |
| `paper_pre_submit_check.py` | Paper pre-submit checklist | Maybe (PR 9D) |
| `paper_smoke_check.py` | Paper workflow smoke test | Maybe (PR 9D) |
| `paper_status.py` | Read-only paper status / doctor | Maybe (PR 9D) |
| `replay_order_reconciliation.py` | Offline reconciliation replay | Maybe (PR 9D) |

**Important:** All six have test files and the test files import from
`src.tools.*`. Moving any of these requires updating all import paths in
the corresponding test files — which constitutes a code change requiring
its own PR and test run.

---

## 3. Rules

The following rules apply to every sub-PR in this isolation plan.

| Rule | Rationale |
|------|-----------|
| No file moves without passing tests for the new import paths | A moved module is a broken import until tests verify it |
| No deletion without full coverage of moved functionality | Tests must prove the replacement works before old code goes away |
| Safety-critical tools stay in `src/tools/` | Live-readiness pipeline CLIs use `python -m src.tools.*`; moving them breaks operator runbooks |
| No move of any tool that has a test importing `src.tools.*` without updating that test | Import paths in tests are a contract |
| Live/paper tools remain fail-closed at all times | No refactor step may relax a safety gate |
| No broker/API/credentials access in any refactor PR | All tool moves are source-only operations |
| No automated trading approved | This refactor is structural only |

---

## 4. Sub-PR Implementation Plan

### PR 9A — Design (this document)

**Status: designed — `docs/tools_scripts_isolation_design.md`**

Docs-only. No `src/`, `tests/`, `config/`, or `output/` changes.

### PR 9B — Inventory tests for `src/tools/`

**Status: implemented — `tests/test_tools_inventory.py` (363 tests)**

**Goal:** Add import-presence and source-scan tests for every tool in
`src/tools/`, confirming:

- Each tool module is importable from `src.tools.*`.
- No tool imports `src.main.build_engine` at module level (already removed).
- Each live and manual-guard tool has a `main()` callable.
- No tool-level imports trigger Alpaca SDK loads or network access.

**Scope:** Added `tests/test_tools_inventory.py`. No moves, no deletions.

**Test classes:**
- `TestToolsInventory` — count assertions (30/4/6/40), file existence (parametrised),
  mutual exclusivity of categories, no unclassified tools, no phantom tools.
- `TestToolsTestCoverage` — every tool has a `tests/test_{name}.py`.
- `TestLiveToolsHaveMain` — every live safety + manual-guard tool defines `main()`.
- `TestToolsSourceScan` — no module-level Alpaca import; no module-level `os.environ`
  reads; no module-level order-mutation calls; no hardcoded secret literals;
  `live_submit_enablement_gate` does not set `LIVE_SUBMIT_ENABLED = True` at top level.
- `TestToolsImportSafety` — all 40 tools importable; no module-level
  `from src.main import build_engine`.

**Full suite after PR 9B:** 5 117 passed.

### PR 9C — `scripts/` directory and classification README

**Status: implemented — `scripts/README.md`**

**Goal:** Create `scripts/` directory with a `README.md` classifying tools
as permanent (`src/tools/`) vs. future-move candidates.

**Scope:** Added `scripts/README.md`. No tool moves. No test changes.

**Contents:** Explains the purpose of `scripts/` vs `src/tools/`; full 30/4/6
classification tables; rules for adding files; safety guarantee table.
Notes that `tests/test_tools_inventory.py` (PR 9B) locks the counts.

### PR 9D — Move paper diagnostic utilities to `scripts/` (conditional)

**Goal:** If PR 9B confirms all six paper diagnostic utilities have zero
cross-imports (no other `src/` module imports them), move them to
`scripts/` with compatibility shims in `src/tools/` that import-forward.

**Preconditions:**
- PR 9B inventory tests pass.
- Source scan confirms zero `from src.tools.paper_*` imports outside `tests/`.
- Each moved tool retains its `python -m` CLI surface via a shim or the move target.
- All test imports updated in the same PR.
- Full suite passes before and after the move.

**Scope:** `scripts/paper_*.py`, `scripts/replay_order_reconciliation.py`,
updated `src/tools/` shims (or removed if tests are also updated),
updated `tests/test_paper_*.py` imports. Conditional on preconditions.

### PR 9E — Confirm live-readiness tools stay in `src/tools/`

**Status: implemented — `tests/test_tools_inventory.py::TestPermanentToolsLocation` (76 tests)**

**Goal:** Explicitly document and test that all 34 permanent tools (30 live
safety/readiness + 4 manual guard) remain in `src/tools/` and are absent
from `scripts/`.

**Scope:** Added `TestPermanentToolsLocation` class to `tests/test_tools_inventory.py`.
No moves. No source file changes.

**Tests added (76):**
- `test_permanent_tools_count` — asserts `_PERMANENT_TOOLS` length is 34.
- `test_live_safety_tools_count_unchanged` — asserts LIVE_SAFETY_TOOLS count is 30.
- `test_manual_guard_tools_count_unchanged` — asserts MANUAL_GUARD_TOOLS count is 4.
- `test_permanent_tool_in_src_tools[*]` — parametrised over 34: each tool file exists in `src/tools/`.
- `test_permanent_tool_not_in_scripts[*]` — parametrised over 34: no tool file exists in `scripts/`.
- `test_no_live_tool_file_in_scripts` — no `live_*.py` in `scripts/`.
- `test_no_manual_tool_file_in_scripts` — no `manual_*.py` in `scripts/`.
- `test_scripts_readme_documents_permanent_tools` — `scripts/README.md` uses "permanent" and references `src/tools/`.
- `test_scripts_readme_lists_live_safety_count` — `scripts/README.md` mentions count 30.
- `test_scripts_readme_lists_manual_guard_count` — `scripts/README.md` mentions count 4.

**Full suite after PR 9E:** 5 193 passed.

### PR 9F — Update docs after actual moves

**Goal:** Update `README.md`, `docs/live_readiness_status.md`, and operator
runbooks to reflect any moves made in PRs 9C–9E.

**Scope:** Docs only. No code changes.

---

## 5. What This Design Does Not Approve

- **No live tools moved.** `live_*.py` tools stay in `src/tools/` permanently.
- **No deletions.** No tool is deleted in any sub-PR without full replacement coverage.
- **No safety-gate relaxation.** All fail-closed guards are preserved exactly.
- **No broker calls.** No endpoint is contacted in any refactor step.
- **No credentials.** No API key, secret, or token is read by this refactor.
- **No automated paper trading.** Paper tools remain gated behind explicit human-set config fields.
- **No order submission.** No `submit_order` call is added or removed.
- **No behavior change.** This is a structural isolation only.

---

## 6. Validation

For this **docs-only** PR:

```bash
git diff origin/main...HEAD -- src tests output config scripts
# Expected: empty (no src/tests/output/config/scripts changes)
```

For each implementation sub-PR:

```bash
python -m pytest          # must not reduce passing test count
python -m src.tools.live_readiness_gate --help   # must still work after any move
python -m src.tools.paper_status --help          # must still work after any move
```

---

## 7. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No tool is changed in this docs PR |
| No Alpaca SDK imported | Zero `src/` changes; no new imports possible |
| No credentials read | Zero `src/` changes |
| No order submission | Zero `src/` changes |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |
| Test suite unchanged | Zero `tests/` changes |

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
