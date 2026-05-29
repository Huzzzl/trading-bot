# Manual Position Status Checker — Without Flag BLOCKED Snapshot

Dry-run snapshot taken after PR #137 merged to `main`, confirming the flag gate
fires correctly when `--allow-live-broker-api-readonly` is absent.

**No Alpaca endpoint was contacted.**
**No credentials were read.**
**No TradingClient was constructed.**
**No orders were submitted, sold, cancelled, replaced, or closed.**
**No broker mutation call was made.**
**No live ledger was written.**
**No config was mutated.**
**No position decision was made.**

---

## Command

```bash
python -m src.tools.manual_position_status_checker_readonly \
    --credential-guard  output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol            SPY \
    --output            output/manual_position_status_checker_readonly.json
```

`--allow-live-broker-api-readonly` was intentionally omitted.

---

## Observed Output Fields

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
| `close_position_reachable` | `false` |
| `position_observed` | `null` |
| `open_order_observed` | `null` |
| `market_session_status` | `null` |
| `position_decision_made` | `false` |
| `blocker` | `"readonly broker api flag not set"` |

The tool blocked at gate 4 before reading any environment variable,
constructing any `TradingClient`, or making any broker API call.

---

## Safety Invariants Confirmed

| Invariant | Confirmed |
|-----------|----------|
| No Alpaca endpoint contacted | ✓ |
| No credentials read | ✓ (`credentials_read=false`) |
| No TradingClient constructed | ✓ |
| No submit/cancel/replace called | ✓ |
| No broker mutation calls | ✓ (`broker_mutation_calls_made=false`) |
| No live ledger written | ✓ |
| No config mutated | ✓ |
| No position decision made | ✓ (`position_decision_made=false`) |
| `--allow-live-broker-api-readonly` required | ✓ — BLOCKED without it |

---

## What Was Not Committed

The following were not committed and do not appear in this document or any
PR artifact:

- Raw `output/manual_position_status_checker_readonly.json`
- Credential values or fragments
- Account ID or account number
- Raw broker order or position IDs
- Position size, quantity, fill price, current price
- Cost basis, unrealized PnL
- Buying power or account balance
- Raw broker API response details

---

## Suggested Git Tag

```
manual-position-status-checker-without-flag-blocked-observed
```

---

## Warning

> **This snapshot does not approve real trading.**
> **This snapshot does not approve future broker calls.**
> **No Alpaca endpoint was contacted.**
> **No credentials were read.**
> **No position decision was made.**
> The `--allow-live-broker-api-readonly` flag remains required for any live
> read-only broker contact. Any position decision remains a manual operator
> action. Emergency actions remain manual via the Alpaca broker UI only.
