# Live Submit Adapter Readiness Checklist

Final checklist that must be fully satisfied before implementing a real Alpaca
live submit adapter in `live_single_manual_submit` or any successor tool.

**The real `AlpacaLiveSubmitBroker` adapter is now implemented (PR #122).**
**No real order has been submitted.**
**All tests are mock-only — no real Alpaca calls in any test.**
**CLI requires `--allow-real-live-submit-once` plus all prerequisite artifacts.**
**This PR does NOT submit, cancel, or replace orders.**
**This PR does NOT enable automated trading.**
**This PR does NOT bypass `config_safety`.**
**Real live submit requires explicit operator flag and all gate conditions at runtime.**

---

## 1. Required Completed Milestones

All of the following must be merged and verified on `main` before any real
adapter implementation PR is opened.

- [ ] `live_credential_presence_guard` complete — `result="PASS"` confirmed
- [ ] `live_operator_config_override_review` complete — `result="PASS"` confirmed
- [ ] `live_broker_preflight_readonly` PASS observed — manual run with live
  credentials confirmed (see `docs/live_readonly_preflight_result_snapshot.md`)
- [ ] `live_single_submit_approval_review` complete — 196 tests, all pass
- [ ] `live_single_manual_submit` mock-only core complete — 193 tests, all pass;
  CLI always BLOCKED ("real live submit adapter not implemented")
- [ ] All relevant docs and status snapshots merged to `main`
  (see `docs/live_readiness_status.md` milestone history)

**Current state:** All items above are complete as of PR #120.
`AlpacaLiveSubmitBroker` real adapter implemented in PR #122 (mock-only tests; no real order submitted).

---

## 2. Runtime Prerequisites for Any Future Real Adapter

The following conditions must all be true at the time the real adapter is
invoked.  Any failure must produce `result="BLOCKED"` with zero broker
mutation calls and zero credential reads.

### Prerequisite artifacts (all must be PASS, same session)

- [ ] `output/live_credential_presence_guard.json` — `result="PASS"`,
  produced in the same operator session
- [ ] `output/live_operator_config_override_review.json` — `result="PASS"`,
  produced in the same operator session
- [ ] `output/live_broker_preflight_readonly.json` — `result="PASS"`,
  produced in the same trading session (market conditions current)
- [ ] `output/live_single_submit_approval_review.json` — `result="PASS"`,
  not expired (`approval_expires_at_utc` strictly after current UTC)

### Local operator config (never `settings.yaml`)

Must be present in a local operator config file only — never committed to
`settings.yaml` or any shared config:

- [ ] `live_trading_enabled: true`  (strict YAML/JSON boolean — not string)
- [ ] `live_submit_dry_run: false`  (strict YAML/JSON boolean — not string)
- [ ] `live_kill_switch_enabled: false`  (strict YAML/JSON boolean — not string)

### `settings.yaml` integrity

- [ ] `settings.yaml` is unchanged from its committed state — no `config_safety`
  flag modified, no diff against `origin/main`

### Order parameters (hardcoded, not configurable)

- [ ] `symbol` is exactly `"SPY"` (no case folding, no whitespace stripping)
- [ ] `side` is exactly `"buy"` (no case folding, no whitespace stripping)
- [ ] `order_type` is exactly `"market"` (no case folding, no whitespace stripping)
- [ ] `notional_cap` is a number in `(0, 100.0]` — not a boolean, not a string

### Account and market state (read-only checks via existing preflight)

- [ ] Account status is `"ACTIVE"`
- [ ] Buying power ≥ `notional_cap`
- [ ] Market is open (`GET /v2/clock` returns `is_open=true`)
- [ ] SPY is `tradable=true` and `fractionable=true`

---

## 3. Required Future Implementation Constraints

The real adapter PR must satisfy every constraint below.  None may be
softened, skipped, or made configurable.

### Adapter construction

- [ ] Real adapter (e.g. `AlpacaLiveSubmitBroker`) may only be constructed
  after **all** gates above pass — never before
- [ ] Credentials (`ALPACA_LIVE_API_KEY`, `ALPACA_LIVE_SECRET_KEY`) may only
  be read after **all** gates pass — never at import time or CLI startup
- [ ] Alpaca SDK import must be lazy (inside `__init__` only, never at module
  level)

### Submit behavior

- [ ] `submit_order` is called **exactly once** per operator run — no retry
- [ ] `cancel_order` is **not implemented** — absent from adapter source
- [ ] `replace_order` is **not implemented** — absent from adapter source
- [ ] No retry logic on exception or rejection — BLOCKED is a final result
- [ ] No orders endpoint (`/v2/orders`) access except the single submit call
  required by the broker SDK
- [ ] No POST/PATCH/DELETE calls except the single broker submit method if
  unavoidable by the SDK; all other mutation endpoints absent

### Output safety

- [ ] All broker exception text is redacted — raw exception message must not
  appear in output JSON, `violations`, `blocker`, or stdout
- [ ] No credential values, account IDs, or account-sensitive data in any
  output field, log line, or stdout
- [ ] `broker_order_id_redacted` in output must be a safe redacted reference
  (e.g. `"<redacted>"`) — never the raw broker order ID string
- [ ] Output artifact (`single_manual_live_submit_attempt.json`) is always
  written regardless of SUBMITTED or BLOCKED outcome

### Ledger behavior

- [ ] Pre-submit ledger row must be written with `status="attempting"` before
  `submit_order` is called — if ledger write fails, BLOCKED with no submit
- [ ] Post-submit ledger row must be updated in-place after submit:
  - On success: `status="submitted"`, `broker_order_id="<redacted>"`
  - On exception: `status="exception"`, `error="details redacted"`
- [ ] Ledger schema must match `LEDGER_COLUMNS` in `live_single_manual_submit.py`
- [ ] Final ledger must pass `live_ledger_verify` (without `--allow-attempting`)

---

## 4. Required Tests Before Real Adapter PR Can Merge

No real adapter PR may merge unless **all** of the following tests pass.

### Structural requirements

- [ ] All tests are mock-only by default — no real Alpaca calls in any test
- [ ] No real `TradingClient` or live broker client constructed in tests
- [ ] Test suite passes with zero failures on a clean checkout

### Source scans (automated)

- [ ] No `cancel_order(` in adapter source (non-comment lines)
- [ ] No `replace_order(` in adapter source (non-comment lines)
- [ ] No retry loop in adapter source
- [ ] No credential value printed, logged, or stored in any test-observable path
- [ ] No Alpaca SDK module-level import in adapter source

### Behavioral tests

- [ ] **Happy path SUBMITTED**: all mocks pass → exactly one `submit_order` call,
  `result="SUBMITTED"`, `order_submitted=true`, `broker_mutation_calls_made=true`
- [ ] **All pre-gate BLOCKED paths**: each of the 7 gate failures produces
  `result="BLOCKED"` with `submit_order_called=false` and no broker construction
- [ ] **Broker exception → BLOCKED**: mock raises exception → `result="BLOCKED"`,
  `submit_order_called=true`, `broker_mutation_calls_made=false`, exception text
  absent from all output fields
- [ ] **Ledger `attempting` → `submitted`**: pre-submit row exists with
  `status="attempting"` at time of mock `submit_order` call; row updated to
  `status="submitted"` after
- [ ] **Ledger `attempting` → `exception`**: mock raises → row updated to
  `status="exception"`, `error="details redacted"`
- [ ] **CLI requires explicit operator config**: test that missing or invalid
  local config YAML → BLOCKED before any broker construction
- [ ] **CLI requires approval artifacts**: test that missing or non-PASS
  prerequisite artifacts → BLOCKED before any broker construction
- [ ] **No cancel/replace calls**: assert `cancel_order` and `replace_order`
  are never called in any test path
- [ ] **Submit called at most once**: assert `submit_order` call count ≤ 1
  in every test scenario

---

## 5. Manual Operator Checklist

The human operator must personally verify each item before running the real
submit tool.  This checklist supplements automated gates — it does not replace
them.

- [ ] Confirmed live account mode (not paper) in Alpaca dashboard
- [ ] Confirmed market is open (regular hours — not pre/post market)
- [ ] Confirmed `symbol=SPY` only — no other symbol
- [ ] Confirmed `notional_cap` value and that buying power ≥ cap
- [ ] Confirmed no existing open SPY order in broker UI (not just via API check)
- [ ] Confirmed no existing open SPY position that conflicts with this buy
- [ ] `output/live_broker_preflight_readonly.json` — PASS run is recent
  (same trading session, market conditions current)
- [ ] Local operator config has all three `config_safety` overrides set
- [ ] `settings.yaml` is unchanged — verified via `git diff`
- [ ] Understood: this is one attempt only — no retry if BLOCKED
- [ ] Understood: no `cancel_order` or `replace_order` logic exists in this
  implementation; emergency cancel must be done manually in broker UI
- [ ] Understood: SUBMITTED means the order reached the broker — not that
  it filled or that a profit is guaranteed
- [ ] Understood: `config_safety` flags must be reset to safe defaults
  immediately after the attempt (SUBMITTED or BLOCKED)
- [ ] Config reset plan confirmed:

  ```yaml
  # In local operator config — reset after attempt
  live_trading_enabled: false
  live_submit_dry_run: true
  live_kill_switch_enabled: true
  ```

---

## 6. Abort Conditions

Abort **immediately** (before any broker contact) if any of the following
conditions is detected:

| Condition | Action |
|-----------|--------|
| Any prerequisite artifact missing or `result != "PASS"` | BLOCKED — do not proceed |
| Approval artifact expired (`approval_expires_at_utc` ≤ current UTC) | BLOCKED — obtain fresh approval |
| `symbol` is not exactly `"SPY"` | BLOCKED |
| `side` is not exactly `"buy"` | BLOCKED |
| `notional_cap` ≤ 0 or > 100.0 | BLOCKED |
| Local operator config missing or invalid | BLOCKED |
| Any `config_safety` flag wrong or absent in local config | BLOCKED |
| `settings.yaml` has been mutated | BLOCKED — revert before proceeding |
| Broker read-only preflight is stale (different trading session) | BLOCKED — re-run preflight |

Abort **after pre-submit ledger write**, update ledger row to `status="exception"`,
and exit BLOCKED if:

| Condition | Action |
|-----------|--------|
| Exception raised during `submit_order` | Catch, redact, update ledger, BLOCKED |
| Any credential-like value detected in output | Redact, do not write, BLOCKED |
| Any unexpected order/cancel/replace code path reached | This should be unreachable; if reached, BLOCKED |

A BLOCKED result is **final**:

- Do not retry with modified parameters.
- Do not re-run without a new operator approval artifact.
- Reset `config_safety` flags immediately.
- Resolve the underlying condition before a new attempt.

---

## References

- [docs/live_readiness_status.md](live_readiness_status.md) — full readiness
  status and milestone history
- [docs/single_manual_live_submit_attempt_design.md](single_manual_live_submit_attempt_design.md) — submit flow design
- [docs/live_readonly_preflight_runbook.md](live_readonly_preflight_runbook.md) — preflight operator runbook
- [docs/live_readonly_preflight_result_snapshot.md](live_readonly_preflight_result_snapshot.md) — preflight PASS snapshot
- `src/tools/live_single_manual_submit.py` — mock-only core (broker=None)
- `tests/test_live_single_manual_submit.py` — 193 tests

---

## Suggested git tag

```
live-submit-adapter-readiness-checklist-complete
```

---

## Warning

> **The real `AlpacaLiveSubmitBroker` adapter is now implemented (PR #122).**
> **No real order has been submitted.**
> **All tests are mock-only — no real Alpaca calls in any test.**
> CLI requires `--allow-real-live-submit-once` plus all prerequisite artifacts
> with `result="PASS"`, strict local operator config booleans, and valid
> credentials in `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`.
> Without the flag, CLI is always BLOCKED.
> `cancel_order` and `replace_order` remain unimplemented.
> No retry logic exists — BLOCKED is a final result.
> Automated and recurring live trading remain unimplemented.
> A real live order attempt requires the operator to satisfy all runtime
> prerequisites listed in Section 2 and Section 5 above at the time of the run.
