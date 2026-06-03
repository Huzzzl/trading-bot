# Strategy Candidate Universe Design

Design document for PR S1: define the candidate universe for offline strategy
discovery to identify which symbols, intervals, strategy families, and holding
periods are worth evaluating for the automated trading bot.

**No code is implemented in this document.**
**No Alpaca endpoint is contacted.**
**No credentials are read.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live trading is approved.**
**No automated paper trading is approved.**
**No parameters are optimized.**
**This document does not approve any individual trade.**

---

## 1. Goal

Define a structured candidate universe for offline strategy discovery. The
purpose is to narrow the research space to a tractable set of (symbol,
interval, strategy family, holding horizon) combinations that are plausible
candidates for a future automated bot before any backtest is run.

The target holding horizon for the eventual bot is **1 day to 2 days**, but
the candidate universe deliberately includes shorter and slightly longer
horizons for comparison. A strategy outside the primary target may still
inform parameter selection or reveal edge-decay patterns.

This document does **not** select a final strategy. It defines what to test
and how to evaluate it. Selection requires evidence from S2 (offline evaluation
runner) and a dedicated strategy approval review.

---

## 2. Research Questions

Before any backtest, the following questions must be answerable from the
candidate universe design:

| # | Question |
|---|----------|
| 1 | Which symbols have enough intraday or daily directional movement after transaction costs? |
| 2 | Which intervals produce tradable signals without excessive noise or look-ahead artifacts? |
| 3 | Which strategy families fit 1-day, 1–2-day, and swing (3–5-day) horizons structurally? |
| 4 | Can the strategy produce **1%–2% average monthly return** after realistic costs and slippage, on any candidate? |
| 5 | What drawdown, turnover, and exposure would make that return unacceptable regardless of the mean? |
| 6 | Is the edge stable out-of-sample on a held-out time period? |
| 7 | Do QQQ/SPY alone have sufficient 1–2 day movement, or is a broader symbol universe necessary? |
| 8 | Are there structural patterns (gaps, intraday breakout, overnight momentum) that survive across multiple symbols? |

Note on Q4: 1%–2% average monthly return is a **research target**, not a
performance guarantee or promise. Monthly return is volatile; the distribution
over months matters more than the mean. A strategy with a 2% mean but
unacceptable drawdown or zero out-of-sample evidence is not acceptable.

---

## 3. Candidate Dimensions

### 3.1 Symbol Buckets

The initial universe is organized into buckets, not a flat list. Broader
coverage in S1 is intentional: SPY/QQQ 60m was the characterization lane in
the B-series, but it is not sufficient to answer the strategy discovery question.

| Bucket | Symbols | Rationale |
|--------|---------|-----------|
| Broad market ETFs | SPY, QQQ, IWM, DIA | Highest liquidity; lowest spread risk; well-studied intraday patterns |
| Sector ETFs | XLK, XLF, XLE, XLY, XLV, XLI, XLP, XLU | Sector rotation and momentum can diverge from broad index |
| Liquid mega-cap stocks | AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA | High ADV; tight spreads; known momentum and gap patterns |
| High-volume momentum names | Initially limited to top liquid names above; expansion deferred | Broader momentum universe requires separate data availability check |
| Volatility / defensive (deferred) | TLT, GLD, USO | May be useful for drawdown reduction or signal diversification; defer to S3+ |

**Initial scope for S2**: broad market ETFs + sector ETFs + liquid mega-caps.
High-volume momentum expansion and volatility/defensive candidates are deferred
pending data availability confirmation.

### 3.2 Intervals

| Interval | Use case | Notes |
|----------|----------|-------|
| 15m | Short intraday; noise-sensitive | Viable only for mean-reversion patterns; high turnover |
| 30m | Intraday trend / breakout | Better signal-to-noise than 15m; still intraday |
| 60m | Primary characterization lane | Already validated pipeline via B-series; start here |
| 1d | Daily swing | Session-end artifact known (see PR 10W); force_exit_time must be None |

The 1d + force_exit_time guard from PR 10W is already in the codebase. Any
1d backtest must use `force_exit_time=None` or Phase 2 Policy A engine fix
(deferred). Daily-bar session_end artifact remains present on 1d until
Phase 2 is implemented; 1d results must be interpreted with that caveat.

### 3.3 Holding Horizon Buckets

| Bucket | Target bars | Use in evaluation |
|--------|-------------|-------------------|
| Intraday only | ≤ market session length | Comparison baseline; high friction |
| 1 trading day | ~6–8 bars at 60m | Primary target lower bound |
| 1–2 trading days | ~12–16 bars at 60m | Primary target range |
| 3–5 trading days (comparison only) | ~20–40 bars at 60m | Swing comparison; may reveal edge stability |

Holding horizon is a **result** of strategy behavior (entry/exit rules), not
a parameter to optimize directly. The evaluation checks whether the actual
median holding aligns with the target bucket.

### 3.4 Strategy Families

| Family | Description | Candidate intervals | Primary holding |
|--------|-------------|---------------------|----------------|
| Trend following / breakout | Enter on directional signal above moving average or range break | 30m, 60m, 1d | 1–2 days |
| Mean reversion | Enter against short-term move when price deviates from short-term mean | 15m, 30m, 60m | Intraday to 1 day |
| Volatility contraction / expansion | Enter when realized volatility compresses then expands (squeeze) | 30m, 60m | 1–2 days |
| Momentum continuation | Enter aligned with multi-bar momentum without reversal signal | 30m, 60m, 1d | 1–2 days |
| Gap / overnight effect | Entry based on overnight gap direction or gap-fill tendency | 60m, 1d | Intraday to 1 day |
| Simple ensemble filter (deferred) | Combine signals from multiple families with a voting or ranking rule | TBD | TBD |

For S2, the initial evaluation starts with **trend following / breakout** and
**mean reversion** across broad ETFs and mega-caps at 30m/60m/1d. Other
families are added in subsequent passes.

---

## 4. Initial Candidate Matrix

The first-pass evaluation matrix is conservative and structured to avoid
combinatorial explosion. Not all (symbol × interval × strategy × holding)
combinations need to be run in S2.

| Group | Symbol set | Interval | Strategy family | Holding target |
|-------|-----------|---------|----------------|---------------|
| A | SPY, QQQ, IWM | 60m, 1d | Trend following / breakout | 1–2 days |
| B | XLK, XLF, XLE, XLY | 60m | Trend following / breakout | 1–2 days |
| C | SPY, QQQ, IWM | 30m, 60m | Mean reversion | Intraday to 1 day |
| D | AAPL, MSFT, NVDA, AMZN | 60m | Momentum continuation | 1–2 days |
| E | TSLA, META, GOOGL | 30m, 60m | Momentum continuation | Intraday to 1 day |
| F | SPY, QQQ | 60m, 1d | Gap / overnight effect | Intraday to 1 day |
| G (comparison) | SPY, QQQ | 1d | Trend following / breakout | 3–5 days (swing) |

Groups A and C are immediate priority because the 60m pipeline is already
validated. Other groups are run in parallel pending data availability.

---

## 5. Evaluation Metrics

The following metrics are required for each (symbol, interval, strategy)
combination in S2. All metrics are aggregate only — no raw prices, fill
prices, or individual trade details in output.

### Return metrics

| Metric | Notes |
|--------|-------|
| CAGR | Compound annual growth rate over full backtest period |
| Average monthly return | Mean of per-month returns; must be accompanied by distribution |
| Monthly return distribution | Histogram or quartiles; reveals variability not visible in mean |
| Worst month | Single worst calendar month; key drawdown risk indicator |
| Consecutive losing trades | Max losing streak |

### Risk metrics

| Metric | Notes |
|--------|-------|
| Sharpe ratio | Annualized; flag if std ≈ 0 using existing `diagnose_sharpe()` |
| Sortino ratio | Downside deviation only; preferred for skewed return distributions |
| Max drawdown | Peak-to-trough; reject if exceeds risk tolerance threshold (TBD) |
| Calmar ratio | CAGR / max drawdown; summary of return per unit of drawdown risk |

### Trade quality metrics

| Metric | Notes |
|--------|-------|
| Win rate | % of trades profitable |
| Profit factor | Gross profit / gross loss |
| Average trade return | Mean of individual trade returns (after commission) |
| Median trade return | Less sensitive to outliers than mean |
| Exposure pct | % of bars in a position |
| Trades per month | Reject if too few (not statistically meaningful) or too many (excessive friction) |
| Turnover | Round-trips per period |
| Average holding bars / days | Must align with target holding horizon |

### Execution quality metrics

| Metric | Notes |
|--------|-------|
| Stop-loss frequency | % of exits via stop; high rate suggests parameter sensitivity |
| Session-end / force-exit frequency | % of exits via time guard; indicates strategy is not self-exiting |
| Slippage sensitivity | Re-run with ±0.05% and ±0.1% slippage assumption; result must remain positive |

---

## 6. Acceptance Gates

No single combination may proceed to paper trading based on backtest results
alone. The following gates must all pass:

| Gate | Requirement |
|------|-------------|
| Train/test split | Backtest must be split into in-sample training and held-out test; performance must be non-negative on test |
| Walk-forward stability | At least one rolling walk-forward window must show consistent performance |
| Out-of-sample period | At least one full calendar year held out; never used for parameter selection |
| Post-cost positive | Must remain positive after realistic slippage (≥ 0.05% per round-trip) |
| Not daily 1d session_end artifact | Must not rely on session_end exit on 1d bars (PR 10W guard); use `force_exit_time=None` or Phase 2 fix |
| Not low-volatility Sharpe artifact | `diagnose_sharpe()` must not flag `zero_std_detected=True` or `low_variance_warning=True` on key periods |
| Sufficient trade count | At least 30 trades per in-sample period for any statistical inference |
| Drawdown tolerance | Max drawdown must not exceed operator-defined threshold (set before S2 evaluation begins) |
| Liquidity check | Symbol ADV must comfortably support the target notional; no illiquid instruments |
| No parameter over-fit | Parameters must be validated on a held-out set, not tuned to it |

Only combinations passing all gates may be considered for a future paper
automation approval PR.

---

## 7. Return Target Framing

The 1%–2% average monthly return target is a **research objective**, not a
promise. The following clarifications apply:

- Fixed monthly income is unrealistic. Returns vary month-to-month and year-to-year.
- The target distribution matters: a strategy with 2% mean but 15% standard
  deviation of monthly return is far less desirable than one with 1.2% mean
  and 4% standard deviation.
- A strategy with 2% mean monthly return but 40% max drawdown is rejected
  on risk grounds regardless of return.
- QQQ and SPY may have limited 1–2 day directional movement in low-volatility
  regimes. Broader candidate inclusion (sector ETFs, mega-caps) is justified
  precisely because relying solely on SPY/QQQ may not meet the return target
  under realistic risk constraints.
- The target is a filter, not a floor. A strategy consistently generating
  0.8% per month with low drawdown may be more appropriate than one generating
  2% with erratic behavior.

---

## 8. Data Requirements

| Requirement | Detail |
|-------------|--------|
| Source | Cached historical data only for first pass (existing cache pipeline) |
| Live data dependency | None in S1 or S2 |
| S2 data fetching | Use existing `yahoo_cache_fetch` tool with `--allow-network` gate; follow PR 10F/10G runbook |
| Minimum rows per interval | 15m: ≥ 2000 bars; 30m: ≥ 1000 bars; 60m: ≥ 500 bars; 1d: ≥ 252 bars per year |
| Time split requirement | At least 2 years total; first 60–70% in-sample, remainder held out |
| No raw data committed | Cache files remain in `data/cache/` (gitignored); no raw OHLCV committed |
| No raw prices in output | Aggregate metrics only; no individual fill prices or account values |

---

## 9. Next Implementation PR: S2

S2 should add an **offline candidate evaluation runner**. Suggested structure:

```
src/research/__init__.py
src/research/candidate_universe.py   # defines the matrix; no I/O
src/research/candidate_evaluator.py  # runs backtest per (symbol, interval, strategy)
```

Requirements for S2:
- All evaluation fully offline; no broker, credential, or network access
- Uses existing `run_backtest()` and `trade_summary_diagnostics()`
- Outputs aggregate metrics only; no raw prices or individual trade records
- Applies `diagnose_sharpe()` for low-volatility detection
- Applies train/test split before any result is reported
- Source-scanned for forbidden imports: no Alpaca SDK, no `os.environ`,
  no live tools, no broker adapters
- Tests: matrix generation correctness, safety flags always False, offline-only
- Does not approve paper or live trading

---

## 10. Safety and Direction Guard

| Statement | Status |
|-----------|--------|
| This does not approve paper trading | Confirmed |
| This does not approve live trading | Confirmed |
| This does not alter runtime behavior | Confirmed |
| This does not read credentials | Confirmed |
| This does not call any broker | Confirmed |
| This does not submit any order | Confirmed |
| This is research design only | Confirmed |
| No parameters are optimized here | Confirmed |
| No raw data is committed | Confirmed |

Before opening S2, answer: does this move us toward automated runtime by
narrowing the strategy space? Yes — S2 runner outputs feed the eventual
risk gate rule set.

---

## Warnings

> **This document does not approve automated live trading.**
> **This document does not approve any individual trade.**
> **No code is implemented here.**
> **No Alpaca endpoint is contacted.**
> **No credentials are read.**
> All automated trading requires completing the full staged roadmap with each
> phase reviewed and approved in its own PR.
>
> **Nothing in this repository is financial advice.**
> All trading decisions are made by the operator and are the operator's
> sole responsibility.
