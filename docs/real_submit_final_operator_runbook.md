# Real Submit Final Operator Runbook

Final manual runbook for one real live SPY market buy attempt using
`--allow-real-live-submit-once`.

**This PR does NOT run the submit tool.**
**This PR does NOT contact Alpaca.**
**This PR does NOT read credentials.**
**This PR does NOT submit, cancel, or replace any order.**
**This PR does NOT write a live ledger.**
**This PR does NOT approve trading.**
**This PR is only a final operator checklist and runbook.**
**A real order attempt is NOT performed by this PR.**

---

## 1. Preconditions

All of the following must be true before proceeding to any step in this runbook.

### Repository state

- [ ] `main` branch is synced through at least PR #123
- [ ] `git diff origin/main` shows no local uncommitted changes to any tracked file
- [ ] `settings.yaml` is unchanged — verified via `git diff` (no diff output)

### Prerequisite tools available

- [ ] `src/tools/live_credential_presence_guard.py` present
- [ ] `src/tools/live_operator_config_override_review.py` present
- [ ] `src/tools/live_broker_preflight_readonly.py` present
- [ ] `src/tools/live_single_submit_approval_review.py` present
- [ ] `src/tools/live_single_manual_submit.py` present

### Prerequisite artifact freshness

- [ ] `output/live_broker_preflight_readonly.json` — PASS run is from the **current trading session**
  (market conditions current; do not reuse a result from a prior day)
- [ ] `output/live_single_submit_approval_review.json` — PASS, and
  `approval_expires_at_utc` is **strictly after** current UTC

### Local operator config (never committed)

A local operator config file must exist only on the operator's machine and must
**never** be committed to the repository or shared.  It must contain these
strict YAML booleans (not strings, not null):

```yaml
live_trading_enabled: true
live_submit_dry_run: false
live_kill_switch_enabled: false
```

- [ ] Local operator config file exists at the expected local path
- [ ] All three flags are set to the exact values above (strict booleans)
- [ ] File is excluded from git (confirm with `git status` — must not appear)

### Environment variables (current shell only)

Credentials must be set only in the current terminal session and must not be
written to any file, shell profile, or `.env` committed to the repo.

```sh
export ALPACA_LIVE_API_KEY="..."
export ALPACA_LIVE_SECRET_KEY="..."
```

- [ ] `ALPACA_LIVE_API_KEY` is non-empty in the current shell
- [ ] `ALPACA_LIVE_SECRET_KEY` is non-empty in the current shell
- [ ] Neither value is written to any committed file or log

### Order parameters

- [ ] `symbol` is exactly `SPY` (no case folding, no whitespace)
- [ ] `side` is exactly `buy`
- [ ] `order_type` is exactly `market`
- [ ] `notional_cap` is a number in `(0, 100.0]`

### Broker and market state

- [ ] Market is open (regular hours only — not pre/post market)
- [ ] Alpaca account status is `ACTIVE` (live account, not paper)
- [ ] Buying power ≥ `notional_cap` (confirmed in Alpaca dashboard)
- [ ] No existing open SPY order in Alpaca UI (verified manually, not just via API)
- [ ] No existing open SPY position that conflicts with this buy (verified in UI)

### Operator understanding

- [ ] Understood: this is **one attempt only** — no retry if BLOCKED
- [ ] Understood: `cancel_order` and `replace_order` are **not implemented** in code;
  emergency cancel must be performed **manually in the broker UI**
- [ ] Understood: SUBMITTED means the order reached the broker — not that it
  filled, not that a profit is guaranteed
- [ ] Understood: `config_safety` flags must be reset to safe defaults
  immediately after the attempt regardless of outcome

---

## 2. Safety Dry Checks Before Real Submit

Run these commands **in order** before the real submit attempt.
All must return `result="PASS"` (or `result="BLOCKED"` for the final submit
dry check).  Stop and do not proceed if any step fails.

> **Note on shell syntax:** Commands below use Unix `\` line continuations.
> On Windows PowerShell replace `\` with `` ` `` (backtick), or write the
> command as a single line with no line continuations.

### Step 2a — Credential presence guard

```sh
python -m src.tools.live_credential_presence_guard \
    --required-env ALPACA_LIVE_API_KEY \
    --required-env ALPACA_LIVE_SECRET_KEY \
    --output output/live_credential_presence_guard.json
```

Expected: `result="PASS"`

- [ ] PASS confirmed

### Step 2b — Operator config override review

```sh
python -m src.tools.live_operator_config_override_review \
    --override-artifact output/live_operator_config_override.json \
    --output output/live_operator_config_override_review.json
```

Expected: `result="PASS"`

- [ ] PASS confirmed

### Step 2c — Live broker read-only preflight

```sh
python -m src.tools.live_broker_preflight_readonly \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --symbol SPY \
    --side buy \
    --notional-cap <value> \
    --output output/live_broker_preflight_readonly.json \
    --allow-live-broker-api-readonly
```

Expected: `result="PASS"` — account ACTIVE, buying power ≥ notional_cap,
market open, SPY tradable and fractionable.

- [ ] PASS confirmed
- [ ] This run is from the current trading session (timestamp matches today's
  market session)

### Step 2d — Single submit approval review

```sh
python -m src.tools.live_single_submit_approval_review \
    --approval-artifact output/live_single_submit_approval.json \
    --output output/live_single_submit_approval_review.json
```

Expected: `result="PASS"`, `approval_expires_at_utc` strictly after current UTC.

- [ ] PASS confirmed
- [ ] Approval not expired

### Step 2e — Submit dry check (WITHOUT `--allow-real-live-submit-once`)

```sh
python -m src.tools.live_single_manual_submit \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --broker-preflight output/live_broker_preflight_readonly.json \
    --submit-approval output/live_single_submit_approval_review.json \
    --local-operator-config <local-config-path> \
    --symbol SPY \
    --side buy \
    --order-type market \
    --notional-cap <value> \
    --ledger <local-ledger-path> \
    --output output/live_single_manual_submit_drycheck.json
```

Expected output fields:

| Field | Expected |
|-------|---------|
| `result` | `"BLOCKED"` |
| `submit_order_called` | `false` |
| `broker_mutation_calls_made` | `false` |
| `live_ledger_written` | `false` |
| `blocker` | `"real live submit adapter not implemented"` |

- [ ] `result="BLOCKED"` confirmed
- [ ] `submit_order_called=false` confirmed
- [ ] `broker_mutation_calls_made=false` confirmed
- [ ] `live_ledger_written=false` confirmed
- [ ] `blocker="real live submit adapter not implemented"` confirmed

**Do not proceed if the dry check does not return BLOCKED with all fields above.**

---

## 3. Final Pre-Submit Checklist

Confirm every item personally before issuing the real submit command.

- [ ] The exact command below includes `--allow-real-live-submit-once`
- [ ] `notional_cap` value is correct and buying power ≥ that value
- [ ] Approval artifact (`output/live_single_submit_approval_review.json`) is
  not expired — `approval_expires_at_utc` strictly after current UTC
- [ ] Preflight result (`output/live_broker_preflight_readonly.json`) is from
  the current trading session — not stale
- [ ] Local operator config flags are **intentionally** set:
  - `live_trading_enabled: true`
  - `live_submit_dry_run: false`
  - `live_kill_switch_enabled: false`
- [ ] Output artifact path and ledger path are **local only** — not committed
  to the repository
- [ ] Config reset command (Section 6) is ready in a second terminal window
- [ ] Emergency cancel plan confirmed: if order submits unexpectedly, cancel
  manually in Alpaca broker UI — the code has no cancel/replace logic

---

## 4. Real Submit Command

> **DANGEROUS — issues a real live market order to Alpaca.**
> **A real order will be submitted if all gates pass.**
> **Do not run this unless all steps in Sections 1–3 are complete.**

```sh
python -m src.tools.live_single_manual_submit \
    --credential-guard output/live_credential_presence_guard.json \
    --operator-override output/live_operator_config_override_review.json \
    --broker-preflight output/live_broker_preflight_readonly.json \
    --submit-approval output/live_single_submit_approval_review.json \
    --local-operator-config <local-config-path> \
    --symbol SPY \
    --side buy \
    --order-type market \
    --notional-cap <value> \
    --ledger <local-ledger-path> \
    --output output/live_single_manual_submit_attempt.json \
    --allow-real-live-submit-once
```

Replace `<value>` with the exact notional cap (e.g. `50`).
Replace `<local-config-path>` with the path to the local operator config file.
Replace `<local-ledger-path>` with the path to the local ledger CSV file.

**Only one run.**  If the result is BLOCKED, do not retry.  Inspect the
`blocker` field, resolve the underlying condition, obtain a fresh approval
artifact, and start from Section 1.

---

## 5. Expected Outputs

### SUBMITTED (order reached the broker)

| Field | Expected |
|-------|---------|
| `result` | `"SUBMITTED"` |
| `order_submitted` | `true` |
| `submit_order_called` | `true` |
| `broker_mutation_calls_made` | `true` |
| `broker_order_id_redacted` | `"<redacted>"` |
| `live_ledger_written` | `true` |

SUBMITTED means the order reached the Alpaca broker.  It does **not** mean
the order filled.  Check the Alpaca dashboard for fill status.

### BLOCKED (order was not submitted)

| Field | Expected |
|-------|---------|
| `result` | `"BLOCKED"` |
| `order_submitted` | `false` |
| `submit_order_called` | `false` or `true` (if broker exception) |
| `broker_mutation_calls_made` | `false` |
| `live_ledger_written` | `false` or `true` (if ledger write preceded exception) |

If BLOCKED:

- **Do not retry.**
- Inspect the `blocker` field for the failure reason.
- Reset `config_safety` flags immediately (Section 6).
- Do not rerun without a fresh operator approval artifact.
- Resolve the underlying condition before any new attempt.

---

## 6. Immediate Post-Run Actions

Perform all of the following immediately after the run, regardless of whether
the result was SUBMITTED or BLOCKED.

### Clear credentials from environment

```sh
unset ALPACA_LIVE_API_KEY
unset ALPACA_LIVE_SECRET_KEY
```

- [ ] `ALPACA_LIVE_API_KEY` unset
- [ ] `ALPACA_LIVE_SECRET_KEY` unset

### Reset local operator config to safe defaults

Update the local operator config file:

```yaml
live_trading_enabled: false
live_submit_dry_run: true
live_kill_switch_enabled: true
```

- [ ] `live_trading_enabled` reset to `false`
- [ ] `live_submit_dry_run` reset to `true`
- [ ] `live_kill_switch_enabled` reset to `true`

### Verify ledger (if SUBMITTED)

```sh
python -m src.tools.live_ledger_verify \
    --ledger <local-ledger-path> \
    --output output/live_ledger_verify.json
```

Expected: ledger row with `status="submitted"`, `broker_order_id="<redacted>"`.

- [ ] Ledger verified (or BLOCKED — skip if no ledger written)

### Output artifacts

- [ ] Output artifact (`live_single_manual_submit_attempt.json`) is **not**
  committed to the repository
- [ ] Ledger file is **not** committed to the repository
- [ ] Confirm with `git status` — neither file should appear as staged or tracked

### Check Alpaca UI

- [ ] Log in to Alpaca dashboard and check order status manually
- [ ] If order exists and emergency action is needed, use the broker UI
  directly — the code has **no `cancel_order` or `replace_order` logic**
- [ ] Note fill price, fill quantity, and order status for records

---

## 7. What to Report Back

When reporting the outcome, include **only** the following non-sensitive
summary fields from the output artifact:

| Field | Include |
|-------|---------|
| `result` | ✓ |
| `order_submitted` | ✓ |
| `submit_order_called` | ✓ |
| `broker_mutation_calls_made` | ✓ |
| `cancel_order_called` | ✓ |
| `replace_order_called` | ✓ |
| `live_ledger_written` | ✓ |
| `credential_values_exposed` | ✓ |
| `blocker` (if BLOCKED) | ✓ |

**Do NOT report any of the following:**

- Credential values or key fragments
- Account ID or account number
- Raw broker order ID
- Raw JSON output from the broker response
- Account balance or buying power
- Fill price or fill quantity
- Any broker response details beyond the fields listed above

---

## 8. Abort Conditions

Abort **before the real submit command** if any of the following is detected:

| Condition | Action |
|-----------|--------|
| Any prerequisite artifact missing or `result != "PASS"` | Stop — do not proceed |
| Approval artifact expired (`approval_expires_at_utc` ≤ current UTC) | Stop — obtain fresh approval |
| Preflight result is stale (different trading session) | Stop — re-run preflight |
| Market is closed or outside regular hours | Stop — wait for next session |
| `symbol` is not exactly `"SPY"` | Stop |
| `side` is not exactly `"buy"` | Stop |
| `order_type` is not exactly `"market"` | Stop |
| `notional_cap` ≤ 0 or > 100.0 | Stop |
| Local operator config missing or invalid | Stop |
| Any `config_safety` flag wrong or absent | Stop |
| `settings.yaml` has any diff vs `origin/main` | Stop — revert before proceeding |
| Dry check (Step 2e) did not return BLOCKED | Stop — investigate |
| Any credential-like value visible in any output | Stop — do not proceed |
| Any unexpected cancel/replace/retry code path observed | Stop — unreachable; investigate |
| Operator has any uncertainty about any item above | Stop — resolve uncertainty first |

Abort **during or after the real submit** if:

| Condition | Action |
|-----------|--------|
| Exception raised by broker | Catch handled automatically — check `result` in output |
| Order submitted but unexpected behavior observed in broker UI | Cancel manually in broker UI — code has no cancel logic |
| Output artifact contains credential-like values | Do not share, do not commit |

A BLOCKED result is **final** for this attempt:

- Do not retry with modified parameters.
- Do not re-run without a new operator approval artifact.
- Reset `config_safety` flags immediately.
- Resolve the underlying condition before any new attempt.

---

## Suggested Git Tag

```
real-submit-final-operator-runbook-prepared
```

---

## References

- `src/tools/live_single_manual_submit.py` — real adapter implementation (PR #122)
- `src/tools/live_single_submit_approval_review.py` — approval review tool
- `tests/test_live_single_manual_submit.py` — 255 tests, all mock-only
- [docs/live_readiness_status.md](live_readiness_status.md) — full readiness status and milestone history
- [docs/live_submit_adapter_readiness_checklist.md](live_submit_adapter_readiness_checklist.md) — adapter readiness checklist
- [docs/single_manual_live_submit_attempt_design.md](single_manual_live_submit_attempt_design.md) — submit flow design
- [docs/real_submit_without_flag_blocked_snapshot.md](real_submit_without_flag_blocked_snapshot.md) — BLOCKED dry-run snapshot (PR #123)

---

## Warning

> **This runbook does not approve real trading.**
> **This runbook does not approve live order submission.**
> **No real order has been submitted by this PR.**
> **No Alpaca endpoint was contacted by this PR.**
> **No credentials were read by this PR.**
> The `--allow-real-live-submit-once` flag is required for any real submit
> attempt. Without it, CLI is always BLOCKED.
> A real order attempt additionally requires a funded live account, fresh PASS
> runs of all four prerequisite tools, valid credentials in environment
> variables, and explicit local operator config overrides — none of which
> are committed to this repository.
> `config_safety` overrides must be reset to safe defaults immediately after
> any attempt (SUBMITTED or BLOCKED).
> Emergency cancel must be performed manually in the broker UI — the code
> has no `cancel_order` or `replace_order` logic.
