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

Before Phase B implementation begins, the repository underwent staged
architecture alignment for the trend-following MVP (PRs 1–9). This is
documented in `docs/trend_bot_architecture_refactor_plan.md` and
`docs/archive/snapshots/automated_trading_architecture_readiness_snapshot.md`.

**Architecture alignment is now complete (PR 9F).** The refactor covered:
strategy factory, indicators, trend analysis, TrendFollowing strategy,
position sizer, interval-aware metrics, backtest runner, slim main.py,
tools/scripts isolation, and README updates.  The Phase A–H safety roadmap
is unchanged. Live and paper trading remain not enabled.

### Phase B — Backtest and metrics

**Status: implemented — `src/backtest/backtest_runner.py`, `src/backtest/metrics.py`**

- Historical bar data ingestion from offline source (yfinance via `cached_provider`)
- `BacktestRunConfig` + `run_backtest()` — sole wiring point for backtest dispatch
- Metrics: interval-aware Sharpe, CAGR, max drawdown, win rate, trade count
- `BacktestRunResult` passed to `ReportGenerator` (CSV + JSON artifacts)
- No live execution; no real broker calls; `broker_calls_made` always `False`

**Next step within Phase B:** Run offline backtest scenarios for TrendFollowing
on SPY/QQQ; compare vs ORB baseline; document results before Phase C begins.
Scenario design: PR 10B (`docs/trendfollowing_offline_backtest_scenarios_design.md`).
Implementation: PR 10C (`tests/test_trendfollowing_offline_scenarios.py`, 72 tests,
synthetic in-test fixtures, no network access).
Real-data gate design: PR 10D (`docs/real_data_backtest_gate_design.md`) —
defines safe use of cached Yahoo data for offline validation; corrects 1h
retention limit to 730 days; specifies cache policy, operator runbook, and
two-tier test strategy (CI: synthetic; local: `@pytest.mark.integration`).
Cache availability checker: PR 10E (`src/tools/cached_data_availability_check.py`)
— offline read-only tool; scans `data/cache/` for SPY/QQQ × 1d/60m files; validates
OHLCV columns; 60m ↔ 1h aliasing; 42 tests; PASS/BLOCKED result; no network,
no broker, no credentials; `data/cache/` added to `.gitignore`.
Yahoo fetch gate design: PR 10F (`docs/yahoo_fetch_gate_design.md`) — defines
explicit `--allow-network` approval gate for Yahoo/yfinance data fetch; default
BLOCKED; scope SPY/QQQ × 1d/60m; conservative rate-limit and retry policy;
post-fetch validation via `cached_data_availability_check`; fail-closed; no raw
prices in output; PASS means cache populated only, not strategy/paper/live approval.
Yahoo fetch tool: PR 10G (`src/tools/yahoo_cache_fetch.py`) — guarded fetch
tool; default BLOCKED without `--allow-network`; fetches via YahooDataProvider
+ CachedMarketDataProvider; ≥1s between fetches; max 3 retries with exponential
backoff; post-fetch validation via `cached_data_availability_check`; no raw
prices in output; all tests use mocked provider (no live network in tests);
count 41 → 42 tools.
Local fetch runbook: PR 10H (`docs/local_yahoo_cache_fetch_runbook.md`) —
step-by-step operator runbook; confirm default BLOCKED; run fetch with
`--allow-network`; verify via `cached_data_availability_check`; failure
remediation; cache cleanup; PASS means cache populated only.
Real-data backtest checker: PR 10I (`src/tools/cached_real_data_backtest_check.py`) —
offline characterization tool; reads from data/cache/ only; no network; runs
run_backtest() with trend_following for SPY/QQQ × 1d/60m; reports metric
summaries (no raw prices); BLOCKED if cache missing; PASS means characterization
ran only; count 42 → 43 tools.
First real-data results snapshot: PR 10J (`docs/archive/snapshots/first_cached_real_data_backtest_results_snapshot.md`) —
docs-only; records first operator-run pipeline results (yahoo_cache_fetch PASS,
4 files written; cached_data_availability_check PASS; cached_real_data_backtest_check
PASS, 4 scenarios); captures scenario metrics (SPY/QQQ × 1d/60m); interpretation
(pipeline working; strategy performance not acceptable; QQQ 60m +0.34% total return
does not approve trading); diagnostic plan: PR 10K (Sharpe calculation), PR 10L
(trade summary diagnostics), PR 10M (default params comparison); no paper/live
approval; no src/tests/config/output/scripts/data changes.

### Phase B diagnostics (pending)

**PR 10K — Sharpe calculation diagnostic (daily scenarios) — implemented**
`src/backtest/metrics_diagnostics.py`: offline `diagnose_sharpe()` helper
recomputes Sharpe and detects zero/near-zero std (which explains extreme values
−134 to −163 seen in daily scenarios). Returns BLOCKED when std=0 rather than
emitting a misleading extreme value. 67 tests. No strategy/engine changes.

**PR 10L — Sharpe diagnostics integrated into cached_real_data_backtest_check — implemented**
`src/tools/cached_real_data_backtest_check.py`: after each `run_backtest()`,
calls `diagnose_sharpe()` and adds 5 per-scenario diagnostic fields
(`sharpe_diagnostic_result`, `zero_std_detected`, `low_variance_warning`,
`annualized_volatility`, `return_points`). Diagnostic BLOCKED does not block
the scenario. 8 new tests (`TestSharpeDiagnostics`). No strategy/engine/metrics changes.

**PR 10M — Default params comparison — implemented**
`tests/test_trendfollowing_param_comparison.py`: 29 tests lock in the divergence
between checker `fast_ema_period=10` (intentional — shorter EMA for broader signal
characterization) and strategy default `fast_ema_period=20`. All other params match.
Checker uses correct key names; no obsolete `ema_fast`/`ema_slow` keys. Both param
sets accepted without error. No parameter optimization. No code behavior changes.

**PR 10N — Calibrate Sharpe diagnostic low-vol threshold — implemented**
`src/backtest/metrics_diagnostics.py`: added `_LOW_ANNUALIZED_VOL_THRESHOLD = 0.001`
(0.1%). `low_variance_warning` now fires when `annualized_volatility < 0.001`, in
addition to the legacy per-bar std check. SPY/QQQ 1d cases (ann_vol ≈ 0.0003) now
correctly show `low_variance_warning=True`. 5 new tests (`TestAnnualizedVolThreshold`).
No strategy/engine/metrics changes.

**PR 10O — Calibrated diagnostics rerun snapshot — implemented**
`docs/archive/snapshots/calibrated_sharpe_diagnostics_real_data_snapshot.md`: docs-only. Confirms
PR 10N calibration: SPY/QQQ 1d `low_variance_warning=True`, 60m scenarios
`low_variance_warning=False`. No src/tests changes.

**PR 10P — Trade summary diagnostics design — implemented**
`docs/trade_summary_diagnostics_design.md`: docs-only. Defines aggregate trade
diagnostic fields (`trade_count`, `trades_per_100_bars`, `avg_holding_bars`,
`median/min/max_holding_bars`, `exposure_pct`, `entry/exit_count`,
`unmatched_entries/exits`, `win_rate`, `avg_trade_return`, `avg_win/loss`,
`profit_factor`, `exit_reason_counts`). Documents `Trade` schema and known
`exit_reason` values. Notes strategy EXIT signals not currently acted on by
engine. Safety constraints for pure/offline helper. Implementation: PR 10Q
(schema tests), PR 10R (helper), PR 10S (checker integration), PR 10T (snapshot).

**PR 10Q — Trade schema characterization tests — implemented**
`tests/test_backtest_trade_schema.py`: 60 tests across 5 classes lock in the
`Trade` schema (symbol, entry_time, exit_time, entry_price, exit_price, shares,
commission, direction, exit_reason, pnl, meta), the pnl computation in
`__post_init__`, the exclusion of meta from `to_dict()`, the 5-value exit_reason
allowlist (`stop_loss`, `force_exit`, `session_end`, `end_of_backtest`,
`daily_loss_limit`), `BacktestRunResult.trades` as list[Trade], and all safety
flags. Source scan confirms no forbidden imports in trade.py / backtest_runner.py.
No strategy, engine, metrics.py, or cached checker changes.

**PR 10R — `trade_summary_diagnostics` helper — implemented**
`src/backtest/trade_diagnostics.py`: pure offline `trade_summary_diagnostics(
trades, *, total_bars=None)` returning 19 aggregate fields (result, blocker,
trade_count, trades_per_100_bars, avg/median/min/max_holding_bars,
exposure_pct, entry/exit_count, unmatched_entries/exits, win_rate_pct,
avg_trade_return_pct, avg_win/loss_pct, profit_factor, exit_reason_counts)
plus 4 safety flags. BLOCKED on non-finite numeric values; PASS with zeros on
empty list. Holding-period in approximate hours; exposure = conservative lower
bound. BLOCKED on non-finite numeric field, `entry_price ≤ 0`, `shares ≤ 0`,
or `exit_time < entry_time`; same-bar trades valid; blocker strings contain
no raw values. 78 tests across 10+ classes. No strategy/engine/metrics/checker changes.

**PR 10S — trade diagnostics in cached checker — implemented**
`src/tools/cached_real_data_backtest_check.py`: after each successful
`run_backtest()`, calls `trade_summary_diagnostics(result_bt.trades,
total_bars=len(df))` and appends 18 fields per scenario (`trade_diagnostic_result`,
`trade_diagnostic_blocker`, `trades_per_100_bars`, `avg/median/min/max_holding_bars`,
`exposure_pct`, `entry/exit_count`, `unmatched_entries/exits`, `win_rate_pct`,
`avg_trade_return_pct`, `avg_win/loss_pct`, `profit_factor`,
`exit_reason_counts`). Diagnostic BLOCKED never blocks scenario or overall result.
Exception → safe fallback BLOCKED with Nones. No raw prices/trade records in output.
25 new tests (86 total in checker test file). No strategy/engine/metrics changes.

**PR 10T — trade diagnostics real-data snapshot — implemented**
`docs/archive/snapshots/trade_diagnostics_real_data_snapshot.md`: docs-only. Operator rerun of
`cached_real_data_backtest_check` confirmed PR 10S integration works end-to-end.
All 4 scenarios returned `trade_diagnostic_result=PASS`. Key finding: daily 1d
scenarios show `avg_holding_bars=0.0` with 279/280 (SPY) and 265/266 (QQQ)
`session_end` exits — same-bar exit artifact from daily bar timestamps at
midnight, which precede the 15:55 force-exit guard. This explains 0% win rate
and extreme Sharpe on daily scenarios. 60m scenarios have plausible structure
(median hold 5–6 h, stop_loss exits present, profit_factor ≈ 0.97–1.01). No
gate status changes. Next: PR 10U (daily-bar session_end policy design),
PR 10V (characterization tests), PR 10W (policy decision + fix).

**PR 10U — daily-bar session_end policy design — implemented**
`docs/daily_bar_session_end_policy_design.md`: docs-only. Four candidate policies
evaluated (A: disable intraday logic for daily bars; B: next-bar semantics;
C: block 1d + force_exit_time as invalid config; D: annotate results invalid).
Recommended: Phase 1 — Policy C block guard in `run_backtest()` or config
validation (no engine change, fail-closed, operator forced to choose); Phase 2 —
Policy A disable `session_end`/`force_exit` checks in engine for daily bars.
Acceptance criteria, safety implications, and PR chain (10V → 10W → 10X)
documented.

**PR 10V — daily-bar session_end / force_exit characterization tests — implemented**
`tests/test_daily_bar_session_end_behavior.py`: 62 characterization tests (10
classes) locking in current engine behavior before PR 10W changes. Covers: daily
bar midnight timestamps; `"00:00" < "15:55"` string comparison (force_exit never
fires); session_end fires on every daily bar pair; same-bar exit artifact
(`entry_time == exit_time`, `holding = 0.0`, 0% win rate); 60m intraday
timestamps; 60m session_end only at day boundaries; 60m non-zero holding; 1d vs
60m structural contrast; safety flags; source scan of engine/risk_manager/runner.
No `src/` changes. All 5 761 tests pass.

**PR 10W — Phase 1 Policy C block guard — implemented**
`src/backtest/backtest_runner.py`: fail-closed validation guard rejects
`bar_interval in {"1d","1day","daily"}` combined with `force_exit_time is not None`.
Raises `ValueError("invalid backtest run config")` — fixed string, no raw values
echoed. `force_exit_time: str | None`; `None` bypasses the guard via sentinel
`"23:59"` passed to `RiskManager`. NOTE: `force_exit_time=None` does not fix the
session_end same-bar artifact — daily 1d results remain not valid for strategy
performance until Phase 2 / Policy A. `cached_real_data_backtest_check.py`
unchanged — its 1d scenarios (which still pass `force_exit_time="15:55"`) now
return `BLOCKED`. Also updated: `test_backtest_trade_schema.py`,
`test_trendfollowing_offline_scenarios.py`, `test_trendfollowing_param_comparison.py`
(each had a synthetic 1d config with `force_exit_time="15:55"`; changed to `None`).
14 new guard tests in `test_backtest_runner.py`, 6 in
`test_daily_bar_session_end_behavior.py` (including one confirming artifact remains).
All 5 780 tests pass. Phase 2 (Policy A engine disable) deferred to a later PR.

**PR 10X — post-Phase-1 cached checker snapshot — implemented**
`docs/archive/snapshots/post_phase1_daily_guard_cached_checker_snapshot.md`: docs-only. Operator
rerun after PR 10W Phase 1 confirms guard works as intended. Overall result:
`BLOCKED`. SPY/1d and QQQ/1d: `BLOCKED` (Phase 1 guard fires). SPY/60m and
QQQ/60m: `OK`, unchanged from PR 10T (197 and 195 trades respectively). Daily
1d remains not valid for strategy performance until Phase 2 / Policy A.

**PR 10Y — 60m-only evaluation scope design — implemented**
`docs/real_data_60m_only_evaluation_scope_design.md`: docs-only. Defines the
authorized short-term evaluation scope after PR 10X: SPY/QQQ 60m only while
daily 1d remains BLOCKED. Documents metrics to evaluate (`total_return_pct`,
`max_drawdown_pct`, `sharpe_ratio`, `win_rate_pct`, `profit_factor`,
`exit_reason_counts`, `exposure_pct`, trade diagnostic fields), interpretation
constraints (backtest-only, no performance forecasts), acceptance gates for
diagnostic outputs, and future PR chain (10Z, 11A, 11B, Phase 2 separate track).
No parameter optimisation, no paper/live approval.

**PR 10Z — 60m-only cached checker runbook — implemented**
`docs/real_data_60m_only_cached_checker_runbook.md`: docs-only. Step-by-step
operator runbook for `cached_real_data_backtest_check --intervals 60m`. Covers:
pre-check availability, checker command with `--output` flag, expected
`result=PASS`, bash and PowerShell print commands for overall status / per-scenario
metrics / Sharpe diagnostics / trade diagnostics, interpretation rules
(PASS ≠ trading approval, metrics are backtest-only), failure handling table,
baseline values from PR 10T/10X. No `src/` changes.

**— 10-series runbook chain complete. No further 10-series runbook PRs. —**

### Phase R — Codebase simplification and automated-runtime alignment

The 10-series diagnostic chain (10U–10Z) is closed. The next work block is
Phase R: simplify the codebase toward the automated bot target by archiving
manual-only tools, extracting embedded execution paths, and building the
automated state machine skeleton.

**PR R1 — Codebase inventory and deletion plan — implemented**
`docs/automated_bot_codebase_inventory_deletion_plan.md`: docs-only. Classifies
all `src/` modules and tools into `KEEP_RUNTIME`, `KEEP_RESEARCH`,
`CONVERT_TO_RUNTIME`, `ARCHIVE_MANUAL`, `DELETE_CANDIDATE`, `FREEZE_DEFERRED`.
Identifies `src/main.py` paper execution / close paths as `CONVERT_TO_RUNTIME`.
Identifies ~17 manual-only tools in `src/tools/` as `ARCHIVE_MANUAL` or
`DELETE_CANDIDATE`. Defines Phase R PR chain (R2–R6) and Phase A2 automated
skeleton PRs (A2-1, A2-2, A2-3). Includes direction guard.

**PR R2 — Refactor tool inventory: active vs. archive classification — implemented**
`tests/test_tools_inventory.py` rewritten (384 tests). Replaces old 4-group
constant model with a 5-group cleanup-aware model:
`ACTIVE_RESEARCH_TOOLS` (3) + `ACTIVE_RUNTIME_CANDIDATE_TOOLS` (15) +
`ARCHIVE_MANUAL_TOOLS` (14) + `DELETE_CANDIDATE_TOOLS` (10) +
`PRESERVE_RUNTIME_SUPPORT_TOOLS` (1) = `ALL_TOOLS` (43).
`ACTIVE_TOOLS` = 19 (research + runtime candidates + preserve).
`main()` requirement now scoped to `ACTIVE_TOOLS` only.
Safety scans (Alpaca/env/mutation/secrets) still cover `ALL_TOOLS`.
`TestPermanentToolsLocation` removed; replaced by `TestCleanupEligibility`.
No tools moved or deleted — all 43 remain in `src/tools/`.
Full suite: 384 tests in `test_tools_inventory.py`.

**PR R3 — Archive superseded snapshot docs — implemented**
Created `docs/archive/snapshots/`. Moved 5 superseded snapshot docs (PR 10J, 10O, 10T, 10X,
and the PR 9F architecture readiness snapshot). Archive notes added to each file. Path
references updated in 7 active docs. No tests modified. Full suite: 5 701 passed.

**PR R4 — Archive manual tools and delete stubs — implemented**
First real codebase cleanup after R1/R2/R3. Dependency scan over all 43 tools determined
final classifications. `ACTIVE_TOOLS` grows 19 → 30 after 11 reclassifications forced by
active imports found in the scan.

- Archived 10 manual-operator tools to `scripts/archive/manual_live_readiness/`
  (archive header prepended; not importable as `src.tools.*`)
- Deleted 3 redundant tool stubs (`live_readiness_history_review`, `paper_ledger_import`,
  `paper_pre_submit_check`)
- Deleted 13 matching test files (1 113 tests removed)
- Updated `tests/conftest.py`: removed `"test_paper_ledger_import.py"` from
  `_LEDGER_TEST_FILES`
- Removed `test_report_consumable_by_blocked_review` from
  `tests/test_live_submit_executor_check.py` (imported now-archived tool)
- Rewrote `tests/test_tools_inventory.py` (310 tests, 5 classes):
  `ACTIVE_TOOLS`=30 (`ACTIVE_RESEARCH`=3 + `ACTIVE_RUNTIME_CANDIDATE`=26 + `PRESERVE`=1);
  `ARCHIVED_TOOLS`=10; `DELETED_TOOLS_R4`=3; `TestCleanupEligibility` →
  `TestArchiveIntegrity`; all scans / coverage / import-safety tests scoped to
  `ACTIVE_TOOLS` only.
Full suite: 4 513 passed.

**PR R4b — Document legacy active tool dependency reduction plan — implemented**
Docs-only. Adds `docs/legacy_active_tool_dependency_reduction_plan.md`. Documents the
11 legacy tools that PR R4 was forced to keep active due to old import chains, organises
them into 5 dependency clusters (A–E), and defines reduction PRs R4c–R4g. Direction
checkpoint: if `ACTIVE_TOOLS` is still above ~22–24 after R4g, do another cleanup pass
before R5. No `src/`, `tests/`, `scripts/`, or `config/` changes.

**PR R4c — Remove paper_smoke_check active dependency — implemented**
`tests/test_paper_ledger.py`: rewrote `test_smoke_check_does_not_write_ledger` to use
`AlpacaBrokerAdapter` with a locally-defined fake client and `OrderIntent` CSV write
directly — no `paper_smoke_check` import. `paper_smoke_check.py` archived to
`scripts/archive/manual_live_readiness/`. `tests/test_paper_smoke_check.py` deleted.
`tests/test_tools_inventory.py` updated: `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 26 → 25,
`ACTIVE_TOOLS` 30 → 29, `ARCHIVED_TOOLS` 10 → 11. No runtime trading behavior change.

**PR R4d — Extract replay_order_reconciliation library — implemented**
`src/reporting/replay_reconciliation.py` created. `src/tools/replay_order_reconciliation.py` archived.
`paper_status.py` and `test_paper_ledger.py` updated to import from `src.reporting.replay_reconciliation`.
`test_paper_status.py` patch target updated. `TestCLIMain` (5 tests) removed.
`tests/test_tools_inventory.py` updated: `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 25 → 24,
`ACTIVE_TOOLS` 29 → 28, `ARCHIVED_TOOLS` 11 → 12. No runtime trading behavior change.

**PR R4e — Decouple live_submit from manual checklist chain — implemented**
`live_submit._run_checklist` replaced by `_check_automated_risk_gate()`.
`_AUTOMATED_RISK_GATE_IMPLEMENTED = False` ensures live submit is fail-closed until a real
automated gate exists. `live_pre_submit_checklist.py` and `live_dry_run_review.py` archived.
`test_live_pre_submit_checklist.py` and `test_live_dry_run_review.py` deleted.
`tests/test_tools_inventory.py` updated: `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 24 → 22,
`ACTIVE_TOOLS` 28 → 26, `ARCHIVED_TOOLS` 12 → 14. No runtime trading behavior change.

**PR R4f — Decouple live_readiness_gate from manual shadow review chain — implemented**
Module-level imports of `live_shadow_review` and `live_shadow_screen_review` removed from
`live_readiness_gate.py`. `_AUTOMATED_RUNTIME_STATE_GATE_IMPLEMENTED = False` constant added.
`_stage_preflight_review` and `_stage_symbol_screen_review` replaced with fail-closed stubs.
`live_shadow_review.py` and `live_shadow_screen_review.py` archived.
`test_live_shadow_review.py` and `test_live_shadow_screen_review.py` deleted.
`tests/test_tools_inventory.py` updated: `ACTIVE_RUNTIME_CANDIDATE_TOOLS` 22 → 20,
`ACTIVE_TOOLS` 26 → 24, `ARCHIVED_TOOLS` 14 → 16. No runtime trading behavior change.

**PR R4g — Decouple live_submit_enablement_gate from v2 approval bundle — implemented**
Module-level imports of `live_v2_approvals_review` and `live_v2_executor_readiness_review`
removed from `live_submit_enablement_gate.py`. `_read_json` inlined locally.
`_AUTOMATED_SUBMIT_ENABLEMENT_GATE_IMPLEMENTED = False` constant added. `validate_approvals`
and `validate_readiness` calls replaced with fail-closed stubs. All four v2 tools archived:
`live_v2_approvals_review.py`, `live_v2_executor_readiness_review.py`,
`live_v2_final_readiness_review.py`, `live_v2_readiness_bundle.py`.
Four v2 test files deleted. `TestGoHappyPath` and `TestDecisionHardening` removed;
`TestAutomatedSubmitEnablementGate` added. `tests/test_tools_inventory.py` updated:
`ACTIVE_RUNTIME_CANDIDATE_TOOLS` 20 → 16, `ACTIVE_TOOLS` 24 → 20, `ARCHIVED_TOOLS` 16 → 20.
No runtime trading behavior change. Full suite: 4 021 passed.

**PR R5 — Extract paper buy/submit execution path to src/execution/paper_runner.py — implemented**
`src/execution/paper_runner.py` created: two-phase paper buy/submit runner extracted
from `src/main.py`. `PaperRunResult` frozen dataclass captures deterministic result
fields (result, mode, orders_submitted, intents_generated, ledger_rows_written,
broker_calls_made, credentials_read, order_action_requested, network_calls_made).
`run_paper_execution(config, *, output_dir, _broker, _data_provider)` accepts
injectable broker and data provider for fully offline testing. All guards (kill switch,
market hours, daily limits, open orders, ledger) remain as lazy imports inside the
function body — unchanged from main.py behavior. `src/main.py` paper buy/submit block
replaced by three-line thin dispatch: `from src.execution.paper_runner import
run_paper_execution; run_paper_execution(cfg, output_dir=output_dir); return`.
`_run_paper_close` and all gates in `main()` unchanged.
`tests/test_paper_runner.py` added: 11 test classes covering PaperRunResult dataclass,
preview mode, submit mode, preview/submit safety flags, safety constraints,
position safety, quantity override, guard delegation, output artifacts, and main()
delegation. `tests/test_main_characterization.py` updated: 2 new tests in
`TestMainImport` and `TestMainPaperGate`.
No live trading behavior changed. Injected-broker tests remain offline. Default paper
AlpacaBrokerAdapter path may read paper credentials and make broker/account/position
preflight calls, matching existing behavior. Preview mode submits no orders; submit mode
may request exactly one paper order after all guards pass.

**PR R6 — Extract paper close/flatten runner to src/execution/paper_close_runner.py — implemented**
`src/execution/paper_close_runner.py` created: two-phase paper close/flatten runner
extracted from `src/main.py`. `PaperCloseRunResult` frozen dataclass captures
deterministic result fields (result, mode, close_candidates_generated, orders_submitted,
ledger_rows_written, broker_calls_made, credentials_read, order_action_requested,
network_calls_made). `run_paper_close(config, *, output_dir, _broker)` accepts injectable
broker for fully offline testing. All guards and safety constraints remain as lazy imports
inside the function body — unchanged from `_run_paper_close` behavior. `src/main.py`
`_run_paper_close` function (~325 lines) removed; close/flatten path replaced by 3-line
thin dispatch: `from src.execution.paper_close_runner import run_paper_close;
run_paper_close(cfg, output_dir=output_dir); return`.
`tests/test_paper_close_runner.py` added: 10 test classes covering PaperCloseRunResult
dataclass, preview mode, submit mode, preview/submit safety flags, safety constraints,
quantity override, guard delegation, output artifacts, and default-broker-path flag
semantics. `tests/test_main_characterization.py` updated: 2 comment updates, 1 new test
in `TestMainPaperGate`, 1 new test in `TestSourceCharacterization`.
`tests/test_paper_runner.py` updated: `test_paper_close_path_does_not_call_run_paper_execution`
now patches `src.execution.paper_close_runner.run_paper_close` instead of
`src.main._run_paper_close`.
No live trading behavior changed. Injected-broker tests remain offline. Default paper AlpacaBrokerAdapter path may read paper credentials and make broker/account/position preflight calls, matching existing behavior. Preview mode submits no orders; submit mode may request exactly one paper close order after all guards pass. Full suite: 4 167 passed.

No parameter optimisation or paper/live progression until diagnostics complete.

**PR A2-1 — Automated runtime state machine skeleton — implemented**
`src/runtime/__init__.py` and `src/runtime/state_machine.py` created.
`RuntimeState` (9 states: IDLE, CHECKING_RISK, PAPER_PREVIEW, PAPER_SUBMIT_READY,
PAPER_SUBMITTED, PAPER_CLOSE_PREVIEW, PAPER_CLOSED, BLOCKED, ERROR) and
`RuntimeAction` (5 actions: NONE, PREVIEW_BUY, SUBMIT_BUY, PREVIEW_CLOSE, SUBMIT_CLOSE)
enums defined. `RuntimeDecision` and `RuntimeStepResult` frozen dataclasses defined.
`AutomatedRuntimeStateMachine` class accepts injectable `risk_gate`, `paper_buy_runner`,
`paper_close_runner`, and optional `state_store`. Default behavior is fail-closed:
no `risk_gate` injected → every submit action returns `BLOCKED` with blocker
"automated risk gate not implemented". No direct broker, credential, or Alpaca access
inside state machine. All broker interaction remains inside injected paper runners.
Config is deep-copied before preview/submit flags are set; caller config is never mutated.
`tests/test_runtime_state_machine.py` added: 57 tests across 12 classes covering
enums, dataclasses, default fail-closed behavior, risk gate rejection, PREVIEW_BUY,
SUBMIT_BUY, PREVIEW_CLOSE, SUBMIT_CLOSE, runner exception → ERROR transition,
safety flag aggregation, config immutability, state transitions, no-live-gate-import
scan, and `_extract_safety_flags()` helper.
No live trading behavior changed. No live gates unfrozen. No direct broker/API/credential
access inside state machine. Injected-runner tests are fully offline.
Full suite: 4 224 passed.

**PR A2-2 — Automated risk gate skeleton — implemented**
`src/runtime/risk_gate.py` created. `RiskDecision` (APPROVED/BLOCKED) enum,
`RiskCheckResult` and `RiskGateResult` frozen dataclasses defined.
`AutomatedRiskGate(*, enabled=False, rules=None)` class: default fail-closed —
`enabled=False` → `evaluate()` returns `BLOCKED("automated risk gate not enabled")`,
`__call__()` returns `False`. When `enabled=True`, evaluates deterministic local rules
against a caller-supplied context dict: `max_order_quantity`, `allowed_symbols`,
`allowed_sides`. No broker, credential, network, or env access — offline-only.
`live_trading_allowed` is always `False` in A2-2. `broker_calls_made`,
`credentials_read`, `network_calls_made` always `False`. `AutomatedRiskGate` is
callable and can be injected directly into `AutomatedRuntimeStateMachine` as
`risk_gate`. `tests/test_runtime_risk_gate.py` added: 62 tests across 12 classes
covering enum/dataclass structure, default fail-closed, safety flags, live trading
always blocked, enabled approval, all rule types, multiple violations, context/rule
immutability, determinism, callable interface + state machine integration, and
no-forbidden-import scan.
No live trading behavior changed. No live gates unfrozen. No broker/API/credential/env
access. Fully offline. Full suite: 4 286 passed.

**PR A2-3 — Order lifecycle manager skeleton — implemented**
`src/runtime/order_lifecycle.py` created. `OrderLifecycleState` (11 states:
CREATED, RISK_APPROVED, SUBMIT_REQUESTED, SUBMITTED, ACKNOWLEDGED, FILLED,
PARTIALLY_FILLED, CANCELED, REJECTED, FAILED, CLOSED) and `OrderLifecycleEvent`
(11 events: CREATE through CLOSE) enums defined. `OrderLifecycleRecord` and
`OrderLifecycleTransition` frozen dataclasses defined. `OrderLifecycleManager`
in-memory manager: `create_order()`, `apply_event()`, `get()`, `all_orders()`.
Deterministic finite-state transition table: invalid transitions return
`OrderLifecycleTransition(allowed=False)` without mutating state. Terminal states
(FILLED, CANCELED, REJECTED, FAILED, CLOSED) block forward submit events.
MARK_REJECTED and MARK_FAILED allowed from any non-terminal state. No broker,
credential, env, or network access. No file I/O. No order submission, cancellation,
replacement, or closure performed. All safety flags always False.
`tests/test_runtime_order_lifecycle.py` added: 60 tests across 15 classes covering
enums, dataclasses, create_order, duplicate ID blocking, happy path CREATED→CLOSED,
partial fill path, cancel path, rejected/failed paths, invalid transitions, event
history, get/all_orders, multiple order isolation, safety flags, determinism, and
no-forbidden-import scan.
No live trading behavior changed. No live gates unfrozen. No order action performed.
Full suite: 4 346 passed.

**PR A2-4 — Runtime context design and wiring — implemented**
`src/runtime/context.py` created: `RuntimeOrderContext` and `RuntimeContext` frozen
dataclasses. `RuntimeOrderContext` carries `client_order_id`, `symbol`, `side`,
`quantity`, `action`, `metadata`. `RuntimeContext` carries `order`, `mode`, `allow_order_action`,
`allow_live_trading` (always `False`), `metadata`. Both are frozen, deterministic,
no file I/O, no broker/API/env access.
`src/runtime/state_machine.py` updated: `lifecycle_manager=None` constructor parameter
added. `step()` gains optional `context=None` — backward compatible. `_evaluate_risk_gate(context)`
replaces `_check_risk_gate()` and supports three calling protocols: (1) object with
`evaluate(context)` method, (2) one-arg callable, (3) zero-arg callable (backward compat).
`_lc_pre_submit`, `_lc_post_submit`, `_lc_on_error`, `_lc_coid` lifecycle helpers with
lazy imports added. Lifecycle wiring for SUBMIT_BUY/SUBMIT_CLOSE: requires
`context.order.client_order_id`; blocks if absent. Applies `RISK_APPROVE` and
`REQUEST_SUBMIT` before runner call; applies `MARK_SUBMITTED` after runner if
`order_action_requested=True`; applies `MARK_FAILED` on runner exception if lifecycle
record exists. Preview actions never use lifecycle manager.
`tests/test_runtime_context.py` added: 25 tests across 3 classes covering
`RuntimeOrderContext` and `RuntimeContext` frozen dataclass fields, defaults, equality,
mutation rejection, and source scan (no broker/env/network imports).
`tests/test_runtime_state_machine.py` updated: 26 new tests across 5 new classes
covering context passing to risk gate (Protocol-1 evaluate, Protocol-2 one-arg,
backward-compat zero-arg), safety flag merging, lifecycle wiring for SUBMIT_BUY
and SUBMIT_CLOSE, missing `client_order_id` blocks, `MARK_FAILED` on runner exception,
preview actions not calling lifecycle, and `_NO_COID_BLOCKER` constant import.
No live trading behavior changed. No live gates unfrozen. No broker/API/credential access.
Fully offline. Full suite: 4 397 passed.

**PR A2-5 — Fake end-to-end runtime cycle — implemented**
`src/runtime/fake_cycle.py` created. `FakeRuntimeCycleResult` frozen dataclass.
`run_fake_paper_cycle(config, *, symbol, quantity, buy_coid, close_coid,
_paper_buy_runner, _paper_close_runner)` wires `AutomatedRiskGate(enabled=True,
rules={max_order_quantity:1, allowed_symbols:["SPY"], allowed_sides:["buy","sell"]})`,
`OrderLifecycleManager`, local fake paper runners, and `AutomatedRuntimeStateMachine`
into a deterministic four-step cycle: PREVIEW_BUY → SUBMIT_BUY → PREVIEW_CLOSE →
SUBMIT_CLOSE. Halts early on BLOCKED or ERROR. `_AdaptedGate` bridges
`RuntimeContext` to `AutomatedRiskGate.evaluate()` (which expects a Mapping).
All broker/credential/network flags always False. `order_action_requested` is True
for fake submit steps. `live_trading_allowed` always False. No file I/O, no broker
or API access, no env vars, no network.
`tests/test_runtime_fake_cycle.py` added: 44 tests across 8 classes covering
result dataclass, happy-path PASS (4 steps, 2 lifecycle records reaching SUBMITTED),
safety flags, risk gate blocking by quantity/symbol, missing coid blocking, fake runner
exception → ERROR + MARK_FAILED lifecycle state, context guard (allow_live_trading=True
raises), `_context_to_risk_dict` helper, no-forbidden-import scan, and determinism.
No live trading behavior changed. No live gates unfrozen. No broker/API/credential/env
access. Fully offline. This validates runtime skeleton integration only; it does not
approve paper or live automation.
Full suite: 4 446 passed.

**Next PR: S1 candidate universe design (unless code review shows A2-5 needs cleanup).**

### Phase S — Strategy candidate universe and offline discovery

**PR S1 — Strategy candidate universe design — implemented**
`docs/strategy_candidate_universe_design.md` created. Docs-only. Defines the
candidate universe for offline strategy discovery. Covers 8 research questions
(symbol movement, tradable intervals, strategy families, return target framing,
drawdown constraints, out-of-sample stability). Candidate dimensions: symbol
buckets (broad ETFs: SPY/QQQ/IWM/DIA; sector ETFs: XLK/XLF/XLE/XLY/XLV/XLI/XLP/XLU;
liquid mega-caps: AAPL/MSFT/NVDA/AMZN/META/GOOGL/TSLA; volatility/defensive deferred);
intervals (15m, 30m, 60m, 1d); holding horizons (intraday, 1-day, 1–2-day, 3–5-day
comparison); strategy families (trend following/breakout, mean reversion, volatility
contraction/expansion, momentum continuation, gap/overnight, ensemble deferred).
Initial candidate matrix: 7 groups (A–G) covering broad ETFs, sector ETFs, mega-caps
across 30m/60m/1d. Evaluation metrics: return (CAGR, average/distribution monthly
return, worst month), risk (Sharpe, Sortino, max drawdown, Calmar), trade quality
(win rate, profit factor, exposure, trades per month, average/median trade return),
execution quality (stop-loss frequency, session-end frequency, slippage sensitivity).
Acceptance gates: train/test split, walk-forward stability, out-of-sample period, post-cost
positive, no daily 1d session_end artifact, no low-volatility Sharpe artifact, sufficient
trade count, drawdown tolerance, liquidity check, no over-fit. Return target framing:
1%–2% average monthly return is a research objective not a promise; distribution
over months matters more than mean; broader symbol inclusion justified because
SPY/QQQ alone may not meet the target under realistic risk constraints. Data
requirements: cached historical data only; no live dependency; train/test time split;
no raw data committed. Next step: S2 offline candidate evaluation runner
(`src/research/candidate_universe.py`, `src/research/candidate_evaluator.py`).
No src/tests/scripts/config/output/data changes. No broker/API/credential/env
access. No trading approved.

**Next PR: S2 — Offline candidate evaluation runner.**

### Phase S — Strategy candidate universe and offline discovery (continued)

**PR S2 — Offline candidate evaluation runner — implemented**
`src/research/__init__.py`, `src/research/candidate_universe.py`, and
`src/research/candidate_evaluator.py` created. Fully offline, deterministic,
no broker/API/credential/env/network access.
`candidate_universe.py`: `StrategyFamily` (4 values: trend_breakout,
mean_reversion, momentum_continuation, gap_overnight) and `HoldingHorizon`
(4 values: intraday, one_day, one_to_two_days, three_to_five_days) enums.
`CandidateSpec` frozen dataclass. `build_initial_candidate_universe()` returns
a deterministic tuple of **32 candidates** across 7 groups (A–G) following the
S1 design: Group A — broad ETFs × 60m+1d × trend_breakout × 1–2 days;
Group B — sector ETFs × 60m × trend_breakout × 1–2 days; Group C — broad
ETFs × 30m+60m × mean_reversion × intraday/1 day; Group D — mega-caps × 60m
× momentum × 1–2 days; Group E — high-volume × 30m+60m × momentum ×
intraday/1 day; Group F — SPY/QQQ × 60m+1d × gap_overnight × 1 day; Group G
— SPY/QQQ × 1d × trend_breakout × 3–5 days. `filter_candidates()` supports
filtering by group, symbol, interval, strategy family, and holding horizon.
`candidate_evaluator.py`: `CandidateEvaluationResult` and `CandidateGateResult`
frozen dataclasses. 18 `REQUIRED_METRIC_KEYS`. `evaluate_candidate()` fail-closed:
no injected data_provider/backtest_runner → BLOCKED; with both injected →
PASS with metrics from runner; exception → ERROR. `evaluate_candidates()` evaluates
all candidates in order; one failure does not stop others. `apply_candidate_acceptance_gates()`
blocks on: total_trades < 30 or None, max_drawdown None, average_monthly_return None,
session_end_frequency > 0.95, low-volatility artifact (exposure_pct < 0.01 + near-zero
monthly return). All safety flags always False. No parameter optimization.
`tests/test_candidate_universe.py` added: 55 tests across 6 classes.
`tests/test_candidate_evaluator.py` added: 42 tests across 8 classes.
No broker/API/credential/env access. Fully offline. No trading approved.
Full suite: 4 543 passed.

**PR S3 — Wire candidate evaluator to cached backtest path — implemented**
`src/research/cached_candidate_runner.py` created. Fully offline, no broker/API/
credential/env/network access. No order submission. No live or paper trading approval.
`CachedCandidateRunResult` frozen dataclass: `result`, `blocker`, `candidates_requested`,
`candidates_evaluated`, `candidates_passed`, `candidates_blocked`, `candidates_error`,
`results` (tuple of `CandidateEvaluationResult`), and 5 safety flags (all always False).
`run_cached_candidate_evaluation(candidates, *, cache_dir, max_candidates,
_data_provider, _backtest_runner)` injects an offline data loader and backtest runner
into the candidate evaluator via two-phase dispatch:
(1) Strategy family pre-check: `TREND_BREAKOUT` → `"trend_following"` (S3 partial wiring);
all other families → `BLOCKED("strategy family not yet wired: {family}")`.
(2) Cache file lookup via glob `{symbol}_*_{interval}.(parquet|csv)` — BLOCKED if no match.
(3) Load first matching file via `_load_cache_file()` (parquet then csv); BLOCKED on load error
or empty file.
(4) Build `_DataFrameProvider` + real backtest runner closure; call `evaluate_candidate()`.
Real backtest runner: extracts date range from DataFrame index; uses daily-bar guard
(`force_exit_time=None` for 1d/1day/daily intervals); calls `run_backtest()` with
`_TREND_PARAMS`; calls `trade_summary_diagnostics()`; maps to `REQUIRED_METRIC_KEYS`
format (percentage → decimal conversion for cagr, max_drawdown, win_rate,
average_trade_return, exposure_pct; None for worst_month, sortino, median_trade_return,
slippage_bps; exit_reason_counts used to compute session_end_frequency and
stop_loss_frequency; average_monthly_return derived from CAGR; calmar derived from
cagr/|max_drawdown|; trades_per_month from n/months).
Injection mode: when both `_data_provider` and `_backtest_runner` are non-None, cache
lookup is skipped and injected providers are passed directly to `evaluate_candidate()`.
Strategy family check always runs regardless of injection.
Overall result: `PASS` if ≥1 candidate passed; `ERROR` if ≥1 error and none passed;
`BLOCKED` otherwise.
`tests/test_cached_candidate_runner.py` added: 61 tests across 9 classes:
`TestCachedCandidateRunResultDataclass` (5), `TestRunCachedCandidateEvaluationBasics` (7),
`TestStrategyFamilyWiring` (8), `TestCachePreCheck` (7), `TestInjectedProviders` (8),
`TestCounters` (7), `TestOverallResult` (5), `TestSafetyFlags` (4),
`TestMetricsFromInjectedRunner` (4), `TestNoForbiddenImports` (6).
No broker/API/credential/env access. Fully offline. No trading approved.
Full suite: 4 604 passed.

**PR S4 — Metrics mapping validation and walk-forward skeleton — implemented**
`src/research/metrics_validation.py` and `src/research/walk_forward.py` created.
Fully offline, no broker/API/credential/env/network access. No order submission.
No parameter optimization. No live or paper trading approval. No strategy family
expansion — partial wiring (TREND_BREAKOUT only) from S3 unchanged.

`metrics_validation.py`: `MetricsValidationResult` frozen dataclass.
`validate_candidate_metrics(metrics)` validates a `REQUIRED_METRIC_KEYS` dict against
research conventions: all keys present; non-None values are `int` or `float` (not bool);
all values finite (NaN/Inf rejected); `max_drawdown ≤ 0` (negative decimal convention);
`total_trades ≥ 0`; `profit_factor ≥ 0`; `win_rate`, `exposure_pct`,
`session_end_frequency`, `stop_loss_frequency` ∈ [0, 1]. Warnings (informational,
non-blocking) for fields always None in S3: `worst_month`, `sortino`,
`median_trade_return`, `slippage_bps`. result="PASS" or "BLOCKED". All safety flags
always False.

`walk_forward.py`: `WalkForwardSplit` and `WalkForwardResult` frozen dataclasses.
`build_walk_forward_splits(*, start_date, end_date, train_months, test_months,
step_months)`: deterministic rolling split generation. Train windows overlap by
`(train_months − step_months)` months; test windows never overlap. Month arithmetic
via `_add_months()` (standard library only). Split ID format:
`WF{n:03d}_{train_start}_{train_end}_{test_start}_{test_end}`.
`evaluate_walk_forward(candidate, splits, *, split_evaluator=None)`: fail-closed
by default (no evaluator → BLOCKED); evaluate-all policy (all splits evaluated even
if earlier ones fail); exceptions per split → ERROR result for that split; aggregation:
ERROR > BLOCKED > PASS. All safety flags always False.

`tests/test_metrics_validation.py` added: 57 tests across 9 classes.
`tests/test_research_walk_forward.py` added: 64 tests across 8 classes covering
dataclasses, validation/raises (including step_months < test_months guard),
determinism, exact split dates (3-split reference configuration),
non-overlapping test windows, blocked/pass/error aggregation,
evaluate-all policy (continues after exception), safety flags, and no-forbidden-imports.
Note: `tests/test_walk_forward.py` is a pre-existing file testing
`src/experiments/walk_forward_runner.py` (a different, older module).
Full suite: 4 725 passed.

**PR S5 — Wire walk-forward skeleton to cached candidate evaluation — implemented**
`src/research/cached_walk_forward_runner.py` created. Fully offline, no broker/
API/credential/env/network access. No order submission. No parameter optimization.
No strategy family expansion — TREND_BREAKOUT partial wiring from S3 unchanged.

`CachedWalkForwardRunResult` frozen dataclass: `result`, `blocker`, `candidate_id`,
`splits_requested`, `splits_evaluated`, `split_results`
(tuple of `CandidateEvaluationResult`), `validation_results`
(tuple of `MetricsValidationResult`), and 5 safety flags (all always False).

`run_cached_walk_forward_evaluation(candidate, *, start_date, end_date,
train_months, test_months, step_months, cache_dir, _split_evaluator)`:
(1) Calls `build_walk_forward_splits()` — ValueError (e.g. `step_months <
test_months`) → BLOCKED("invalid walk-forward configuration: ...").
(2) No splits generated → BLOCKED("no walk-forward splits generated ...").
(3) Resolves split evaluator: injected `_split_evaluator` or default closure
that calls `run_cached_candidate_evaluation([candidate], max_candidates=1)`.
(4) Calls `evaluate_walk_forward()` — evaluate-all policy; exceptions per split
→ ERROR for that split; all safety flags always False.
(5) Validates each split result's metrics with `validate_candidate_metrics()`.
(6) Aggregates: ERROR > BLOCKED > PASS. A PASS split with validation BLOCKED
contributes BLOCKED to the aggregate without mutating the original frozen result.

`tests/test_cached_walk_forward_runner.py` added: 45 tests across 10 classes
covering `CachedWalkForwardRunResult` dataclass, invalid walk-forward config,
no splits, injected evaluator (call count, order, candidate_id, split counts),
aggregation (PASS/BLOCKED/ERROR/evaluate-all), metrics validation (invalid
metrics → BLOCKED, validation count matches splits, None metrics → PASS),
default path (cache miss → BLOCKED, unsupported family → BLOCKED), safety
flags (always False), determinism, and no-forbidden-imports.
Full suite: 4 770 passed.

**PR S5b — Add per-split date slicing to cached walk-forward evaluator — implemented**
`src/research/cached_candidate_runner.py` extended. `_slice_cached_df()` helper
added: slices a DatetimeIndex DataFrame to `[start_date, end_date]` inclusive
by `.date` comparison. Non-DatetimeIndex → BLOCKED. Empty result → BLOCKED.
`run_cached_candidate_evaluation()` gains `_start_date`/`_end_date` params
(ISO-8601 strings, both optional). Slicing applied after cache-file load (step
4.5); injection mode (`_data_provider` + `_backtest_runner` both provided)
bypasses slicing entirely.

`src/research/cached_walk_forward_runner.py` updated. Default split evaluator
now passes `_start_date=split.test_start` and `_end_date=split.test_end` into
`run_cached_candidate_evaluation()` so each split evaluates only its test-window
rows. "Not yet implemented" wording removed from all docstrings.

`tests/test_cached_candidate_runner.py`: 22 tests added across 2 new classes
(`TestSliceCachedDf` — direct helper tests including invalid-date cases;
`TestDateRangeSlicingIntegration` — end-to-end via tmp_path CSV including
invalid-date BLOCKED and metric-key assertions). Total: 83 tests.
`tests/test_cached_walk_forward_runner.py`: 4 tests added in
`TestDateRangeSlicingWiring` verifying date wiring from split to runner,
BLOCKED propagation on empty window, per-split unique dates, and stale-wording
removal. Total: 49 tests.
Full suite: 4 796 passed.

**PR S6 — Offline research report schema — implemented**
`src/research/report_schema.py` created. Fully offline; no broker/API/
credential/env/network access. No order submission. No parameter optimization.
No report files written or committed. Schema is deterministic and JSON-
serializable with `sort_keys=True`.

Four frozen dataclasses:
  `ResearchReportCandidate` — candidate metadata snapshot (id, group, symbol,
  interval, strategy_family, holding_horizon).
  `ResearchReportSplit` — per-split result with train/test dates (None when
  WalkForwardSplit not supplied), result, blocker, metrics dict, and metrics
  validation result/blocker.
  `ResearchReportSummary` — aggregate counts (splits_passed/blocked/error,
  validations_passed/blocked) and numeric aggregates
  (average_monthly_return_mean/min, max_drawdown_worst, total_trades_sum).
  `ResearchReport` — top-level record: schema_version ("S6/1.0"),
  generated_at_utc (injectable), candidate, summary, splits tuple, safety
  Mapping, notes tuple.

Builders:
  `build_research_report(candidate, walk_forward_result, *, generated_at_utc,
  notes, splits)` — assembles all sub-records; generated_at_utc injectable for
  deterministic tests (defaults to current UTC ISO timestamp); optional splits
  param (tuple[WalkForwardSplit, ...]) enriches split records with date metadata.
  `research_report_to_dict(report)` → plain JSON-serializable dict.
  `research_report_to_json(report)` → deterministic JSON string (sort_keys=True).

Aggregation: only splits where both split result == "PASS" and metrics
validation result == "PASS" contribute to numeric aggregates.
max_drawdown_worst uses the minimum (most negative) value, consistent with
the S4 convention (max_drawdown ≤ 0).
If any safety flag is True, a note is appended automatically; result unchanged.

`tests/test_research_report_schema.py` added: 76 tests across 7 classes
(`TestResearchReportDataclasses`, `TestBuildResearchReport`,
`TestSummaryAggregation`, `TestSafetyFlags`, `TestResearchReportToDict`,
`TestResearchReportToJson`, `TestNoForbiddenImports`).
Full suite: 4 872 passed.

**PR S7 — Offline report snapshot runner — implemented**
`src/research/report_snapshot_runner.py` created. Fully offline; no broker/
API/credential/env/network access. No order submission. No parameter
optimization. No files written or committed.

Wires `CandidateSpec`, `run_cached_walk_forward_evaluation`, and
`build_research_report` into a single entry point:
  `run_offline_report_snapshot(candidates, *, start_date, end_date,
  train_months, test_months, step_months, cache_dir, max_candidates,
  include_json, generated_at_utc, _walk_forward_runner)`

Returns `ReportSnapshotRunResult` (frozen dataclass):
  result, blocker, candidates_requested, reports_created,
  reports (tuple[ResearchReport, ...]), json_reports (tuple[str, ...]),
  and five safety flags OR'd across all individual report safety dicts.

Aggregate result rule:
  ERROR  — if any report's summary.result is "ERROR".
  PASS   — if ≥1 report's summary.result is "PASS" and none are ERROR.
  BLOCKED — all BLOCKED, or no candidates requested.

Exception handling: if walk-forward runner raises for a candidate, a
synthetic ERROR `CachedWalkForwardRunResult` is built (via
`_make_error_wf_result()`); remaining candidates still evaluated.
Exception logged as WARNING.

`include_json=True` serialises each report to a deterministic JSON string
via `research_report_to_json()`; no files written.
`_walk_forward_runner` is injectable for deterministic tests.
`generated_at_utc` is injectable for deterministic tests.

`tests/test_report_snapshot_runner.py` added: 46 tests across 4 classes
(`TestReportSnapshotRunResultDataclass`, `TestRunOfflineReportSnapshot`,
`TestSafetyFlagAggregation`, `TestNoForbiddenImports`).
Full suite: 4 918 passed.

**PR S8 — Offline research pipeline orchestrator — implemented**
`src/research/pipeline_orchestrator.py` created. Fully offline; no broker/
API/credential/env/network access. No order submission. No parameter
optimisation. No files written or committed. No result persistence in S8.
No paper/live trading approved.

Provides `run_offline_research_pipeline(*, candidate_filter, start_date,
end_date, train_months, test_months, step_months, cache_dir, include_json,
generated_at_utc, _snapshot_runner)` as a higher-level entry point above
`run_offline_report_snapshot()`.

Three new frozen dataclasses:
  `CandidateFilter` — multi-value dimension filter (groups, symbols,
  intervals, strategy_families, holding_horizons, max_candidates).
  Each non-empty dimension is an OR filter; dimensions AND-combined.
  strategy_families and holding_horizons match enum .value strings.
  `PipelineSummary` — compact aggregate: counts (candidates_selected,
  reports_created/passed/blocked/error), best_candidate_id (highest
  average_monthly_return_mean among PASS reports; lexicographic tie-break),
  best_average_monthly_return, worst_max_drawdown (most negative across
  all reports with numeric value), total_trades_sum (sum across all
  reports with numeric value).
  `PipelineRunResult` — top-level result: result/blocker, filter,
  summary, snapshot (ReportSnapshotRunResult), and five safety flags
  mirrored from the snapshot.

Candidate selection always starts from `build_initial_candidate_universe()`.
No candidates selected → BLOCKED("no candidates selected") returned
without calling the snapshot runner.
`_snapshot_runner` injectable for deterministic tests.
`generated_at_utc` injectable for deterministic tests.

`tests/test_pipeline_orchestrator.py` added: 51 tests across 7 classes
(`TestCandidateFilterDataclass`, `TestPipelineSummaryDataclass`,
`TestPipelineRunResultDataclass`, `TestCandidateFiltering`,
`TestPipelineIntegration`, `TestPipelineSummaryComputation`,
`TestSafetyFlagAggregation`, `TestNoForbiddenImports`).
Full suite: 4 969 passed.

**PR S9 — Controlled report persistence design — implemented (docs-only)**
`docs/controlled_report_persistence_design.md` added. No source code
added or changed. No test files added or changed. No persistence
implemented. No output artifacts written or committed. No broker/API/
credential/env/network access. No order submission. No live/paper
trading approval.

Defines design for future controlled persistence of `ResearchReport`
objects produced by the S8 pipeline orchestrator:

Artifact structure (future S10 implementation):
  `<output_dir>/<run_id>/manifest.json` — run metadata, counts, safety
  flags, git commit SHA, file inventory.
  `<output_dir>/<run_id>/pipeline_summary.json` — compact PipelineSummary.
  `<output_dir>/<run_id>/reports/<candidate_id>.json` — one ResearchReport
  per candidate.
  `<output_dir>/<run_id>/summary.md` — optional human-readable summary.

Safety gates for future implementation: explicit output directory required;
refuse unsafe paths; refuse overwrite by default; refuse if any safety flag
is True; atomic write (temp dir → rename); no credentials; no network; no
order action.

Validation plan for S10: no writes by default, deterministic filenames,
manifest consistency, safety flag refusal, forbidden-import scans.

Relationship to S10: S10 may implement persistence only after S9 is merged;
S10 still must not approve paper/live trading or add broker/API/credential/
env/network/order access.

**PR S10 — Controlled local report persistence implementation — implemented**
`src/research/report_persistence.py` created. Fully offline; no broker/
API/credential/env/network/order/live/paper access. No live/paper trading
approved. No parameter optimisation. No automatic file writes.

Implements the persistence design from S9:
  `persist_pipeline_run_result(pipeline_result, *, output_dir, generated_at_utc,
  git_commit_sha, include_markdown, allow_overwrite, allow_unsafe_path)`
  → `ReportPersistenceResult` (frozen dataclass).

Fail-closed: returns BLOCKED (writes nothing) when output_dir is None or
empty; any safety flag on the pipeline result is True; output_dir resolves
into data/ or output/; absolute path without allow_unsafe_path=True; run
directory already exists without allow_overwrite=True.

Atomic write: all files written to a temp sibling directory first; renamed
to the final run directory only after all files are prepared; temp directory
cleaned up on any failure.

Artifact structure: `<output_dir>/<run_id>/manifest.json` (schema "S9/1.0"),
`pipeline_summary.json`, `reports/<candidate_id>.json` (one per candidate,
via research_report_to_dict()), optional `summary.md` if include_markdown=True.

`ReportPersistenceResult` safety flags are always False — they represent
actions taken by the persistence function itself, which never contacts
brokers, credentials, network, or orders.

`tests/test_report_persistence.py` added: 48 tests across 7 classes
(`TestReportPersistenceResultDataclass`, `TestOutputDirValidation`,
`TestSafetyFlagRefusal`, `TestOverwriteBehavior`,
`TestSuccessfulPersistence`, `TestAtomicWrite`, `TestNoForbiddenImports`).
Full suite: 5 017 passed.

**PR S11 — Controlled offline research snapshot command — implemented**
`src/research/offline_snapshot_command.py` created. Fully offline; no
broker/API/credential/env/network/order/live/paper access. No live/paper
trading approved. No default output path. No files written unless output_dir
explicitly supplied. No parameter optimisation.

Wires `run_offline_research_pipeline()` (S8) and
`persist_pipeline_run_result()` (S10) into:
  `run_controlled_offline_snapshot(*, candidate_filter, start_date, end_date,
  train_months, test_months, step_months, cache_dir, output_dir,
  include_json, include_markdown, generated_at_utc, git_commit_sha,
  allow_overwrite, allow_unsafe_path, _pipeline_runner, _persistence_runner)`
  → `OfflineSnapshotCommandResult` (frozen dataclass).

Fail-closed: returns BLOCKED without calling pipeline or persistence when
output_dir is None/empty. Returns ERROR when pipeline errors. Returns BLOCKED
when persistence blocks. Any True safety flag forces result to BLOCKED.
Pipeline ERROR does not call persistence. Persistence exceptions return ERROR.

Aggregate result: PASS when pipeline ran (PASS or BLOCKED) and persistence
PASS and no safety flags set; BLOCKED when output_dir missing or persistence
blocked; ERROR when pipeline errored or persistence raised.

Safety flags OR'd from pipeline and persistence sub-results; expected all
False for fully offline operation.

`tests/test_offline_snapshot_command.py` added: 42 tests across 7 classes
(`TestOfflineSnapshotCommandResultDataclass`, `TestOutputDirValidation`,
`TestPipelineRunnerCallthrough`, `TestPersistenceCallthrough`,
`TestAggregateResult`, `TestSafetyFlagAggregation`, `TestNoForbiddenImports`).
Full suite: 5 059 passed.

**PR S12 — Offline research snapshot integration tests — implemented**
`tests/test_offline_snapshot_integration.py` added. Integration-style tests for
the offline research snapshot chain: `CandidateFilter` → `run_controlled_offline_snapshot()`
→ `run_offline_research_pipeline()` (injected fake) → `persist_pipeline_run_result()`
(real, writes to tmp_path). Fake pipeline returns a real `ResearchReport` object
(schema_version "S6/1.0", candidate_id "A_SPY_60m_trend_breakout_1to2d") so that
integration tests verify actual report JSON output through real persistence.
No real backtests. No market data required. No broker/API/credential/env/network/
order/live/paper access.

38 tests across 8 classes:
  `TestEndToEndPass` (8) — PASS pipeline writes manifest, pipeline_summary,
  report JSON; deterministic run_id from fixed timestamp; manifest file-inventory
  cross-check including report files; include_markdown true/false; all safety flags
  false; files_written count ≥ 3 (manifest + pipeline_summary + report).
  `TestEndToEndBlocked` (3) — BLOCKED pipeline still persists; command result is PASS
  (persistence succeeded); run_id contains "BLOCKED".
  `TestEndToEndNoPersist` (4) — output_dir None/empty returns BLOCKED without writing;
  ERROR pipeline does not persist; no default output path.
  `TestOverwrite` (2) — allow_overwrite=False blocks second run; allow_overwrite=True
  replaces (same run_id).
  `TestCandidateFilterPropagation` (2) — filter appears in pipeline_summary.json;
  filter is passed to the pipeline runner.
  `TestSafetyFlagIntegration` (2) — any True safety flag prevents PASS; all flags
  False in clean run.
  `TestReportJsonOutput` (8) — verifies real ResearchReport JSON through real
  persistence: report_paths has length 1; report file exists; filename is
  candidate_id + ".json"; JSON has schema_version "S6/1.0"; JSON candidate_id
  matches; manifest["files"]["reports"] contains the filename; manifest
  inventory matches disk including report; BLOCKED pipeline also writes report JSON.
  `TestNoForbiddenImports` (9) — scans `offline_snapshot_command` and
  `report_persistence` source for forbidden imports/patterns.
`tests/test_offline_snapshot_command.py` updated: `test_empty_string_output_dir_returns_blocked`
replaced weak `files_written_count()` assertion with explicit assertions for
result=="BLOCKED", blocker contains "output_dir", pipeline/persistence/output_dir
are None, and all five safety flags are False.
Full suite: 5 097 passed.

**PR S13 — Offline candidate promotion design — implemented (docs-only)**
`docs/offline_candidate_promotion_design.md` added. No source code added or
changed. No test files added or changed. No persistence implemented. No output
artifacts written or committed. No broker/API/credential/env/network/order/live/
paper access. No paper or live trading approved.

Defines design for future S14 implementation of a pure offline promotion
evaluator that classifies persisted S10/S11 research run artifacts:

Inputs: manifest.json (schema "S9/1.0"), pipeline_summary.json, and
per-candidate reports/<candidate_id>.json (schema "S6/1.0").

Eligibility criteria: result==PASS; all five safety flags false; total_trades_sum
≥ 30; average_monthly_return_mean > 0.0; max_drawdown_worst ≥ −0.25; zero
validations_blocked; zero splits_error; no session_end artifact (frequency > 0.95
disqualifies); no low-exposure Sharpe artifact; schema versions supported;
manifest file inventory consistent.

Disqualification criteria: any safety flag true → BLOCKED_SAFETY; unsupported
schema → BLOCKED_SCHEMA; result not PASS, any split error, any validation
blocked, too few trades, unacceptable drawdown, session_end dominance, candidate_id
mismatch, manifest inventory inconsistency, missing git SHA → REJECTED; null return,
majority blocked splits, low-exposure artifact → NEEDS_MORE_DATA.

Promotion statuses (6): NOT_REVIEWED, PAPER_CANDIDATE_ELIGIBLE, NEEDS_MORE_DATA,
REJECTED, BLOCKED_SAFETY, BLOCKED_SCHEMA. PAPER_CANDIDATE_ELIGIBLE still requires
manual human review before any paper trading decision; it is not paper or live
trading approval; no automated execution follows.

S14 implementation plan: `src/research/candidate_promotion.py` with
`CandidatePromotionResult` frozen dataclass, `PromotionStatus` enum, and
`evaluate_candidate_for_promotion(report_dict, *, manifest_dict)` pure function
(no file I/O, no broker/credential/network/env/order/live access); corresponding
`tests/test_candidate_promotion.py`.
Full suite: 5 097 passed (unchanged — docs-only).

**PR S14 — Pure offline candidate promotion evaluator — implemented**
`src/research/candidate_promotion.py` created. Fully offline; no file I/O;
no broker/API/credential/env/network/order/live/paper access. No trading
approved. Pure function: same inputs → same output. No parameter optimisation.

`PromotionStatus` (str, Enum) with 6 values: NOT_REVIEWED,
PAPER_CANDIDATE_ELIGIBLE, NEEDS_MORE_DATA, REJECTED, BLOCKED_SAFETY,
BLOCKED_SCHEMA.

`CandidatePromotionResult` frozen dataclass: result, blocker, candidate_id,
run_id, status, criteria_checked, criteria_failed, and 5 safety flags (always
False — evaluator makes no broker/credential/network/order/live calls).

`evaluate_candidate_for_promotion(report_dict, *, manifest_dict)`:
(1) Schema check — report schema_version must be "S6/1.0"; manifest
schema_version must be "S9/1.0"; unsupported or missing → BLOCKED_SCHEMA.
(2) Safety flags — all five flags in report["safety"] and manifest["safety"]
must be False; any True → BLOCKED_SAFETY.
(3) Candidate identity — candidate_id must be present; manifest["files"]["reports"]
must contain "<candidate_id>.json"; manifest git_commit_sha must be non-empty.
(4) Summary thresholds — result=="PASS"; splits_error==0;
validations_blocked==0; total_trades_sum≥30; average_monthly_return_mean>0.0;
max_drawdown_worst≥−0.25; session_end_frequency≤0.95 in all splits.
Hard failures → REJECTED. average_monthly_return_mean None → NEEDS_MORE_DATA.
(5) Soft failures — splits_blocked/splits_requested>0.5 or low-exposure
Sharpe artifact (exposure_pct<0.01 and |sharpe_ratio|≥5.0) → NEEDS_MORE_DATA.
(6) All pass → PAPER_CANDIDATE_ELIGIBLE (research classification only; not
paper or live trading approval).

15 stable criterion names tracked in criteria_checked / criteria_failed.

`tests/test_candidate_promotion.py` added: 95 tests across 11 classes
(`TestPromotionStatusEnum`, `TestCandidatePromotionResultDataclass`,
`TestFullyEligibleCandidate`, `TestSchemaBlocking`, `TestSafetyFlagBlocking`,
`TestRejected`, `TestManifestInventory`, `TestRequiredSplitCounts`,
`TestNeedsMoreData`, `TestCriteriaTracking`, `TestPurityAndImmutability`,
`TestSafetyFlagsOnResult`, `TestNoForbiddenImports`).
Full suite: 5 192 passed.

**PR S15 — Paper-candidate manual review workflow design — implemented (docs-only)**
`docs/paper_candidate_manual_review_workflow.md` added. No source code added or
changed. No test files added or changed. No persistence implemented. No output
artifacts written or committed. No broker/API/credential/env/network/order/live/
paper access. No paper or live trading approved.

Defines the manual review workflow that follows an S14 PAPER_CANDIDATE_ELIGIBLE
classification:

Inputs: S10/S11 manifest, pipeline_summary, candidate report JSON, S14
CandidatePromotionResult, manifest git SHA, per-split ResearchReport metrics,
candidate universe metadata, reviewer notes.

Manual review checklist (§4): promotion status verification; schema and
provenance (git SHA traceable to reviewed merged commit, no manual edits);
safety flag verification (all five flags false in manifest, report, and S14
result); performance metrics inspection (returns, drawdown, trade count,
per-split consistency); artifact anomaly inspection (session_end_frequency,
low-exposure Sharpe, blocked/error splits); overfitting risk assessment (symbol
concentration, recency bias, curve-fit risk, horizon/interval alignment);
evidence sufficiency (≥12 months OOS data, ≥3 non-blocked splits, economic
rationale, reproducibility); reviewer record (name, date, decision, risk notes,
limitations, evidence bundle reference).

Review decisions (5): APPROVED_FOR_PAPER_CONFIG_DESIGN, NEEDS_MORE_RESEARCH,
REJECTED_MANUAL_REVIEW, BLOCKED_SAFETY_REVIEW, BLOCKED_PROVENANCE.

APPROVED_FOR_PAPER_CONFIG_DESIGN authorises only a future docs-only paper
configuration design PR (S16). It does not approve paper trading, live trading,
any broker/API/order/credential/network access, or any runtime/execution change.
Paper and live trading remain not enabled.

Required evidence bundle: manifest, pipeline_summary, candidate report, S14
result, completed checklist, reviewer record, git SHA verification. Bundle
retained locally only; no files committed to repository.

S16 plan: docs-only paper configuration design (`docs/paper_trading_config_design.md`);
still no broker/API/credential/order/live implementation; no execution path changes.
Full suite: 5 192 passed (unchanged — docs-only).

**PR S16 — Paper trading configuration design — implemented (docs-only)**
`docs/paper_trading_config_design.md` added. No source code added or changed.
No test files added or changed. No real config file created. No persistence
implemented. No output artifacts written or committed. No broker/API/credential/
env/network/order/live/paper access. No paper or live trading approved.

Defines the paper trading configuration artifact schema as a proposal for a
future operator-managed JSON file (stored outside the repository):

Preconditions: S14 PAPER_CANDIDATE_ELIGIBLE + S15 APPROVED_FOR_PAPER_CONFIG_DESIGN.
These preconditions do not approve paper trading; they only permit authoring a
paper config schema.

Proposed schema "PC/1.0" (4 field groups): provenance (config_schema_version,
candidate_id, run_id, source_git_sha); strategy identity (symbol, interval,
strategy_family, holding_horizon); paper account (paper_account_label — label
only, no credentials); risk limits (max_notional_per_position,
max_position_fraction ≤ 0.10, max_daily_loss, max_drawdown_stop,
max_orders_per_day, min_cash_buffer); execution assumptions
(allowed_order_types, allowed_session, slippage_bps_assumption,
commission_bps_assumption); review fields (risk_review_notes, reviewer_name,
review_date_utc, approval_status).

Explicitly forbidden config fields: broker API/secret keys, account credentials,
live account IDs, env var names containing secrets, direct order instructions,
market data subscription credentials, any field enabling live trading.

Config approval statuses (6): DRAFT, READY_FOR_PAPER_CONFIG_REVIEW,
APPROVED_FOR_PAPER_SIMULATION_DESIGN, REJECTED_CONFIG_REVIEW,
BLOCKED_RISK_LIMITS, BLOCKED_PROVENANCE. APPROVED_FOR_PAPER_SIMULATION_DESIGN
authorises only a future docs-only paper simulation design PR; it does not
approve paper trading, live trading, or any broker/API/order/credential access.

S17 validation rules: schema version, provenance cross-check, risk limit bounds
(max_position_fraction ≤ 0.10, max_orders_per_day ≤ conservative cap), allowed
order type allowlist, forbidden field credential scan, reviewer field presence.
Full suite: 5 192 passed (unchanged — docs-only).

**PR S17 — Pure offline paper config validator — implemented**
`src/research/paper_config_validator.py` created. Fully offline; no file I/O;
no broker/API/credential/env/network/order/live/paper access. No real config
file created. No paper or live trading approved. Pure function: same inputs →
same output. No parameter optimisation.

`PaperConfigStatus` (str, Enum) with 6 values matching the S16 schema:
DRAFT, READY_FOR_PAPER_CONFIG_REVIEW, APPROVED_FOR_PAPER_SIMULATION_DESIGN,
REJECTED_CONFIG_REVIEW, BLOCKED_RISK_LIMITS, BLOCKED_PROVENANCE.

`PaperConfigValidationResult` frozen dataclass: result, blocker, candidate_id,
run_id, status, criteria_checked, criteria_failed, and 5 safety flags (always
False — validator makes no broker/credential/network/order/live calls).

`validate_paper_config(config_dict)`: checks 24 criteria in deterministic order.
(1) Schema — `config_schema_version` must be "PC/1.0"; mismatch → early exit
with BLOCKED_PROVENANCE after exactly 1 criterion checked.
(2) Provenance — candidate_id, run_id, source_git_sha: missing/empty →
BLOCKED_PROVENANCE.
(3) Strategy identity — symbol, interval, strategy_family, holding_horizon:
missing/empty → REJECTED_CONFIG_REVIEW.
(4) Paper account label — must be non-empty; any credential-like substring
(api_key, secret, token, password, credential, auth) → BLOCKED_PROVENANCE.
(5) Risk limits — max_notional_per_position (finite positive),
max_position_fraction (≤ 0.10), max_daily_loss (finite positive),
max_drawdown_stop (≤ 1.0), max_orders_per_day (positive integer ≤ 10),
min_cash_buffer (finite positive < 1.0) → violation: BLOCKED_RISK_LIMITS.
(6) Execution — allowed_order_types (non-empty list, allowlist {"market","limit"}),
allowed_session ("regular"), slippage_bps_assumption (finite ≥ 0),
commission_bps_assumption (finite ≥ 0) → violation: REJECTED_CONFIG_REVIEW.
(7) Review fields — risk_review_notes, reviewer_name (non-empty strings);
review_date_utc (ISO-8601-like datetime string: `YYYY-MM-DDTHH:MM:SS[Z|±HH:MM]`
— plain date-only strings rejected); approval_status (valid enum value) →
BLOCKED_PROVENANCE or REJECTED_CONFIG_REVIEW.
(8) Forbidden scan — recursive scan of all keys and string values for
credential/order/live patterns (api_key, secret_key, api_secret, auth_token,
password, credential, live_account_id, production_account, submit_order,
place_order, live_trading, env:, os.environ) → BLOCKED_PROVENANCE.

Priority bucketing (highest first): BLOCKED_PROVENANCE → BLOCKED_RISK_LIMITS
→ REJECTED_CONFIG_REVIEW.

APPROVED_FOR_PAPER_SIMULATION_DESIGN is a config classification only; it does
not approve paper trading, live trading, or any broker/API/order/credential
access. Paper and live trading remain blocked.

`tests/test_paper_config_validator.py` added: 135 tests across 13 classes
(`TestPaperConfigStatusEnum`, `TestPaperConfigValidationResultDataclass`,
`TestPassingConfigs`, `TestSafetyFlagsAlwaysFalse`, `TestSchemaVersion`,
`TestProvenanceFields`, `TestStrategyFields`, `TestPaperAccountLabel`,
`TestRiskLimits`, `TestExecutionFields`, `TestReviewFields`,
`TestReviewDateUtc`, `TestForbiddenScan`, `TestPriorityBucketing`,
`TestCriteriaOrder`, `TestPureFunctionProperties`, `TestNoForbiddenImports`).

No real config file added. No file I/O. No broker/API/credential/env/network/
order/live/paper access. All 5 safety flags always False.
Full suite: 5 319 passed.

**PR S18 — Paper simulation design — implemented (docs-only)**
`docs/paper_simulation_design.md` added. No source code added or changed.
No test files added or changed. No real config file created. No simulation
runner implemented. No output artifacts written or committed. No broker/API/
credential/env/network/order/live/paper access. No paper or live trading
approved.

Defines the paper simulation layer that follows S17 config validation:

Preconditions: S14 PAPER_CANDIDATE_ELIGIBLE + S15
APPROVED_FOR_PAPER_CONFIG_DESIGN + S16 config schema + S17
`validate_paper_config()` returning PASS. These preconditions do not approve
paper trading; they only permit designing a future simulation implementation.

Proposed simulation inputs: already-validated config dict, offline cached bars
only, start/end dates. No broker account state, no live market data, no
credentials, no env vars.

Proposed simulation outputs (all in-memory, no file writes): simulation summary
dict (result, blocker, provenance, metrics, 5 safety flags always False),
simulated trades list (entry/exit bar indices, price assumptions with slippage,
simulated P&L, exit reason), simulated daily equity curve, simulated risk limit
events.

Simulation safety model: fail-closed — BLOCKED if config validation is not
PASS, any safety flag is True, bars are empty/wrong interval, or date range is
out of bounds. All 5 safety flags always False on simulation result. No broker
calls, no order submission, no paper account connection, no live data, no live
gate changes.

Simulation status vocabulary (6): NOT_RUN, PASS, BLOCKED_CONFIG,
BLOCKED_SAFETY, BLOCKED_DATA, ERROR_SIMULATION. PASS authorises only review
of simulation outputs as additional evidence; it does not approve paper trading,
live trading, or any broker/API/order/credential access.

S19 implementation plan: `run_paper_simulation(config_dict, bars, *, ...)` →
`PaperSimulationResult` frozen dataclass; pure offline function; injectable
signal provider and fill model for deterministic tests; no file I/O; no
broker/API/credential/env/network/order access; no runtime/execution changes;
no paper/live trading approval.

`APPROVED_FOR_PAPER_SIMULATION_DESIGN` is a config classification only.
Simulation PASS is an offline research finding only. Neither authorises paper
trading, live trading, or any order submission.
Full suite: 5 319 passed (unchanged — docs-only).

**PR S19 — Pure offline paper simulation skeleton — implemented**
`src/research/paper_simulation.py` created. Fully offline; no file I/O; no
broker/API/credential/env/network/order/live/paper access. No real config file
created. No paper or live trading approved. Pure function: same inputs → same
output. No parameter optimisation.

`PaperSimulationStatus` (str, Enum) with 6 values: NOT_RUN, PASS,
BLOCKED_CONFIG, BLOCKED_SAFETY, BLOCKED_DATA, ERROR_SIMULATION.

`PaperSimulationResult` frozen dataclass: result, blocker, status, summary
dict, trades tuple, equity_curve tuple, risk_limit_events tuple, and 5 safety
flags (always False — simulator makes no broker/credential/network/order/live
calls).

`run_paper_simulation(config_dict, bars, *, start_date, end_date,
_signal_provider, _fill_model, _config_validator)`:
(1) Config gate — calls validate_paper_config() (injectable); any True safety
flag → BLOCKED_SAFETY; non-PASS result → BLOCKED_CONFIG.
(2) Data gate — bars must be non-empty list of dicts with timestamp, interval,
open, high, low, close; all prices finite positive; all bars matching
config interval → BLOCKED_DATA; if all bars have wrong interval →
BLOCKED_CONFIG; date range must select ≥1 bar → BLOCKED_DATA.
(3) Simulation loop — bar-by-bar; signal provider (injectable, default HOLD)
returns "ENTER_LONG", "EXIT", or "HOLD"; unsupported signal → ERROR_SIMULATION;
fill model (injectable, default close ± slippage_bps) prices entries/exits;
risk checks before every entry: max_drawdown_stop, max_daily_loss,
max_orders_per_day, min_cash_buffer/notional cap — violations produce
risk_limit_events (not BLOCKED); position sizing respects
max_notional_per_position, max_position_fraction, min_cash_buffer; commission
deducted in PnL; open position closed at end of simulation.
(4) Outputs — summary dict (metrics + all 5 safety flags always False), trades
tuple, equity_curve tuple, risk_limit_events tuple; all in-memory, no writes.

Simulation PASS is an offline research finding only. It does not approve paper
trading, live trading, or any order submission.

`tests/test_paper_simulation.py` added: 74 tests across 9 classes
(`TestPaperSimulationStatusEnum`, `TestPaperSimulationResultDataclass`,
`TestDefaultNoSignal`, `TestConfigGate`, `TestDataGate`,
`TestUnsupportedSignal`, `TestSimulatedTrade`, `TestPositionSizing`,
`TestRiskLimitEvents`, `TestEquityCurve`, `TestSafetyFlags`,
`TestPureFunctionProperties`, `TestNoForbiddenImports`).

No real config file added. No file I/O. No broker/API/credential/env/network/
order/live/paper access. All 5 safety flags always False.
Full suite: 5 401 passed.

**PR S20 — Paper simulation integration tests and cleanup — implemented**
`tests/test_paper_simulation_integration.py` created: 22 tests covering the
offline S17 → S19 chain `validate_paper_config(config_dict) →
run_paper_simulation(config_dict, bars, ...)`. Uses the real
`validate_paper_config()` (no fake validator) for every scenario, including
a parametrised class that explicitly drives ≥5 cases through the real
validator. All bars and configs are in-memory plain dicts; no real config
files, no cached data, no network/broker access, no file writes.

Coverage: valid config passes both S17 and S19; invalid schema version,
forbidden credential-like field, and out-of-range risk limit each block at
S17 and propagate to `BLOCKED_CONFIG`; valid config with empty bars →
`BLOCKED_DATA`; valid config with interval-mismatched bars → `BLOCKED_CONFIG`;
deterministic ENTER_LONG → EXIT provider yields exactly one simulated trade;
summary preserves `candidate_id`/`run_id` provenance from the config; all 5
safety flags remain False across PASS, BLOCKED_CONFIG, BLOCKED_DATA, and
ERROR_SIMULATION paths; simulation PASS carries no paper/live approval field
or flag; config and bars are not mutated by the chain; same input always
produces the same output (determinism); and no files are created under
`output/` or `data/cache/` by running the chain.

`tests/test_paper_simulation.py` cleanup: replaced the weak conditional
assertion in `test_risk_event_has_required_fields` (which used an invalid
`min_cash_buffer=0.0` config and only checked fields `if` an event happened
to fire) with a deterministic `max_orders_per_day=1` scenario that reliably
produces a `risk_limit_event`, and now asserts `result == "PASS"`, at least
one event is produced, every event has `bar_index`/`limit_type`/`limit_value`,
and all 5 safety flags remain False.

No production code changed. No real config file added. No file I/O. No
broker/API/credential/env/network/order/live/paper access added.
Simulation PASS remains an offline research finding only — it does not
approve paper trading, live trading, or any order submission. Paper and
live trading remain not enabled.
Full suite: 5 423 passed.

**PR S21 — Paper simulation results review workflow design (docs-only) — added**
`docs/paper_simulation_results_review_workflow.md` created, defining the
manual review workflow that follows the S19 simulation skeleton and the S20
integration evidence. Docs-only: no source code, no tests, no config files,
no artifacts.

Defines: review inputs (S17 validation result, S19 `PaperSimulationResult`,
S20 integration evidence, summary/trades/equity-curve/risk-event tuples,
reviewer notes); a 15-item manual review checklist (config/simulation PASS,
all 5 safety flags False, candidate_id/run_id provenance match,
total_simulated_trades, total_return_pct, max_drawdown_pct, win_rate_pct,
risk_limit_events count/types, equity curve stability, trade assumptions,
slippage/commission realism, single-trade/single-day dependence, risk-limit
calibration, no implied paper/live approval); a 6-value review decision
vocabulary (NOT_REVIEWED, NEEDS_MORE_SIMULATION_DATA,
REJECTED_SIMULATION_REVIEW, BLOCKED_SAFETY_REVIEW, BLOCKED_PROVENANCE,
APPROVED_FOR_PAPER_TRADING_DESIGN); the required evidence bundle (S14
promotion result, S15 review decision, S16 config design reference, S17
validation result, S19 simulation result, S20 integration evidence, reviewer
checklist/decision/risk-notes/known-limitations); 11 disqualification
criteria; and the future S22 docs-only paper trading architecture design plan.

`APPROVED_FOR_PAPER_TRADING_DESIGN` authorises only a future docs-only
architecture design PR (S22) — no broker, paper account, API key, credential,
network, runtime, executor, or order path follows automatically. Simulation
PASS remains an offline research finding only; it does not approve paper
trading, live trading, or any order submission. Paper and live trading
remain not enabled. No review workflow tooling implemented.
Full suite: 5 423 passed (unchanged — docs-only).

**PR S22 — Paper trading architecture design (docs-only) — added**
`docs/paper_trading_architecture_design.md` created, defining the proposed
architecture for a future paper trading system, produced after S21 recorded
`APPROVED_FOR_PAPER_TRADING_DESIGN`. Docs-only: no source code, no tests, no
config files, no artifacts.

Defines: six preconditions (S14 PAPER_CANDIDATE_ELIGIBLE, S15
APPROVED_FOR_PAPER_CONFIG_DESIGN, S17 PASS, S19 PASS, S20 integration
evidence, S21 APPROVED_FOR_PAPER_TRADING_DESIGN — none of which approves
paper trading); ten proposed architecture components (paper config validator
and simulation result reviewer — existing/pure offline; paper trading
approval artifact, paper account connector boundary, paper order planner,
paper order safety gate, paper execution adapter boundary, paper ledger /
observation recorder — all future-only and unimplemented; kill switch /
fail-closed state; audit log / evidence bundle); component boundaries
distinguishing pure-offline from broker/network-requiring components (only
the account connector and execution adapter boundaries would ever need
credentials/network — both remain conceptual placeholders, explicitly
undesigned-in-detail); a proposed 8-stage data flow with stages 3–8 (future
approval artifact → order plan → safety gate → execution adapter → ledger →
observation review) explicitly marked future-only and unimplemented; an
8-point safety gate model (fail-closed default, explicit approval artifact
required, paper-only account labels never credentials, kill switch enabled
by default, max notional/orders/daily-loss/drawdown limits, dry-run/no-submit
default, human confirmation required, no live account access); five required
future approval artifact types (architecture review approval, sandbox
readiness approval, dry-run approval, limited-run approval, observation-period
review — none of which exists, each explicitly stated as not live trading
approval); and the future S23 plan (docs-only safety gate design or
tests-only architecture-invariant characterization — still no broker/API/
credential/network/order access, still no paper/live approval, no
runtime/execution changes).

S22 does not approve paper or live trading, does not create any broker/API/
order path, does not permit credential/env/network access, and does not
modify runtime/execution. It only permits a future S23 docs-only or
tests-only design PR. Paper trading remains not approved. Live trading
remains blocked. No order action follows from any architecture status
described.
Full suite: 5 423 passed (unchanged — docs-only).

**PR S23 — Paper architecture invariant tests (tests-only) — added**
`tests/test_paper_architecture_invariants.py` created (36 tests): a
characterization-test module that locks down the S14–S22 offline/paper-prep
chain as an architecture invariant, with no new production behaviour. Tests
only — no source code, config, or runtime changes.

Coverage: (1) the six core offline modules (`pipeline_orchestrator`,
`report_persistence`, `offline_snapshot_command`, `candidate_promotion`,
`paper_config_validator`, `paper_simulation`) contain no actual broker/
network/credential/order imports or calls — and confirms the
`os.environ`/`submit_order`/`place_order` substrings that exist in
`paper_config_validator` are scan-list string literals only, never real
usage; (2) `run_paper_simulation` / `PaperSimulationResult` remain pure
offline with all five safety flags False on a PASS run and no
approval-implying summary keys; (3) `validate_paper_config` remains pure
offline, returns PASS with all five safety flags False for a valid PC/1.0
config, and blocks configs containing credential fields or forbidden
substrings; (4) `evaluate_candidate_for_promotion` remains research-only,
with all five safety flags False on an eligible result and no
approval-named result fields; (5) `docs/paper_trading_architecture_design.md`
still states docs-only/future-only/not-approved/blocked invariants;
(6) `docs/live_readiness_status.md` still records live trading disabled,
kill switch engaged, dry-run mode, human-confirm required, and not approved;
(7) no production paper-trading execution path
(`PaperBroker`/`PaperExecutionAdapter`/`PaperOrderSubmitter`/
`submit_order(`/`place_order(`) exists anywhere under `src/research/`;
(8) no new artifact/config files were added, and the test module itself
performs no file-write operations (read-only `_read_doc` helper only).

S23 adds tests only. No production behaviour was added. No broker, API,
Alpaca, credential, environment variable, network, or order access was
added anywhere. Paper trading remains not approved. Live trading remains
blocked. All live-gate safety flags remain fail-closed.
Full suite: 5 459 passed (5 423 baseline + 36 new invariant tests).

**PR S24 — Paper trading approval artifact schema design (docs-only) — added**
`docs/paper_trading_approval_artifact_design.md` created, defining the
proposed `PTA/1.0` schema for a future paper trading approval artifact —
the "paper trading limited-run approval" record named as a required future
artifact type in the S22 architecture design. Docs-only: no source code, no
tests, no config files, no artifacts. **S24 adds design only. No approval
artifact exists.**

Defines: the purpose (a future, separately-approved record required before
any future paper order planner or paper order path could proceed; does not
approve paper trading by itself; can never approve live trading); the
required upstream evidence chain (S14 PAPER_CANDIDATE_ELIGIBLE, S15
APPROVED_FOR_PAPER_CONFIG_DESIGN, S17 PASS, S19 PASS, S20 integration
evidence, S21 APPROVED_FOR_PAPER_TRADING_DESIGN, S22 architecture reference,
S23 invariant tests passing); 32 proposed schema fields (provenance hashes,
risk limits, allowed symbols/intervals/strategy-families/order-types/session,
dry-run/human-confirmation/kill-switch flags, `live_trading_approved` /
`live_order_submission_approved`, notes, known limitations); 8 structurally
fixed values (`artifact_schema_version = "PTA/1.0"`, `approval_artifact_type
= "PAPER_TRADING_APPROVAL"`, `approval_scope =
"PAPER_TRADING_LIMITED_RUN_ONLY"`, `live_trading_approved = false`,
`live_order_submission_approved = false`, `dry_run_required = true`,
`human_confirmation_required = true`, `kill_switch_required = true`); a
forbidden-field list (credential fields, account identifiers, order-action
instructions, `live_trading_approved = true`, anything that would directly
enable broker/network/order access); 20 proposed validation rules for a
future S25 validator (schema version, required-field presence, provenance
matching, hash matching, expiry, risk-limit bounds incl.
`max_position_fraction <= 0.10` and `max_orders_per_day <= 10`,
`allowed_order_types ⊆ {"market","limit"}`, `allowed_session == "regular"`,
forbidden-field scanning, missing-evidence blocking); 9 proposed future
statuses (`NOT_REVIEWED`, `DRAFT`, `APPROVED_FOR_DRY_RUN_DESIGN`,
`APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN`, `APPROVED_FOR_LIMITED_PAPER_RUN`,
`REJECTED_APPROVAL_REVIEW`, `BLOCKED_PROVENANCE`, `BLOCKED_RISK_LIMITS`,
`BLOCKED_SAFETY`); what an approved artifact would and would not authorise
(only its named scope/candidate/run/config/evidence/account-label, never
live trading, never automatic execution); and the future S25 plan (a pure
offline approval-artifact validator, analogous to S17's
`validate_paper_config()` — in-memory dict input only, no file/broker/API/
credential/env/network/order access, grants no paper/live approval, all
five safety flags always False, with its own required test coverage).

S24 does not create any real approval artifact, does not approve paper or
live trading, does not add any broker/API/credential/env/network/order
access, and does not modify runtime/execution. It only permits a future,
separately-approved S25 implementation PR for a pure offline validator —
which would itself still grant no paper or live trading approval. Paper
trading remains not approved. Live trading remains blocked. No order action
follows from any artifact shape, status, or field described.
Full suite: 5 459 passed (unchanged — docs-only).

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
