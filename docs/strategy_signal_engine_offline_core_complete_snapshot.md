# Strategy Signal Engine Offline Core — Complete

Snapshot document for Phase A completion: offline-only deterministic strategy
signal engine implemented in PR #140.

**This document does NOT trade.**
**This document does NOT submit, sell, cancel, replace, or close positions.**
**This document does NOT contact Alpaca.**
**This document does NOT read credentials.**
**This document does NOT access environment variables.**
**This document does NOT import any Alpaca SDK.**
**This document does NOT import any network library.**
**This document does NOT implement a broker executor.**
**This document does NOT implement a scheduler.**
**This document does NOT implement paper trading.**
**This document does NOT implement live trading.**
**This document does NOT approve automated trading.**
**This document does NOT approve any individual trade.**
**All position and trading decisions remain entirely manual.**

---

## What Was Implemented (PR #140)

### Source files

| File | Status |
|------|--------|
| `src/strategy/signal_engine.py` | Complete — offline-only deterministic signal engine |
| `tests/test_strategy_signal_engine.py` | Complete — 96 tests |

### Core function

`evaluate_signal(bars, position_state, open_order_state, market_session, config) → SignalResult`

Pure function. Deterministic. No side effects. No broker calls. No credential
reads. Same inputs always produce the same output.

---

## Signal Contract

### Inputs

| Input | Type | Notes |
|-------|------|-------|
| `bars` | `list[Bar]` | OHLCV bars in chronological order; most recent last; not mutated |
| `position_state` | `PositionState` | `has_position: bool`; `symbol: str \| None`; no entry price |
| `open_order_state` | `OpenOrderState` | `has_open_order: bool` |
| `market_session` | `str \| None` | `"open"` / `"closed"` / `"pre_market"` / `"after_hours"` / `None` |
| `config` | `SignalEngineConfig` | `strategy_name`, `symbol`, `timeframe`, `min_bars_required`, `short_window`, `long_window` |

### Output (`SignalResult`)

| Field | Type | Notes |
|-------|------|-------|
| `signal` | `str` | One of: `"BUY"`, `"SELL"`, `"HOLD"`, `"BLOCK"` |
| `reason_codes` | `list[str]` | Non-empty; short audit codes |
| `strategy_name` | `str` | From config |
| `timeframe` | `str` | From config |
| `symbol` | `str` | From config |
| `bar_count` | `int` | `len(bars)` |
| `deterministic` | `bool` | Always `true` |
| `broker_calls_made` | `bool` | Always `false` |
| `credentials_read` | `bool` | Always `false` |
| `live_submit_enabled` | `bool` | Always `false` |
| `order_action_requested` | `bool` | Always `false` |
| `position_decision_is_recommendation_only` | `bool` | Always `true` |

### Safety fields — hardcoded invariants (every result)

| Field | Required value |
|-------|---------------|
| `deterministic` | `true` |
| `broker_calls_made` | `false` |
| `credentials_read` | `false` |
| `live_submit_enabled` | `false` |
| `order_action_requested` | `false` |
| `position_decision_is_recommendation_only` | `true` |

---

## Gate Sequence (BLOCK on first failure)

| Gate | Trigger | Reason code |
|------|---------|-------------|
| 1 | `len(bars) < config.min_bars_required` | `INSUFFICIENT_BARS` |
| 2 | `config.symbol != "SPY"` | `INVALID_SYMBOL` |
| 3 | `config.timeframe not in {"1h", "1d"}` | `INVALID_TIMEFRAME` |
| 4 | `market_session != "open"` | `MARKET_NOT_OPEN` |
| 5 | `open_order_state.has_open_order` | `OPEN_ORDER_PRESENT` |

Gates are checked in order. Only the first failure reason code is returned.

---

## Strategy Logic (SMA Crossover Placeholder)

After all gates pass, the engine computes simple moving averages from bar
close prices:

- `short_sma` = mean of the last `config.short_window` closes
- `long_sma` = mean of the last `config.long_window` closes

| Condition | Signal | Reason code |
|-----------|--------|-------------|
| `short_sma > long_sma` and `not has_position` | `BUY` | `SMA_CROSSOVER_BULLISH` |
| `short_sma < long_sma` and `has_position` | `SELL` | `SMA_CROSSOVER_BEARISH` |
| `short_sma > long_sma` and `has_position` | `HOLD` | `HOLD_ALREADY_IN_POSITION` |
| `short_sma <= long_sma` and `not has_position` | `HOLD` | `HOLD_NO_POSITION_TO_EXIT` |

`BUY` and `SELL` from this module are **recommendations only**. They do not
execute anything. They do not call any broker. They do not write any ledger.
They must pass through the risk gate and executor before any broker action
can occur. Neither the risk gate nor the executor is implemented in this module.

---

## Test Coverage

### Summary

| Metric | Value |
|--------|-------|
| Targeted tests | 96 passed |
| Full suite | 4164 passed |
| Real Alpaca calls | None — engine has no broker dependency |
| Credentials read | None |
| Network calls | None |

### Test classes

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestBuySignal` | 6 | Bullish crossover, no position → BUY; reason code; bar count; symbol; strategy name |
| `TestSellSignal` | 4 | Bearish crossover, has position → SELL; reason code; bar count; symbol |
| `TestHoldSignal` | 5 | Bullish+position → HOLD; bearish+flat → HOLD; reason codes |
| `TestBlockInsufficientBars` | 5 | Zero, one, below-min bar counts → BLOCK; boundary; at-min passes |
| `TestBlockInvalidSymbol` | 5 | AAPL/lowercase/empty → BLOCK; SPY passes |
| `TestBlockInvalidTimeframe` | 6 | 5m/empty/tick → BLOCK; 1h/1d pass |
| `TestBlockMarketSession` | 9 | closed/pre_market/after_hours/None → BLOCK; open passes |
| `TestBlockOpenOrder` | 4 | Open order present → BLOCK; no order passes |
| `TestDeterminism` | 5 | Same input → same output; multiple runs; flag always true |
| `TestInputNotMutated` | 3 | bars list not modified; length unchanged; closes unchanged |
| `TestOutputFields` | 9 | All result fields present; correct types; valid signal value |
| `TestSafetyFields` | 12 | All safety invariants on BUY/SELL/HOLD/BLOCK paths; broker_calls/credentials never true |
| `TestGateOrder` | 4 | Gates fire in correct order; only first failure reported |
| `TestSmaLogic` | 3 | Custom windows; equal SMAs → HOLD |
| `TestSourceScans` | 16 | No Alpaca/network/environ/mutation markers in source |

---

## Safety Invariants Confirmed

| Invariant | Method | Result |
|-----------|--------|--------|
| No Alpaca SDK imported | `TestSourceScans::test_no_alpaca_import` | Confirmed absent |
| No `requests` imported | `TestSourceScans::test_no_requests_import` | Confirmed absent |
| No `httpx` imported | `TestSourceScans::test_no_httpx_import` | Confirmed absent |
| No `aiohttp` imported | `TestSourceScans::test_no_aiohttp_import` | Confirmed absent |
| No `urllib` imported | `TestSourceScans::test_no_urllib_request_import` | Confirmed absent |
| No environment variable access | `TestSourceScans::test_no_os_environ` | Confirmed absent |
| No `os` import | `TestSourceScans::test_no_os_import` | Confirmed absent |
| No `submit_order(` | `TestSourceScans::test_no_submit_order` | Confirmed absent |
| No `cancel_order(` | `TestSourceScans::test_no_cancel_order` | Confirmed absent |
| No `replace_order(` | `TestSourceScans::test_no_replace_order` | Confirmed absent |
| No `close_position(` | `TestSourceScans::test_no_close_position` | Confirmed absent |
| No POST/PATCH/DELETE markers | `TestSourceScans` | Confirmed absent |
| No ledger writes (`write_text`/`json.dump`) | `TestSourceScans` | Confirmed absent |
| `broker_calls_made` always `false` | `TestSafetyFields::test_broker_calls_never_true` | Confirmed |
| `credentials_read` always `false` | `TestSafetyFields::test_credentials_never_read` | Confirmed |
| `position_decision_is_recommendation_only` always `true` | `TestSafetyFields` | Confirmed |
| Same input → same output | `TestDeterminism` | Confirmed |
| Input bars not mutated | `TestInputNotMutated` | Confirmed |

---

## What Remains

The risk gate, executor, scheduler, and paper/live trading are **NOT implemented**.
Each of the following requires its own design, implementation, and review PR:

| Component | Status |
|-----------|--------|
| Historical data ingestion / backtest (Phase B) | Not implemented |
| Paper trading executor (Phase C) | Not implemented |
| Automated risk gate (Phase D) | Not implemented |
| Mock automated buy/sell state machine (Phase E) | Not implemented |
| Paper broker integration (Phase F) | Not implemented |
| Limited live automation (Phase G) | Not implemented |
| Expanded live automation (Phase H) | Not implemented |
| Scheduler | Not implemented |
| Kill switch | Not implemented |
| Monitoring and alerting | Not implemented |

No automated live trading may be implemented until all required phases are
completed, reviewed, and approved in their own PRs.

---

## Suggested Git Tag

```
strategy-signal-engine-offline-core-complete
```

---

## Warnings

> **BUY and SELL from this module are recommendations only.**
> They do not execute anything. No broker is called. No ledger is written.
> The risk gate and executor must be implemented in future phases before any
> broker action can occur.

> **`signal="BUY"` does not mean a buy order was placed.**
> **`signal="SELL"` does not mean a sell order was placed.**
> All position and trading decisions remain entirely manual operator actions.

> **The risk gate is not implemented.**
> **The executor is not implemented.**
> **The scheduler is not implemented.**
> **Paper and live trading remain not implemented.**
> No automated trading is approved by this document or by PR #140.

> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility.
