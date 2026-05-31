# Daily-Bar Session-End Policy Design

Design document for PR 10U: define and select a policy for how the backtest
engine handles `session_end` and `force_exit` logic when the bar interval is
`1d` (daily). Motivated by the PR 10T finding that all daily backtest scenarios
produce same-bar exits with `avg_holding_bars=0.0`.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**No network requests are made by reading this document.**
**No raw market data files are committed in this document.**
**data/cache/ is gitignored; cache files are never committed.**

---

## 1. Problem Statement

### 1.1 PR 10T finding

The operator rerun documented in `docs/trade_diagnostics_real_data_snapshot.md`
showed that both daily scenarios are dominated by same-bar `session_end` exits:

| Scenario | trades | session_end | avg_holding_bars | win_rate_pct |
|----------|--------|------------|-----------------|-------------|
| SPY 1d | 280 | 279 / 280 (99.6%) | 0.0 | 0.0% |
| QQQ 1d | 266 | 265 / 266 (99.6%) | 0.0 | 0.0% |

Every trade enters and exits on the same bar (`entry_time == exit_time`),
yielding a degenerate equity curve with near-zero variance. This produces
nonsensical performance metrics (0% win rate, |Sharpe| > 100) and cannot
represent meaningful strategy evaluation.

### 1.2 Root cause: daily bar timestamps and the session_end / force_exit interaction

Daily bars produced by `pd.bdate_range` carry timestamps at `00:00:00 Eastern`.
The backtest engine applies two intraday close mechanisms:

1. **`force_exit`** — closes any open position when the bar timestamp is at or
   after `force_exit_time` (default `15:55 Eastern`). For daily bars at
   `00:00 Eastern`, the condition `00:00 ≥ 15:55` is **never true**, so
   `force_exit` never fires on daily bars.

2. **`session_end`** — closes any position that remains open when a new
   trading session begins (i.e. the current bar's date differs from the
   previous bar's date). For daily bars, every consecutive bar pair is on a
   different date, so **every position carried past bar close triggers
   `session_end`** at the very next bar.

Combined effect: a position opened on bar _N_ (at `00:00 Day N`) is
immediately closed at bar _N+1_ (at `00:00 Day N+1`) via `session_end`. The
engine records `entry_time = 00:00 Day N` and `exit_time = 00:00 Day N+1`, but
because `trade_summary_diagnostics` computes holding as
`(exit_time − entry_time).total_seconds() / 3600`, this is 24 hours, not 0.

**Re-examination after close reading of `_process_bar`:** The actual
`entry_time` and `exit_time` stored on the `Trade` object both equal the
timestamp of the bar at which the position closes (the exit bar), not the entry
bar. This means for a position entered on bar _N_ and closed on bar _N+1_, both
times are `00:00 Day N+1`, yielding `holding = 0.0`.

The `session_end` exit fires at the **start of the new session bar**, recording
the new bar's timestamp as both the trade's `exit_time` and, retrospectively,
its apparent `entry_time` in the aggregated holding metric.

### 1.3 Why 60m scenarios are less affected

For 60m bars, consecutive bars within the same trading day share the same date,
so `session_end` fires only at the day boundary (first bar of a new day). A
position opened at 10:30 and still open at 14:30 does **not** trigger
`session_end` — only the transition from 14:30 to the next day's 09:30 does.
The strategy has 5–6 intraday bars per session to close via signal or
`force_exit` before the day boundary, so most 60m exits are intraday and carry
non-zero holding periods.

---

## 2. Current Behavior Summary

| Mechanism | Daily 1d bars | 60m bars |
|-----------|--------------|----------|
| `force_exit` fires | Never (`00:00 < 15:55` always) | Yes, when bar time ≥ 15:55 |
| `session_end` fires | Every bar (each day is a new session) | Only at day boundaries |
| Effect | Every open position closes each bar | Positions survive intraday |
| Holding period | Effectively 0 bars (same-bar artifact) | 0–6 bars (meaningful) |
| Valid for strategy eval | **No** — degenerate | **Yes** — structurally sound |

The current behavior is not a bug in the engine's logic per se — it is a
predictable consequence of applying intraday session semantics to a daily bar
series. The engine was designed for intraday use and does not distinguish
between bar intervals.

---

## 3. Candidate Policies

### Policy A — Disable intraday session_end and force_exit for daily bars

**Mechanism:** When `bar_interval` is `1d` (or any interval ≥ 1 day), skip the
`session_end` check between consecutive bars and skip the `force_exit` time
check entirely. Positions on daily bars are closed only by:
- ATR stop-loss (`stop_loss`)
- Strategy EXIT signal (currently not acted on — see `docs/trade_summary_diagnostics_design.md` § 2.2)
- End-of-backtest flush (`end_of_backtest`)

**Pros:**
- Eliminates the degenerate same-bar exit artifact cleanly
- Daily P&L reflects actual bar-to-bar price moves
- Holding periods become meaningful (whole number of trading days)

**Cons:**
- Positions that would realistically be forced out at day-end (e.g. margin calls,
  daily rebalancing) are not modelled
- Strategy EXIT signals are not yet implemented — without session_end, the only
  closes are stop_loss and end_of_backtest; the strategy could hold indefinitely
- Requires a clear definition of when "daily" applies (1d only? weekly? monthly?)
- Changes the backtest engine — requires its own tested PR

**Safety implication:** Fail-open for stop-loss (positions held longer). Risk
exposure is higher if stop-loss is wide. A position could theoretically be held
through the entire backtest window if stop never triggers and no EXIT signal fires.

---

### Policy B — Next-bar / end-of-session semantics for daily bars

**Mechanism:** For daily bars, treat each bar's close price as the session close.
A `session_end` exit is recorded at the **current bar's close**, not the next
bar's open. The `exit_time` is set to the current bar's timestamp plus an end-of-
session offset (e.g. `16:00 Eastern`), or the bar is marked as a daily close and
the position is carried to the next bar's open only when explicitly configured.

**Pros:**
- Preserves the concept of session-end exits for daily bars
- Exit price is the current bar's close (more realistic for EOD strategies)
- Holding period becomes `n × 1 day` instead of 0

**Cons:**
- Requires modifying `Trade` timestamp semantics — currently `exit_time` is
  always a bar index timestamp
- Downstream tools (`trade_summary_diagnostics`, Sharpe computation) use
  `exit_time - entry_time` directly; adjusting timestamps may break these
- More complex to implement correctly; higher risk of introducing subtle bugs

**Safety implication:** Neutral — effectively equivalent to today's behavior but
with corrected timestamps. No change to when or how often exits occur.

---

### Policy C — Block daily backtests when force_exit_time is configured

**Mechanism:** In `run_backtest()` or `cached_real_data_backtest_check`, detect
when `bar_interval=1d` and `force_exit_time` is set. Return `BLOCKED` with a
clear message: "force_exit_time is incompatible with daily bars; use 60m or
finer interval, or remove force_exit_time." Refuse to run the backtest silently.

**Pros:**
- Fail-closed: prevents silently misleading results
- No engine change required — validation at the config/tool layer
- Operator is forced to make an explicit choice before proceeding
- Easy to implement and test

**Cons:**
- Breaks the current `cached_real_data_backtest_check` pipeline for 1d intervals
  (requires removing 1d from defaults or adding an explicit bypass flag)
- Does not solve the problem — just refuses to run it
- Operator may simply remove `force_exit_time` without understanding why, leading
  to Policy A behavior without explicit intent

**Safety implication:** Most fail-closed of all policies. No risk of producing
misleading metrics silently.

---

### Policy D — Keep current behavior; mark daily results invalid in output

**Mechanism:** No engine change. After each scenario run, if `bar_interval=1d`
and `avg_holding_bars < threshold` (e.g. < 1.0), add a flag to the scenario dict:
`daily_session_end_artifact=True` and set `scenario_status=INVALID` (distinct
from BLOCKED and OK). Metrics are still computed and reported but labeled as
unreliable.

**Pros:**
- Zero engine change
- Results are still emitted (useful for debugging)
- Operator can see the artifact explicitly without it blocking the pipeline

**Cons:**
- Misleading metrics remain in the output
- Adds a third scenario status (`INVALID`) requiring updates to all downstream
  consumers
- Does not actually fix the problem — just annotates it
- Risk: operator may ignore the `INVALID` flag and act on the metrics

**Safety implication:** Weakest fail-closed behavior. The misleading metrics
remain; the annotation relies on the operator reading and respecting it.

---

## 4. Policy Comparison

| Criterion | A (disable) | B (next-bar) | C (block) | D (annotate) |
|-----------|------------|-------------|----------|-------------|
| Eliminates artifact | Yes | Yes | N/A (refuses) | No |
| Fail-closed | Partial | Partial | **Yes** | No |
| Engine change required | Yes | Yes | No | No |
| Test surface | Large | Large | Small | Small |
| 60m behavior unchanged | Yes | Yes | Yes | Yes |
| Operator must make explicit choice | No | No | **Yes** | No |
| Risk of silent misleading output | Low | Low | None | High |
| Implementation effort | Medium | High | Low | Low |

---

## 5. Recommended Policy

**Primary recommendation: Policy C (block) as the gate; Policy A (disable) as the implementation.**

### Phase 1 — PR 10W (immediate): Policy C

Add a validation guard in `BacktestRunConfig` or `run_backtest()`: if
`bar_interval` is `"1d"` (or any interval where all bar timestamps are at
midnight) and `force_exit_time` is set to a non-None value, raise a
`ValueError` with a clear message:

```
ValueError: force_exit_time='15:55' is not compatible with bar_interval='1d'.
Daily bars have midnight timestamps; the force_exit guard never fires and
session_end exits every position each bar. Either use a sub-daily interval
(e.g. '60m') or set force_exit_time=None to disable intraday session management.
```

This is the safest, lowest-risk change: no engine logic is modified, the error
is raised before any backtest runs, and the operator is forced to make an
explicit choice.

`cached_real_data_backtest_check` must be updated to either:
- Remove `force_exit_time` from the 1d config (Policy A semantics)
- Or exclude `1d` from the default interval list until Policy A is implemented

The checker test must also be updated.

### Phase 2 — PR 10W or later: Policy A

After the block guard is in place, implement Policy A: when `bar_interval` is
`1d` (or daily-equivalent), skip the `session_end` check and the `force_exit`
check in `BacktestEngine._process_bar`. Add explicit unit tests confirming:
- Daily positions are not closed by `session_end` between bars
- Daily positions are not closed by `force_exit` time check
- Daily positions close via `stop_loss`, `end_of_backtest`, or (future) EXIT signal
- 60m behavior is completely unchanged

This is a larger change with a higher test surface and therefore separated into
its own PR.

### Rationale for this recommendation

1. **Fail-closed first:** Policy C blocks silently wrong output immediately with
   zero engine risk. It is the most conservative entry point.

2. **Explicit operator intent:** forcing the operator to set `force_exit_time=None`
   for daily bars makes the semantics transparent — there is no ambiguity about
   whether the intraday guard is active.

3. **Policy A is the correct long-term fix:** once the block guard is in place,
   Policy A cleanly removes the artifact without complex timestamp surgery
   (Policy B) or leaving misleading output (Policy D).

4. **60m safety:** Policy A explicitly leaves 60m behavior unchanged; Policy B
   risks subtle timestamp regressions.

---

## 6. Acceptance Criteria for Implementation PR(s)

All of the following must hold after PR 10W merges:

| Criterion | Requirement |
|-----------|-------------|
| Daily 1d same-bar artifact | Eliminated or blocked — no silent `avg_holding_bars=0.0` |
| 60m behavior | Completely unchanged |
| ORB/backtest existing tests | All pass without modification |
| `cached_real_data_backtest_check` 1d output | Either valid performance metrics or clearly BLOCKED |
| No broker/API calls | `broker_calls_made=False` always |
| No credentials read | `credentials_read=False` always |
| No orders | `order_action_requested=False` always |
| No live/paper trading approved | Gate statuses unchanged |
| All existing tests pass | `pytest` full suite green |

---

## 7. Next PRs

| PR | Scope | Status |
|----|-------|--------|
| PR 10V | Characterization tests for current daily-bar session_end behavior | Pending |
| PR 10W | Implement chosen policy (Phase 1: block guard; Phase 2: disable) | Pending |
| PR 10X | Rerun `cached_real_data_backtest_check` after PR 10W; record snapshot | Pending |

PR 10V comes before PR 10W: tests must characterize current behavior first, so
that PR 10W tests can assert the behavior has changed in the expected direction
and that 60m behavior is preserved.

---

## 8. What This Design Does and Does Not Authorise

| This design AUTHORISES | This design does NOT authorise |
|------------------------|-------------------------------|
| Implementing a validation guard (Policy C) | Parameter optimisation |
| Implementing Policy A disable in the engine | Paper trading |
| Adding characterization tests (PR 10V) | Live trading |
| Running `cached_real_data_backtest_check` after fix (PR 10X) | Removing `force_exit` for 60m bars |
| Documenting the session_end artifact | Changing risk management logic |
| Blocking 1d + force_exit_time as invalid config | Any behavior change in this docs PR |

---

## 9. Validation for This Docs PR

```bash
git diff origin/main...HEAD -- src tests config output scripts data
# Expected: empty
```

No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` files are
changed in this PR. The test suite is not run for docs-only PRs.

---

## 10. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this docs PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | No `src/` changes |
| No order submission | No `src/` changes |
| No broker calls | No `src/` changes |
| No raw data committed | `data/cache/` gitignored; no bar files added |
| No network in tests | `pytest` not run for this docs PR |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
