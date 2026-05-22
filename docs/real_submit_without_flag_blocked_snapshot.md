# Real Submit Without Flag — BLOCKED Snapshot

Non-sensitive local dry-run snapshot taken after PR #122 merged to `main`.

**No real order was submitted.**
**No Alpaca submit call was made.**
**No live ledger was written.**
**No credential values were exposed.**
**The explicit `--allow-real-live-submit-once` flag remains required.**
**This snapshot does not approve trading.**

---

## Run 1: `live_single_submit_approval_review`

Ran `live_single_submit_approval_review` offline against a locally produced
operator approval artifact.

### Result: PASS

| Field | Observed |
|-------|---------|
| `result` | `"PASS"` |
| `single_attempt_approved` | `true` |
| `live_submit_enabled` | `false` |
| `submit_order_reachable` | `false` |
| `broker_calls_made` | `false` |
| `credentials_read` | `false` |
| `cancel_order_called` | `false` |
| `replace_order_called` | `false` |
| `automated_trading_enabled` | `false` |
| `recurring_trading_enabled` | `false` |
| `credential_values_exposed` | `false` |

PASS confirms the approval artifact is structurally valid, correctly scoped
(`approval_scope="AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"`), not expired,
and contains all required strict-boolean acknowledgements.

PASS does **not** submit an order, enable live trading, or remove any
`config_safety` guard.

---

## Run 2: `live_single_manual_submit` — WITHOUT `--allow-real-live-submit-once`

Ran `live_single_manual_submit` locally with all prerequisite artifacts
present (`result="PASS"`) and a valid local operator config
(`live_trading_enabled=true`, `live_submit_dry_run=false`,
`live_kill_switch_enabled=false`), but **without** the
`--allow-real-live-submit-once` CLI flag.

### Result: BLOCKED

| Field | Observed |
|-------|---------|
| `result` | `"BLOCKED"` |
| `blocker` | `"real live submit adapter not implemented"` |
| `order_submitted` | `false` |
| `broker_mutation_calls_made` | `false` |
| `submit_order_called` | `false` |
| `cancel_order_called` | `false` |
| `replace_order_called` | `false` |
| `automated_trading_enabled` | `false` |
| `recurring_trading_enabled` | `false` |
| `credential_values_exposed` | `false` |
| `live_ledger_written` | `false` |
| `live_submit_enabled` | `true` |

### Note on `live_submit_enabled=true`

`live_submit_enabled=true` reflects only that all three local operator config
flags were set to their required values in the local config file
(`live_trading_enabled=true`, `live_submit_dry_run=false`,
`live_kill_switch_enabled=false`). It does **not** indicate that a real submit
occurred or is imminent. The tool reached the final gate —
`--allow-real-live-submit-once` absent — and returned BLOCKED before reading
any credentials or constructing any broker client.

### What did not happen

- No `submit_order` call was made.
- No Alpaca endpoint was contacted.
- No credentials were read from the environment.
- No `TradingClient` was constructed.
- No live ledger row was written.
- No order was submitted, queued, or attempted.
- No `cancel_order` or `replace_order` call was made.

---

## Safety Invariants Confirmed

| Invariant | Confirmed |
|-----------|----------|
| No real order submitted | ✓ |
| No Alpaca endpoint contacted | ✓ |
| No credential values read or exposed | ✓ |
| No live ledger written | ✓ |
| `submit_order_called=false` | ✓ |
| `broker_mutation_calls_made=false` | ✓ |
| `cancel_order_called=false` | ✓ |
| `replace_order_called=false` | ✓ |
| `automated_trading_enabled=false` | ✓ |
| `recurring_trading_enabled=false` | ✓ |
| `credential_values_exposed=false` | ✓ |
| `--allow-real-live-submit-once` required | ✓ — BLOCKED without it |
| `config_safety` controlled by local operator config | ✓ — flags reset to safe defaults after run |

---

## What Remains Required for a Real Submit Attempt

All of the following must be satisfied at runtime before a real order can
be attempted:

1. All four prerequisite artifacts present with `result="PASS"`:
   - `live_credential_presence_guard`
   - `live_operator_config_override_review`
   - `live_broker_preflight_readonly`
   - `live_single_submit_approval_review`
2. Local operator config with strict YAML booleans:
   - `live_trading_enabled: true`
   - `live_submit_dry_run: false`
   - `live_kill_switch_enabled: false`
3. `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` non-empty in environment.
4. `--allow-real-live-submit-once` flag explicitly passed on the CLI.
5. `symbol=SPY`, `side=buy`, `order_type=market`, `0 < notional_cap <= 100.0`.

Without all five conditions, `run_submit()` returns BLOCKED before reading
any credentials or constructing any broker client.

---

## Suggested Git Tag

```
real-submit-without-flag-blocked-observed
```

---

## References

- `src/tools/live_single_manual_submit.py` — real adapter implementation (PR #122)
- `src/tools/live_single_submit_approval_review.py` — approval review tool
- `tests/test_live_single_manual_submit.py` — 255 tests, all mock-only
- [docs/live_readiness_status.md](live_readiness_status.md) — full readiness status and milestone history
- [docs/live_submit_adapter_readiness_checklist.md](live_submit_adapter_readiness_checklist.md) — adapter readiness checklist
- [docs/single_manual_live_submit_attempt_design.md](single_manual_live_submit_attempt_design.md) — submit flow design

---

## Warning

> **This snapshot does not approve real trading.**
> **This snapshot does not approve live order submission.**
> The `--allow-real-live-submit-once` flag is required for any real submit
> attempt. Without it, CLI is always BLOCKED.
> A real order attempt additionally requires a funded account, fresh PASS
> runs of all four prerequisite tools, valid credentials in environment
> variables, and explicit local operator config overrides — none of which
> are committed to this repository.
> `config_safety` overrides must be reset to safe defaults immediately after
> any attempt (SUBMITTED or BLOCKED).
