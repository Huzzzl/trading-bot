# Trading Bot — Intraday Backtesting Framework

A modular, look-ahead-bias-free backtesting engine for US equities and ETFs.
Supports Opening Range Breakout (ORB) and Trend-Following strategies on SPY and QQQ.
All backtest output is simulated only. No broker connection or API credentials are
required for normal backtest usage.

---

## Project Structure

```
trading-bot/
├── README.md
├── requirements.txt
├── config/
│   └── settings.yaml            # all tuneable parameters
├── src/
│   ├── main.py                  # CLI dispatcher (backtest · candidate-b · sweep · walk-forward)
│   ├── config/
│   │   └── loader.py            # YAML → AppConfig dataclass
│   ├── data/
│   │   ├── base.py              # BaseDataProvider ABC
│   │   ├── yahoo_provider.py    # yfinance implementation
│   │   └── cached_provider.py  # disk-cache layer
│   ├── indicators/
│   │   ├── moving_average.py   # sma(), ema()
│   │   ├── volatility.py       # atr()
│   │   └── trend.py            # rolling_high/low, breakout_above/below
│   ├── analysis/
│   │   └── trend.py            # classify_trend() → TrendState
│   ├── strategy/
│   │   ├── base.py             # BaseStrategy, Signal, SignalDirection
│   │   ├── factory.py          # build_strategy(name, params)
│   │   ├── opening_range_breakout.py  # ORB — legacy/benchmark
│   │   ├── trend_following.py  # TrendFollowing — MVP strategy
│   │   └── signal_engine.py   # Phase A offline signal engine
│   ├── risk/
│   │   ├── risk_manager.py     # pre-entry gates + per-bar exit rules
│   │   └── position_sizer.py  # calculate_shares_by_risk()
│   ├── portfolio/
│   │   └── portfolio.py        # cash, positions, equity curve
│   ├── backtest/
│   │   ├── engine.py           # bar-by-bar event loop (no look-ahead)
│   │   ├── backtest_runner.py  # BacktestRunConfig + run_backtest()
│   │   ├── metrics.py          # interval-aware performance statistics
│   │   └── trade.py            # Trade dataclass
│   ├── reporting/
│   │   ├── report_generator.py # ReportGenerator (CSV + JSON artifacts)
│   │   └── reconciliation.py   # order reconciliation
│   ├── execution/
│   │   ├── broker.py           # BrokerProtocol interface
│   │   ├── fake_broker.py      # FakeBrokerAdapter (test double / dry-run)
│   │   ├── alpaca_broker.py    # AlpacaBrokerAdapter (paper; gated)
│   │   ├── order_intent.py     # OrderIntent dataclass
│   │   └── paper_*.py          # paper-trading guards (gated; fail-closed)
│   ├── experiments/
│   │   ├── sweep_runner.py     # parameter grid search
│   │   └── walk_forward_runner.py  # walk-forward validation
│   ├── tools/
│   │   └── live_*.py           # live-readiness tools (all offline/read-only)
│   └── utils/
│       └── logger.py
└── tests/                       # 4 754 tests — all offline, no real broker calls
```

---

## Quickstart

### 1. Install dependencies

```bash
cd trading-bot
pip install -r requirements.txt
```

> **Note:** `yfinance` requires network access to fetch bar data.
> All unit tests use synthetic data and run fully offline.

### 2. Run the backtest

```bash
# Default backtest (SPY, ORB strategy, dates from config/settings.yaml)
python -m src.main

# Explicit backtest mode
python -m src.main --mode backtest

# Custom config file and output directory
python -m src.main --config config/settings.yaml --output-dir output

# Candidate B — QQQ, 09:45 OR end, close trigger, 50% position size
python -m src.main --mode candidate-b

# Parameter sweep — writes output/experiments.csv
python -m src.main --mode sweep

# Walk-forward validation across time windows
python -m src.main --mode walk-forward
```

Output files are written to `output/` (or the directory specified by `--output-dir`):

| File | Description |
|------|-------------|
| `equity_curve.png` | Portfolio value over time |
| `trade_log.csv` | One row per completed round-trip |
| `metrics.json` | Performance statistics |
| `order_intents.csv` | All generated order intents |

> **Backtest output is simulated only.** No real brokerage account is connected.
> No API credentials are required for any backtest mode.

### 3. Run the tests

```bash
# Targeted suites
python -m pytest tests/test_main_characterization.py   # CLI dispatch characterization
python -m pytest tests/test_backtest_runner.py          # BacktestRunConfig + run_backtest
python -m pytest tests/test_backtest.py                 # engine / portfolio / risk
python -m pytest tests/test_strategy_factory.py         # strategy factory

# Full suite (all 4 754 tests, fully offline)
python -m pytest
```

---

## CLI Modes

| Mode | Command | Description |
|------|---------|-------------|
| `backtest` | `python -m src.main` | Default — single backtest with full reporting |
| `candidate-b` | `python -m src.main --mode candidate-b` | QQQ, 09:45 OR end, close trigger, 50% size |
| `sweep` | `python -m src.main --mode sweep` | Parameter grid search → `experiments.csv` |
| `walk-forward` | `python -m src.main --mode walk-forward` | Rolling walk-forward validation |

### Disabled modes

`--mode live` and `--mode paper` are **not valid CLI options** and are rejected by argparse.

- **Paper trading** is gated via `execution.mode = paper` in config (fail-closed by default;
  requires `execution.paper_trading_enabled = true` explicitly set).
- **Live trading** is not enabled. See `docs/live_readiness_status.md`.

---

## Configuration

Everything tuneable lives in `config/settings.yaml`. Nothing is hardcoded.

```yaml
backtest:
  start_date: "2024-01-01"
  end_date:   "2024-12-31"
  initial_capital: 100000.0
  commission_per_share: 0.005   # USD per share
  slippage_per_share:  0.01     # USD per share

symbols: [SPY]

strategy:
  name: opening_range_breakout   # or: trend_following
  params:
    opening_range_start: "09:30"
    opening_range_end:   "10:00"
    force_exit_time:     "15:55"
    position_size_pct:   0.95
    long_only: true
```

---

## Strategies

### Opening Range Breakout (legacy / benchmark)

| Rule | Detail |
|------|--------|
| Timezone | US Eastern |
| Opening range | `[09:30, 10:00)` — first 30 minutes |
| Entry | Bar close > `opening_range_high` after 10:00 |
| Direction | Long only |
| Stop loss | `opening_range_low` |
| Position size | Configurable `position_size_pct` of cash, whole shares |
| Max trades/day | 1 per symbol |
| Force exit | 15:55 Eastern — no overnight positions |

### Trend Following (MVP)

| Rule | Detail |
|------|--------|
| Trend filter | EMA crossover via `classify_trend()` |
| Entry | Trend `"bullish"` AND close > prior rolling high |
| Exit | Trend `"bearish"` OR close < fast EMA |
| Stop metadata | ATR-based stop written to `Signal.meta` |
| Direction | Long only |

Select a strategy by name in `config/settings.yaml`:

```yaml
strategy:
  name: trend_following   # or: opening_range_breakout
```

The strategy factory (`src/strategy/factory.py`) accepts:
`"opening_range_breakout"`, `"orb"` (alias), `"trend_following"`.

---

## Backtest Metrics

| Metric | Description |
|--------|-------------|
| Total return % | `(final_equity − initial_capital) / initial_capital` |
| Annualised return % | CAGR over the backtest period |
| Max drawdown % | Largest peak-to-trough decline in equity |
| Sharpe ratio | Annualised excess return / std-dev (interval-aware bars/year) |
| Win rate % | % of trades with PnL > 0 |
| Avg winning trade | Mean PnL of profitable trades |
| Avg losing trade | Mean PnL of unprofitable trades |
| # Trades | Total completed round-trips |
| Total commission | Sum of all commissions paid |

Annualisation uses `bars_per_year_for_interval(interval)` — supports `1m`, `5m`, `15m`,
`30m`, `1h`, `1d`, and other standard intervals.

---

## Architecture

### No look-ahead bias

The engine passes only bars up to and including the current bar index to the strategy
on every tick. Future data is structurally inaccessible.

### Dispatcher

`src/main.py` is a thin dispatcher: it reads CLI flags, loads config, and calls the
appropriate runner. It does not construct `BacktestEngine`, `Portfolio`, or `RiskManager`
directly — those are wired inside `src/backtest/backtest_runner.run_backtest()`.

### Module responsibilities

| Module | Responsibility |
|--------|---------------|
| `src/main.py` | CLI parsing, config loading, mode dispatch |
| `src/backtest/backtest_runner.py` | `BacktestRunConfig`, `run_backtest()` |
| `src/backtest/engine.py` | Bar-by-bar simulation (no look-ahead) |
| `src/strategy/factory.py` | Strategy construction by name |
| `src/indicators/` | Pure pandas indicator functions |
| `src/analysis/trend.py` | `classify_trend()` → `TrendState` |
| `src/risk/risk_manager.py` | Pre-entry gates and per-bar exit rules |
| `src/experiments/sweep_runner.py` | Parameter grid search |
| `src/experiments/walk_forward_runner.py` | Walk-forward validation |
| `src/reporting/report_generator.py` | CSV / JSON artifact generation |

### Adding a new strategy

```python
# src/strategy/my_strategy.py
from src.strategy.base import BaseStrategy, Signal, SignalDirection

class MyStrategy(BaseStrategy):
    def generate_signal(self, symbol, bars, current_bar):
        ...
        return Signal(direction=SignalDirection.LONG, ...)
```

Register it in `src/strategy/factory.py` so it can be selected by name in config.

---

## Safety

| Guarantee | How enforced |
|-----------|-------------|
| No live trading | `--mode live` rejected by argparse; live path blocked by config flags |
| No automated paper trading | Paper gate requires explicit `paper_trading_enabled=true` in config |
| No Alpaca SDK at import time | Alpaca imports are lazy (inside functions only) |
| No credentials required | Backtest and all offline modes require zero API keys |
| No order submission in backtest | `BacktestRunResult.broker_calls_made` is always `False` |
| No look-ahead bias | Engine passes only historical bars to strategy |

---

## Disclaimer

This project is for educational and research purposes only.
Backtest output is simulated and does not represent real trading results.
Past backtest performance is not indicative of future results.
No real brokerage account is connected in any backtest or offline mode.
No API keys or credentials are stored in this repository.
Live and paper trading require explicit operator opt-in and are not enabled by default.
Nothing in this repository constitutes financial advice.
