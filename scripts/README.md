# scripts/

This directory is reserved for **non-core, one-off, or manual utility scripts**
that do not belong to the live-readiness safety pipeline.

No files have been moved here yet.
All 40 tool modules currently live in `src/tools/`.

---

## Purpose

`scripts/` is distinct from `src/tools/`:

| Directory | Contains | CLI surface |
|-----------|----------|-------------|
| `src/tools/` | Safety-critical live/paper pipeline tools (40 today) | `python -m src.tools.<name>` |
| `scripts/` | Non-core one-off utilities, future move candidates | `python scripts/<name>.py` |

Scripts placed here are **not part of the live-readiness gate** and are
**not imported by `src/` modules**.  Moving a tool here requires its own PR
with passing tests for the new import paths.

---

## Current Tool Classification

As of PR 9B, `tests/test_tools_inventory.py` locks the following counts.
**No tool may be reclassified without updating those tests.**

### Permanent in `src/tools/` — do not move

#### Live Safety / Readiness Gate (30 tools)

These implement the pre-submit safety pipeline and the live-readiness gate.
Moving them would break the `python -m src.tools.live_*` CLI surface used
in operator runbooks and documented in `docs/live_readiness_status.md`.

| Tool | Purpose |
|------|---------|
| `live_account_check` | Read-only account health check |
| `live_broker_preflight_readonly` | Read-only broker preflight |
| `live_credential_presence_guard` | Env var presence check |
| `live_dry_run_intents` | Dry-run intent audit |
| `live_dry_run_review` | Dry-run artifact review |
| `live_ledger_verify` | Live ledger schema validator |
| `live_operator_config_override_review` | Operator config override review |
| `live_operator_release_checklist` | Operator release checklist |
| `live_order_submission_approval` | Order submission approval artifact |
| `live_post_submit_ledger_update_dry_run` | Post-submit ledger dry-run |
| `live_pre_submit_checklist` | Unified pre-submit checklist |
| `live_pre_submit_ledger_dry_run` | Pre-submit ledger dry-run |
| `live_readiness_gate` | GO/NO-GO readiness gate |
| `live_readiness_history_review` | Readiness history trend review |
| `live_real_submit_pr_approval` | PR approval artifact |
| `live_safety_status` | Read-only safety config status |
| `live_shadow_preflight` | Shadow preflight (read-only) |
| `live_shadow_review` | Shadow preflight artifact review |
| `live_shadow_screen_review` | Symbol screen artifact review |
| `live_shadow_screen_symbols` | Multi-symbol live sizing screen |
| `live_submit` | Dry-run submit plan writer |
| `live_submit_blocked_review` | Blocked report reviewer |
| `live_submit_enablement_gate` | GO/NO-GO enablement gate |
| `live_submit_executor_check` | Executor gate check |
| `live_submit_plan_review` | Submit plan artifact reviewer |
| `live_trading_approval` | Live trading approval artifact |
| `live_v2_approvals_review` | V2 dual-approval reviewer |
| `live_v2_executor_readiness_review` | V2 executor readiness reviewer |
| `live_v2_final_readiness_review` | V2 combined readiness review |
| `live_v2_readiness_bundle` | V2 readiness bundle runner |

#### Manual Live / Paper Guard (4 tools)

These implement manual operator gates for live and paper submission.
They are part of the live-readiness safety pipeline and are referenced
in operator runbooks.

| Tool | Purpose |
|------|---------|
| `live_position_reconciliation_readonly` | Read-only live position reconciliation |
| `live_single_manual_submit` | One-time manual live submit gate |
| `live_single_submit_approval_review` | Single-submit approval review |
| `manual_position_status_checker_readonly` | Manual position status checker |

---

### Conditional move candidates — `scripts/` (6 tools)

These support paper trading operations and diagnostics.  They **may** move here
in PR 9D, but **only after** all preconditions are met:

- PR 9B inventory tests pass (already done).
- Source scan confirms zero `from src.tools.paper_*` cross-imports outside `tests/`.
- Each moved tool retains its `python -m` CLI surface via a shim or the move target.
- All test import paths updated in the same PR.
- Full suite passes before and after the move.

| Tool | Purpose |
|------|---------|
| `paper_ledger_import` | Offline paper ledger backfill |
| `paper_ledger_verify` | Paper ledger schema validator |
| `paper_pre_submit_check` | Paper pre-submit checklist |
| `paper_smoke_check` | Paper workflow smoke test |
| `paper_status` | Read-only paper status / doctor |
| `replay_order_reconciliation` | Offline reconciliation replay |

---

## Rules for Adding Files Here

1. **No safety-critical tools.** `live_*` and `manual_*` tools stay in `src/tools/` permanently.
2. **No move without passing tests.** A moved module is a broken import until tests verify new paths.
3. **No deletion without full coverage.** Tests must prove any replacement works before old code is removed.
4. **No broker/API/credentials access.** Scripts here must not contact Alpaca or read credentials.
5. **No automated trading.** Scripts here are structural utilities only.
6. **Live/paper tools remain fail-closed.** No refactor step may relax a safety gate.

---

## Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No tool moved here in this PR; `src/tools/` unchanged |
| No Alpaca SDK imported | No `src/` changes; no new imports possible |
| No credentials read | No `src/` changes |
| No order submission | No `src/` changes |
| Paper gate unchanged | Paper tools untouched in `src/tools/` |
| Live gate unchanged | Live tools untouched in `src/tools/` |
| Counts locked | `tests/test_tools_inventory.py` enforces 30/4/6/40 classification |

---

Nothing in this directory or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
