# Paper Execution Runbook

> **WARNING: This runbook is for the project owner only.**
> Run this only in a controlled local environment against an Alpaca **paper** account.
> **No manual run against a real Alpaca paper account has been completed yet.**
> Follow each step exactly and stop at any unexpected result.

---

## 1. Current State

The minimal paper execution path is wired in `main.py` and is mock-tested only.
A manual run against a real Alpaca paper account has not been performed yet.

### Hard constraints enforced by `main.py` (fail closed — raises before `submit_order`):

| Constraint | Behaviour on violation |
|------------|------------------------|
| Symbol must be `SPY` | `RuntimeError` |
| `order_type` must be `market` | `RuntimeError` |
| `quantity` must be `<= 1` share | `RuntimeError` |
| `client_order_id` must be non-empty | `RuntimeError` |
| At most **1** intent generated per run | `RuntimeError` |
| `OrderResult` must carry `client_order_id` | `RuntimeError` after order |
| `order_reconciliation.json` must be written | `RuntimeError` if absent |
| Reconciliation `overall_status` must be `PASS` or `N/A` | `RuntimeError` on mismatch |

**First manual run must be during regular market hours (09:30–16:00 ET, Mon–Fri).**
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

Add or confirm these fields under `execution:`:

```yaml
execution:
  mode: paper
  paper_trading_enabled: true
  paper_order_quantity_override: 1
```

**Why `paper_order_quantity_override: 1` is needed for the first manual run:**

The strategy's position-sizing logic calculates a full position (e.g. 139 shares of SPY based on
allocated capital).  The paper safety gate rejects any intent with `quantity > 1`.  Without the
override, the run fails immediately with:

```
RuntimeError: Paper safety constraint violated for intent 'BT-000001': quantity=139.0 (must be <= 1)
```

Setting `paper_order_quantity_override: 1` tells `main.py` to replace the intent's quantity with
`1.0` *before* the safety validation runs.  The original quantity is preserved in
`paper_intent_audit.csv` and in the intent's metadata so the override is fully auditable.

**Important:**
- Default (`null` / not set) is fail-closed — any intent with `quantity > 1` raises immediately.
- Only `1.0` is accepted; values `<= 0` or `> 1` raise `RuntimeError` before any order is sent.
- The override does **not** bypass symbol, order-type, or `client_order_id` checks.
- Remove or unset this field once position sizing is adjusted to produce `quantity <= 1` natively.

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
| 4 | Current time is within regular market hours (09:30–16:00 ET, Mon–Fri) | ☐ |
| 5 | `symbols:` in `settings.yaml` is `["SPY"]` only (constraint: SPY only) | ☐ |
| 6 | Position sizing will produce `quantity <= 1` share, **OR** `paper_order_quantity_override: 1` is set (see § 3) | ☐ |
| 7 | Strategy will produce at most 1 intent per run (constraint: max 1 order) | ☐ |
| 8 | Daily loss limit is confirmed (`daily_loss_limit_pct`, `daily_loss_action`) | ☐ |
| 9 | Full test suite passes without credentials: `pytest` | ☐ |
| 10 | `paper_trading_enabled: true` is set in `settings.yaml` | ☐ |

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

### Step 3 — Run preflight only (safe, no orders)

Until the final execution wiring is merged, running `main.py` in paper mode only
runs `preflight_check` and then raises before any order is submitted.  Use this step
to confirm your credentials and account state are correct:

```bash
python -m src.main
# Expected: NotImplementedError after "Paper trading preflight passed"
```

Inspect the log output.  Confirm:
- `"Paper trading preflight passed"` appears in the log.
- `account_status=ACTIVE`.
- `symbols=` lists your target symbols.
- No `RuntimeError` about blocked accounts or conflicting positions.

### Step 4 — Inspect account and positions

Log into <https://app.alpaca.markets> (paper account) and verify:
- Account status is ACTIVE.
- No open positions in target symbols.
- Buying power is sufficient for expected order sizes.

### Step 5 — Proceed to paper execution (future, once wiring is approved)

Once the final execution PR is merged:

```bash
python -m src.main
```

Monitor the process output.  Watch for:
- `"Paper trading preflight passed"` — confirms account is clear.
- `OrderResult` status entries per submitted intent.
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
| Intent with symbol other than `SPY` | `RuntimeError` in `main.py` before `submit_order` |
| Intent with `order_type` other than `market` | `RuntimeError` in `main.py` before `submit_order` |
| Intent with `quantity > 1` (no override set) | `RuntimeError` in `main.py` before `submit_order` |
| `paper_order_quantity_override` set to `> 1` or `<= 0` | `RuntimeError` in `main.py` — only 1.0 accepted |
| More than 1 intent generated in one run | `RuntimeError` in `main.py` — no order submitted |
| `OrderResult` returned without `client_order_id` | `RuntimeError` in `main.py` after `submit_order` |
| `order_reconciliation.json` not written | `RuntimeError` in `main.py` — internal error |
| Reconciliation `overall_status` not `PASS`/`N/A` | `RuntimeError` — execution halts |

---

## 9. References

- [Alpaca Adapter Design](alpaca_adapter_design.md) — full adapter specification
- [Paper Execution Readiness](paper_execution_readiness.md) — safety gates and merge checklist
- [Paper Trading Readiness Checklist](paper_trading_readiness.md) — system-level checklist
