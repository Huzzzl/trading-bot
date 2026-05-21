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

## Pre-Submit Ledger Dry-Run CLI

`src/tools/live_pre_submit_ledger_dry_run.py` is an offline dry-run writer that
proves the required pre-submit ledger row can be written before any real order
attempt.  It requires a GO gate artifact, validates order metadata, and appends
one row with ``status="attempting"`` to the live submit ledger CSV.

```bash
python -m src.tools.live_pre_submit_ledger_dry_run \
    --enablement-gate output/live_submit_enablement_gate.json \
    --symbol SPY \
    --side buy \
    --notional 100.0 \
    --client-order-id LIVE-TEST-000001 \
    --ledger output/live_submit_ledger.csv \
    --output output/live_pre_submit_ledger_dry_run.json
```

**This tool does not submit orders and makes no real broker calls.**
It only writes the ledger CSV row.  Future real submit must reuse the same
ledger schema and update the same ``client_order_id`` row after the order
attempt completes.

The tool blocks (``result="BLOCKED"``) when:
- enablement gate decision is not ``"GO"``
- symbol is empty, side is not ``"buy"``, notional ≤ 0, or client_order_id is empty
- ``client_order_id`` is already present in the ledger (idempotency guard)

Exit 0 on ``LEDGER_DRY_RUN_WRITTEN``; exit 1 on ``BLOCKED``.  Always writes
output JSON.  Never calls Alpaca.  Never reads credentials.

---

## Post-Submit Ledger Update Dry-Run CLI

`src/tools/live_post_submit_ledger_update_dry_run.py` is an offline dry-run
updater that proves the same pre-submit ledger row can be updated with a
hypothetical submit outcome.  It requires the existing ledger to contain
exactly one ``attempting`` row matching the provided ``client_order_id``.

```bash
python -m src.tools.live_post_submit_ledger_update_dry_run \
    --ledger output/live_submit_ledger.csv \
    --client-order-id LIVE-TEST-000001 \
    --outcome submitted \
    --broker-order-id ALPACA-ORDER-123 \
    --output output/live_post_submit_ledger_update_dry_run.json
```

**This tool does not submit orders and makes no real broker calls.**
It rewrites the ledger CSV in place, updating only the matching row.
Future real submit must update the same ``client_order_id`` row with the
actual ``broker_order_id``, ``error``, and outcome after the order attempt.

Valid outcomes: ``submitted`` (requires ``--broker-order-id``),
``rejected`` (requires ``--error``), ``exception`` (requires ``--error``).

Exit 0 on ``LEDGER_DRY_RUN_UPDATED``; exit 1 on ``BLOCKED``.  Always writes
output JSON.  Never calls Alpaca.  Never reads credentials.

---

## Ledger Verifier CLI

`src/tools/live_ledger_verify.py` (with ``--output``) is an offline validator
for the live submit ledger CSV after pre- and post-submit dry-run operations.

```bash
python -m src.tools.live_ledger_verify \
    --ledger output/live_submit_ledger.csv \
    --output output/live_ledger_verify.json

# allow in-progress attempting rows during a dry-run sequence:
python -m src.tools.live_ledger_verify \
    --ledger output/live_submit_ledger.csv \
    --output output/live_ledger_verify.json \
    --allow-attempting
```

The verifier checks:
- Exact schema match against ``LEDGER_COLUMNS``
- Non-empty, unique ``client_order_id`` for every row
- Valid ``status`` (attempting / submitted / rejected / exception)
- ``submitted`` rows: non-empty ``broker_order_id``, empty ``error``
- ``rejected`` / ``exception`` rows: non-empty ``error``
- ``attempting`` rows only permitted with ``--allow-attempting``
- Non-empty ``source_enablement_gate``, ``symbol``; ``side=="buy"``; ``notional > 0``

**Without ``--allow-attempting``, any row with ``status="attempting"`` is a
violation.** Future real submit must leave ``live_ledger_verify`` PASS after
every submit attempt (i.e. no row may remain in ``attempting`` status).

Exit 0 on PASS; exit 1 on FAIL.  Always writes output JSON.  Offline only —
never calls Alpaca, never reads credentials, never submits orders.

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
`--operator-name`, `--approval-note`, `--risk-acknowledge`.
`approval_timestamp_utc` is generated by the CLI and must be present in the artifact.

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

## Operator Config Override Review CLI

`src/tools/live_operator_config_override_review.py` is an offline read-only
reviewer that validates a locally-produced operator config override artifact.
The artifact is a JSON file the operator manually writes to explicitly
acknowledge all preconditions before a future single live order attempt.

```bash
python -m src.tools.live_operator_config_override_review \
    --override-artifact output/live_operator_config_override.json \
    --output output/live_operator_config_override_review.json
```

**PASS does not remove `config_safety` and does not approve real trading.**
PASS only means the artifact is structurally valid and all required
acknowledgements are present.

The tool blocks (`result="BLOCKED"`) when:
- the override artifact file is missing or cannot be parsed
- `config_safety_acknowledged` is not `true`
- `submit_order_unreachable_acknowledged` is not `true`
- `real_live_submit_unimplemented_acknowledged` is not `true`
- `approval_scope` is not `"AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"`
- `symbol` is not `"SPY"`
- `side` is not `"buy"`
- `notional_cap` is not in the range (0, 100.0]
- `recurring_trading_approved` is `true`
- `automated_trading_approved` is `true`
- `operator_name` or `approval_note` is empty

The override artifact must be produced manually by the operator.  It must
not be generated autonomously or by any executor.  Example minimal artifact:

```json
{
  "config_safety_acknowledged": true,
  "submit_order_unreachable_acknowledged": true,
  "real_live_submit_unimplemented_acknowledged": true,
  "approval_scope": "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
  "symbol": "SPY",
  "side": "buy",
  "notional_cap": 100.0,
  "recurring_trading_approved": false,
  "automated_trading_approved": false,
  "operator_name": "your-name",
  "approval_note": "Single SPY buy market order, $100 cap, one attempt."
}
```

Output JSON fields: `result`, `config_safety_override_reviewed`,
`live_submit_enabled` (always `false`), `real_submit_implemented` (always
`false`), `submit_order_reachable` (always `false`), `broker_calls_made`
(always `false`), `credentials_read` (always `false`), `violations`, `blocker`.

Exit 0 on PASS; exit 1 on BLOCKED.  Always writes output JSON.
Never calls Alpaca.  Never reads credentials.  Never writes the live ledger.
Never enables live trading.  Never removes `config_safety`.

---

## References

- [live_readiness.md](live_readiness.md) — full readiness pipeline
- [live_readiness_status.md](live_readiness_status.md) — current gate status
- [live_submit_design.md](live_submit_design.md) — proposed submit flow
- [live_submit_enablement_v2.md](live_submit_enablement_v2.md) — v2 approval layer design
