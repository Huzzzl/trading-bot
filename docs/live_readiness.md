# Live Account Readiness Check

Read-only tool to verify live Alpaca credentials and account health before any
future live trading work.

**This tool is strictly read-only. It never submits or cancels orders.**

> **Current gate status:** see [docs/live_readiness_status.md](live_readiness_status.md)
> for the latest GO/NO-GO result, active blockers, and conditions required before
> any live submit design begins.

---

## What it does

1. Resolves live credentials from `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY`.
2. Opens a live (non-paper) `TradingClient` connection (`paper=False`).
3. Reads account status, buying power, portfolio value, open positions, and open orders.
4. Prints a structured report and exits `0` (all PASS) or `1` (any WARN or FAIL).

## What it never does

- Never calls `submit_order` or `cancel_order`.
- Never modifies positions, orders, or account state.
- Never reads paper credentials (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`).
- Never writes any file.

---

## This is NOT a live trading submission tool

This tool exists solely to let you verify that your live account is accessible and
in a healthy state. Running it will not place trades, will not affect your positions,
and will not consume any of your daily order limits.

Future live trading submission support (if ever added) would be a separate,
explicitly gated tool — not an extension of this one.

---

## Usage

```bash
export ALPACA_LIVE_API_KEY="your-live-api-key"
export ALPACA_LIVE_SECRET_KEY="your-live-secret-key"

python -m src.tools.live_account_check
```

```powershell
$env:ALPACA_LIVE_API_KEY    = "your-live-api-key"
$env:ALPACA_LIVE_SECRET_KEY = "your-live-secret-key"

python -m src.tools.live_account_check
```

Clear credentials from the shell when done:

```bash
unset ALPACA_LIVE_API_KEY
unset ALPACA_LIVE_SECRET_KEY
```

```powershell
Remove-Item Env:\ALPACA_LIVE_API_KEY    -ErrorAction SilentlyContinue
Remove-Item Env:\ALPACA_LIVE_SECRET_KEY -ErrorAction SilentlyContinue
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | All checks PASS — account is active, unblocked, no open positions or orders |
| `1`  | Any check WARN or FAIL — review the report before proceeding |

---

## Check summary

| Check | PASS | WARN | FAIL |
|-------|------|------|------|
| `credentials` | Both env vars present and non-empty | — | Either var missing or empty |
| `account` | Status `ACTIVE`, not blocked | — | Non-active status, trading/account blocked, or API error |
| `positions` | No open positions | Open positions found | API error |
| `orders` | No open orders | Open orders found | API error |

WARN on positions or orders is informational — open positions on a live account
are normal. Investigate before starting any automated trading to confirm the
account is in the state you expect.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ALPACA_LIVE_API_KEY` | Yes | Live (non-paper) Alpaca API key |
| `ALPACA_LIVE_SECRET_KEY` | Yes | Live (non-paper) Alpaca secret key |
| `ALPACA_LIVE_BASE_URL` | No | Override the default live endpoint URL |

Paper credential variables (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`) are
deliberately ignored by this tool.

---

## Live Shadow Preflight

A second read-only step that combines a local strategy preview with a live
account state check. Run this **after** `live_account_check` passes.

**This tool is also strictly read-only. It never submits or cancels orders.**

### What it does

1. Loads config and runs the local backtest/strategy pipeline to generate
   candidate order intents for the selected symbol (same data path as paper preview).
2. Filters intents to buy+market orders for the selected symbol.
3. Evaluates hypothetical live order sizing against configured limits
   (`live_sizing_mode`, `live_max_quantity`, `live_max_notional`, `live_quantity_override`,
   `live_order_notional_override`, `live_max_order_notional`).
4. Connects to the live account (`paper=False`) and reads account status,
   buying power, open positions, and open orders.
5. Reports whether a hypothetical live submit would be safe.

### What it never does

- Never calls `submit_order` or `cancel_order`.
- Never writes a ledger row or any persistent artifact.
- Never reads paper credentials (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`).

### Usage

```bash
export ALPACA_LIVE_API_KEY="your-live-api-key"
export ALPACA_LIVE_SECRET_KEY="your-live-secret-key"

python -m src.tools.live_shadow_preflight \
    --config config/settings.paper.local.yaml \
    --output-dir output/live_shadow_preflight
```

```powershell
$env:ALPACA_LIVE_API_KEY    = "your-live-api-key"
$env:ALPACA_LIVE_SECRET_KEY = "your-live-secret-key"

python -m src.tools.live_shadow_preflight `
    --config config/settings.paper.local.yaml `
    --output-dir output/live_shadow_preflight
```

Optional flags:

```bash
python -m src.tools.live_shadow_preflight \
    --config config/settings.paper.local.yaml \
    --output-dir output/live_shadow_preflight \
    --symbol SPY \
    --write-report
```

```powershell
python -m src.tools.live_shadow_preflight `
    --config config/settings.paper.local.yaml `
    --output-dir output/live_shadow_preflight `
    --symbol SPY `
    --write-report
```

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol` | `SPY` | Symbol to check |
| `--write-report` | off | Write JSON report and CSV candidates to `--output-dir` |

### Checks performed

| Check | PASS | WARN | FAIL |
|-------|------|------|------|
| `config` | Config loads and validates | — | Load or validation error |
| `candidates` | ≥1 buy+market candidate for symbol | — | No candidates |
| `live_sizing` | Sizing within all configured limits | — | Qty/notional exceeds limit; `entry_price` missing in notional mode; `live_order_notional_override` missing |
| `credentials` | Both `ALPACA_LIVE_*` vars present | — | Either missing or empty |
| `live_account` | Status `ACTIVE`, not blocked | `buying_power` or `portfolio_value` = 0 | Inactive, blocked, or API error |
| `live_position` | No open position for symbol | — | Existing live position for symbol |
| `live_orders` | No open orders for symbol | — | Existing live open order for symbol |

### Sizing config fields

These fields live under `execution:` in your settings YAML:

| Field | Default | Description |
|-------|---------|-------------|
| `live_sizing_mode` | `"quantity"` | `"quantity"` (default) or `"notional"` — controls which sizing path is used |
| `live_max_quantity` | `1.0` | Maximum effective order quantity allowed (quantity mode) |
| `live_max_notional` | `500.0` | Maximum estimated notional value. Set to `null` to disable. Applied in both modes. |
| `live_quantity_override` | `1.0` | Override effective quantity regardless of strategy output (quantity mode). Set to `null` to use raw candidate quantity. |
| `live_order_notional_override` | `null` | Fixed notional per order (notional mode). Required when `live_sizing_mode=notional`. |
| `live_max_order_notional` | `100.0` | Maximum allowed value for `live_order_notional_override` (notional mode). |
| `live_max_daily_notional` | `200.0` | Daily notional cap (reserved for future gate integration). |
| `live_max_account_notional_fraction` | `0.1` | Maximum fraction of portfolio value per order (reserved for future gate integration). |

#### Quantity mode (default)

`effective_quantity = live_quantity_override` (if set) else `original_quantity`.
`estimated_notional = effective_quantity × entry_price` (when `entry_price` is available).
If `live_max_notional` is set and `entry_price` is absent, the check fails closed.

#### Notional mode

Set `live_sizing_mode: notional` and `live_order_notional_override: <amount>` to use fixed-dollar sizing.

`effective_notional = live_order_notional_override`.
`effective_quantity = effective_notional / entry_price`.

The check **fails closed** if:
- `live_order_notional_override` is not set or ≤ 0
- `entry_price` is missing or ≤ 0 from the candidate intent's metadata
- `effective_notional > live_max_order_notional`
- `effective_notional > live_max_notional` (when `live_max_notional` is set)

Example notional-mode config snippet:

```yaml
execution:
  live_sizing_mode: notional
  live_order_notional_override: 100.0   # fixed $100 per order
  live_max_order_notional: 100.0        # must be >= live_order_notional_override
  live_max_notional: 500.0              # portfolio-level cap; null to disable
```

> **Shadow sizing only** — `live_sizing_mode` and all related fields control the
> *shadow preflight sizing check* only. No live orders are ever submitted by any
> tool in this repository. The effective notional is computed to validate whether
> a hypothetical live order would be within configured limits.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All checks PASS — safe to proceed to a hypothetical live submit |
| `1` | Any check WARN or FAIL — review before proceeding |

### Artifact review (`live_shadow_review`)

After running `--write-report`, review the artifacts with the read-only review CLI:

```bash
python -m src.tools.live_shadow_review \
    --report     output/live_shadow_preflight/live_shadow_preflight_report.json \
    --candidates output/live_shadow_preflight/live_shadow_candidates.csv
```

```powershell
python -m src.tools.live_shadow_review `
    --report     output/live_shadow_preflight/live_shadow_preflight_report.json `
    --candidates output/live_shadow_preflight/live_shadow_candidates.csv
```

Outputs a concise operator summary:

```
=== Live Shadow Review ===
  final_status    : PASS
  selected_symbol : SPY
  candidate_count : 1
  pass_count      : 1
  fail_count      : 0

  Blockers:
    (none)

  Warnings:
    (none)

  Suggested actions:
    > All checks passed — review sizing_summary before any live work.

  RESULT: PASS
============================
```

| Exit code | Meaning |
|-----------|---------|
| `0` | `final_status=PASS` and no candidate sizing failures |
| `1` | Any WARN or FAIL in report, or any candidate `sizing_status=FAIL` |

The review tool is strictly read-only: no credentials, no Alpaca calls, no file writes.

### Full recommended pre-flight sequence

```bash
# Step 1: verify credentials and account health
python -m src.tools.live_account_check

# Step 2: shadow preflight — strategy preview + live account state + artifacts
python -m src.tools.live_shadow_preflight \
    --config config/settings.paper.local.yaml \
    --output-dir output/live_shadow_preflight \
    --write-report

# Step 3: review artifacts
python -m src.tools.live_shadow_review \
    --report     output/live_shadow_preflight/live_shadow_preflight_report.json \
    --candidates output/live_shadow_preflight/live_shadow_candidates.csv
```

Stop at any FAIL. WARN requires manual review.

---

## Live Readiness Gate

A single command that runs all five live-readiness checks in order and produces
one GO / NO-GO decision.  Use this instead of running the individual steps by
hand.

**This tool is strictly read-only. It never submits or cancels orders.**

### What it does

Runs the following stages in order (all stages always run — no early exit):

| Stage | What it checks |
|-------|---------------|
| `account_check` | Credentials + live account health (active, unblocked, buying_power > 0) |
| `shadow_preflight` | Single-symbol strategy preview + live account state |
| `shadow_review` | Review of preflight artifacts |
| `symbol_screen` | Multi-symbol live sizing screen across the configured universe |
| `symbol_screen_review` | Review of symbol-screen artifacts |

### Decision

| Decision | Condition |
|----------|-----------|
| `GO` | All five stages PASS |
| `NO-GO` | Any stage is WARN or FAIL |

### What it never does

- Never calls `submit_order` or `cancel_order`.
- Never writes a ledger row.
- Never reads paper credentials (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`).

### Usage

```bash
export ALPACA_LIVE_API_KEY="your-live-api-key"
export ALPACA_LIVE_SECRET_KEY="your-live-secret-key"

python -m src.tools.live_readiness_gate \
    --config     config/settings.paper.local.yaml \
    --output-dir output/live_readiness_gate

# Optional: append a snapshot row to a history CSV
python -m src.tools.live_readiness_gate \
    --config         config/settings.paper.local.yaml \
    --output-dir     output/live_readiness_gate \
    --append-history output/live_readiness_history.csv
```

### Output example

```
=== Live Readiness Gate ===
  account_check           : PASS
  shadow_preflight        : PASS
  shadow_review           : PASS
  symbol_screen           : PASS
  symbol_screen_review    : PASS

  decision: GO
============================
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | `GO` — all stages PASS |
| `1` | `NO-GO` — any stage is WARN or FAIL |

### Audit artifacts

All files are written to `--output-dir`:

| File | Written by |
|------|-----------|
| `live_shadow_preflight_report.json` | Stage 2 (shadow_preflight) |
| `live_shadow_candidates.csv` | Stage 2 (shadow_preflight) |
| `live_shadow_symbol_screen_report.json` | Stage 4 (symbol_screen) |
| `live_shadow_symbol_screen.csv` | Stage 4 (symbol_screen) |
| `live_readiness_gate_report.json` | Gate final summary |

**`live_readiness_gate_report.json`**

| Field | Description |
|-------|-------------|
| `checked_at_utc` | ISO-8601 timestamp of the run |
| `decision` | `GO` or `NO-GO` |
| `stages` | Per-stage status dict (`PASS`, `WARN`, or `FAIL`) |
| `top_blockers` | Up to 5 most important blockers from failing stages |

### Optional history log (`--append-history`)

Passing `--append-history <CSV_PATH>` appends one snapshot row per run to a
CSV file, creating it (with a header) on first use.  The CSV path may be
outside `--output-dir`.

```
checked_at_utc,decision,account_check,shadow_preflight,shadow_review,symbol_screen,symbol_screen_review,top_blockers
2026-05-19T12:00:00+00:00,NO-GO,WARN,FAIL,FAIL,WARN,FAIL,[account_check] buying_power=0 | [shadow_review] ...
```

If the write fails (e.g. permissions error), the gate continues and exits
normally — history logging never causes the gate to fail.

### History review (`live_readiness_history_review`)

After one or more gate runs with `--append-history`, review the trend with
the read-only history review CLI:

```bash
python -m src.tools.live_readiness_history_review \
    --history output/live_readiness_history.csv
```

Output includes: total runs, latest decision, GO/NO-GO counts, per-stage
statuses from the latest run, top 5 recurring blockers, and a plain-English
trend statement (`Latest run is GO.` / `No GO observed yet.` /
`Readiness regressed from GO to NO-GO.`).

Exit codes: `0` if the latest run is GO, `1` otherwise (including missing
or empty file).  No credentials required.  Never writes files.

---

## Live Shadow Symbol Screen

A read-only multi-symbol screening tool that runs the strategy preview and live
sizing checks across several symbols in a single pass. Run this to identify which
symbols from a candidate list are currently safe under live sizing limits.

**This tool is strictly read-only. It never submits or cancels orders.**

### What it does

1. Loads config and resolves live credentials.
2. Creates a live `TradingClient` (`paper=False`) and reads account state, open
   positions, and open orders — once for all symbols.
3. For each symbol, runs the local strategy preview and applies live sizing checks.
4. Prints a per-symbol table and an overall PASS/WARN/FAIL result.
5. Optionally writes `live_shadow_symbol_screen_report.json` and
   `live_shadow_symbol_screen.csv` when `--write-report` is set.

### What it never does

- Never calls `submit_order` or `cancel_order`.
- Never writes a ledger row.
- Never reads paper credentials (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`).

### Symbol universe

Symbols are resolved in this order:

1. `--symbols` CLI flag (comma-separated) — takes precedence when provided.
2. `execution.live_shadow_screen_symbols` in the config YAML — used when `--symbols` is omitted.
3. Default: `["SPY", "QQQ", "IWM", "DIA"]` — used when the field is absent from config.

In all cases symbols are uppercased, whitespace-stripped, and deduplicated (preserving
order). The run fails immediately if the resolved list is empty.

Configure the universe in your YAML:

```yaml
execution:
  live_shadow_screen_symbols:
    - SPY   # S&P 500
    - QQQ   # Nasdaq-100
    - IWM   # Russell 2000
    - DIA   # Dow Jones
    - XLF   # Financials
    - XLE   # Energy
    - XLV   # Health Care
    - TLT   # 20+ Year Treasury
    - GLD   # Gold
```

### Usage

```bash
export ALPACA_LIVE_API_KEY="your-live-api-key"
export ALPACA_LIVE_SECRET_KEY="your-live-secret-key"

# Use symbol universe from config
python -m src.tools.live_shadow_screen_symbols \
    --config     config/settings.paper.local.yaml \
    --output-dir output/live_shadow_symbol_screen

# Override symbol universe from CLI
python -m src.tools.live_shadow_screen_symbols \
    --config     config/settings.paper.local.yaml \
    --output-dir output/live_shadow_symbol_screen \
    --symbols    SPY,QQQ,IWM,DIA
```

Optional flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--symbols` | (omit to use config) | Comma-separated symbols to screen — overrides `execution.live_shadow_screen_symbols` |
| `--write-report` | off | Write JSON report and CSV summary to `--output-dir` |

### Per-symbol result

Each symbol is independently evaluated:

| Result | Condition |
|--------|-----------|
| `PASS` | Account ok, no existing position/order for symbol, ≥1 candidate passes sizing |
| `FAIL` | Account not ok, existing position or open order, no candidates, or all candidates fail sizing |

A position or order in QQQ does **not** block SPY (and vice versa) — each symbol
is checked independently against the live account state.

### Overall result

| Result | Condition |
|--------|-----------|
| `PASS` | Credentials ok, account active and unblocked, ≥1 symbol is `PASS` |
| `WARN` | No symbol passes but account has `warn` state (zero buying_power or portfolio_value) |
| `FAIL` | Credentials fail, account inactive/blocked, or no symbol passes |

### Output example

```
=== Live Shadow Symbol Screen ===
  account_status  : active
  buying_power    : 10000.00
  portfolio_value : 25000.00
  symbols_checked : SPY,QQQ,IWM,DIA

  Symbol    Cands  Pass  Fail  Min Notional  Max Notional  Status  Blocker
  ------    -----  ----  ----  ------------  ------------  ------  -------
  SPY           1     1     0        450.00        450.00  PASS
  QQQ           1     0     1        480.00        480.00  FAIL    all 1 candidate(s) fail sizing ...
  IWM           0     0     0             -             -  FAIL    no candidates
  DIA           1     1     0        420.00        420.00  PASS

  RESULT: PASS (2/4 symbols suitable)
  suitable: SPY, DIA
===================================
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Overall result is `PASS` — ≥1 symbol is suitable under live sizing limits |
| `1` | Overall result is `WARN` or `FAIL` |

### Artifact files (with `--write-report`)

**`live_shadow_symbol_screen_report.json`**

| Field | Description |
|-------|-------------|
| `checked_at_utc` | ISO-8601 timestamp of the run |
| `symbols` | List of symbols checked |
| `overall_result` | `PASS`, `WARN`, or `FAIL` |
| `account` | Snapshot of account state (status, balances, block flags) |
| `symbol_results` | Per-symbol result list (see CSV columns below) |

**`live_shadow_symbol_screen.csv`**

One row per symbol:

| Column | Description |
|--------|-------------|
| `symbol` | Ticker symbol |
| `candidate_count` | Number of buy+market candidates from strategy preview |
| `best_status` | `PASS` or `FAIL` |
| `min_estimated_notional` | Lowest effective notional across all candidates |
| `max_estimated_notional` | Highest effective notional across all candidates |
| `pass_count` | Candidates passing live sizing |
| `fail_count` | Candidates failing live sizing |
| `position_for_symbol` | `True` if a live position exists for this symbol |
| `open_orders_for_symbol` | `True` if a live open order exists for this symbol |
| `blocker_summary` | Human-readable reason when `best_status=FAIL` |

### Artifact review (`live_shadow_screen_review`)

After running `--write-report`, review the artifacts with the read-only review CLI:

```bash
python -m src.tools.live_shadow_screen_review \
    --report output/live_shadow_symbol_screen/live_shadow_symbol_screen_report.json \
    --csv    output/live_shadow_symbol_screen/live_shadow_symbol_screen.csv
```

Outputs a concise operator summary:

```
=== Live Shadow Symbol Screen Review ===
  overall_status   : PASS
  symbols_checked  : SPY, QQQ, IWM, DIA
  suitable_count   : 2/4

  Suitable symbols:
    SPY, DIA

  No-candidate symbols:
    IWM

  Sizing-blocked symbols (notional cap exceeded):
    QQQ

  Suggested actions:
    > Do not raise live_max_notional without a full funding and risk review; ...
    > Rerun on another signal day or expand the strategy universe to generate candidates.

  RESULT: PASS
==========================================
```

| Exit code | Meaning |
|-----------|---------|
| `0` | `overall_result=PASS` and ≥1 suitable symbol |
| `1` | Zero suitable symbols, or `overall_result` is `WARN` or `FAIL` |

The review tool is strictly read-only: no credentials, no Alpaca calls, no file writes.



**`live_shadow_preflight_report.json`**

| Field | Description |
|-------|-------------|
| `checked_at_utc` | ISO-8601 timestamp of the run |
| `selected_symbol` | Symbol checked |
| `final_status` | `PASS`, `WARN`, or `FAIL` |
| `config_path` | Path to the config file used |
| `account_status` | Normalized live account status |
| `trading_blocked` / `account_blocked` | Live account block flags |
| `buying_power` / `portfolio_value` | Live account balances |
| `position_for_symbol` | `true` if a live position exists for the symbol |
| `open_orders_for_symbol` | `true` if a live open order exists for the symbol |
| `candidate_count` | Number of buy+market candidates from strategy preview |
| `sizing_summary` | Summary of the `live_sizing` check (status, limits, computed values) |
| `checks` | Full list of every check with label, status, and detail |

**`live_shadow_candidates.csv`**

One row per candidate intent:

| Column | Description |
|--------|-------------|
| `client_order_id` | Intent ID from strategy |
| `symbol`, `side`, `order_type` | Order parameters |
| `live_sizing_mode` | `"quantity"` or `"notional"` — which sizing path was used |
| `original_quantity` | Raw quantity from strategy |
| `effective_quantity` | After override or notional division is applied |
| `entry_price` | From intent metadata (if present) |
| `effective_notional` | Canonical notional for this sizing mode |
| `estimated_notional` | Alias for `effective_notional` (backward compatibility) |
| `live_max_quantity` | Configured quantity limit |
| `live_max_notional` | Configured portfolio-level notional cap |
| `live_max_order_notional` | Configured per-order notional cap (notional mode) |
| `sizing_status` | `PASS` or `FAIL` per candidate |
| `sizing_reason` | Human-readable explanation |

No ledger rows are ever written. These files are audit artifacts only.


---

## Live Dry-Run Intent Audit (`live_dry_run_intents`)

### What it does

Produces hypothetical live order intent artifacts using the same live
readiness and notional sizing logic as `live_readiness_gate` and
`live_shadow_preflight`, without ever submitting or cancelling an order.

1. Runs all live readiness checks: credentials, account health, candidates,
   live sizing, open positions, open orders.
2. If all checks PASS (GO): writes dry-run intent artifacts and exits 0.
3. If any check fails (NO-GO): writes a summary-only artifact and exits 1.

### What it never does

- Never calls `submit_order` or `cancel_order`.
- Never writes a ledger row.
- Never reads paper credentials (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`).
- Never modifies any live account state.

> **Dry-run only.** Every artifact produced by this tool includes
> `dry_run_only=true` and `submit_allowed=false`. These fields must never be
> removed or overridden. Adding a live order submission path requires its own
> dedicated PR and explicit human sign-off.

### Usage

```bash
export ALPACA_LIVE_API_KEY="your-live-api-key"
export ALPACA_LIVE_SECRET_KEY="your-live-secret-key"

python -m src.tools.live_dry_run_intents \
    --config    config/settings.paper.local.yaml \
    --output-dir output/live_dry_run_intents \
    --symbol    SPY
```

```powershell
$env:ALPACA_LIVE_API_KEY    = "your-live-api-key"
$env:ALPACA_LIVE_SECRET_KEY = "your-live-secret-key"

python -m src.tools.live_dry_run_intents `
    --config     config/settings.paper.local.yaml `
    --output-dir output/live_dry_run_intents `
    --symbol     SPY
```

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | required | Path to YAML config |
| `--output-dir` | required | Directory for output artifacts |
| `--symbol` | `SPY` | Symbol to audit |

### Readiness checks performed

The same checks as `live_shadow_preflight` for the specified symbol:

| Check | PASS | FAIL |
|-------|------|------|
| `config` | Loads and validates | Load or validation error |
| `credentials` | Both `ALPACA_LIVE_*` vars present | Either missing |
| `live_account` | Status `ACTIVE`, not blocked | Inactive, blocked, or API error |
| `candidates` | ≥1 buy+market candidate for symbol | No candidates |
| `live_sizing` | Sizing within all configured limits | Qty/notional/mode invalid |
| `live_position` | No open position for symbol | Existing live position |
| `live_orders` | No open orders for symbol | Existing live open order |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All checks PASS (GO) — dry-run artifacts written |
| `1` | Any check fails (NO-GO) — summary only, no intent rows |

### Output artifacts

Three files are written to `--output-dir`:

**`live_order_intents.csv`** / **`live_order_intents.json`**

One row per candidate intent on GO runs (empty on NO-GO):

| Field | Description |
|-------|-------------|
| `checked_at_utc` | ISO-8601 timestamp |
| `symbol`, `side`, `order_type` | Order parameters |
| `live_sizing_mode` | `"quantity"` or `"notional"` |
| `effective_quantity` | Computed order quantity |
| `effective_notional` | Computed order notional |
| `entry_price` | From intent metadata |
| `live_max_order_notional` | Per-order notional cap |
| `live_max_notional` | Portfolio-level notional cap |
| `readiness_decision` | `GO` or `NO-GO` |
| `dry_run_only` | Always `true` |
| `submit_allowed` | Always `false` |
| `sizing_status` | `PASS` or `FAIL` per intent |
| `sizing_reason` | Human-readable explanation |

**`live_dry_run_summary.json`**

| Field | Description |
|-------|-------------|
| `checked_at_utc` | ISO-8601 timestamp |
| `symbol` | Symbol audited |
| `decision` | `GO` or `NO-GO` |
| `dry_run_only` | Always `true` |
| `submit_allowed` | Always `false` |
| `intent_count` | Number of intent rows written |
| `pass_count` | Intents passing sizing |
| `fail_count` | Intents failing sizing |
| `top_blockers` | Up to 5 blocker messages |

No live orders are ever submitted. These files are audit artifacts only.
