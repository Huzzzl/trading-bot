# TrendFollowing Offline Backtest Scenarios Design

Design document for PR 10B: define the offline backtest validation scenarios
for the TrendFollowing strategy against the ORB legacy/benchmark.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**No network requests are made in this docs PR.**
**A positive backtest result does not approve live trading.**
**This document plans the validation only — each sub-PR requires its own review.**

---

## 1. Scope and Goal

Validate the TrendFollowing strategy **offline** using simulated historical
bar data before any paper or live automation work begins.

**Goal:** Characterise strategy behaviour — not optimise it. The purpose is
to produce a reproducible, documented baseline of how TrendFollowing performs
on SPY and QQQ at 1d and 1h intervals relative to the ORB legacy benchmark.
No parameter sweep is planned in these scenarios.

**Explicitly out of scope:**
- No broker/API/credentials access of any kind
- No Alpaca SDK calls, no HTTP requests in the scenario runner
- No live or paper order execution
- No parameter optimisation or grid search
- No production deployment of any kind
- Automated trading remains disabled (fail-closed) throughout

---

## 2. Symbols and Intervals

### 2.1 Symbols

| Symbol | Role | Rationale |
|--------|------|-----------|
| SPY | Primary | Broadest US equity ETF; most liquid; benchmarked historically |
| QQQ | Secondary | Tech-weighted ETF; different regime sensitivity; required for config validation |

SPY runs **first** in all scenarios. QQQ runs after SPY baseline is reviewed.

### 2.2 Bar Intervals

| Interval | `bar_interval` config value | Notes |
|----------|---------------------------|-------|
| Daily | `"1d"` | Longest clean history; no intraday gap risk |
| Hourly | `"1h"` / `"60m"` | Requires `bars_per_year_for_interval("1h") = 252 × 6 = 1 512` |

**Yahoo Finance 1h / 60m retention limitation:**
Yahoo Finance retains intraday data (1m, 2m, 5m, 15m, 30m, 60m, 90m) for
only approximately **60 days** in the free API. This means:
- `"1d"` scenarios can use multi-year history (2020–present).
- `"1h"` / `"60m"` scenarios are limited to the most recent ~60 days of
  live data, which is insufficient for meaningful backtest statistics.
- **For the implementation sub-PR:** hourly scenarios must use either
  pre-downloaded CSV fixtures committed to the repo, or a synthetic data
  generator that produces deterministic bar sequences. Live `yfinance` fetches
  for hourly data are not suitable as a test default.

---

## 3. Strategies

### 3.1 TrendFollowing (MVP candidate)

Strategy: `src/strategy/trend_following.py`
Factory name: `"trend_following"`

Default parameters (used in baseline scenarios unless noted):

| Parameter | Default | Notes |
|-----------|---------|-------|
| `ema_fast` | 10 | Fast EMA period |
| `ema_slow` | 50 | Slow EMA period |
| `atr_period` | 14 | ATR lookback for stop sizing |
| `atr_stop_mult` | 2.0 | ATR multiplier for stop price |
| `volatility_lookback` | 50 | Bars for rolling volatility window |
| `breakout_lookback` | 5 | Bars for rolling high breakout reference |

Entry: trend `"bullish"` (EMA crossover via `classify_trend()`) AND close > prior
rolling high over `breakout_lookback` bars.
Exit: trend `"bearish"` OR close < fast EMA.
Stop metadata written to `Signal.meta["atr_stop_price"]`.

**Warm-up requirement:** at least `max(ema_slow, atr_period + volatility_lookback - 1,
breakout_lookback + 1)` bars before the first signal is possible. With defaults,
this is `max(50, 63, 6) = 63` bars. Daily scenarios must include at least 63
calendar trading days of warm-up before the evaluation window.

### 3.2 OpeningRangeBreakout (legacy / benchmark)

Strategy: `src/strategy/opening_range_breakout.py`
Factory name: `"opening_range_breakout"` (or `"orb"`)

The ORB strategy is designed for **intraday** bars (5m default). Its opening-range
logic (first 30 minutes, 09:30–10:00 Eastern) does not apply meaningfully to
daily bars. ORB comparison runs are limited to matching intraday data where
the bar interval is ≤ 30m. For 1d scenarios, ORB is omitted.

---

## 4. Scenarios

All scenarios use `run_backtest(BacktestRunConfig(...))` via `src/main.py` or
directly in a test fixture. No broker calls. No credentials.

### Scenario 1 — SPY 1d TrendFollowing baseline

| Field | Value |
|-------|-------|
| Symbol | `SPY` |
| Interval | `"1d"` |
| Strategy | `"trend_following"` (default params) |
| Date range | 2020-01-01 – 2024-12-31 (5 years; covers bull + bear + chop) |
| Initial capital | $100 000 |
| Commission | $0.005 / share |
| Slippage | $0.010 / share |
| Force exit time | n/a (daily bars; no intraday close) |

**Purpose:** Primary baseline. Establishes TrendFollowing behaviour on the most
common US equity proxy over a period that includes the 2020 COVID crash and
recovery, 2021–2022 bull/bear cycle, and 2023–2024 recovery.

### Scenario 2 — SPY 1h TrendFollowing baseline

| Field | Value |
|-------|-------|
| Symbol | `SPY` |
| Interval | `"1h"` / `"60m"` |
| Strategy | `"trend_following"` (default params) |
| Date range | Fixture data (see data note below) |
| Initial capital | $100 000 |
| Commission | $0.005 / share |
| Slippage | $0.010 / share |
| Force exit time | `"15:55"` Eastern |

**Data note:** Live yfinance 1h fetch is limited to ~60 days. For the
implementation PR, this scenario must use a pre-downloaded CSV fixture or a
synthetic deterministic bar sequence. The fixture must be committed to the
repo under `tests/fixtures/` or equivalent (not `config/` or `output/`).

**Purpose:** Validates TrendFollowing on intraday bars with the interval-aware
Sharpe annualisation (`bars_per_year = 1 512` for `"1h"`).

### Scenario 3 — QQQ 1d TrendFollowing baseline

| Field | Value |
|-------|-------|
| Symbol | `QQQ` |
| Interval | `"1d"` |
| Strategy | `"trend_following"` (default params) |
| Date range | 2020-01-01 – 2024-12-31 |
| Initial capital | $100 000 |
| Commission | $0.005 / share |
| Slippage | $0.010 / share |
| Force exit time | n/a |

**Purpose:** Secondary baseline. Checks strategy behaviour on a tech-weighted
ETF that diverges from SPY during sector rotations (2022 bear, 2023 AI rally).

### Scenario 4 — QQQ 1h TrendFollowing baseline

| Field | Value |
|-------|-------|
| Symbol | `QQQ` |
| Interval | `"1h"` / `"60m"` |
| Strategy | `"trend_following"` (default params) |
| Date range | Fixture data |
| Initial capital | $100 000 |
| Commission | $0.005 / share |
| Slippage | $0.010 / share |
| Force exit time | `"15:55"` Eastern |

**Data note:** Same 60-day retention limitation as Scenario 2. Requires fixture.

**Purpose:** Intraday TrendFollowing on QQQ; compare trade frequency and drawdown
vs. Scenario 2 (SPY 1h).

### Scenario 5 — ORB comparison on matching intraday data

| Field | Value |
|-------|-------|
| Symbol | `SPY` (primary) |
| Interval | `"5m"` or matching intraday interval |
| Strategy | `"opening_range_breakout"` |
| Date range | Same fixture window as Scenario 2 |
| Initial capital | $100 000 |
| Commission / slippage | Same as Scenarios 1–4 |

**Scope note:** ORB requires intraday bars where the opening-range window
(09:30–10:00 Eastern) is resolvable. If hourly bars are used, the first bar
covers 09:30–10:30, which spans the OR window — ORB performance on 1h bars
may be degenerate. The implementation PR should use 5m bars for ORB comparison
or document any limitation explicitly.

**Purpose:** Establishes the ORB legacy benchmark on the same data window used
by TrendFollowing Scenario 2. Enables direct metric comparison. A positive
TrendFollowing result vs. ORB does not constitute approval for any live trading.

---

## 5. Metrics

Each scenario must record all of the following from `BacktestRunResult.metrics`:

| Metric | Key | Notes |
|--------|-----|-------|
| Total return % | `total_return` | `(final_equity − capital) / capital` |
| Annualised return % | `annualized_return` | CAGR; interval-aware |
| Max drawdown % | `max_drawdown` | Peak-to-trough |
| Sharpe ratio | `sharpe_ratio` | Annualised excess return / std-dev; interval-aware |
| Trade count | `trade_count` | Total completed round-trips |
| Win rate % | `win_rate` | % of trades with PnL > 0 |
| Average winning trade | `avg_win` | Mean PnL of profitable trades |
| Average losing trade | `avg_loss` | Mean PnL of unprofitable trades |
| Total commission | `total_commission` | Sum of all commissions paid |

Exposure (% of time in market) should be included if the engine provides it.
If not available, it is acceptable to derive from trade log durations.

---

## 6. Validation Rules

All scenarios must satisfy these rules on every run:

| Rule | How validated |
|------|--------------|
| No look-ahead | Engine passes only bars `[0..i]` to strategy on bar `i`; structural guarantee in `BacktestEngine` |
| Interval-aware metrics | `bars_per_year_for_interval(bar_interval)` used for Sharpe and CAGR; validated by `test_backtest_runner.py` |
| Deterministic repeated runs | Running the same scenario twice must produce identical metrics; validated by repeating the run in tests |
| Same input → same output | `BacktestRunConfig` is fully specified; no randomness in strategy, engine, or metrics |
| No live/paper execution | `BacktestRunResult.broker_calls_made == False` asserted in every test |
| No network access in tests | All test runs use local fixtures or synthetic data; no `yfinance` calls in CI |

---

## 7. Acceptance Criteria

A scenario is **accepted** when:

1. The scenario runner produces a complete `BacktestRunResult` without errors.
2. `broker_calls_made == False`.
3. `metrics` contains all required keys (§ 5).
4. Two back-to-back runs with the same config produce byte-identical metrics.
5. The result is documented (metrics table in the PR description or a committed
   output artifact).

**A positive backtest result does not approve live trading, paper trading, or
any automated order execution.** The purpose of these scenarios is
characterisation: understanding how the strategy behaves across market regimes,
not certifying it for deployment.

---

## 8. Data Strategy

### 8.1 Daily bars (Scenarios 1, 3)

yfinance `"1d"` data can be fetched on demand; multi-year history is available
(no 60-day retention limit). Implementation options:

- **Option A (preferred):** Commit a pre-downloaded CSV fixture for SPY and QQQ
  daily bars (2020–2024) under `tests/fixtures/`. The fixture is generated once
  by a documented `python scripts/fetch_fixtures.py` command and committed.
  CI never fetches live data.
- **Option B:** Use `CachedDataProvider` (disk cache) with a pre-populated
  cache directory. Requires cache files to be committed or generated in a
  separate data-prep step.

### 8.2 Hourly bars (Scenarios 2, 4, 5)

Yahoo Finance 1h / 60m data: ~60-day rolling window only. Implications:

- A live fetch for a 6-month backtest will fail silently or return partial data.
- **Required approach:** Pre-downloaded CSV fixtures committed to the repo
  covering at least 40 trading days of 1h bars (enough for 63-bar warm-up).
- Alternative: synthetic deterministic bar generator that produces realistic
  OHLCV sequences satisfying strategy warm-up requirements.

### 8.3 Network access policy

No test in the scenario runner may make a live network request by default.
Any test that requires a live data fetch must be:
- Marked `@pytest.mark.integration` (skipped in CI unless `--run-integration`
  is passed), **or**
- Replaced by a fixture-based equivalent before the PR is merged.

---

## 9. Sub-PR Implementation Plan

### PR 10B — Design (this document)

**Status: designed — `docs/trendfollowing_offline_backtest_scenarios_design.md`**

Docs-only. No `src/`, `tests/`, `config/`, `output/`, or `scripts/` changes.

### PR 10C — Scenario fixtures and runner

**Goal:** Implement the scenario runner and data fixtures.

**Scope:**
- `tests/fixtures/spy_1d_2020_2024.csv` — SPY daily bars (pre-downloaded)
- `tests/fixtures/qqq_1d_2020_2024.csv` — QQQ daily bars (pre-downloaded)
- `tests/fixtures/spy_1h_recent.csv` — SPY hourly bars (40+ trading days)
- `tests/fixtures/qqq_1h_recent.csv` — QQQ hourly bars (40+ trading days)
- `tests/test_trendfollowing_backtest_scenarios.py` — parametrised scenario tests
  asserting: no errors, `broker_calls_made == False`, all metric keys present,
  deterministic repeated runs.
- No `yfinance` live fetches in the test suite.
- Full suite must not regress.

**Not in scope:** Parameter sweep, optimisation, production deployment,
live/paper execution.

---

## 10. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this docs PR |
| No Alpaca SDK imported | No `src/` changes |
| No credentials read | No `src/` changes |
| No order submission | No `src/` changes; `broker_calls_made` assertion in every test |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |
| Test suite unchanged | No `tests/` changes in this docs PR |
| No network in tests | Fixture-based approach required for all scenario tests |

---

## 11. Validation for This Docs PR

```bash
git diff origin/main...HEAD -- src tests config output scripts
# Expected: empty (no src/tests/config/output/scripts changes)
```

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
