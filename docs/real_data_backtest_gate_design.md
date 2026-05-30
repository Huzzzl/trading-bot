# Real-Data Backtest Gate Design

Design document for PR 10D: define the safe gate for using cached or
Yahoo-fetched historical data in offline TrendFollowing backtests.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**No network requests are made in this docs PR.**
**No raw market data files are committed in this docs PR.**
**This document plans the gate only — implementation requires its own PR.**

---

## 1. Motivation

PR 10C implemented four backtest scenarios (SPY/QQQ × 1d/1h) using
fully synthetic deterministic fixtures. Synthetic data validates the
mechanics of `run_backtest()` and the strategy pipeline. It does not
validate strategy behaviour against realistic historical price patterns.

The next step is to enable **offline** validation against real historical
bar data — while keeping all the same safety guarantees:
no broker calls, no credentials, no live/paper execution, no look-ahead.

---

## 2. Default Remains Synthetic Fixtures

The default CI test run (`python -m pytest`) **must not** make network
requests. The synthetic fixtures from PR 10C remain the CI default.

Real-data backtests are run:
- Manually, by the operator, on demand
- After explicit data fetch (see § 5)
- Using tests marked `@pytest.mark.integration` (skipped in CI unless
  `--run-integration` is explicitly passed)

This two-tier approach keeps CI fast and credential-free while allowing
the operator to validate against real data locally.

---

## 3. Data Sources and Retention Limits

### 3.1 Yahoo Finance via `YahooDataProvider`

`src/data/yahoo_provider.py` documents the following retention windows
(from `_INTRADAY_MAX_HISTORY_DAYS`):

| Interval | Retention (calendar days) | Notes |
|----------|--------------------------|-------|
| `1m` | 7 | Not used in these scenarios |
| `2m`, `5m`, `15m`, `30m`, `90m` | 60 | Not used in these scenarios |
| `60m` / `1h` | **730** (~2 years) | Sufficient for meaningful backtests |
| `1d` | Years (no documented limit) | Multi-year history available |

**Correction from PR 10B design doc:** The PR 10B document stated "Yahoo
Finance retains intraday data for approximately 60 days." This is accurate
for sub-hourly intervals (5m, 15m, 30m) but **not** for `60m` / `1h`,
which retains approximately 730 days. The correct limit is documented above
and is enforced by `YahooDataProvider._validate_retention_window()`.

### 3.2 Symbols

Initial real-data runs are restricted to:
- `SPY` — primary US equity ETF
- `QQQ` — secondary tech-weighted ETF

No other symbols are in scope for this gate design.

### 3.3 Intervals

| Interval | History available | Backtest window |
|----------|------------------|----------------|
| `1d` | Multi-year | 2020-01-01 – 2024-12-31 (5 years) |
| `1h` / `60m` | ~730 days | Most recent ~2 years from fetch date |

---

## 4. Cache Strategy

### 4.1 `CachedMarketDataProvider`

`src/data/cached_provider.py` provides a disk-backed caching layer that
wraps any `BaseDataProvider`. Cache key: `(symbol, start, end, interval)`.

- **First call:** data is fetched from the underlying provider and written
  to disk as Parquet (or CSV fallback if pyarrow is absent).
- **Subsequent calls:** data is loaded from disk; no network request is made.
- **Determinism:** once cached, the same request always returns identical
  data, satisfying the determinism requirement from § 6.

### 4.2 Cache directory

Default cache dir: `data/cache/` (relative to repo root, created automatically).

**Git policy:** `data/cache/` must be listed in `.gitignore`. Raw bar data
files must not be committed to the repository by default.
- Reason: raw bar files are large, change over time (split-adjusted prices),
  and are re-downloadable on demand. Committing them creates maintenance
  burden and may violate Yahoo Finance terms of use.
- Exception: if a specific frozen snapshot is explicitly approved for
  archival (e.g. for audit purposes), it may be committed under
  `data/snapshots/` with a documented rationale. That approval requires
  its own PR.

### 4.3 `.gitignore` update (required in implementation PR)

The implementation PR (PR 10E) must add or verify:

```gitignore
# Market data cache (generated on demand — do not commit)
data/cache/
```

---

## 5. Operator Runbook — Fetching Real Data

The following commands fetch and cache data for offline backtest use.
**No trading occurs. No credentials are required.**

### 5.1 Daily bars (SPY, 2020–2024)

```bash
# Fetch and cache SPY daily bars — no network in subsequent runs
python - <<'EOF'
from src.data.yahoo_provider import YahooDataProvider
from src.data.cached_provider import CachedMarketDataProvider

provider = CachedMarketDataProvider(
    YahooDataProvider(),
    cache_dir="data/cache",
)
df = provider.fetch_bars("SPY", "2020-01-01", "2024-12-31", "1d")
print(f"SPY 1d: {len(df)} bars  {df.index[0]} → {df.index[-1]}")
EOF
```

### 5.2 Hourly bars (SPY, last ~700 days)

```bash
python - <<'EOF'
import datetime
from src.data.yahoo_provider import YahooDataProvider
from src.data.cached_provider import CachedMarketDataProvider

provider = CachedMarketDataProvider(
    YahooDataProvider(),
    cache_dir="data/cache",
)
end   = datetime.date.today().isoformat()
start = (datetime.date.today() - datetime.timedelta(days=700)).isoformat()
df = provider.fetch_bars("SPY", start, end, "60m")
print(f"SPY 60m: {len(df)} bars  {df.index[0]} → {df.index[-1]}")
EOF
```

### 5.3 QQQ (same pattern)

Repeat the above commands with `"QQQ"` replacing `"SPY"`.

### 5.4 Notes

- Run once; subsequent runs load from `data/cache/` without network access.
- If `data/cache/` is deleted, re-run to refetch.
- No API key or credential is needed for Yahoo Finance.
- No order is placed. No broker is contacted.

---

## 6. Validation Rules for Real-Data Backtests

All real-data runs must satisfy the same rules as synthetic-fixture runs:

| Rule | How enforced |
|------|-------------|
| No look-ahead | `BacktestEngine` structural guarantee — only bars ≤ current index passed to strategy |
| Deterministic once cached | `CachedMarketDataProvider` returns identical data on repeated calls after first fetch |
| Interval-aware metrics | `bars_per_year_for_interval(interval)` used for Sharpe and CAGR |
| Same input → same output | `BacktestRunConfig` fully specified; no randomness in strategy or engine |
| No live/paper execution | `BacktestRunResult.broker_calls_made == False` asserted |
| No credentials | `YahooDataProvider` requires no API key or secret |
| No Alpaca SDK | `YahooDataProvider` and `CachedMarketDataProvider` do not import Alpaca |

---

## 7. Outputs

### 7.1 What may be committed

| Output | May be committed? | Conditions |
|--------|------------------|------------|
| Summary metrics (JSON/Markdown table) | **Yes** | After operator review; in `docs/` |
| Equity curve plots (PNG) | **Maybe** | With explicit approval; under `docs/` or `output/` |
| Raw bar CSV/Parquet files | **No** | Default: stays in `data/cache/` (gitignored) |
| `output/` run artifacts | **No** | `output/` is gitignored by default |

### 7.2 Example committed metrics format

Summary metrics from a real-data backtest run may be committed as a
Markdown table in `docs/` or as a `JSON` file. Example format:

```markdown
| Scenario | Total return % | Annualised % | Max drawdown % | Sharpe | Trades |
|----------|---------------|-------------|---------------|--------|--------|
| SPY 1d TrendFollowing 2020–2024 | +12.4 | +2.4 | −18.7 | 0.28 | 12 |
| QQQ 1d TrendFollowing 2020–2024 | +18.1 | +3.4 | −31.2 | 0.31 | 15 |
```

**A positive result does not approve live trading.** Summary metrics are
characterisation records only.

### 7.3 Output directory policy

`output/` is gitignored (confirmed by `.gitignore` entry `outputs/`).
Trade logs, equity curve CSVs, and order intent files written there
by `ReportGenerator` are never committed unless explicitly approved.

---

## 8. What This Gate Explicitly Does Not Approve

- **No automated fetch.** Data fetch is always a manual operator action.
- **No scheduled or CI data fetch.** The CI pipeline never calls yfinance.
- **No raw data committed by default.** Cache files stay in `data/cache/` (gitignored).
- **No parameter sweep.** Real-data runs use the same default params as PR 10C.
- **No paper trading.** Real-data backtests produce simulated results only.
- **No live trading.** No Alpaca calls; no credentials; no order submission.
- **No multi-symbol expansion.** SPY and QQQ only until scope is explicitly extended.
- **No production deployment.** Real-data backtest results are characterisation only.

---

## 9. Sub-PR Implementation Plan

### PR 10D — Design (this document)

**Status: designed — `docs/real_data_backtest_gate_design.md`**

Docs-only. No `src/`, `tests/`, `config/`, `output/`, or `scripts/` changes.

### PR 10E — Cache availability checker (no trading, no credentials)

**Status: implemented — `src/tools/cached_data_availability_check.py`**

**What was added:**
- `src/tools/cached_data_availability_check.py` — offline read-only tool (41st tool
  in `src/tools/`); scans `data/cache/` for SPY/QQQ × 1d/60m bar files; validates
  OHLCV columns; reports PASS or BLOCKED; 60m ↔ 1h aliasing supported.
- `tests/test_cached_data_availability_check.py` — 42 tests across 9 test classes:
  `TestMissingCacheDir`, `TestMissingFiles`, `TestValidCache`, `TestIntervalAliasing`,
  `TestInvalidColumns`, `TestSafetyFlags`, `TestNoPricesEmitted`, `TestDeterminism`,
  `TestOutputJson`, `TestSourceScan` (AST-based forbidden-import checks).
- `tests/test_tools_inventory.py` — updated count from 40 to 41; added `DATA_TOOLS`
  tuple containing `cached_data_availability_check`.
- `.gitignore` — added `data/cache/` entry per § 4.3.

**CLI:**
```bash
python -m src.tools.cached_data_availability_check
python -m src.tools.cached_data_availability_check --cache-dir data/cache --symbols SPY QQQ --intervals 1d 60m
python -m src.tools.cached_data_availability_check --output cache_status.json
```

Exit 0 on PASS; exit 1 on BLOCKED. Prints human-readable summary; JSON only with `--output`.

**Not in scope:** Live data fetch, broker calls, credentials, trading.

### PR 10F — Yahoo fetch gate design

**Status: designed — `docs/yahoo_fetch_gate_design.md`**

Docs-only. Defines the explicit approval gate for fetching Yahoo/yfinance
historical bar data into `data/cache/`. Covers: default-BLOCKED stance, the
`--allow-network` opt-in flag, symbol/interval scope, rate-limit and retry
policy, post-fetch validation via `cached_data_availability_check`, output
summary policy (no raw prices), and failure policy (fail-closed, no partial
approval). No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/`
changes.

### PR 10G — Yahoo fetch tool (explicit `--allow-network` gate)

**Status: implemented — `src/tools/yahoo_cache_fetch.py`**

**What was added:**
- `src/tools/yahoo_cache_fetch.py` — guarded fetch tool (42nd tool in `src/tools/`);
  default BLOCKED without `--allow-network`; fetches via `YahooDataProvider` +
  `CachedMarketDataProvider`; conservative rate-limit (≥1s, max 3 retries,
  exponential backoff); post-fetch validation via `cached_data_availability_check`;
  no raw prices in output.
- `tests/test_yahoo_cache_fetch.py` — 43 tests across 8 test classes; all mock the
  inner provider; no live yfinance calls in any test.
- `tests/test_tools_inventory.py` — count updated from 41 to 42.

**Not in scope:** Broker calls, credentials, order submission, trading.

### PR 10H — Local operator runbook (docs-only)

**Status: complete — `docs/local_yahoo_cache_fetch_runbook.md`**

Step-by-step operator runbook for populating `data/cache/` locally.
Covers: confirm default BLOCKED, run fetch with `--allow-network`, verify
with `cached_data_availability_check`, failure remediation, cache cleanup,
and what PASS does and does not mean.
No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` changes.

### PR 10I — Cached real-data backtest checker

**Status: implemented — `src/tools/cached_real_data_backtest_check.py`**

**What was added:**
- `src/tools/cached_real_data_backtest_check.py` — offline characterization tool
  (43rd tool in `src/tools/`); reads from `data/cache/` only; no network; runs
  `run_backtest()` with `trend_following` for SPY/QQQ × 1d/60m; reports metric
  summaries (no raw prices); BLOCKED if cache missing; PASS means characterization
  ran only; 60m ↔ 1h aliasing supported.
- `tests/test_cached_real_data_backtest_check.py` — 53 tests across 10 test classes:
  `TestMissingCache`, `TestValidCache`, `TestInvalidColumns`, `TestDeterminism`,
  `TestIntervalAliasing`, `TestSafetyFlags`, `TestNoPricesEmitted`, `TestOutputJson`,
  `TestSourceScan` (AST-based forbidden-import checks), `TestTrendParams` (verifies
  `fast_ema_period=10`/`slow_ema_period=50` — correct strategy param names).
- `tests/test_tools_inventory.py` — count updated from 42 to 43.

**CLI:**
```bash
python -m src.tools.cached_real_data_backtest_check
python -m src.tools.cached_real_data_backtest_check --cache-dir data/cache --symbols SPY QQQ --intervals 1d 60m
python -m src.tools.cached_real_data_backtest_check --output result.json
```

Exit 0 on PASS; exit 1 on BLOCKED. Prints human-readable summary; JSON only with `--output`.

**Not in scope:** Live data fetch in CI, broker calls, credentials, trading, raw OHLCV values in output.

### PR 10J — First real-data backtest results snapshot (this document)

**Status: complete — `docs/first_cached_real_data_backtest_results_snapshot.md`**

Docs-only. Records the first operator-run results from the full three-step
real-data pipeline (yahoo_cache_fetch → cached_data_availability_check →
cached_real_data_backtest_check). All four scenarios (SPY/QQQ × 1d/60m)
returned PASS. Captures raw metric values, interpretation, and follow-up
diagnostic plan (PR 10K: Sharpe diagnostic implemented; PR 10L: Sharpe diagnostics integrated into cached checker implemented; PR 10N: annualized-vol warning threshold calibrated; PR 10O: calibrated-diagnostics rerun snapshot; PR 10M:
default params comparison; PR 10P: trade summary diagnostics design; PR 10Q:
trade schema characterization tests). No strategy/paper/live approval.
No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/` changes.

### PR 10K — Backtest metrics diagnostics

**Status: implemented — `src/backtest/metrics_diagnostics.py`**

**What was added:**
- `src/backtest/metrics_diagnostics.py` — offline Sharpe diagnostic helper;
  `diagnose_sharpe(equity_curve, interval)` recomputes Sharpe using the same
  formula as `compute_metrics()`; detects zero std (BLOCKED), near-zero std
  (warning), and non-finite values (BLOCKED); no raw equity values in output;
  no strategy/engine/execution changes.
- `tests/test_backtest_metrics_diagnostics.py` — 67 tests across 10 classes:
  `TestInvalidInputs`, `TestFlatCurve`, `TestNormalCurve`, `TestLowVariance`,
  `TestIntervalLookup`, `TestSafetyFlags`, `TestNoPricesEmitted`,
  `TestDeterminism`, `TestSourceScan`, `TestDiagnosticVsProduction`.

**Not in scope:** `compute_metrics()` changes, strategy changes, paper/live trading.

### PR 10L — Sharpe diagnostics in cached_real_data_backtest_check

**Status: implemented — `src/tools/cached_real_data_backtest_check.py`**

**What was added:**
- `run_check()` now calls `diagnose_sharpe(result_bt.equity_curve, interval)`
  after each successful `run_backtest()` and appends 5 per-scenario fields:
  `sharpe_diagnostic_result`, `zero_std_detected`, `low_variance_warning`,
  `annualized_volatility`, `return_points`.
- Diagnostic BLOCKED does not affect scenario status (independent code paths).
- `sharpe_ratio` from `compute_metrics()` is unchanged.
- `tests/test_cached_real_data_backtest_check.py` — 8 new tests (`TestSharpeDiagnostics`).

**Not in scope:** `metrics.py` changes, strategy changes, paper/live trading.

### PR 10N — Calibrate diagnose_sharpe() low-vol threshold

**Status: implemented — `src/backtest/metrics_diagnostics.py`**

**What was added:**
- `_LOW_ANNUALIZED_VOL_THRESHOLD = 0.001` constant; `low_variance_warning` now fires
  when `annualized_volatility < 0.001` in addition to the legacy per-bar std check.
  SPY/QQQ 1d real-data cases (ann_vol ≈ 0.0003) now show `low_variance_warning=True`.
- `tests/test_backtest_metrics_diagnostics.py` — 5 new tests (`TestAnnualizedVolThreshold`);
  72 total. `cached_real_data_backtest_check.py` unchanged.

**Not in scope:** `metrics.py` changes, strategy changes, paper/live trading.

### PR 10O — Calibrated diagnostics rerun snapshot

**Status: implemented — `docs/calibrated_sharpe_diagnostics_real_data_snapshot.md`**

Docs-only. Records operator rerun confirming PR 10N calibration: SPY/QQQ 1d
`low_variance_warning=True`, SPY/QQQ 60m `low_variance_warning=False`. No src changes.

### PR 10M — Default params comparison

**Status: implemented — `tests/test_trendfollowing_param_comparison.py`**

**What was added:**
- `tests/test_trendfollowing_param_comparison.py` — 29 characterization tests
  across 5 classes locking in the param divergence between the checker and the
  strategy defaults.

**Finding:** The checker uses `fast_ema_period=10` intentionally for broader signal
characterization; `TrendFollowing()` with no args defaults to `fast_ema_period=20`.
All other params are shared. Checker uses correct key names (`fast_ema_period`,
`slow_ema_period`); no obsolete `ema_fast`/`ema_slow` keys present. Both param sets
accepted by `TrendFollowing()` without error.

No parameter optimization performed. No `cached_real_data_backtest_check.py`,
`metrics.py`, strategy, engine, or execution changes.

**Not in scope:** Parameter optimisation, paper trading, live trading.

### PR 10P — Trade summary diagnostics design

**Status: implemented — `docs/trade_summary_diagnostics_design.md`**

Docs-only design for trade-level aggregate diagnostics. Defines the diagnostic
fields to be computed from `BacktestRunResult.trades`, documents the full
`Trade` schema (`symbol`, `entry_time`, `exit_time`, `entry_price`,
`exit_price`, `shares`, `commission`, `direction`, `exit_reason`, `pnl`,
`meta`) and known `exit_reason` values (`stop_loss`, `force_exit`,
`session_end`, `end_of_backtest`, `daily_loss_limit`). Notes that strategy
EXIT signals are currently not acted on by the engine. Specifies safety
constraints for the pure/offline `trade_summary_diagnostics()` helper.
Implementation plan: PR 10Q (schema tests), PR 10R (helper), PR 10S
(checker integration), PR 10T (snapshot).

**Not in scope:** Parameter optimisation, paper trading, live trading.

### PR 10Q — Trade schema characterization tests

**Status: implemented — `tests/test_backtest_trade_schema.py`**

60 tests across 5 classes (`TestTradeSchema`, `TestToDictKeys`,
`TestExitReasonValues`, `TestBacktestRunResultTradesField`,
`TestSafetySourceScan`). Locks in `Trade` field names and types, pnl computed
in `__post_init__` (not an init param), meta excluded from `to_dict()`, the
5-value `exit_reason` allowlist, and all `BacktestRunResult` safety flags.
Synthetic fixture: fast_ema_period=5, slow_ema_period=20, n=100 daily bars,
seed=42 → 13 closed LONG trades with exit_reason='session_end'. Source scan
confirms no forbidden imports in trade.py and backtest_runner.py.

No strategy, engine, `metrics.py`, `metrics_diagnostics.py`, or
`cached_real_data_backtest_check.py` changes.

**Not in scope:** Parameter optimisation, paper trading, live trading.

### PR 10R — `trade_summary_diagnostics` helper

**Status: implemented — `src/backtest/trade_diagnostics.py`**

`trade_summary_diagnostics(trades, *, total_bars=None)` returns 19 aggregate
fields plus 4 safety flags. Pure offline. BLOCKED on non-finite numeric
values; PASS with zeros on empty list. Holding-period fields in approximate
hours (`(exit_time - entry_time).total_seconds() / 3600`; 0.0 for same-bar
trades). `exposure_pct` = conservative lower bound (1 bar/trade). No raw
prices in output. 70 tests across 10+ classes.

No strategy, engine, `metrics.py`, `metrics_diagnostics.py`, or
`cached_real_data_backtest_check.py` changes.

**Not in scope:** Parameter optimisation, paper trading, live trading.

---

## 10. Validation for This Docs PR

```bash
git diff origin/main...HEAD -- src tests config output scripts
# Expected: empty
```

---

## 11. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this docs PR |
| No Alpaca SDK | No `src/` changes; `YahooDataProvider` does not import Alpaca |
| No credentials | `YahooDataProvider` requires no API key |
| No order submission | No `src/` changes; `broker_calls_made` always `False` in backtest |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |
| Test suite unchanged | No `tests/` changes in this docs PR |
| No raw data committed | Cache gitignored; no bar files added in this PR |

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
