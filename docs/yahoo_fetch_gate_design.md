# Yahoo Fetch Gate Design

Design document for PR 10F: define the explicit approval gate for fetching
Yahoo/yfinance historical bar data into the local cache.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**No network requests are made in this docs PR.**
**No raw market data files are committed in this docs PR.**
**This document plans the gate only — implementation requires its own PR (PR 10G).**

---

## 1. Motivation

PR 10E added `cached_data_availability_check` — an offline tool that reports
whether `data/cache/` is populated. When the cache is empty, the tool reports
BLOCKED, but provides no path to populate it without manual intervention.

PR 10D's operator runbook (§ 5) shows inline Python scripts that fetch and
cache data. These scripts are correct but unguarded: there is no fail-closed
mechanism ensuring that the operator runs them safely, understands what is
being fetched, or verifies the result.

This document defines the explicit approval gate that governs when and how
a Yahoo/yfinance network fetch may be performed. The gate enforces the same
fail-closed philosophy as the live-readiness gate: a default-BLOCKED stance
that requires an explicit operator opt-in flag before any network request is
made.

---

## 2. Default Remains No Network

`python -m pytest` must never make a network request. This invariant is
unchanged by this gate and all subsequent implementation PRs.

Real data fetch is always:
- A manual operator action
- Triggered only by an explicit `--allow-network` flag
- Blocked without that flag (exit 1, result=BLOCKED, no network call made)

The CI pipeline never calls yfinance.

---

## 3. Scope

### 3.1 Symbols

Initial scope: **SPY** and **QQQ** only.

No other symbols are permitted until scope is explicitly extended in a
separate design PR.

### 3.2 Intervals

| Interval | Max history | Notes |
|----------|------------|-------|
| `1d` | Multi-year | No documented Yahoo retention limit |
| `60m` / `1h` | ~730 days | Per `YahooDataProvider._INTRADAY_MAX_HISTORY_DAYS["60m"] = 730` |

No sub-hourly intervals (`1m`, `5m`, `15m`, `30m`) are in scope for this gate.

### 3.3 Date ranges

| Interval | Recommended fetch window |
|----------|------------------------|
| `1d` | `2020-01-01` to present |
| `60m` | Most recent 700 calendar days from fetch date |

These are guidance values. The implementation PR (PR 10G) must enforce
the retention-window limits defined in `YahooDataProvider._validate_retention_window()`.
Requests outside the allowed window must BLOCK before any network call.

---

## 4. Data Source Policy

| Rule | Value |
|------|-------|
| Provider | `YahooDataProvider` + `CachedMarketDataProvider` only |
| API key required | **No** — Yahoo Finance free API; no credentials |
| Alpaca SDK | **Forbidden** — no import, no reference |
| Broker calls | **Forbidden** — no Alpaca, no broker API |
| `os.environ` reads | **Forbidden** — no credential env vars |
| Other HTTP clients | **Forbidden** — no `requests`, `httpx`, `aiohttp`, `urllib.request` directly |

`YahooDataProvider` uses `yfinance` internally. Any network call must flow
exclusively through this provider. Direct `yfinance` calls outside of
`YahooDataProvider` are not permitted in the implementation PR.

---

## 5. Rate-Limit and Retry Policy

Yahoo Finance rate-limits free API callers. The implementation must be
conservative to avoid hitting those limits and causing partial or corrupt
cache state.

### 5.1 Required behaviour

| Rule | Value |
|------|-------|
| Delay between symbol fetches | ≥ 1 second |
| Delay between interval fetches for the same symbol | ≥ 1 second |
| Maximum retries on transient failure | 3 attempts |
| Retry backoff | Exponential: 2 s, 4 s, 8 s |
| Partial fetch on retry exhaustion | **BLOCKED** (see § 9) |

### 5.2 Timeout

Each `fetch_bars` call must complete within a reasonable wall-clock timeout
(suggested: 60 seconds per symbol × interval combination). If the call
exceeds the timeout, the run is BLOCKED; no partial data is written.

---

## 6. Fetch Workflow

The implementation PR (PR 10G) must implement the following workflow:

```
Operator runs:
  python -m src.tools.yahoo_fetch [--allow-network] [--symbols SPY QQQ]
                                   [--intervals 1d 60m] [--cache-dir data/cache]
                                   [--output fetch_report.json]

Without --allow-network:
  → result = BLOCKED
  → blocker = "network fetch not enabled: pass --allow-network to proceed"
  → exit 1
  → zero network calls made

With --allow-network:
  For each (symbol, interval) in scope:
    1. Validate (symbol, interval) is in the allowed set
    2. Validate date range is within retention window
    3. Call CachedMarketDataProvider.fetch_bars(symbol, start, end, interval)
       (first call → fetches from Yahoo and writes to data/cache/)
       (subsequent calls → loads from disk, no network)
    4. On success: record rows fetched, inferred start/end dates (no raw prices)
    5. On failure/timeout: record BLOCKED entry; abort remaining fetches
    6. Wait ≥ 1 second before next fetch
  After all fetches:
    Run cached_data_availability_check (inline, no subprocess)
    If availability check PASS → result = PASS
    If availability check BLOCKED → result = BLOCKED (even if fetches appeared to succeed)
  Write summary report (see § 7)
  Exit 0 on PASS; exit 1 on BLOCKED
```

---

## 7. Write Policy

### 7.1 Write targets

| Location | May be written? | Conditions |
|----------|----------------|------------|
| `data/cache/` | **Yes** | Parquet or CSV; gitignored; generated by `CachedMarketDataProvider` |
| `output/` | **Yes** | Summary report JSON only; gitignored |
| `src/`, `tests/`, `config/` | **No** | Fetch tool must not modify these paths |
| Anywhere else | **No** | No other writes permitted |

### 7.2 Raw bar policy

Raw bar data (OHLCV values) must never appear in any output artifact
committed to the repository. This includes:
- `output/` JSON reports — must contain only row counts, date ranges, and
  summary metadata; no price values
- `docs/` committed summaries — must contain only aggregated metrics;
  no raw prices
- Log output to stdout — row counts and date range strings are permitted;
  raw close prices are not

The cache files in `data/cache/` contain raw OHLCV data and are gitignored.
They must never be committed.

---

## 8. Post-Fetch Validation

After every successful fetch, the implementation must run
`cached_data_availability_check` (from `src/tools/cached_data_availability_check.py`)
to confirm the cache is now populated and valid.

The availability check result is authoritative:
- If it returns PASS → the overall fetch result is PASS
- If it returns BLOCKED for any entry → the overall fetch result is BLOCKED

This ensures the fetch and the availability check share a single source of
truth for what "populated" means.

---

## 9. Output Summary Policy

The fetch summary report (`--output`) must contain:

```json
{
  "result": "PASS | BLOCKED",
  "fetched_at": "ISO timestamp",
  "symbols_requested": ["SPY", "QQQ"],
  "intervals_requested": ["1d", "60m"],
  "entries": [
    {
      "symbol": "SPY",
      "interval": "1d",
      "status": "OK | BLOCKED | SKIPPED",
      "rows": 1258,
      "inferred_start": "2020-01-02",
      "inferred_end": "2024-12-31",
      "cached_file": "SPY_2020-01-01_2024-12-31_1d.parquet"
    }
  ],
  "availability_check_result": "PASS | BLOCKED",
  "network_calls_made": true,
  "broker_calls_made": false,
  "credentials_read": false,
  "order_action_requested": false
}
```

**Fields forbidden from output:**
- Raw price values (`open`, `high`, `low`, `close`)
- Raw volume values
- Any value derived from individual bar prices

**Fields permitted:**
- Row counts (integer)
- Date range strings (`inferred_start`, `inferred_end`) — ISO date only
- File names and paths
- Status strings

`network_calls_made` is the only safety flag that may be `true` when
`--allow-network` is passed. All other flags (`broker_calls_made`,
`credentials_read`, `order_action_requested`) must remain `false`.

---

## 10. Failure Policy

Fetch failures are fail-closed:

| Failure condition | Behaviour |
|-------------------|-----------|
| `--allow-network` not passed | BLOCKED immediately; no network call |
| Symbol not in allowed set | BLOCKED; no network call |
| Interval not in allowed set | BLOCKED; no network call |
| Date range outside retention window | BLOCKED; no network call |
| `yfinance` raises exception | BLOCKED; partial results discarded |
| Timeout exceeded | BLOCKED; partial results discarded |
| Retry exhaustion (3 attempts) | BLOCKED; partial results discarded |
| Post-fetch availability check BLOCKED | BLOCKED overall |

**No partial approval.** If any (symbol, interval) pair fails, the overall
result is BLOCKED. Successful pairs are recorded but the run is not PASS.

---

## 11. Gate Result Semantics

| Result | Meaning |
|--------|---------|
| BLOCKED (no flag) | Network fetch not enabled; no data fetched; cache unchanged |
| BLOCKED (fetch failure) | One or more fetches failed; cache may be partially populated |
| BLOCKED (post-check) | Fetches appeared successful but availability check disagrees |
| PASS | All requested symbol × interval combinations are now in `data/cache/` and validated by `cached_data_availability_check` |

**PASS means cache is populated only.**

A PASS result does not approve:
- Any trading strategy for deployment
- Paper trading
- Live trading
- Any Alpaca account action
- Any change to `live_trading_approved` or `paper_trading_enabled`

---

## 12. Sub-PR Implementation Plan

### PR 10F — Design (this document)

**Status: designed — `docs/yahoo_fetch_gate_design.md`**

Docs-only. No `src/`, `tests/`, `config/`, `output/`, `scripts/`, or `data/`
changes.

### PR 10G — Yahoo fetch tool (explicit `--allow-network` gate)

**Goal:** Implement `src/tools/yahoo_fetch.py` — the first tool in `src/tools/`
that may make a network call when explicitly opted in by the operator.

**Scope:**
- `src/tools/yahoo_fetch.py`
- `tests/test_yahoo_fetch.py` (all tests use mocks; no live network in tests)
- `tests/test_tools_inventory.py` update (count 41 → 42; new tool in `DATA_TOOLS`)
- Update `docs/yahoo_fetch_gate_design.md` with implemented status

**Behaviour:**
- Without `--allow-network`: result=BLOCKED, exit 1, zero network calls made
- With `--allow-network`: fetches via `YahooDataProvider` + `CachedMarketDataProvider`,
  applies rate-limit policy, runs post-fetch availability check, exits 0 on PASS
- All tests mock the provider; no live `yfinance` calls in any test
- Source scan: no Alpaca, no credentials, no order calls

**Not in scope:** Live data fetch in CI, broker calls, credentials, trading.

### PR 10H — Integration tests with real cached data

**Goal:** Add `@pytest.mark.integration` tests that run the four backtest
scenarios (SPY/QQQ × 1d/1h) against cached real data, skipped in CI unless
`--run-integration` is passed.

**Preconditions:**
- PR 10G fetch tool implemented and passing.
- Operator has run `python -m src.tools.yahoo_fetch --allow-network` and
  `data/cache/` is populated and PASS from availability check.
- Tests skip gracefully when cache is absent (`pytest.skip("cache not populated")`).

**Not in scope:** Live data fetch in CI, broker calls, credentials, trading.

---

## 13. Validation for This Docs PR

```bash
git diff origin/main...HEAD -- src tests config output scripts data
# Expected: empty
```

---

## 14. Safety Guarantees

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this docs PR |
| No Alpaca SDK | No `src/` changes |
| No credentials | No `src/` changes; `YahooDataProvider` requires no API key |
| No order submission | No `src/` changes; `broker_calls_made` always `False` |
| No network in tests | No `tests/` changes in this docs PR |
| No raw data committed | No `data/` changes in this docs PR; cache is gitignored |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |
| Full suite unchanged | No `src/` or `tests/` changes |

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
