# Position Reconciliation Without Flag — BLOCKED Snapshot

Snapshot taken after PR #130 merged to `main`, confirming the BLOCKED gate
operates correctly when `--allow-live-broker-api-readonly` is absent.

**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No TradingClient was constructed.**
**No orders were submitted, sold, cancelled, or replaced.**
**No live ledger was written.**
**No config was mutated.**
**No position decision was made.**

---

## Command

```sh
python -m src.tools.live_position_reconciliation_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --output output/live_position_reconciliation_readonly.json
```

The `--allow-live-broker-api-readonly` flag was intentionally omitted.

---

## Observed Result (non-sensitive fields only)

| Field | Observed |
|-------|---------|
| `result` | `"BLOCKED"` |
| `broker_calls_made` | `false` |
| `broker_calls_readonly` | `false` |
| `broker_mutation_calls_made` | `false` |
| `credentials_read` | `false` |
| `credential_values_exposed` | `false` |
| `live_submit_enabled` | `false` |
| `submit_order_reachable` | `false` |
| `cancel_order_reachable` | `false` |
| `replace_order_reachable` | `false` |
| `position_observed` | `null` |
| `open_order_observed` | `null` |
| `blocker` | `"readonly broker api flag not set"` |

---

## What was confirmed

### Flag gate fires before any credential or broker access

The tool returned BLOCKED at gate 4 (flag check) — before reading any
environment variable, before constructing any `TradingClient`, and before
making any broker API call.

`credentials_read=false` confirms no `os.environ` read occurred.
`broker_calls_made=false` confirms no broker call was attempted.

### All safety invariants held

| Invariant | Observed |
|-----------|---------|
| No Alpaca endpoint contacted | ✓ |
| No credentials read | ✓ (`credentials_read=false`) |
| No TradingClient constructed | ✓ |
| No submit_order called | ✓ (`submit_order_reachable=false`) |
| No cancel_order called | ✓ (`cancel_order_reachable=false`) |
| No replace_order called | ✓ (`replace_order_reachable=false`) |
| No broker mutation calls | ✓ (`broker_mutation_calls_made=false`) |
| No credential values exposed | ✓ (`credential_values_exposed=false`) |
| No live ledger written | ✓ |
| No config mutated | ✓ |
| No position decision made | ✓ |

### position_observed and open_order_observed are null

Both fields are `null` because the tool was BLOCKED before any broker call.
No position or order data was queried, returned, or inferred.

---

## What the flag gate means

`--allow-live-broker-api-readonly` is an explicit operator action required
before any live Alpaca read-only API contact. Without it:

- Credentials are never read from the environment
- `TradingClient` is never constructed
- No broker API call is ever made
- The tool always returns `result="BLOCKED"` with
  `blocker="readonly broker api flag not set"`

This gate operates even when both prerequisite artifacts
(`live_credential_presence_guard.json` and
`live_operator_config_override_review.json`) are present and have
`result="PASS"`, and even when valid credentials exist in the environment.

---

## What was not collected

The following were not collected and do not appear in this document:

- Raw output JSON artifact
- Credential values or fragments
- Account ID or account number
- Order IDs or broker order IDs
- Position size, fill price, or quantity
- Buying power or account balance
- Raw broker response details

The output artifact was written locally and not committed to this repository.

---

## Suggested Git Tag

```
position-reconciliation-without-flag-blocked-observed
```

---

## References

- `src/tools/live_position_reconciliation_readonly.py` — tool implementation (PR #130)
- `tests/test_live_position_reconciliation_readonly.py` — 106 tests (PR #130)
- `docs/live_position_reconciliation_readonly_design.md` — design document
- `docs/live_readiness_status.md` — full readiness status and milestone history

---

## Warning

> **This snapshot does not approve real trading.**
> **This snapshot does not approve future broker calls.**
> **No Alpaca endpoint was contacted.**
> **No credentials were read.**
> **No position decision was made.**
>
> The `--allow-live-broker-api-readonly` flag remains required for any live
> read-only broker contact. Passing the flag additionally requires both
> prerequisite artifacts with `result="PASS"` and valid credentials in
> `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`.
> Any position decision (hold or sell) remains a manual operator action.
> Emergency actions (cancel, close, replace) remain manual via the Alpaca
> broker UI only.
