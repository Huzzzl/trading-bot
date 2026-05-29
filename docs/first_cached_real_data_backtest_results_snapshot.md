# First Cached Yahoo Real-Data TrendFollowing Backtest Results Snapshot

Snapshot document for PR 10J: record the first operator-run results from the
full real-data pipeline (yahoo_cache_fetch → cached_data_availability_check →
cached_real_data_backtest_check) and define follow-up diagnostic PRs.

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

## 1. Pipeline Run Summary

The full three-step real-data pipeline was run locally by the operator on
2026-05-28 using the commands documented in `docs/local_yahoo_cache_fetch_runbook.md`.

### Step 1 — Yahoo cache fetch

```bash
python -m src.tools.yahoo_cache_fetch \
    --allow-network \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

**Result: PASS**

| Field | Value |
|-------|-------|
| `result` | `PASS` |
| `files_written` | `4` |
| `availability_check_result` | `PASS` |
| `network_calls_made` | `True` |
| `broker_calls_made` | `False` |
| `credentials_read` | `False` |
| `order_action_requested` | `False` |

### Step 2 — Cache availability check

```bash
python -m src.tools.cached_data_availability_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

**Result: PASS**

| Field | Value |
|-------|-------|
| `result` | `PASS` |
| `network_calls_made` | `False` |
| `broker_calls_made` | `False` |
| `credentials_read` | `False` |
| `order_action_requested` | `False` |

### Step 3 — Cached real-data backtest check

```bash
python -m src.tools.cached_real_data_backtest_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

**Result: PASS**

| Field | Value |
|-------|-------|
| `result` | `PASS` |
| `scenarios_run` | `4` |
| `availability_check_result` | `PASS` |
| `network_calls_made` | `False` |
| `broker_calls_made` | `False` |
| `credentials_read` | `False` |
| `order_action_requested` | `False` |

---

## 2. Scenario Metrics

TrendFollowing default params used:
`fast_ema_period=10, slow_ema_period=50, atr_period=14, atr_stop_mult=2.0,
volatility_lookback=50, breakout_lookback=5`

Initial capital: $100,000. Commission: $0.005/share. Slippage: $0.010/share.
Position size: 95%. Stop execution: bar_close. Force exit time: 15:55.

| Scenario | Rows | Total return % | Annualized % | Max drawdown % | Sharpe | Trades |
|----------|------|---------------|-------------|---------------|--------|--------|
| SPY 1d   | 1610 | −1.7641 | −0.2777 | −1.7641 | −163.3505 | 280 |
| SPY 60m  | 3341 | −0.6967 | −0.3641 | −4.6668 | −1.6661  | 197 |
| QQQ 1d   | 1610 | −1.9938 | −0.3141 | −1.9938 | −134.9166 | 266 |
| QQQ 60m  | 3341 | +0.3374 | +0.1759 | −7.2882 | −1.1458  | 195 |

---

## 3. Interpretation

### 3.1 Pipeline is working correctly

All three steps returned PASS with correct safety flags:
- `yahoo_cache_fetch` wrote 4 files to `data/cache/`; network was used only
  when `--allow-network` was explicitly passed.
- `cached_data_availability_check` confirmed all 4 files are present and valid
  offline (no network).
- `cached_real_data_backtest_check` ran all 4 backtest scenarios using the
  cached data (no network, no credentials, no broker calls).

The real-data infrastructure is functional end-to-end.

### 3.2 Strategy performance is not acceptable

All four scenarios produced negative or near-zero risk-adjusted returns.
The daily Sharpe ratios (−163.35 for SPY 1d, −134.92 for QQQ 1d) are
extreme outliers that indicate a calculation or annualisation bug rather than
genuine performance — see § 4.1.

The hourly Sharpe ratios are in a plausible range (−1.67 for SPY 60m,
−1.15 for QQQ 60m) but still negative, indicating the strategy currently
loses money net of friction under these default parameters.

### 3.3 Positive QQQ 60m total return does not approve trading

QQQ 60m shows a positive total return (+0.34%) and positive annualised return
(+0.18%). This single data point does not constitute strategy approval:
- It is a single symbol × interval × parameter set.
- The Sharpe ratio is still negative (−1.15).
- The max drawdown is −7.3% on a +0.34% gain.
- No out-of-sample validation has been performed.
- No walk-forward analysis has been run.

**This positive result does not approve paper trading or live trading.**

### 3.4 No change to any gate status

| Gate | Status | Change? |
|------|--------|---------|
| `live_trading_approved` | `false` | No |
| `live_order_submission_approved` | `false` | No |
| `paper_trading_enabled` | `false` | No |
| Paper trading gate | Fail-closed | No |
| Live trading gate | Fail-closed | No |

---

## 4. Diagnostic Plan

The metrics reveal two issues that must be diagnosed before any further
evaluation. No parameter optimisation or paper/live progression is planned
until diagnostics are complete.

### PR 10K — Sharpe calculation diagnostic (daily scenarios)

**Problem:** Daily Sharpe ratios are in the range −134 to −163. This is
implausible for any real backtest, even a losing one. Likely causes:

| Hypothesis | How to check |
|-----------|-------------|
| Annualisation denominator wrong for 1d interval | Inspect `bars_per_year_for_interval("1d")` and how it enters Sharpe formula |
| Excess return uses wrong risk-free rate | Check `metrics.py` risk-free rate assumption |
| Returns distribution has near-zero variance but many small negative returns | Inspect distribution of per-bar returns for 1d scenarios |
| `max_drawdown_pct` equals `total_return_pct` for 1d (SPY: −1.76 vs −1.76, QQQ: −1.99 vs −1.99) | Suggests equity curve never recovered — strategy went straight down; not a calculation bug but a signal quality bug |

**Scope:** Read-only diagnostic. Inspect `src/backtest/metrics.py` and emit
diagnostic output for 1d scenario returns. No `src/` changes until the
diagnosis is confirmed. If a bug is found, fix in a separate sub-PR.

**Not in scope:** Paper trading, live trading, parameter changes, new strategies.

### PR 10L — Trade summary diagnostics

**Problem:** 280 trades in 1610 daily bars ≈ 1 trade every 5.75 bars, which
is extremely high turnover for a trend-following strategy with `slow_ema_period=50`.
This suggests the strategy is frequently entering and exiting, likely whipsawing.

**Diagnostics to add:**

| Metric | Purpose |
|--------|---------|
| Average holding period (bars) | Detect excessive turnover |
| Entry reason breakdown (EMA crossover vs breakout) | Understand entry driver |
| Exit reason breakdown (stop-loss vs force-exit vs bearish signal) | Understand exit driver |
| Exposure % (bars in position / total bars) | Assess time in market |
| Win rate by exit reason | Identify dominant loss source |

**Scope:** Extend `BacktestRunResult.metrics` or add a `trade_summary` field.
No live or paper code changes. No strategy changes.

**Not in scope:** Parameter tuning, paper trading, live trading.

### PR 10M — Default params comparison

**Problem:** `cached_real_data_backtest_check.py` uses `fast_ema_period=10,
slow_ema_period=50` but the TrendFollowing strategy default is
`fast_ema_period=20, slow_ema_period=50`. The checker uses a non-default
fast period. This needs to be documented as intentional (aggressive signal
sensitivity) or aligned to strategy defaults.

**Scope:** Document the param choice rationale and optionally align
`_TREND_PARAMS` in the checker to the strategy defaults.
No live or paper code changes.

**Not in scope:** Parameter optimisation, paper trading, live trading.

---

## 5. What These Results Do and Do Not Mean

| This result MEANS | This result does NOT MEAN |
|-------------------|--------------------------|
| The real-data pipeline works end-to-end | The strategy is ready for paper trading |
| The backtest engine runs without errors on real data | The strategy produces acceptable risk-adjusted returns |
| 4 cache files were written and validated | Any individual trade is approved |
| Sharpe and return metrics can be computed | The Sharpe calculation is correct (see PR 10K) |
| QQQ 60m had positive total return in this single run | QQQ 60m is profitable or approvable |
| All safety flags remain False | Any gate status has changed |

---

## 6. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | `yahoo_cache_fetch` uses Yahoo Finance free API; no API keys |
| No order submission | `order_action_requested=False` in all three tool outputs |
| No broker calls | `broker_calls_made=False` in all three tool outputs |
| No raw data committed | `data/cache/` gitignored; no bar files added in this PR |
| No network in tests | This is a docs-only PR; `pytest` suite unchanged |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |

---

## 7. Validation for This Docs PR

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
