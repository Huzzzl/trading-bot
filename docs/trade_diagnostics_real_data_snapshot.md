# Trade Diagnostics Real-Data Snapshot

Snapshot document for PR 10T: record the first operator-run results from
`cached_real_data_backtest_check` after the PR 10R/PR 10S trade diagnostics
integration, capturing per-scenario `trade_summary_diagnostics` output.

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

## 1. Pipeline Run Summary

Run performed locally by the operator on 2026-05-30 using the cached bar files
written in the PR 10J run (still present in `data/cache/`). No new network
fetch was required.

```bash
python -m src.tools.cached_real_data_backtest_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

| Field | Value |
|-------|-------|
| `result` | `PASS` |
| `availability_check_result` | `PASS` |
| `scenarios_run` | `4` |
| `broker_calls_made` | `False` |
| `credentials_read` | `False` |
| `network_calls_made` | `False` |
| `order_action_requested` | `False` |

---

## 2. Scenario Results

TrendFollowing checker params:
`fast_ema_period=10, slow_ema_period=50, atr_period=14, atr_stop_mult=2.0,
volatility_lookback=50, breakout_lookback=5`

Initial capital: $100,000. Commission: $0.005/share. Slippage: $0.010/share.
Position size: 95%. Stop execution: bar_close. Force exit time: 15:55.

Performance metrics are unchanged from the PR 10J snapshot (same cache files,
same parameters). Trade diagnostics are the new output from PR 10S.

### 2.1 SPY 1d

| Field | Value |
|-------|-------|
| `rows` | 1610 |
| `num_trades` | 280 |
| `trade_diagnostic_result` | `PASS` |
| `trade_diagnostic_blocker` | `null` |
| `trades_per_100_bars` | 17.391304347826086 |
| `exposure_pct` | 17.391304347826086 |
| `avg_holding_bars` | 0.0 |
| `median_holding_bars` | 0.0 |
| `min_holding_bars` | 0.0 |
| `max_holding_bars` | 0.0 |
| `entry_count` | 280 |
| `exit_count` | 280 |
| `unmatched_entries` | 0 |
| `unmatched_exits` | 0 |
| `win_rate_pct` | 0.0 |
| `avg_trade_return_pct` | −0.006713405055507287 |
| `avg_win_pct` | `null` |
| `avg_loss_pct` | −0.006713405055507287 |
| `profit_factor` | 0.0 |
| `exit_reason_counts` | `session_end=279, end_of_backtest=1` |

### 2.2 SPY 60m

| Field | Value |
|-------|-------|
| `rows` | 3341 |
| `num_trades` | 197 |
| `trade_diagnostic_result` | `PASS` |
| `trade_diagnostic_blocker` | `null` |
| `trades_per_100_bars` | 5.896438192158036 |
| `exposure_pct` | 5.896438192158036 |
| `avg_holding_bars` | 4.284263959390863 |
| `median_holding_bars` | 5.0 |
| `min_holding_bars` | 0.0 |
| `max_holding_bars` | 6.0 |
| `entry_count` | 197 |
| `exit_count` | 197 |
| `unmatched_entries` | 0 |
| `unmatched_exits` | 0 |
| `win_rate_pct` | 51.26903553299492 |
| `avg_trade_return_pct` | −0.003144581915173595 |
| `avg_win_pct` | 0.2421947327406312 |
| `avg_loss_pct` | −0.2612619858759682 |
| `profit_factor` | 0.9703981035588297 |
| `exit_reason_counts` | `session_end=170, stop_loss=26, end_of_backtest=1` |

### 2.3 QQQ 1d

| Field | Value |
|-------|-------|
| `rows` | 1610 |
| `num_trades` | 266 |
| `trade_diagnostic_result` | `PASS` |
| `trade_diagnostic_blocker` | `null` |
| `trades_per_100_bars` | 16.52173913043478 |
| `exposure_pct` | 16.52173913043478 |
| `avg_holding_bars` | 0.0 |
| `median_holding_bars` | 0.0 |
| `min_holding_bars` | 0.0 |
| `max_holding_bars` | 0.0 |
| `entry_count` | 266 |
| `exit_count` | 266 |
| `unmatched_entries` | 0 |
| `unmatched_exits` | 0 |
| `win_rate_pct` | 0.0 |
| `avg_trade_return_pct` | −0.007993309774537568 |
| `avg_win_pct` | `null` |
| `avg_loss_pct` | −0.007993309774537568 |
| `profit_factor` | 0.0 |
| `exit_reason_counts` | `session_end=265, end_of_backtest=1` |

### 2.4 QQQ 60m

| Field | Value |
|-------|-------|
| `rows` | 3341 |
| `num_trades` | 195 |
| `trade_diagnostic_result` | `PASS` |
| `trade_diagnostic_blocker` | `null` |
| `trades_per_100_bars` | 5.836575875486381 |
| `exposure_pct` | 5.836575875486381 |
| `avg_holding_bars` | 4.430769230769231 |
| `median_holding_bars` | 6.0 |
| `min_holding_bars` | 0.0 |
| `max_holding_bars` | 6.0 |
| `entry_count` | 195 |
| `exit_count` | 195 |
| `unmatched_entries` | 0 |
| `unmatched_exits` | 0 |
| `win_rate_pct` | 50.76923076923077 |
| `avg_trade_return_pct` | 0.0028817271456299865 |
| `avg_win_pct` | 0.3206187407768007 |
| `avg_loss_pct` | −0.3247845681615148 |
| `profit_factor` | 1.0114712040193952 |
| `exit_reason_counts` | `session_end=174, stop_loss=20, end_of_backtest=1` |

---

## 3. Interpretation

### 3.1 Trade diagnostics are working correctly

All four scenarios returned `trade_diagnostic_result=PASS` with no blocker.
All safety flags are `False`. No raw prices or trade records were emitted.
The PR 10S integration is confirmed functional end-to-end.

### 3.2 Daily 1d scenarios are dominated by same-bar session_end exits

| Scenario | session_end | end_of_backtest | avg_holding_bars |
|----------|------------|-----------------|-----------------|
| SPY 1d | 279 / 280 | 1 / 280 | 0.0 |
| QQQ 1d | 265 / 266 | 1 / 266 | 0.0 |

`avg_holding_bars = 0.0` means every exit occurred on the same bar as the
entry (`exit_time == entry_time`). The `session_end` exit reason is triggered
when a position is carried overnight — i.e. opened but not closed within the
same session. For daily bars, the bar timestamp is `00:00 Eastern`, which is
before the `15:55` force-exit guard; the position therefore reaches `session_end`
at the next bar's open, recorded with the same bar timestamp.

This is an execution-model artifact of daily bars: the backtest engine's
`session_end` logic closes positions between bars, and because daily bar
timestamps are at midnight, `entry_time == exit_time` for every such trade.
The resulting P&L and Sharpe metrics for 1d scenarios should be treated as
unreliable until the session-end handling policy for daily bars is reviewed and
decided.

### 3.3 Daily 1d results explain the extreme Sharpe and low-variance warnings

The PR 10K/10L/10N Sharpe diagnostics flagged SPY/QQQ 1d as
`zero_std_detected=True` with |Sharpe| > 100 and `low_variance_warning=True`.
The trade diagnostics now explain why: all trades close on the same bar with
`holding = 0.0`, producing near-zero equity change per bar and near-zero return
standard deviation.

These extreme Sharpe values are a direct consequence of the execution-model
artifact described in § 3.2, not a property of the strategy.

### 3.4 60m scenarios have plausible trade structure

| Scenario | avg_holding_bars | min_hold | max_hold | Dominant exit |
|----------|-----------------|----------|----------|---------------|
| SPY 60m | 4.28 h | 0.0 h | 6.0 h | session_end (170 / 197) |
| QQQ 60m | 4.43 h | 0.0 h | 6.0 h | session_end (174 / 195) |

60m trades have non-zero holding periods (median 5–6 hours, max 6 hours ≈
one trading session). Stop-loss exits are present (26 for SPY 60m, 20 for
QQQ 60m). These results are structurally meaningful.

However, even the 60m scenarios are not approved for trading:
- `profit_factor < 1` for SPY 60m (0.97) — expected loss on average
- `profit_factor ≈ 1.01` for QQQ 60m — marginal; a single run does not validate
- No out-of-sample validation. No walk-forward analysis.
- `session_end` still dominates (86% of SPY 60m, 89% of QQQ 60m) — suggests the
  strategy rarely captures its full intended hold period

### 3.5 No change to any gate status

| Gate | Status | Change? |
|------|--------|---------|
| `live_trading_approved` | `false` | No |
| `live_order_submission_approved` | `false` | No |
| `paper_trading_enabled` | `false` | No |
| Paper trading gate | Fail-closed | No |
| Live trading gate | Fail-closed | No |

---

## 4. Next Diagnostic Plan

The same-bar session_end exit behavior in daily scenarios must be understood and
a policy decision made before any further evaluation of 1d backtest results.

### PR 10U — Daily-bar session-end handling policy design

**Status: implemented — `docs/daily_bar_session_end_policy_design.md`**

Design document covering four candidate policies (A: disable intraday
session_end/force_exit for daily bars; B: next-bar semantics; C: block daily
backtests when force_exit_time is configured; D: annotate results as invalid).
Recommended policy: Phase 1 — Policy C block guard (block 1d + force_exit_time
as invalid config); Phase 2 — Policy A disable (skip session_end/force_exit
checks for daily bars in engine). Acceptance criteria, next PRs, and safety
implications documented.

No behavior change in this docs PR.

### PR 10V — Daily-bar and force_exit/session_end characterization tests

**Status: implemented — `tests/test_daily_bar_session_end_behavior.py`**

Characterizes how the backtest engine processes daily bars: confirms
`entry_time == exit_time` pattern, confirms `force_exit` never fires for
midnight daily timestamps (since `00:00 < 15:55`), and locks in current
behavior before any change. 62 tests across 10 classes (daily bar timestamps,
force_exit string comparison, session_end dominance, same-bar exit artifact,
60m bar timestamps, 60m session_end at day boundaries only, 60m non-zero
holding, 1d vs 60m contrast, safety flags, source scan). No `src/` changes.

### PR 10W — Implement chosen policy (block guard + disable)

**Status: Phase 1 implemented**

**Scope:** `src/backtest/backtest_runner.py`, `tests/test_backtest_runner.py`,
`tests/test_daily_bar_session_end_behavior.py`, `tests/test_backtest_trade_schema.py`,
`tests/test_trendfollowing_offline_scenarios.py`, `tests/test_trendfollowing_param_comparison.py`

Phase 1 (implemented): fail-closed guard blocks `bar_interval in {"1d","1day","daily"}`
with `force_exit_time is not None`; raises `ValueError("invalid backtest run config")`.
`force_exit_time` type updated to `str | None`; `None` bypasses the guard via
sentinel `"23:59"` but does NOT fix the session_end artifact — daily 1d results
remain not valid for strategy performance until Phase 2 / Policy A.
`cached_real_data_backtest_check.py` unchanged; its 1d scenarios (still using
`force_exit_time="15:55"`) return `BLOCKED`. Three additional test files updated
because their synthetic 1d configs used `force_exit_time="15:55"`.
All 5 779 tests pass.

Phase 2 (deferred): disable `session_end`/`force_exit` checks in engine for daily
bars. Requires its own PR with tests confirming 60m behavior preserved.

### PR 10X — Rerun snapshot after PR 10W

**Scope:** `docs/daily_bar_policy_rerun_snapshot.md` (new, docs-only)

Operator rerun of `cached_real_data_backtest_check` after PR 10W confirms
daily 1d results are either valid or clearly BLOCKED. No metrics until then.

---

## 5. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | `cached_real_data_backtest_check` uses cached files; no API keys |
| No order submission | `order_action_requested=False` in all outputs |
| No broker calls | `broker_calls_made=False` in all outputs |
| No raw data committed | `data/cache/` gitignored; no bar files added in this PR |
| No network in tests | Docs-only PR; `pytest` suite unchanged |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |

---

## 6. Validation for This Docs PR

```bash
git diff origin/main...HEAD -- src tests config output scripts data
# Expected: empty
```

No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` files are
changed in this PR. The test suite is not run for docs-only PRs.

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
