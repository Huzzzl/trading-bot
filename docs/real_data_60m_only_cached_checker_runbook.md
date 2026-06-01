# 60m-Only Cached Real-Data Backtest Checker Runbook

Operator runbook for PR 10Z: run `cached_real_data_backtest_check` restricted
to 60m intervals (SPY/QQQ), inspect per-scenario diagnostic output, and
interpret results within the scope defined in PR 10Y.

**This runbook is for local operator use only.**
**Never run in CI. Never commit raw cache files.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**PASS means diagnostics ran successfully — not strategy, paper, or live approval.**

---

## 1. Prerequisites

- Python environment with dependencies installed (`pip install -r requirements.txt`)
- Cache files already populated (run `docs/local_yahoo_cache_fetch_runbook.md`
  first if `data/cache/` is empty)
- `data/cache/` is gitignored and must never be committed
- `output/` files are gitignored and must never be committed

---

## 2. Step 1 — Pre-check: confirm 60m cache files exist

Before running the backtest checker, verify that 60m cache files are present
for SPY and QQQ.

```bash
python -m src.tools.cached_data_availability_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 60m
```

```powershell
python -m src.tools.cached_data_availability_check `
    --cache-dir data/cache `
    --symbols SPY QQQ `
    --intervals 60m
```

Expected result: `PASS` with both SPY/60m and QQQ/60m files present.

If this returns `BLOCKED` or any file is missing, run the Yahoo cache fetch
first (see `docs/local_yahoo_cache_fetch_runbook.md`) before proceeding.

---

## 3. Step 2 — Run 60m-only cached checker

Run the backtest checker restricted to 60m intervals only. The `--output` flag
writes the result to a JSON file for offline inspection.

```bash
python -m src.tools.cached_real_data_backtest_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 60m \
    --output output/cached_real_data_backtest_check_60m_only.json
```

```powershell
python -m src.tools.cached_real_data_backtest_check `
    --cache-dir data/cache `
    --symbols SPY QQQ `
    --intervals 60m `
    --output output/cached_real_data_backtest_check_60m_only.json
```

**Note:** This command does not run 1d scenarios. Daily 1d remains excluded
until Phase 2 / Policy A resolves the `BacktestEngine.session_end` artifact.

### Expected high-level result

| Field | Expected value |
|-------|---------------|
| `result` | `PASS` |
| `availability_check_result` | `PASS` |
| `scenarios_run` | `2` |
| `broker_calls_made` | `False` |
| `credentials_read` | `False` |
| `network_calls_made` | `False` |
| `order_action_requested` | `False` |

**Stop if `result` is not `PASS`.** See Section 6 (Failure handling).

---

## 4. Step 3 — Inspect output

### 4.1 Print overall status

```bash
python -c "
import json, sys
d = json.load(open('output/cached_real_data_backtest_check_60m_only.json'))
print('result                  :', d['result'])
print('availability_check_result:', d['availability_check_result'])
print('scenarios_run           :', d['scenarios_run'])
print('broker_calls_made       :', d['broker_calls_made'])
print('credentials_read        :', d['credentials_read'])
print('network_calls_made      :', d['network_calls_made'])
print('order_action_requested  :', d['order_action_requested'])
"
```

```powershell
$d = Get-Content output/cached_real_data_backtest_check_60m_only.json | ConvertFrom-Json
Write-Host "result                  : $($d.result)"
Write-Host "availability_check_result: $($d.availability_check_result)"
Write-Host "scenarios_run           : $($d.scenarios_run)"
Write-Host "broker_calls_made       : $($d.broker_calls_made)"
Write-Host "credentials_read        : $($d.credentials_read)"
Write-Host "network_calls_made      : $($d.network_calls_made)"
Write-Host "order_action_requested  : $($d.order_action_requested)"
```

### 4.2 Print per-scenario performance metrics

```bash
python -c "
import json
d = json.load(open('output/cached_real_data_backtest_check_60m_only.json'))
for s in d['scenarios']:
    print(f\"{s['symbol']}/{s['interval']}  status={s['status']}\")
    for k in ('rows','num_trades','total_return_pct','annualized_return_pct',
              'max_drawdown_pct','sharpe_ratio','win_rate_pct','profit_factor',
              'avg_trade_return_pct','avg_win_pct','avg_loss_pct',
              'trades_per_100_bars','exposure_pct','exit_reason_counts'):
        print(f'  {k}: {s.get(k)}')
    print()
"
```

```powershell
$d = Get-Content output/cached_real_data_backtest_check_60m_only.json | ConvertFrom-Json
foreach ($s in $d.scenarios) {
    Write-Host "$($s.symbol)/$($s.interval)  status=$($s.status)"
    foreach ($k in @('rows','num_trades','total_return_pct','annualized_return_pct',
                     'max_drawdown_pct','sharpe_ratio','win_rate_pct','profit_factor',
                     'avg_trade_return_pct','avg_win_pct','avg_loss_pct',
                     'trades_per_100_bars','exposure_pct','exit_reason_counts')) {
        Write-Host "  ${k}: $($s.$k)"
    }
    Write-Host ""
}
```

### 4.3 Print Sharpe diagnostics

```bash
python -c "
import json
d = json.load(open('output/cached_real_data_backtest_check_60m_only.json'))
for s in d['scenarios']:
    print(f\"{s['symbol']}/{s['interval']}\")
    for k in ('sharpe_ratio','sharpe_diagnostic_result',
              'zero_std_detected','low_variance_warning','annualized_volatility'):
        print(f'  {k}: {s.get(k)}')
    print()
"
```

```powershell
$d = Get-Content output/cached_real_data_backtest_check_60m_only.json | ConvertFrom-Json
foreach ($s in $d.scenarios) {
    Write-Host "$($s.symbol)/$($s.interval)"
    foreach ($k in @('sharpe_ratio','sharpe_diagnostic_result',
                     'zero_std_detected','low_variance_warning','annualized_volatility')) {
        Write-Host "  ${k}: $($s.$k)"
    }
    Write-Host ""
}
```

### 4.4 Print trade diagnostics

```bash
python -c "
import json
d = json.load(open('output/cached_real_data_backtest_check_60m_only.json'))
for s in d['scenarios']:
    print(f\"{s['symbol']}/{s['interval']}\")
    for k in ('trade_diagnostic_result','trade_diagnostic_blocker',
              'entry_count','exit_count','unmatched_entries','unmatched_exits',
              'avg_holding_bars','median_holding_bars','min_holding_bars','max_holding_bars'):
        print(f'  {k}: {s.get(k)}')
    print()
"
```

```powershell
$d = Get-Content output/cached_real_data_backtest_check_60m_only.json | ConvertFrom-Json
foreach ($s in $d.scenarios) {
    Write-Host "$($s.symbol)/$($s.interval)"
    foreach ($k in @('trade_diagnostic_result','trade_diagnostic_blocker',
                     'entry_count','exit_count','unmatched_entries','unmatched_exits',
                     'avg_holding_bars','median_holding_bars',
                     'min_holding_bars','max_holding_bars')) {
        Write-Host "  ${k}: $($s.$k)"
    }
    Write-Host ""
}
```

---

## 5. Interpretation Rules

### 5.1 What PASS means

`result=PASS` (60m-only) means:
- Both 60m scenarios completed without error
- All safety flags are `False`
- Trade diagnostics passed

`result=PASS` does **not** mean:
- The strategy is profitable
- Parameters are optimized
- Paper or live trading is approved
- Out-of-sample validity is established

### 5.2 Metric interpretation constraints

All metrics are derived from cached historical data using fixed TrendFollowing
parameters. They characterize in-sample behavior only. No forward-looking
inference is authorized from this runbook.

| Metric | Constraint |
|--------|-----------|
| `sharpe_ratio` | Only meaningful if `sharpe_diagnostic_result=PASS`; treat as invalid if `zero_std_detected=True` |
| `win_rate_pct ≈ 50%` | Near-random for ~200 trades; requires statistical test (PR 11A scope) |
| `avg_trade_return_pct ≈ 0%` | Consistent with session_end-dominated 60m exits; not an edge signal |
| `exit_reason_counts` | Expected: `session_end` dominant (~86%); `stop_loss` present |
| `profit_factor` | `null` if no losing trades; values near 1.0 do not imply edge |

### 5.3 Daily 1d remains deferred

Running this runbook with `--intervals 60m` intentionally excludes 1d. Daily
1d metrics are not valid until Phase 2 / Policy A is implemented. Do not add
`1d` back to the `--intervals` flag unless Phase 2 has been completed.

### 5.4 No parameter optimization

The TrendFollowing parameters used by the checker are fixed characterization
parameters (`fast_ema_period=10`, `slow_ema_period=50`, etc.). Do not tune
parameters based on these results.

---

## 6. Failure Handling

| Symptom | Action |
|---------|--------|
| `availability_check_result=BLOCKED` or missing files | Run `docs/local_yahoo_cache_fetch_runbook.md` to populate cache, then retry |
| Any 60m scenario `status=BLOCKED` | Inspect `blocker` and `status` fields in the scenario dict; check `backtest_runner.py` guard logic |
| Any safety flag `True` (`broker_calls_made`, `credentials_read`, `network_calls_made`, `order_action_requested`) | **Stop immediately.** Do not proceed. Investigate the source of the unexpected call |
| `trade_diagnostic_result=BLOCKED` | Inspect `trade_diagnostic_blocker`; this does not block the scenario but the metric may be invalid |
| `sharpe_diagnostic_result=BLOCKED` | `zero_std_detected=True`; Sharpe is invalid for this scenario (no trades or flat equity curve) |
| `result=BLOCKED` | One or more scenarios failed; check each scenario's `status` and `blocker` field |
| `output/` file accidentally staged | Run `git reset HEAD output/` and `git restore --staged output/` before committing |
| `data/cache/` file accidentally staged | Run `git reset HEAD data/cache/` immediately; these files must never be committed |

---

## 7. Reference: known baseline values (PR 10T / PR 10X)

From the PR 10T and PR 10X snapshots (same cache files, same parameters):

| Field | SPY 60m | QQQ 60m |
|-------|---------|---------|
| `rows` | 3 341 | 3 341 |
| `num_trades` | 197 | 195 |
| `trades_per_100_bars` | 5.896 | 5.837 |
| `win_rate_pct` | 51.27% | 50.77% |
| `exit_reason_counts` | `session_end=170, stop_loss=26, end_of_backtest=1` | `session_end=174, stop_loss=20, end_of_backtest=1` |
| `trade_diagnostic_result` | `PASS` | `PASS` |

If the cache files have not been refreshed, results should match these values
exactly (deterministic backtest).

---

## 8. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed; `order_action_requested=False` |
| No Alpaca SDK | `cached_real_data_backtest_check` uses only cached files |
| No credentials | No API key required; `credentials_read=False` |
| No network calls | Checker is fully offline; `network_calls_made=False` |
| No order submission | `order_action_requested=False` in all outputs |
| No raw data committed | `data/cache/` gitignored; `output/` gitignored |

> **This runbook does not approve automated live trading.**
> **This runbook does not approve any individual trade.**
> **No Alpaca endpoint is contacted. No credentials are read.**
> **Nothing in this repository is financial advice.**
