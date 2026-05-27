# Backtest and Metrics — Offline Design

Design document for Phase B of the automated strategy execution roadmap:
offline historical data ingestion, backtest runner, and metrics for
`strategy_signal_engine`.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live ledger is written.**
**No config is mutated.**
**No paper trading is implemented.**
**No live trading is implemented.**
**No automated trading is approved.**
**This document designs offline backtest and metrics only.**

---

## 1. Phase B Goal

Design an offline backtest system that:

1. Loads historical OHLCV bars from a local offline source.
2. Iterates bars chronologically with no look-ahead bias.
3. Calls `evaluate_signal()` at each bar boundary to obtain a signal.
4. Simulates a position locally using deterministic fill rules.
5. Accumulates performance metrics over the full bar history.
6. Produces a structured result containing only simulated, non-sensitive
   metric fields.

The backtest engine validates whether `strategy_signal_engine` produces
plausible signal behaviour on historical data before any paper or live
execution is considered. A passing backtest does not approve live trading.
Backtest results do not guarantee future performance.

---

## 2. Data Scope

### Allowed data sources

| Source | Status |
|--------|--------|
| Local CSV file | Allowed |
| Local fixture file (Python list / JSON) | Allowed |
| Alpaca data API | **Not allowed** |
| Any live broker data API | **Not allowed** |
| Any network request | **Not allowed** |
| Any credentials | **Not allowed** |

### Required fields

Each bar must provide:

| Field | Type | Notes |
|-------|------|-------|
| `timestamp` | `datetime` or ISO string | Chronologically ordered; no look-ahead |
| `open` | `float` | Bar open price |
| `high` | `float` | Bar high price |
| `low` | `float` | Bar low price |
| `close` | `float` | Bar close price |
| `volume` | `float` | Bar volume |

### Initial scope

| Constraint | Value |
|-----------|-------|
| Symbol universe | SPY only |
| Timeframes | `1h` and `1d` |
| Data format | Local CSV or in-process fixture |
| Minimum bar history | `config.long_window` bars (sufficient for SMA computation) |

---

## 3. Backtest Engine Scope

The backtest engine is a pure offline function:

```
run_backtest(bars, config, starting_equity) → BacktestResult
```

### Responsibilities

- Accept a list of `Bar` objects in chronological order (oldest first).
- Construct `PositionState` and `OpenOrderState` from local simulation state.
- Determine `market_session` from bar timestamp (or accept as input per bar).
- Call `evaluate_signal()` at each bar boundary with the slice of bars up to
  and including the current bar.
- Apply fill rules deterministically to simulate BUY/SELL fills.
- Track simulated equity and position state.
- Accumulate metrics.
- Return a `BacktestResult` containing all metrics labelled as simulated.

### Fill policy

**Policy: fill at the open of the next bar.**

- BUY signal at bar N → simulated fill at `bars[N+1].open`.
- SELL signal at bar N → simulated fill at `bars[N+1].open`.
- If bar N is the last bar, the signal is recorded but no fill is possible
  (signal is noted in output; position remains open at end of backtest).
- This policy is deterministic and avoids look-ahead within bar N.

This policy must be documented in the backtest output. Any alternative policy
requires its own design review before implementation.

### Prohibited behaviour

- Must not call any broker method.
- Must not write any live ledger.
- Must not read any environment variable.
- Must not import Alpaca SDK or any network library.
- Must not mutate the input `bars` list or any `Bar` object.
- Must not mutate the `config` object.
- Must not use randomness.
- Must not depend on wall-clock time (except optional `run_timestamp` in output).

---

## 4. Initial Simulation Rules

### Position rules

| Rule | Value |
|------|-------|
| Symbol | SPY only |
| Direction | Long only |
| Max concurrent positions | 1 |
| Pyramiding | Not permitted |
| Averaging down | Not permitted |
| Same-day re-entry after exit | Not permitted (initial implementation) |

### Signal handling

| Signal | Action |
|--------|--------|
| `BUY` | If flat: open simulated position at next-bar open |
| `BUY` | If already in position: ignored (HOLD behaviour) |
| `SELL` | If in position: close simulated position at next-bar open |
| `SELL` | If flat: ignored (HOLD behaviour) |
| `HOLD` | No action |
| `BLOCK` | No action; reason code recorded in metrics |

### Open order simulation

Initial implementation models all fills as **immediate deterministic fills**
at next-bar open. There are no pending open orders in the simulation.
`open_order_state.has_open_order` is always `False` during backtest evaluation.

More realistic order modelling (partial fills, rejections, expiry) is out of
scope until separately designed.

### Invalid state handling

If the simulation reaches an internally inconsistent state (e.g. SELL signal
with no recorded entry price, or BUY signal when position already marked open):
the backtest returns a `BacktestResult` with `status="ERROR"` and a
`reason` string. No exception is raised from the top-level function.

---

## 5. Metrics

All metrics are **simulated** values derived from local bar data. They do not
reflect real account balances, real fills, or real broker data.

### Required metrics

| Metric | Type | Description |
|--------|------|-------------|
| `total_return_pct` | `float` | `(final_equity / starting_equity - 1) * 100` |
| `final_equity` | `float` | Simulated ending equity |
| `starting_equity` | `float` | Initial equity passed to engine |
| `trade_count` | `int` | Number of completed round-trip trades (entry + exit) |
| `win_count` | `int` | Trades with positive simulated return |
| `loss_count` | `int` | Trades with zero or negative simulated return |
| `win_rate` | `float` | `win_count / trade_count` if `trade_count > 0` else `null` |
| `average_trade_return_pct` | `float \| null` | Mean per-trade return; `null` if no trades |
| `max_drawdown_pct` | `float` | Maximum peak-to-trough equity decline over the run |
| `exposure_bars` | `int` | Number of bars the simulated position was open |
| `exposure_pct` | `float` | `exposure_bars / total_bars * 100` |
| `average_hold_bars` | `float \| null` | Mean bars held per trade; `null` if no trades |
| `max_hold_bars` | `int \| null` | Maximum bars held in a single trade; `null` if no trades |
| `signal_counts` | `dict[str, int]` | Count of each signal: `{"BUY": n, "SELL": n, "HOLD": n, "BLOCK": n}` |
| `blocked_reason_counts` | `dict[str, int]` | Count per BLOCK reason code |
| `open_position_at_end` | `bool` | Whether a position was still open at the last bar |
| `fill_policy` | `str` | Documents the fill policy used (e.g. `"next_bar_open"`) |
| `total_bars` | `int` | Total bars evaluated |
| `status` | `str` | `"OK"` or `"ERROR"` |
| `reason` | `str \| null` | Non-sensitive error description if `status="ERROR"` |

### What must NOT appear in output

| Field | Reason |
|-------|--------|
| Credentials or credential fragments | Sensitive |
| Account ID or account number | Sensitive identifier |
| Real broker order ID | Sensitive identifier |
| Real broker response body | May contain sensitive data |
| Live account balance | Sensitive financial data |
| Live buying power | Sensitive financial data |
| Real position data from broker | Sensitive |
| Real fill price from broker | Not applicable to backtest |

Simulated fields are derived entirely from local bar data and the
`starting_equity` parameter. They must be clearly labelled as simulated.

---

## 6. Output Safety

Backtest output is a local in-process result. It must:

- Contain only simulated metrics (see Section 5).
- Never contact any external service.
- Never write to any file unless the operator explicitly requests a snapshot.
- If a snapshot is written, it must contain no credentials, no account
  identifiers, and no raw broker data.

All output fields that represent simulated values must be derivable
from the input bars and `starting_equity` alone, with no dependency on
live broker state.

---

## 7. Determinism Requirements

| Requirement | Notes |
|-------------|-------|
| Same bars + config → same output | Must hold unconditionally |
| No randomness | No `random`, no `uuid`, no stochastic elements in signal or fill logic |
| No wall-clock dependency | `run_timestamp` is the only permitted wall-clock read; it must not affect metrics |
| Input `bars` not mutated | Engine must not sort, filter, or modify the input list |
| `config` not mutated | Engine must treat config as read-only |
| No global state | Engine must be re-entrant: calling it twice with the same inputs must return equal results |

---

## 8. Proposed Files for Future Implementation

The following files must be created in a future implementation PR. None of
them are created in this design PR.

| File | Purpose |
|------|---------|
| `src/backtest/offline_backtest_engine.py` | Backtest runner; pure offline; no broker dependency |
| `tests/test_offline_backtest_engine.py` | Unit tests; no real Alpaca calls |
| `tests/fixtures/spy_1h_synthetic.py` or `.json` | Synthetic bars for tests; not real market data |

Output artifacts (CSV, JSON snapshots) are generated locally on demand.
They must not be committed to the repository unless they are sanitised
docs snapshots with no sensitive data.

---

## 9. Testing Requirements

The future implementation PR must include tests for:

| Test scenario | Notes |
|--------------|-------|
| No bars → status=ERROR or empty result | No crash |
| Insufficient bars → no trades; BLOCK reason recorded | `INSUFFICIENT_BARS` reason code |
| Bullish synthetic dataset → BUY signals; win trades | Verify via metrics |
| Bearish synthetic dataset → SELL signals if position held | Verify via metrics |
| No look-ahead bias | Bar slice passed to `evaluate_signal` must not exceed current bar index |
| Deterministic repeated runs | Same input → same output on multiple calls |
| Input bars not mutated | Bar list identical before and after run |
| Config not mutated | Config object identical before and after run |
| One position max | BUY while in position is ignored, not doubled |
| No pyramiding | Only one position opened per BUY sequence |
| No same-day re-entry | If exit on day D, no new position opened on day D |
| `total_return_pct` correct | `(final_equity / starting_equity - 1) * 100` |
| `win_rate` null when no trades | Not divided by zero |
| `max_drawdown_pct` correct | Peak-to-trough verified against synthetic data |
| `exposure_pct` correct | `exposure_bars / total_bars * 100` |
| `signal_counts` correct | Each signal type counted |
| `blocked_reason_counts` correct | Each BLOCK reason counted |

### Source scan tests (required)

| Scan | What must be absent |
|------|---------------------|
| No Alpaca import | `import alpaca`, `from alpaca` |
| No network imports | `import requests`, `import httpx`, `import aiohttp`, `from urllib` |
| No environment variable access | `os.environ` |
| No broker mutation calls | `submit_order(`, `cancel_order(`, `replace_order(`, `close_position(`, `close_all_positions(` |
| No HTTP mutation markers | `.post(`, `.patch(`, `.delete(` |
| No live ledger writes | `write_text(`, `json.dump(` (except to local snapshot on explicit request) |
| No config mutation | Config object must not be modified |

---

## 10. Non-Goals

The following are explicitly out of scope for Phase B:

| Non-goal | Status |
|----------|--------|
| Paper trading | Out of scope — Phase C |
| Live trading | Out of scope — Phase G |
| Broker execution of any kind | Out of scope |
| Scheduler | Out of scope |
| Automated risk gate | Out of scope — Phase D |
| State machine | Out of scope — Phase E |
| Parameter optimisation or sweep | Out of scope |
| ML model integration | Out of scope |
| Multi-symbol portfolio | Out of scope |
| Options, futures, or leveraged instruments | Out of scope |
| Short selling | Out of scope |
| Live data feed | Out of scope |
| Commission or slippage modelling | Out of scope unless separately designed |
| Real-time metrics dashboard | Out of scope |

---

## 11. Relationship to Later Phases

| Phase | Dependency on Phase B |
|-------|-----------------------|
| Phase C (paper trading) | Cannot start until Phase B implementation and snapshot are reviewed and approved |
| Phase D (automated risk gate) | Informed by Phase B signal distribution; not blocked by it |
| Phase G (live automation) | Requires Phases C–F completed first; Phase B is foundational evidence |

### Backtest evidence is not live-trading approval

A backtest that produces a positive `total_return_pct` does not:

- Approve any live trading.
- Approve any automated execution.
- Guarantee future performance.
- Remove the requirement to complete Phases C–G before any live automation.

Backtest results are offline evidence only. They must be reviewed and
documented in a snapshot PR before Phase C can proceed.

---

## 12. Next Engineering Step

After this design PR is approved:

1. Implement `src/backtest/offline_backtest_engine.py`:
   - Pure function: `run_backtest(bars, config, starting_equity) → BacktestResult`
   - No broker dependency
   - No network access
   - No credential reads
   - Deterministic
2. Implement `tests/test_offline_backtest_engine.py`:
   - All scenarios in Section 9
   - Source scans confirming offline-only
   - No real Alpaca calls
3. Use only synthetic fixture bars initially — no external data download.
4. Record results in a docs snapshot PR after implementation.
5. Review snapshot before proceeding to Phase C.

---

## Proposed Interface

```python
# src/backtest/offline_backtest_engine.py (future implementation)

@dataclass
class BacktestConfig:
    strategy_name: str
    symbol: str          # "SPY" only initially
    timeframe: str       # "1h" or "1d"
    short_window: int
    long_window: int
    min_bars_required: int
    same_day_reentry: bool = False  # False = no same-day re-entry after exit

@dataclass
class TradeRecord:
    entry_bar_index: int
    exit_bar_index: int
    hold_bars: int
    simulated_return_pct: float  # labelled simulated
    signal_at_entry: str         # "BUY"
    signal_at_exit: str          # "SELL"

@dataclass
class BacktestResult:
    status: str                  # "OK" or "ERROR"
    reason: str | None           # non-sensitive; None if status="OK"
    # --- simulated metrics ---
    starting_equity: float
    final_equity: float
    total_return_pct: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float | None
    average_trade_return_pct: float | None
    max_drawdown_pct: float
    exposure_bars: int
    exposure_pct: float
    average_hold_bars: float | None
    max_hold_bars: int | None
    total_bars: int
    signal_counts: dict            # {"BUY": n, "SELL": n, "HOLD": n, "BLOCK": n}
    blocked_reason_counts: dict    # {reason_code: n}
    open_position_at_end: bool
    fill_policy: str               # "next_bar_open"
    # --- invariants ---
    deterministic: bool = True
    broker_calls_made: bool = False
    credentials_read: bool = False
    live_submit_enabled: bool = False
    order_action_requested: bool = False

def run_backtest(
    bars: list[Bar],
    config: BacktestConfig,
    starting_equity: float,
) -> BacktestResult:
    """Pure offline deterministic backtest. No broker calls. No credential reads."""
    ...
```

---

## References

- `src/strategy/signal_engine.py` — Phase A signal engine (complete)
- `tests/test_strategy_signal_engine.py` — Phase A tests (96 passed)
- `docs/strategy_signal_engine_offline_core_complete_snapshot.md` — Phase A snapshot
- `docs/automated_strategy_execution_roadmap.md` — full roadmap

---

## Suggested Git Tag

```
backtest-and-metrics-offline-designed
```

---

## Warnings

> **This document does not implement any code.**
> **This document does not approve automated live trading.**
> **This document does not approve any individual trade.**
> **No Alpaca endpoint is contacted.**
> **No credentials are read.**
> All automated live trading requires completing the full staged roadmap
> (Phases A–H), with each phase reviewed and approved in its own PR.
> A positive backtest result does not approve live trading and does not
> guarantee future performance.
> Until automation is fully implemented, tested, and approved, all trading
> decisions remain entirely manual operator actions.

> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility.
