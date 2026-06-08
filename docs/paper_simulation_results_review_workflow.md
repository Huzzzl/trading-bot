# Paper Simulation Results Review Workflow

Design document for S21: the manual review workflow that follows the S19
`run_paper_simulation()` skeleton and the S20 integration evidence.

**S21 is docs-only. No source code changes. No tests. No config files.**
**No artifacts. No paper or live trading approval. No broker, API,**
**credential, env, network, or order access. No automatic execution,**
**persistence, or promotion.**

---

## 1. Purpose

A `PaperSimulationResult` with `result == "PASS"` (S19) and the integration
evidence confirming the S17 → S19 chain behaves correctly (S20) are offline
research findings — not a trading approval. Before any paper trading
architecture may even be designed, a human reviewer must manually examine the
simulation outputs, the integration evidence, and the full upstream provenance
chain, and record an explicit review decision.

This document defines:

- the inputs the reviewer inspects
- the manual review checklist
- the review decision vocabulary
- what `APPROVED_FOR_PAPER_TRADING_DESIGN` does and does not authorise
- the required evidence bundle for the record
- disqualification criteria
- the future S22 plan
- the relationship between this review and paper trading approval

No review tooling is implemented in this PR. No paper trading is approved.
No simulation persistence is implemented.

---

## 2. Scope

| Item | In scope |
|------|----------|
| Define paper simulation results review inputs | Yes |
| Define manual review checklist | Yes |
| Define review decision vocabulary | Yes |
| Define required evidence bundle | Yes |
| Define disqualification criteria | Yes |
| Define what each decision authorises | Yes |
| Describe S22 future plan | Yes |
| Implement any review workflow tooling | **No — deferred to a future PR** |
| Implement simulation persistence | **No** |
| Implement a paper trading runner | **No** |
| Implement paper trading | **No** |
| Implement live trading | **No** |
| Add broker, API, Alpaca, or credential access | **No** |
| Read environment variables | **No** |
| Make network calls | **No** |
| Submit, request, or cancel any order | **No** |
| Approve paper or live trading automatically | **No** |
| Load or write any config file | **No** |
| Write any output, report, or artifact | **No** |
| Change any runtime or execution module | **No** |
| Write any source code or tests | **No** |

---

## 3. Inputs

The reviewer works entirely from already-produced, in-memory or
already-recorded offline evidence. No live data, broker connection,
credential access, or simulation re-run is required or permitted at this
stage.

| Input | Source | Notes |
|-------|--------|-------|
| S17 validated paper config result | `PaperConfigValidationResult` from `validate_paper_config()` | Must show `result == "PASS"` |
| S19 `PaperSimulationResult` | `run_paper_simulation()` output | Must show `result == "PASS"` |
| S20 integration evidence | S20 integration test results for the S17 → S19 chain | Confirms the chain behaves as designed for the candidate's config shape |
| Simulation summary | `PaperSimulationResult.summary` dict | Metrics, provenance, safety flags |
| Simulated trades | `PaperSimulationResult.trades` tuple | Per-trade entry/exit/P&L assumptions |
| Simulated equity curve | `PaperSimulationResult.equity_curve` tuple | Daily simulated equity and P&L |
| Simulated risk limit events | `PaperSimulationResult.risk_limit_events` tuple | Count and type of triggered limits |
| Reviewer notes | Recorded by the human reviewer | Free-text observations, caveats, and risk notes |

### Explicitly excluded inputs

| Excluded input | Reason |
|---|---|
| Broker account state | Review is fully offline; no account connection |
| Live or real-time market data | Review works only from already-produced simulation outputs |
| Credentials of any kind | No broker/API/network access |
| Environment variables | No env var reads |
| A re-run of the simulation during review | Review inspects existing outputs only; does not trigger new simulation runs |

---

## 4. Manual Review Checklist

The reviewer must work through each item below and record a pass/fail/note
for each before recording an overall decision (§5). The checklist is
fail-closed: any unresolved or failing item should push the decision toward
`NEEDS_MORE_SIMULATION_DATA` or `REJECTED_SIMULATION_REVIEW` rather than
toward approval.

1. **Confirm config validation PASS** — `validate_paper_config(config_dict).result == "PASS"`.
2. **Confirm simulation result PASS** — `PaperSimulationResult.result == "PASS"`.
3. **Confirm all five safety flags are False** — `broker_calls_made`,
   `credentials_read`, `network_calls_made`, `order_action_requested`,
   `live_trading_allowed` must all be `False` on both the validation result
   and the simulation result.
4. **Confirm `candidate_id`/`run_id` match** — the config dict, the
   `PaperSimulationResult.summary`, and the evidence bundle (§6) must agree
   on `candidate_id` and `run_id`.
5. **Inspect `total_simulated_trades`** — is the trade count large enough to
   draw any tentative conclusion, or is it a near-empty sample?
6. **Inspect `total_return_pct`** — what is the cumulative simulated return
   over the window, and how does it compare to the cost of the assumptions
   used (slippage, commission)?
7. **Inspect `max_drawdown_pct`** — how severe is the worst simulated
   drawdown, and how does it compare with `max_drawdown_stop` from the
   config?
8. **Inspect `win_rate_pct`** — what fraction of simulated trades were
   profitable, and is the sample large enough for the figure to be
   meaningful?
9. **Inspect `risk_limit_events` count and types** — how many times did a
   risk limit halt a simulated entry, and which limits (`max_daily_loss`,
   `max_drawdown_stop`, `max_orders_per_day`, `min_cash_buffer`) fired most
   often?
10. **Inspect equity curve stability** — does `equity_curve` show a smooth,
    explainable progression, or large unexplained jumps, long flat stretches,
    or erratic day-to-day swings?
11. **Inspect simulated trade assumptions** — do `entry_price_assumption`,
    `exit_price_assumption`, `simulated_shares`, and `exit_reason` look
    internally consistent and plausible for the configured symbol/interval?
12. **Inspect slippage/commission assumptions** — are
    `slippage_bps_assumption` and `commission_bps_assumption` realistic for
    the instrument and order types being modelled, or do they understate real
    trading frictions?
13. **Inspect over-dependence on one trade or one day** — would removing the
    single best (or worst) simulated trade, or the single best (or worst)
    simulated day, change the overall conclusion materially?
14. **Inspect whether risk limits are too loose or too tight** — do the
    `risk_limit_events` suggest the configured limits
    (`max_daily_loss`, `max_drawdown_stop`, `max_orders_per_day`,
    `min_cash_buffer`) are calibrated sensibly, rarely triggered, or
    triggered so often that they dominate the simulated behaviour?
15. **Confirm no output implies paper/live approval** — re-confirm that
    nothing in the summary, trades, equity curve, or risk events implies, by
    naming or structure, that paper or live trading has been approved or
    that any order action is pending.

---

## 5. Review Decisions

The reviewer must record exactly one decision from the following vocabulary.
The default state for any simulation record that has not yet been reviewed is
`NOT_REVIEWED`.

| Decision | Meaning |
|----------|---------|
| `NOT_REVIEWED` | No manual review has been recorded yet (initial state) |
| `NEEDS_MORE_SIMULATION_DATA` | The existing simulation sample is too small, too narrow, or too dependent on a small number of trades/days to support any conclusion; more offline simulation runs are needed before a decision can be made |
| `REJECTED_SIMULATION_REVIEW` | The simulation outputs show unacceptable behaviour (e.g. excessive drawdown, dominant risk-limit activity, unrealistic assumptions, unstable equity curve) and the candidate should not proceed |
| `BLOCKED_SAFETY_REVIEW` | A safety flag was found `True` somewhere in the chain (config validation result or simulation result), or any other safety invariant from S17/S19/S20 appears violated; review cannot proceed until this is resolved by engineering, not by the reviewer |
| `BLOCKED_PROVENANCE` | `candidate_id`/`run_id`/`source_git_sha` do not match across the config, the simulation result, and the evidence bundle, or required upstream evidence (S14/S15/S16/S17/S19/S20) is missing or inconsistent |
| `APPROVED_FOR_PAPER_TRADING_DESIGN` | The reviewer has completed the full checklist (§4), found no disqualifying condition (§7), and judges the simulation evidence sufficient to justify *designing* (not implementing or running) a future paper trading architecture |

---

## 6. What `APPROVED_FOR_PAPER_TRADING_DESIGN` Authorises

`APPROVED_FOR_PAPER_TRADING_DESIGN` authorises **only** the creation of a
future docs-only paper trading architecture design document (S22). It does
**not**:

- Approve paper trading for any candidate, strategy, or symbol
- Approve live trading in any form
- Allow any broker, API, Alpaca, or network connection
- Allow any credential or environment variable access
- Allow any order submission, cancellation, or modification
- Cause, trigger, or imply any runtime, execution, or live-gate change
- Cause any broker, paper account, API key, credential, network, runtime,
  executor, or order-submission path to be created automatically
- Change any kill switch or fail-closed guard
- Constitute financial advice or a trading recommendation

No broker, paper account, API key, credential, network, runtime, executor, or
order path follows automatically from this decision. Every subsequent step —
including S22 itself — requires its own PR, its own review, and its own
explicit approval artifact. Live trading remains blocked throughout.

---

## 7. Required Evidence Bundle

A complete review record must reference the following pieces of evidence.
Missing or inconsistent evidence is itself a disqualifying condition (§8) and
should drive the decision toward `BLOCKED_PROVENANCE`.

| Evidence item | Source | Notes |
|---|---|---|
| `CandidatePromotionResult` | S14 | Must show `status == PAPER_CANDIDATE_ELIGIBLE` |
| Manual review decision | S15 | Must show decision `APPROVED_FOR_PAPER_CONFIG_DESIGN` |
| Config design reference | S16 | The "PC/1.0" schema design that the candidate's config follows |
| `PaperConfigValidationResult` | S17 | Must show `result == "PASS"` and all five safety flags `False` |
| `PaperSimulationResult` | S19 | Must show `result == "PASS"` and all five safety flags `False` |
| Integration test evidence | S20 | Confirms the S17 → S19 chain behaves correctly for configs of this shape |
| Reviewer checklist | This document, §4 | Completed pass/fail/note for every checklist item |
| Reviewer decision | This document, §5 | Exactly one decision from the vocabulary |
| Risk notes | Reviewer | Free-text caveats, concerns, and observations about risk-limit behaviour |
| Known limitations | Reviewer | Free-text record of sample-size constraints, assumption sensitivities, and anything that would change the conclusion if altered |

---

## 8. Disqualification Criteria

Any of the following disqualifies a candidate from receiving
`APPROVED_FOR_PAPER_TRADING_DESIGN` and should drive the decision toward
`REJECTED_SIMULATION_REVIEW`, `BLOCKED_SAFETY_REVIEW`,
`BLOCKED_PROVENANCE`, or `NEEDS_MORE_SIMULATION_DATA` as appropriate:

1. Simulation `result` is not `"PASS"` (i.e. `BLOCKED` or `ERROR`)
2. Any of the five safety flags is `True` anywhere in the chain
3. `candidate_id` or `run_id` mismatch between config, simulation result, and
   evidence bundle
4. Zero simulated trades (`total_simulated_trades == 0`) — no behavioural
   evidence to review
5. Unacceptable simulated drawdown relative to `max_drawdown_stop` or to any
   reasonable risk tolerance
6. Poor or unstable simulated equity curve (large unexplained jumps, long
   flat stretches, erratic swings)
7. Risk limit events dominate the simulated result (the simulation behaviour
   is mostly "limit fired, no entry" rather than genuine trade behaviour)
8. Unrealistic slippage or commission assumptions that would materially
   understate real trading frictions
9. The result is a one-trade or one-day artifact — removing a single trade or
   day would flip the conclusion
10. Missing reviewer evidence or missing/inconsistent provenance evidence
    from any of S14/S15/S16/S17/S19/S20
11. Any suggestion, in the outputs, the evidence bundle, or the reviewer's own
    notes, of direct paper or live order approval

---

## 9. Future S22 Plan

S22 will produce a **docs-only** paper trading architecture design — still no
implementation, still no approval to trade.

### 9.1 Scope of S22

| Item | In scope for S22 |
|------|-----------------|
| Define proposed paper trading architecture components | Yes |
| Define safety boundaries between components | Yes |
| Define what would still require separate approval after S22 | Yes |
| Implement any architecture component | **No** |
| Implement a paper trading runner | **No** |
| Connect to broker or Alpaca API | **No** |
| Access credentials or environment variables | **No** |
| Make network calls | **No** |
| Submit any order | **No** |
| Write any files | **No** |
| Modify any runtime or execution module | **No** |
| Approve live or paper trading | **No** |

### 9.2 What S22 will not grant

An S22 architecture design is not paper trading approval. Paper trading
implementation would still require, at minimum:

1. This review (S21) recording `APPROVED_FOR_PAPER_TRADING_DESIGN`
2. A paper trading architecture design (S22) with full gate review
3. A separate paper trading implementation PR with additional approval
4. An explicit paper trading approval artifact (new type, separate PR)
5. An observation period on the paper account
6. Evidence review before any live trading consideration

---

## 10. Safety Statement

This design document does not:

- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Create any real configuration file, simulation persistence layer, or
  paper trading runner.
- Implement any review workflow tooling, simulation persistence, paper
  trading runner, or live trading runner.
- Add any broker, API, Alpaca, credential, environment variable, or network
  access to the codebase.
- Submit, request, place, cancel, or modify any order.
- Enable automated execution, persistence, or promotion of any kind.
- Change the live gate status, kill switch, or any fail-closed guard.
- Grant any permission beyond allowing a future docs-only paper trading
  architecture design PR (S22).

Simulation `PASS` (S19) is an offline research finding only. It does not
approve paper or live trading. `APPROVED_FOR_PAPER_TRADING_DESIGN` (the
decision defined in this document) is a review classification only — it
authorises **only** a future docs-only architecture design PR, and no broker,
paper account, API key, credential, network, runtime, executor, or order path
follows automatically from it. No order action follows from any review
status defined here.

Paper and live trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by this document or by any review decision
described herein.

---

## 11. Relationship to S-Series

```
S14 (promotion evaluator)
  └─ S15 (manual review workflow)
       └─ S16 (paper config design)
            └─ S17 (config validator)
                 └─ S18 (paper simulation design)
                      └─ S19 (simulation implementation)
                           └─ S20 (simulation integration tests)
                                └─ S21 (results review workflow design)  ← this PR
                                     └─ S22 (paper trading architecture design, future)
                                          └─ future: paper trading implementation (separate approval)
```

Each step requires its own PR. No step automatically grants permission for
the next. Paper trading implementation requires an explicit approval artifact
of a new type, a separate PR, and an operator observation period. Live
trading remains blocked throughout the S-series.

---

*Nothing in this document is financial advice.*
*No position sizing, entry/exit timing, or trading decision is implied or approved.*
