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

There is no automatic close or sell logic in this bot.  Manual action is required.

**Submit phase must run during regular market hours (09:30–16:00 ET, Mon–Fri).**
The `_ensure_market_hours` guard in `AlpacaBrokerAdapter.submit_order` raises outside
these hours.  Running outside RTH with a real intent will raise `RuntimeError` before
the order reaches Alpaca.

---

## 2. Required Environment Variables

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

## 3. Required Config (`config/settings.yaml`)

### Phase 1 — Preview run (inspect candidates, no order sent)

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_preview_only: true          # default; omitting this field also defaults to true
```

Run `python -m src.main`.  The process exits cleanly after writing
`output/paper_candidate_intents.csv`.  Open that file and note the
`client_order_id` of the intent you want to submit.

### Phase 2 — Submit selected intent

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

---

## 4. Pre-Run Checklist

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

## 5. Safe Manual Flow

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

## 6. Emergency Stop

If at any point the process behaves unexpectedly:

1. **Stop the process immediately** (`Ctrl+C` or `kill <pid>`).
2. Open the Alpaca paper dashboard at <https://app.alpaca.markets>.
3. Navigate to **Orders** and cancel any open paper orders manually.
4. Do **not** switch to your live Alpaca account.
5. Inspect `order_intents.csv`, `order_results.csv`, and `order_reconciliation.json`
   in the output directory for the session.
6. Do not restart until you understand what happened.

---

## 7. Expected Output Artifacts

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

## 8. What Is Still Blocked or Constrained

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
| `OrderResult` returned without `client_order_id` | `RuntimeError` in `main.py` after `submit_order` |
| `order_reconciliation.json` not written | `RuntimeError` in `main.py` — internal error |
| Reconciliation `overall_status` not `PASS`/`N/A` | `RuntimeError` — execution halts |

---

## 9. References

- [Alpaca Adapter Design](alpaca_adapter_design.md) — full adapter specification
- [Paper Execution Readiness](paper_execution_readiness.md) — safety gates and merge checklist
- [Paper Trading Readiness Checklist](paper_trading_readiness.md) — system-level checklist
