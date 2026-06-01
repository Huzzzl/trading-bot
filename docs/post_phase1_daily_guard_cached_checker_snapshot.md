# Post-Phase-1 Daily Guard: Cached Checker Snapshot

Snapshot document for PR 10X: record the operator-run results from
`cached_real_data_backtest_check` after the PR 10W Phase 1 daily-bar guard,
confirming that 1d scenarios fail closed and 60m scenarios continue to run.

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

Run performed locally by the operator after PR 10W Phase 1 merged, using the
cached bar files written in the PR 10J run (still present in `data/cache/`).
No new network fetch was required.

```bash
python -m src.tools.cached_real_data_backtest_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

| Field | Value |
|-------|-------|
| `result` | `BLOCKED` |
| `availability_check_result` | `PASS` |
| `scenarios_run` | `2` |
| `broker_calls_made` | `False` |
| `credentials_read` | `False` |
| `network_calls_made` | `False` |
| `order_action_requested` | `False` |
| `blocker` | `one or more scenarios failed or had missing files: SPY/1d, QQQ/1d` |

---

## 2. Scenario Results

TrendFollowing checker params:
`fast_ema_period=10, slow_ema_period=50, atr_period=14, atr_stop_mult=2.0,
volatility_lookback=50, breakout_lookback=5`

Initial capital: $100,000. Commission: $0.005/share. Slippage: $0.010/share.
Position size: 95%. Stop execution: bar_close. Force exit time: `15:55`
(hardcoded in checker — this is the value that triggers the Phase 1 guard for
1d scenarios).

### 2.1 SPY 1d — BLOCKED

| Field | Value |
|-------|-------|
| `symbol` | `SPY` |
| `interval` | `1d` |
| `rows` | 1610 |
| `status` | `BLOCKED` |

No `num_trades`, performance metrics, or trade diagnostic fields are present.
The `run_backtest()` call raised `ValueError("invalid backtest run config")`
before the engine ran. The checker's try/except caught it and recorded
`status=BLOCKED`.

### 2.2 SPY 60m — OK

| Field | Value |
|-------|-------|
| `symbol` | `SPY` |
| `interval` | `60m` |
| `rows` | 3341 |
| `status` | `OK` |
| `num_trades` | 197 |
| `trade_diagnostic_result` | `PASS` |
| `trades_per_100_bars` | 5.896438192158036 |
| `win_rate_pct` | 51.26903553299492 |
| `exit_reason_counts` | `session_end=170, stop_loss=26, end_of_backtest=1` |

### 2.3 QQQ 1d — BLOCKED

| Field | Value |
|-------|-------|
| `symbol` | `QQQ` |
| `interval` | `1d` |
| `rows` | 1610 |
| `status` | `BLOCKED` |

No `num_trades`, performance metrics, or trade diagnostic fields are present.
Same cause as SPY 1d: Phase 1 guard rejected the 1d + `force_exit_time="15:55"`
configuration.

### 2.4 QQQ 60m — OK

| Field | Value |
|-------|-------|
| `symbol` | `QQQ` |
| `interval` | `60m` |
| `rows` | 3341 |
| `status` | `OK` |
| `num_trades` | 195 |
| `trade_diagnostic_result` | `PASS` |
| `trades_per_100_bars` | 5.836575875486381 |
| `win_rate_pct` | 50.76923076923077 |
| `exit_reason_counts` | `session_end=174, stop_loss=20, end_of_backtest=1` |

---

## 3. Interpretation

### 3.1 Phase 1 guard working as intended

The two 1d scenarios are `BLOCKED` because `cached_real_data_backtest_check`
hardcodes `force_exit_time="15:55"` for all intervals, and PR 10W Phase 1
rejects `bar_interval in {"1d","1day","daily"}` combined with a non-None
`force_exit_time`.

`availability_check_result=PASS` confirms the cache files exist for all four
scenarios (SPY/1d, SPY/60m, QQQ/1d, QQQ/60m). The `BLOCKED` overall result is
from the backtest config guard, not from missing data.

`scenarios_run=2` counts only `status=OK` scenarios (the two 60m runs); the
two 1d scenarios raised `ValueError` before execution.

### 3.2 60m scenarios unchanged

The 60m results are identical to the PR 10T snapshot:

| Scenario | num_trades | win_rate_pct | trades_per_100_bars |
|----------|-----------|-------------|---------------------|
| SPY 60m  | 197 | 51.27% | 5.896 |
| QQQ 60m  | 195 | 50.77% | 5.837 |

The Phase 1 guard has no effect on sub-daily intervals.

### 3.3 Daily 1d remains not valid for strategy performance

The guard prevents the prior misleading output (280 same-bar `session_end`
exits with `avg_holding_bars=0.0`, `win_rate=0%`). However, daily 1d is still
not valid for strategy evaluation:

- `force_exit_time=None` bypasses the Phase 1 guard but does not fix
  `BacktestEngine.session_end` behavior. Daily bars with `force_exit_time=None`
  still produce same-bar exits (characterized in `TestDailySameBarExitArtifact`
  and confirmed by `test_1d_force_exit_none_session_end_artifact_remains`).
- `force_exit_time=None` is for characterization only, not production use.
- Phase 2 / Policy A (disable `session_end`/`force_exit` checks for daily bars
  in `BacktestEngine`) is required before daily 1d results are meaningful.

### 3.4 No gate status changes

No paper trading, live trading, or parameter optimization is approved by this
snapshot. The Phase A–H safety roadmap is unchanged.

---

## 4. Comparison with PR 10T Snapshot

| Field | PR 10T (pre-guard) | PR 10X (post-guard) |
|-------|--------------------|---------------------|
| `result` | `PASS` | `BLOCKED` |
| `scenarios_run` | 4 | 2 |
| SPY 1d `status` | `OK` | `BLOCKED` |
| QQQ 1d `status` | `OK` | `BLOCKED` |
| SPY 60m `status` | `OK` | `OK` |
| QQQ 60m `status` | `OK` | `OK` |
| SPY 60m `num_trades` | 197 | 197 |
| QQQ 60m `num_trades` | 195 | 195 |
| SPY 60m `win_rate_pct` | 51.27% | 51.27% |
| QQQ 60m `win_rate_pct` | 50.77% | 50.77% |

The 60m metrics are byte-for-byte identical. The only change is that 1d
scenarios are now `BLOCKED` instead of `OK` with degenerate output.

---

## 5. Next PRs

| PR | Scope | Status |
|----|-------|--------|
| PR 10W | Phase 1 Policy C block guard | **Implemented** |
| PR 10X | This snapshot — post-Phase-1 checker rerun | **Implemented** |
| PR 10Y | 60m-only evaluation scope design | **Implemented** |
| PR 10Z | 60m-only checker command wrapper or docs runbook | Pending |
| PR 11A | 60m metrics threshold design | Pending |
| Phase 2 | Policy A: disable session_end / force_exit for daily bars in engine | Pending (separate track) |

---

## 6. Validation

```bash
git diff origin/main...HEAD -- src tests config output scripts data
# Expected: empty
```

No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` files are
changed in this PR. `pytest` not run for docs-only PRs.

---

## 7. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | `cached_real_data_backtest_check` uses cached files; no API keys |
| No order submission | `order_action_requested=False` in all outputs |
| No raw data committed | `data/cache/` is gitignored |
| Paper gate unchanged | Paper tools untouched |

> **This snapshot does not approve automated live trading.**
> **This snapshot does not approve any individual trade.**
> **No Alpaca endpoint was contacted. No credentials were read.**
> **Nothing in this repository is financial advice.**
