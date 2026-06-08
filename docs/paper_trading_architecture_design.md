# Paper Trading Architecture Design

Design document for S22: a docs-only architecture design for a future paper
trading system, produced after S21 recorded
`APPROVED_FOR_PAPER_TRADING_DESIGN`.

**S22 is docs-only. No source code changes. No tests. No config files.**
**No artifacts. No paper or live trading approval. No broker, API,**
**credential, env, network, or order access. No automatic execution,**
**persistence, or promotion.**

---

## 1. Purpose

S21's `APPROVED_FOR_PAPER_TRADING_DESIGN` decision authorised **only** the
creation of this document — a proposed architecture for a future paper
trading system. It did not authorise implementing, running, or approving
paper trading in any form.

This document defines:

- the preconditions that must hold before this design work begins
- the proposed architecture components and which are pure offline vs.
  future-only
- the component boundaries and what each component may and may not do
- the proposed data flow, with all execution/broker/order stages marked
  future-only and unimplemented
- the proposed safety gate model
- the future approval artifacts that paper trading implementation would
  require, each of which is explicitly not live trading approval
- the future S23 plan
- the relationship between this design and any trading approval

No component is implemented in this PR. No paper trading is approved. No
broker, API, Alpaca, credential, environment variable, network, or order
access is added anywhere in the codebase by this document.

---

## 2. Scope

| Item | In scope |
|------|----------|
| Define proposed architecture components | Yes |
| Define component boundaries (offline vs. future-only) | Yes |
| Define proposed data flow | Yes |
| Define safety gate model | Yes |
| Define required future approval artifacts | Yes |
| Describe S23 future plan | Yes |
| Implement any architecture component | **No** |
| Implement a paper account connector, order planner, safety gate, execution adapter, or ledger | **No** |
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

---

## 3. Preconditions

All of the following conditions must be satisfied before any paper trading
architecture design work begins. **None of these conditions, individually or
together, approves paper trading.**

1. **S14:** The candidate must have `CandidatePromotionResult.status ==
   PAPER_CANDIDATE_ELIGIBLE`.
2. **S15:** The manual review must have recorded decision
   `APPROVED_FOR_PAPER_CONFIG_DESIGN`.
3. **S17:** `validate_paper_config(config_dict)` must return `result ==
   "PASS"` for the specific config under consideration.
4. **S19:** `run_paper_simulation(config_dict, bars, ...)` must return
   `result == "PASS"` for that config, with all five safety flags `False`.
5. **S20:** Integration evidence must confirm the S17 → S19 chain behaves
   correctly for configs of this shape.
6. **S21:** The manual simulation-results review must have recorded decision
   `APPROVED_FOR_PAPER_TRADING_DESIGN`.

Satisfying all six preconditions only permits *designing* a future paper
trading architecture (this document). It does not permit implementing,
running, or approving paper trading, live trading, or any order submission.

---

## 4. Proposed Architecture Components

The table below lists every component this design proposes. The **Status**
column states whether the component could be pure offline (and therefore
implementable in a future, still-bounded PR) or whether it would require
broker/network access (and therefore must remain **unimplemented and
undesigned-in-detail** until a dedicated, separately-approved design phase).

| Component | Purpose (proposed) | Status |
|---|---|---|
| Paper config validator | Validates a PC/1.0 config offline (already exists: S17 `validate_paper_config()`) | **Existing — pure offline** |
| Paper simulation result reviewer | Human-in-the-loop review of `PaperSimulationResult` outputs against the S21 checklist (already designed: S21) | **Existing — pure offline / human process** |
| Paper trading approval artifact | A new, explicit, separately-approved record type that — and only that — could authorise moving toward a paper order path | **Future only — not created here** |
| Paper account connector boundary | A proposed seam between offline planning and a paper brokerage account; would require network/credential access | **Future only — must remain unimplemented in S22** |
| Paper order planner | Would translate a reviewed, approved simulation + live config into a proposed (not submitted) order plan, entirely offline | **Future only — pure offline in design, but not implemented here** |
| Paper order safety gate | Would apply fail-closed checks (notional caps, order-count caps, daily loss limits, kill switch) before any plan could reach an execution boundary | **Future only — must remain unimplemented in S22** |
| Paper execution adapter boundary | A proposed seam where an order plan could, in a future and separately-approved phase, be submitted to a paper brokerage API | **Future only — must remain unimplemented in S22; requires broker/network access** |
| Paper ledger / observation recorder | Would record paper-only outcomes (no credentials, no account numbers) for the human observation period | **Future only — pure offline record format, but not implemented here** |
| Kill switch / fail-closed state | A single, always-on, default-enabled stop control that every other component must honour | **Conceptual control — not implemented here; must exist and default to "stopped" before any future execution component could be designed** |
| Audit log / evidence bundle | A proposed append-only, local, credential-free record of every gate decision and architecture-state transition | **Future only — pure offline record format, but not implemented here** |

No component listed as "future only" is implemented, scaffolded, stubbed, or
imported anywhere by this PR.

---

## 5. Component Boundaries

### 5.1 Pure offline components (already exist or could be designed safely)

These components read only already-loaded, already-validated in-memory data.
They make no network calls, read no credentials, and submit no orders:

- Paper config validator (S17 — exists)
- Paper simulation skeleton (S19 — exists)
- Paper simulation results reviewer process (S21 — exists, human-driven)
- Paper order planner *(future; would remain pure offline even when designed
  in detail — it must never itself touch a broker)*
- Paper ledger / observation recorder format *(future; a pure data-shape
  design — the recorder writes locally, never to a broker)*
- Audit log / evidence bundle format *(future; a pure data-shape design)*

### 5.2 Components that would require broker/network access in a future phase

These components are the **only** ones that would ever need credentials,
network access, or a live connection to any account — and they are the
components this document explicitly refuses to design in detail or implement:

- Paper account connector boundary
- Paper execution adapter boundary

Both remain **conceptual placeholders only**. Their internal design,
authentication model, and submission protocol are explicitly **out of scope**
for S22 and for any docs-only design PR. Designing them in implementable
detail would itself require a dedicated, separately-approved security review
— not a continuation of this S-series chain.

### 5.3 Universal boundary rules

Regardless of which phase a component belongs to:

1. **No component may bypass the safety gate.** Every proposed path from a
   reviewed simulation toward any execution boundary must pass through the
   paper order safety gate (§6) — there is no proposed shortcut.
2. **No component may read credentials without explicit future approval.**
   Credential access is not granted by this document, by S21's decision, or
   by any future architecture-review artifact alone — it would require its
   own dedicated security-scoped approval, separate from this S-series chain.
3. **No component may submit orders without an explicit future approval
   artifact.** Order submission of any kind — paper or live — requires the
   "paper order path dry-run approval" and "paper trading limited-run
   approval" artifacts defined in §7, neither of which exists yet.

---

## 6. Proposed Data Flow

The following flow is **proposed, not implemented**. Every stage from
"safety gate" onward is explicitly future-only and unimplemented; no code in
this repository performs any of these stages.

```
 [already exists / pure offline — implemented in prior S-series PRs]
 1. Validated config            (S17 validate_paper_config -> PASS)
 2. Reviewed simulation evidence (S19 PaperSimulationResult PASS
                                  + S20 integration evidence
                                  + S21 APPROVED_FOR_PAPER_TRADING_DESIGN)

 [future only — none of the following exists; all require new, separately
  approved PRs; none may be implemented as a side effect of this document]
 3. Explicit future paper trading approval artifact   <- FUTURE, UNIMPLEMENTED
        |
        v
 4. Paper order plan generation (offline, no submission)  <- FUTURE, UNIMPLEMENTED
        |
        v
 5. Paper order safety gate (fail-closed; §6 of this doc)  <- FUTURE, UNIMPLEMENTED
        |
        v
 6. Paper execution adapter boundary (requires broker/network)  <- FUTURE, UNIMPLEMENTED
        |
        v
 7. Paper ledger (local, credential-free record)  <- FUTURE, UNIMPLEMENTED
        |
        v
 8. Observation review (human, over a defined period)  <- FUTURE, UNIMPLEMENTED
```

Stages 1–2 already exist as pure offline research artifacts. Stages 3–8 are
**proposed only**; each would require its own design PR, its own
implementation PR, and — for stages that touch broker/network/credentials —
its own dedicated security review wholly outside this S-series chain. None of
stages 3–8 may be implemented, scaffolded, stubbed, or wired into any runtime
module as part of this PR or as an automatic consequence of it.

---

## 7. Proposed Safety Gates

Any future implementation of the order-path stages (§6, stages 3–8) would be
required to satisfy **all** of the following before any order of any kind —
paper or live — could be submitted:

1. **Fail closed by default.** Absence of an explicit, valid, current
   approval artifact must always resolve to "blocked" — never to "proceed."
2. **Require an explicit paper trading approval artifact.** No order plan
   may proceed past the safety gate without a current, valid "paper trading
   limited-run approval" record (§7 below) referencing the exact candidate,
   config, and simulation evidence under review.
3. **Require a paper-only account label, never credentials.** Any reference
   to an account in config or evidence must be a label
   (e.g. `"alpaca-paper-primary"`), never an API key, secret, token, or
   account number — consistent with the S16/S17 forbidden-field design.
4. **Require the kill switch enabled (i.e., "stopped") by default.** Any new
   component must start in a stopped state and require explicit, logged,
   human action to move toward "running" — and even "running" must still
   require every other gate in this list to pass.
5. **Require max notional / max orders / daily loss limits.** Every order
   plan must be checked against `max_notional_per_position`,
   `max_orders_per_day`, `max_daily_loss`, and `max_drawdown_stop` from the
   validated config (S17) before it may proceed.
6. **Require dry-run / no-submit as the default mode.** Any future order
   path component must default to producing a plan and logging it — not
   submitting it — until a separate, explicit "dry-run approval" artifact
   exists and is current.
7. **Require human confirmation for any future order path.** No order of any
   kind may be submitted by any automated process without a synchronous,
   logged, human confirmation step immediately preceding submission.
8. **Require no live account access.** No component designed or implemented
   under this chain may connect to, reference, or be configured to reach a
   live (non-paper) brokerage account. Live trading remains categorically
   blocked by the existing `live_readiness_gate` /
   `live_submit_enablement_gate` and is wholly outside this chain's scope.

These are *requirements that any future safety-gate design or implementation
must satisfy* — this document does not implement, simulate, or stub any of
them.

---

## 8. Required Future Approval Artifacts

Paper trading implementation — should it ever be pursued — would require, at
minimum, the following **new, separately-approved artifact types**, each
produced by its own PR with its own human review. **None of these artifacts
exists yet. Creating any of them is explicitly out of scope for S22.** Each
artifact, when and if it is ever created, must explicitly state that it is
**not** live trading approval.

| Artifact | Would authorise (if it existed) | Explicitly does not authorise |
|---|---|---|
| Paper trading architecture review approval | Proceeding from this design (S22) toward a detailed, security-reviewed design of the order-path components | Implementing any component; reading credentials; submitting any order; live trading |
| Paper account sandbox readiness approval | Confirming a sandboxed, paper-only account environment is provisioned and isolated from any live account | Connecting that sandbox to this codebase; submitting any order; live trading |
| Paper order path dry-run approval | Running the (still-hypothetical) order planner and safety gate in a logging-only, no-submit mode | Submitting any real or paper order; connecting to any broker; live trading |
| Paper trading limited-run approval | A strictly bounded, time-boxed, human-supervised paper order submission under all of the gates in §7 | Any live trading; any unsupervised run; any extension beyond the bounded window without a fresh approval |
| Observation-period review | Recording the operator's evidence-based assessment after a defined paper observation period | Live trading; any further automation without a fresh, separate approval chain |

Every artifact in this table is **future and hypothetical**. None of them
exists in this repository. Creating, drafting a template for, or stubbing any
of them is explicitly out of scope for this PR.

---

## 9. Explicit Non-Approval

This document, and the `APPROVED_FOR_PAPER_TRADING_DESIGN` decision (S21)
that authorised it, do **not**:

- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Create any broker, API, Alpaca, or order-submission path — proposed or
  real.
- Permit any credential, environment variable, or network access anywhere in
  the codebase.
- Modify, relax, or bypass any runtime, execution, or live-gate module
  (`live_submit`, `live_readiness_gate`, `live_submit_enablement_gate`, or
  any fail-closed guard).
- Authorise anything beyond a future docs-only or tests-only design PR (S23)
  that itself still grants no implementation, execution, or approval rights.

Reaching `APPROVED_FOR_PAPER_TRADING_DESIGN` and producing this design
document moves the S-series **one step further along a long, fully-gated
chain** — it does not shorten, skip, or pre-approve any future step.

---

## 10. Future S23 Plan

S23 will be **either**:

- a **docs-only** design of the paper order safety gate described in §6 of
  this document (with still no implementation), **or**
- a **tests-only** characterization of the architecture invariants this
  document defines (e.g. tests that assert the existing S17/S19/S20/S21
  artifacts and decisions remain wired together exactly as described here,
  with no new production behaviour).

Whichever direction S23 takes, it must satisfy all of the following:

| Constraint | S23 requirement |
|---|---|
| Broker, API, Alpaca, or credential access | **No** |
| Environment variable or network access | **No** |
| Order submission of any kind | **No** |
| Paper or live trading approval | **No** |
| Runtime or execution module changes | **No** |
| New artifact types created | **No — design or characterize only** |
| Implementation of any "future only" component from §4/§5 | **No** |

S23 — like every step before it — requires its own PR and its own review. It
does not inherit any implementation or approval rights from this document.

---

## 11. Safety Statement

This design document does not:

- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Create any real configuration file, paper artifact, simulation artifact, or
  approval artifact.
- Implement, scaffold, or stub any architecture component — pure offline or
  future-only.
- Add any broker, API, Alpaca, credential, environment variable, or network
  access to the codebase.
- Submit, request, place, cancel, or modify any order.
- Enable automated execution, persistence, or promotion of any kind.
- Change the live gate status, kill switch, or any fail-closed guard.
- Grant any permission beyond allowing a future S23 docs-only or tests-only
  design PR.

`APPROVED_FOR_PAPER_TRADING_DESIGN` (the S21 review decision) is a review
classification only. It authorised only the creation of this document.
Simulation `PASS` (S19) remains an offline research finding only. Neither
authorises paper trading, live trading, or any order submission — and neither
does this document.

Paper trading remains not approved. Live trading remains blocked. No
automatic promotion into runtime or execution follows from this document or
from any architecture status defined here. No order action of any kind
follows from any architecture status described in this document. All
live-gate safety flags remain fail-closed.

*Nothing in this repository is financial advice.*

---

## 12. Relationship to S-Series

```
S14 (promotion evaluator)
  └─ S15 (manual review workflow)
       └─ S16 (paper config design)
            └─ S17 (config validator)
                 └─ S18 (paper simulation design)
                      └─ S19 (simulation implementation)
                           └─ S20 (simulation integration tests)
                                └─ S21 (results review workflow design)
                                     └─ S22 (paper trading architecture design)  ← this PR
                                          └─ S23 (safety gate design, or
                                                  architecture invariant
                                                  characterization tests —
                                                  docs-only or tests-only)
                                               └─ future: paper trading
                                                  implementation PRs, each
                                                  separately approved, each
                                                  gated, each reviewed
```

Each step requires its own PR. No step automatically grants permission for
the next. Paper trading implementation requires multiple explicit approval
artifacts of new types (§8), each in its own separate PR, plus an operator
observation period and ongoing evidence review. Live trading remains blocked
throughout the entire S-series and beyond, gated by the existing
`live_readiness_gate` / `live_submit_enablement_gate` / kill-switch
infrastructure, none of which this document touches, weakens, or proposes to
change.

---

*Nothing in this document is financial advice.*
*No position sizing, entry/exit timing, broker connection, or trading*
*decision is implied, designed in implementable detail, or approved.*
