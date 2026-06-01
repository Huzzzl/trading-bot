# Real-Data 60m-Only Evaluation Scope Design

Design document for PR 10Y: define the short-term real-data evaluation scope
after PR 10W/10X, restricting analysis to SPY/QQQ 60m scenarios while daily 1d
remains invalid pending Phase 2 / Policy A.

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

## 1. Context

After PR 10W Phase 1 and the PR 10X snapshot:

| Scenario | Status | Reason |
|----------|--------|--------|
| SPY 1d | `BLOCKED` | Phase 1 guard: `force_exit_time="15:55"` + `bar_interval=1d` invalid |
| SPY 60m | `OK` | Unaffected by guard |
| QQQ 1d | `BLOCKED` | Phase 1 guard: same reason |
| QQQ 60m | `OK` | Unaffected by guard |

`availability_check_result=PASS` (cache files exist for all four scenarios).
Overall `result=BLOCKED` because two scenarios are blocked, but the 60m
scenarios continue to run and produce meaningful per-scenario output.

The 60m results are structurally unchanged from PR 10T:

| Scenario | rows | num_trades | trades_per_100_bars | win_rate_pct |
|----------|------|-----------|---------------------|-------------|
| SPY 60m | 3 341 | 197 | 5.896 | 51.27% |
| QQQ 60m | 3 341 | 195 | 5.837 | 50.77% |

---

## 2. Problem Statement

The current checker `result=BLOCKED` is accurate but operationally inconvenient:
every run with the default `--intervals 1d 60m` flag is `BLOCKED` due to the
1d guard, even though the 60m scenarios are fully functional. Operators who want
to evaluate 60m performance must either know to filter mentally or await a mode
change.

This design defines:
1. The **authorized evaluation scope**: 60m only until Phase 2 is implemented
2. The **metrics to evaluate** and their interpretation constraints
3. The **acceptance gates** for diagnostic outputs (not for trading decisions)
4. The **future PR chain** to formalize 60m-only operation and progress toward
   valid daily 1d evaluation

---

## 3. Authorized Short-Term Evaluation Scope

### 3.1 In scope

| Item | Detail |
|------|--------|
| Symbols | SPY, QQQ |
| Interval | 60m only |
| Checker command | `python -m src.tools.cached_real_data_backtest_check --cache-dir data/cache --symbols SPY QQQ --intervals 60m` |
| Cache source | Existing `data/cache/` files from PR 10J fetch |
| Evaluation type | Diagnostic / characterization only — not trading decisions |

### 3.2 Out of scope

| Item | Reason |
|------|--------|
| Daily 1d interval | Phase 1 guard blocks it; same-bar artifact persists until Phase 2 |
| Parameter optimisation | No grid search, no best-params selection |
| Paper trading approval | Requires separate safety gate |
| Live trading approval | Requires separate safety gate |
| Strategy changes | No changes to `TrendFollowing` or engine logic in this scope |
| `force_exit_time=None` 1d bypass | Characterization only; same-bar artifact remains |

---

## 4. Metrics to Evaluate

The following fields from the 60m `cached_real_data_backtest_check` output are
authorized for evaluation. All are read from the checker's per-scenario dict;
none are derived from raw prices or individual trade records.

| Metric | Field name | Interpretation constraint |
|--------|-----------|--------------------------|
| Total return | `total_return_pct` | Backtest-only; no live performance implied |
| Max drawdown | `max_drawdown_pct` | Historical simulation; not a forward guarantee |
| Sharpe ratio | `sharpe_ratio` | Subject to `sharpe_diagnostic_result`; treat as BLOCKED if `zero_std_detected=True` |
| Sharpe diagnostic | `sharpe_diagnostic_result`, `zero_std_detected`, `low_variance_warning` | Must be checked before interpreting Sharpe |
| Trade count | `num_trades` | Absolute count; context-dependent |
| Trades per 100 bars | `trades_per_100_bars` | Activity rate; compare across symbols |
| Win rate | `win_rate_pct` | % of closed trades profitable |
| Profit factor | `profit_factor` | Gross profit / gross loss; `null` if no losing trades |
| Avg trade return | `avg_trade_return_pct` | Mean per-trade return including commission |
| Avg win / avg loss | `avg_win_pct`, `avg_loss_pct` | Reward/risk ratio per trade |
| Exit reason breakdown | `exit_reason_counts` | Understand driver of exits: session_end vs stop_loss vs end_of_backtest |
| Exposure | `exposure_pct` | Fraction of bars holding a position |
| Trade diagnostic | `trade_diagnostic_result`, `trade_diagnostic_blocker` | Must be PASS for metrics to be interpreted |

### 4.1 Warning: backtest metrics are not performance forecasts

All metrics above are computed on cached historical data using a deterministic
TrendFollowing parameter set. They characterize the strategy's in-sample
behavior during the period covered by the cache. They do not imply:

- Future profitability
- Out-of-sample validity
- Parameter robustness
- Suitability for live or paper trading

---

## 5. Acceptance Gates for Diagnostic Outputs

The following gates must all pass before any 60m metric output is considered
valid for diagnostic review. These gates are about output quality, not trading.

| Gate | Requirement |
|------|-------------|
| Cache availability | `availability_check_result=PASS` |
| 60m scenario status | All 60m scenarios: `status=OK` |
| Trade diagnostic | All 60m scenarios: `trade_diagnostic_result=PASS` |
| Sharpe diagnostic | `sharpe_diagnostic_result` is `PASS` or `BLOCKED` (BLOCKED only invalidates Sharpe, not other metrics) |
| Safety flags | `broker_calls_made=False`, `credentials_read=False`, `network_calls_made=False`, `order_action_requested=False` |
| No raw data | No `data/cache/` files committed; `data/cache/` is gitignored |
| No raw prices | Per-scenario dict must not contain `entry_price`, `exit_price`, or `trades` list |

If any gate fails, the output is not valid for diagnostic review and must be
investigated before proceeding.

---

## 6. Interpretation Constraints

### 6.1 60m session_end dominance

In the current 60m snapshot, `exit_reason_counts` shows:

| Scenario | session_end | stop_loss | end_of_backtest |
|----------|------------|-----------|----------------|
| SPY 60m | 170 / 197 | 26 / 197 | 1 / 197 |
| QQQ 60m | 174 / 195 | 20 / 195 | 1 / 195 |

`session_end` dominates (~86%). This is expected for 60m bars: `session_end`
fires when a position is held into the next trading day. The 60m timestamps are
intraday (09:30–15:30 Eastern); unlike daily bars, this does not produce the
same-bar exit artifact — positions genuinely span overnight and are closed at
the next day's open.

### 6.2 Near-zero avg_trade_return_pct

The `avg_trade_return_pct` values from PR 10T/10X are near zero (SPY:
−0.003%, QQQ: +0.003%). This is consistent with a high-frequency session_end
exit strategy applied to trend-following parameters tuned for longer holding
periods. It does not imply the strategy is unprofitable at longer timeframes.

### 6.3 Win rate around 50%

`win_rate_pct ≈ 51%` for SPY and QQQ 60m. This is near-random for 197 / 195
trades and cannot be used to infer edge without statistical testing (PR 11A
scope).

---

## 7. Future PR Chain

| PR | Scope | Status |
|----|-------|--------|
| PR 10Y | This design — 60m-only evaluation scope | **This PR** |
| PR 10Z | 60m-only checker command wrapper or docs runbook, if needed | **Implemented** |
| PR 11A | 60m metrics threshold design (statistical gates, not trading) | Pending |
| PR 11B | 60m out-of-sample / walk-forward design | Pending |
| Phase 2 | Policy A: disable `session_end`/`force_exit` for daily bars in `BacktestEngine` | Pending (separate track) |

Phase 2 is a separate track from the 60m evaluation chain. Progress on 60m
evaluation does not unblock or depend on Phase 2, and vice versa.

---

## 8. What This Design Does and Does Not Authorise

| This design AUTHORISES | This design does NOT authorise |
|------------------------|-------------------------------|
| Evaluating SPY/QQQ 60m diagnostic metrics | Parameter optimisation |
| Defining acceptance gates for diagnostic outputs | Paper trading |
| Documenting the future 60m evaluation PR chain | Live trading |
| Running checker with `--intervals 60m` | Running checker with `--intervals 1d` |
| Interpreting trade diagnostic fields | Changing `TrendFollowing` parameters |
| Documenting metric interpretation constraints | Any engine or strategy code change |

---

## 9. Validation

```bash
git diff origin/main...HEAD -- src tests config output scripts data
# Expected: empty
```

No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` files are
changed in this PR. `pytest` not run for docs-only PRs.

---

## 10. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | No code changes; checker uses cached files |
| No order submission | `order_action_requested=False` in all outputs |
| No raw data committed | `data/cache/` is gitignored |
| Paper gate unchanged | Paper tools untouched |

> **This design does not approve automated live trading.**
> **This design does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> **This design does not constitute parameter optimization or trading approval.**
> The Phase A–H safety roadmap remains unchanged and required before any automation.
> Nothing in this repository is financial advice.
