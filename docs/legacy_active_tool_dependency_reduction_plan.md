# Legacy Active Tool Dependency Reduction Plan

Design document for PR R4b–R4g: reduce the 11 legacy tools that PR R4 was
forced to keep active because of old tool-to-tool import chains.

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

## 1. Direction Reminder

The final target is a **fully automated online trading bot**. Manual readiness
chains — checklists, approval bundles, shadow review artifacts — served a
purpose during the manual phase but must not define runtime architecture.

> `ACTIVE_TOOLS` should eventually mean **runtime or research necessity**,
> not old import-chain survival.

PR R4 archived 10 manual-operator tools and deleted 3 redundant stubs, but a
dependency scan forced 11 tools back into `ACTIVE_RUNTIME_CANDIDATE` because
they are still imported by other legacy tools. This plan documents how to break
those chains one cluster at a time.

No reduction step may weaken fail-closed behavior. Any checklist or approval
dependency that is removed must be replaced by an explicit
`BLOCKED / not-implemented / future-automated-risk-gate` condition.

---

## 2. Post-R4 Baseline

| Metric | Value |
|--------|-------|
| `ACTIVE_TOOLS` in `src/tools/` | 30 |
| `ACTIVE_RESEARCH_TOOLS` | 3 |
| `ACTIVE_RUNTIME_CANDIDATE_TOOLS` | 26 |
| `PRESERVE_RUNTIME_SUPPORT_TOOLS` | 1 |
| `ARCHIVED_TOOLS` (in `scripts/archive/manual_live_readiness/`) | 10 |
| `DELETED_TOOLS_R4` | 3 |
| Full suite baseline | 4 513 passed |

---

## 3. The 11 Reclassified Legacy Tools

These tools were originally classified as `ARCHIVE_MANUAL` or `DELETE_CANDIDATE`
in PR R2 but were promoted to `ACTIVE_RUNTIME_CANDIDATE` in PR R4 because the
dependency scan found active imports. They are **not** active because they
clearly serve automated runtime — they are active because legacy tool chains
reference them.

| Tool | Original R2 class | Reason kept in R4 |
|------|-------------------|-------------------|
| `live_dry_run_review` | `ARCHIVE_MANUAL` | imported by `live_pre_submit_checklist` |
| `live_pre_submit_checklist` | `ARCHIVE_MANUAL` | imported by `live_submit` |
| `paper_smoke_check` | `ARCHIVE_MANUAL` | imported by `test_paper_ledger.py` (immutable in R4) |
| `paper_status` | `ARCHIVE_MANUAL` | imported by 5 active FREEZE_DEFERRED tools via `check_config` |
| `live_shadow_review` | `DELETE_CANDIDATE` | imported by `live_readiness_gate` |
| `live_shadow_screen_review` | `DELETE_CANDIDATE` | imported by `live_readiness_gate` |
| `live_v2_approvals_review` | `DELETE_CANDIDATE` | imported by `live_submit_enablement_gate` |
| `live_v2_executor_readiness_review` | `DELETE_CANDIDATE` | imported by `live_submit_enablement_gate` |
| `live_v2_final_readiness_review` | `DELETE_CANDIDATE` | imported by `live_v2_readiness_bundle` |
| `live_v2_readiness_bundle` | `DELETE_CANDIDATE` | v2 chain; imported transitively |
| `replay_order_reconciliation` | `DELETE_CANDIDATE` | imported by `paper_status` and `test_paper_ledger.py` |

---

## 4. Dependency Chain Clusters

### Cluster A — Live submit checklist chain

```
live_submit
    └── live_pre_submit_checklist
            └── live_dry_run_review
```

`live_submit` imports `live_pre_submit_checklist` for a manual pre-flight
checklist. `live_pre_submit_checklist` imports `live_dry_run_review` to review
the dry-run artifact.

**Question:** Should the automated runtime use this manual checklist chain, or
should `live_submit` depend on a future `automated_risk_gate` instead?

**Assessment:** The checklist chain exists for human operator use. In automated
runtime the equivalent gate is a programmatic risk engine, not a CLI checklist.
The dependency is a historical accident, not a runtime requirement.

---

### Cluster B — Live readiness shadow chain

```
live_readiness_gate
    ├── live_shadow_review
    └── live_shadow_screen_review
```

`live_readiness_gate` imports `live_shadow_review` and `live_shadow_screen_review`
to validate shadow preflight and symbol-screen artifacts.

**Question:** Should these shadow review tools remain active, or should the
readiness gate be replaced by runtime state and risk checks?

**Assessment:** Shadow review tools parse operator-produced JSON artifacts.
Automated runtime does not produce those artifacts; it produces structured
state objects directly. The shadow review imports are not needed in the
final runtime path.

---

### Cluster C — V2 approval bundle chain

```
live_submit_enablement_gate
    ├── live_v2_approvals_review
    └── live_v2_executor_readiness_review

live_v2_readiness_bundle
    └── live_v2_final_readiness_review
```

`live_submit_enablement_gate` imports two v2 review tools that parse dual
human-approval JSON artifacts. `live_v2_readiness_bundle` imports the combined
review.

**Question:** Are these human approval bundles obsolete once an automated state
machine and risk gate exist?

**Assessment:** Yes. The v2 approval bundle is a human-operator construct.
Automated runtime approval is represented by explicit config/state flags, not
parsed JSON artifacts. Once a proper fail-closed approval state placeholder
exists, these imports are unnecessary.

---

### Cluster D — Paper status / replay chain

```
paper_status
    └── replay_order_reconciliation  (lazy import)

test_paper_ledger.py
    └── replay_order_reconciliation  (direct import)
```

`paper_status` lazily imports `replay_order_reconciliation` for post-submit
reconciliation output. `test_paper_ledger.py` also imports it directly.

**Question:** Should this be converted into a runtime reconciliation library
in `src/reporting/` or `src/execution/` rather than a `src/tools/` CLI?

**Assessment:** Reconciliation logic is a runtime concern, not a CLI tool.
Moving the core logic to `src/reporting/` or `src/execution/` would decouple
it from the `src/tools/` namespace and allow `paper_status` and the test to
use it as a library without keeping a CLI wrapper active.

---

### Cluster E — Paper smoke check

```
test_paper_ledger.py
    └── from src.tools.paper_smoke_check import main as smoke_main
```

`test_paper_ledger.py` imports `main` from `paper_smoke_check` to run a
smoke check as part of ledger tests. This is the only reason `paper_smoke_check`
remains in `src/tools/`.

**Question:** Should the test use lower-level ledger primitives directly,
removing the dependency on the CLI tool?

**Assessment:** Yes. The smoke check is a thin wrapper around ledger primitives.
Rewriting the test to call those primitives directly eliminates the
`src/tools/paper_smoke_check` dependency entirely.

---

## 5. Proposed Reduction Strategy

Each sub-PR is docs + implementation. No sub-PR may weaken fail-closed behavior.
Milestone R4b is this document (docs-only).

### PR R4b — This plan (docs-only) ✓

Document post-R4 legacy tool dependency clusters and the reduction roadmap.
No code changes.

---

### PR R4c — Break `paper_smoke_check` dependency from tests ✓

**Status: implemented.**

**Scope:** `tests/test_paper_ledger.py`, `src/tools/paper_smoke_check.py`

**Change:**
- `TestSmokeCheckDoesNotAppendLedger.test_smoke_check_does_not_write_ledger`
  rewritten as `test_paper_preview_path_does_not_write_ledger` — uses
  `AlpacaBrokerAdapter` with a locally-defined fake client + `OrderIntent` CSV
  write directly; no `paper_smoke_check` import.
- `paper_smoke_check.py` archived to `scripts/archive/manual_live_readiness/`
  (archive header prepended; not importable as `src.tools.paper_smoke_check`).
- `tests/test_paper_smoke_check.py` deleted (66 tests removed — CLI wrapper
  tests; behavioral ledger coverage preserved in `test_paper_ledger.py`).

**Safety invariant:** The behavioral assertion is preserved — the paper preview
execution path (fake broker preflight + CSV write) must not call
`append_ledger_row`. The assertion is now tested against the execution
primitives directly rather than through the CLI wrapper.

**Actual outcome:** `paper_smoke_check` removed from `ACTIVE_TOOLS`.
`ACTIVE_TOOLS`: 30 → 29. `ARCHIVED_TOOLS`: 10 → 11. Full suite: 4 440 passed.

---

### PR R4d — Extract `replay_order_reconciliation` into runtime helper

**Scope:** `src/tools/replay_order_reconciliation.py`, `paper_status.py`,
`test_paper_ledger.py`

**Change:**
- Extracted reconciliation logic into `src/reporting/replay_reconciliation.py`.
- Updated `paper_status` (`check_replay`) and `test_paper_ledger.py` (Section 7)
  to import `replay` from `src.reporting.replay_reconciliation`.
- Updated `test_paper_status.py`: stale patch target
  `src.tools.replay_order_reconciliation.replay` →
  `src.reporting.replay_reconciliation.replay`.
- Archived `src/tools/replay_order_reconciliation.py` to
  `scripts/archive/manual_live_readiness/replay_order_reconciliation.py`.
- Removed `TestCLIMain` (5 tests) from `test_replay_order_reconciliation.py`;
  all library behaviour tests kept.

**Safety invariant:** Reconciliation correctness tests remain. Only the
`src/tools/` CLI entry-point is archived; the logic lives on in
`src/reporting/replay_reconciliation.py`.

**Actual outcome:** `replay_order_reconciliation` removed from `ACTIVE_TOOLS`.
`ACTIVE_TOOLS`: 29 → 28. `ARCHIVED_TOOLS`: 11 → 12. Full suite: 4 428 passed.

---

### PR R4e — Decouple `live_submit` from the checklist chain

**Scope:** `src/tools/live_submit.py`, `src/tools/live_pre_submit_checklist.py`,
`src/tools/live_dry_run_review.py`

**Change:**
- Replaced `_run_checklist` in `live_submit.py` with `_check_automated_risk_gate()`.
  `_AUTOMATED_RISK_GATE_IMPLEMENTED = False` constant ensures live submit is
  fail-closed until a real automated gate exists.
- Verified no other active callers of `live_pre_submit_checklist` or
  `live_dry_run_review` remained; archived both to
  `scripts/archive/manual_live_readiness/`.
- Deleted `tests/test_live_pre_submit_checklist.py` and
  `tests/test_live_dry_run_review.py` (manual CLI checklist tests only).
- Updated `tests/test_live_submit.py`: removed `_run_checklist` mock and
  happy-path tests; added `TestCheckAutomatedRiskGate` class.

**Safety invariant:** `live_submit` remains fail-closed. `_AUTOMATED_RISK_GATE_IMPLEMENTED`
is `False`; main() always exits 1 until a real automated gate is implemented.

**Actual outcome:** `live_pre_submit_checklist` and `live_dry_run_review`
removed from `ACTIVE_TOOLS`. `ACTIVE_TOOLS`: 28 → 26. `ARCHIVED_TOOLS`: 12 → 14.
Full suite: 4 337 passed.

---

### PR R4f — Decouple `live_readiness_gate` from shadow review tools

**Scope:** `src/tools/live_readiness_gate.py`, `src/tools/live_shadow_review.py`,
`src/tools/live_shadow_screen_review.py`

**Change:**
- Remove `live_readiness_gate`'s imports of `live_shadow_review` and
  `live_shadow_screen_review`.
- Replace with an explicit `BLOCKED — shadow review not required by runtime`
  condition, or remove the shadow review step from the gate if it is
  entirely manual-operator logic.
- Archive or delete both shadow review tools if no active references remain.

**Safety invariant:** `live_readiness_gate` GO/NO-GO result must remain
fail-closed. No change may cause the gate to pass when it would previously
block.

**Expected outcome:** `live_shadow_review` and `live_shadow_screen_review`
removed from `ACTIVE_TOOLS`. `ACTIVE_TOOLS`: 26 → 24 (after R4c+R4d+R4e).

---

### PR R4g — Decouple `live_submit_enablement_gate` from v2 approval bundle

**Scope:** `src/tools/live_submit_enablement_gate.py`,
`src/tools/live_v2_approvals_review.py`,
`src/tools/live_v2_executor_readiness_review.py`,
`src/tools/live_v2_final_readiness_review.py`,
`src/tools/live_v2_readiness_bundle.py`

**Change:**
- Remove `live_submit_enablement_gate`'s imports of the v2 review tools.
- Replace with explicit fail-closed approval state placeholders
  (e.g., `approval_result = BLOCKED — automated approval not yet implemented`).
- Archive or delete all four v2 tools if no active references remain.

**Safety invariant:** `live_submit_enablement_gate` must remain fail-closed.
Human approval bundle removal must be replaced by an explicit block, not a
silent pass.

**Expected outcome:** `live_v2_approvals_review`, `live_v2_executor_readiness_review`,
`live_v2_final_readiness_review`, `live_v2_readiness_bundle` removed from
`ACTIVE_TOOLS`. `ACTIVE_TOOLS`: 24 → 20 (after R4c+R4d+R4e+R4f).

---

## 6. Safety Rules

The following rules apply to every reduction PR (R4c–R4g):

1. **Do not delete runtime primitives.** Only CLI wrappers and manual-operator
   review tools may be archived or deleted.
2. **Do not weaken fail-closed behavior.** Any checklist or approval dependency
   that is removed must be replaced by an explicit `BLOCKED` / `not implemented`
   / `future automated risk gate` condition that preserves the fail-closed result.
3. **No broker/API/credentials/trading.** All reduction PRs are pure code
   refactors; no broker integration, no credentials, no orders.
4. **No live submit approval.** `live_trading_approved=false` and
   `live_order_submission_approved=false` must remain true in all artifacts.
5. **No paper trading approval.** Paper trading is not approved in any
   reduction PR.

---

## 7. Test Strategy

For each reduction PR:

| Category | Action |
|----------|--------|
| Tests that only protect manual review CLI behavior | Remove or rewrite |
| Tests that protect active runtime fail-closed behavior | Keep and strengthen |
| `tests/test_tools_inventory.py` | Update `ACTIVE_RUNTIME_CANDIDATE_TOOLS` count after each PR |
| Overall direction | `ACTIVE_TOOLS` must decrease, not increase |

Specifically:
- **R4c**: update `test_paper_ledger.py` primitives call site; update inventory counts.
- **R4d**: add tests for the new runtime helper module; update inventory counts.
- **R4e**: verify `live_submit` remains fail-closed; remove checklist tests that only
  exercised manual CLI flow; update inventory counts.
- **R4f**: verify `live_readiness_gate` GO/NO-GO logic unchanged; remove shadow review
  CLI tests; update inventory counts.
- **R4g**: verify `live_submit_enablement_gate` remains fail-closed; remove v2 bundle
  CLI tests; update inventory counts.

---

## 8. Direction Checkpoint

After completing R4b–R4g, pause and reassess before R5.

> **If `ACTIVE_TOOLS` is still above ~22–24 after R4g, perform another cleanup
> pass before opening PR R5 (paper runner extraction).**

The target at R5 entry is a lean `src/tools/` with no legacy manual-operator
import chains. Tools in `src/tools/` at R5 entry should all be either:
- Directly needed by automated runtime (state checks, gates, safety guards), or
- Offline research / characterisation tools.

---

## 9. Projected Tool Counts

| After PR | Expected `ACTIVE_TOOLS` | Tools removed |
|----------|------------------------|---------------|
| R4 (current) | 30 | baseline |
| R4c | 29 | `paper_smoke_check` |
| R4d | 28 | `replay_order_reconciliation` |
| R4e | 26 | `live_pre_submit_checklist`, `live_dry_run_review` |
| R4f | 24 | `live_shadow_review`, `live_shadow_screen_review` |
| R4g | 20 | `live_v2_approvals_review`, `live_v2_executor_readiness_review`, `live_v2_final_readiness_review`, `live_v2_readiness_bundle` |

All projections are estimates. Exact counts depend on whether additional active
references are discovered during each reduction PR's dependency scan.

---

## 10. Validation

```bash
git diff origin/main...HEAD -- src tests scripts config output data
# Expected: empty (no src/tests/scripts/config/output/data changes in this PR)
```

`pytest` is not run for docs-only PRs.

---

## 11. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK imported | Zero `src/` changes; no new imports possible |
| No credentials read | Zero `src/` changes |
| No order submission | Zero `src/` changes |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |
| Test suite unchanged | Zero `tests/` changes |

> **This document does not approve automated live trading.**
> **This document does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility.
