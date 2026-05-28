# Main Dispatcher Slimdown Design

Design document for PR 8 of the trend-bot architecture refactor:
slim `src/main.py` into a thin dispatcher.

**No code is implemented in this document.**
**No files are moved in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live record is written.**
**No paper trading is implemented.**
**No live trading is implemented.**
**No automated trading is approved.**
**This document plans the refactor only — each sub-PR requires its own review.**

---

## 1. Current Problem

`src/main.py` is 903 lines and mixes concerns that belong in separate layers:

| Concern | Current location | Where it belongs |
|---------|-----------------|-----------------|
| CLI argument parsing | `main.py` | `main.py` (keep here) |
| Config loading | `main.py` | `main.py` (keep here) |
| `BacktestEngine` construction | `main.py` (`build_engine`) | `backtest_runner.py` (already exists) |
| `Portfolio` / `RiskManager` wiring | `main.py` (`build_engine`) | `backtest_runner.py` (already exists) |
| Strategy construction | `main.py` (`build_engine`) | `backtest_runner.py` + `factory.py` |
| Equity curve plotting | `main.py` | `backtest_runner` or reporting layer |
| Report generation | `main.py` | `main.py` via `ReportGenerator` (acceptable) |
| Sweep / walk-forward execution | `main.py` (inline imports) | `main.py` dispatch only (inline ok for now) |
| Paper-close flow | `main.py` (`_run_paper_close`) | `main.py` gated section (keep, with explicit gate) |
| Live mode | Not implemented — TODO comment | Remains disabled / fail-closed placeholder |

### Specific issues

1. **Duplicate backtest wiring** — `build_engine()` in `main.py` constructs the same
   `Portfolio`, `RiskManager`, and `BacktestEngine` that `run_backtest()` in
   `src/backtest/backtest_runner.py` now handles.  Two call sites for the same flow
   means config drift is possible.

2. **Modes scattered** — The `--mode` routing is spread across 60+ lines in `main()` with
   no single dispatch table.  Adding a new mode requires editing multiple `if` blocks.

3. **No live/paper guard at parse time** — `--mode` currently accepts
   `["backtest", "candidate-b", "sweep", "walk-forward"]`.  A future `--mode live` or
   `--mode paper` can only be gated inside `main()` after parsing; there is no structural
   barrier.

4. **Private field access** — Line 851: `engine._portfolio.positions` accesses a private
   field.  When backtest wiring moves into `backtest_runner`, this can be replaced with
   public result fields.

---

## 2. Target Architecture

After PR 8, `main.py` contains **only**:

```
parse_args()
load_config()
dispatch(mode) →
    "backtest"      → run_backtest(config, data_provider=...)  ← backtest_runner.py
    "candidate-b"   → apply_candidate_b(cfg) + run_backtest(...)
    "sweep"         → SweepRunner(...)
    "walk-forward"  → WalkForwardRunner(...)
    "paper"         → _run_paper_close(...)  [explicit gate; fail-closed by default]
    "live"          → raise NotImplementedError("live trading is not enabled")
```

`main.py` becomes a **dispatcher**: it reads CLI flags, calls the appropriate module,
and writes outputs via `ReportGenerator`.  It does not construct `BacktestEngine`,
`Portfolio`, or `RiskManager` directly.

### Module responsibilities after PR 8

| Module | Responsibility |
|--------|---------------|
| `src/main.py` | CLI, config loading, dispatch, output wiring |
| `src/backtest/backtest_runner.py` | `BacktestRunConfig`, `run_backtest()` |
| `src/backtest/engine.py` | Bar-by-bar simulation (unchanged) |
| `src/strategy/factory.py` | Strategy construction (unchanged) |
| `src/experiments/sweep_runner.py` | Parameter sweep (called by main, unchanged) |
| `src/experiments/walk_forward_runner.py` | Walk-forward (called by main, unchanged) |

---

## 3. Proposed Modes

### 3a. `backtest` (default)

```
python -m src.main --mode backtest
python -m src.main                  # default
```

**After PR 8:** delegates to `run_backtest(BacktestRunConfig(...), data_provider=...)`.
Result is a `BacktestRunResult`; `main.py` passes it to `ReportGenerator`.

`build_engine()` in `main.py` is **removed** once `backtest_runner` is the sole wiring
point.

### 3b. `candidate-b`

```
python -m src.main --mode candidate-b
```

Applies `CANDIDATE_B_OVERRIDES`, then delegates to the same `run_backtest()` path.
Behaviour is identical; only the config differs.

### 3c. `sweep`

```
python -m src.main --mode sweep
```

Calls `SweepRunner` as today.  No change in PR 8 beyond ensuring dispatch is clean.
`SweepRunner` internally constructs its own engines; this is acceptable for now.

### 3d. `walk-forward`

```
python -m src.main --mode walk-forward
```

Calls `WalkForwardRunner` as today.  Same as sweep: dispatch only; internals unchanged.

### 3e. `paper` (gated)

```
python -m src.main --mode paper
```

`_run_paper_close` remains in `main.py` behind an explicit gate.  The gate currently
requires `execution.mode == "paper"` in config **and** a human-readable
`paper_selected_close_client_order_id`.  This gate is **not relaxed** in PR 8.

Paper mode is not reachable by default.  No automated paper trading is enabled.

### 3f. `live` (disabled placeholder)

```
python -m src.main --mode live
```

`--mode live` is **not in the current `choices` list**.  PR 8 explicitly adds it as a
rejected placeholder so the error message is clear:

```python
if args.mode == "live":
    raise NotImplementedError(
        "Live trading is not enabled in this build. "
        "See docs/live_readiness_status.md."
    )
```

This is the **only** change to the live path: making the rejection explicit rather than
relying on argparse rejecting an unknown choice.

No Alpaca calls.  No credentials.  No order submission.

---

## 4. Sub-PR Implementation Plan

Each sub-PR is independently reviewable and must not reduce the passing test count.

### PR 8A — CLI parser tests for current `main.py` behaviour

**Status: implemented — `tests/test_main_characterization.py`**

- `tests/test_main_characterization.py` added: 42 characterization tests across 6 classes
  (`TestMainImport`, `TestParseArgs`, `TestCandidateBOverrides`, `TestApplyCandidateB`,
  `TestMainPaperGate`, `TestMainModeDispatch`, `TestSourceCharacterization`).
- Covers: import safety; all 4 current modes accepted; `--mode live` and `--mode paper`
  rejected; default mode/output-dir/config; `apply_candidate_b` constants and no-mutation;
  paper gate raises `NotImplementedError` when disabled; backtest dispatch calls
  `build_engine`; candidate-b applies overrides before engine build; sweep/walk-forward
  route to their respective runners; Alpaca import not at module top level.
- No `src/main.py` changes.  No broker/API/credentials/live/paper trading.
- Full suite: 4 726 passed.

### PR 8B — Route `backtest` + `candidate-b` modes through `backtest_runner`

**Status: implemented — `src/main.py`, `src/backtest/backtest_runner.py`,
`tests/test_main_characterization.py`, `tests/test_paper_trading_readiness.py`**

- `BacktestRunConfig` extended with 6 new optional fields (commission, slippage,
  force_exit_time, max_open_positions, daily_loss_limit_pct, daily_loss_action)
  to preserve full behavioral equivalence with `build_engine()`.
- `run_backtest()` updated to pass new fields to `Portfolio` and `RiskManager`.
- `src/main.py` backtest dispatch block replaced: `build_engine()` call removed;
  `BacktestRunConfig` built from `AppConfig`; `run_backtest()` called instead.
- `ReportGenerator` receives `BacktestRunResult` fields; `open_positions_count=0`
  (always correct: engine closes all positions before returning).
- `engine._portfolio.positions` private-field access eliminated.
- `build_engine()` remains in `main.py` (unused by backtest dispatch; removed in PR 8C).
- `src/backtest/backtest_runner.py`: `_validate_config()` extended to validate all 6
  new fields (commission/slippage finite ≥ 0; force_exit_time HH:MM 00:00–23:59;
  max_open_positions None or int ≥ 1; daily_loss_limit_pct None or finite > 0;
  daily_loss_action in {"block_new_entries","close_all"}).  Raw values never echoed.
- `tests/test_backtest_runner.py`: 27 new tests in `TestNewFieldValidation`; 94 total.
- `tests/test_main_characterization.py`: `test_backtest_mode_calls_build_engine` →
  `test_backtest_mode_calls_run_backtest`; `test_candidate_b_mode_applies_overrides_before_engine` →
  `test_candidate_b_mode_applies_overrides_before_run_backtest`; new
  `test_backtest_run_config_core_fields` added.  43 tests.
- `tests/test_paper_trading_readiness.py`: startup-log helpers updated to abort at
  `run_backtest` instead of `build_engine`.  No assertion changes.
- Full suite: **4 754 passed**.
- No `src/backtest/engine.py` changes.  No broker/API/credentials/live/paper trading.

### PR 8C — Remove `build_engine()` from `main.py`

**Status: implemented — `src/main.py`, `tests/test_main_characterization.py`,
`tests/test_backtest.py`, `tests/test_alpaca_broker_skeleton.py`, and paper-path test files**

- `build_engine()` deleted from `src/main.py`.
- `from src.portfolio.portfolio import Portfolio` removed — was only needed by `build_engine`.
- `from src.risk.risk_manager import RiskManager` removed — was only needed by `build_engine`.
- `from src.strategy.opening_range_breakout import OpeningRangeBreakout` removed — was only needed by `build_engine`.
- `from src.backtest.engine import BacktestEngine` retained — still used by `BacktestEngine.plot_equity_curve()` in the backtest dispatch block.
- `tests/test_main_characterization.py`: `test_build_engine_is_callable` renamed to `test_build_engine_is_not_present`; count unchanged at 43.
- `tests/test_backtest.py`: `TestBuildEngineWiring` class (2 tests) and its `_make_app_config` helper removed; no longer needed.
- `tests/test_alpaca_broker_skeleton.py` and paper-path test files: all `mock.patch("src.main.build_engine", ...)` calls replaced with `mock.patch("src.backtest.backtest_runner.run_backtest", ...)`.
- Full suite: **4 752 passed**.
- No `src/backtest/engine.py` changes. No broker/API/credentials/live/paper trading.

### PR 8D — Paper/live fail-closed placeholders

**Status: implemented — `src/main.py`, `tests/test_main_characterization.py`**

- Stale `TODO (Alpaca integration)` comment removed from module docstring; replaced with an
  accurate note: `--mode live` and `--mode paper` are not valid CLI options; paper execution
  is gated via config; live trading is not enabled.
- Paper gate error message updated: removed the phrase "Paper order execution is not yet
  wired"; now reads "Paper trading is disabled (execution.paper_trading_enabled is false).
  Set execution.paper_trading_enabled to true in config to reach the paper execution gate."
- `--mode live` remains argparse-rejected (not added to `choices`) — current behavior preserved.
- Two source-scan tests added to `TestSourceCharacterization`:
  `test_no_stale_live_mode_todo`, `test_live_not_in_cli_choices`.
- `tests/test_main_characterization.py` — 45 tests (+2).
- Full suite: **4 754 passed**.
- No Alpaca calls, no credentials, no order submission. Paper gate logic unchanged.

### PR 8E — README usage update

**Goal:** Update `README.md` to reflect the new CLI and module layout.

- Add a usage section showing `--mode backtest`, `--mode sweep`, etc.
- Explicitly document that `--mode live` is disabled and how to check readiness.
- No code changes.

---

## 5. Safety Guarantees

The following guarantees apply across all PR 8 sub-PRs:

| Guarantee | How enforced |
|-----------|-------------|
| No live trading approved | `--mode live` raises `NotImplementedError`; no Alpaca calls in main dispatch path |
| No Alpaca SDK imported in dispatch path | `BacktestEngine` / `backtest_runner` do not import Alpaca; `main.py` does not import Alpaca |
| No credentials read in backtest path | `run_backtest()` and `BacktestRunConfig` have no credential fields |
| No order submission in backtest path | `BacktestRunResult.broker_calls_made` is always `False` |
| Paper gate unchanged | `_run_paper_close` gate logic is not relaxed |
| No behavior change in this docs PR | This document describes; no `src/`, `tests/`, `output/`, or `config/` files are modified |
| Test suite cannot regress | PR 8A locks in CLI regression tests before any code moves |

---

## 6. Files Changed Per Sub-PR

| Sub-PR | Files added | Files modified | Files deleted |
|--------|------------|----------------|---------------|
| 8A | `tests/test_main_cli.py` | — | — |
| 8B | — | `src/main.py`, `src/reporting/report_generator.py` (if needed) | — |
| 8C | — | `src/main.py` | — |
| 8D | — | `src/main.py` | — |
| 8E | — | `README.md` | — |

`src/backtest/backtest_runner.py`, `src/backtest/engine.py`, `src/strategy/factory.py`,
`src/portfolio/portfolio.py`, `src/risk/risk_manager.py` are **not modified** in any
PR 8 sub-PR.

---

## 7. Validation

For this **docs-only** PR:

```bash
git diff origin/main...HEAD -- src tests output config
# Expected: empty (no src/tests/output/config changes)
```

For each implementation sub-PR:

```bash
python -m pytest          # must not reduce passing count
python -m src.main        # must produce identical output to pre-PR baseline
python -m src.main --mode sweep   # must complete without error
```

---

## 8. What This Design Does Not Approve

- **No live trading.** Not implemented, not enabled, not approved.
- **No Alpaca API calls.** No endpoint is contacted in any mode after this refactor.
- **No credentials.** No API key, secret, or token is read by the dispatcher.
- **No automated paper trading.** Paper mode remains gated behind explicit human-set config fields.
- **No order submission.** `BacktestRunResult.broker_calls_made` is always `False`.
- **No behavior change.** This is a docs-only PR. All behavior changes happen in 8A–8E.

Nothing in this document or this repository constitutes financial advice.
