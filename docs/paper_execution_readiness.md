# Paper Execution Readiness

This document describes what must be true before `AlpacaBrokerAdapter.submit_order()` is
allowed to reach the Alpaca paper trading API.  It is the final gate between the current
preflight-only state and actual paper order execution.

See also:
- [Alpaca Adapter Design](alpaca_adapter_design.md) — full adapter specification
- [Paper Trading Readiness Checklist](paper_trading_readiness.md) — original system-level checklist
- [Paper Execution Runbook](paper_execution_runbook.md) — step-by-step manual procedure for the project owner

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
| `execution.paper_order_quantity_override` flag | Present; defaults to `None` (fail-closed); only `1.0` accepted |
| `execution.paper_preview_only` flag | Present; defaults to `True` (preview-only, no submit) |
| `execution.paper_selected_client_order_id` field | Present; defaults to `None`; required in submit mode |
| `execution.paper_close_positions_enabled` flag | Present; defaults to `False` (close path disabled) |
| `execution.paper_close_preview_only` flag | Present; defaults to `True` (close preview only, no submit) |
| `execution.paper_selected_close_client_order_id` field | Present; defaults to `None`; required in close submit mode |
| `execution.paper_close_quantity_override` flag | Present; defaults to `None`; only `1.0` accepted |
| `paper_trading_enabled=False` | Raises `NotImplementedError` before adapter creation |
| `paper_trading_enabled=True` | Two-phase preview/selection paper path — mock-tested only (see § 1.1) |
| `paper_close_positions_enabled=True` | Two-phase close/flatten path — mock-tested only (see § 1.2) |
| Live trading (`paper=False`) | Permanently blocked — raises `ValueError` in constructor |
| Real Alpaca network calls in CI | None — all tests mocked |

### 1.1 Two-Phase Paper Execution Path (current)

`paper_trading_enabled=True` wires a two-phase preview/selection execution path in `main.py`.
The path is mock-tested only.  **No manual run against a real Alpaca paper account
has been completed yet.**

**Phase 1 — Preview (`paper_preview_only=True`, the default):**
- `paper_candidate_intents.csv` is always written (even with 0 intents).
- No order is submitted.
- Multiple generated intents are allowed.
- `generate_all` is called to produce standard report artifacts.

**Phase 2 — Submit selected intent (`paper_preview_only=False`):**
- `paper_candidate_intents.csv` is written first (before any submission decision).
- `paper_selected_client_order_id` must be set to a non-empty value.
- Exactly one intent matching that ID must exist; 0 or >1 matches raises `RuntimeError`.
- `paper_order_quantity_override` (if set) is applied to the selected intent only.
- Safety validation runs on the selected intent only.
- Exactly one order is submitted.
- `paper_intent_audit.csv`, `order_intents.csv`, `order_results.csv`, and
  `order_reconciliation.json` are written after submission.
- Reconciliation compares the selected (submitted) intent vs its result.

**Hard constraints on the selected intent (fail closed — raises before `submit_order`):**

| Constraint | Behaviour on violation |
|------------|------------------------|
| `paper_preview_only=false` requires non-empty `paper_selected_client_order_id` | `RuntimeError` |
| Selected ID must match exactly one candidate | `RuntimeError` (0 or >1 matches) |
| Symbol must be `SPY` | `RuntimeError` |
| `order_type` must be `market` | `RuntimeError` |
| `quantity` must be `<= 1` (after override) | `RuntimeError` |
| `client_order_id` must be non-empty | `RuntimeError` |
| Selected buy with existing nonzero position for that symbol | `RuntimeError` — no order submitted |
| `OrderResult` must carry `client_order_id` | `RuntimeError` after `submit_order` |
| `order_reconciliation.json` must be written | `RuntimeError` if file absent after `generate_all` |
| Reconciliation `overall_status` must be `PASS` or `N/A` | `RuntimeError` on `WARN` or other |

Non-selected candidate intents are visible in `paper_candidate_intents.csv` but their
content does not block submission of the selected intent.

**Position-aware buy safety (`main.py`):**

- Preview-only mode always runs the engine and writes `paper_candidate_intents.csv`
  even when the paper account already holds a position in the target symbol.
- Submit mode calls `preflight_check(..., allow_existing_positions=True)` so that
  the check does not abort preview runs; `main.py` then enforces its own position rule.
- If the selected intent is a buy and the paper account holds a nonzero position in that
  symbol, `main.py` raises `RuntimeError` before `submit_order`.  The error message
  explicitly states no order was submitted.
- Positions in non-target symbols and positions with `qty = 0` do not block a buy.
- There is no automatic close or sell logic.  To submit another buy, manually close the
  existing position in the Alpaca dashboard first.

**Optional quantity override (`paper_order_quantity_override`):**

When the strategy generates a quantity larger than 1 (e.g. 139 shares from a full position-
sizing calculation), the selected intent would fail the `quantity > 1` constraint.  Setting
`execution.paper_order_quantity_override: 1` in `settings.yaml` causes `main.py` to replace
the selected intent's quantity with `1.0` *before* the safety validation runs.  The override
is logged and recorded in `paper_intent_audit.csv` with the original quantity preserved.

Rules for the override field:
- Default `None` — no override applied; selected intent with `quantity > 1` raises as normal.
- Only `1.0` is accepted; any value `<= 0` or `> 1` raises `RuntimeError` before `submit_order`.
- The override does **not** bypass symbol, order-type, or `client_order_id` checks.
- The override does **not** apply in preview mode or in backtest mode.
- `paper_intent_audit.csv` records `original_quantity`, `submitted_quantity`, and `override_applied`.

**Audit artifacts always written** (even when 0 intents generated):
- `order_intents.csv` — all candidate intents generated by the strategy pipeline
- `order_results.csv` — only actually submitted `OrderResult` objects
- `order_reconciliation.json` — reconciliation keyed by `client_order_id`

**cancel_order is never called** in the paper execution or close paths.

### 1.2 Two-Phase Paper Close/Flatten Path (current)

`paper_close_positions_enabled=True` wires a separate two-phase close/flatten
execution path in `main.py` that is mutually exclusive with the buy-submit path.
The path is mock-tested only.

> **First implementation constraints:** SPY only, sell market only, max 1 share
> (via `paper_close_quantity_override=1`).

**Phase A — Close preview (`paper_close_preview_only=True`, the default):**
- Calls `preflight_check(..., allow_existing_positions=True)`.
- Fetches current positions from preflight result.
- Generates SPY sell-market close candidates for all SPY positions with `qty > 0`.
- Writes `paper_close_candidate_intents.csv` (empty if no qualifying positions).
- No order is submitted; no engine run; returns cleanly.

**Phase B — Close submit (`paper_close_preview_only=False`):**
- `paper_selected_close_client_order_id` must be set to a non-empty value.
- Exactly one candidate matching that ID must exist; 0 or >1 matches raises `RuntimeError`.
- `paper_close_quantity_override` (if set) is applied to the selected candidate only.
- Safety validation runs on the selected candidate.
- Exactly one sell-market order is submitted.
- `paper_close_intent_audit.csv`, `order_intents.csv`, `order_results.csv`, and
  `order_reconciliation.json` are written after submission.

**Hard constraints on the selected close candidate (fail closed — raises before `submit_order`):**

| Constraint | Behaviour on violation |
|------------|------------------------|
| `paper_close_preview_only=false` requires non-empty `paper_selected_close_client_order_id` | `RuntimeError` |
| Selected close ID must match exactly one candidate | `RuntimeError` (0 or >1 matches) |
| Symbol must be `SPY` | `RuntimeError` |
| Side must be `sell` — no buys in close flow | `RuntimeError` |
| `order_type` must be `market` | `RuntimeError` |
| `quantity` must be `<= 1` (after override) | `RuntimeError` |
| `quantity` must be `<= current position qty` — no shorting | `RuntimeError` |
| `client_order_id` must be non-empty | `RuntimeError` |
| `OrderResult` must carry `client_order_id` | `RuntimeError` after `submit_order` |
| `order_reconciliation.json` must be written | `RuntimeError` if file absent |
| Reconciliation `overall_status` must be `PASS` or `N/A` | `RuntimeError` on `WARN` or other |
| `cancel_order` is never called | By design |

**Optional close quantity override (`paper_close_quantity_override`):**

When a position holds more than 1 share (e.g. 5 shares), the close candidate
will have `quantity=5` which exceeds the `<= 1` constraint.  Setting
`execution.paper_close_quantity_override: 1` in `settings.yaml` overrides the
submitted quantity to `1.0` only.  The override is logged and recorded in
`paper_close_intent_audit.csv` with the original position quantity preserved.

Rules for the close override field:
- Default `None` — no override; candidate with `quantity > 1` raises as normal.
- Only `1.0` is accepted; values `<= 0` or `> 1` raise `RuntimeError` before submit.
- Override does **not** bypass symbol, side, order-type, or `client_order_id` checks.
- Override does **not** allow selling more than the current position qty.
- `paper_close_intent_audit.csv` records `original_quantity`, `submitted_quantity`,
  `override_applied`, and `current_position_qty`.

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

- `preflight_check(symbols, allow_existing_positions=True)` is called in paper mode.
- Account must be `ACTIVE`, `trading_blocked=False`, `account_blocked=False`.
- Existing target-symbol positions do **not** block preflight in paper mode; position
  blocking for buy submits is handled separately by `main.py` (see position-aware safety above).
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

| # | Requirement | Status |
|---|-------------|--------|
| 1 | All `AlpacaBrokerAdapter` tests mocked (no real API calls) | ✅ Done |
| 2 | `main.py` paper path tests mock `AlpacaBrokerAdapter` | ✅ Done |
| 3 | Tests prove `submit_order` is not called when preflight fails | ✅ Done |
| 4 | Tests prove no orders submitted when `paper_trading_enabled=False` | ✅ Done |
| 5 | Tests prove missing credentials fail before client creation | ✅ Done |
| 6 | Tests prove market-hours guard blocks `submit_order` | ✅ Done |
| 7 | Tests prove `cancel_order` is not called in paper path | ✅ Done |
| 8 | No real network calls in CI (`pytest` without `ALPACA_API_KEY`) | ✅ Done |
| 9 | Tests prove unsafe intents raise `RuntimeError` before `submit_order` | ✅ Done |
| 10 | Tests prove >1 safe intent raises `RuntimeError` before `submit_order` | ✅ Done |
| 11 | Tests prove `order_reconciliation.json` always written (empty or not) | ✅ Done |
| 12 | Tests prove missing `order_reconciliation.json` raises `RuntimeError` | ✅ Done |
| 13 | Tests prove `OrderResult` without `client_order_id` raises `RuntimeError` | ✅ Done |
| 14 | Tests prove reporter receives only the selected (submitted) intent in submit mode | ✅ Done |
| 15b | Tests prove `paper_order_quantity_override=1.0` converts large qty and passes safety checks | ✅ Done |
| 15c | Tests prove override `> 1` or `<= 0` raises `RuntimeError` before `submit_order` | ✅ Done |
| 15d | Tests prove override does not bypass symbol/order-type/`client_order_id` constraints | ✅ Done |
| 15e | Tests prove `paper_intent_audit.csv` written correctly with and without override | ✅ Done |
| 15f | Tests prove preview mode writes `paper_candidate_intents.csv`, does not call `submit_order` | ✅ Done |
| 15g | Tests prove preview mode allows multiple generated intents | ✅ Done |
| 15h | Tests prove preview mode writes CSV even with 0 intents | ✅ Done |
| 15i | Tests prove non-preview without selected ID raises before `submit_order` | ✅ Done |
| 15j | Tests prove selected ID not found raises before `submit_order` | ✅ Done |
| 15k | Tests prove duplicate selected ID raises before `submit_order` | ✅ Done |
| 15l | Tests prove only selected intent is submitted from multiple candidates | ✅ Done |
| 15m | Tests prove non-selected unsafe intents do not block submission of selected intent | ✅ Done |
| 15n | Tests prove selected unsafe intent raises before `submit_order` | ✅ Done |
| 15o | Tests prove override applies only to selected intent | ✅ Done |
| 16a | `paper_close_positions_enabled=false` does not create close candidates | ✅ Done |
| 16b | Close preview writes `paper_close_candidate_intents.csv`, does not call `submit_order` | ✅ Done |
| 16c | Close preview with no positions writes empty candidate CSV | ✅ Done |
| 16d | Close preview with SPY position creates one SPY sell market candidate | ✅ Done |
| 16e | Close preview ignores non-SPY positions | ✅ Done |
| 16f | Close submit without selected close `client_order_id` raises before `submit_order` | ✅ Done |
| 16g | Close submit selected ID not found raises | ✅ Done |
| 16h | Duplicate selected close ID raises | ✅ Done |
| 16i | Close submit SPY sell calls `submit_order` exactly once | ✅ Done |
| 16j | Selected close `quantity > current position qty` raises before submit | ✅ Done |
| 16k | Selected close `quantity > 1` raises unless `paper_close_quantity_override=1` | ✅ Done |
| 16l | `paper_close_quantity_override=1` applies only to selected close intent | ✅ Done |
| 16m | No buy close intent can be generated or submitted | ✅ Done |
| 16n | `cancel_order` is never called in close path | ✅ Done |
| 16o | No real Alpaca network calls in close tests | ✅ Done |
| 15 | Paper integration test against live Alpaca paper account (`pytest -m integration`) | ☐ Not yet run |
| 16 | Market-hours guard tested manually outside RTH | ☐ Not yet done |
| 17 | Startup account check tested with blocked account | ☐ Not yet done |
| 18 | Startup position check tested with pre-existing position | ☐ Not yet done |

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
