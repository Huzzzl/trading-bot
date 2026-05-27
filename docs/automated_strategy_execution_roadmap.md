# Automated Strategy Execution Roadmap

Design and roadmap document for the final project goal: a fully automated
strategy execution trading bot targeting 1-hour to 1-day holding horizons.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**This document does not approve automated live trading.**
**This document does not approve any individual trade.**
**All position and trading decisions remain entirely manual until automation**
**is implemented, tested, reviewed, and explicitly approved in its own PR.**

---

## 1. Final Target System

The final goal is an automated strategy execution bot that mechanically
executes predefined trading strategies without emotional decision-making.
The bot replaces manual operator judgment in the entry/exit execution loop,
while preserving all safety gates, redaction policies, and kill switch
behaviors established in the current infrastructure.

### Core components

| Component | Responsibility |
|-----------|---------------|
| **Strategy signal generator** | Reads historical bars and position state; outputs BUY / SELL / HOLD / BLOCK |
| **Risk gate** | Validates signal against hard rules before any action is approved |
| **Order executor** | Accepts approved actions only; submits exactly one broker mutation per run |
| **Position manager** | Tracks open position state; never infers position from broker without reconciliation |
| **Exit manager** | Applies exit rules; triggers EXIT_SIGNAL when conditions are met |
| **Scheduler** | Triggers evaluation cycles on a defined timeframe cadence |
| **Audit logger** | Records every signal, risk decision, approval, and execution event |
| **Kill switch** | Blocks all execution immediately on activation; requires explicit reset |
| **Read-only monitor** | Provides operator visibility into position and order state without mutation |
| **Paper / live separation** | Paper and live execution paths are strictly separated; paper is default |

### Target trading horizon

- Minimum holding period: approximately **1 hour**
- Maximum holding period: approximately **1 day**
- Evaluation cadence: aligned with bar timeframe (1h or 1d)

The bot is not a high-frequency or intra-minute system. All execution
decisions are made at bar boundaries, not tick-by-tick.

---

## 2. Strategy Scope

### Initial constraints (hard limits until explicitly relaxed by design)

| Constraint | Value |
|-----------|-------|
| Symbol universe | **SPY only** — no multi-symbol automation initially |
| Direction | **Long only** — no shorting initially |
| Leverage | **None** — no margin or leveraged instruments initially |
| Instruments | **Equity only** — no options, futures, or other derivatives initially |
| Concurrent positions | **One at a time** — no multi-leg or simultaneous positions initially |
| Pyramiding | **Not permitted** unless separately designed and approved |
| Strategy type | **Deterministic rules only** — no ML model live execution initially |
| Re-entry | **No automatic same-day re-entry after exit** unless separately designed |
| Averaging down | **Not permitted** |
| Timeframe | **1h to 1d bars** — no intra-minute data initially |

### What "deterministic rules" means

A deterministic strategy produces the same output (BUY / SELL / HOLD / BLOCK)
given the same inputs (historical bars, position state, market session).
No random decisions, no model sampling, no gradient-based scoring that changes
between runs. This is required for testability, auditability, and safe review.

---

## 3. Current Foundation Already Completed

The following infrastructure is implemented, tested, and merged to `main`:

| Component | Status | Notes |
|-----------|--------|-------|
| `live_credential_presence_guard` | Complete | Validates env var presence without exposing values |
| `live_operator_config_override_review` | Complete | Offline safety acknowledgement review |
| `live_broker_preflight_readonly` | Complete | Read-only account/clock/asset check via real adapter |
| `live_single_manual_submit` + `AlpacaLiveSubmitBroker` | Complete | Single manual buy with all gates; real adapter gated behind explicit flag |
| `live_position_reconciliation_readonly` | Complete | Read-only position and open-order presence check |
| `manual_position_status_checker_readonly` + `AlpacaManualPositionStatusBroker` | Complete | On-demand position/order/session status; market session mapping |
| Redaction and no-sensitive-output policy | Complete | Exception text, IDs, prices, quantities never in output |
| No-mutation-without-gates policy | Complete | All mutation paths require explicit CLI flag + artifact gates |
| Mock-only test pattern | Complete | All tests use injected mocks; no real Alpaca calls in any test |
| Fail-closed gate design | Complete | BLOCKED is default; PASS requires all gates to explicitly pass |

This infrastructure is the safety foundation the automation will be built on.
It is not the final product — it is a prerequisite.

---

## 4. Gap to Final Automation

The following components are **not yet implemented** and are required before
any automated live trading is possible:

| Missing component | Notes |
|-------------------|-------|
| Strategy signal module | Offline only first; no broker calls; deterministic contract |
| Historical data ingestion | Bar data pipeline for 1h/1d; no live tick feed initially |
| Backtest validation | Must validate strategy on historical data before any live use |
| Paper trading executor | Full automated cycle on paper account before any live automation |
| Automated risk engine | Programmatic enforcement of all risk rules per-signal |
| Automated sell/close path | Separate design and approval required; not reuse of buy path |
| Order lifecycle manager | Tracks order from submission through fill/rejection/expiry |
| Scheduler | Safe cadenced trigger; fail-closed if previous cycle not completed |
| Automated state machine | Formal states with transitions; tested exhaustively before live |
| Live automation approval model | New approval artifact type for recurring automated runs |
| Monitoring and alerting | Read-only status stream; operator notification on state changes |
| Kill switch enforcement | Must block scheduler at activation; requires explicit reset procedure |

None of the above may be added as a side effect of another PR. Each requires
its own design document, mock-only implementation PR, and safety review.

---

## 5. Proposed Staged Roadmap

Each phase must be completed and reviewed before the next begins.
No phase may be skipped. Each phase has its own PR(s).

### Phase A — Strategy signal module (offline only)

**Status: offline core implemented — `src/strategy/signal_engine.py`**

- Implement `strategy_signal_engine` as a pure function: inputs → signal
- No broker calls, no credentials, no network access
- Deterministic contract: same inputs always produce same output
- Extensive unit tests covering all signal types and edge cases
- Source scans: no Alpaca import, no os.environ, no network libraries

**This does not trade. This does not approve automation. Risk gate, executor,**
**scheduler, and paper/live trading are not implemented — each requires its own PR.**

### Architecture alignment note

Before Phase B implementation begins, the repository will undergo staged
architecture alignment for the trend-following MVP. This is documented in
`docs/trend_bot_architecture_refactor_plan.md` and consists of up to 10
small PRs (strategy factory, indicators, trend analysis, TrendFollowing
strategy, position sizer, metrics fix, backtest runner, slim main.py, tools
isolation, README). This does not change the Phase A–H safety roadmap and
does not approve live or paper trading.

### Phase B — Backtest and metrics

**Status: design complete — `docs/backtest_and_metrics_offline_design.md`**

- Historical bar data ingestion from offline source
- Backtest runner: applies strategy to historical bars, records trades
- Metrics: win rate, max drawdown, Sharpe ratio, trade count, hold duration
- Backtest results must be reviewed and documented before Phase C
- No live execution; no real broker calls

**This does not trade. This does not approve automation. Implementation,**
**paper trading, risk gate, and live trading each require their own PR.**

### Phase C — Paper trading execution

- Paper account executor: applies approved signal on Alpaca paper account
- Full automated cycle: signal → risk check → paper submit → fill confirm
- All existing gate patterns preserved
- Extensive logging; kill switch enforced
- Must run for a defined observation period before Phase D

### Phase D — Automated risk gate

- Formal risk engine: validates signal against all hard rules programmatically
- Risk gate output: APPROVED / BLOCKED / KILL_SWITCH
- Must not approve any action that violates any hard rule
- Risk gate is called before every executor invocation; cannot be bypassed
- Risk gate is separately tested from signal module

### Phase E — Mock automated buy/sell state machine

- Full state machine implemented with mock broker
- All states and transitions tested exhaustively
- Exception paths tested: verify BLOCKED and ERROR_BLOCKED are reachable
- Kill switch tested: verify KILL_SWITCH_ACTIVE blocks all transitions
- No real broker calls in any test

### Phase F — Paper broker integration

- State machine connected to real paper Alpaca account
- Automated evaluation cycles run on paper for an observation period
- Results reviewed: fill quality, rejection handling, state correctness
- Kill switch tested on real paper account
- No live account access in Phase F

### Phase G — Limited live automation (tiny notional cap)

- Live account integration with hard notional cap (e.g., ≤ $100)
- Requires explicit automation approval artifact (new type)
- Requires fresh preflight PASS
- Requires observation period on paper first (Phase F evidence)
- Scheduler runs at defined cadence; fail-closed if prior cycle incomplete
- Kill switch must be tested before first live automated run

### Phase H — Expanded live automation

- Notional cap raised only after documented evidence from Phase G
- Each cap increase requires its own review
- Multi-symbol support added only after single-symbol is stable
- No new instrument types (options, futures) until separately designed

---

## 6. Required State Machine

No live automation may be implemented until this state machine is fully
designed, implemented, and tested with a mock broker.

### States

| State | Meaning |
|-------|---------|
| `IDLE` | No active position; awaiting next evaluation cycle |
| `SIGNAL_OBSERVED` | Strategy has emitted a BUY or SELL signal |
| `RISK_CHECK_PENDING` | Signal forwarded to risk gate; awaiting decision |
| `RISK_BLOCKED` | Risk gate rejected the signal; return to IDLE |
| `ENTRY_APPROVED` | Risk gate approved BUY; proceeding to submit |
| `ENTRY_SUBMITTED` | Buy order submitted; awaiting fill confirmation |
| `POSITION_OPEN` | Position confirmed open; holding |
| `EXIT_SIGNAL_OBSERVED` | Strategy or exit rule has emitted a SELL signal |
| `EXIT_APPROVED` | Risk gate approved SELL; proceeding to submit |
| `EXIT_SUBMITTED` | Sell order submitted; awaiting fill confirmation |
| `POSITION_CLOSED` | Position confirmed closed; return to IDLE |
| `ERROR_BLOCKED` | Unrecoverable error; requires operator intervention |
| `KILL_SWITCH_ACTIVE` | Kill switch engaged; all transitions blocked |

### Transition rules

- Every transition must be logged with a timestamp and reason code.
- No transition may be taken without a valid current state.
- `KILL_SWITCH_ACTIVE` is terminal until explicitly reset by the operator.
- `ERROR_BLOCKED` is terminal until explicitly reset by the operator.
- Any unhandled exception → `ERROR_BLOCKED` (fail-closed).
- No transition from `IDLE` → `ENTRY_SUBMITTED` without passing through
  `SIGNAL_OBSERVED → RISK_CHECK_PENDING → ENTRY_APPROVED`.
- The risk gate cannot be bypassed by any code path.

---

## 7. Risk Rules for Initial Automation

These are hard rules enforced by the automated risk gate. No signal approval
is possible if any rule is violated. Rules may only be relaxed by a
dedicated design and review PR.

| Rule | Value |
|------|-------|
| Symbol | SPY only |
| Direction | Long only |
| Max concurrent positions | 1 |
| Max notional per trade | TBD (set in Phase G; ≤ $100 initially) |
| Max trades per day | TBD (e.g., 1 or 2; conservative initially) |
| Retry loops | Not permitted — BLOCKED is final for that cycle |
| Market orders outside regular hours | Not permitted unless separately approved |
| Averaging down | Not permitted |
| Same-day re-entry after exit | Not permitted unless separately designed |
| Kill switch state | Blocks all actions immediately |
| Stale data (bar older than threshold) | Blocks trading |
| Open order ambiguity | Blocks trading until resolved |
| Position ambiguity | Blocks trading until reconciled |
| Broker exception | Blocks trading; details redacted; state → ERROR_BLOCKED |
| Missing or non-PASS prerequisite artifact | Blocks trading |

---

## 8. Strategy Interface Proposal

A future strategy module must conform to this interface. The interface
ensures the strategy cannot directly call a broker or bypass the risk gate.

### Inputs

| Input | Type | Notes |
|-------|------|-------|
| `bars` | `list[Bar]` | Historical OHLCV bars; 1h or 1d; no look-ahead |
| `position_state` | `PositionState` | Current position: open/flat, entry price absent (boolean only) |
| `open_order_state` | `OpenOrderState` | Open orders present: bool |
| `market_session` | `str \| None` | Allowlisted: `"open"`, `"closed"`, `"pre_market"`, `"after_hours"`, `None` |

### Outputs

| Output | Type | Notes |
|--------|------|-------|
| `signal` | `str` | One of: `"BUY"`, `"SELL"`, `"HOLD"`, `"BLOCK"` |
| `reason_code` | `str` | Short audit code; no sensitive data |
| `confidence` | `None` | Not used in deterministic strategies |

### Constraints

- Strategy must be a pure function with no side effects.
- Strategy must not call any broker method.
- Strategy must not read credentials or env vars.
- Strategy must not import Alpaca SDK or any network library.
- Strategy must return the same output for the same input (deterministic).
- Strategy output is validated by the risk gate before any action is taken.
- `BLOCK` output from strategy → risk gate blocks; no executor called.

---

## 9. Execution Interface Proposal

A future executor must conform to this interface. The executor is
responsible only for submitting a pre-approved action — it does not
compute strategy and cannot bypass the risk gate.

### Inputs

| Input | Type | Notes |
|-------|------|-------|
| `approved_action` | `ApprovedAction` | Pre-validated by risk gate; includes symbol, side, notional |
| `broker` | `BrokerClient` | Injected; never constructed by executor |
| `ledger_path` | `Path` | Pre-submit row written before broker call |
| `kill_switch` | `KillSwitch` | Checked before every mutation |

### Behavior

- Checks kill switch before any broker call; aborts if active.
- Writes pre-submit ledger row before calling broker.
- Calls broker exactly once per run (one mutation per invocation).
- Writes post-submit ledger row after broker response.
- On exception: state → ERROR_BLOCKED; exception text redacted.
- Never retries a failed submission.
- Never computes a strategy signal.
- Never bypasses the risk gate.
- Never raises — all errors captured and state transitioned.

---

## 10. Audit and Safety Requirements

### Must record (non-sensitive fields only)

| Event | Fields to record |
|-------|-----------------|
| Strategy signal | timestamp, signal, reason_code, bar_count |
| Risk decision | timestamp, input_signal, decision, rule_violated (if any) |
| Action intent | timestamp, symbol, side, notional, state_before |
| Approval result | timestamp, approval_type, result |
| Execution result | timestamp, result, state_after |
| State transition | timestamp, from_state, to_state, trigger |

### Must NOT record

| Field | Reason |
|-------|--------|
| Credentials or credential fragments | Sensitive |
| Account ID or account number | Sensitive identifier |
| Raw broker order ID | Sensitive identifier |
| Raw broker response body | May contain sensitive data |
| Exact account balance or buying power | Sensitive financial data |
| Fill price or exact position cost | Not needed for audit |
| Unnecessary position details beyond boolean presence | Not needed |

All audit records must be written to a local file; none are transmitted
to an external service without a dedicated design review.

---

## 11. Non-Goals for Now

The following are explicitly out of scope until separately designed,
approved, and implemented:

| Non-goal | Status |
|----------|--------|
| Fully autonomous live trading immediately | Out of scope — staged phases required |
| Multi-symbol portfolio automation | Out of scope — SPY only initially |
| Options trading | Out of scope |
| Futures or other derivatives | Out of scope |
| Leverage or margin | Out of scope |
| Short selling | Out of scope |
| High-frequency or intra-minute trading | Out of scope |
| ML model live execution | Out of scope — deterministic only initially |
| Automatic parameter optimization in live | Out of scope |
| Telegram / Slack / email notifications | Out of scope (separate design required) |
| Web dashboard or UI | Out of scope |
| Multi-account management | Out of scope |

---

## 12. Next Engineering Step After This PR

The immediate next step after this roadmap is approved is:

**Design and implement `strategy_signal_engine` — offline only.**

Requirements for that next PR:
- Pure function: `(bars, position_state, open_order_state, market_session) → signal`
- No Alpaca SDK import — source-scanned
- No network library import — source-scanned
- No `os.environ` access — source-scanned
- No broker calls of any kind
- No credentials read
- No live execution
- Deterministic: same input always produces same output
- Fully unit-tested with mock inputs
- Signal contract documented and reviewed

That PR must be docs + implementation only — no live execution, no paper
execution, no broker integration. Paper and live execution come in later phases.

---

## References

- `src/tools/live_position_reconciliation_readonly.py` — read-only position check (Phase foundation)
- `src/tools/manual_position_status_checker_readonly.py` — on-demand status check (Phase foundation)
- `src/tools/live_single_manual_submit.py` — single manual buy (Phase foundation)
- `docs/live_readiness_status.md` — full milestone history
- `docs/manual_position_monitoring_and_exit_framework.md` — post-position monitoring framework
- `docs/manual_position_status_checker_readonly_design.md` — status checker design

---

## Suggested Git Tag

```
automated-strategy-execution-roadmap-designed
```

---

## Warnings

> **This document does not approve automated live trading.**
> **This document does not approve any individual trade.**
> **No code is implemented here.**
> **No Alpaca endpoint is contacted.**
> **No credentials are read.**
> All automated live trading requires completing the full staged roadmap
> (Phases A–G), with each phase reviewed and approved in its own PR.
> Until automation is fully implemented, tested, and approved, all trading
> decisions remain entirely manual operator actions.

> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility.
