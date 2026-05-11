# Trading Bot — Intraday Backtesting Framework

A modular, look-ahead-bias-free backtesting engine for US equities and ETFs,
starting with an Opening Range Breakout (ORB) strategy on SPY and QQQ.

---

## Project Structure

```
trading-bot/
├── README.md
├── requirements.txt
├── conftest.py                  # pytest/unittest path setup
├── config/
│   └── settings.yaml            # all tuneable parameters live here
├── src/
│   ├── main.py                  # CLI entry point
│   ├── config/
│   │   └── loader.py            # YAML → AppConfig dataclass
│   ├── data/
│   │   ├── base.py              # BaseDataProvider ABC
│   │   └── yahoo_provider.py    # yfinance implementation
│   ├── strategy/
│   │   ├── base.py              # BaseStrategy ABC + Signal dataclass
│   │   └── opening_range_breakout.py
│   ├── backtest/
│   │   ├── engine.py            # bar-by-bar event loop
│   │   ├── metrics.py           # performance statistics
│   │   └── trade.py             # Trade dataclass
│   ├── risk/
│   │   └── risk_manager.py      # pre-entry gates + per-bar exit rules
│   ├── portfolio/
│   │   └── portfolio.py         # cash, positions, equity curve
│   └── utils/
│       └── logger.py
└── tests/
    ├── test_strategy.py         # strategy unit tests (28 tests total)
    └── test_backtest.py         # engine / portfolio / risk integration tests
```

---

## Quickstart

### 1. Install dependencies

```bash
cd trading-bot
pip install -r requirements.txt
```

> **Note:** `yfinance` requires network access to fetch bar data.  
> All unit tests use synthetic data and run offline.

### 2. Run the backtest

```bash
# from the trading-bot/ directory
python -m src.main

# with a custom config
python -m src.main --config config/settings.yaml --output-dir output
```

Output files are written to `output/`:
- `equity_curve.png` — portfolio value over time  
- `trade_log.csv` — one row per completed trade

### 3. Run the tests

```bash
python -m unittest discover -s tests -v
```

All 28 tests run offline (no yfinance calls).

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

symbols: [SPY, QQQ]

strategy:
  name: opening_range_breakout
  params:
    opening_range_start: "09:30"   # US Eastern
    opening_range_end:   "10:00"
    force_exit_time:     "15:55"
    position_size_pct:   0.95
    long_only: true
```

---

## Strategy: Opening Range Breakout

| Rule | Detail |
|------|--------|
| Timezone | US Eastern (all times) |
| Opening range | `[09:30, 10:00)` — first 30 minutes |
| Entry condition | Bar close > `opening_range_high` after 10:00 |
| Direction | Long only (MVP) |
| Stop loss | `opening_range_low` |
| Position size | 95% of available cash, whole shares only |
| Max trades/day | 1 per symbol |
| Force exit | 15:55 Eastern — no overnight positions |
| Commission | Configurable per-share (deducted on entry and exit) |
| Slippage | Configurable per-share (added on buy, subtracted on sell) |

---

## Backtest Metrics

| Metric | Description |
|--------|-------------|
| Total return % | `(final_equity − initial_capital) / initial_capital` |
| Annualised return % | CAGR over the backtest period |
| Max drawdown % | Largest peak-to-trough decline in equity |
| Sharpe ratio | Annualised excess return / std-dev (bar-level, 252 × 78 bars/yr) |
| Win rate % | % of trades with PnL > 0 |
| Avg winning trade | Mean PnL of profitable trades |
| Avg losing trade | Mean PnL of unprofitable trades |
| # Trades | Total completed round-trips |
| Total commission | Sum of all commissions paid |

---

## Architecture Notes

### No look-ahead bias

The engine passes `bars.loc[bars.index <= current_bar]` to the strategy on
every tick. Future data is structurally inaccessible.

### Modular design

| Concern | Owner | Swap-out path |
|---------|-------|---------------|
| Market data | `BaseDataProvider` | Add `AlpacaDataProvider(BaseDataProvider)` |
| Strategy logic | `BaseStrategy` | Add any new strategy by subclassing |
| Risk rules | `RiskManager` | Extend `approve_entry` / `check_exits` |
| Execution | `Portfolio` | Replace with `AlpacaBrokerAdapter` for live trading |

### Loose coupling

Modules communicate only via the public interfaces defined in `base.py` files
and the `Signal` / `Trade` dataclasses. The engine never imports concrete
strategy or data classes directly — those are wired together in `main.py`.

---

## Extending the Bot

### Add a new strategy

```python
# src/strategy/my_strategy.py
from src.strategy.base import BaseStrategy, Signal, SignalDirection

class MyStrategy(BaseStrategy):
    def generate_signal(self, symbol, bars, current_bar):
        ...
        return Signal(direction=SignalDirection.LONG, ...)
```

Then register it in `src/main.py`'s `build_engine()` function.

### Add a new data provider

```python
# src/data/alpaca_provider.py
from src.data.base import BaseDataProvider

class AlpacaDataProvider(BaseDataProvider):
    def fetch_bars(self, symbol, start, end, interval):
        # TODO: call Alpaca Market Data API
        ...
```

---

## Roadmap / TODOs

The codebase contains `# TODO` markers at every Alpaca integration point:

- `src/main.py` — `build_engine()`: swap `YahooDataProvider` → `AlpacaDataProvider`  
- `src/main.py` — `build_engine()`: swap `Portfolio` → `AlpacaBrokerAdapter`  
- `src/risk/risk_manager.py` — `approve_entry()`: add real-time buying-power check  
- `src/data/yahoo_provider.py` — chunked window fetch for long date ranges  

Planned features:
- [ ] Short-side signals
- [ ] Multi-day walk-forward validation
- [ ] Alpaca paper trading integration
- [ ] Parameter optimisation / grid search
- [ ] HTML backtest report

---

## Disclaimer

This project is for educational and research purposes only.  
It does not connect to any real brokerage account and contains no API keys.  
Past backtest performance is not indicative of future results.
