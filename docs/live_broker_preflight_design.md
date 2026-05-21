# Live Broker Preflight Design (Read-Only)

Design document for a future read-only live broker API preflight tool.

**This PR does not implement broker preflight.**
**This PR does not contact Alpaca.**
**This PR does not approve real trading.**
**This PR does not approve live order submission.**
**No live order may be submitted after this design PR.**
**Real live submit remains unimplemented. `submit_order` remains unreachable.**

The next implementation PR, if any, must still be read-only and manually
run. It must not submit, cancel, or modify any order or account state.

---

## Proposed Tool

```
src/tools/live_broker_preflight_readonly.py
```

Output artifact:

```
output/live_broker_preflight_readonly.json
```

---

## Purpose

Before any future real submit attempt, the operator would run this tool
manually to confirm the live broker API is reachable, the account is in
a suitable state, and asset metadata for the target symbol passes a set
of read-only checks. This is a precondition check only — PASS does not
authorize a live order and does not remove `config_safety`.

---

## Explicit Non-Goals

The future implementation must never:

- Call `submit_order`, `cancel_order`, `replace_order`, or any
  POST/PATCH/DELETE broker endpoint
- Call any order-preview endpoint that reserves buying power or modifies
  account state
- Write or modify any live ledger file
- Remove or bypass `config_safety`
- Enable live trading
- Use a paper endpoint as proof of live readiness
- Store, log, print, or include actual credential values in any output
- Change `settings.yaml` or any shared config file to enable live trading
- Run any automatic retry loop that could hit the broker repeatedly
- Run without a prior PASS from `live_credential_presence_guard` and
  `live_operator_config_override_review`

---

## Prerequisite Artifacts

The tool must verify the following artifacts exist and have the expected
result before making any broker call:

| Artifact | Required field | Required value |
|----------|---------------|----------------|
| `output/live_credential_presence_guard.json` | `result` | `"PASS"` |
| `output/live_operator_config_override_review.json` | `result` | `"PASS"` |

If either artifact is missing or does not have `result="PASS"`, the tool
must produce `result="BLOCKED"` and exit without contacting Alpaca.

---

## Allowed Read-Only Checks

All broker calls must be GET-only.  The tool must maintain an explicit
allowlist of permitted endpoints and reject any call not on that list.

| Check | Allowed endpoint category | Notes |
|-------|--------------------------|-------|
| Account identity | `GET /v2/account` | Log only non-secret metadata; never log API key or secret |
| Account status | `GET /v2/account` | `status` field must be `"ACTIVE"` |
| Pattern-day-trading flag | `GET /v2/account` | Block if `pattern_day_trader=true` and relevant |
| Buying power | `GET /v2/account` | `buying_power` must be ≥ `notional_cap` |
| Market clock | `GET /v2/clock` | Log open/closed status; block if closed when required |
| Asset metadata for SPY | `GET /v2/assets/SPY` | Must be `tradable=true` |

### Endpoint allowlist (design)

```python
_ALLOWED_ENDPOINT_PREFIXES = frozenset({
    "/v2/account",
    "/v2/clock",
    "/v2/assets/",
})
```

Any broker call whose path does not begin with one of these prefixes must
be refused before the request is issued.

---

## Fail-Closed Conditions

The tool must produce `result="BLOCKED"` and exit without submitting any
order when any of the following conditions are true:

| Condition | Description |
|-----------|-------------|
| Missing credential guard artifact | `live_credential_presence_guard.json` absent or `result != "PASS"` |
| Missing operator override artifact | `live_operator_config_override_review.json` absent or `result != "PASS"` |
| Either prerequisite artifact malformed | Cannot be parsed as JSON or missing `result` field |
| Symbol is not `SPY` | Any configured symbol other than `SPY` |
| Side is not `buy` | Any configured side other than `buy` |
| Notional cap missing or `> 100.0` | Config or override artifact notional cap exceeds $100 |
| Account status not `"ACTIVE"` | Broker reports any non-active account status |
| Insufficient buying power | `buying_power < notional_cap` |
| Market closed | If the tool requires open market and `GET /v2/clock` returns `is_open=false` |
| SPY not tradable | `GET /v2/assets/SPY` returns `tradable=false` |
| Non-read-only endpoint attempted | Any POST/PATCH/DELETE call blocked by the allowlist |
| Credential value in output | Any check that would write a real API key or secret to stdout or JSON |
| Any exception from broker client | Uncaught exception during any broker call → BLOCKED |

All failures are hard blockers. No partial execution, no retry.

---

## Required Output Fields

```json
{
  "checked_at_utc": "<ISO-8601 UTC timestamp>",
  "result": "PASS | BLOCKED",
  "broker_calls_made": true,
  "broker_calls_readonly": true,
  "broker_mutation_calls_made": false,
  "credential_values_exposed": false,
  "live_submit_enabled": false,
  "real_submit_implemented": false,
  "submit_order_reachable": false,
  "config_safety_still_blocks": true,
  "endpoint_allowlist_used": ["/v2/account", "/v2/clock", "/v2/assets/"],
  "checks": [
    {
      "name": "account_status",
      "result": "PASS | BLOCKED",
      "detail": "<non-secret metadata only>"
    }
  ],
  "violations": [],
  "blocker": null
}
```

### Field invariants

The following fields must be hardcoded in every output, regardless of
broker response:

| Field | Invariant value |
|-------|----------------|
| `broker_mutation_calls_made` | `false` always |
| `credential_values_exposed` | `false` always |
| `live_submit_enabled` | `false` always |
| `real_submit_implemented` | `false` always |
| `submit_order_reachable` | `false` always |
| `config_safety_still_blocks` | `true` always |
| `broker_calls_readonly` | `true` always |

---

## Proposed CLI

```bash
python -m src.tools.live_broker_preflight_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --side buy \
    --notional-cap 100.0 \
    --output output/live_broker_preflight_readonly.json
```

Exit 0 on PASS; exit 1 on BLOCKED.  Always writes output JSON.

---

## Testing Plan

All unit tests must use a mock broker client.  No real Alpaca calls in
any unit test.

| Test area | What to verify |
|-----------|----------------|
| No `submit_order` reference | Source scan: `submit_order(` must not appear in non-comment, non-docstring lines |
| No `cancel_order` reference | Source scan: `cancel_order(` must not appear |
| No `replace_order` reference | Source scan: `replace_order(` must not appear |
| Endpoint allowlist | Any call with a path outside `_ALLOWED_ENDPOINT_PREFIXES` must raise or return BLOCKED before the request |
| No secrets in output | Inject a known secret value into the mock env; assert it is absent from output JSON and stdout |
| No secrets in stdout | Same injection; capture stdout and assert secret absent |
| Missing credential guard → BLOCKED | Prerequisite artifact absent; assert BLOCKED without broker call |
| Malformed credential guard → BLOCKED | Prerequisite artifact has bad JSON; assert BLOCKED without broker call |
| Credential guard result != PASS → BLOCKED | Prerequisite artifact has `result="BLOCKED"`; assert BLOCKED without broker call |
| Missing operator override → BLOCKED | Same pattern for the second prerequisite artifact |
| Wrong symbol → BLOCKED | `symbol="AAPL"` → BLOCKED |
| Wrong side → BLOCKED | `side="sell"` → BLOCKED |
| Notional cap > 100.0 → BLOCKED | `notional_cap=200.0` → BLOCKED |
| Account status not ACTIVE → BLOCKED | Mock returns `status="INACTIVE"` → BLOCKED |
| Insufficient buying power → BLOCKED | Mock `buying_power=0.0` → BLOCKED |
| SPY not tradable → BLOCKED | Mock asset `tradable=false` → BLOCKED |
| Broker exception → BLOCKED | Mock raises exception on any call → BLOCKED |
| `config_safety_still_blocks` always true | Assert field is `true` in every result |
| `broker_mutation_calls_made` always false | Assert field is `false` in every result |
| `credential_values_exposed` always false | Assert field is `false` in every result |
| `live_submit_enabled` always false | Assert field is `false` in every result |
| PASS path | All mocks return good values; both prerequisite artifacts PASS → result=PASS |

---

## Integration With Existing Pipeline

This tool sits after the existing offline guards and before any future
real submit attempt:

```
live_credential_presence_guard      (PASS required as input)
live_operator_config_override_review (PASS required as input)
         ↓
live_broker_preflight_readonly      (future — manually run, read-only)
         ↓
[future real submit — not implemented]
```

The existing `config_safety` guard remains the final blocker.  A PASS
from `live_broker_preflight_readonly` does not change any config flag
and does not authorize a live order.

---

## Current Implementation Status

| Item | State |
|------|-------|
| Design document | This file |
| `src/tools/live_broker_preflight_readonly.py` | **Not implemented** |
| `output/live_broker_preflight_readonly.json` | Not generated |
| Real Alpaca live endpoint calls | **None — this PR makes zero broker calls** |
| `submit_order` | Unreachable — no call path exists |
| `config_safety` | Still the hard blocker |

---

## References

- [live_submit_enablement_gate.md](live_submit_enablement_gate.md) — gate conditions
- [live_readiness_status.md](live_readiness_status.md) — current readiness status
- [live_submit_enablement_v2.md](live_submit_enablement_v2.md) — v2 approval layer design
- [live_submit_design.md](live_submit_design.md) — proposed submit flow
