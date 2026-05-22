# Live Read-Only Broker Preflight Runbook

Operator runbook for manually running `live_broker_preflight_readonly` after
PR #112 / PR #113.

**This runbook does NOT run the preflight.**
**This PR must not contact Alpaca.**
**This PR must not read credentials.**
**This PR must not submit, cancel, or replace orders.**
**This PR must not write the live ledger.**
**This PR must not enable live trading.**
**This PR must not bypass `config_safety`.**
**PASS from preflight is a precondition check only — not approval to trade.**

---

## 1. Preconditions

All of the following must be true before starting.

### 1.1 Repository

- `main` branch is up to date with the remote.
- PR #112 (`add-live-broker-preflight-readonly-alpaca-adapter`) is merged.
- Python environment is installed and `alpaca-py` is available.

### 1.2 Required prior artifacts

These artifacts must exist locally from previous offline guard runs.
Both must have `result="PASS"`.

| Artifact | Required field | Required value |
|----------|---------------|----------------|
| `output/live_credential_presence_guard.json` | `result` | `"PASS"` |
| `output/live_operator_config_override_review.json` | `result` | `"PASS"` |

If either is missing or not `"PASS"`, stop here and run the corresponding
guard first. Do not pass `--allow-live-broker-api-readonly` until both are
`"PASS"`.

To regenerate:

```bash
# Credential presence guard (offline — does not validate against Alpaca)
python -m src.tools.live_credential_presence_guard \
    --required-env ALPACA_LIVE_API_KEY \
    --required-env ALPACA_LIVE_SECRET_KEY \
    --output output/live_credential_presence_guard.json
# Expected: result="PASS", exit 0

# Operator override review (offline — reads local override artifact only)
python -m src.tools.live_operator_config_override_review \
    --override-artifact output/live_operator_config_override.json \
    --output output/live_operator_config_override_review.json
# Expected: result="PASS", exit 0
```

### 1.3 Live environment variables

Set live credentials in the local shell only. Do not add them to any config
file, `.env`, or committed artifact.

```bash
export ALPACA_LIVE_API_KEY="<your-live-api-key>"
export ALPACA_LIVE_SECRET_KEY="<your-live-secret-key>"
```

> **Never print, log, store, or commit credential values.**
> Clear them immediately after the run (see Section 6).

### 1.4 Parameter constraints

The following are hard requirements enforced by the tool. Any deviation
produces `result="BLOCKED"` before any broker call is made.

| Parameter | Required value |
|-----------|---------------|
| `--symbol` | `SPY` |
| `--side` | `buy` |
| `--notional-cap` | `> 0` and `<= 100.0` |

---

## 2. Dry Safety Check (Before Live API Contact)

Run these commands before using `--allow-live-broker-api-readonly`.
They confirm the offline guard chain is intact and that the tool correctly
blocks without the flag.

### 2.1 Verify credential guard artifact

```bash
python -m src.tools.live_credential_presence_guard \
    --required-env ALPACA_LIVE_API_KEY \
    --required-env ALPACA_LIVE_SECRET_KEY \
    --output output/live_credential_presence_guard.json
```

Expected: `result="PASS"`, exit 0.

### 2.2 Verify operator override review artifact

```bash
python -m src.tools.live_operator_config_override_review \
    --override-artifact output/live_operator_config_override.json \
    --output output/live_operator_config_override_review.json
```

Expected: `result="PASS"`, exit 0.

### 2.3 Dry run WITHOUT the live flag

Run the preflight tool **without** `--allow-live-broker-api-readonly`.
This must produce BLOCKED with zero broker calls. It confirms the flag gate
is working before any live API contact is attempted.

```bash
python -m src.tools.live_broker_preflight_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --side buy \
    --notional-cap 100.0 \
    --output output/live_broker_preflight_readonly_dry.json
```

Expected output (inspect `output/live_broker_preflight_readonly_dry.json`):

```json
{
  "result": "BLOCKED",
  "broker_calls_made": false,
  "broker_mutation_calls_made": false,
  "credential_values_exposed": false,
  "live_submit_enabled": false,
  "real_submit_implemented": false,
  "submit_order_reachable": false,
  "config_safety_still_blocks": true,
  "broker_calls_readonly": true
}
```

**If `broker_calls_made` is not `false` or `result` is not `"BLOCKED"`,
stop immediately. Do not proceed.**

---

## 3. Manual Live Read-Only Preflight Command

Only run this after all preconditions in Sections 1 and 2 are satisfied.

```bash
python -m src.tools.live_broker_preflight_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --side buy \
    --notional-cap 100.0 \
    --output output/live_broker_preflight_readonly.json \
    --allow-live-broker-api-readonly
```

Exit 0 = PASS. Exit 1 = BLOCKED. Output always written to
`output/live_broker_preflight_readonly.json`.

The tool performs exactly three read-only broker calls:

| Call | Endpoint | Purpose |
|------|----------|---------|
| `get_account()` | `GET /v2/account` | Account status, buying power, PDT flag |
| `get_clock()` | `GET /v2/clock` | Market open/closed state |
| `get_asset("SPY")` | `GET /v2/assets/SPY` | Tradability check |

No other endpoints are contacted. No POST/PATCH/DELETE calls are made.
No orders endpoint (`/v2/orders`) is used.

---

## 4. Expected Output Interpretation

Inspect `output/live_broker_preflight_readonly.json` after the run.

### 4.1 Invariant fields — must always be these values

Regardless of PASS or BLOCKED, these seven fields are hardcoded:

| Field | Required value |
|-------|---------------|
| `broker_mutation_calls_made` | `false` |
| `credential_values_exposed` | `false` |
| `live_submit_enabled` | `false` |
| `real_submit_implemented` | `false` |
| `submit_order_reachable` | `false` |
| `config_safety_still_blocks` | `true` |
| `broker_calls_readonly` | `true` |

**If any of these fields deviate from the required value, abort immediately.**

### 4.2 PASS result

`result="PASS"` means:

- Account status is `"ACTIVE"`
- Buying power ≥ `notional_cap`
- Market clock returned successfully
- SPY is `tradable=true`
- No disallowed endpoints were contacted
- No credential values appeared in output

PASS is a **precondition check only**. It does NOT:

- Remove or weaken `config_safety`
- Enable live trading
- Authorize any live order submission
- Bypass any existing guard

The `config_safety` guard remains the final blocker. No order can be placed
as a result of this PASS.

### 4.3 BLOCKED result

`result="BLOCKED"` means one or more checks failed. Review the `violations`
and `blocker` fields for the specific reason. Do not attempt to override or
work around a BLOCKED result. See Section 5 for abort conditions.

---

## 5. Abort Conditions

Stop immediately and do not proceed if any of the following are true.

| Condition | Action |
|-----------|--------|
| Output JSON contains any string resembling a credential value (API key, secret) | Stop. Treat as a security incident. |
| `broker_calls_made=false` when a live check was expected | Investigate before retrying |
| `broker_mutation_calls_made` is not `false` | Stop immediately |
| `submit_order_reachable` is not `false` | Stop immediately |
| Any reference to `/v2/orders` in output | Stop immediately |
| Any reference to POST, PATCH, or DELETE in output | Stop immediately |
| `result="BLOCKED"` for any of the following reasons: | Do not proceed |
| — Market is closed (`is_open=false`) | Wait for market hours |
| — Account status not `"ACTIVE"` | Resolve account issue with broker |
| — Insufficient buying power (`buying_power < notional_cap`) | Fund account before proceeding |
| — SPY not tradable or not fractionable | Resolve with broker before proceeding |
| — `pattern_day_trader=true` | Resolve PDT restriction before proceeding |
| Any unhandled exception visible in stdout | Investigate before retrying |

Do not raise `notional_cap`, change the symbol, or change the side to work
around a BLOCKED result.

---

## 6. Post-Run Handling

### 6.1 Clear credentials immediately

```bash
unset ALPACA_LIVE_API_KEY
unset ALPACA_LIVE_SECRET_KEY
```

```powershell
Remove-Item Env:\ALPACA_LIVE_API_KEY    -ErrorAction SilentlyContinue
Remove-Item Env:\ALPACA_LIVE_SECRET_KEY -ErrorAction SilentlyContinue
```

### 6.2 Save the output artifact locally

Keep `output/live_broker_preflight_readonly.json` locally for audit.

### 6.3 Do not commit sensitive output

Do not commit:
- Any output artifact that may contain account metadata (buying power, etc.)
- Any file containing credential values
- Any local override config with credential references

### 6.4 Do not proceed to submit

A PASS from this tool does not authorize a live order. The `config_safety`
guard still blocks all submit paths. Do not attempt to override
`live_trading_enabled`, `live_submit_dry_run`, or `live_kill_switch_enabled`
in `settings.yaml`.

### 6.5 Next step after a successful manual run

If the preflight returns PASS, the appropriate next step is a **docs-only
result snapshot PR** containing:

- A non-sensitive summary of which checks passed (account status, clock, SPY
  tradability) — **no account balance values, no credential fragments**
- Confirmation that `broker_mutation_calls_made=false`,
  `credential_values_exposed=false`, `config_safety_still_blocks=true`
- Explicit statement that no order was submitted and `config_safety` was not
  bypassed

Do not include raw `output/` artifact files in that PR.

---

## 7. Explicit Non-Goals

This runbook does not cover and must never lead to:

| Non-goal | Notes |
|----------|-------|
| Live order submission | `submit_order` is not implemented for live |
| Automated or recurring trading | Not approved, not implemented |
| `config_safety` override or removal | Remains the hard blocker |
| Live ledger write | No ledger row is written by this tool |
| Order placement of any kind | No orders endpoint, no POST/PATCH/DELETE |
| Credential exposure | Credentials must never appear in output or commits |
| Paper endpoint as substitute | Paper endpoint does not prove live readiness |

---

## References

- [docs/live_broker_preflight_design.md](live_broker_preflight_design.md) — design and implementation details
- [docs/live_readiness_status.md](live_readiness_status.md) — milestone snapshot and safety status
- [docs/live_submit_enablement_gate.md](live_submit_enablement_gate.md) — gate conditions
- [docs/live_submit_design.md](live_submit_design.md) — proposed submit flow (not implemented)
