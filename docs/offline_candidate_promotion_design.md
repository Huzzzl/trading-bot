# Offline Candidate Promotion Design

Design document for S13: offline candidate promotion review.

**S13 is docs-only. No source code changes. No tests. No persistence artifacts.**
**No paper or live trading approval. No broker, API, credential, env, network, or**
**order access. No automatic promotion into any execution path.**

---

## 1. Purpose

After the S10/S11 pipeline writes a research run to disk, a separate offline
promotion review step determines whether any persisted candidate meets the
threshold to be classified as **PAPER_CANDIDATE_ELIGIBLE**. That classification
is a research finding, not a trading approval. It means the candidate is
eligible for further human review before any paper trading decision is made.

This document defines:

- what inputs the promotion evaluator will read
- the eligibility and disqualification criteria
- the promotion status vocabulary
- the human review requirements that still apply after promotion
- the S14 implementation plan

---

## 2. Scope

| Item | In scope |
|------|----------|
| Define promotion criteria | Yes |
| Define promotion status vocabulary | Yes |
| Define inputs and disqualification rules | Yes |
| Define human review requirements | Yes |
| Describe S14 implementation plan | Yes |
| Implement promotion logic | **No — deferred to S14** |
| Write any source code | **No** |
| Add any tests | **No** |
| Write any output, report, or artifact | **No** |
| Approve paper trading | **No** |
| Approve live trading | **No** |
| Access broker, API, credentials, network, or env | **No** |
| Submit or request any order | **No** |

---

## 3. Inputs

The promotion evaluator reads from already-persisted S10/S11 artifacts. All
inputs are local files written by `persist_pipeline_run_result()`. No network
access, no broker calls, no environment variable reads.

### 3.1 Manifest

`<output_dir>/<run_id>/manifest.json` — schema version "S9/1.0".

Required fields read:

| Field | Use |
|-------|-----|
| `schema_version` | Verify supported schema |
| `generated_at_utc` | Run timestamp provenance |
| `git_commit_sha` | Source code version at time of run |
| `result` | Top-level pipeline result |
| `safety` | All five safety flags |
| `files.pipeline_summary` | Filename for cross-check |
| `files.reports` | List of report filenames for cross-check |

### 3.2 Pipeline summary

`<output_dir>/<run_id>/pipeline_summary.json`.

Required fields read:

| Field | Use |
|-------|-----|
| `result` | Pipeline-level result |
| `summary.reports_passed` | Count of PASS reports |
| `summary.reports_blocked` | Count of BLOCKED reports |
| `summary.reports_error` | Count of ERROR reports |
| `summary.best_candidate_id` | Cross-check against per-report result |
| `candidate_filter` | Scope of candidate universe evaluated |

### 3.3 Per-candidate report

`<output_dir>/<run_id>/reports/<candidate_id>.json` — schema version "S6/1.0".

Required fields read per report:

| Field | Use |
|-------|-----|
| `schema_version` | Verify "S6/1.0" |
| `candidate.candidate_id` | Cross-check against filename |
| `candidate.group` | Provenance |
| `candidate.symbol` | Provenance |
| `candidate.interval` | Provenance |
| `summary.result` | Candidate-level PASS/BLOCKED/ERROR |
| `summary.blocker` | Reason if not PASS |
| `summary.splits_passed` | Walk-forward stability |
| `summary.splits_blocked` | Walk-forward blockers |
| `summary.splits_error` | Walk-forward errors |
| `summary.validations_passed` | Metrics validation count |
| `summary.validations_blocked` | Metrics validation failures |
| `summary.average_monthly_return_mean` | Return quality gate |
| `summary.average_monthly_return_min` | Worst-month stability gate |
| `summary.max_drawdown_worst` | Drawdown gate |
| `summary.total_trades_sum` | Trade count gate |
| `safety` | All five safety flags (must all be false) |
| `notes` | Free-text provenance notes |

---

## 4. Eligibility Criteria

A candidate is eligible for PAPER_CANDIDATE_ELIGIBLE only when **all** of the
following conditions are met. Any single failure → REJECTED or a more specific
status.

### 4.1 Result

- `summary.result == "PASS"`
- No `"ERROR"` in any split result

### 4.2 Safety flags

All five flags must be `false` in both the per-report `safety` mapping and the
manifest-level `safety` block:

- `broker_calls_made`
- `credentials_read`
- `network_calls_made`
- `order_action_requested`
- `live_trading_allowed`

Any `true` value → **BLOCKED_SAFETY** regardless of performance metrics.

### 4.3 Trade count

`summary.total_trades_sum` must not be `null` and must be ≥ 30.

Rationale: fewer than 30 trades does not provide statistically meaningful
performance evidence.

### 4.4 Return quality

`summary.average_monthly_return_mean` must not be `null` and must be > 0.0
(post-cost positive average monthly return across walk-forward test windows).

### 4.5 Drawdown

`summary.max_drawdown_worst` must not be `null` and must be ≥ −0.25
(max drawdown no worse than −25%; expressed as a negative decimal per S4
convention).

### 4.6 Metrics validation

`summary.validations_blocked` must equal 0.

Rationale: blocked validation indicates non-finite or convention-violating
metrics, which disqualify a candidate regardless of surface performance.

### 4.7 Walk-forward split ratio

`summary.splits_error` must equal 0.

A high ratio of blocked splits is a warning but not an automatic disqualifier —
NEEDS_MORE_DATA is returned instead (see §5).

### 4.8 Artifact sanity

- `schema_version` in the per-report JSON must equal `"S6/1.0"` — any other
  value → **BLOCKED_SCHEMA**
- `manifest.schema_version` must equal `"S9/1.0"` — any other value →
  **BLOCKED_SCHEMA**
- `candidate.candidate_id` must match the report filename
  (`<candidate_id>.json`) — mismatch → REJECTED
- `files.reports` list in the manifest must be consistent with the set of
  report files present on disk — inventory mismatch → REJECTED

### 4.9 Low-exposure Sharpe artifact

If `summary.average_monthly_return_mean` is non-null and
`summary.total_trades_sum` is ≥ 30 but the individual split metrics show
`exposure_pct < 0.01` combined with near-zero or extreme Sharpe, the candidate
is flagged for NEEDS_MORE_DATA rather than PASS. The S14 implementation must
apply the same low-volatility artifact check used in S2
(`apply_candidate_acceptance_gates`).

### 4.10 Session-end artifact

If any split shows `session_end_frequency > 0.95` the candidate is disqualified
(REJECTED). This mirrors the S2 acceptance gate.

---

## 5. Disqualification Criteria

Any disqualification criterion produces one of the terminal statuses below.
Disqualifications are checked before eligibility criteria; a single hit
short-circuits.

| Criterion | Status |
|-----------|--------|
| Any safety flag `true` | BLOCKED_SAFETY |
| `schema_version` not in supported list | BLOCKED_SCHEMA |
| `manifest.schema_version` not in supported list | BLOCKED_SCHEMA |
| `candidate_id` mismatch between JSON field and filename | REJECTED |
| Manifest file inventory inconsistent with disk | REJECTED |
| `summary.result != "PASS"` | REJECTED |
| `summary.splits_error > 0` | REJECTED |
| `summary.validations_blocked > 0` | REJECTED |
| `summary.total_trades_sum` null or < 30 | REJECTED |
| `summary.max_drawdown_worst` null or worse than −0.25 | REJECTED |
| `session_end_frequency > 0.95` in any split | REJECTED |
| `summary.average_monthly_return_mean` null | NEEDS_MORE_DATA |
| `splits_blocked` ratio > 0.5 (majority of splits blocked) | NEEDS_MORE_DATA |
| Low-exposure Sharpe artifact detected | NEEDS_MORE_DATA |
| `git_commit_sha` absent from manifest | REJECTED |
| Missing required field in manifest or report | REJECTED |

---

## 6. Promotion Statuses

| Status | Meaning |
|--------|---------|
| `NOT_REVIEWED` | Run has not been evaluated yet |
| `PAPER_CANDIDATE_ELIGIBLE` | All eligibility criteria met; ready for human review |
| `NEEDS_MORE_DATA` | Partially qualifying but insufficient evidence |
| `REJECTED` | One or more disqualification criteria met |
| `BLOCKED_SAFETY` | Any safety flag was non-false |
| `BLOCKED_SCHEMA` | Schema version not supported |

Status is a research classification only. It does not constitute paper or live
trading approval. It does not trigger any execution path.

---

## 7. Human Review Requirements

Promotion to **PAPER_CANDIDATE_ELIGIBLE** still requires manual human review
before any paper trading decision may be made.

The following approvals remain explicitly **not granted** by this design or by
any automated promotion evaluator:

- Paper trading is **not approved**. Paper trading requires a separate review,
  approval artifact, and PR.
- Live trading is **not approved** and remains fully blocked.
- No automated order action follows from PAPER_CANDIDATE_ELIGIBLE status.
- No broker, API, or network call is made during or after promotion.
- No credentials or environment variables are read.
- Promotion is a read-only offline classification of already-written artifacts.

The human reviewer must independently verify:

1. The source research run is trustworthy (git SHA traceable to reviewed code)
2. The strategy's out-of-sample performance is genuinely positive
3. The candidate is suitable for the target paper account and risk budget
4. The paper trading configuration and risk gates are ready (separate S-series)

---

## 8. S14 Implementation Plan

S14 will implement a pure offline promotion evaluator as described below.

### 8.1 New file

`src/research/candidate_promotion.py`

- `CandidatePromotionResult` — frozen dataclass:
  `result`, `blocker`, `candidate_id`, `run_id`, `status`
  (`PromotionStatus` enum), `criteria_checked`, `criteria_failed`,
  `safety_flags` (all always False — the evaluator itself makes no
  broker/credential/network/order/live calls).
- `PromotionStatus` — enum with the 6 values from §6.
- `evaluate_candidate_for_promotion(report_dict, *, manifest_dict)` →
  `CandidatePromotionResult`.
  - Input: already-loaded plain dicts (not file paths).
  - No file I/O. No broker calls. No credentials. No network. No env vars.
  - Pure function: same inputs → same output.
  - All safety flags on the result are always False.

### 8.2 New test file

`tests/test_candidate_promotion.py`

- Tests for all eligibility and disqualification criteria.
- Tests for each `PromotionStatus` value.
- Tests for safety flag blocking.
- Tests for schema mismatch.
- Tests for missing/null required fields.
- Tests for session_end and low-exposure artifact detection.
- Source scan: no broker/API/credential/env/network/order/live imports.

### 8.3 Constraints

- No runtime or execution module changes.
- No paper or live trading approval.
- No modification to `src/backtest/`, `src/strategy/`, `src/risk/`,
  `src/runtime/`, `src/execution/`, `src/main.py`, or any live-gate modules.
- No file writes by default — the evaluator receives dicts and returns a result.
- `data/cache/` and `output/` remain gitignored and uncommitted.

### 8.4 Relationship to S15+

S14 promotion evaluator output is a research artefact. Subsequent S-series PRs
may define how PAPER_CANDIDATE_ELIGIBLE status feeds into a paper trading
configuration review, but those PRs must each have their own design document
and explicit approvals. The S14 evaluator does not wire into the execution path.

---

## 9. Safety Statement

This design document does not:

- Approve paper trading for any candidate or strategy.
- Approve live trading in any form.
- Add any broker, API, Alpaca, credential, environment variable, or network
  access to the codebase.
- Submit, request, place, cancel, or modify any order.
- Enable automated execution of any kind.
- Change the live gate status, kill switch, or any fail-closed guard.

Live and paper trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by this document.

---

*Nothing in this document is financial advice.*
*No position sizing, entry/exit timing, or trading decision is implied or approved.*
