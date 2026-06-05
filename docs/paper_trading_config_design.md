# Paper Trading Configuration Design

Design document for S16: the paper trading configuration artifact schema.

**S16 is docs-only. No source code changes. No tests. No config files.**
**No paper or live trading approval. No broker, API, credential, env,**
**network, or order access. No automatic execution or promotion.**

---

## 1. Purpose

After an S15 manual review produces `APPROVED_FOR_PAPER_CONFIG_DESIGN`, the
next step is to design a structured configuration artifact that would govern
a future paper trading run for the approved candidate.

This document defines:

- the preconditions required before any paper config may be authored
- the proposed paper config schema and field semantics
- fields that are explicitly forbidden from the config
- the config approval status vocabulary
- validation rules for a future S17 offline config validator
- the relationship to S17 and further S-series work

No paper config file is created in this PR. No paper trading is approved.

---

## 2. Scope

| Item | In scope |
|------|----------|
| Define paper config schema fields | Yes |
| Define forbidden config fields | Yes |
| Define config approval status vocabulary | Yes |
| Define S17 validation rules | Yes |
| Describe S17 implementation plan | Yes |
| Create any actual config file | **No** |
| Implement config loading or parsing | **No** |
| Implement paper trading | **No** |
| Implement live trading | **No** |
| Add broker, API, Alpaca, or credential access | **No** |
| Read environment variables | **No** |
| Make network calls | **No** |
| Submit, request, or cancel any order | **No** |
| Approve paper or live trading | **No** |
| Write any source code or tests | **No** |
| Write any output, report, or artifact | **No** |

---

## 3. Preconditions

Both conditions must be met before any paper config may be authored:

1. The candidate must have S14 `CandidatePromotionResult.status ==
   PAPER_CANDIDATE_ELIGIBLE`.
2. The S15 manual review must have recorded decision
   `APPROVED_FOR_PAPER_CONFIG_DESIGN`.

These preconditions do not approve paper trading. They only permit authoring
a paper config schema document for further review. Paper trading requires
additional steps (see §7 and §9).

---

## 4. Proposed Paper Config Schema

A paper config artifact would be a JSON file (never committed to the
repository; stored in a local operator-controlled directory outside
`data/`, `output/`, and the repository root). The schema below uses
`"PC/1.0"` as its version identifier.

All fields are proposal-stage; exact types and allowed values are subject
to S17 validation design.

### 4.1 Provenance fields

| Field | Type | Description |
|-------|------|-------------|
| `config_schema_version` | string | Schema version; must be `"PC/1.0"` |
| `candidate_id` | string | Must match S14/S15 evidence `candidate_id` |
| `run_id` | string | Must match S10/S11 persistence `run_id` |
| `source_git_sha` | string | Git SHA from manifest; must be traceable |

### 4.2 Strategy identity fields

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | string | Trading symbol (e.g. `"SPY"`) |
| `interval` | string | Bar interval (e.g. `"60m"`) |
| `strategy_family` | string | Strategy family enum value (e.g. `"trend_breakout"`) |
| `holding_horizon` | string | Holding horizon enum value (e.g. `"one_to_two_days"`) |

### 4.3 Paper account fields

| Field | Type | Description |
|-------|------|-------------|
| `paper_account_label` | string | Human-readable label for the paper account (e.g. `"alpaca-paper-primary"`); no credentials stored |

### 4.4 Risk limit fields

| Field | Type | Description |
|-------|------|-------------|
| `max_notional_per_position` | number | Maximum dollar notional for a single position (must be finite and positive) |
| `max_position_fraction` | number | Maximum fraction of account value in a single position; default cap: 0.10 (10%) |
| `max_daily_loss` | number | Maximum dollar loss allowed in a single trading day before halt (must be finite and positive) |
| `max_drawdown_stop` | number | Maximum cumulative drawdown fraction from account peak before halt (expressed as a positive decimal, e.g. `0.05` = 5%) |
| `max_orders_per_day` | integer | Maximum orders permitted per trading day (must be finite, positive, and conservative) |
| `min_cash_buffer` | number | Minimum cash fraction to keep undeployed (expressed as a positive decimal) |

### 4.5 Execution assumption fields

| Field | Type | Description |
|-------|------|-------------|
| `allowed_order_types` | list[string] | Allowed order types; restricted to safe paper-only subset (e.g. `["market", "limit"]`); no stop-loss orders that trigger live execution without confirmation |
| `allowed_session` | string | Allowed trading session (e.g. `"regular"`, `"extended"`); `"regular"` by default |
| `slippage_bps_assumption` | number | Assumed slippage in basis points for post-trade simulation analysis |
| `commission_bps_assumption` | number | Assumed commission in basis points for post-trade simulation analysis |

### 4.6 Review and approval fields

| Field | Type | Description |
|-------|------|-------------|
| `risk_review_notes` | string | Free-text notes from the config reviewer |
| `reviewer_name` | string | Name of the reviewer who authored and approved the config |
| `review_date_utc` | string | ISO-8601 UTC timestamp of the review |
| `approval_status` | string | One of the values in §5; must not imply live trading |

---

## 5. Explicitly Forbidden Config Fields

The following categories of information must never appear in a paper config
file under any field name:

| Forbidden category | Examples |
|--------------------|---------|
| Broker API credentials | `api_key`, `secret_key`, `api_secret`, `auth_token` |
| Account identifiers linking to live accounts | `live_account_id`, `production_account_number` |
| Environment variable names containing secrets | References that cause a runtime to read `os.environ` for keys |
| Direct order instructions | Fields that encode a specific order to submit at load time |
| Market data subscription credentials | Data feed API keys, WebSocket auth tokens |
| Anything enabling live trading | Any field whose presence or value could route execution to a live account |

A future S17 validator must reject any config containing field names or values
that match these patterns.

---

## 6. Config Approval Statuses

| Status | Meaning |
|--------|---------|
| `DRAFT` | Config is being authored; not yet ready for review |
| `READY_FOR_PAPER_CONFIG_REVIEW` | Config is complete and submitted for reviewer inspection |
| `APPROVED_FOR_PAPER_SIMULATION_DESIGN` | Config passed review; a future docs-only paper simulation design PR is permitted |
| `REJECTED_CONFIG_REVIEW` | Reviewer found a disqualifying issue |
| `BLOCKED_RISK_LIMITS` | Risk limits are missing, non-finite, or violate policy caps |
| `BLOCKED_PROVENANCE` | Provenance fields (candidate_id, run_id, source_git_sha) cannot be verified |

### What `APPROVED_FOR_PAPER_SIMULATION_DESIGN` authorises

`APPROVED_FOR_PAPER_SIMULATION_DESIGN` authorises **only** the creation of a
future docs-only paper simulation design PR. It does **not**:

- Approve paper trading execution
- Approve live trading in any form
- Allow any broker, API, Alpaca, or network connection
- Allow any credential or environment variable access
- Allow any order submission, cancellation, or modification
- Change any runtime, execution, or live-gate module
- Change any kill switch or fail-closed guard
- Constitute financial advice or a trading recommendation

Paper and live trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by any config approval status.

---

## 7. Validation Rules for Future S17 Implementation

S17 will implement a pure offline config validator with the following rules.

### 7.1 Schema and provenance

- `config_schema_version` must equal `"PC/1.0"` — any other value →
  `BLOCKED_PROVENANCE`
- `candidate_id` must be a non-empty string — absence → `BLOCKED_PROVENANCE`
- `run_id` must be a non-empty string — absence → `BLOCKED_PROVENANCE`
- `source_git_sha` must be a non-empty string — absence → `BLOCKED_PROVENANCE`

### 7.2 Risk limit validation

- `max_notional_per_position` must be a finite positive number →
  violation: `BLOCKED_RISK_LIMITS`
- `max_position_fraction` must be a finite positive number ≤ 0.10 →
  violation: `BLOCKED_RISK_LIMITS`
- `max_daily_loss` must be a finite positive number → violation:
  `BLOCKED_RISK_LIMITS`
- `max_drawdown_stop` must be a finite positive number ≤ 1.0 → violation:
  `BLOCKED_RISK_LIMITS`
- `max_orders_per_day` must be a positive integer ≤ a conservative cap
  (e.g. 10) → violation: `BLOCKED_RISK_LIMITS`
- `min_cash_buffer` must be a finite positive number < 1.0 → violation:
  `BLOCKED_RISK_LIMITS`

### 7.3 Execution field validation

- `allowed_order_types` must be a non-empty list from a safe allowlist →
  any unlisted type: `REJECTED_CONFIG_REVIEW`
- `allowed_session` must be a known session string → unknown value:
  `REJECTED_CONFIG_REVIEW`

### 7.4 Forbidden field scan

- Validator must scan all field names and string values for credential-like
  patterns (e.g. `key`, `secret`, `token`, `password`, `credential`) →
  any match: `BLOCKED_PROVENANCE`

### 7.5 Reviewer fields

- `reviewer_name` must be a non-empty string → absence:
  `BLOCKED_PROVENANCE`
- `review_date_utc` must be a parseable ISO-8601 string → invalid:
  `BLOCKED_PROVENANCE`
- `approval_status` must be one of the values in §5 → invalid value:
  `REJECTED_CONFIG_REVIEW`

### 7.6 S17 implementation constraints

- Pure offline function: `validate_paper_config(config_dict)` →
  `PaperConfigValidationResult` (frozen dataclass)
- Input is an already-loaded plain dict — no file I/O
- No broker calls, no credentials, no network, no env vars
- All safety flags on the result always `False`
- No paper runner, no runtime/execution changes

---

## 8. Relationship to S17 and Further Work

```
S13 (promotion design)
  └─ S14 (promotion evaluator)
       └─ S15 (manual review workflow)
            └─ S16 (paper config design)       ← this PR
                 └─ S17 (config validator)
                      └─ future: paper simulation design (docs-only)
                           └─ future: paper trading implementation (separate approval)
```

Each step requires its own PR. No step automatically grants permission for
the next. Paper trading implementation requires an explicit approval artifact
of a new type, a separate PR, and an operator observation period. Live trading
remains blocked throughout the S-series.

---

## 9. Safety Statement

This design document does not:

- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Create any real configuration file.
- Add any broker, API, Alpaca, credential, environment variable, or network
  access to the codebase.
- Submit, request, place, cancel, or modify any order.
- Enable automated execution of any kind.
- Change the live gate status, kill switch, or any fail-closed guard.
- Grant any permission beyond allowing a future paper config schema design.

Paper and live trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by this document or by any config approval
status described herein.

---

*Nothing in this document is financial advice.*
*No position sizing, entry/exit timing, or trading decision is implied or approved.*
