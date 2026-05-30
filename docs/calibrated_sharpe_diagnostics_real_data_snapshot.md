# Calibrated Sharpe Diagnostics — Real-Data Rerun Snapshot (PR 10O)

Snapshot document for PR 10O: record the operator rerun of
`cached_real_data_backtest_check` after the PR 10N low-volatility threshold
calibration (`_LOW_ANNUALIZED_VOL_THRESHOLD = 0.001`).

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**No network requests are made by reading this document.**
**No raw market data files are committed in this PR.**
**data/cache/ is gitignored; cache files are never committed.**

---

## 1. Rerun Summary

The cached backtest checker was rerun locally by the operator on 2026-05-30
using the commands documented in `docs/local_yahoo_cache_fetch_runbook.md`.
The same cache files written during the PR 10J run were used; no new network
fetch was performed.

```bash
python -m src.tools.cached_real_data_backtest_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

### Overall result

| Field | Value |
|-------|-------|
| `result` | `PASS` |
| `availability_check_result` | `PASS` |
| `scenarios_run` | `4` |
| `broker_calls_made` | `false` |
| `credentials_read` | `false` |
| `network_calls_made` | `false` |
| `order_action_requested` | `false` |

---

## 2. Per-Scenario Metrics and Diagnostics

TrendFollowing params unchanged from PR 10J:
`fast_ema_period=10, slow_ema_period=50, atr_period=14, atr_stop_mult=2.0,
volatility_lookback=50, breakout_lookback=5`

Initial capital: $100,000. Commission: $0.005/share. Slippage: $0.010/share.
Position size: 95%. Stop execution: bar_close. Force exit time: 15:55.

### Performance metrics (unchanged from PR 10J)

| Scenario | Rows | Total return % | Annualized % | Max drawdown % | Sharpe | Trades |
|----------|------|---------------|-------------|---------------|--------|--------|
| SPY 1d   | 1610 | −1.7641 | −0.2777 | −1.7641 | −163.3505 | 280 |
| SPY 60m  | 3341 | −0.6967 | −0.3641 | −4.6668 | −1.6661  | 197 |
| QQQ 1d   | 1610 | −1.9938 | −0.3141 | −1.9938 | −134.9166 | 266 |
| QQQ 60m  | 3341 | +0.3374 | +0.1759 | −7.2882 | −1.1458  | 195 |

### Sharpe diagnostic fields (new in PR 10L, calibrated in PR 10N)

| Scenario | `return_points` | `annualized_volatility` | `zero_std_detected` | `low_variance_warning` | `sharpe_diagnostic_result` |
|----------|-----------------|------------------------|---------------------|----------------------|--------------------------|
| SPY 1d   | 1609 | 0.00032315 | false | **true** | PASS |
| SPY 60m  | 3340 | 0.03160989 | false | false | PASS |
| QQQ 1d   | 1609 | 0.00039398 | false | **true** | PASS |
| QQQ 60m  | 3340 | 0.04155359 | false | false | PASS |

---

## 3. Interpretation

### 3.1 PR 10N calibration works correctly

The annualized-vol threshold (`_LOW_ANNUALIZED_VOL_THRESHOLD = 0.001`, i.e. 0.1%)
fires exactly as intended:

| Scenario | Ann_vol | 0.1% threshold | Warning fires? |
|----------|---------|----------------|----------------|
| SPY 1d   | 0.000323 (0.032%) | 0.001 (0.1%) | **Yes** |
| QQQ 1d   | 0.000394 (0.039%) | 0.001 (0.1%) | **Yes** |
| SPY 60m  | 0.031610 (3.161%) | 0.001 (0.1%) | No |
| QQQ 60m  | 0.041554 (4.155%) | 0.001 (0.1%) | No |

Before PR 10N, the daily scenarios produced `low_variance_warning=False` despite
|Sharpe| > 100. After PR 10N, they correctly produce `low_variance_warning=True`.

### 3.2 Extreme daily Sharpe values are now properly flagged

The extreme daily Sharpe values (SPY: −163.35, QQQ: −134.92) are now accompanied
by `low_variance_warning=True`, making the source of the anomaly immediately
visible in the output:

> The annualized volatility is 0.032–0.039%, which is far below the 0.1% threshold.
> With such low equity curve variance, the Sharpe magnitude is dominated by the
> sign of the tiny mean excess return, not genuine risk-adjusted performance.

The 60m Sharpe values (SPY: −1.67, QQQ: −1.15) are in a plausible range and
correctly produce `low_variance_warning=False`.

### 3.3 All safety flags remain False

No broker calls, no credentials, no network requests, no order actions in any
part of this diagnostic workflow.

### 3.4 No strategy performance improvement

The rerun uses the same cached data and same parameters as PR 10J. The
performance metrics are identical — the calibration PR only improved the
diagnostic visibility of extreme values, not the strategy itself:

- All four scenarios still show negative or marginal Sharpe ratios
- No out-of-sample validation has been performed
- No walk-forward analysis has been run
- QQQ 60m positive total return (+0.34%) remains a single-scenario observation
  and does not constitute strategy approval

### 3.5 No change to any gate status

| Gate | Status | Change? |
|------|--------|---------|
| `live_trading_approved` | `false` | No |
| `live_order_submission_approved` | `false` | No |
| `paper_trading_enabled` | `false` | No |
| Paper trading gate | Fail-closed | No |
| Live trading gate | Fail-closed | No |

---

## 4. What These Results Do and Do Not Mean

| This result MEANS | This result does NOT MEAN |
|-------------------|--------------------------|
| PR 10N threshold calibration fires on daily scenarios | The strategy is ready for paper trading |
| Extreme daily Sharpe values are now flagged with `low_variance_warning` | The strategy produces acceptable risk-adjusted returns |
| 60m scenarios are not flagged (appropriate, plausible volatility) | Any individual trade is approved |
| Diagnostic PASS with warning ≠ strategy approval | QQQ 60m is profitable or approvable |
| All safety flags remain False | Any gate status has changed |

---

## 5. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | Cached data used; no API keys; `--allow-network` not passed |
| No order submission | `order_action_requested=false` in output |
| No broker calls | `broker_calls_made=false` in output |
| No raw data committed | `data/cache/` gitignored; no bar files added in this PR |
| No network in tests | Docs-only PR; `pytest` suite not run for this PR |
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
