# Trading Bot Project Handoff

## Purpose

This file restores reliable project context across conversations and
prevents dependence on a single long chat thread. When starting a new
conversation, read this file first, then verify every claim against the
current GitHub main branch before acting.

Full historical details remain in GitHub PRs, code, tests, and session
handoffs under `docs/handoffs/`. This file records current state only.

---

## Source-of-truth order

1. Current GitHub main branch
2. Current open pull request
3. Code and tests
4. `docs/project_handoff.md` (this file)
5. Latest session handoff (`docs/handoffs/session_*.md`)
6. Old chat context

Rules:

- Every handoff claim must be verified against GitHub before acting.
- When a handoff conflicts with GitHub, code, or tests, GitHub/code/tests
  win.
- Old chat context must never override current repository state.

---

## Current project status

| Field | Value |
|---|---|
| Repository | `Huzzzl/trading-bot` |
| Current phase | Pure offline / paper-preparation |
| Latest completed milestone | S48 |
| Latest merged PR | #252 |
| Current main SHA | `6fce805` |
| Full-suite test count | 8,087 passed |
| Paper trading | **Not approved** |
| Live trading | **Blocked** |
| Broker connection | Not implemented |
| Credentials / network / runtime / order-submission | Not implemented |

---

## Implemented pipeline

The following components are implemented and tested:

- Cached historical-data research and backtest path
- Strategy candidate evaluation and walk-forward evaluation
- Research report generation and candidate promotion workflow
- Paper configuration validation (PC/1.0)
- Pure offline paper simulation
- Paper approval artifact validation (PTA/1.0)
- Paper order planner (POP/1.0 plan generation)
- Paper order plan validator
- Paper order safety gate (22 checks, PASS_DRY_RUN_ONLY)
- Paper order lifecycle state machine (12 statuses, 11 event types)
- Dry-run/no-submit preview renderer (PDRP/1.0, 32-key preview dict)
- In-memory audit ledger (7 entry types, 6 sources)
- PREVIEW_RESULT_RECORDED entry type with source `"preview"`
- S40 preview identity, status, and safety validation
- S41 canonical project handoff and conversation workflow
- S42 read-only paper-broker boundary design (docs-only)
- S43 credential metadata validation and account environment guard
- S44 fake credential provider and fake paper adapter integration tests
- S45 pure offline paper account snapshot reader
- S46 paper account snapshot isolation integration tests (tests-only)
- S47 pure offline paper snapshot reconciliation
- S48 pure offline reconciliation report renderer
- S49 pure offline broker observation workflow coordinator

---

## Current chain

```
paper approval artifact (PTA/1.0)
  -> paper order planner (create_paper_order_plan)
  -> paper order plan validator (validate_paper_order_plan)
  -> paper order safety gate (evaluate_paper_order_safety_gate)
  -> lifecycle creation (create_lifecycle_from_plan)
  -> SAFETY_GATE_PASSED event (apply_lifecycle_event)
  -> display-only dry-run preview (render_paper_dry_run_preview)
  -> PREVIEW_RESULT_RECORDED audit entry (append_audit_entry)
```

The entire chain is pure offline and in memory:

- No stage contacts a broker.
- No stage reads credentials.
- No stage submits, places, modifies, or cancels an order.
- No stage approves paper or live trading.

---

## Key public APIs

### `src/research/paper_approval_validator.py`
- `validate_paper_approval_artifact(artifact: dict) -> PaperApprovalValidationResult`
- `PaperApprovalStatus` (enum)
- `PaperApprovalValidationResult` (frozen dataclass)

### `src/research/paper_order_planner.py`
- `create_paper_order_plan(approval, *, signal_snapshot, sizing_snapshot, ...) -> PaperOrderPlannerResult`
- `PaperOrderPlannerStatus` (enum, 8 members)
- `PaperOrderPlannerResult` (frozen dataclass)

### `src/research/paper_order_plan_validator.py`
- `validate_paper_order_plan(plan: dict) -> PaperOrderPlanValidationResult`
- `PaperOrderPlanStatus` (enum, 8 members)
- `PaperOrderPlanValidationResult` (frozen dataclass)

### `src/research/paper_order_safety_gate.py`
- `evaluate_paper_order_safety_gate(approval, plan, *, current_state) -> PaperOrderSafetyGateResult`
- `PaperOrderSafetyGateStatus` (enum, 11 members)
- `PaperOrderSafetyGateResult` (frozen dataclass, 20 fields)

### `src/research/paper_order_lifecycle.py`
- `create_lifecycle_from_plan(plan, *, lifecycle_id, created_at_utc) -> PaperOrderLifecycleTransitionResult`
- `apply_lifecycle_event(state, *, event_type, event_at_utc, ...) -> PaperOrderLifecycleTransitionResult`
- `PaperOrderLifecycleEventType` (enum, 11 members)
- `PaperOrderLifecycleStatus` (enum, 12 members)
- `PaperOrderLifecycleState` (frozen dataclass)

### `src/research/paper_dry_run_preview.py`
- `render_paper_dry_run_preview(plan, *, gate_snapshot, lifecycle_snapshot, preview_id, rendered_at_utc) -> PaperDryRunPreviewResult`
- `PaperDryRunPreviewStatus` (enum, 7 members)
- `PaperDryRunPreviewResult` (frozen dataclass)

### `src/research/paper_audit_ledger.py`
- `create_empty_audit_ledger(*, ledger_id) -> PaperAuditLedgerResult`
- `append_audit_entry(ledger, *, entry_id, entry_type, recorded_at_utc, source, payload) -> PaperAuditLedgerResult`
- `PaperAuditLedgerEntryType` (enum, 7 members)
- `PaperAuditLedgerStatus` (enum: EMPTY, UPDATED, BLOCKED, ERROR)
- `PaperAuditLedgerState` (frozen dataclass)
- `PaperAuditLedgerResult` (frozen dataclass)

---

## Current schemas and statuses

| Schema / Status | Meaning |
|---|---|
| **PTA/1.0** | Paper Trading Approval artifact schema. In-memory dict validated by S25. |
| **POP/1.0** | Paper Order Plan schema. In-memory dict generated by S30, validated by S27. |
| **PDRP/1.0** | Paper Dry-Run Preview schema. 32-key display-only dict rendered by S36. |
| **PASS_DRY_RUN_ONLY** | Safety gate result permitting only offline dry-run/no-submit rendering. Not order approval. |
| **GATE_PASSED_DRY_RUN_ONLY** | Lifecycle status after SAFETY_GATE_PASSED event. Not order approval. |
| **PREVIEW_RENDERED** | Preview renderer result. Display-only, not an order, not a broker payload. |
| **PREVIEW_RESULT_RECORDED** | Audit ledger entry type for recording a rendered preview. Bookkeeping only. |

---

## Safety invariants

- A paper order plan is not an order.
- A dry-run preview is not an order.
- A dry-run preview is not a broker payload.
- PASS is not order approval.
- PASS_DRY_RUN_ONLY permits only offline dry-run/no-submit rendering.
- PREVIEW_RESULT_RECORDED is audit bookkeeping only.
- Ledger append does not advance lifecycle.
- All five safety flags remain False:
  - `broker_calls_made`
  - `credentials_read`
  - `network_calls_made`
  - `order_action_requested`
  - `live_trading_allowed`
- Paper trading remains not approved.
- Live trading remains blocked.
- Fail closed.

---

## Forbidden boundaries

The following are not implemented and must not be added without a
separate design and review sequence:

- No broker adapter
- No account connector
- No credential reads
- No environment-variable reads
- No network calls
- No order submission
- No order placement
- No order cancellation
- No order modification
- No runtime/execution/main wiring
- No persistence of preview, ledger, lifecycle, plan, approval, or order
  artifacts unless separately designed and approved
- No live-account support
- No automatic paper-trading approval

---

## Latest architectural decisions

- Preview has a dedicated `PREVIEW_RESULT_RECORDED` audit entry type.
- A preview audit payload requires non-empty `preview_id`, `plan_id`, and
  `lifecycle_id` (S40 `payload.preview_identity`).
- `preview_status` must be exactly `"PREVIEW_RENDERED"` (S40
  `payload.preview_status`).
- `display_only` must be `True`.
- `no_submit` must be `True`.
- `broker_payload_created` must be `False`.
- Preview and audit ledger remain in memory.
- Current implementation ends before broker, credential, network, runtime,
  and execution boundaries.
- S42 read-only paper-broker boundary is a design document only. No
  broker connection, credentials, network access, or account access is
  implemented.
- S43 credential metadata validation and account environment guard are
  pure offline in-memory validators. No credential loading, no
  environment-variable reads, no broker/account/network access, no
  adapter construction.
- S44 fake credential provider and fake paper adapter are pure offline
  test-only fakes. No real credentials, no broker SDK, no network, no
  order methods. PASS is not credential approval, not account-access
  approval, not paper-trading approval.
- S45 paper account snapshot reader is a pure offline in-memory
  validator. No broker connection, no credentials, no network, no
  account access, no order-action logic. Snapshot readiness is
  observation only, not account-access approval, not paper-trading
  approval.
- S46 paper account snapshot isolation integration tests prove the
  S43-S45 observation boundary remains isolated from the existing
  offline order-preparation chain. No production source modified.
  Snapshot PASS never invokes planner, validator, gate, lifecycle,
  preview, or ledger. Snapshot result cannot be used as PTA/1.0 or
  POP/1.0 input. Positions and open_orders remain observations only.
  Snapshot readiness does not advance lifecycle.
- S47 paper snapshot reconciliation compares an already-validated
  PaperAccountSnapshotResult with caller-supplied expected observation
  state. Pure offline in-memory function. Difference found is not an
  order signal. Reconciliation does not approve paper trading and
  does not advance lifecycle. No planner/gate/lifecycle/preview/ledger
  calls. No current_state wiring. No order or broker payload creation.
- S48 paper reconciliation report renderer converts an already-produced
  PaperSnapshotReconciliationResult into a deterministic, read-only
  human review report. Pure offline in-memory function. Report
  readiness is observation only. Difference found is not an order
  signal. Report rendering does not advance lifecycle. No file writes,
  no JSON export to disk, no logging side effects, no ledger append.
- S49 paper observation workflow coordinator runs the existing S43-S48
  observation chain (credential → environment → snapshot →
  reconciliation → report) in a single explicit, pure in-memory
  function. Stops at the first blocked stage and never enters the
  order-preparation chain. Verifies all child safety flags exactly
  False before proceeding. Output exposes only stage statuses and
  final read-only report lines; no raw child objects retained.

---

## Current milestone

S48 complete (PR #252 merged). S49 in progress.

- S48 implemented pure offline reconciliation report renderer with
  195 tests
- S49 adds pure offline broker observation workflow coordinator with
  205 tests
- No broker connection implemented
- No credentials loaded
- No environment variables read
- No network access added
- No account accessed
- No real adapter constructed
- No order-action logic added

---

## Next phase

Current task: S49 pure offline broker observation workflow coordinator.

After S49:

- S50+: Implementation only after design review and explicit approval

Explicit constraints:

- Broker integration is not approved.
- Paper trading is not approved.
- Live trading is blocked.
- Any move toward broker connectivity requires a separate design and
  review sequence.

---

## Near-term roadmap

| Step | Description |
|---|---|
| S41 | Canonical handoff and conversation workflow (docs-only) -- **done** |
| S42 | Paper broker read-only boundary design (docs-only) -- **done** |
| S43 | Credential metadata validation and account environment guard -- **done** |
| S44 | Fake credential provider and fake paper adapter integration tests -- **done** |
| S45 | Pure offline paper account snapshot reader -- **done** |
| S46 | Paper account snapshot isolation integration tests (tests-only) -- **done** |
| S47 | Pure offline paper snapshot reconciliation -- **done** |
| S48 | Pure offline reconciliation report renderer -- **done** |
| S49 | Pure offline broker observation workflow coordinator -- **in progress** |
| Later | Implementation only after design review and explicit approval |

---

## Branch and PR hygiene

```bash
git fetch origin
git checkout main
git pull --ff-only origin main
# create a fresh branch from current main
git checkout -b <branch-name> origin/main
# verify the merge base
git diff --name-only origin/main...HEAD
```

- Keep the PR within its allowed file list.
- Run the full test suite for code changes.
- Do not include unrelated generated files.
- Do not reuse stale branch state.
- Confirm `ahead_by=1` and `behind_by=0` for a normal single-commit PR.

---

## PR review checklist

1. Confirm PR state and mergeability.
2. Confirm base SHA and head SHA.
3. Confirm changed files.
4. Compare base and head.
5. Confirm ahead/behind status.
6. Inspect source changes.
7. Inspect tests.
8. Review forbidden patterns.
9. Verify safety invariants.
10. Give an explicit merge or block conclusion.

---

## How to resume in a new conversation

Use this exact bootstrap prompt:

```
Continue the Huzzzl/trading-bot project.

First, read and verify:
1. docs/project_handoff.md
2. the latest docs/handoffs/session_*.md
3. the current GitHub main branch and main SHA
4. the 10 most recent merged pull requests
5. the current open pull request

Do not rely only on old chat memory, and do not blindly trust a
potentially stale handoff.

Before continuing, summarize:
- the current milestone
- the latest merged pull request
- the current main SHA
- the latest verified full-suite test count
- the current paper-trading status
- the current live-trading status
- the current safety boundaries
- the next planned task

If the handoff conflicts with GitHub, code, or tests, treat GitHub,
code, and tests as authoritative. Explain the discrepancy before
proceeding.
```

---

## Update policy

- Update this canonical handoff after major milestone PRs or every 5-8
  PRs.
- Replace stale current facts instead of appending.
- Do not append full PR narratives.
- Keep historical detail in GitHub PRs.
- Create one session handoff when ending a conversation.
- Start a new conversation every 5-7 days, every 5-8 PRs, or earlier
  when the UI becomes slow or context reliability degrades.
