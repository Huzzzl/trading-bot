# First Real Live Submit — Success Snapshot

Non-sensitive snapshot of the first completed single manual live SPY market
buy attempt.

**No raw JSON artifacts are included.**
**No ledger CSV is included.**
**No credentials or key fragments are included.**
**No account ID or account number is included.**
**No raw broker order ID is included.**
**No fill price or fill quantity is included.**
**No buying power, balance, or broker response details are included.**

---

## Result Summary

| Field | Observed |
|-------|---------|
| `result` | `"SUBMITTED"` |
| `order_submitted` | `true` |
| `submit_order_called` | `true` |
| `broker_mutation_calls_made` | `true` |
| `cancel_order_called` | `false` |
| `replace_order_called` | `false` |
| `live_submit_enabled` | `true` |
| `credential_values_exposed` | `false` |
| `live_ledger_written` | `true` |
| `blocker` | empty / null |
| `broker_order_id_redacted` | `"<redacted>"` |
| `notional_cap` | `50.0` |
| `automated_trading_enabled` | `false` |
| `recurring_trading_enabled` | `false` |

---

## What Happened

The operator executed one real live SPY market buy using
`live_single_manual_submit` with `--allow-real-live-submit-once` after
satisfying all required gates:

1. All four prerequisite artifacts present with `result="PASS"`:
   - `live_credential_presence_guard`
   - `live_operator_config_override_review`
   - `live_broker_preflight_readonly` (same trading session)
   - `live_single_submit_approval_review` (not expired)
2. Local operator config with strict YAML booleans:
   - `live_trading_enabled: true`
   - `live_submit_dry_run: false`
   - `live_kill_switch_enabled: false`
3. `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` non-empty in environment.
4. `--allow-real-live-submit-once` flag explicitly passed on the CLI.
5. `symbol=SPY`, `side=buy`, `order_type=market`, `notional_cap=50.0`.

The tool submitted the order exactly once via `submit_order`. No retry was
attempted. No cancel or replace was issued through code.

---

## Post-Run Actions Completed

| Action | Completed |
|--------|----------|
| Credentials cleared from environment (`unset ALPACA_LIVE_API_KEY`, `unset ALPACA_LIVE_SECRET_KEY`) | ✓ |
| Local operator config reset to safe defaults | ✓ |
| Output artifact not committed to repository | ✓ |
| Ledger file not committed to repository | ✓ |
| Order/fill status verified manually in Alpaca UI | Operator responsibility |

### Local operator config after reset

```yaml
live_trading_enabled: false
live_submit_dry_run: true
live_kill_switch_enabled: true
```

---

## Safety Invariants Confirmed

| Invariant | Confirmed |
|-----------|----------|
| Exactly one `submit_order` call made | ✓ |
| No `cancel_order` call made | ✓ |
| No `replace_order` call made | ✓ |
| No retry logic executed | ✓ |
| No automated trading enabled | ✓ |
| No recurring trading enabled | ✓ |
| Credential values not exposed in any output field | ✓ |
| Broker order ID redacted in all output | ✓ |
| Live ledger written before submit call (`status="attempting"`) | ✓ |
| Live ledger updated after submit (`status="submitted"`) | ✓ |
| Output artifact not committed to repository | ✓ |
| Ledger file not committed to repository | ✓ |
| `config_safety` flags reset to safe defaults after run | ✓ |
| Credentials cleared from environment after run | ✓ |

---

## What Was Not Done

- No automated or recurring live trading was enabled.
- No second order was submitted.
- No order was cancelled or replaced through code.
- No retry was attempted.
- No credentials, account ID, raw order ID, fill price, fill quantity,
  balance, or broker response details were written to this document.
- No output artifacts or ledger files were committed to the repository.

---

## Suggested Git Tag

```
first-real-live-submit-success-observed
```

---

## References

- `src/tools/live_single_manual_submit.py` — real adapter (PR #122)
- `docs/real_submit_final_operator_runbook.md` — operator runbook (PR #124)
- `docs/real_submit_without_flag_blocked_snapshot.md` — prior BLOCKED dry-run snapshot (PR #123)
- `docs/live_readiness_status.md` — full readiness status and milestone history
- `tests/test_live_single_manual_submit.py` — 255 tests, all mock-only

---

## Warning

> **This snapshot documents a single completed manual attempt only.**
> **It does not approve future trading.**
> **It does not approve automated or recurring trading.**
> Any future live submit attempt requires:
> - Fresh `result="PASS"` runs of all four prerequisite tools
> - A fresh, unexpired operator approval artifact
> - A fresh live broker preflight PASS from the same trading session
> - Explicit local operator config overrides (never committed)
> - Valid credentials in environment variables
> - `--allow-real-live-submit-once` explicitly passed on the CLI
>
> SUBMITTED and BLOCKED outcomes are both final for that attempt.
> Do not retry without a new operator approval artifact.
> Emergency cancel and replace remain manual via the Alpaca broker UI only —
> the code has no `cancel_order` or `replace_order` logic.
> `config_safety` overrides must be reset to safe defaults immediately after
> every attempt.
