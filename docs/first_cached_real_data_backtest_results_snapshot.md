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

**Status: implemented — `src/backtest/metrics_diagnostics.py`**

Added offline `diagnose_sharpe(equity_curve, interval)` helper
(`src/backtest/metrics_diagnostics.py`). Recomputes Sharpe using the same
formula as `compute_metrics()` and returns diagnostic flags:

| Flag | Meaning |
|------|---------|
| `zero_std_detected` | std of bar returns is 0 → BLOCKED (prevents misleading Sharpe) |
| `low_variance_warning` | std non-zero but below threshold → PASS but Sharpe may be inflated (calibrated in PR 10N) |
| `finite_values_only` | False → BLOCKED (NaN/inf in equity) |
| `sharpe_ratio_recomputed` | Recomputed value (or `None` if zero std) |
| `annualized_volatility` | `std × sqrt(bars_per_year)` |

Extreme daily Sharpe values (−134 to −163) are consistent with near-zero
std of daily bar returns — when the strategy is flat most bars, equity
changes little, std approaches zero, and the ratio explodes.
The diagnostic tool detects this condition and returns BLOCKED instead
of a misleading extreme value.

67 tests across 10 test classes (includes 7-test `TestDiagnosticVsProduction` class).
No strategy, engine, or execution changes.

**Critical scope boundary:**

> `diagnose_sharpe()` is a **read-only diagnostic helper** that runs
> independently of the backtest pipeline. It does NOT change:
> - `src/backtest/metrics.py` — `compute_metrics()` behaviour is **unchanged**.
>   A flat equity curve still returns `sharpe_ratio=0.0` (numeric, not BLOCKED).
> - `src/backtest/engine.py` — unchanged.
> - `src/tools/cached_real_data_backtest_check.py` — updated in PR 10L to call
>   `diagnose_sharpe()` per scenario, adding 5 diagnostic fields; the existing
>   `sharpe_ratio` field and all other scenario metrics are **unchanged**.
>
> **diagnostic BLOCKED ≠ backtest run BLOCKED.** The diagnostic's BLOCKED
> result only means "the Sharpe value would be misleading" — it does not
> affect `run_backtest()`, `compute_metrics()`, or any production path.
> The snapshot metrics in § 2 remain the authoritative record of what the
> pipeline returned.

**Not in scope:** Paper trading, live trading, parameter changes, new strategies.

### PR 10L — Sharpe diagnostics in cached_real_data_backtest_check

**Status: implemented — `src/tools/cached_real_data_backtest_check.py`**

After each successful `run_backtest()`, `run_check()` now calls
`diagnose_sharpe(result_bt.equity_curve, interval)` and appends 5 per-scenario
diagnostic fields:

| Field | Meaning |
|-------|---------|
| `sharpe_diagnostic_result` | `"PASS"` or `"BLOCKED"` (whether Sharpe is reliable) |
| `zero_std_detected` | `True` → near-zero variance; extreme Sharpe values explained |
| `low_variance_warning` | `True` → near-flat equity; Sharpe may still be inflated |
| `annualized_volatility` | `std × sqrt(bars_per_year)` (float or None) |
| `return_points` | count of bar-level returns computed (int) |

The daily SPY/QQQ scenarios from § 2 would now show `zero_std_detected=True`
and `sharpe_diagnostic_result="BLOCKED"`, making the source of the extreme
values (−163.35, −134.92) immediately visible in the output.

8 new tests (`TestSharpeDiagnostics`).
No strategy, engine, `metrics.py`, or execution changes.

**Not in scope:** Parameter tuning, paper trading, live trading.

### PR 10N — Calibrate Sharpe diagnostic low-vol threshold

**Status: implemented — `src/backtest/metrics_diagnostics.py`**

The first real-data run showed SPY 1d (annualized_vol = 0.000323) and QQQ 1d
(annualized_vol = 0.000394) diagnostic results of PASS without `low_variance_warning`,
despite |Sharpe| > 100. The per-bar std (≈ 2e-5) was above the old 1e-6 threshold.

Added `_LOW_ANNUALIZED_VOL_THRESHOLD = 0.001` (0.1%). `low_variance_warning` now fires
when `annualized_volatility < 0.001`, in addition to the legacy per-bar std check.
SPY/QQQ 1d scenarios now correctly show `low_variance_warning=True` and remain PASS.
5 new tests (`TestAnnualizedVolThreshold`); 72 tests total in diagnostics file.
No strategy, engine, `metrics.py`, or `cached_real_data_backtest_check.py` changes.

**Not in scope:** Parameter tuning, paper trading, live trading.

### PR 10O — Calibrated diagnostics rerun snapshot (docs-only)

**Status: implemented — `docs/calibrated_sharpe_diagnostics_real_data_snapshot.md`**

Operator rerun of `cached_real_data_backtest_check` after PR 10N confirmed the
calibration works: SPY/QQQ 1d now show `low_variance_warning=True`; SPY/QQQ 60m
remain `low_variance_warning=False`. Performance metrics are unchanged from § 2
(same cache files, same parameters). No code changes.
See `docs/calibrated_sharpe_diagnostics_real_data_snapshot.md` for full details.

**Not in scope:** Parameter tuning, paper trading, live trading.

### PR 10M — Default params comparison

**Status: implemented — `tests/test_trendfollowing_param_comparison.py`**

The checker `_TREND_PARAMS` uses `fast_ema_period=10` intentionally for broader
signal characterization during offline backtest runs; the TrendFollowing strategy
default (`TrendFollowing()` with no args) is `fast_ema_period=20`. All other
params match: `slow_ema_period=50, atr_period=14, atr_stop_mult=2.0,
volatility_lookback=50, breakout_lookback=5`. The checker uses the correct key
names (`fast_ema_period`, `slow_ema_period`) — no obsolete `ema_fast`/`ema_slow`
keys. Both param sets are accepted by `TrendFollowing()` without error.

29 tests across 5 classes (`TestStrategyDefaultParams`, `TestCheckerParams`,
`TestSharedDefaults`, `TestParamDivergence`, `TestSyntheticComparison`).
No strategy, engine, `metrics.py`, `cached_real_data_backtest_check.py`,
execution, broker, or config changes. No parameter optimization performed.
A future PR may evaluate parameter policy; this PR only compares and locks
in the current behavior.

**Not in scope:** Parameter optimisation, paper trading, live trading.

### PR 10P — Trade summary diagnostics design

**Status: implemented — `docs/trade_summary_diagnostics_design.md`**

Docs-only design document. Problem: the first real-data run showed high
trade counts (280 SPY 1d, 266 QQQ 1d, 197 SPY 60m, 195 QQQ 60m) suggesting
possible whipsawing or stop-loss cycling. Design defines aggregate diagnostic
fields (`trade_count`, `trades_per_100_bars`, `avg_holding_bars`,
`median_holding_bars`, `min/max_holding_bars`, `exposure_pct`, `entry_count`,
`exit_count`, `unmatched_entries/exits`, `win_rate`, `avg_trade_return`,
`avg_win`, `avg_loss`, `profit_factor`, `exit_reason_counts`), documents
the existing `Trade` schema and known `exit_reason` values, notes that
strategy EXIT signals are currently not acted on by the engine (exits only
via `stop_loss`/`force_exit`/`session_end`/`end_of_backtest`), and specifies
safety constraints for the diagnostic helper. Implementation plan: PR 10Q
(trade schema characterization tests), PR 10R (`trade_summary_diagnostics`
helper), PR 10S (integrate into checker), PR 10T (rerun snapshot). No
parameter optimization or paper/live approval.

**Not in scope:** Parameter optimisation, paper trading, live trading.

### PR 10Q — Trade schema characterization tests

**Status: implemented — `tests/test_backtest_trade_schema.py`**

Characterizes `Trade` fields and `BacktestRunResult.trades` schema using a
synthetic fixture (fast_ema_period=5, slow_ema_period=20, n=100 daily bars,
seed=42 → 13 closed LONG trades with exit_reason='session_end'). Locks in:
field names and types (symbol, entry_time, exit_time, entry_price, exit_price,
shares, commission, direction, exit_reason, pnl, meta), that pnl is computed
in `__post_init__` (not an init parameter), that meta is excluded from
`to_dict()`, and that all 5 known exit_reason values are in the documented
allowlist (`stop_loss`, `force_exit`, `session_end`, `end_of_backtest`,
`daily_loss_limit`). Source scan confirms no forbidden imports in trade.py or
backtest_runner.py. All safety flags remain False.

60 tests across 5 classes. No strategy, engine, metrics.py, or cached checker
changes. No parameter optimization or paper/live approval.

**Not in scope:** Parameter optimisation, paper trading, live trading.

### PR 10R — `trade_summary_diagnostics` helper

**Status: implemented — `src/backtest/trade_diagnostics.py`**

`trade_summary_diagnostics(trades, *, total_bars=None) -> dict` returns all
19 aggregate fields plus 4 safety flags. Pure offline; no raw prices in
output. Holding-period fields in approximate hours (0.0 for same-bar trades).
`exposure_pct` = conservative lower bound (1 bar/trade). `profit_factor=None`
when no strictly-losing trades. BLOCKED on non-finite numeric field,
`entry_price ≤ 0`, `shares ≤ 0`, or `exit_time < entry_time`.
Same-bar trades valid (holding = 0.0). Blocker strings contain no raw values.

78 tests across 10+ test classes. No strategy, engine, metrics.py,
or metrics_diagnostics.py changes.

**Not in scope:** Parameter optimisation, paper trading, live trading.

### PR 10S — Trade diagnostics in `cached_real_data_backtest_check`

**Status: implemented — `src/tools/cached_real_data_backtest_check.py`**

After each successful `run_backtest()`, calls
`trade_summary_diagnostics(result_bt.trades, total_bars=len(df))` and appends
18 per-scenario diagnostic fields: `trade_diagnostic_result`,
`trade_diagnostic_blocker`, `trades_per_100_bars`, `avg/median/min/max_holding_bars`,
`exposure_pct`, `entry_count`, `exit_count`, `unmatched_entries/exits`,
`win_rate_pct`, `avg_trade_return_pct`, `avg_win/loss_pct`, `profit_factor`,
`exit_reason_counts`. Diagnostic BLOCKED never blocks scenario status. Exception
in `trade_summary_diagnostics` → safe fallback dict with BLOCKED and Nones;
scenario status unaffected. No raw prices or trade records in output.

25 new tests across 8 classes (86 total in test file). No strategy, engine,
`metrics.py`, or `metrics_diagnostics.py` changes.

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
