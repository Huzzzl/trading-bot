# Paper-Candidate Manual Review Workflow

Design document for S15: the manual review workflow that follows an S14
PAPER_CANDIDATE_ELIGIBLE classification.

**S15 is docs-only. No source code changes. No tests. No artifacts.**
**No paper or live trading approval. No broker, API, credential, env,**
**network, or order access. No automatic execution or promotion.**

---

## 1. Purpose

An S14 `PAPER_CANDIDATE_ELIGIBLE` classification is a research finding, not
a trading approval. Before any paper configuration design may begin, a human
reviewer must manually examine the evidence bundle, validate provenance, and
record an explicit review decision.

This document defines:

- what inputs the reviewer inspects
- the manual review checklist
- the review decision vocabulary
- the evidence bundle required for the record
- what APPROVED_FOR_PAPER_CONFIG_DESIGN does and does not authorise
- the S16 future plan

---

## 2. Scope

| Item | In scope |
|------|----------|
| Define manual review checklist | Yes |
| Define review decision vocabulary | Yes |
| Define required evidence bundle | Yes |
| Define what each decision authorises | Yes |
| Describe S16 future plan | Yes |
| Implement any review workflow tooling | **No — deferred to a future PR** |
| Implement paper trading | **No** |
| Implement live trading | **No** |
| Add broker, API, Alpaca, or credential access | **No** |
| Read environment variables | **No** |
| Make network calls | **No** |
| Submit, request, or cancel any order | **No** |
| Approve paper or live trading automatically | **No** |
| Write any source code or tests | **No** |
| Write any output, report, or artifact | **No** |

---

## 3. Inputs

The reviewer works from already-persisted S10/S11 artifacts combined with the
S14 promotion result. No live data, broker connection, or credential access is
required or permitted at this stage.

| Input | Source |
|-------|--------|
| `manifest.json` | S10/S11 persistence run directory |
| `pipeline_summary.json` | S10/S11 persistence run directory |
| `reports/<candidate_id>.json` | S10/S11 persistence run directory |
| `CandidatePromotionResult` | S14 evaluator output (status, criteria_checked, criteria_failed) |
| `manifest.git_commit_sha` | Provenance link to reviewed source code |
| `ResearchReport` summary metrics | Aggregate walk-forward performance fields |
| `ResearchReport` per-split metrics | Per-window performance and diagnostic fields |
| Candidate universe metadata | Group, symbol, interval, strategy_family, holding_horizon |
| Reviewer notes | Free-text observations recorded during review |

---

## 4. Manual Review Checklist

The reviewer must work through each item below in order. Every item must be
explicitly marked before a decision is recorded.

### 4.1 Promotion status verification

- [ ] Confirm `CandidatePromotionResult.status == PAPER_CANDIDATE_ELIGIBLE`
- [ ] Confirm `criteria_failed` is empty
- [ ] Confirm `result == "PASS"`

### 4.2 Schema and provenance

- [ ] Confirm `report.schema_version == "S6/1.0"`
- [ ] Confirm `manifest.schema_version == "S9/1.0"`
- [ ] Confirm `manifest.git_commit_sha` is present and non-empty
- [ ] Trace git SHA to a reviewed, merged commit on `main`
- [ ] Confirm no manual edits to report or manifest files
  (file content must match what `persist_pipeline_run_result()` would produce)

### 4.3 Safety flag verification

- [ ] Confirm all five flags are `false` in `manifest.safety`:
  `broker_calls_made`, `credentials_read`, `network_calls_made`,
  `order_action_requested`, `live_trading_allowed`
- [ ] Confirm all five flags are `false` in `report.safety`
- [ ] Confirm `CandidatePromotionResult` safety flags are all `False`

### 4.4 Performance metrics inspection

- [ ] Inspect `summary.average_monthly_return_mean` — is it materially above zero
  after typical transaction costs?
- [ ] Inspect `summary.average_monthly_return_min` — is the worst walk-forward
  window return acceptable?
- [ ] Inspect `summary.max_drawdown_worst` — is the worst drawdown within
  acceptable risk tolerance?
- [ ] Inspect `summary.total_trades_sum` — is the trade count sufficient for
  statistical confidence?
- [ ] Inspect per-split metrics for consistency across windows (no single
  window driving all return)

### 4.5 Artifact and anomaly inspection

- [ ] Check `session_end_frequency` in each split: any value > 0.50 is a
  warning; > 0.95 disqualifies (S14 would have caught this, but reviewer
  confirms independently)
- [ ] Check `exposure_pct` in each split: near-zero exposure with extreme
  Sharpe is a low-exposure artifact (S14 would have caught this, but
  reviewer confirms independently)
- [ ] Check `splits.splits_blocked` count: many blocked splits reduce
  confidence in stability
- [ ] Check `splits.splits_error` count: any errors must be investigated
  before proceeding (S14 rejects these, so this should be zero)

### 4.6 Overfitting risk assessment

- [ ] Is the candidate concentrated on a single symbol or time window?
- [ ] Does performance degrade significantly in later walk-forward windows
  (recency bias or look-ahead concern)?
- [ ] Are the strategy parameters generic or suspiciously curve-fitted to the
  test period?
- [ ] Is the holding horizon and interval consistent with the intended
  live execution cadence?

### 4.7 Evidence sufficiency

- [ ] Is the walk-forward test period long enough (recommend ≥ 12 months of
  out-of-sample data across all splits)?
- [ ] Are there enough splits for stability evidence (recommend ≥ 3 non-blocked
  splits)?
- [ ] Does the candidate have a plausible economic rationale for its edge?
- [ ] Is the research run reproducible from the recorded git SHA?

### 4.8 Reviewer record

- [ ] Reviewer name:
- [ ] Review date (UTC):
- [ ] Decision (one of the values in §5):
- [ ] Risk notes (free text):
- [ ] Known limitations (free text):
- [ ] Reference to evidence bundle location (local path or archive):

---

## 5. Review Decisions

| Decision | Meaning |
|----------|---------|
| `APPROVED_FOR_PAPER_CONFIG_DESIGN` | All checklist items passed; a future docs-only paper configuration design PR is permitted |
| `NEEDS_MORE_RESEARCH` | Evidence is insufficient; additional walk-forward data or splits required before re-review |
| `REJECTED_MANUAL_REVIEW` | Reviewer found a disqualifying issue not caught by S14 automation |
| `BLOCKED_SAFETY_REVIEW` | Safety flag concern discovered during manual review |
| `BLOCKED_PROVENANCE` | Git SHA untraceable, manifest edited, or run reproducibility cannot be verified |

### What `APPROVED_FOR_PAPER_CONFIG_DESIGN` authorises

`APPROVED_FOR_PAPER_CONFIG_DESIGN` authorises **only** the creation of a
future docs-only paper configuration design PR (S16). It does **not**:

- Approve paper trading
- Approve live trading
- Allow any broker, API, Alpaca, or network connection
- Allow any credential or environment variable access
- Allow any order submission, cancellation, or modification
- Change any runtime or execution module
- Change any live gate, kill switch, or fail-closed guard
- Allow the runtime state machine to accept the candidate as an input
- Constitute financial advice or a trading recommendation

Paper and live trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by this review decision.

### Non-approval decisions

`NEEDS_MORE_RESEARCH`, `REJECTED_MANUAL_REVIEW`, `BLOCKED_SAFETY_REVIEW`,
and `BLOCKED_PROVENANCE` do not authorise any further step. The candidate
must complete a new S11 research run and pass S14 again before re-review,
unless the issue is purely administrative (e.g., provenance documentation).

---

## 6. Required Evidence Bundle

The following items must be collected and retained before the review decision
is recorded. The bundle exists as a local archive only; no files are
committed to the repository.

| Item | Required |
|------|----------|
| `manifest.json` (full content) | Yes |
| `pipeline_summary.json` (full content) | Yes |
| `reports/<candidate_id>.json` (full content) | Yes |
| S14 `CandidatePromotionResult` (serialised dict) | Yes |
| Completed §4 checklist (all items marked) | Yes |
| Reviewer name and date | Yes |
| Review decision (§5 value) | Yes |
| Risk notes | Yes |
| Known limitations | Yes |
| Git SHA verification evidence (e.g., `git show <sha> --stat`) | Yes |

---

## 7. Future S16 Plan

S16 will add a docs-only paper configuration design:
`docs/paper_trading_config_design.md`.

### 7.1 Scope of S16

| Item | In scope for S16 |
|------|-----------------|
| Define paper config fields (symbol, interval, max position size, etc.) | Yes |
| Define paper config schema | Yes |
| Define paper config validation rules | Yes |
| Describe how config would be consumed by a future paper runner | Yes |
| Implement paper trading | **No** |
| Connect to broker or Alpaca API | **No** |
| Access credentials or environment variables | **No** |
| Submit any order | **No** |
| Modify any runtime or execution module | **No** |
| Approve live trading | **No** |

### 7.2 What S16 does not grant

S16 paper config design is not paper trading approval. Paper trading requires:

1. A complete paper configuration design (S16)
2. A separate paper trading implementation PR with full gate review
3. An explicit paper trading approval artifact (new type, separate PR)
4. An observation period on the paper account
5. Evidence review before any live trading consideration

---

## 8. Safety Statement

This design document does not:

- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Add any broker, API, Alpaca, credential, environment variable, or network
  access to the codebase.
- Submit, request, place, cancel, or modify any order.
- Enable automated execution of any kind.
- Change the live gate status, kill switch, or any fail-closed guard.
- Grant any permission beyond allowing a future docs-only paper config design.

`PAPER_CANDIDATE_ELIGIBLE` is a research classification. Manual review is
a human verification step. `APPROVED_FOR_PAPER_CONFIG_DESIGN` is permission
to design — not permission to trade.

Live and paper trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by this document or by any review decision
described herein.

---

*Nothing in this document is financial advice.*
*No position sizing, entry/exit timing, or trading decision is implied or approved.*
