# Automated Trading Architecture Readiness Snapshot

Point-in-time snapshot of the trading bot architecture after completion of
the trend-bot architecture refactor (PRs 1–9) and the tools/scripts isolation
(PR 9A–9F).

**Date:** 2026-05-28
**Test baseline:** 5 193 passed (fully offline; no real broker calls in any test)
**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**This document records current state; it does not approve any future action.**

---

## 1. Project Goal

An automated, rule-based trading bot for 1-hour to 1-day intraday strategy
execution on US equities and ETFs (initially SPY and QQQ, long-only).

The bot mechanically executes predefined trading strategies without emotional
decision-making, while preserving all safety gates, kill-switch behaviors, and
operator review checkpoints established in the live-readiness infrastructure.

**This goal is not yet reached.** The current codebase provides a complete
backtesting framework and safety foundation. Live and paper automation require
completing the staged Phase A–H roadmap (see
`docs/automated_strategy_execution_roadmap.md`).

---

## 2. Current Implementation Status

### 2.1 Completed (merged to `main`)

| Component | Location | Status |
|-----------|----------|--------|
| Indicators package | `src/indicators/` | **Complete** — `sma()`, `ema()`, `atr()`, rolling high/low, breakout |
| Trend analysis layer | `src/analysis/trend.py` | **Complete** — `classify_trend()` → `TrendState` |
| Strategy factory | `src/strategy/factory.py` | **Complete** — selects strategy by name; supports `"opening_range_breakout"`, `"orb"`, `"trend_following"` |
| TrendFollowing strategy (offline) | `src/strategy/trend_following.py` | **Complete** — EMA crossover filter + rolling breakout entry/exit; no broker calls |
| OpeningRangeBreakout strategy (legacy/benchmark) | `src/strategy/opening_range_breakout.py` | **Complete** |
| Phase A signal engine (offline) | `src/strategy/signal_engine.py` | **Complete** — deterministic offline signal generation |
| Risk / position sizing helper | `src/risk/position_sizer.py` | **Complete** — `calculate_shares_by_risk()` |
| Risk manager (pre-entry gates + exit rules) | `src/risk/risk_manager.py` | **Complete** |
| Portfolio tracker | `src/portfolio/portfolio.py` | **Complete** — cash, positions, equity curve |
| Interval-aware metrics | `src/backtest/metrics.py` | **Complete** — Sharpe annualisation uses `bars_per_year_for_interval()` |
| Backtest engine | `src/backtest/engine.py` | **Complete** — bar-by-bar event loop; no look-ahead |
| `BacktestRunConfig` + `run_backtest()` | `src/backtest/backtest_runner.py` | **Complete** — sole wiring point for backtest dispatch |
| `main.py` slim dispatcher | `src/main.py` | **Complete** — thin CLI dispatcher; `build_engine()` removed |
| Report generator | `src/reporting/report_generator.py` | **Complete** — CSV + JSON artifacts |
| Paper execution layer (gated) | `src/execution/paper_*.py` | **Complete** — fail-closed; requires explicit config opt-in |
| Live execution adapter (gated) | `src/execution/alpaca_broker.py` | **Complete** — gated behind live approval artifacts |
| Live-readiness tools | `src/tools/live_*.py` (30 tools) | **Complete** — offline/read-only safety pipeline |
| Manual guard tools | `src/tools/manual_*.py` + `live_single_*` (4 tools) | **Complete** |
| Paper diagnostic tools | `src/tools/paper_*.py` + `replay_*.py` (6 tools) | **Complete** |
| `scripts/` directory | `scripts/README.md` | **Complete** — documented, empty of `.py` files |
| Tools inventory + location tests | `tests/test_tools_inventory.py` | **Complete** — 439 tests locking 30/4/6/40 classification |

### 2.2 Not Yet Implemented

| Component | Phase | Notes |
|-----------|-------|-------|
| Offline backtest scenarios for TrendFollowing | Pre-Phase C | SPY/QQQ baseline runs; ORB vs TrendFollowing comparison |
| Validated walk-forward / sweep on TrendFollowing | Pre-Phase C | Only after baseline backtests are stable |
| Automated paper execution scheduler | Phase C/F | Requires scheduler + state machine design |
| Automated risk gate (state machine) | Phase D | Full state machine with mock broker required first |
| Paper broker integration (automated) | Phase F | Requires Phase D + E completion |
| Live automation (limited notional) | Phase G | Requires documented Phase F evidence |
| Expanded live automation | Phase H | Requires documented Phase G evidence |

---

## 3. Current CLI Surface

```bash
# Backtest modes (offline — no broker calls)
python -m src.main                    # default: backtest (ORB)
python -m src.main --mode backtest    # explicit
python -m src.main --mode candidate-b # QQQ, 09:45 OR end, 50% size
python -m src.main --mode sweep       # parameter grid search
python -m src.main --mode walk-forward  # rolling walk-forward

# Live-readiness tools (offline/read-only)
python -m src.tools.live_readiness_gate --config ...
python -m src.tools.live_safety_status --config ...
# ... (30 live + 4 manual tools total)
```

### Disabled modes

| Mode | Status |
|------|--------|
| `--mode live` | Not a valid argparse choice; rejected at CLI parse time |
| `--mode paper` | Not a valid argparse choice; rejected at CLI parse time |
| Paper execution | Config-gated: `execution.paper_trading_enabled = true` required; fail-closed by default |
| Live execution | Not enabled; `live_trading_approved = false` in all approval artifacts |

---

## 4. Current Safety Status

| Guarantee | How enforced |
|-----------|-------------|
| No automated live trading | `--mode live` argparse-rejected; `live_trading_approved=false` in all artifacts; no order submission path in backtest dispatch |
| No automated paper trading | `paper_trading_enabled` must be `true` in config; fail-closed by default |
| No Alpaca SDK at import time | Alpaca imports are lazy (inside functions only); `tests/test_tools_inventory.py` AST-scans all 40 tools |
| No credentials required for offline use | Backtest and all offline modes require zero API keys |
| No order execution in backtest | `BacktestRunResult.broker_calls_made` is always `False` |
| No look-ahead bias | Engine passes only historical bars to strategy per bar |
| Live/paper tools fail-closed | BLOCKED is default; PASS requires all gates to explicitly pass |
| Safety tools locked in `src/tools/` | `TestPermanentToolsLocation` (76 tests) asserts 30+4 tools absent from `scripts/` |
| All 40 tools tested | `tests/test_tools_inventory.py` — 439 tests; no tool has zero coverage |

---

## 5. Current Test Baseline

| Suite | Count |
|-------|-------|
| Full suite (`python -m pytest`) | **5 193 passed** |
| Tools inventory + location | 439 (in `tests/test_tools_inventory.py`) |
| Backtest runner + config validation | 94 (in `tests/test_backtest_runner.py`) |
| Main CLI characterization | 45 (in `tests/test_main_characterization.py`) |
| All tests | Fully offline — no real broker calls in any test |

---

## 6. Next Phase Priorities

### Immediate (before any paper/live work)

1. **Offline strategy validation** — run backtests with TrendFollowing on SPY
   and QQQ using `python -m src.main --mode backtest` with
   `strategy.name = trend_following` in config. Collect metrics, equity curves,
   and trade logs.

2. **ORB vs TrendFollowing comparison** — run both strategies over the same
   date range; compare Sharpe, drawdown, win rate, and trade frequency.

3. **Stable baseline first** — only add `--mode walk-forward` or `--mode sweep`
   to TrendFollowing validation after the single-run backtest results are reviewed
   and documented.

### After baseline validation

4. **Phase C / D / E** — paper execution scheduler + state machine design; each
   requires its own PR with full safety review. See
   `docs/automated_strategy_execution_roadmap.md` for preconditions.

5. **Paper and live automation remain gated** — no paper or live automation may
   be implemented before Phases C–F are individually completed, reviewed, and
   approved. No timeline is committed here.

---

## 7. Architecture Summary Diagram

```
CLI (src/main.py)
    │
    ├── backtest / candidate-b
    │       └── run_backtest(BacktestRunConfig)
    │               ├── BacktestEngine (bar-by-bar, no look-ahead)
    │               │       └── Strategy (ORB | TrendFollowing)
    │               │               └── RiskManager → position sizing
    │               └── BacktestRunResult → ReportGenerator
    │
    ├── sweep         → SweepRunner
    ├── walk-forward  → WalkForwardRunner
    │
    ├── paper  [DISABLED — not a valid CLI choice]
    └── live   [DISABLED — not a valid CLI choice]

src/tools/  (40 tools — offline / read-only safety pipeline)
    ├── live_*.py    (30) — live safety / readiness gate — PERMANENT
    ├── live_single_* + manual_*.py (4) — manual guard — PERMANENT
    └── paper_*.py + replay_*.py (6) — paper diagnostics

scripts/    (empty of .py files — reserved for future non-core utilities)
```

---

## 8. Safety Guarantees for This Document

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | No `src/` code changed in this PR |
| No Alpaca SDK imported | No `src/` changes |
| No credentials read | No `src/` changes |
| No order submission | No `src/` changes |
| Paper gate unchanged | Paper tools untouched |
| Live gate unchanged | Live tools untouched |
| Test suite unchanged | No `tests/` changes |

---

Nothing in this document or this repository constitutes financial advice.
All trading decisions are made by the operator and are the operator's
sole responsibility.
