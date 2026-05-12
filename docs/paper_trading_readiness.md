# Paper Trading Readiness Checklist

This document tracks what must be true before `execution.mode = "paper"` can
be safely enabled.  Each section lists concrete requirements and the current
status of this codebase.

**Current overall status: BACKTEST ONLY.**
`execution.mode = "paper"` raises `NotImplementedError`.
No Alpaca integration exists.  No API keys are present.

The Alpaca adapter is **design-only** — see
[docs/alpaca_adapter_design.md](alpaca_adapter_design.md) for the full
specification.  Implementation is still blocked.

---

## 1. Strategy Readiness

- [x] Strategy produces deterministic signals from historical bars
- [x] Opening Range Breakout (ORB) strategy implemented and back-tested
- [x] Entry cutoff time enforced (`entry_cutoff_time`)
- [x] Force-exit time enforced (`force_exit_time`)
- [x] Long-only flag supported
- [ ] Live signal generation from a streaming bar feed (not yet implemented)
- [ ] Signal latency budget defined and measured

---

## 2. Data Readiness

- [x] Yahoo Finance provider for historical bars
- [x] Disk-based bar cache to avoid redundant downloads
- [x] Bar timezone handling (America/New_York)
- [ ] Real-time / low-latency market data feed (not yet implemented)
- [ ] Data staleness detection and reconnection logic
- [ ] Pre-market / post-market bar filtering

---

## 3. Risk Controls

- [x] Per-symbol position sizing (`position_size_pct`)
- [x] Stop-loss exits (bar_close and stop_price modes)
- [x] Force-exit at configurable time
- [x] Max open positions limit (`max_open_positions`)
- [x] Daily loss limit / kill-switch (`daily_loss_limit_pct`, `daily_loss_action`)
- [x] Session-end overnight position guard
- [ ] Maximum order value / notional cap
- [ ] Duplicate order guard (idempotent submission)
- [ ] Position reconciliation against broker state on startup

---

## 4. Execution Safety

- [x] `OrderIntent` abstraction decouples strategy from broker
- [x] `BrokerAdapter` abstract interface defined
- [x] `FakeBrokerAdapter` for offline testing (no network, no API keys)
- [x] Dry-run path: `execution.dry_run_broker = true` submits intents to
      `FakeBrokerAdapter` post-backtest (audit only, does not affect results)
- [x] Order reconciliation: intent/result counts, mismatch detection,
      PASS/WARN status written to `order_reconciliation.json`
- [x] `execution.mode = "paper"` raises `NotImplementedError` — paper trading
      is explicitly blocked until all checklist items are satisfied
- [ ] Real broker adapter implemented (Alpaca or equivalent) — **design only, not yet implemented** (see [alpaca_adapter_design.md](alpaca_adapter_design.md))
- [ ] No Alpaca integration — no `ALPACA_API_KEY` or `ALPACA_SECRET_KEY`
- [ ] Order acknowledgement and fill confirmation loop
- [ ] Partial-fill handling
- [ ] Order timeout / cancellation logic

---

## 5. Broker Integration Requirements

> **None of these are implemented yet.  Do not add API keys.**

- [ ] Alpaca Paper Trading API adapter (`AlpacaBrokerAdapter`)
- [ ] Paper account credentials stored in environment variables
      (never in source code or `settings.yaml`)
- [ ] Connection health-check on startup
- [ ] Rate-limit handling and retry with exponential back-off
- [ ] Order status polling or WebSocket event subscription
- [ ] Market-hours guard (reject orders outside RTH unless AH trading enabled)

---

## 6. Monitoring / Logging

- [x] Structured logging via `src/utils/logger.py`
- [x] Per-run output directory with timestamped artefacts
- [x] `backtest_report.md` with validation checks and reconciliation section
- [x] `order_intents.csv` — full intent audit trail
- [x] `order_results.csv` — broker response log (dry-run)
- [x] `order_reconciliation.json` — intent/result diff
- [ ] Real-time P&L dashboard
- [ ] Alerting on daily loss limit breach
- [ ] Dead-man switch: auto-flatten if heartbeat is lost

---

## 7. Manual Go / No-Go Checklist

Before enabling paper trading, a human must confirm all of the following:

| # | Item | Confirmed |
|---|------|-----------|
| 1 | All backtest validation checks PASS for target date range | ☐ |
| 2 | Dry-run reconciliation PASS (no mismatches, no unexpected rejects) | ☐ |
| 3 | Risk parameters reviewed (stop-loss %, daily loss limit, position size) | ☐ |
| 4 | Paper account funded and verified | ☐ |
| 5 | API credentials set in environment variables (not in code) | ☐ |
| 6 | `execution.mode = "paper"` NotImplementedError removed only after adapter is wired | ☐ |
| 7 | Full test suite passes (`pytest`) | ☐ |
| 8 | Code review completed | ☐ |
| 9 | Kill-switch tested manually | ☐ |
| 10 | Monitoring / alerting active | ☐ |

---

## Appendix: Blocked Items

The following are intentionally deferred:

- **Alpaca integration** — design complete ([alpaca_adapter_design.md](alpaca_adapter_design.md)); implementation not yet started; no API keys
- **Paper mode** — `execution.mode = "paper"` raises `NotImplementedError`
- **Live mode** — not planned until paper trading is stable for ≥ 30 days
