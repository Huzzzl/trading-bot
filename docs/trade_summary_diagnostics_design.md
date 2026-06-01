# Trade Summary Diagnostics Design

Design document for PR 10P: define trade-summary diagnostics for
TrendFollowing real-data backtest characterization.

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

## 1. Motivation

The first cached real-data backtest run (PR 10J) reported unusually high trade
counts across all four scenarios:

| Scenario | Bars  | Trades | Trades / 100 bars |
|----------|-------|--------|------------------|
| SPY 1d   | 1 610 | 280    | 17.4             |
| QQQ 1d   | 1 610 | 266    | 16.5             |
| SPY 60m  | 3 341 | 197    | 5.9              |
| QQQ 60m  | 3 341 | 195    | 5.8              |

A rate of 17 trades per 100 daily bars (≈ 17 trades per 100 trading days, or
roughly one round-trip every 5–6 days) suggests potential whipsawing or rapid
stop-loss cycling. Without trade-level breakdown it is not possible to
distinguish:

- Productive short trades (tight stops catching real reversals quickly)
- Whipsawing (entering, immediately stopping out, re-entering)
- Configuration-driven churn (checker's `fast_ema_period=10` vs strategy
  default `fast_ema_period=20`, documented in PR 10M)

No parameter changes and no optimization are planned until these diagnostics
are complete and reviewed.

---

## 2. Current Trade Schema

`BacktestRunResult.trades` is a `list[Trade]` where each `Trade`
(`src/backtest/trade.py`) has:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | `str` | Ticker symbol |
| `entry_time` | `pd.Timestamp` | Bar timestamp of entry |
| `exit_time` | `pd.Timestamp` | Bar timestamp of exit |
| `entry_price` | `float` | Fill price at entry (post-slippage) |
| `exit_price` | `float` | Fill price at exit (post-slippage) |
| `shares` | `float` | Shares traded |
| `commission` | `float` | Total commission (entry + exit) |
| `direction` | `str` | `"LONG"` or `"SHORT"` |
| `exit_reason` | `str` | Reason code from the engine |
| `pnl` | `float` | Net PnL after commission |
| `meta` | `dict` | Arbitrary strategy metadata |

### 2.1 Known exit_reason values

The engine and risk manager produce the following `exit_reason` strings:

| Value | Source | Description |
|-------|--------|-------------|
| `"stop_loss"` | `RiskManager.check_exits` | ATR-based stop-loss triggered (bar low ≤ stop price) |
| `"force_exit"` | `RiskManager.check_exits` | Force-exit time (15:55 Eastern) reached |
| `"session_end"` | `BacktestEngine._process_bar` | Position carried overnight — closed at prior session close |
| `"end_of_backtest"` | `BacktestEngine._close_all_open_positions` | Position still open at final bar |
| `"daily_loss_limit"` | `RiskManager.check_exits` | Daily loss limit breached (if configured) |

### 2.2 Engine note: strategy EXIT signals

The TrendFollowing strategy generates `SignalDirection.EXIT` signals when:
1. `classify_trend` returns `trend == "bearish"`
2. `close < fast_ema`

**The current `BacktestEngine._process_bar` only handles `SignalDirection.LONG`
signals.** EXIT signals from the strategy are not acted on; the engine relies
entirely on `RiskManager.check_exits` for position closes. This means strategy
exit logic (bearish trend, fast-EMA cross-under) has no effect on the current
backtest results.

This is a design characteristic to document, not a bug to fix in this diagnostic
phase. The exit_reason breakdown will quantify how often each risk-manager exit
reason fires and whether `force_exit` or `stop_loss` dominates.

---

## 3. Proposed Diagnostic Fields

All fields are aggregate scalars or breakdowns derived from the trades list.
No raw trade timestamps, prices, or sizes are emitted in any output.

### 3.1 Volume and frequency

| Field | Formula | Notes |
|-------|---------|-------|
| `trade_count` | `len(trades)` | Total round-trip trades |
| `trades_per_100_bars` | `trade_count / total_bars * 100` | Normalised frequency |
| `exposure_pct` | `held_bars / total_bars * 100` | Fraction of bars with an open position |

### 3.2 Holding period (in bars)

Derived from `(exit_time − entry_time)` expressed in whole bars by matching
against the sorted bar index.

| Field | Formula |
|-------|---------|
| `avg_holding_bars` | Mean holding period across all trades |
| `median_holding_bars` | Median holding period |
| `min_holding_bars` | Shortest trade |
| `max_holding_bars` | Longest trade |

### 3.3 Trade structure

| Field | Notes |
|-------|-------|
| `entry_count` | Count of entry events (should equal `trade_count` for closed trades) |
| `exit_count` | Count of exit events (should equal `trade_count` for closed trades) |
| `unmatched_entries` | Entries with no matching exit (open at end of window) |
| `unmatched_exits` | Exits with no matching entry (should be 0) |

### 3.4 Return statistics

| Field | Notes |
|-------|-------|
| `win_rate_pct` | `wins / trade_count × 100` where win = `pnl > 0` |
| `avg_trade_return_pct` | Mean `pnl / (entry_price × shares) × 100` |
| `avg_win_pct` | Mean return for winning trades only |
| `avg_loss_pct` | Mean return for losing trades only |
| `profit_factor` | `sum(pnl_wins) / abs(sum(pnl_losses))`; `None` if no losses |

### 3.5 Exit reason breakdown

| Field | Notes |
|-------|-------|
| `exit_reason_counts` | `dict[str, int]` mapping each `exit_reason` to its count |

This directly answers whether trades are dominated by `stop_loss` (tight ATR
cycling), `force_exit` (held through EOD), or `session_end` (unexpected
overnight carry).

### 3.6 Completeness

All fields return deterministic, finite scalars. `None` is returned only
when a field is undefined (e.g. `profit_factor` with no losing trades, or
`avg_win_pct` with no winning trades).

---

## 4. Safety Constraints on the Diagnostic Helper

The planned `trade_summary_diagnostics(trades, bars)` helper (PR 10R) must satisfy
all of the following:

| Constraint | Requirement |
|-----------|-------------|
| No network | No `yfinance`, `requests`, `httpx`, `aiohttp`, `urllib` imports |
| No broker calls | No Alpaca SDK; `broker_calls_made=False` always |
| No credentials | No `os.environ` reads |
| No order actions | No `submit_order`, `cancel_order`, `replace_order` |
| No raw prices in output | Entry/exit prices are not emitted; aggregate stats only |
| No raw trade-by-trade data | Individual trade records are not echoed in any output |
| Pure/offline | Accepts a `list[Trade]` and a bar `pd.Index`; no I/O |
| Deterministic | Given the same inputs, always returns identical outputs |
| No strategy changes | Does not touch `TrendFollowing`, the backtest engine, or `metrics.py` |

---

## 5. Implementation Plan

### PR 10Q — Trade schema characterization tests

**Status: implemented — `tests/test_backtest_trade_schema.py`**

Inspect `Trade` fields and `BacktestRunResult.trades` schema using a synthetic
fixture (fast_ema_period=5, slow_ema_period=20, atr_period=5, seed=42, n=100
daily bars → 13 closed LONG trades with exit_reason='session_end').

60 tests across 5 classes:
- `TestTradeSchema` — field names, types, pnl formula, meta default_factory
- `TestToDictKeys` — to_dict() exact key set; meta excluded; pnl matches attribute
- `TestExitReasonValues` — allowlist of 5 exit_reason strings; fixture produces session_end
- `TestBacktestRunResultTradesField` — trades is list[Trade]; all safety flags; determinism
- `TestSafetySourceScan` — source scan of trade.py and backtest_runner.py; no forbidden imports

No `src/` changes. No strategy, engine, metrics.py, or cached checker changes.

### PR 10R — `trade_summary_diagnostics` helper

**Status: implemented — `src/backtest/trade_diagnostics.py`**

`trade_summary_diagnostics(trades, *, total_bars=None) -> dict[str, Any]`
returns all 19 aggregate fields defined in § 3 plus 4 safety flags. Pure
offline function: no network, no broker, no credentials, no raw prices in
output.

Holding-period fields (`avg/median/min/max_holding_bars`) are expressed in
approximate hours (`(exit_time − entry_time).total_seconds() / 3600`); for
same-bar trades (entry_time == exit_time) this is 0.0. `exposure_pct` is a
conservative lower bound: each trade counted as 1 bar (`trade_count /
total_bars × 100`). `profit_factor = None` when no strictly-losing trades
(denominator would be 0). Empty `trades` → PASS with zeroes and Nones.
Non-finite numeric field → BLOCKED. `entry_price ≤ 0` → BLOCKED.
`shares ≤ 0` → BLOCKED. `exit_time < entry_time` → BLOCKED.
Same-bar trades (`exit_time == entry_time`) are valid: holding = 0.0.
All blocker strings are safe fixed descriptions; no raw trade values echoed.

78 tests across 10+ classes (`TestEmptyTrades`, `TestSingleWinningTrade`,
`TestSingleLosingTrade`, `TestMixedTrades`, `TestProfitFactor`,
`TestExitReasonCounts`, `TestTotalBarsParameter`, `TestBlockedOnInvalidTrades`,
`TestSafetyFlags`, `TestDeterminism`, `TestNoRawDataInOutput`,
`TestHoldingPeriod`, `TestSafetySourceScan`). No strategy/engine/metrics
changes.

### PR 10S — Integrate trade diagnostics into `cached_real_data_backtest_check`

**Status: implemented — `src/tools/cached_real_data_backtest_check.py`**

After each successful `run_backtest()`, calls
`trade_summary_diagnostics(result_bt.trades, total_bars=len(df))` and appends
18 per-scenario fields: `trade_diagnostic_result`, `trade_diagnostic_blocker`,
`trades_per_100_bars`, `avg/median/min/max_holding_bars`, `exposure_pct`,
`entry_count`, `exit_count`, `unmatched_entries/exits`, `win_rate_pct`,
`avg_trade_return_pct`, `avg_win/loss_pct`, `profit_factor`,
`exit_reason_counts`. Diagnostic BLOCKED never blocks scenario status or overall
result. Exception → safe fallback BLOCKED dict with Nones; scenario unaffected.
No raw prices or individual trade records in output. `_ALLOWED_SCENARIO_KEYS`
updated; `test_each_scenario_has_required_keys` updated.

25 new tests across 8 classes (86 total in checker test file). No strategy,
engine, `metrics.py`, `metrics_diagnostics.py`, or `trade_diagnostics.py`
changes.

### PR 10T — Operator rerun snapshot with trade summary diagnostics

**Status: implemented — `docs/trade_diagnostics_real_data_snapshot.md`**

Docs-only. Operator rerun of `cached_real_data_backtest_check` after PR 10S
confirmed the trade diagnostics integration works end-to-end. All four scenarios
returned `trade_diagnostic_result=PASS`. Key findings: daily 1d scenarios are
dominated by same-bar `session_end` exits (`avg_holding_bars=0.0`), which
explains the 0% win rate and extreme Sharpe values. 60m scenarios have
plausible holding periods (median 5–6 h, stop_loss exits present). No gate
status changes. Next diagnostic plan: PR 10U (daily-bar session_end policy
design), PR 10V (characterization tests), PR 10W (policy decision + fix).

### PR 10U — Daily-bar session-end handling policy design

**Status: implemented — `docs/daily_bar_session_end_policy_design.md`**

Docs-only. Covers four candidate policies and recommends Phase 1 (Policy C:
block `bar_interval=1d` + `force_exit_time` as invalid config) then Phase 2
(Policy A: disable `session_end`/`force_exit` checks in engine for daily bars).
Documents acceptance criteria, safety implications, and next PR chain
(PR 10V → 10W → 10X).

### PR 10W — Phase 1 Policy C block guard

**Status: Phase 1 implemented — `src/backtest/backtest_runner.py`**

Fail-closed guard: `bar_interval in {"1d","1day","daily"}` + `force_exit_time is
not None` raises `ValueError("invalid backtest run config")`. `force_exit_time`
updated to `str | None`; `None` bypasses the guard via sentinel `"23:59"` but
does NOT fix session_end behavior — daily 1d metrics remain not valid for
strategy performance until Phase 2 / Policy A. Checker unchanged; its 1d
scenarios (still using `force_exit_time="15:55"`) return `BLOCKED`.
All 5 780 tests pass.

---

## 6. What This Design Does and Does Not Authorise

| This design AUTHORISES | This design does NOT authorise |
|------------------------|--------------------------------|
| Implementing the diagnostic helper (PR 10R) | Parameter changes to TrendFollowing |
| Adding characterization tests (PR 10Q) | Optimization of any parameter |
| Integrating diagnostics into the checker (PR 10S) | Paper trading |
| Documenting aggregate trade statistics | Live trading |
| Identifying the dominant exit reason | Adding live EXIT signal handling to BacktestEngine |
| Describing the exit-signal gap (§ 2.2) | Removing or changing the force_exit guard |
| Recording a snapshot after PR 10S (PR 10T) | Any strategy behavior change |

---

## 7. Validation for This Docs PR

```bash
git diff origin/main...HEAD -- src tests config output scripts data
# Expected: empty
```

No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` files are
changed in this PR. The test suite is not run for docs-only PRs.

---

## 8. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this docs PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | No `src/` changes |
| No order submission | No `src/` changes |
| No broker calls | No `src/` changes |
| No raw data committed | `data/cache/` gitignored; no bar files added |
| No network in tests | pytest not run for this docs PR |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
