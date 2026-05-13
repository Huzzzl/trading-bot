# Paper Execution Readiness

This document describes what must be true before `AlpacaBrokerAdapter.submit_order()` is
allowed to reach the Alpaca paper trading API.  It is the final gate between the current
preflight-only state and actual paper order execution.

See also:
- [Alpaca Adapter Design](alpaca_adapter_design.md) — full adapter specification
- [Paper Trading Readiness Checklist](paper_trading_readiness.md) — original system-level checklist

---

## 1. Current Status

| Component | Status |
|-----------|--------|
| `AlpacaBrokerAdapter.submit_order()` | Implemented; exercised through mocked tests only |
| `AlpacaBrokerAdapter.get_account()` | Implemented; exercised through mocked tests only |
| `AlpacaBrokerAdapter.get_positions()` | Implemented; exercised through mocked tests only |
| `AlpacaBrokerAdapter.cancel_order()` | Implemented; exercised through mocked tests only |
| `AlpacaBrokerAdapter.preflight_check()` | Implemented; exercised through mocked tests only |
| `execution.paper_trading_enabled` flag | Present; defaults to `False` (fail-closed) |
| `paper_trading_enabled=False` | Raises `NotImplementedError` before adapter creation |
| `paper_trading_enabled=True` | Creates adapter, runs preflight, then raises before any order |
| Live trading (`paper=False`) | Permanently blocked — raises `ValueError` in constructor |
| Real Alpaca network calls in CI | None — all tests mocked |

**Nothing submits or cancels orders today.**

---

## 2. Required Safety Gates

All of the following must be satisfied before removing the final `NotImplementedError`
that blocks order submission.

### 2.1 Credentials

- Paper account credentials must be present via environment variables only:
  `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.
- `AlpacaBrokerAdapter.__init__` must not read environment variables.
- The Alpaca client must remain lazily created (inside `_get_client()`).
- Credentials must never appear in logs, config files, or source code.

### 2.2 Paper-only enforcement

- `paper=True` must remain enforced at construction time.
- `paper=False` must continue to raise `ValueError` immediately.
- `execution.mode="live"` must not exist in `_VALID_EXECUTION_MODES`.

### 2.3 Startup preflight

- `preflight_check(symbols)` must pass before any order is submitted.
- Account must be `ACTIVE`, `trading_blocked=False`, `account_blocked=False`.
- No existing open position may overlap the target symbol list.
- If preflight fails for any reason, order submission must not proceed.

### 2.4 Order submission rules

- Market-hours guard (`_ensure_market_hours`) must remain active for every
  `submit_order()` call.
- Only market orders (`order_type="market"`) are supported.
- Every `OrderIntent` must carry a non-empty `client_order_id`.
- Every `OrderResult` must preserve `client_order_id` from the response.
- Order submission must not proceed if credentials are missing.
- Order submission must not proceed outside regular market hours (09:30–16:00 ET,
  Monday–Friday).
- Order submission must not proceed if the daily loss limit blocks new entries.

### 2.5 Reconciliation

- `order_intents.csv` and `order_results.csv` must be written after each session.
- Reconciliation must run by `client_order_id` after order submission.
- A reconciliation mismatch must fail closed (no silent ignoring of unmatched orders).

### 2.6 Security

- No secrets in logs at any log level.
- No API keys in `settings.yaml`, source code, or test fixtures.

---

## 3. Proposed Final Paper Execution Flow

Once all safety gates are satisfied, the paper execution path in `main.py` will
follow this sequence:

1. Load config (`load_config()`).
2. Confirm `execution.mode == "paper"`.
3. Require `execution.paper_trading_enabled == true`; fail closed if not set.
4. Create `AlpacaBrokerAdapter()` (lazy client, no network call yet).
5. Run `broker.preflight_check(cfg.symbols)`:
   - Fetches account and position state.
   - Validates account is active and unblocked.
   - Validates no open positions overlap target symbols.
   - Raises `RuntimeError` on any failure — order submission does not proceed.
6. Build strategy, risk manager, and data pipeline for paper mode.
7. Generate `OrderIntent` objects from live signal feed.
8. Submit each intent via `AlpacaBrokerAdapter.submit_order(intent)`:
   - Validates intent fields.
   - Checks market hours.
   - Sends to Alpaca paper endpoint.
   - Returns `OrderResult`.
9. Persist `order_intents.csv` and `order_results.csv`.
10. Run reconciliation keyed by `client_order_id`.
11. Fail closed on any reconciliation mismatch (raise or log `ERROR` and halt).
12. Produce report and audit artifacts.

---

## 4. Explicit Non-Goals

The following will never be part of this codebase without a separate, explicit
design review and readiness checklist:

- Live trading (`execution.mode="live"` or `paper=False`).
- Storing API keys in `settings.yaml` or any config file.
- Bypassing `preflight_check` before order submission.
- Bypassing the market-hours guard for order submission.
- Submitting orders during automated tests (all tests remain mocked).
- Any broker other than Alpaca paper trading for the first paper implementation.

---

## 5. Testing Requirements Before Final Wiring

Before the final `NotImplementedError` blocking order execution is removed:

| # | Requirement | Status |
|---|-------------|--------|
| 1 | All `AlpacaBrokerAdapter` tests mocked (no real API calls) | ✅ Done |
| 2 | `main.py` paper path tests mock `AlpacaBrokerAdapter` | ✅ Done |
| 3 | Tests prove `submit_order` is not called when preflight fails | ✅ Done |
| 4 | Tests prove no orders submitted when `paper_trading_enabled=False` | ✅ Done |
| 5 | Tests prove missing credentials fail before client creation | ✅ Done |
| 6 | Tests prove market-hours guard blocks `submit_order` | ✅ Done |
| 7 | Tests prove `cancel_order` is not called during preflight | ✅ Done |
| 8 | No real network calls in CI (`pytest` without `ALPACA_API_KEY`) | ✅ Done |
| 9 | `order_results.csv` and reconciliation written in paper mode once enabled | ☐ Not yet wired |
| 10 | Paper integration test against live Alpaca paper account (`pytest -m integration`) | ☐ Not yet run |
| 11 | Market-hours guard tested manually outside RTH | ☐ Not yet done |
| 12 | Startup account check tested with blocked account | ☐ Not yet done |
| 13 | Startup position check tested with pre-existing position | ☐ Not yet done |

---

## 6. Final Merge Checklist

Before merging the PR that removes the order-execution `NotImplementedError`:

| # | Item | Confirmed |
|---|------|-----------|
| 1 | Review full diff of `main.py` against `main` branch | ☐ |
| 2 | Confirm changed files: only `main.py` and `docs/` | ☐ |
| 3 | `main.py` still fails closed when `paper_trading_enabled=False` | ☐ |
| 4 | No `execution.mode="live"` introduced | ☐ |
| 5 | No API keys or secrets in source code, `settings.yaml`, or test fixtures | ☐ |
| 6 | All Alpaca tests mocked — `pytest` passes without `ALPACA_API_KEY` | ☐ |
| 7 | Manual paper account credentials are not committed | ☐ |
| 8 | README and docs warn paper mode only | ☐ |
| 9 | `preflight_check` runs before the first `submit_order` call | ☐ |
| 10 | `order_intents.csv`, `order_results.csv`, reconciliation all written | ☐ |
| 11 | Reconciliation mismatch fails closed | ☐ |
| 12 | Code review completed | ☐ |
