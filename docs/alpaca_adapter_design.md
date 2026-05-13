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
  when `execution.mode == "paper"` (currently blocked).

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
   lazily inside `_get_client()`, which is called only by the four interface
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
| `client_order_id` | `client_order_id` | Propagated as-is; `None` → Alpaca auto-generates an ID |
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
| `client_order_id` | `client_order_id` | Echoed back from the request |
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

## 8. Position and Account Checks on Startup

Before the first order is submitted, the adapter performs two startup checks:

### 8.1 Account check
- Call `GET /v2/account`.
- Verify `account.status == "ACTIVE"`.
- Verify `account.trading_blocked == False`.
- Verify `account.account_blocked == False`.
- Fail with `RuntimeError` if any check fails.

### 8.2 Position check
- Call `GET /v2/positions`.
- If any open position exists for a symbol that the strategy intends to trade,
  raise `RuntimeError` with an explicit message listing the conflicting
  symbols.
- Stale positions in unrelated symbols are logged as `WARNING` but do not
  block startup.

These checks run once at adapter initialisation (lazy, on first method call)
and the result is cached for the lifetime of the adapter instance.

---

## 9. Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Network error (timeout / connection refused) | Retry up to 3 times with exponential back-off (1 s, 2 s, 4 s); raise `RuntimeError` after exhausting retries |
| HTTP 429 rate limit | Respect `Retry-After` header; sleep and retry; log `WARNING` |
| Order rejected by Alpaca | Return `OrderResult(status="rejected")`; log `WARNING` with reason |
| Partial fill | Return `OrderResult(status="filled")`; store `filled_qty` in `metadata`; log `WARNING` |
| Submit timeout (no response within 10 s) | Cancel the order via `DELETE /v2/orders/{id}`; raise `RuntimeError` |
| Missing / expired credentials | Raise `RuntimeError` before any network call; log `ERROR` (no key values in log) |
| Orders submitted outside market hours | Raise `RuntimeError` before any network call; log `ERROR` |

---

## 10. Testing Plan

### 10.1 Unit tests (no real API calls)

- All tests mock the Alpaca HTTP client at the `requests` / SDK layer.
- CI must never make real network calls.
- Tests cover:
  - `submit_order` happy path (market buy, market sell)
  - Status normalisation for every Alpaca status string
  - `client_order_id` round-trip
  - Partial fill detection
  - Each error scenario in section 9
  - Market-hours guard (mock `datetime.now()`)
  - Missing credentials → `RuntimeError` before network call
  - `paper=False` → `ValueError` at construction

### 10.2 Paper integration tests (gated by env vars)

- Skipped automatically if `ALPACA_API_KEY` or `ALPACA_SECRET_KEY` is unset.
- Use `pytest.mark.integration` marker.
- Submit a single market order for 1 share of a liquid ETF (e.g. SPY) and
  immediately cancel it.
- Verify `OrderResult` fields match the Alpaca response.
- Never run in CI without explicit opt-in (`pytest -m integration`).

### 10.3 Regression tests

- All existing 589 tests continue to pass without any new dependencies.
- `AlpacaBrokerAdapter` skeleton tests (17 tests) remain green.

---

## 11. Manual Go / No-Go Checklist

Before removing the `NotImplementedError` guard in `main.py`:

| # | Item | Confirmed |
|---|------|-----------|
| 1 | `AlpacaBrokerAdapter` unit tests pass (mocked) | ☐ |
| 2 | Paper integration test passes against live Alpaca paper account | ☐ |
| 3 | Market-hours guard tested manually outside RTH | ☐ |
| 4 | Startup account check tested with blocked account (expected failure) | ☐ |
| 5 | Startup position check tested with pre-existing position (expected failure) | ☐ |
| 6 | Reconciliation PASS for a full dry-run session | ☐ |
| 7 | No API keys or secrets present in source code or `settings.yaml` | ☐ |
| 8 | Full test suite passes without `ALPACA_API_KEY` set (`pytest`) | ☐ |
| 9 | Code review completed | ☐ |
| 10 | Paper trading readiness checklist updated and confirmed | ☐ |
