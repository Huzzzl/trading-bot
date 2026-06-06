# Paper Simulation Design

Design document for S18: the paper simulation layer that follows S17 config
validation.

**S18 is docs-only. No source code changes. No tests. No config files.**
**No paper or live trading approval. No broker, API, credential, env,**
**network, or order access. No automatic execution or promotion.**

---

## 1. Purpose

After an S17 `validate_paper_config()` call returns `PASS`, the next step is
to design a structured simulation layer that would replay historical strategy
behaviour against the validated config parameters, producing simulated trade
and equity outputs entirely offline.

This document defines:

- the preconditions required before any paper simulation may be designed
- the proposed simulation inputs and outputs
- the simulation safety model
- the proposed simulation status vocabulary
- the future S19 implementation plan
- the relationship between simulation PASS and paper trading approval

No simulation runner is implemented in this PR. No paper trading is approved.

---

## 2. Scope

| Item | In scope |
|------|----------|
| Define paper simulation inputs | Yes |
| Define paper simulation outputs | Yes |
| Define simulation safety model | Yes |
| Define simulation status vocabulary | Yes |
| Describe S19 future implementation plan | Yes |
| Implement any simulation runner | **No** |
| Implement paper trading | **No** |
| Implement live trading | **No** |
| Add broker, API, Alpaca, or credential access | **No** |
| Read environment variables | **No** |
| Make network calls | **No** |
| Submit, request, or cancel any order | **No** |
| Approve paper or live trading | **No** |
| Load or write any config file | **No** |
| Write any output, report, or artifact | **No** |
| Change any runtime or execution module | **No** |

---

## 3. Preconditions

All of the following conditions must be satisfied before any paper simulation
design work begins. These conditions do not approve paper trading.

1. **S14:** The candidate must have `CandidatePromotionResult.status ==
   PAPER_CANDIDATE_ELIGIBLE`.
2. **S15:** The manual review must have recorded decision
   `APPROVED_FOR_PAPER_CONFIG_DESIGN`.
3. **S16:** A paper config schema ("PC/1.0") must exist in the operator's
   local evidence archive.
4. **S17:** `validate_paper_config(config_dict)` must return `result == "PASS"`
   for the specific config being simulated.

Satisfying all four preconditions only permits designing a future paper
simulation layer. It does not permit paper trading, live trading, or any
order submission.

---

## 4. Proposed Future Simulation Inputs

A future paper simulation function would accept only already-loaded,
already-validated in-memory objects. No file I/O, no network, no broker state.

### 4.1 Required inputs

| Input | Source | Notes |
|-------|--------|-------|
| `config_dict` | Already-loaded and S17-validated paper config dict | Must have passed `validate_paper_config()` with `result == "PASS"` before being passed to the simulation |
| `bars` | Offline historical cached bars only | Pre-loaded from `data/cache/`; no live market data; no network fetch at simulation time |
| `start_date` | ISO-8601 date string | Simulation window start; must lie within the cached bars range |
| `end_date` | ISO-8601 date string | Simulation window end; must lie within the cached bars range |

### 4.2 Derived from config

The simulation would read the following fields directly from the validated
config dict. No additional inputs required for these.

| Config field | Simulation use |
|---|---|
| `candidate_id` | Provenance cross-check |
| `run_id` | Provenance cross-check |
| `source_git_sha` | Provenance cross-check |
| `symbol` | Instrument identifier |
| `interval` | Bar interval; must match loaded bars |
| `strategy_family` | Strategy dispatch key |
| `holding_horizon` | Expected exit cadence (informational) |
| `max_notional_per_position` | Position size cap applied per simulated entry |
| `max_position_fraction` | Fraction cap applied per simulated entry |
| `max_daily_loss` | Daily loss halt threshold |
| `max_drawdown_stop` | Cumulative drawdown halt threshold |
| `max_orders_per_day` | Order count cap per simulated day |
| `min_cash_buffer` | Minimum undeployed cash fraction |
| `allowed_order_types` | Determines fill model (market vs limit) |
| `slippage_bps_assumption` | Slippage applied to simulated fills |
| `commission_bps_assumption` | Commission deducted from simulated fills |

### 4.3 Explicitly excluded inputs

The following are never passed to the simulation function:

| Excluded input | Reason |
|---|---|
| Broker account state | Simulation is fully offline; no account connection |
| Live or real-time market data | Offline cached bars only |
| Credentials of any kind | No broker/API/network access |
| Environment variables | No env var reads |
| Open orders or position state from broker | Not available; simulation tracks synthetic state |

---

## 5. Proposed Simulation Outputs

All outputs are in-memory dicts or lists. No files are written during
simulation. A future S19 persistence layer may write outputs; that is out of
scope here.

### 5.1 Simulation summary dict

| Field | Type | Description |
|-------|------|-------------|
| `simulation_result` | string | One of the statuses in §6 |
| `blocker` | string or null | Reason for BLOCKED/ERROR; null on PASS |
| `candidate_id` | string | From config; provenance |
| `run_id` | string | From config; provenance |
| `start_date` | string | Actual simulation window start used |
| `end_date` | string | Actual simulation window end used |
| `bars_used` | integer | Count of bars in the simulation window |
| `total_simulated_trades` | integer | Count of completed simulated trades |
| `total_return_pct` | number or null | Cumulative simulated return over the window |
| `max_drawdown_pct` | number or null | Worst simulated drawdown fraction |
| `win_rate_pct` | number or null | Fraction of profitable simulated trades |
| `risk_limit_events` | integer | Count of times a risk limit halted a simulated entry |
| `broker_calls_made` | boolean | Always `False` — simulation makes no broker calls |
| `credentials_read` | boolean | Always `False` |
| `network_calls_made` | boolean | Always `False` |
| `order_action_requested` | boolean | Always `False` — no real orders |
| `live_trading_allowed` | boolean | Always `False` |

### 5.2 Simulated trades list

A list of dicts, one per completed simulated round-trip trade. Each dict
contains:

| Field | Type | Description |
|-------|------|-------------|
| `entry_bar_index` | integer | Index into the bars list of the simulated entry bar |
| `exit_bar_index` | integer | Index into the bars list of the simulated exit bar |
| `entry_price_assumption` | number | Entry price with slippage applied (no raw broker price) |
| `exit_price_assumption` | number | Exit price with slippage applied (no raw broker price) |
| `simulated_shares` | number | Share count based on notional cap and price |
| `simulated_pnl` | number | Net P&L after commission and slippage |
| `exit_reason` | string | One of: `stop_loss`, `max_drawdown_stop`, `daily_loss_limit`, `max_orders_reached`, `session_end`, `end_of_simulation` |

### 5.3 Simulated daily equity curve

A list of dicts, one per simulated trading day, containing:

| Field | Type | Description |
|-------|------|-------------|
| `date` | string | ISO-8601 date |
| `simulated_equity` | number | Running simulated account value at end of day |
| `daily_pnl` | number | Day's simulated net P&L |

### 5.4 Simulated risk limit events

A list of dicts recording each time the simulation halted a potential entry
due to a risk limit from the config:

| Field | Type | Description |
|-------|------|-------------|
| `bar_index` | integer | Bar at which the limit was hit |
| `limit_type` | string | Which limit triggered (e.g. `max_daily_loss`, `max_drawdown_stop`, `max_orders_per_day`, `min_cash_buffer`) |
| `limit_value` | number | The config-defined threshold |

---

## 6. Simulation Safety Model

The simulation function is fail-closed. BLOCKED is the default; PASS requires
all safety invariants to hold.

### 6.1 Pre-simulation gate

Before any bar is processed, the simulation must verify:

1. `validate_paper_config(config_dict)` returns `result == "PASS"`. Any other
   result → immediate BLOCKED_CONFIG; no simulation runs.
2. All five safety flags on the validation result are `False`. Any `True` →
   immediate BLOCKED_SAFETY; no simulation runs.
3. Bars are non-empty and cover the requested date range. Empty or
   out-of-range → BLOCKED_DATA.
4. Bar interval in bars matches `config_dict["interval"]`. Mismatch →
   BLOCKED_CONFIG.

### 6.2 During-simulation invariants

- No broker calls. No credential reads. No network calls. No env var reads.
- No order submission, cancellation, or modification of any kind.
- No connection to any paper or live account.
- No live or real-time data consumed after simulation start.
- All safety flags on the simulation result are always `False`.

### 6.3 Post-simulation

- Simulation outputs are returned as in-memory dicts/lists only.
- No files are written. No database rows are inserted.
- No automatic promotion into the paper trading runtime.
- Simulation PASS does not approve paper trading (see §8).

### 6.4 No live gate changes

The simulation layer must not:

- Change `live_submit`, `live_readiness_gate`, or `live_submit_enablement_gate`
- Relax any fail-closed guard
- Add any broker, API, Alpaca, credential, or network import to any module
- Change `src/main.py`, `src/backtest/`, `src/strategy/`, `src/risk/`,
  `src/runtime/`, or `src/execution/`

---

## 7. Simulation Status Vocabulary

| Status | Meaning |
|--------|---------|
| `NOT_RUN` | Simulation has not been executed yet (initial state for a config record) |
| `PASS` | Simulation completed with no errors or safety violations; all outputs are populated |
| `BLOCKED_CONFIG` | Config validation did not return PASS, or config is inconsistent with bars |
| `BLOCKED_SAFETY` | A safety flag was True on the validation result |
| `BLOCKED_DATA` | Bars are empty, out of range, or have wrong interval |
| `ERROR_SIMULATION` | An unhandled exception occurred during simulation; outputs are incomplete |

### What `PASS` authorises

A simulation `PASS` authorises **only** a review of the simulation outputs as
one additional evidence input. It does **not**:

- Approve paper trading for any candidate, strategy, or symbol
- Approve live trading in any form
- Allow any broker, API, Alpaca, or network connection
- Allow any credential or environment variable access
- Allow any order submission, cancellation, or modification
- Change any runtime, execution, or live-gate module
- Change any kill switch or fail-closed guard
- Constitute financial advice or a trading recommendation

Paper and live trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by any simulation status.

---

## 8. Safety Statement

This design document does not:

- Approve paper trading for any candidate, strategy, or symbol.
- Approve live trading in any form.
- Create any real configuration file.
- Implement any simulation runner, strategy executor, or paper trading runner.
- Add any broker, API, Alpaca, credential, environment variable, or network
  access to the codebase.
- Submit, request, place, cancel, or modify any order.
- Enable automated execution of any kind.
- Change the live gate status, kill switch, or any fail-closed guard.
- Grant any permission beyond allowing a future paper simulation implementation
  PR (S19).

`APPROVED_FOR_PAPER_SIMULATION_DESIGN` (the S17 config approval status) is a
config classification only. It does not approve paper trading. Simulation PASS
is an offline research finding only. It does not approve paper trading.

Paper and live trading remain not enabled. All live-gate safety flags remain
fail-closed. No trade is approved by this document or by any simulation status
described herein.

---

## 9. Future S19 Implementation Plan

S19 will implement a pure offline paper simulation skeleton with the
following constraints.

### 9.1 Scope of S19

| Item | In scope for S19 |
|------|-----------------|
| Implement `run_paper_simulation(config_dict, bars, ...)` pure offline function | Yes |
| Return `PaperSimulationResult` frozen dataclass | Yes |
| Implement fail-closed pre-simulation gate (§6.1) | Yes |
| Produce simulation summary, trades list, equity curve, risk events | Yes |
| Injectable offline data and signal providers | Yes |
| All safety flags always False | Yes |
| Tests with injected fake providers only | Yes |
| Connect to broker or Alpaca API | **No** |
| Access credentials or environment variables | **No** |
| Make network calls | **No** |
| Submit any order | **No** |
| Write any files | **No** |
| Modify any runtime or execution module | **No** |
| Approve live or paper trading | **No** |

### 9.2 S19 API sketch

```
run_paper_simulation(
    config_dict,         # already-validated PC/1.0 dict (must have PASS from S17)
    bars,                # pre-loaded list of bar dicts; no network fetch
    *,
    start_date,          # ISO-8601 date string
    end_date,            # ISO-8601 date string
    _signal_provider,    # injectable for deterministic tests
    _fill_model,         # injectable for deterministic tests
) -> PaperSimulationResult
```

`PaperSimulationResult` is a frozen dataclass with:
- `result`: `"PASS"` or `"BLOCKED"` or `"ERROR"`
- `blocker`: string or `None`
- `status`: `PaperSimulationStatus` enum value
- `summary`: dict (§5.1 fields)
- `trades`: tuple of dicts (§5.2 fields)
- `equity_curve`: tuple of dicts (§5.3 fields)
- `risk_limit_events`: tuple of dicts (§5.4 fields)
- Five safety flags (all always `False`)

### 9.3 What S19 does not grant

S19 simulation implementation is not paper trading approval. Paper trading
requires:

1. A complete paper simulation design (S18) ← this PR
2. A paper simulation implementation (S19) with full gate review
3. A separate paper trading implementation PR with additional approval
4. An explicit paper trading approval artifact (new type, separate PR)
5. An observation period on the paper account
6. Evidence review before any live trading consideration

---

## 10. Relationship to S-Series

```
S13 (promotion design)
  └─ S14 (promotion evaluator)
       └─ S15 (manual review workflow)
            └─ S16 (paper config design)
                 └─ S17 (config validator)
                      └─ S18 (paper simulation design)   ← this PR
                           └─ S19 (simulation implementation, future)
                                └─ future: paper trading implementation (separate approval)
```

Each step requires its own PR. No step automatically grants permission for
the next. Paper trading implementation requires an explicit approval artifact
of a new type, a separate PR, and an operator observation period. Live trading
remains blocked throughout the S-series.

---

*Nothing in this document is financial advice.*
*No position sizing, entry/exit timing, or trading decision is implied or approved.*
