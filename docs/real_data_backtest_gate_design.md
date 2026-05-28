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

**Goal:** Implement `src/tools/yahoo_fetch.py` — a guarded fetch tool that
makes network calls only when `--allow-network` is explicitly passed.

**Scope:**
- `src/tools/yahoo_fetch.py`
- `tests/test_yahoo_fetch.py` (all tests mock the provider; no live network)
- `tests/test_tools_inventory.py` update (count 41 → 42)
- Update relevant design docs

**Not in scope:** Broker calls, credentials, order submission, trading.

### PR 10H — Integration tests with real cached data

**Goal:** Add `@pytest.mark.integration` tests that run the four backtest
scenarios (SPY/QQQ × 1d/1h) against cached real data, skipped in CI unless
`--run-integration` is passed.

**Preconditions:**
- PR 10G fetch tool implemented and passing.
- Operator has run `python -m src.tools.yahoo_fetch --allow-network` and
  `data/cache/` is PASS from `cached_data_availability_check`.
- Tests skip gracefully when cache is absent (`pytest.skip("cache not populated")`).

**Not in scope:** Live data fetch in CI, broker calls, credentials, trading.

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
