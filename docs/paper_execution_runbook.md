# Paper Execution Runbook

> **WARNING: This runbook is for the project owner only.**
> Run this only in a controlled local environment against an Alpaca **paper** account.
> **No manual run against a real Alpaca paper account has been completed yet.**
> Follow each step exactly and stop at any unexpected result.

---

## 1. Current State

The paper execution path uses a **two-phase preview/selection flow** wired in `main.py`
and is mock-tested only.  A manual run against a real Alpaca paper account has not been
performed yet.

### Phase 1 — Preview (default)

Set `execution.paper_preview_only: true` (or omit the field; it defaults to `true`).

`main.py` runs preflight + engine, writes `paper_candidate_intents.csv` with all generated
candidate intents, then exits cleanly without submitting any order.  Multiple generated
intents are allowed in this phase.

### Phase 2 — Submit selected intent

Set `execution.paper_preview_only: false` and
`execution.paper_selected_client_order_id: <id>` to the `client_order_id` you chose
from the preview CSV.

`main.py` selects exactly that one intent, applies `paper_order_quantity_override` if set,
validates safety constraints, submits exactly one order, and writes full audit artifacts.

### Hard constraints enforced by `main.py` (fail closed — raises before `submit_order`):

| Constraint | Behaviour on violation |
|------------|------------------------|
| `paper_preview_only=false` requires non-empty `paper_selected_client_order_id` | `RuntimeError` |
| Selected `client_order_id` must match exactly one candidate | `RuntimeError` |
| Symbol of selected intent must be `SPY` | `RuntimeError` |
| `order_type` of selected intent must be `market` | `RuntimeError` |
| `quantity` of selected intent must be `<= 1` (after override) | `RuntimeError` |
| `client_order_id` must be non-empty | `RuntimeError` |
| **Selected buy intent with existing nonzero position for that symbol** | `RuntimeError` — no order submitted |
| `OrderResult` must carry `client_order_id` | `RuntimeError` after order |
| `order_reconciliation.json` must be written | `RuntimeError` if absent |
| Reconciliation `overall_status` must be `PASS` or `N/A` | `RuntimeError` on mismatch |

Non-selected candidate intents are visible in `paper_candidate_intents.csv` but their
content does not block submission of the selected intent.

### Position-aware safety rule

**Preview-only mode** (`paper_preview_only: true`) always runs the engine and writes
`paper_candidate_intents.csv` even when the paper account already holds a position in
the target symbol.  No order is ever submitted in preview mode.

**Submit mode** (`paper_preview_only: false`) enforces an additional check before calling
`submit_order`:

- If the selected intent is a **buy** and the Alpaca paper account already holds a
  **nonzero position** in that symbol, `main.py` raises `RuntimeError` before any
  order reaches Alpaca.  The error message confirms no order was submitted.
- Positions in non-target symbols are ignored.
- A position with `qty = 0` is treated as no position and does not block the buy.

**To submit another buy after a prior fill:**

1. Log in to <https://app.alpaca.markets> (paper account).
2. Close / liquidate the existing position manually from the dashboard.
3. Confirm the position is gone (qty = 0 or position absent).
4. Re-run `main.py` in submit mode.

The close/flatten path (see below) allows you to explicitly submit a sell order to close a position through a separate two-phase flow.  It is **not automatic** — it must be enabled and a specific candidate must be selected.

**Submit phase must run during regular market hours (09:30–16:00 ET, Mon–Fri).**
The `_ensure_market_hours` guard in `AlpacaBrokerAdapter.submit_order` raises outside
these hours.  Running outside RTH with a real intent will raise `RuntimeError` before
the order reaches Alpaca.

---

## 2. Close/Flatten Flow (paper_close_positions_enabled)

> **WARNING — SPY only, sell market only, max 1 share in first implementation.**
> This flow is separate from the buy-submit flow.  It does not run the backtest engine.
> Enable it only when you have an existing SPY paper position you want to close.

The close/flatten path is a distinct two-phase flow triggered by
`execution.paper_close_positions_enabled: true`.  It is **mutually exclusive**
with the buy-submit flow: when `paper_close_positions_enabled=true` the bot skips
the buy-submit path entirely.

### Phase A — Close preview (default)

Set `execution.paper_close_preview_only: true` (or omit; it defaults to `true`).

`main.py` runs preflight, calls `get_positions()` through the preflight result,
generates SPY sell-market close candidates for any SPY position with `qty > 0`,
and writes `paper_close_candidate_intents.csv`.  No order is submitted.
Return cleanly.

### Phase B — Close submit

Set `execution.paper_close_preview_only: false` and
`execution.paper_selected_close_client_order_id: <id>` to the `client_order_id`
you chose from the close preview CSV.

`main.py` selects exactly that one candidate, optionally applies
`paper_close_quantity_override` (only `1.0` accepted), validates all safety
constraints, submits exactly one sell-market order, and writes audit artifacts.

### Hard constraints enforced for close/flatten (fail closed — raises before `submit_order`):

| Constraint | Behaviour on violation |
|------------|------------------------|
| `paper_close_positions_enabled=false` (default) | Close path skipped entirely |
| `paper_close_preview_only=false` requires non-empty `paper_selected_close_client_order_id` | `RuntimeError` |
| Selected close ID must match exactly one candidate | `RuntimeError` |
| Symbol of selected candidate must be `SPY` | `RuntimeError` |
| `order_type` must be `market` | `RuntimeError` |
| `side` must be `sell` — no buy orders in close flow | `RuntimeError` |
| `quantity` must be `<= 1` (after override) | `RuntimeError` |
| `quantity` must be `<= current position qty` — no shorting | `RuntimeError` |
| `client_order_id` must be non-empty | `RuntimeError` |
| `OrderResult` must carry `client_order_id` | `RuntimeError` after order |
| `order_reconciliation.json` must be written | `RuntimeError` if absent |
| Reconciliation `overall_status` must be `PASS` or `N/A` | `RuntimeError` on mismatch |
| `cancel_order` is never called | By design |

### Config for Phase A — Close preview

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_close_positions_enabled: true
  paper_close_preview_only: true    # default; omitting also defaults to true
```

Run `python -m src.main`.  Open `output/paper_close_candidate_intents.csv` and note
the `client_order_id` of the candidate you want to submit.

### Config for Phase B — Close submit

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_close_positions_enabled: true
  paper_close_preview_only: false
  paper_selected_close_client_order_id: "BC-20260513154235-SPY"  # replace with your ID
  paper_close_quantity_override: 1   # required if position qty > 1
```

**Why `paper_close_quantity_override: 1` is needed:**
If the position holds more than 1 share (e.g. 5 shares), the close candidate will
have `quantity=5` which exceeds the `<= 1` safety limit.  Setting
`paper_close_quantity_override: 1` overrides the submitted quantity to `1.0`.
The original position quantity is preserved in `paper_close_intent_audit.csv`
and in the intent's metadata.

**Close artifact files written:**

| File | Contents |
|------|----------|
| `paper_close_candidate_intents.csv` | All close candidates (always written) |
| `paper_close_intent_audit.csv` | The selected/submitted close intent (Phase B only) |
| `order_intents.csv` | The submitted intent (Phase B only, via ReportGenerator) |
| `order_results.csv` | The `OrderResult` from Alpaca (Phase B only) |
| `order_reconciliation.json` | Reconciliation; `overall_status` must be `PASS` |

---

## 3. Required Environment Variables

Set these in your local shell only.  **Never commit credentials.**

```bash
export ALPACA_API_KEY="your-paper-api-key"
export ALPACA_SECRET_KEY="your-paper-secret-key"

# Optional — only needed to override the default paper endpoint
export ALPACA_PAPER_BASE_URL="https://paper-api.alpaca.markets"
```

Credentials must come from your Alpaca **paper trading** account dashboard at
<https://app.alpaca.markets>.  Never use live account credentials here.

---

## 4. Required Config (`config/settings.yaml`)

### Valid Config Combinations

`load_config()` runs `validate_paper_config()` immediately after parsing.
Invalid or contradictory combinations raise `ValueError` before any broker
or execution logic runs.

| Combination | paper_preview_only | paper_close_positions_enabled | paper_close_preview_only | Valid? |
|-------------|-------------------|-------------------------------|--------------------------|--------|
| **A. Buy preview** (default) | `true` | `false` | `true` | ✅ |
| **B. Buy submit** | `false` + selected ID set | `false` | `true` | ✅ |
| **C. Close preview** | `true` | `true` | `true` | ✅ |
| **D. Close submit** | `true` | `true` | `false` + close selected ID set | ✅ |
| Buy submit + close enabled | `false` | `true` | any | ❌ `ValueError` |
| Buy submit without selected ID | `false` | `false` | any | ❌ `ValueError` |
| Close submit without close selected ID | `true` | `true` | `false` | ❌ `ValueError` |
| `paper_order_quantity_override` ≠ 1.0 | any | any | any | ❌ `ValueError` |
| `paper_close_quantity_override` ≠ 1.0 | any | any | any | ❌ `ValueError` |

---

### Combination A — Buy preview (inspect candidates, no order sent)

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_preview_only: true          # default; omitting also defaults to true
```

Run `python -m src.main`.  The process exits cleanly after writing
`output/paper_candidate_intents.csv`.  Open that file and note the
`client_order_id` of the intent you want to submit.

### Combination B — Buy submit (selected intent)

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_preview_only: false
  paper_selected_client_order_id: "BT-000001"   # replace with your chosen ID
  paper_order_quantity_override: 1              # needed if strategy generates > 1 share
```

**Why `paper_order_quantity_override: 1` is needed:**

The strategy's position-sizing logic calculates a full position (e.g. 139 shares of SPY based on
allocated capital).  The paper safety gate rejects any selected intent with `quantity > 1`.
Setting `paper_order_quantity_override: 1` tells `main.py` to replace the selected intent's
quantity with `1.0` *before* the safety validation runs.  The original quantity is preserved in
`paper_intent_audit.csv` and in the intent's metadata so the override is fully auditable.

**Important:**
- `paper_preview_only` defaults to `true` — omitting the field is safe (no order will be sent).
- `paper_selected_client_order_id` is only used when `paper_preview_only: false`.
- Override default (`null` / not set) is fail-closed — a selected intent with `quantity > 1` raises immediately.
- Only `1.0` is accepted for override; values `<= 0` or `> 1` raise `RuntimeError` before any order is sent.
- The override does **not** bypass symbol, order-type, or `client_order_id` checks.

All other config fields (symbols, strategy params, risk limits) follow the same
schema as for backtest mode.  Review them carefully before any run.

### Combination C — Close preview (inspect existing positions, no order sent)

> **First implementation: SPY only, sell market, max 1 share.**

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_close_positions_enabled: true
  paper_close_preview_only: true    # default; omitting also defaults to true
```

Run `python -m src.main`.  The process exits cleanly after writing
`output/paper_close_candidate_intents.csv`.  Open that file and note the
`client_order_id` of the close candidate you want to submit.

### Combination D — Close submit (selected close intent)

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_close_positions_enabled: true
  paper_close_preview_only: false
  paper_selected_close_client_order_id: "BC-20260513154235-SPY"  # replace with your ID
  paper_close_quantity_override: 1   # required if position qty > 1
```

**Config validation (`validate_paper_config`) raises `ValueError` immediately for:**
- `paper_preview_only: false` while `paper_close_positions_enabled: true`
  (buy-submit and close are mutually exclusive)
- `paper_preview_only: false` without `paper_selected_client_order_id` set
- `paper_close_preview_only: false` without `paper_selected_close_client_order_id` set
- `paper_order_quantity_override` or `paper_close_quantity_override` set to any value other than `1.0`

---

## 5. Pre-Run Checklist

Complete every item before proceeding:

| # | Check | Confirmed |
|---|-------|-----------|
| 1 | I am using Alpaca **paper** account credentials, not live | ☐ |
| 2 | `execution.mode="live"` does not exist in `_VALID_EXECUTION_MODES` | ☐ |
| 3 | The paper account has **no open positions** in any target symbol | ☐ |
| 4 | `symbols:` in `settings.yaml` is `["SPY"]` only (constraint: SPY only) | ☐ |
| 5 | Daily loss limit is confirmed (`daily_loss_limit_pct`, `daily_loss_action`) | ☐ |
| 6 | Full test suite passes without credentials: `pytest` | ☐ |
| 7 | `paper_trading_enabled: true` is set in `settings.yaml` | ☐ |
| 8 | *Phase 1 only:* `paper_preview_only: true` (or omit) is set | ☐ |
| 9 | *Phase 2 only:* `paper_preview_only: false` and `paper_selected_client_order_id` are set | ☐ |
| 10 | *Phase 2 only:* Current time is within regular market hours (09:30–16:00 ET, Mon–Fri) | ☐ |
| 11 | *Phase 2 only:* Selected intent has `symbol=SPY`, `order_type=market`, `quantity <= 1` (or override set) | ☐ |

---

## 6. Offline Smoke Check (no credentials, no network)

Before running against a real paper account, validate the entire paper execution
infrastructure offline using the built-in smoke check utility.

```bash
python -m src.tools.paper_smoke_check \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check
```

The utility:
1. Loads and validates `config/settings.paper.local.yaml` via `validate_paper_config`.
2. Simulates the buy preview path with a fake broker (no credentials) and a fake
   engine (no market data download), writing `paper_candidate_intents.csv`.
3. Simulates the close preview path with a fake broker holding 1 SPY share,
   writing `paper_close_candidate_intents.csv`.
4. *(Optional)* Replays reconciliation on existing artifacts:

```bash
python -m src.tools.paper_smoke_check \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check \
    --replay-dir output/paper_submit_BT000035
```

Expected output on success:

```
=== Paper Smoke Check ===
  [PASS] config_load_and_validate  (mode=paper paper_trading_enabled=True)
  [PASS] buy_preview  (1 candidate(s) written → paper_candidate_intents.csv)
  [PASS] close_preview  (1 candidate(s) written → paper_close_candidate_intents.csv)

  RESULT: PASS
=========================
```

Exit code is `0` on full pass, `1` on any failure.

**Safety guarantees:**
- `submit_order` and `cancel_order` are never called (raise immediately if reached).
- No Alpaca credentials are read from the environment.
- No network access is required.

---

## 7. Safe Manual Flow

Follow these steps in order.  Stop at any step that produces an unexpected result.

### Step 1 — Run tests without credentials

```bash
unset ALPACA_API_KEY
unset ALPACA_SECRET_KEY
pytest
```

All tests must pass.  If any test fails, do not proceed.

### Step 2 — Export paper credentials locally

```bash
export ALPACA_API_KEY="your-paper-api-key"
export ALPACA_SECRET_KEY="your-paper-secret-key"
```

### Step 3 — Phase 1: Preview run (safe — no order sent)

Set `settings.yaml`:
```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_preview_only: true
```

```bash
python -m src.main
```

Inspect the log output.  Confirm:
- `"Paper trading preflight passed"` appears in the log.
- `account_status=ACTIVE`.
- `symbols=` lists your target symbols.
- `"Paper preview-only mode: N candidate intent(s) written"` appears.
- No `RuntimeError` about blocked accounts or conflicting positions.

Open `output/paper_candidate_intents.csv`.  Review every row:
- Note `client_order_id`, `symbol`, `side`, `quantity`, `reason`.
- Choose the one intent you want to submit.  Record its `client_order_id`.

### Step 4 — Inspect account and positions

Log into <https://app.alpaca.markets> (paper account) and verify:
- Account status is ACTIVE.
- No open positions in target symbols.
- Buying power is sufficient for expected order sizes.

### Step 5 — Phase 2: Submit selected intent (must be within market hours)

Update `settings.yaml`:
```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_preview_only: false
  paper_selected_client_order_id: "BT-000001"   # replace with your chosen ID
  paper_order_quantity_override: 1              # set if strategy generates > 1 share
```

```bash
python -m src.main
```

Monitor the process output.  Watch for:
- `"Paper trading preflight passed"` — confirms account is clear.
- `"Paper quantity override: ..."` — if override is applied.
- `"Paper execution: submitting intent ..."` — confirms correct intent selected.
- `OrderResult` status log line.
- Reconciliation result in the log.

Check the output directory for audit artifacts (see section 7).

---

## 8. Emergency Stop

If at any point the process behaves unexpectedly:

1. **Stop the process immediately** (`Ctrl+C` or `kill <pid>`).
2. Open the Alpaca paper dashboard at <https://app.alpaca.markets>.
3. Navigate to **Orders** and cancel any open paper orders manually.
4. Do **not** switch to your live Alpaca account.
5. Inspect `order_intents.csv`, `order_results.csv`, and `order_reconciliation.json`
   in the output directory for the session.
6. Do not restart until you understand what happened.

---

## 9. Expected Output Artifacts

After a successful paper run the output directory will contain:

| File | Contents |
|------|----------|
| `order_intents.csv` | Every `OrderIntent` generated by the strategy, with `client_order_id` |
| `order_results.csv` | Every `OrderResult` returned by `submit_order()`, with status |
| `order_reconciliation.json` | Intent/result match summary; `overall_status` must be `"PASS"` |
| `backtest_report.md` (or paper equivalent) | Summary metrics and trade log |

If `order_reconciliation.json` reports `overall_status: "WARN"` or any unmatched
orders, investigate before running again.

---

## 10. What Is Still Blocked or Constrained

The following are explicitly **not** supported and will raise immediately:

| Action | Behaviour |
|--------|-----------|
| `paper=False` constructor argument | `ValueError` — live trading blocked |
| `execution.mode="live"` | `ValueError` at config load — not a valid mode |
| `paper_trading_enabled=false` in config | `NotImplementedError` before adapter creation |
| Missing `ALPACA_API_KEY` or `ALPACA_SECRET_KEY` | `RuntimeError` before any network call |
| `submit_order` outside market hours | `RuntimeError` before request is sent |
| `submit_order` without `client_order_id` | `ValueError` from `_validate_order_intent` |
| `paper_preview_only=false` with no `paper_selected_client_order_id` | `RuntimeError` before `submit_order` |
| `paper_selected_client_order_id` matches 0 candidates | `RuntimeError` before `submit_order` |
| `paper_selected_client_order_id` matches >1 candidates | `RuntimeError` before `submit_order` |
| Selected intent has symbol other than `SPY` | `RuntimeError` in `main.py` before `submit_order` |
| Selected intent has `order_type` other than `market` | `RuntimeError` in `main.py` before `submit_order` |
| Selected intent has `quantity > 1` (no override set) | `RuntimeError` in `main.py` before `submit_order` |
| `paper_order_quantity_override` set to `> 1` or `<= 0` | `RuntimeError` in `main.py` — only 1.0 accepted |
| `paper_close_positions_enabled=false` (default) | Close path skipped; buy-submit path runs |
| `paper_close_preview_only=false` with no `paper_selected_close_client_order_id` | `RuntimeError` before `submit_order` |
| Close candidate selected with side other than `sell` | `RuntimeError` in `main.py` before `submit_order` |
| Close submitted quantity `> current position qty` | `RuntimeError` — no shorting |
| `paper_close_quantity_override` set to `> 1` or `<= 0` | `RuntimeError` — only 1.0 accepted |
| No SPY position exists during close preview | Empty `paper_close_candidate_intents.csv`; no error |
| `OrderResult` returned without `client_order_id` | `RuntimeError` in `main.py` after `submit_order` |
| `order_reconciliation.json` not written | `RuntimeError` in `main.py` — internal error |
| Reconciliation `overall_status` not `PASS`/`N/A` | `RuntimeError` — execution halts |

---

## 11. References

- [Alpaca Adapter Design](alpaca_adapter_design.md) — full adapter specification
- [Paper Execution Readiness](paper_execution_readiness.md) — safety gates and merge checklist
- [Paper Trading Readiness Checklist](paper_trading_readiness.md) — system-level checklist
