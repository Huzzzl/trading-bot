# Local Yahoo Cache Fetch Runbook

Operator runbook for populating `data/cache/` with SPY/QQQ historical bar
data using `src/tools/yahoo_cache_fetch`.

**This runbook is for local operator use only.**
**Never run in CI. Never commit raw cache files.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**PASS means cache populated only — not strategy, paper, or live approval.**

---

## 1. Prerequisites

- Python environment with dependencies installed (`pip install -r requirements.txt`)
- No API key or credential is required (Yahoo Finance free API)
- `data/cache/` is gitignored and must never be committed

---

## 2. Step 1 — Confirm tool is BLOCKED by default

Before running a real fetch, confirm that the tool blocks without the flag:

```bash
python -m src.tools.yahoo_cache_fetch
```

Expected output:

```
Yahoo cache fetch — BLOCKED
  Cache dir     : data/cache
  Symbols       : SPY, QQQ
  Intervals     : 1d, 60m
  Fetched at    : <timestamp>

  files_written            : 0
  availability_check_result: SKIPPED
  network_calls_made       : False
  broker_calls_made        : False
  credentials_read         : False
  order_action_requested   : False
  blocker                  : network fetch not enabled: pass --allow-network to proceed

Result: BLOCKED
```

Exit code: **1** (BLOCKED). No network call was made.

---

## 3. Step 2 — Fetch and cache data (explicit opt-in)

Run the fetch with the explicit `--allow-network` flag. This is the **only
command that makes a network request**:

```bash
python -m src.tools.yahoo_cache_fetch \
    --allow-network \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

**What happens:**
1. For each (symbol, interval) pair — SPY/1d, SPY/60m, QQQ/1d, QQQ/60m:
   - Fetches bars from Yahoo Finance via `YahooDataProvider`
   - Writes to `data/cache/` via `CachedMarketDataProvider` (Parquet or CSV)
   - Waits ≥ 1 second before the next fetch (rate limit)
   - Retries up to 3 times on transient failures (2 s / 4 s / 8 s backoff)
2. After all fetches, runs `cached_data_availability_check` automatically.
3. Exits 0 (PASS) if all pairs fetched and availability check passes.

**Expected output (success):**

```
Yahoo cache fetch — PASS
  Cache dir     : data/cache
  Symbols       : SPY, QQQ
  Intervals     : 1d, 60m
  Fetched at    : <timestamp>

  Fetched successfully:
    OK    SPY/1d   rows=<N>  2020-01-02 → <today>
    OK    SPY/60m  rows=<N>  <start> → <today>
    OK    QQQ/1d   rows=<N>  2020-01-02 → <today>
    OK    QQQ/60m  rows=<N>  <start> → <today>

  files_written            : 4
  availability_check_result: PASS
  network_calls_made       : True
  broker_calls_made        : False
  credentials_read         : False
  order_action_requested   : False

Result: PASS
```

**Safe output fields:**
- `rows` — integer count of bars fetched (not raw price values)
- `inferred_start` / `inferred_end` — ISO date strings (not raw prices)
- `files_written` — integer count of cache files written
- `result` — `PASS` or `BLOCKED`

**Forbidden output fields** (these must not appear):
- `open`, `high`, `low`, `close`, `volume` (raw OHLCV values)
- Any numeric price value

### Optional: save a JSON report

```bash
python -m src.tools.yahoo_cache_fetch \
    --allow-network \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m \
    --output output/fetch_report.json
```

`output/` is gitignored. Do not commit the report.

---

## 4. Step 3 — Verify cache availability

After the fetch, confirm the cache is populated and valid using the offline
checker (no network required):

```bash
python -m src.tools.cached_data_availability_check \
    --cache-dir data/cache \
    --symbols SPY QQQ \
    --intervals 1d 60m
```

**Expected output (success):**

```
Cache availability check — PASS
  Cache dir : data/cache
  Symbols   : SPY, QQQ
  Intervals : 1d, 60m

  Valid files found:
    OK  SPY/1d   SPY_2020-01-01_<today>_1d.parquet  (<N> rows)
    OK  SPY/60m  SPY_<start>_<today>_60m.parquet    (<N> rows)
    OK  QQQ/1d   QQQ_2020-01-01_<today>_1d.parquet  (<N> rows)
    OK  QQQ/60m  QQQ_<start>_<today>_60m.parquet    (<N> rows)

  All expected entries are present and valid.

  broker_calls_made    : False
  credentials_read     : False
  network_calls_made   : False
  order_action_requested: False

Result: PASS
```

Exit code: **0** (PASS).

---

## 5. If fetch fails (BLOCKED)

If either step 2 or step 3 returns BLOCKED, **do not proceed** to
real-data backtests. Investigate the blocker before retrying.

**Common blockers and remediation:**

| Blocker | Cause | Remediation |
|---------|-------|-------------|
| `network fetch not enabled` | `--allow-network` flag missing | Add `--allow-network` |
| `empty dataframe returned` | Yahoo returned no data for the date range | Check retention window; try shorter range |
| `no matching cache file found` | Cache was not written | Re-run fetch; check disk permissions on `data/cache/` |
| `OHLCV columns missing` | File written with wrong schema | Delete corrupt file and re-fetch |
| Provider exception | Yahoo transient error | Wait a few minutes and retry |

To retry a single symbol or interval:

```bash
python -m src.tools.yahoo_cache_fetch \
    --allow-network \
    --cache-dir data/cache \
    --symbols SPY \
    --intervals 60m
```

---

## 6. Subsequent runs (cache already populated)

`CachedMarketDataProvider` writes once and reads from disk on subsequent
calls. If `data/cache/` already contains the required files, re-running the
fetch command makes **no network request** for those entries (cache hit).

To force a fresh fetch, delete the relevant cache files first (see § 7).

---

## 7. Optional — Inspect or clear the local cache

**List cache files:**

```bash
ls -lh data/cache/
```

**Delete specific files (to force re-fetch):**

```bash
rm data/cache/SPY_*_60m.parquet   # delete SPY hourly cache
rm data/cache/QQQ_*_60m.parquet   # delete QQQ hourly cache
```

**Delete entire cache (all symbols/intervals):**

```bash
rm -rf data/cache/
```

After clearing, `data/cache/` will be recreated automatically on the next
fetch run.

**Git policy:** `data/cache/` is listed in `.gitignore`. Cache files are
never committed to the repository. Raw bar data must not be committed under
any path by default.

---

## 8. What PASS means (and does not mean)

| PASS means | PASS does NOT mean |
|---|---|
| Cache is populated with bars for the requested symbols/intervals | Strategy approved for deployment |
| OHLCV columns are present and valid | Paper trading approved |
| Row counts are non-zero | Live trading approved |
| `cached_data_availability_check` returned PASS | Any Alpaca action approved |
| Offline backtests may now use `@pytest.mark.integration` tests | Any individual trade approved |

---

## 9. Safety Summary

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No trading code invoked; no Alpaca import |
| No Alpaca SDK | `yahoo_cache_fetch` and `cached_data_availability_check` do not import Alpaca |
| No credentials | Yahoo Finance free API; no `os.environ` reads |
| No order submission | `order_action_requested=false` in all outputs |
| No broker calls | `broker_calls_made=false` in all outputs |
| No raw prices in output | Row counts and ISO dates only |
| No cache committed | `data/cache/` gitignored; no bar files added |
| No CI network | `--allow-network` required; CI never passes this flag |

---

## 10. Next steps after successful cache fetch

With `data/cache/` populated and `cached_data_availability_check` returning
PASS, the following is possible locally:

- Run `@pytest.mark.integration` tests against cached real data (PR 10I):

```bash
python -m pytest --run-integration
```

These tests skip automatically if the cache is absent (they call
`cached_data_availability_check` internally and `pytest.skip()` if BLOCKED).

They do **not** make network requests. They read from the existing
`data/cache/` files only.

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
