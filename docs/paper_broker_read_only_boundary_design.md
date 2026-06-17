# Paper Broker Read-Only Boundary Design

## Purpose

This document defines the architecture, inputs, outputs, safety checks,
account-isolation rules, failure modes, and future implementation plan
for a read-only paper-account connector.

This is a design document only. No implementation is included.

Explicit current status:

- No broker connection is implemented.
- No credentials are read.
- No network request is made.
- No account is accessed.
- No order is created, submitted, replaced, modified, or cancelled.
- Paper trading remains not approved.
- Live trading remains blocked.

---

## Scope

The future read-only boundary may eventually read only:

- Account environment classification (paper vs. live indicator)
- Account status (active, disabled, etc.)
- Cash balance
- Buying power
- Equity
- Open positions (as observations only)
- Open orders (as observations only)
- Market clock (open/close schedule)
- Connectivity health (reachable, latency)
- Broker response timestamp
- Broker request correlation ID

The future boundary must never mutate broker state.

---

## Explicitly forbidden operations

The read-only boundary must never perform any of the following:

- Submit order
- Place order
- Cancel order
- Replace order
- Modify order
- Close position
- Liquidate position or account
- Transfer funds
- Change account settings
- Enable live trading
- Use a live account
- Infer paper status from a user-provided label alone

Any attempt to perform a forbidden operation must hard-block and fail
closed.

---

## Proposed module boundary

A future module is proposed at:

```
src/broker/paper_account_reader.py
```

This module is not created in S42. It is a conceptual placeholder only.

Proposed public interface:

```python
read_paper_account_snapshot(
    adapter,
    *,
    expected_environment,
    request_id,
    requested_at_utc,
    timeout_policy,
) -> PaperAccountReadResult
```

This is conceptual only. No implementation exists.

---

## Proposed input model

Future inputs to the read-only boundary:

- **adapter**: An adapter instance injected by the caller. The boundary
  does not construct its own adapter or manage its own connection.
- **expected_environment**: Must be `"paper"`. The boundary rejects any
  other value.
- **request_id**: A caller-supplied correlation ID for tracing.
- **requested_at_utc**: A caller-supplied UTC timestamp.
- **timeout_policy**: Supplied externally. The boundary does not define
  its own timeout.
- **No raw credentials**: The function must never accept credentials,
  API keys, secrets, tokens, or account passwords as arguments.
- **No environment-variable access**: The function must never read
  environment variables directly.

Credential loading must be handled by a separately designed credential
provider boundary in a future PR (S43).

---

## Proposed output schema

A future immutable in-memory result dataclass with the following fields:

| Field | Type | Description |
|---|---|---|
| `result` | snapshot or `None` | The read snapshot, or `None` on failure |
| `status` | enum | Read outcome status |
| `blocker` | str or `None` | Human-readable reason for block |
| `environment` | str or `None` | Confirmed environment from broker |
| `account_status` | str or `None` | Account status from broker |
| `cash` | numeric or `None` | Cash balance |
| `buying_power` | numeric or `None` | Buying power |
| `equity` | numeric or `None` | Total equity |
| `positions` | tuple or `None` | Open positions (observation only) |
| `open_orders` | tuple or `None` | Open orders (observation only) |
| `market_clock` | dict or `None` | Market clock snapshot |
| `broker_timestamp` | str or `None` | Broker response timestamp |
| `request_id` | str | Correlation ID from input |
| `criteria_checked` | tuple | Names of checks performed |
| `criteria_failed` | tuple | Names of checks that failed |
| `broker_calls_made` | bool | Whether a broker call was attempted |
| `credentials_read` | bool | Whether credentials were accessed |
| `network_calls_made` | bool | Whether a network call was made |
| `order_action_requested` | bool | Whether any order action was attempted |
| `live_trading_allowed` | bool | Whether live trading is permitted |

Safety flag expectations for the future real read-only connector:

- `broker_calls_made` may eventually become `True` (reading is a broker
  call).
- `network_calls_made` may eventually become `True` (reading requires
  network).
- `credentials_read` may eventually become `True`, but only in the
  separate credential boundary (S43), not inside this module.
- `order_action_requested` must always remain `False`.
- `live_trading_allowed` must always remain `False`.

None of this is implemented in S42.

---

## Proposed statuses

Conceptual status enum members:

| Status | Meaning |
|---|---|
| `NOT_CHECKED` | No read attempted |
| `READ_OK_PAPER` | Successful paper-account read |
| `BLOCKED_ENVIRONMENT_UNKNOWN` | Environment could not be determined |
| `BLOCKED_LIVE_ACCOUNT` | Live account detected; hard-blocked |
| `BLOCKED_ACCOUNT_STATUS` | Account status invalid or disabled |
| `BLOCKED_SCHEMA` | Response schema validation failed |
| `BLOCKED_STALE_RESPONSE` | Response timestamp too old |
| `BLOCKED_SAFETY` | Safety check failed |
| `ERROR_CONNECTIVITY` | Network/connectivity failure |
| `ERROR_BROKER_RESPONSE` | Broker returned an unprocessable response |

---

## Required fail-closed checks

The future implementation must perform these deterministic checks in
order. Any failure blocks downstream use and returns the previous safe
state.

1. Environment is explicitly confirmed as paper by broker metadata.
2. Live environment is rejected immediately.
3. Environment mismatch between expected and actual is rejected.
4. Account status is readable and valid (not disabled, not suspended).
5. Response schema is valid (all expected fields present and typed).
6. Timestamps are present in the broker response.
7. Response is not stale (broker timestamp within acceptable window).
8. No mutation or action fields are present in the response.
9. No order-action capability is exposed by the adapter.
10. No live-trading approval field is `True`.
11. All returned positions and orders are treated as observations only.
12. Unknown broker state blocks downstream use.

---

## Paper/live account isolation

Design constraints for account isolation:

- Paper and live base URLs must be distinct. No shared endpoint.
- Environment must be verified from broker metadata, not only from local
  configuration labels.
- Live account detection must hard-block immediately.
- Ambiguous environment must hard-block immediately.
- No fallback from paper to live.
- No fallback from live to paper.
- No shared mutable adapter state between paper and live contexts.
- No automatic environment switching.

No real URLs, keys, secrets, tokens, account IDs, or endpoint strings
are included in this design document.

---

## Credential boundary

S42 does not design credential storage, retrieval, or management in
detail. That is deferred to S43.

Credential constraints for this boundary:

- No credentials in configuration files.
- No credentials in approval, plan, gate, lifecycle, preview, or ledger
  artifacts.
- No credentials in log output.
- No credentials in exception messages or stack traces.
- No credentials passed through the planner, validator, safety gate,
  lifecycle, preview renderer, or audit ledger modules.
- Credential isolation is a separate boundary designed in S43.

---

## Data minimization

The read-only boundary must minimize stored data:

- Store only fields needed for safety checks and future reconciliation.
- No full raw broker response persistence.
- No account identifiers in general-purpose artifacts (plans, gates,
  lifecycles, previews, ledger entries).
- No credentials or tokens stored in any artifact.
- No unnecessary personal or account metadata retained.

---

## Interaction with current offline chain

The current implemented chain is:

```
approval (PTA/1.0)
  -> planner (create_paper_order_plan)
  -> validator (validate_paper_order_plan)
  -> safety gate (evaluate_paper_order_safety_gate)
  -> lifecycle (create_lifecycle_from_plan)
  -> preview (render_paper_dry_run_preview)
  -> audit ledger (append_audit_entry)
```

This chain remains pure offline and in memory. The future read-only
broker snapshot must not automatically enter this chain.

Any future integration between the broker read-only boundary and the
existing offline chain requires a separate reviewed integration design
PR. No automatic wiring is permitted.

---

## No-trust assumptions

The read-only boundary must not trust any external input:

- Broker response may be missing entirely.
- Broker response may be stale.
- Environment metadata may be ambiguous or absent.
- Account may be disabled, suspended, or in an unknown state.
- Positions and orders may be internally inconsistent.
- Network may fail at any point.
- Retries may duplicate reads (idempotent reads only).
- Any ambiguity blocks downstream progression. Fail closed.

---

## Observation-only semantics

All data returned by the read-only boundary is observation only:

- Positions are observations, not action approvals.
- Orders are observations, not action approvals.
- Account status is an observation, not an action approval.
- Market-open status is an observation, not an action approval.
- Connectivity success is an observation, not an action approval.
- Read success is not paper-trading approval.
- Read success is not live-trading approval.
- No observation triggers automatic downstream action.

---

## Proposed test plan for a future implementation

The following tests should be created when the boundary is implemented
(not in S42):

- Paper account accepted (READ_OK_PAPER).
- Live account detected and hard-blocked (BLOCKED_LIVE_ACCOUNT).
- Unknown environment blocked (BLOCKED_ENVIRONMENT_UNKNOWN).
- Malformed broker response blocked (BLOCKED_SCHEMA).
- Stale broker response blocked (BLOCKED_STALE_RESPONSE).
- Disabled account blocked (BLOCKED_ACCOUNT_STATUS).
- Empty positions accepted (valid paper account with no positions).
- Multiple positions parsed as read-only observations.
- Open orders parsed as read-only observations.
- No mutation methods exposed by the result or adapter interface.
- Deterministic results (same input produces same output).
- Input immutability (adapter and arguments not mutated).
- No credential leakage in result, blocker message, or exceptions.
- No live fallback (paper failure does not attempt live).
- `order_action_requested` always `False`.
- `live_trading_allowed` always `False`.
- Fail-closed error handling (connectivity and broker errors return safe
  blocked state).

No tests are created in S42.

---

## Future implementation sequence

| Step | Description |
|---|---|
| S42 | Read-only boundary design (this document, docs-only) |
| S43 | Credential and account-isolation design (docs-only) |
| S44 | Connectivity health-check design (docs-only) |
| S45 | Pure interface/schema implementation with fake injected adapter only |
| S46 | Integration tests using fake adapter only |
| Later | Real broker connectivity only after separate explicit review |

Real broker connectivity requires:

- Completed S43 credential isolation design.
- Completed S44 health-check design.
- Completed S45 pure interface implementation.
- Completed S46 fake-adapter integration tests.
- Explicit review and approval for broker connectivity.
- Explicit review and approval for credential access.
- Explicit review and approval for network access.

---

## Safety invariants

- Read success is not order approval.
- Account connectivity is not paper-trading approval.
- A broker account snapshot is observation only.
- No mutation operation is permitted.
- No live-account access is permitted.
- `order_action_requested` must always be `False`.
- `live_trading_allowed` must always be `False`.
- Paper trading remains not approved.
- Live trading remains blocked.
- Fail closed.
