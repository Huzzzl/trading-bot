# Trend Bot Architecture Refactor Plan

Design document for aligning the repository architecture with the final goal:
a practical 1h-to-intraday automated trend-following trading bot.

**No code is implemented in this document.**
**No files are moved in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live ledger is written.**
**No config is mutated.**
**No paper trading is implemented.**
**No live trading is implemented.**
**No automated trading is approved.**
**This document plans the refactor only — each PR requires its own review.**

---

## 1. Refactor Decision

The refactor direction is accepted in principle. It will be executed as a
series of small, independently reviewable PRs. Each PR must be reviewed and
merged before the next begins.

### Principles

| Principle | Detail |
|-----------|--------|
| No rewrite from scratch | All changes are additive or targeted replacements |
| Preserve working tests | No PR may reduce passing test count without justification |
| Preserve no-look-ahead backtesting | The bar-by-bar event loop in `src/backtest/engine.py` must not be broken |
| Preserve live safety / fail-closed | All live safety gates, redaction policies, and kill-switch behaviours are unchanged |
| Preserve broker abstraction | `BrokerProtocol`, `FakeBroker`, `AlpacaBroker` interfaces remain stable |
| Preserve ORB as legacy/example | `OpeningRangeBreakout` becomes a benchmark/legacy strategy, not deleted |
| Trend-following becomes MVP strategy | New `TrendFollowing` strategy is the primary focus for the automated bot |

### What this refactor is not

- Not a from-scratch rewrite.
- Not a removal of existing live-safety tooling.
- Not a change to the Phase A–H automated execution roadmap.
- Not an approval of live or paper trading.
- Not a change to any broker integration.

---

## 2. Target Architecture

### Module flow

```
data provider
    │
    ▼
indicators (SMA, EMA, ATR, rolling breakout high/low)
    │
    ▼
analysis / trend (TrendState: bullish / bearish / neutral)
    │
    ▼
strategy (evaluate_signal → BUY / SELL / HOLD / BLOCK)
    │
    ▼
risk manager (position sizing, hard rules)
    │
    ▼
portfolio (simulated position state)
    │
    ▼
backtest engine (bar-by-bar event loop; no look-ahead)
    │
    ▼
broker adapter (FakeBroker / AlpacaBroker; injected)
    │
    ▼
paper / live runner (explicit opt-in; fail-closed by default)
```

Each layer communicates only downward. Strategy cannot reach into the broker
adapter. Broker adapter cannot reach into strategy. Risk manager sits between
strategy output and execution.

### Desired repository structure

```
src/
  data/
    base.py                    # existing — preserved
    yahoo_provider.py          # existing — preserved
    cached_provider.py         # existing — preserved
  indicators/
    moving_average.py          # new — SMA, EMA
    volatility.py              # new — ATR
    trend.py                   # new — rolling breakout high/low
  analysis/
    trend.py                   # new — TrendState classification
  strategy/
    base.py                    # existing — preserved
    signal_engine.py           # existing Phase A — preserved
    opening_range_breakout.py  # existing — preserved as legacy/benchmark
    trend_following.py         # new — MVP trend strategy
    factory.py                 # new — strategy selection by name
  risk/
    risk_manager.py            # existing — preserved
    position_sizer.py          # new — calculate_shares_by_risk()
  portfolio/
    portfolio.py               # existing — preserved
  backtest/
    engine.py                  # existing — preserved (bar-by-bar loop)
    offline_backtest_engine.py # Phase B — to be implemented
    metrics.py                 # existing — to be extended for 1h/1d
    trade.py                   # existing — preserved
    backtest_runner.py         # new or refined — wires factory + engine
  execution/
    broker.py                  # existing — preserved
    fake_broker.py             # existing — preserved
    alpaca_broker.py           # existing — preserved
    order_intent.py            # existing — preserved
    live_ledger.py             # existing — preserved
    live_submit_executor.py    # existing — preserved
    [paper_*.py files]         # existing — preserved
  reporting/
    report_generator.py        # existing — preserved
    reconciliation.py          # existing — preserved
  experiments/
    sweep_runner.py            # existing — preserved
    walk_forward_runner.py     # existing — preserved
  tools/                       # existing — audited in PR 9, not moved yet
  utils/
    logger.py                  # existing — preserved
  main.py                      # existing — to be slimmed in PR 8
```

---

## 3. What to Preserve

The following files and behaviours must not be broken by any refactor PR.
Any PR that touches these requires explicit justification.

### Source files to preserve

| File | Reason |
|------|--------|
| `src/backtest/engine.py` | Bar-by-bar event loop; no-look-ahead guarantee |
| `src/strategy/base.py` | `BaseStrategy`, `Signal`, `SignalDirection` — all strategies depend on these |
| `src/strategy/opening_range_breakout.py` | Legacy/benchmark strategy; existing tests must pass |
| `src/strategy/signal_engine.py` | Phase A signal engine; used by Phase B backtest |
| `src/data/base.py` | Data provider interface |
| `src/data/yahoo_provider.py` | Primary offline data source |
| `src/data/cached_provider.py` | Caching layer |
| `src/portfolio/portfolio.py` | Position and equity tracking for MVP |
| `src/risk/risk_manager.py` | Risk rule enforcement |
| `src/execution/broker.py` | Broker protocol / interface |
| `src/execution/fake_broker.py` | Test broker; must stay as the default test double |
| `src/execution/alpaca_broker.py` | Live broker adapter |
| `src/execution/order_intent.py` | Order intent dataclass |
| `src/execution/live_ledger.py` | Live trade ledger |
| `src/execution/live_submit_executor.py` | Gated live submission |
| `src/execution/paper_*.py` | Paper trading infrastructure |
| `src/reporting/` | Research and reporting utilities |
| `src/experiments/` | Sweep and walk-forward runners |
| All `src/tools/live_*.py` | Live-readiness safety tools |
| All `src/tools/manual_*.py` | Manual status/reconciliation tools |

### Behaviours to preserve

| Behaviour | Notes |
|-----------|-------|
| No look-ahead in backtest | `engine.py` passes only bars up to current bar to strategy |
| Fail-closed live execution | All mutation paths require explicit CLI flag + artifact gates |
| Mock-only test pattern | No real Alpaca calls in any test |
| Redaction policy | Exception text, IDs, prices, quantities never in output |
| No broker mutation without gates | BLOCKED is default; PASS requires all gates to explicitly pass |

---

## 4. Current Structural Problems

The following problems are documented here for planning purposes.
No code is changed in this PR.

| Problem | Detail |
|---------|--------|
| `src/main.py` too large | Mixes backtest, sweep, walk-forward, and live modes in a single file; should become a dispatcher only |
| ORB dominates architecture | Opening Range Breakout is the only complete strategy; trend-following infrastructure is absent |
| `src/tools/` overrepresented | Many manual live-readiness scripts in `src/tools/`; non-core scripts could move to `scripts/` later |
| Trend-following incomplete | No `indicators/`, no `analysis/trend.py`, no `TrendFollowing` strategy |
| Backtest metrics assume 5m bars | `bars_per_year` constant likely hard-codes 5-minute bar count; 1h/1d support needed |
| No strategy factory | ~~Strategy instantiation is scattered~~ — **resolved in PR 1**: `src/strategy/factory.py` added |
| README may lag architecture | Documentation likely reflects older state of the repository |

---

## 5. Staged Refactor Plan

Each item below is a separate PR. PRs must be merged in order where noted.
No PR may be skipped. Each requires its own test coverage where applicable.

### PR 1 — Strategy factory

**Status: implemented — `src/strategy/factory.py`**

**File:** `src/strategy/factory.py`

- Accepts a strategy name string and params dict.
- Returns an instantiated `BaseStrategy` subclass.
- Supports: `"opening_range_breakout"` and `"orb"` alias.
- `TrendFollowing` not wired yet — added in PR 4.
- No behaviour changes to existing ORB backtests.
- Unit tests: 45 passed (full suite 4209).
- Source scans confirm no Alpaca, network, environ, or mutation markers.
- Additive only. ORB preserved as legacy/benchmark. No main.py refactor.

### PR 2 — Indicators package

**Status: implemented**

**Files:**
- `src/indicators/__init__.py`
- `src/indicators/moving_average.py` — `sma(series, window)`, `ema(series, span)`
- `src/indicators/volatility.py` — `true_range(high, low, close)`, `atr(high, low, close, window)`
- `src/indicators/trend.py` — `rolling_high(series, window, *, exclude_current=True)`, `rolling_low(...)`, `breakout_above(...)`, `breakout_below(...)`

**Constraints:**
- Pure pandas functions; no broker calls; no network; no credentials.
- All functions must not use future data (no look-ahead).
- `rolling_high` and `rolling_low` default to `exclude_current=True` — required for breakout logic to avoid look-ahead bias.
- Unit tests: 83 passed (full suite 4292).
- Source scans confirm no Alpaca, network, environ, execution, or mutation markers.
- TrendFollowing strategy not implemented yet — PR 4.
- No main.py refactor yet — PR 8.

### PR 3 — Analysis / trend layer

**Status: implemented — `src/analysis/trend.py`, `src/analysis/__init__.py`**

**Files:** `src/analysis/trend.py`, `src/analysis/__init__.py`

- `TrendState` frozen dataclass: `trend` (`"bullish"` / `"bearish"` / `"neutral"` / `"unknown"`), `strength` (`"strong"` / `"weak"` / `"unknown"`), `volatility_regime` (`"high"` / `"low"` / `"normal"` / `"unknown"`), `fast_ema`, `slow_ema`, `atr`, `reason_codes`, safety fields. `"unknown"` means validation failed or data was insufficient; `"neutral"` means indicators were successfully computed and the EMA relationship is non-directional.
- `classify_trend(bars, *, symbol, timeframe, fast_ema_period=20, slow_ema_period=50, atr_period=14, volatility_lookback=50) → TrendState` using EMA crossover and ATR-ratio volatility regime.
- Validation gates: INVALID_SYMBOL → INVALID_TIMEFRAME → INVALID_PERIOD → INVALID_PERIOD_ORDER → MISSING_REQUIRED_COLUMNS → INSUFFICIENT_BARS.
- Pure function; no broker calls; no network; no credentials; no environment variable access.
- 106 unit tests in `tests/test_trend_analysis.py`.

### PR 4 — TrendFollowing strategy

**Status: implemented — `src/strategy/trend_following.py`, `src/strategy/factory.py` updated**

**Files:** `src/strategy/trend_following.py` (new), `src/strategy/factory.py` (updated),
`tests/test_trend_following_strategy.py` (new), `tests/test_strategy_factory.py` (updated)

**Implementation:**
- `TrendFollowing(BaseStrategy)` — MVP strategy module. Long-only; offline/deterministic.
- Uses `classify_trend()` (EMA trend filter) and `rolling_high(exclude_current=True)` (prior-bar breakout).
- Entry: trend == "bullish" AND close > prior rolling-high over `breakout_lookback` bars.
- Exit: trend == "bearish" OR close < fast EMA.
- ATR stop metadata in `Signal.meta` and `Signal.stop_loss`; no broker execution.
- `signal.meta` includes: `deterministic=True`, `broker_calls_made=False`,
  `credentials_read=False`, `order_action_requested=False`, `recommendation_only=True`.
- Factory updated: `build_strategy("trend_following", params)` returns `TrendFollowing`.
- ORB remains preserved as legacy/benchmark — behavior unchanged.
- No modification to `src/main.py`, `src/tools/`, `src/execution/`, or any config.
- No paper trading, live trading, or scheduler implemented.
- No automated trading approved.
- 87 unit tests (`tests/test_trend_following_strategy.py`); 57 factory tests
  (`tests/test_strategy_factory.py`); full suite 4 497 passed.

### PR 5 — Risk position sizing helper

**Status: implemented — `src/risk/position_sizer.py`**

**Files:** `src/risk/position_sizer.py` (new), `tests/test_position_sizer.py` (new),
`src/risk/__init__.py` (circular import fix — eager `RiskManager` export removed; no consumer used it)

**Implementation:**
- `calculate_shares_by_risk(equity, risk_pct, entry_price, stop_price, *, max_notional=None) → int`
  — returns whole shares from fractional equity risk and stop distance.
  — formula: `risk_amount = equity * risk_pct / 100`, `per_share_risk = entry_price - stop_price`,
    `shares = floor(risk_amount / per_share_risk)`.
  — optional `max_notional` hard cap: `shares = min(shares, floor(max_notional / entry_price))`.
  — always returns `>= 0`; sub-1 result returns 0 (trade not sized).
- `calculate_notional(shares, entry_price) → float` — dollar value of a position.
- All invalid inputs raise `ValueError("invalid position sizing parameters")`.
  Raw values are never echoed in the message.
- Pure function; no broker calls; no network; no credentials; no environment variable access.
- 79 unit tests in `tests/test_position_sizer.py`; full suite 4 576 passed.

### PR 6 — Metrics annualisation fix

**Status: implemented — `src/backtest/metrics.py` updated, `src/backtest/engine.py` updated**

**Files:** `src/backtest/metrics.py` (updated), `src/backtest/engine.py` (updated),
`tests/test_backtest_metrics.py` (new)

**Implementation:**
- `bars_per_year_for_interval(interval: str) → int` — pure helper, US equity regular-session
  assumptions (252 trading days, 6.5 h/day, 390 min/day). For hourly intervals, only complete
  bars within the session are counted (1h → 6/day; 2h → 3/day; 4h → 1/day).
  Unknown interval raises `ValueError("invalid interval")`; raw value never echoed.
- `compute_metrics(...)` gains `interval: str = "5m"` parameter. Sharpe ratio now uses
  `bars_per_year_for_interval(interval)` instead of the hardcoded `252 * 78` constant.
  Default `"5m"` preserves all existing callers unchanged.
- `BacktestEngine.run()` now passes `interval=self._bar_interval` to `compute_metrics`.
  1h and 1d backtests no longer silently use the 5m annualisation factor.
- `total_return_pct`, `max_drawdown_pct`, `annualized_return_pct` (CAGR, calendar-based),
  and all trade statistics are unchanged — they are not bar-interval-dependent.
- No backtest execution behaviour changed. No strategy behaviour changed.
- No broker/API access. No live/paper trading. No environment variable access.

| Interval | Bars/year | Calculation | Note |
|----------|-----------|-------------|------|
| `"1m"` | 98 280 | 252 × 390 | |
| `"2m"` | 49 140 | 252 × 195 | Yahoo-supported |
| `"5m"` | 19 656 | 252 × 78 | |
| `"15m"` | 6 552 | 252 × 26 | |
| `"30m"` | 3 276 | 252 × 13 | |
| `"60m"` | 1 512 | 252 × 6 | Yahoo alias for `"1h"` |
| `"1h"` | 1 512 | 252 × 6 (6 complete bars/day) | |
| `"90m"` | 1 008 | 252 × 4 | Yahoo-supported; floor(390/90)=4 |
| `"2h"` | 756 | 252 × 3 | |
| `"4h"` | 252 | 252 × 1 | |
| `"1d"` | 252 | 252 | |

- 41 unit tests in `tests/test_backtest_metrics.py`; full suite 4 617 passed.

### PR 7 — Backtest runner integration

**Status: implemented — `src/backtest/backtest_runner.py` (new)**

**Files:** `src/backtest/backtest_runner.py` (new), `tests/test_backtest_runner.py` (new)

**Implementation:**
- `BacktestRunConfig` — frozen dataclass: `strategy_name`, `strategy_params`, `symbols`,
  `start_date`, `end_date`, `bar_interval="5m"`, `initial_capital=100_000.0`,
  `position_size_pct=0.95`, `stop_execution="bar_close"`.
- `BacktestRunResult` — frozen dataclass: `metrics`, `trades`, `equity_curve`,
  `order_intents`, `strategy_name`, `symbols`, `bar_interval`, plus six read-only
  safety flags: `broker_calls_made=False`, `credentials_read=False`,
  `live_submit_enabled=False`, `order_action_requested=False`,
  `paper_trading_enabled=False`, `recommendation_only=True`.
- `run_backtest(config, *, data_provider)` — validates config (raises
  `ValueError("invalid backtest run config")` without echoing raw values); calls
  `build_strategy()`, constructs `Portfolio` + `RiskManager`, wires into
  `BacktestEngine`, calls `engine.run()`, wraps output into `BacktestRunResult`.
- No broker calls, no network access, no credentials, no environment variables,
  no live/paper trading, no file writes.
- `_validate_config` rejects non-finite `initial_capital` (``math.isfinite()``); rejects
  symbols that don't match ``^[A-Z0-9.\-/]{1,10}$`` (uppercase ticker regex); never
  echoes raw values in error messages.
- 67 unit tests in `tests/test_backtest_runner.py`; full suite 4 684 passed.

### PR 8 — Slim `main.py`

**Status: designed — `docs/main_dispatcher_slimdown_design.md`**

**File:** `src/main.py` (refactor existing) — implementation split into sub-PRs 8A–8E

- `main.py` becomes a dispatcher only: parse mode, delegate to runner.
- Backtest mode (`backtest`, `candidate-b`) routes through `run_backtest()` in
  `src/backtest/backtest_runner.py`; `build_engine()` in `main.py` is removed.
- `sweep` and `walk-forward` continue to delegate to `SweepRunner` / `WalkForwardRunner`.
- `paper` mode remains explicitly gated (unchanged gate logic).
- `live` mode becomes an explicit `NotImplementedError` placeholder — not reachable by default.
- All existing CLI behaviour preserved; no test regressions.
- Sub-PRs: 8A (CLI characterization tests ✓ implemented) → 8B (route backtest through runner ✓ implemented) →
  8C (remove `build_engine` ✓ implemented) → 8D (paper/live placeholders) → 8E (README update).
- 8A: 42 tests in `tests/test_main_characterization.py`; full suite 4 726 passed.
- 8B: 43 tests in `tests/test_main_characterization.py` (+1); 94 tests in `tests/test_backtest_runner.py` (+27 field-validation); full suite 4 754 passed.
- 8C: 43 tests in `tests/test_main_characterization.py` (unchanged count; `test_build_engine_is_callable` renamed to `test_build_engine_is_not_present`); `TestBuildEngineWiring` removed from `tests/test_backtest.py` (−2); paper-path tests updated to patch `run_backtest`; full suite 4 752 passed.
- 8D: 45 tests in `tests/test_main_characterization.py` (+2 source-scan tests); stale live-mode TODO removed from `src/main.py`; paper gate message clarified; `--mode live` remains argparse-rejected; full suite 4 754 passed.
- 8E: `README.md` fully rewritten — current project structure, CLI modes, strategies (ORB + TrendFollowing), strategy factory, dispatcher architecture, pytest commands, safety table, disabled modes explicitly documented. Docs-only; no src/tests/config/output changes.

### PR 9 — Tools / scripts isolation

**Status: designed — `docs/tools_scripts_isolation_design.md`**

**Files:** audit `src/tools/`; move non-core manual scripts to `scripts/` if safe.

- Identify which `src/tools/` scripts are imported by tests.
- Scripts imported by tests must not move without updating imports.
- Only move scripts that have zero test imports and are not part of live-readiness gate.
- No deletion without full test coverage of moved functionality.
- All live-readiness and safety tools remain in place.

**Final classification (as of PR 9F):**
- 30 live safety/readiness tools — **permanent in `src/tools/`**
- 4 manual live/paper guard tools — **permanent in `src/tools/`**
- 6 paper diagnostic utilities — **remain in `src/tools/`** (PR 9D deferred; see design doc)
- All 40 tools have test coverage; no tool has zero tests.
- `scripts/` created (PR 9C); documented; currently empty of `.py` files.

**Sub-PRs:**
- 9A: Design (this doc) ✓ designed
- 9B: Inventory tests for `src/tools/` ✓ implemented — `tests/test_tools_inventory.py` (363 tests)
- 9C: `scripts/` directory + classification README ✓ implemented — `scripts/README.md`
- 9D: Move paper diagnostic utilities — **deferred** (import/CLI risk > benefit; layout stable)
- 9E: Confirm live-readiness tools stay in `src/tools/` ✓ implemented — `TestPermanentToolsLocation` (76 tests)
- 9F: Finalize isolation docs ✓ implemented — docs-only

### PR 10 — README update

**File:** `README.md`

- Document new architecture and module layout.
- Show trend-following backtest usage example.
- Document how to run tests.
- State live trading is disabled by default.
- Document paper/live opt-in requirement.
- No code changes.

---

## 6. Near-Term MVP Definition

| Dimension | Value |
|-----------|-------|
| Symbols | SPY initially; QQQ later via config |
| Direction | Long only |
| Timeframe | 1h bars (intraday) |
| Strategy | Trend-following (EMA filter + rolling breakout) |
| Execution path | Backtest first → paper trading second → live automation much later |
| Options | Not in scope |
| Leverage | Not in scope |
| Short selling | Not in scope |
| Intra-minute trading | Not in scope |
| ML live execution | Not in scope |
| Multi-symbol portfolio | Not in initial MVP |

---

## 7. Safety Rules

The following rules apply to every PR in this refactor plan and to all future
code in this repository.

| Rule | Detail |
|------|--------|
| No live trading by default | Live mode is a disabled placeholder until Phases C–G are complete |
| No API keys stored | Credentials are read only after all gates pass and explicit flag is set |
| No real broker calls in tests | All tests use `FakeBroker` or injected mocks |
| Paper/live opt-in only | Both paper and live execution require explicit CLI flags and artifact gates |
| Fail-closed on uncertainty | BLOCKED is the default; PASS requires all gates to explicitly pass |
| No broker mutation outside execution layer | Strategy, risk manager, indicators, and analysis layers cannot call broker |
| Strategy cannot call broker | Strategy returns a signal only; execution is a separate layer |
| Strategy cannot bypass risk gate | Every signal must pass through the risk gate before the executor is called |
| Backtest result does not approve live trading | A positive backtest is evidence only; live trading requires completing Phases C–G |

---

## 8. No-Look-Ahead Requirements

Look-ahead bias produces inflated backtest results and invalidates any
comparison to live performance. The following requirements apply to all
new and existing code.

| Requirement | Detail |
|-------------|--------|
| Strategy receives only historical bars | `bars` passed to `generate_signal` or `evaluate_signal` must contain only bars up to and including the current bar index |
| Rolling breakout excludes current bar | `rolling_high(series, window)` at index `i` must use `series[i-window:i]`, not `series[i-window:i+1]` |
| Indicators must not use future bars | Any shift, rolling, or ewm operation must not introduce future data |
| Tests must cover no-look-ahead | Each indicator and strategy must have at least one test asserting the current bar is not included in the look-back window |
| Backtest engine design preserved | `src/backtest/engine.py` passes only the current-bar slice to the strategy; this must not be changed |

---

## 9. Relationship to Existing Phase A / Phase B

| Item | Relationship |
|------|-------------|
| Phase A `signal_engine.py` | Remains valid as an offline signal foundation; `TrendFollowing` strategy (PR 4) can be a separate strategy, not a replacement |
| Phase B backtest design | Remains valid; `offline_backtest_engine.py` is the Phase B deliverable; `backtest_runner.py` (PR 7) is the integration layer |
| This refactor plan | Complements Phase B, does not replace it; Phase B implementation comes before PR 7 |
| Safety roadmap (Phases A–H) | Unchanged; the refactor aligns architecture but does not skip any phase |

### Sequencing

The next code PRs should be, in order:

1. PR 1 (strategy factory) — small, low-risk, immediately useful
2. PR 2 (indicators) — required for PR 3 and PR 4
3. Phase B implementation (`offline_backtest_engine.py`) — can proceed in parallel with PR 3
4. PR 3 (trend analysis) — depends on PR 2
5. PR 4 (TrendFollowing strategy) — depends on PR 2 and PR 3

A giant `main.py` rewrite (PR 8) must not be the first step.

---

## 10. Explicit Non-Goals for This PR

| Non-goal | Status |
|----------|--------|
| Code changes of any kind | Out of scope for this PR |
| File moves | Out of scope for this PR |
| `main.py` refactor | Out of scope for this PR — PR 8 |
| Tools migration | Out of scope for this PR — PR 9 |
| Live execution | Out of scope |
| Paper execution | Out of scope |
| Broker calls | Out of scope |
| Credential reads | Out of scope |
| Trading of any kind | Out of scope |
| Test changes | Out of scope for this PR |

---

## References

- `src/strategy/signal_engine.py` — Phase A signal engine
- `src/backtest/engine.py` — existing bar-by-bar backtest engine
- `docs/automated_strategy_execution_roadmap.md` — Phase A–H safety roadmap
- `docs/backtest_and_metrics_offline_design.md` — Phase B design
- `docs/strategy_signal_engine_offline_core_complete_snapshot.md` — Phase A snapshot

---

## Suggested Git Tag

```
trend-bot-architecture-refactor-plan-designed
```

---

## Warnings

> **This document does not implement any code.**
> **This document does not approve automated live trading.**
> **This document does not approve any individual trade.**
> **No Alpaca endpoint is contacted.**
> **No credentials are read.**
> All refactor PRs must be individually reviewed before merging.
> Each phase of the safety roadmap (A–H) remains required.
> A positive backtest result does not approve live trading.
> Until automation is fully implemented, tested, and approved through
> the Phase A–H roadmap, all trading decisions remain entirely manual.

> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility.
