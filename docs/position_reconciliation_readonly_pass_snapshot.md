# Position Reconciliation Read-Only — PASS Snapshot

Snapshot taken after PR #130 merged to `main`, recording the first PASS
result from `live_position_reconciliation_readonly` with the
`--allow-live-broker-api-readonly` flag present and valid credentials
in the environment.

**Alpaca was contacted read-only only (GET calls only).**
**Credentials were read but never exposed, stored, or written to any output.**
**TradingClient was constructed only after all gates passed and the flag was present.**
**No order was submitted, sold, cancelled, replaced, or closed.**
**No broker mutation call was made.**
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
    --output output/live_position_reconciliation_readonly.json \
    --allow-live-broker-api-readonly
```

Both prerequisite artifacts (`live_credential_presence_guard.json` and
`live_operator_config_override_review.json`) had `result="PASS"`.
`ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` were set in the
environment before the run and cleared immediately after.

---

## Observed Result (non-sensitive fields only)

| Field | Observed |
|-------|---------|
| `result` | `"PASS"` |
| `broker_calls_made` | `true` |
| `broker_calls_readonly` | `true` |
| `broker_mutation_calls_made` | `false` |
| `credentials_read` | `true` |
| `credential_values_exposed` | `false` |
| `live_submit_enabled` | `false` |
| `submit_order_reachable` | `false` |
| `cancel_order_reachable` | `false` |
| `replace_order_reachable` | `false` |
| `symbol` | `"SPY"` |
| `position_observed` | `true` |
| `open_order_observed` | `false` |
| `broker_ids_redacted` | `true` |
| `account_identifiers_redacted` | `true` |
| `raw_broker_response_included` | `false` |
| `violations` | `[]` |
| `blocker` | `null` |

---

## What was confirmed

### Read-only broker calls made

Two read-only calls were made via `AlpacaLivePositionBroker`:

| Call | Method | Result |
|------|--------|--------|
| `get_position("SPY")` | `get_open_position` (GET) | Position exists |
| `get_open_orders("SPY")` | `get_orders` with `QueryOrderStatus.OPEN` (GET) | No open orders |

No POST, PATCH, or DELETE calls were made. The orders mutation endpoint
(`/v2/orders` POST) was not contacted. No `submit_order`, `cancel_order`,
or `replace_order` method was called.

### position_observed and open_order_observed are presence flags only

`position_observed=true` means only that a SPY position was observed to
exist in the live account at the time of the run. It does not record
position size, fill price, quantity, cost basis, or any broker identifier.

`open_order_observed=false` means only that no open SPY orders were
observed at the time of the run.

**Neither field constitutes a position management decision.** Whether to
hold or sell the position remains a manual operator decision, unchanged
by this tool.

### Credentials were read but not exposed

`credentials_read=true` confirms that `os.environ.get` was called for
`ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` after all gates passed.
`credential_values_exposed=false` confirms that no credential value was
written to any output field, violation, or stdout.

Credentials were cleared from the environment immediately after the run.

### TradingClient constructed only after all gates passed

`AlpacaLivePositionBroker` (which wraps `TradingClient(paper=False)`) was
constructed only after:
1. `credential_guard` artifact present and `result="PASS"`
2. `operator_override` artifact present and `result="PASS"`
3. `symbol` exactly `"SPY"`
4. `--allow-live-broker-api-readonly` flag present
5. Both env vars non-empty

### All safety invariants held

| Invariant | Observed |
|-----------|---------|
| Only GET-equivalent broker calls made | ✓ (`broker_calls_readonly=true`) |
| No broker mutation calls | ✓ (`broker_mutation_calls_made=false`) |
| No submit_order called | ✓ (`submit_order_reachable=false`) |
| No cancel_order called | ✓ (`cancel_order_reachable=false`) |
| No replace_order called | ✓ (`replace_order_reachable=false`) |
| No credential values exposed | ✓ (`credential_values_exposed=false`) |
| No raw broker IDs in output | ✓ (`broker_ids_redacted=true`) |
| No account identifiers in output | ✓ (`account_identifiers_redacted=true`) |
| No raw broker response in output | ✓ (`raw_broker_response_included=false`) |
| No live ledger written | ✓ |
| No config mutated | ✓ |
| No position decision made | ✓ |
| No automated or recurring trading | ✓ |

---

## What was not collected

The following were not collected and do not appear in this document:

- Raw output JSON artifact
- Credential values or fragments
- Account ID or account number
- Raw broker order IDs or position IDs
- Position size, fill price, or quantity
- Cost basis or unrealized gain/loss
- Buying power or account balance
- Raw broker response details

The output artifact was written locally and not committed to this
repository.

---

## Post-run actions

- Credentials cleared from the environment immediately after the run
- Output artifact not committed
- No config changes required (tool is read-only and makes no config mutations)

---

## What this PASS means (and does not mean)

PASS from `live_position_reconciliation_readonly` confirms:

- The tool reached the broker without error
- A SPY position was observed (`position_observed=true`)
- No open SPY orders were observed (`open_order_observed=false`)

PASS does **not**:

- Decide whether to hold or sell the position (that remains manual)
- Authorize any order submission, cancellation, or replacement
- Remove or weaken `config_safety`
- Enable live trading
- Approve automated or recurring position management

---

## Suggested Git Tag

```
position-reconciliation-readonly-pass-observed
```

---

## References

- `src/tools/live_position_reconciliation_readonly.py` — tool implementation (PR #130)
- `tests/test_live_position_reconciliation_readonly.py` — 106 tests (PR #130)
- `docs/live_position_reconciliation_readonly_design.md` — design document
- `docs/position_reconciliation_without_flag_blocked_snapshot.md` — prior BLOCKED snapshot (PR #131)
- `docs/live_readiness_status.md` — full readiness status and milestone history
- `docs/post_submit_manual_position_handling_runbook.md` — post-submit operator runbook

---

## Warning

> **This snapshot does not approve holding or selling the observed position.**
> **This snapshot does not approve future trading.**
> **This snapshot does not approve future broker calls.**
>
> `position_observed=true` is a boolean presence flag only — it does not
> record size, price, or any other position detail.
> Whether to hold or sell the position remains a manual operator decision.
> Emergency actions (cancel, close, replace) remain manual via the Alpaca
> broker UI only.
> Any future read-only reconciliation run requires the
> `--allow-live-broker-api-readonly` flag, both prerequisite artifacts with
> `result="PASS"`, and valid credentials in the environment.
