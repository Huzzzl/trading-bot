# Alpaca Adapter Design

This document specifies how `AlpacaBrokerAdapter` is designed and implemented
for paper trading.  `execution.mode = "paper"` with
`execution.paper_trading_enabled = false` (the default) raises
`NotImplementedError` before any adapter is created.
`execution.paper_trading_enabled = true` runs preflight checks only; order
execution is still blocked until every item in the go/no-go checklist at the
bottom of this file is satisfied.

See also:
- [Paper Trading Readiness Checklist](paper_trading_readiness.md)
- [Paper Execution Readiness](paper_execution_readiness.md) — safety gates and final wiring checklist

---

## 1. Current Status

| Item | Status |
|------|--------|
| `AlpacaBrokerAdapter` class | Implemented (all five public methods + helpers); mocked tests only |
| `alpaca-py` SDK dependency | Added to `requirements.txt` |
| API keys | **Not present** — resolved lazily from env vars at first broker call |
| `execution.paper_trading_enabled` | `False` by default (fail-closed); `True` runs preflight only |
| `execution.mode = "paper"` + flag disabled | Raises `NotImplementedError` before adapter creation |
| `execution.mode = "paper"` + flag enabled | Runs `preflight_check`; raises before order execution |
| Live trading | **Out of scope — permanently blocked** |

---

## 2. Scope

**Paper trading only.**  This adapter targets the Alpaca Paper Trading
endpoint exclusively.

- `paper=True` is hardcoded as the constructor default and must be enforced
  at submission time.
- Live trading (`paper=False`) will never be wired into this codebase without
  an explicit, separate design review and a dedicated live-trading readiness
  checklist.
- The adapter is not activated by the backtest engine.  It is only reachable
  when `execution.mode == "paper"` and `execution.paper_trading_enabled == true`.

---

## 3. Environment Variables

API credentials are read **only when `execution.mode == "paper"`** and only
immediately before the first broker call.  They are never read during backtest
mode, import time, or test collection.

| Variable | Purpose | Required |
|----------|---------|---------|
| `ALPACA_API_KEY` | Alpaca API key ID | Yes |
| `ALPACA_SECRET_KEY` | Alpaca secret key | Yes |
| `ALPACA_PAPER_BASE_URL` | Paper trading base URL | No (default: `https://paper-api.alpaca.markets`) |

Rules:
- Variables are read with `os.environ` (no fallback to config files or
  `settings.yaml`).
- If `ALPACA_API_KEY` or `ALPACA_SECRET_KEY` is absent or empty the adapter
  raises `RuntimeError` before any network call is made (**fail closed**).
- Values are never logged, stored in instance attributes accessible outside
  the adapter, or written to any output file.

---

## 4. Safety Rules

1. **Never read API keys unless `execution.mode == "paper"`.**
   The adapter constructor does not read env vars.  Credentials are resolved
   lazily inside `_get_client()`, which is called only by the public interface
   methods.

2. **Never place live orders.**  `paper=True` is passed to the Alpaca client
   at construction time and cannot be overridden at runtime.

3. **Require `paper=True`.**  If the constructor is ever called with
   `paper=False`, it raises `ValueError` immediately.

4. **Fail closed if keys are missing.**  Missing or empty credentials →
   `RuntimeError` before any network call.

5. **No trading outside regular market hours.**  `submit_order` checks the
   current time (America/New_York) against the regular trading window
   (09:30–16:00 ET, Monday–Friday).  Orders submitted outside that window
   are rejected with `RuntimeError` before they reach Alpaca.

6. **No secrets in logs.**  The adapter logs order IDs and statuses but
   never logs API keys, secret keys, or account numbers.

---

## 5. Order Mapping: OrderIntent → Alpaca Request

For the first implementation only **market orders** are supported.

| `OrderIntent` field | Alpaca request field | Notes |
|---------------------|---------------------|-------|
| `symbol` | `symbol` | Passed through unchanged |
| `side` | `side` | `"buy"` / `"sell"` — matches Alpaca enum |
| `quantity` | `qty` | Integer shares (fractional shares deferred) |
| `order_type` | `type` | Only `"market"` is sent; other types raise `NotImplementedError` |
| `client_order_id` | `client_order_id` | **Required — non-empty.** `_validate_order_intent` raises `ValueError` if absent or blank. |
| `time_in_force` | `time_in_force` | Hard-coded to `"day"` |
| `limit_price` | — | Ignored for market orders; raises `NotImplementedError` for limit orders |
| `stop_price` | — | Ignored for market orders; raises `NotImplementedError` for stop orders |

---

## 6. OrderResult Mapping: Alpaca Response → OrderResult

| Alpaca field | `OrderResult` field | Notes |
|--------------|-------------------|-------|
| `id` | `order_id` | Alpaca-assigned UUID |
| `symbol` | `symbol` | |
| `side` | `side` | |
| `qty` | `quantity` | |
| `client_order_id` | `client_order_id` | Echoed back from the request; falls back to `intent.client_order_id` if absent in response |
| `submitted_at` | `submitted_at` | Parsed to `pd.Timestamp` (UTC-aware) |
| `filled_at` | `filled_at` | `None` if not yet filled |
| `filled_avg_price` | `filled_price` | `None` if not yet filled |
| `reason` | `reason` | Echoed from the originating `OrderIntent` |

### Status normalisation

| Alpaca status | `OrderResult.status` |
|---------------|---------------------|
| `new`, `pending_new`, `accepted`, `accepted_for_bidding` | `"accepted"` |
| `filled`, `partially_filled` | `"filled"` (see partial-fill note) |
| `canceled`, `expired`, `replaced` | `"cancelled"` |
| `rejected`, `stopped`, `suspended`, `calculated` | `"rejected"` |

**Partial fills** are mapped to `"filled"` for the first implementation.
The filled quantity (`filled_qty`) is stored in `metadata["filled_qty"]`
so callers can detect partial fills.

---

## 7. Reconciliation

The `_build_reconciliation()` logic in `ReportGenerator` already supports
ID-based matching.  The Alpaca adapter must preserve `client_order_id`
through the full round-trip so reconciliation can match by ID rather than
position.

Rules:
- Every `OrderIntent` produced by `BacktestEngine` carries a `BT-{seq:06d}`
  `client_order_id`.  The adapter passes this to Alpaca unchanged.
- Alpaca echoes `client_order_id` in the response; the adapter copies it into
  `OrderResult.client_order_id`.
- Reconciliation matches by `client_order_id` when all intents and results
  carry IDs (the default after PR #18).
- Out-of-order results are handled correctly because matching is set-based,
  not positional.
- Missing IDs (Alpaca did not echo `client_order_id`) → `missing_ids_warn=True`
  in `order_reconciliation.json`.
- Duplicate IDs → the later result overwrites the earlier in the result map
  and a warning is logged.

---

## 8. Startup Preflight Checks

Before the first order is submitted, the adapter must be validated via
`preflight_check(symbols)`.  **This is not automatic — it must be called
explicitly by `main.py`.**  There is no cached preflight state; the caller
is responsible for ensuring `preflight_check` ran successfully before any
`submit_order` call.

### 8.1 Account check (inside `preflight_check`)
- Calls `get_account()` → `_account_response_to_dict()`.
- Verifies `account["status"] == "ACTIVE"`.
- Verifies `account["trading_blocked"] is False`.
- Verifies `account["account_blocked"] is False`.
- Raises `RuntimeError` if any check fails.

### 8.2 Position check (inside `preflight_check`)
- Calls `get_positions()` → list of `_position_response_to_dict()` results.
- If any open position symbol overlaps the normalised target symbol list,
  raises `RuntimeError("Unexpected open positions for target symbols")`.
- Positions in unrelated symbols are silently ignored (not logged as WARNING
  in the current implementation).

### 8.3 What is NOT automatic
- Preflight does **not** run at adapter construction time.
- Preflight does **not** run automatically before each `submit_order` call.
- There is **no** cached preflight result stored on the adapter instance.

---

## 9. Error Handling

> **Note:** Only the behaviours marked **Implemented** are present in the
> current code.  Items marked **Future** are design targets for a later PR.

| Scenario | Behaviour | Status |
|----------|-----------|--------|
| Missing / expired credentials | Raise `RuntimeError` before any network call (no key values logged) | **Implemented** |
| Orders submitted outside market hours | Raise `RuntimeError` before any network call | **Implemented** |
| Order rejected by Alpaca | Return `OrderResult(status="rejected")`; `metadata["raw_status"]` preserved | **Implemented** |
| Partial fill | Return `OrderResult(status="filled")`; `filled_qty` in `metadata["filled_qty"]`; `metadata["partial_fill"] = True` | **Implemented** |
| Unknown Alpaca status string | Raise `ValueError` from `_normalize_status()` | **Implemented** |
| Client missing `submit_order`/`create_order` | Raise `NotImplementedError` with explicit message | **Implemented** |
| Network error (timeout / connection refused) | Retry up to 3 times with exponential back-off (1 s, 2 s, 4 s); raise `RuntimeError` | **Future** |
| HTTP 429 rate limit | Respect `Retry-After` header; sleep and retry; log `WARNING` | **Future** |
| Submit timeout (no response within N s) | Cancel the order and raise `RuntimeError` | **Future** |

---

## 10. Testing Plan

### 10.1 Unit tests (no real API calls) — current state

- All tests mock the Alpaca SDK (`alpaca.trading.client.TradingClient`) or
  use injected fake clients.
- CI never makes real network calls; the full suite passes without
  `ALPACA_API_KEY` set.
- Tests cover (859 passing as of this writing):
  - `submit_order` happy path (market buy, market sell) via injected client
  - `submit_order` via lazy `_get_client()` factory (mocked SDK)
  - Status normalisation for all 14 Alpaca status strings
  - `client_order_id` required — `ValueError` if absent or blank
  - `client_order_id` round-trip through `_order_response_to_result`
  - Partial fill detection (`metadata["partial_fill"]`)
  - Market-hours guard (injected `now` datetime)
  - Missing credentials → `RuntimeError` before network call
  - `paper=False` → `ValueError` at construction
  - `_get_client()` caches client; constructs SDK once
  - `get_account()` and `get_positions()` with injected and factory clients
  - `cancel_order()` method dispatch, status codes, missing-method error
  - `preflight_check()` happy path, symbol normalisation, account/position guards
  - `main.py` paper path: disabled flag raises; enabled flag calls preflight

### 10.2 Paper integration tests (gated by env vars)

- Skipped automatically if `ALPACA_API_KEY` or `ALPACA_SECRET_KEY` is unset.
- Use `pytest.mark.integration` marker.
- Submit a single market order for 1 share of a liquid ETF (e.g. SPY) and
  immediately cancel it.
- Verify `OrderResult` fields match the Alpaca response.
- Never run in CI without explicit opt-in (`pytest -m integration`).

### 10.3 Regression tests

- All existing tests continue to pass without `ALPACA_API_KEY` present.
- No new mandatory dependencies beyond `alpaca-py` (already in `requirements.txt`).

---

## 11. Manual Go / No-Go Checklist

Before removing the `NotImplementedError` that blocks order execution in `main.py`:

| # | Item | Status |
|---|------|--------|
| 1 | `AlpacaBrokerAdapter` unit tests pass (mocked) | ✅ Done |
| 2 | Full test suite passes without `ALPACA_API_KEY` set (`pytest`) | ✅ Done |
| 3 | No API keys or secrets present in source code or `settings.yaml` | ✅ Done |
| 4 | `paper_trading_enabled=False` still fail-closed | ✅ Done |
| 5 | Paper integration test passes against live Alpaca paper account | ☐ |
| 6 | Market-hours guard tested manually outside RTH | ☐ |
| 7 | Startup account check tested with blocked account (expected failure) | ☐ |
| 8 | Startup position check tested with pre-existing position (expected failure) | ☐ |
| 9 | `order_results.csv` and reconciliation wired for paper mode | ☐ |
| 10 | Reconciliation PASS for a full paper session | ☐ |
| 11 | Code review completed | ☐ |
| 12 | Paper execution readiness checklist confirmed | ☐ |
