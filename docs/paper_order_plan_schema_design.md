# Paper Order Plan Schema Design

Design document for S26: a docs-only schema design for a future paper order
plan, produced after S25 added a pure offline validator for the S24
paper trading approval artifact.

**S26 is docs-only. No source code changes. No tests. No config files.**
**No artifacts. No order plan artifacts. No paper or live trading approval.**
**No broker, API, credential, env, network, or order access. No automatic**
**execution, persistence, or promotion.**

---

## 1. Scope

| Item | In scope |
|------|----------|
| Define the schema for a future in-memory paper order plan | Yes |
| Define required upstream evidence references | Yes |
| Define proposed schema fields and required fixed values | Yes |
| Define forbidden fields | Yes |
| Define validation rules a future validator would implement | Yes |
| Define proposed future statuses | Yes |
| Define what a valid plan would and would not authorise | Yes |
| Describe the future S27 plan | Yes |
| Implement an order planner | **No** |
| Implement an order plan validator | **No** |
| Implement paper trading | **No** |
| Implement live trading | **No** |
| Add broker, API, Alpaca, or credential access | **No** |
| Read environment variables | **No** |
| Make network calls | **No** |
| Submit, request, or cancel any order | **No** |
| Approve paper or live trading automatically | **No** |
| Load or write any config file | **No** |
| Write any output, report, or artifact | **No** |
| Change any runtime or execution module | **No** |
| Write any source code or tests | **No** |

This document, like S16 (paper config schema design), S18 (paper simulation
design), S22 (paper trading architecture design), and S24 (paper trading
approval artifact schema design) before it, is a pure schema/shape design.
**No artifact of the type described here exists in this repository.**
Creating one is explicitly out of scope for S26.

A paper order plan is **not an order**. It cannot be submitted to any broker.
It cannot bypass or be substituted for the future safety gate described in
the S22 architecture design. It must be consumed by a future, separately-
approved safety gate before any execution boundary is ever reached.

---

## 2. Purpose

S22's architecture design (`docs/paper_trading_architecture_design.md`)
proposed a multi-stage paper order path in which, after a valid approval
artifact is confirmed, a future "paper order planner" would generate an
in-memory order plan — and that plan would then be inspected by a future
"paper safety gate" before any execution adapter could be invoked.

This document defines the proposed **schema** for that in-memory plan
record — call it the "paper order plan" (schema family `POP/1.0`). It exists
to give a future, separately-approved implementation PR (S27, §12 below) a
concrete, reviewed shape to implement a pure offline validator against —
exactly as S16 gave S17 a schema to validate, S18 gave S19 a design to
implement, and S24 gave S25 a schema to validate.

This document explicitly states, and nothing in it should be read to imply
otherwise:

- **The artifact described here does not exist.** No file, record, JSON
  document, config entry, or in-memory structure of this shape is created,
  stubbed, or persisted by this PR or by any prior PR in the S-series.
- **Designing this schema does not approve paper trading.** Just as S24's
  approval artifact schema design did not approve an artifact, designing
  the schema for an order plan does not approve, create, or pre-fill any
  plan or order.
- **A paper order plan, even once it exists in some future approved form,
  is not an order and cannot be submitted.** The plan record carries
  `order_action_requested = false` and `live_trading_allowed = false` as
  fixed, structurally required values (§5). It must pass a future, separately-
  approved safety gate before any execution boundary is reached. No
  execution adapter, broker connection, or order submission follows
  automatically from the existence or validity of a plan of this shape.
- **Live trading remains blocked.** This document does not touch, weaken, or
  propose to change the existing `live_readiness_gate` /
  `live_submit_enablement_gate` / kill-switch infrastructure.

---

## 3. Required Upstream Evidence

A future paper order planner that generates a plan of this shape would be
required to reference, and a future plan validator (S27/S28) would be
required to confirm the presence and consistency of, the following upstream
evidence chain:

| Evidence item | Source | Required condition |
|---|---|---|
| `CandidatePromotionResult` | S14 | `status == PAPER_CANDIDATE_ELIGIBLE` |
| Manual review decision | S15 | decision == `APPROVED_FOR_PAPER_CONFIG_DESIGN` |
| `PaperConfigValidationResult` | S17 | `result == "PASS"`, all five safety flags `False` |
| `PaperSimulationResult` | S19 | `result == "PASS"`, all five safety flags `False` |
| Integration test evidence | S20 | confirms the S17 → S19 chain behaves correctly |
| Manual review decision | S21 | decision == `APPROVED_FOR_PAPER_TRADING_DESIGN` |
| Architecture design reference | S22 | `docs/paper_trading_architecture_design.md` |
| Architecture invariant test evidence | S23 | `tests/test_paper_architecture_invariants.py` passing |
| `PaperApprovalValidationResult` | S25 | `result == "PASS"`, all five safety flags `False` |

None of these evidence items, individually or together, approves paper
trading or authorises any order submission. They are the **inputs** a
future planner and validator would examine before a plan of this shape could
be created or accepted — and even a fully-validated plan would still need
to pass a separately-approved future safety gate before any execution could
proceed.

---

## 4. Proposed Schema Fields

The table below lists every field this design proposes for a future
`POP/1.0` paper order plan. **No code, type, dataclass, enum, or config
loader implementing this shape exists anywhere in the codebase.** This is a
pure data-shape proposal, in the same spirit as S16 (`PC/1.0`) and S24
(`PTA/1.0`).

| Field | Proposed type | Purpose (proposed) |
|---|---|---|
| `plan_schema_version` | string | Schema family/version identifier; fixed value (§5) |
| `plan_type` | string | Identifies the record as a paper order plan; fixed value (§5) |
| `plan_id` | string | Unique identifier for this specific plan instance |
| `candidate_id` | string | The exact candidate this plan was generated for (must match the approval artifact's `candidate_id`) |
| `run_id` | string | The exact research run this plan was generated for |
| `source_git_sha` | string | The exact commit SHA under which this plan was generated |
| `approval_artifact_hash` | string | A content hash of the `PTA/1.0` approval artifact whose scope this plan operates within |
| `paper_config_hash` | string | A content hash of the validated `PC/1.0` config (must match the approval artifact's `paper_config_hash`) |
| `simulation_result_hash` | string | A content hash of the validated `PaperSimulationResult` (must match the approval artifact's `simulation_result_hash`) |
| `generated_at_utc` | string | ISO-8601-like UTC timestamp when the plan was generated |
| `expires_at_utc` | string | ISO-8601-like UTC timestamp after which the plan is no longer current |
| `symbol` | string | The trading symbol this plan covers (must be in the approval artifact's `allowed_symbols`) |
| `interval` | string | The bar interval this plan was generated from (must be in the approval artifact's `allowed_intervals`) |
| `strategy_family` | string | The strategy family that produced the signal (must be in the approval artifact's `allowed_strategy_families`) |
| `holding_horizon` | string | Human-readable description of the intended holding duration (e.g. `"intraday"`, `"multi-day"`) |
| `side` | string | Order direction; fixed to `"BUY"` or `"SELL"` (§6) |
| `order_type` | string | Order execution type; must be within the approval artifact's `allowed_order_types` (§6) |
| `quantity` | number | Proposed number of shares or units; must be finite and positive (§6) |
| `notional` | number | Proposed notional value in USD; must be finite, positive, and bounded by the approval artifact's `max_notional_per_position` (§6) |
| `limit_price` | number or null | Required for `limit` orders; may be omitted (null) for `market` orders (§6) |
| `time_in_force` | string | Order lifetime policy; fixed to `"day"` (§6) |
| `allowed_session` | string | The session within which this plan is valid; fixed to `"regular"` (§5, §6) |
| `rationale` | string | Human-readable description of the signal rationale that produced this plan |
| `signal_snapshot` | dict | Read-only snapshot of the signal values that drove this plan (for audit; no live data) |
| `risk_snapshot` | dict | Read-only snapshot of active risk state at plan generation time (must include `max_position_fraction`, `max_daily_loss`, `max_drawdown_stop`, `max_orders_per_day`) |
| `approval_scope` | string | The approval scope this plan operates within; fixed to the approval artifact's `approval_scope` — always `"PAPER_TRADING_LIMITED_RUN_ONLY"` (§5) |
| `dry_run_required` | bool | Whether the plan must be treated as a dry-run (logging-only, no submit) by any downstream component; fixed `true` (§5) |
| `human_confirmation_required` | bool | Whether a synchronous human confirmation step is required before any submission; fixed `true` (§5) |
| `kill_switch_required` | bool | Whether the kill switch must be engaged by default for any downstream component; fixed `true` (§5) |
| `safety_gate_required` | bool | Whether this plan must pass a future safety gate before any execution boundary; fixed `true` (§5) — this is the primary structural difference between a plan and an order |
| `broker_calls_made` | bool | Whether this plan creation contacted any broker or API; fixed `false` (§5) |
| `credentials_read` | bool | Whether this plan creation read any credential or secret; fixed `false` (§5) |
| `network_calls_made` | bool | Whether this plan creation made any network call; fixed `false` (§5) |
| `order_action_requested` | bool | Whether this plan constitutes or requests an order action; fixed `false` (§5) — a plan is not an order |
| `live_trading_allowed` | bool | Whether live trading is allowed; fixed `false` (§5) — a plan of this shape can never authorise live trading |
| `notes` | string | Free-text planner notes, caveats, and observations |

This field list is a **proposal only**. No struct, dataclass, `TypedDict`,
JSON schema, or config loader for it exists. A future, separately-approved
implementation PR could refine, rename, add to, or remove from this list —
subject to its own design review.

---

## 5. Required Fixed Values

The following fields would be **structurally fixed** — a future validator
(S27/S28) would be required to reject any plan that deviates from these
values, regardless of who generated it or what they intended:

| Field | Required fixed value | Rationale |
|---|---|---|
| `plan_schema_version` | `"POP/1.0"` | Identifies this exact, reviewed schema family/version |
| `plan_type` | `"PAPER_ORDER_PLAN"` | Prevents confusion with an order, a config, or an approval artifact |
| `approval_scope` | `"PAPER_TRADING_LIMITED_RUN_ONLY"` | Carries forward the scope from the approval artifact; structurally bounds the plan so it can never be read as authorising anything beyond a bounded paper run |
| `allowed_session` | `"regular"` | No extended-hours or overnight session may ever be named by a plan of this shape |
| `dry_run_required` | `true` | Every downstream component must treat this plan as logging-only, no-submit, unless a future, separate, explicitly-approved dry-run override exists |
| `human_confirmation_required` | `true` | No submission of any kind may occur without a synchronous, logged, human confirmation step |
| `kill_switch_required` | `true` | Every downstream component must start "stopped" and require explicit, logged, human action before approaching any submission boundary |
| `safety_gate_required` | `true` | A plan is not an order — it must pass a future, separately-approved safety gate before any execution adapter could be invoked; this flag is the structural expression of that invariant |
| `broker_calls_made` | `false` | Plan generation must not contact any broker or API — fixed at the schema level, not left to implementer discretion |
| `credentials_read` | `false` | Plan generation must not read any credential or secret |
| `network_calls_made` | `false` | Plan generation must not make any network call |
| `order_action_requested` | `false` | A plan is definitionally not an order action — fixed at the schema level |
| `live_trading_allowed` | `false` | A plan of this shape can never authorise live trading — fixed at the schema level, not left to reviewer or implementer discretion |

These fixed values are **structural invariants of the proposed schema
itself** — not configuration choices an implementer could override. A future
validator would be required to treat any plan violating them as
`PLAN_BLOCKED_SAFETY` (§8), independent of any other field's value.

---

## 6. Allowed Values

Beyond the fixed values in §5, the following fields have explicit allowed
value sets that a future validator (S27/S28) would be required to enforce:

| Field | Allowed values | Notes |
|---|---|---|
| `side` | `"BUY"` or `"SELL"` | No other direction is permitted |
| `order_type` | Subset of the approval artifact's `allowed_order_types` (itself a subset of `{"market", "limit"}`) | No other order type may be named in a plan of this shape |
| `time_in_force` | `"day"` | No other order lifetime policy is permitted |
| `limit_price` | Required (finite, positive number) for `limit` orders; may be `null` or absent for `market` orders | Must be consistent with `order_type` |
| `quantity` | Finite, positive number | Non-finite (`NaN`, `Inf`) or non-positive values are invalid |
| `notional` | Finite, positive number, `<= approval artifact's max_notional_per_position` | Bounded by the approval artifact's risk limits |
| `symbol` | Must be present in the approval artifact's `allowed_symbols` | No symbol outside the exact closed allowlist may be planned |
| `interval` | Must be present in the approval artifact's `allowed_intervals` | No interval outside the exact closed allowlist may be planned |
| `strategy_family` | Must be present in the approval artifact's `allowed_strategy_families` | No strategy family outside the exact closed allowlist may be planned |

---

## 7. Forbidden Fields

A future validator (S27/S28) would be required to scan a candidate plan for,
and reject any plan containing, any of the following — consistent with the
forbidden-field designs already established in S16/S17 for paper configs and
S24/S25 for approval artifacts:

| Forbidden field / pattern | Reason |
|---|---|
| `api_key` | Credential — never permitted in any plan, config, artifact, or evidence record in this chain |
| `secret_key` | Credential |
| `api_secret` | Credential |
| `auth_token` | Credential |
| `password` | Credential |
| `credential` (or any field containing this substring) | Generic credential marker |
| `broker_secret` | Credential |
| `account_number` | Account-identifying value — forbidden in a paper-only plan |
| `live_account_id` | Live-account reference — categorically forbidden |
| `production_account` | Live/production-account reference — categorically forbidden |
| `broker_account_id` | Any reference to a broker account identifier — forbidden; account routing is handled only at a future, separately-approved execution adapter boundary |
| Any environment-variable-shaped name containing a secret-like substring (e.g. `*_API_KEY`, `*_SECRET`, `*_TOKEN`) | Signals an attempt to reference credential material via indirection |
| `submit_order` as a field name or instruction | Order action — forbidden in a plan; a plan gates future order submission, never contains it |
| `place_order` as a field name or instruction | Same reasoning |
| `live_submit` as a field name or instruction | Live-order action — categorically forbidden |
| `paper_trading_approved` set to `true` | Would circumvent the required approval artifact chain |
| `live_trading_approved` set to `true` | Structurally forbidden by §5 |
| `approved_for_live_trading` | Any field claiming live-trading approval — categorically forbidden in a paper-only plan |
| Any field that, by its name, structure, or value, would directly enable broker access, network access, or order submission | Catch-all: a plan is a **gate description** and a **safety-gate input**, never a **gate bypass** or an **execution mechanism** |

A future validator finding any forbidden field or value would be required to
return a `PLAN_BLOCKED_SAFETY` or `PLAN_BLOCKED_PROVENANCE` classification
(§8) and must never attempt to "clean," strip, redact, or silently ignore
the offending field — fail closed, not fail open.

---

## 8. Proposed Validation Rules for Future Implementation

A future, pure offline plan validator (S27/S28, §12) — analogous to S17's
`validate_paper_config()` and S25's `validate_paper_approval_artifact()` —
would be required to enforce **all** of the following rules, in a
deterministic order, before classifying any candidate plan:

1. **Schema version supported** — `plan_schema_version` must be a known,
   supported value (initially only `"POP/1.0"`).
2. **Required fields present** — every field in §4 must be present; no
   field may be silently defaulted into existence.
3. **Provenance fields non-empty** — `plan_id`, `candidate_id`, `run_id`,
   `source_git_sha`, `approval_artifact_hash`, `paper_config_hash`,
   `simulation_result_hash` must all be non-empty strings.
4. **`candidate_id`/`run_id`/`source_git_sha` match the referenced
   approval artifact** — a mismatch indicates the plan references different
   evidence than was actually reviewed and approved.
5. **`approval_artifact_hash` matches the referenced `PTA/1.0` approval
   artifact** — the plan must be operating within the exact artifact whose
   scope and limits were reviewed.
6. **`paper_config_hash` and `simulation_result_hash` match the approval
   artifact** — must match the hashes recorded in the referenced `PTA/1.0`
   approval artifact; any mismatch is `PLAN_BLOCKED_PROVENANCE`.
7. **Plan has not expired** — the current time (as supplied by the caller;
   the validator itself reads no clock, no environment variable, and makes
   no filesystem or network call) must be before `expires_at_utc`.
8. **`generated_at_utc` and `expires_at_utc` are ISO-8601-like datetime
   strings** — following the same format rules as S17's `review_date_utc`
   and S25's `approved_at_utc`/`expires_at_utc` validation.
9. **`expires_at_utc` is after `generated_at_utc`** — a plan that expires
   before (or at) the moment it was generated is invalid.
10. **`symbol` is within the approval artifact's `allowed_symbols`** — a
    plan naming a symbol outside the exact closed allowlist is
    `PLAN_BLOCKED_PROVENANCE`.
11. **`interval` is within the approval artifact's `allowed_intervals`** —
    same reasoning for bar intervals.
12. **`strategy_family` is within the approval artifact's
    `allowed_strategy_families`** — same reasoning for strategy families.
13. **`order_type` is within the approval artifact's `allowed_order_types`
    and within `{"market", "limit"}`** — no other order type may be
    named in a plan of this shape.
14. **`quantity` and `notional` are finite and positive** — non-finite
    (`NaN`, `Inf`) or non-positive values are `PLAN_BLOCKED_RISK`.
15. **`notional` does not exceed the approval artifact's
    `max_notional_per_position`** — the plan may not exceed the notional
    ceiling granted by the reviewed approval artifact.
16. **`risk_snapshot` includes required risk keys** — the dict must contain
    `max_position_fraction`, `max_daily_loss`, `max_drawdown_stop`, and
    `max_orders_per_day`, each with finite, positive values no looser than
    the corresponding limits in the referenced approval artifact.
17. **All §5 fixed-value fields match their required values** — any
    deviation is `PLAN_BLOCKED_SAFETY` regardless of other fields.
18. **Forbidden field/value scan must pass** — none of the patterns in §7
    may appear anywhere in the plan, its nested structures, or its
    string-valued fields.
19. **Missing approval artifact reference blocks the plan** — if the
    approval artifact identified by `approval_artifact_hash` cannot be
    located, verified, or confirmed as `APPROVED_FOR_LIMITED_PAPER_RUN`,
    the plan is `PLAN_BLOCKED_PROVENANCE`; no plan may bypass this check.
20. **No plan can bypass the future paper safety gate** — `safety_gate_
    required` must always be `true`; any plan with a `false` value for this
    field is `PLAN_BLOCKED_SAFETY` regardless of any other classification.

As with S17's and S25's validators, a future implementation of these rules
would be required to:

- run **entirely offline**, on an **already-loaded, in-memory dict**;
- perform **no file I/O, no network calls, no environment variable reads,
  and no credential access**;
- **never** mutate its input;
- be **deterministic** — the same input always yields the same
  classification; and
- **always** return all five safety flags (`broker_calls_made`,
  `credentials_read`, `network_calls_made`, `order_action_requested`,
  `live_trading_allowed`) as `False`.

None of these rules is implemented, stubbed, or characterised by this PR.
They are a **proposal** for what S27/S28 (§12) would need to implement and
test.

---

## 9. Proposed Future Statuses

A future validator would classify any candidate plan into exactly one of
the following statuses. This vocabulary is proposed in the same spirit as
the `PaperConfigStatus` (S17), `PaperSimulationStatus` (S19), and
`PaperApprovalStatus` (S25) vocabularies before it:

| Status | Meaning (proposed) |
|---|---|
| `NOT_PLANNED` | No plan has been generated or drafted yet (initial state) |
| `PLAN_DRAFT` | A plan has been drafted but not yet submitted to the future safety gate |
| `PLAN_READY_FOR_SAFETY_GATE` | The plan has passed all offline validation rules (§8) and is ready to be inspected by a future, separately-approved safety gate; this status alone does not cause any order, connection, or execution to occur |
| `PLAN_REJECTED_SCHEMA` | The plan's schema, structure, or required fields are invalid or unsupported |
| `PLAN_BLOCKED_PROVENANCE` | Required upstream evidence (S14/S15/S17/S19/S20/S21/S22/S23/S25) is missing, inconsistent, or does not match the plan's provenance fields; or the referenced approval artifact is absent or not in `APPROVED_FOR_LIMITED_PAPER_RUN` state |
| `PLAN_BLOCKED_RISK` | One or more risk-limit fields are missing, non-finite, non-positive, or exceed the approval artifact's bounded limits |
| `PLAN_BLOCKED_SAFETY` | A structurally-fixed field (§5) deviates from its required value, a forbidden field/value (§7) is present, `safety_gate_required` is not `true`, or any safety invariant from S17/S19/S22/S23/S25 appears violated |
| `PLAN_EXPIRED` | The plan's `expires_at_utc` has passed; an expired plan may not be submitted to the safety gate or any execution boundary |

The default state for any plan that has not yet been generated is
`NOT_PLANNED`. This vocabulary is a **proposal**; no enum, constant, or
status type implementing it exists anywhere in the codebase.

---

## 10. What a Valid Plan Authorises

Even in a hypothetical future where a plan of this shape exists, has been
generated, and has been classified `PLAN_READY_FOR_SAFETY_GATE`, it would
authorise **only**:

1. **Safety-gate review** — a future, separately-approved safety gate
   component (a proposed step in the S22 data flow) could inspect the plan
   and decide whether to allow it to proceed further. The plan itself is the
   **input** to that gate, not the gate, not the output, and not the order.
2. **No broker or API access** — the plan carries
   `broker_calls_made = false` and `network_calls_made = false` as fixed
   values; it cannot itself create a broker connection or invoke a network
   call.
3. **No order submission** — the plan carries `order_action_requested = false`
   as a fixed value; it is definitionally not an order and cannot be submitted
   to any broker, routing system, or execution adapter.
4. **No paper trading by itself** — a plan of this shape is a proposed input
   to a future, separately-approved safety gate; even after that gate, at
   least the separately-approved execution adapter boundary, the
   human-confirmation step, and the kill-switch check would remain between
   the plan and any actual paper submission.
5. **No live trading ever** — the plan carries `live_trading_allowed = false`
   as a fixed, structurally required value. No plan of this shape can
   authorise live trading.

A valid plan of this shape would **not** authorise: any order submission;
any broker or API connection; any network call; any credential access; any
live-trading action; any symbol, interval, strategy, or order type outside
the exact closed allowlists in the referenced approval artifact; any notional
exceeding the bounded limit in the referenced approval artifact; or any
relaxation of the kill switch, dry-run default, human-confirmation
requirement, or safety-gate requirement.

---

## 11. Explicit Non-Approval

This document does **not**:

- Create any real paper order plan, approval artifact, config file, paper
  artifact, simulation artifact, or any other artifact.
- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Add any broker, API, Alpaca, credential, environment variable, network, or
  order-submission access anywhere in the codebase.
- Implement, scaffold, or stub an order planner, an order plan validator,
  or any component named in §4–§10.
- Submit, request, place, cancel, or modify any order.
- Create any order path, paper submit path, or live submit path.
- Modify, relax, weaken, or bypass any runtime, execution, or live-gate
  module (`live_submit`, `live_readiness_gate`,
  `live_submit_enablement_gate`, or any fail-closed guard).
- Change the kill switch or any fail-closed default.
- Authorise anything beyond a future, separately-approved S27 implementation
  PR for a pure offline order plan validator (or docs-only order planner
  design) — which would itself still grant no paper or live trading approval.

Reaching this design document does not shorten, skip, weaken, or pre-approve
any future step in the S-series chain (§13).

---

## 12. Future S27 Plan

S27, if pursued, would implement either:

(a) A **pure offline paper order plan validator** — analogous in shape,
scope, and constraints to S17's `validate_paper_config()` and S25's
`validate_paper_approval_artifact()`:

| Constraint | S27 requirement |
|---|---|
| Input | An already-loaded, in-memory dict (no file I/O) |
| Broker, API, Alpaca, or credential access | **No** |
| Environment variable or network access | **No** |
| Order submission of any kind | **No** |
| Paper or live trading approval granted by the validator's result | **No** — the validator only *classifies*, exactly as S17's and S25's do |
| Mutation of its input | **No** |
| Determinism | Required — same input always yields the same classification |
| Safety flags | All five (`broker_calls_made`, `credentials_read`, `network_calls_made`, `order_action_requested`, `live_trading_allowed`) always `False` |
| Tests | Required — covering schema-version support, required-field presence, provenance matching (including approval artifact hash), expiry, risk-limit bounds, allowlist checks, forbidden-field/value scanning, `safety_gate_required` enforcement, and confirming all five safety flags are always `False` on every result |

or:

(b) A **docs-only paper order planner design** — defining the proposed
inputs, outputs, and constraints of a future pure-offline planner that
would consume signal/config/approval-artifact evidence and produce a plan of
this shape, without implementing the planner or running any paper trading.

Either option for S27 would still require its own design review, its own
PR, and would itself not approve, implement, or schedule paper or live
trading, an order path, or any runtime/execution change. A validated plan
classified `PLAN_READY_FOR_SAFETY_GATE` would remain only **one** required
input to the future, separately-approved safety gate in the S22 data flow —
never a substitute for it.

---

## 13. Relationship to S-Series

```
S14 (promotion evaluator)
  └─ S15 (manual review workflow)
       └─ S16 (paper config design)
            └─ S17 (config validator)
                 └─ S18 (paper simulation design)
                      └─ S19 (simulation implementation)
                           └─ S20 (simulation integration tests)
                                └─ S21 (results review workflow design)
                                     └─ S22 (paper trading architecture design)
                                          └─ S23 (architecture invariant tests)
                                               └─ S24 (paper trading approval
                                                       artifact schema design)
                                                    └─ S25 (pure offline approval
                                                            artifact validator)
                                                         └─ S26 (paper order plan
                                                                 schema design)  ← this PR
                                                              └─ S27 (pure offline
                                                                      order plan
                                                                      validator OR
                                                                      order planner
                                                                      design, future,
                                                                      separately
                                                                      approved)
                                                                   └─ future: paper
                                                                      safety gate,
                                                                      execution adapter,
                                                                      each separately
                                                                      approved, each
                                                                      gated, each
                                                                      reviewed
```

Each step requires its own PR. No step automatically grants permission for
the next. A plan of the shape described here, even once implemented,
reviewed, and classified `PLAN_READY_FOR_SAFETY_GATE` in some hypothetical
future, would still be only one of many required preconditions before any
paper order could ever be gated or submitted — and live trading would remain
blocked throughout the entire chain and beyond, gated by the existing
`live_readiness_gate` / `live_submit_enablement_gate` / kill-switch
infrastructure, none of which this document touches, weakens, or proposes
to change.

---

## 14. Safety Statement

This design document does not:

- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Create any real order plan, configuration file, approval artifact, paper
  artifact, simulation artifact, or any other artifact.
- Implement, scaffold, or stub an order planner, an order plan validator,
  or any future-only component named in this document.
- Add any broker, API, Alpaca, credential, environment variable, or network
  access to the codebase.
- Submit, request, place, cancel, or modify any order.
- Enable automated execution, persistence, or promotion of any kind.
- Change the live gate status, kill switch, or any fail-closed guard.
- Grant any permission beyond allowing a future, separately-approved S27
  implementation PR for a pure offline order plan validator (or docs-only
  order planner design) — which would itself still grant no paper or live
  trading approval.

A paper order plan is **not an order**. A plan classified
`PLAN_READY_FOR_SAFETY_GATE` is not a submitted order, not a paper
trade, and not paper trading approval. It is the proposed input to a future,
separately-approved safety gate — and even that gate, if approved and
implemented, would be only one of multiple remaining required steps before
any actual paper submission could ever be approached.

Paper trading remains not approved. Live trading remains blocked. No
automatic promotion into runtime or execution follows from this document,
or from any plan whose shape is merely proposed here. No order action of
any kind follows from any status, field, or classification described in this
document. All live-gate safety flags remain fail-closed.

*Nothing in this repository is financial advice.*

---

*Nothing in this document is financial advice.*
*No position sizing, entry/exit timing, broker connection, or trading*
*decision is implied, designed in implementable detail, or approved.*
