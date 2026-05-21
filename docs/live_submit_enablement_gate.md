# Live Submit Enablement Gate

Design document defining the exact conditions that must all be satisfied before
the `config_safety` blocker can be removed and a real live order can be
attempted.

**This document does not implement real submit.**
**Real submit is not implemented in the current codebase.**
**config_safety remains the final blocker.**

---

## Gate Checker CLI

`src/tools/live_submit_enablement_gate.py` is a read-only GO/NO_GO checker that
evaluates all conditions listed in this document.

```bash
python -m src.tools.live_submit_enablement_gate \
    --readiness-bundle output/live_v2_bundle/live_v2_readiness_bundle.json \
    --live-trading-approval output/live_trading_approval.json \
    --live-order-submission-approval output/live_order_submission_approval.json \
    --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \
    --output output/live_submit_enablement_gate.json
```

**GO does not submit an order.**
GO only means `config_safety` is the remaining blocker and all documented
preconditions are satisfied.  The operator must still explicitly change the
three config flags (`live_trading_enabled`, `live_submit_dry_run`,
`live_kill_switch_enabled`) before a real order attempt can be made.

The checker never calls Alpaca, never reads credentials, and never writes
the live ledger.  Exit 0 on GO; exit 1 on NO_GO.  Always writes output JSON.

---

## Current Status

| Item | State |
|------|-------|
| Real submit | Not implemented |
| `submit_order` | Unreachable — no call path exists |
| `live_trading_enabled` | `false` (default) |
| `live_submit_dry_run` | `true` (default) |
| `live_kill_switch_enabled` | `true` (default) |
| `config_safety` guard | Blocks on every run |
| Live trading | Disabled — paper-only unless separately approved |
| Alpaca live endpoint | Must not be used |

---

## What Remains Blocked

Live trading remains disabled by default.
No live order can be submitted while any of the following conditions are true:

- `live_trading_enabled=false` (default)
- `live_submit_dry_run=true` (default)
- `live_kill_switch_enabled=true` (default)
- Any v2 approval artifact fails validation
- `live_v2_readiness_bundle.json` does not have `bundle_result="PASS"`
- Pre-submit ledger row has not been written
- Credentials are missing or invalid

All of these are hard blockers. Failure of any one must result in
`blocked=true`, `submit_order_called=false`, and no live order submitted.

---

## Required Artifacts Before Enablement

All of the following artifacts must exist, be valid, and produce the expected
results before `config_safety` can be removed for a real submit attempt.

### v2 Approval Artifacts

| Artifact | Required field | Required value |
|----------|---------------|----------------|
| `live_trading_approval.json` | `live_trading_approved` | `true` |
| `live_trading_approval.json` | `live_order_submission_approved` | `false` |
| `live_trading_approval.json` | `approval_scope` | `AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY` |
| `live_trading_approval.json` | `risk_acknowledged` | `true` |
| `live_order_submission_approval.json` | `live_order_submission_approved` | `true` |
| `live_order_submission_approval.json` | `order_submission_approval_for_single_attempt` | `true` |
| `live_order_submission_approval.json` | `approval_scope` | `AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY` |
| `live_order_submission_approval.json` | `source_live_trading_approval_path` | matches trading approval path |

No live order can be submitted unless all v2 approval artifacts pass the
executor guard validation (`live_v2_approvals_review` rules).

### Readiness Bundle

No live order can be submitted unless `live_v2_readiness_bundle.json` has
`bundle_result="PASS"`. This requires all three sub-reviews to pass:

- `live_v2_approvals_review.json` — `review_result=PASS`
- `live_v2_executor_readiness_review.json` — `review_result=PASS`
- `live_v2_final_readiness_review.json` — `review_result=PASS`

### Executor Blocked Report

`live_submit_blocked_report.json` must exist and show:

- `blocked=true`
- `submit_order_called=false`
- `block_guard=config_safety`

This confirms the executor accepted all v2 approval artifacts and is blocked
only by the config-safety flags — not by any approval or v2 guard.

---

## Required Config Flags

The following config flags must be explicitly set in the operator's local
config before a real submit attempt. They must not become new defaults in
`settings.yaml` or any shared config file.

| Flag | Required value | Current default |
|------|---------------|-----------------|
| `live_trading_enabled` | `true` | `false` |
| `live_submit_dry_run` | `false` | `true` |
| `live_kill_switch_enabled` | `false` | `true` |
| `live_require_human_confirm` | `true` | `true` (unchanged) |
| `live_max_orders_per_day` | `1` | safe low value |
| `live_max_notional_per_day` | `100.0` | safe low value |
| `live_max_order_notional` | `100.0` | safe low value |

No live order can be submitted unless `live_trading_enabled=true`.
No live order can be submitted unless `live_submit_dry_run=false`.
No live order can be submitted unless `live_kill_switch_enabled=false`.

**Do not change these defaults in `settings.yaml` or any shared config file.**
They must be set only in a local operator config that overrides the safe defaults.

---

## Required Manual Approvals

Two separate approval artifacts must be produced by offline CLI tools before a
real submit attempt. Neither may be produced autonomously or by the executor.

1. **`live_trading_approval.json`** — produced by `live_trading_approval` CLI.
   Authorises enabling live trading mode only. Must have
   `live_order_submission_approved=false`.

2. **`live_order_submission_approval.json`** — produced by
   `live_order_submission_approval` CLI. Authorises a single live order
   submission attempt. Must reference `source_live_trading_approval_path`
   pointing to the trading approval artifact.

Combined approval artifacts are not permitted. A single artifact may not set
both `live_trading_approved=true` and `live_order_submission_approved=true`.

Both artifacts require explicit operator flags:
`--operator-name`, `--approval-note`, `--risk-acknowledge`,
`--approval-timestamp-utc`.

---

## Required Readiness Bundle PASS

Before any real submit attempt, the operator must run:

```bash
python -m src.tools.live_v2_readiness_bundle \
    --live-trading-approval output/live_trading_approval.json \
    --live-order-submission-approval output/live_order_submission_approval.json \
    --executor-readiness-report output/live_submit_executor/live_submit_blocked_report.json \
    --output-dir output/live_v2_bundle
```

The bundle must produce:
- `live_v2_readiness_bundle.json` with `bundle_result="PASS"`
- All three sub-review artifacts with `review_result=PASS`

No live order can be submitted unless `live_v2_readiness_bundle.json` has
`bundle_result="PASS"`.

---

## Required Environment Variables

API keys must not be committed to source control or written to logs.
Missing credentials must fail closed.

| Variable | Notes |
|----------|-------|
| `ALPACA_LIVE_API_KEY` | Must not be committed. Missing → fail closed. |
| `ALPACA_LIVE_SECRET_KEY` | Must not be committed. Missing → fail closed. |

If either variable is missing or empty at runtime, the executor must fail
closed with `blocked=true` before any broker call. No partial credential usage.
API keys must not be logged, printed to stdout, or written to any artifact file.

---

## Required Ledger Behavior

The live ledger must record every submit attempt, regardless of outcome.

### Pre-submit ledger row

A pre-submit ledger row must be written before `submit_order` is called.
The row must include all intent fields: symbol, notional, client_order_id,
timestamp, and status `attempting`.

No live order can be submitted unless the pre-submit ledger row has been
successfully written.

### Post-submit ledger update

After `submit_order` returns:

| Outcome | Ledger status | Additional fields |
|---------|--------------|-------------------|
| Accepted | `submitted` | `broker_order_id` |
| Broker rejection | `rejected` | rejection reason |
| Exception raised | `exception` | exception message |

Any exception during or after `submit_order` must update the same ledger row
as `failed/exception`. The ledger row written before the call and the update
after the call must reference the same `client_order_id`.

The ledger must never be left in `attempting` status after a submit attempt
completes or fails.

---

## Required Pre-Submit Checks

All of the following must pass immediately before `submit_order` is called:

| Check | Description |
|-------|-------------|
| Confirm token | Must equal `"REAL-LIVE-SUBMIT-AUTHORIZED"` |
| v2 trading approval | `live_trading_approved=true`, scope, risk_acknowledged |
| v2 submission approval | `live_order_submission_approved=true`, scope, source path match |
| v2 cross-check | Symbols match, submission notional ≤ trading notional, separate files |
| Config safety | `live_trading_enabled=true`, `live_submit_dry_run=false`, `live_kill_switch_enabled=false` |
| Human confirm | `live_require_human_confirm=true` |
| Pre-submit checklist | `final_result=READY` |
| Plan review | `review_result=PASS` |
| `live_readiness_gate` | Decision `GO`, all five stages PASS |
| Readiness bundle | `bundle_result=PASS` in `live_v2_readiness_bundle.json` |
| Daily order count | Orders today `< live_max_orders_per_day` |
| Daily notional | Today total `+ intended ≤ live_max_notional_per_day` |
| No open position | Broker reports zero position for target symbol |
| No open order | Broker reports no open order for target symbol |
| Idempotency | `client_order_id` not already in live ledger |
| Notional cap | `intended_notional ≤ live_max_order_notional` |

Failure of any single check must result in `blocked=true` and no order
submitted. No partial execution, no retry.

---

## Required Post-Submit Reconciliation

After any submit attempt (successful or not):

1. Update the pre-submit ledger row with outcome status and broker fields.
2. Run `live_ledger_verify` to confirm ledger consistency.
3. If `live_ledger_verify` fails, log the error — do not retry the order.
4. Do not delete the ledger artifact under any circumstances.
5. If the order was accepted, confirm the broker-side order ID matches the
   ledger's `broker_order_id` field.

---

## Explicit Non-Goals

This document and the current codebase do not implement or authorise:

- Real live order submission (`submit_order` is unreachable)
- Alpaca live endpoint usage
- Sell orders, short positions, options, crypto
- Bracket orders or attached stop/limit legs
- Scheduled or looping execution
- Retries of any kind after exception or rejection
- Auto-close logic
- Multi-symbol batching
- Any order type other than market (buy, notional)
- Raising config defaults in `settings.yaml`
- Autonomous approval generation
- Credentials in source control or logs

---

## Manual Go / No-Go Checklist

The following must all be verified by the operator before any live submit
attempt. Each item is a hard gate — not a suggestion.

- [ ] Live account funded: `buying_power > 0` and `portfolio_value > 0`
- [ ] `live_readiness_gate` returns `GO` — all five stages PASS
- [ ] At least one symbol passes live sizing
- [ ] `live_trading_approval.json` produced and valid
- [ ] `live_order_submission_approval.json` produced and valid
- [ ] `live_v2_approvals_review` result: `PASS`
- [ ] `live_v2_executor_readiness_review` result: `PASS`
- [ ] `live_v2_final_readiness_review` result: `PASS`
- [ ] `live_v2_readiness_bundle.json` has `bundle_result="PASS"`
- [ ] `live_submit_executor_check` result: `blocked=true`, `block_guard=config_safety`
- [ ] Config flags set in local operator config only (not in `settings.yaml`)
- [ ] `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY` set in environment
- [ ] API keys not present in any committed file or log
- [ ] Pre-submit ledger path writable
- [ ] All pre-submit checks listed above confirmed
- [ ] Rollback procedure reviewed

No item on this checklist may be skipped or deferred.

---

## Rollback Plan

If a live order is submitted and any subsequent check fails, or the operator
decides to abort:

1. Set `live_trading_enabled=false` in local config immediately.
2. Set `live_submit_dry_run=true` in local config.
3. Set `live_kill_switch_enabled=true` in local config.
4. Do **not** delete the ledger artifact — preserve it for audit.
5. If an open position or order exists, close or cancel it manually via the
   Alpaca dashboard. Do not use automated close logic.
6. Run `live_ledger_verify` to confirm ledger consistency.
7. Investigate the cause before any retry. No automated retry is permitted.

---

## References

- [live_readiness.md](live_readiness.md) — full readiness pipeline
- [live_readiness_status.md](live_readiness_status.md) — current gate status
- [live_submit_design.md](live_submit_design.md) — proposed submit flow
- [live_submit_enablement_v2.md](live_submit_enablement_v2.md) — v2 approval layer design
