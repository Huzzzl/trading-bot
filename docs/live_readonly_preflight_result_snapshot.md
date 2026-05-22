# Live Read-Only Broker Preflight Result Snapshot

Non-sensitive result snapshot of a manual operator run of
`live_broker_preflight_readonly` following the procedure in
[docs/live_readonly_preflight_runbook.md](live_readonly_preflight_runbook.md).

**This PR does not contact Alpaca.**
**This PR does not read credentials.**
**This PR does not submit, cancel, or replace orders.**
**This PR does not write the live ledger.**
**This PR does not enable live trading.**
**This PR does not bypass `config_safety`.**
**PASS is a precondition check only — not approval to trade.**
**No raw output artifact, account ID, credential fragment, or sensitive
account metadata is included in this document.**

---

## Run Summary

| Field | Value |
|-------|-------|
| Tool | `src/tools/live_broker_preflight_readonly` |
| Flag used | `--allow-live-broker-api-readonly` |
| Symbol | `SPY` |
| Side | `buy` |
| Notional cap | `≤ 100.0` |
| Result | **PASS** |

---

## Output Invariants (all confirmed)

These seven fields are hardcoded in every result. All were confirmed to hold
in this run.

| Field | Required value | Observed |
|-------|---------------|----------|
| `broker_mutation_calls_made` | `false` | `false` ✓ |
| `credential_values_exposed` | `false` | `false` ✓ |
| `live_submit_enabled` | `false` | `false` ✓ |
| `real_submit_implemented` | `false` | `false` ✓ |
| `submit_order_reachable` | `false` | `false` ✓ |
| `config_safety_still_blocks` | `true` | `true` ✓ |
| `broker_calls_readonly` | `true` | `true` ✓ |

---

## Read-Only Checks Performed

| Check | Endpoint | Result |
|-------|----------|--------|
| Account status | `GET /v2/account` | **PASS** — account active |
| Market clock | `GET /v2/clock` | **PASS** — market open |
| SPY asset metadata | `GET /v2/assets/SPY` | **PASS** — tradable and fractionable |

`broker_calls_made=true` — three read-only GET calls were made.
No other endpoints were contacted.
No POST, PATCH, or DELETE calls were made.
The orders endpoint (`/v2/orders`) was not used.

---

## What Did Not Happen

| Item | Status |
|------|--------|
| Order submitted | No |
| Order cancelled or replaced | No |
| Live ledger written | No |
| Credential values exposed or logged | No |
| `submit_order` called | No |
| `cancel_order` / `replace_order` called | No |
| POST / PATCH / DELETE broker call made | No |
| Orders endpoint contacted | No |
| `config_safety` removed or bypassed | No |
| Live trading enabled | No |
| Automated trading approved | No |

---

## What PASS Means

PASS confirms:

- The live Alpaca broker API was reachable at the time of the run.
- The account was in `"ACTIVE"` status.
- The market was open at the time of the run.
- SPY was `tradable=true` and `fractionable=true`.
- All read-only checks completed without error.
- All seven output invariants held.

PASS does **not**:

- Remove or weaken `config_safety`
- Enable live trading
- Authorize any live order submission
- Bypass any existing guard
- Constitute operator approval to submit a live order

The `config_safety` guard remains the final blocker:
`live_trading_enabled=false`, `live_submit_dry_run=true`,
`live_kill_switch_enabled=true`.

---

## Sensitive Data Exclusions

The following were intentionally excluded from this document:

- Raw `output/live_broker_preflight_readonly.json` artifact
- Account ID or account number
- Credential fragments (API key, secret key)
- Exact buying power or portfolio balance values
- Any other sensitive account metadata

---

## Recommended git tag

```
live-readonly-preflight-pass-observed
```

---

## Next Step

A PASS from `live_broker_preflight_readonly` is a precondition check only.
`config_safety` remains the hard blocker. No order submission path exists in
the current codebase. Any future step toward a real live submit requires:

- A funded live account with non-zero buying power ≥ notional cap
- All three `config_safety` flags explicitly overridden in a local operator
  config (not in `settings.yaml`)
- A dedicated PR reviewed and approved for real live submission
- A separate explicit human approval for that specific submission attempt

None of those steps are taken here.

---

## References

- [docs/live_readonly_preflight_runbook.md](live_readonly_preflight_runbook.md) — operator runbook
- [docs/live_broker_preflight_design.md](live_broker_preflight_design.md) — design and implementation details
- [docs/live_readiness_status.md](live_readiness_status.md) — full readiness status and milestone history
