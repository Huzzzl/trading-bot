"""
config/loader.py
----------------
Loads and validates the YAML configuration file, exposing a single
`AppConfig` dataclass that the rest of the application consumes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Dataclasses — one per config section
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    start_date: str
    end_date: str
    initial_capital: float
    commission_per_share: float
    slippage_per_share: float


@dataclass
class DataConfig:
    provider: str
    bar_interval: str
    timezone: str
    cache_enabled: bool = True
    cache_dir: str = "data/cache"


@dataclass
class StrategyConfig:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


_VALID_DAILY_LOSS_ACTIONS = {"block_new_entries", "close_all"}
_VALID_EXECUTION_MODES    = {"backtest", "paper"}


@dataclass
class RiskConfig:
    max_open_positions: int | None = None
    daily_loss_limit_pct: float | None = None
    daily_loss_action: str = "block_new_entries"


@dataclass
class ExecutionConfig:
    mode:            str  = "backtest"
    dry_run_broker:  bool = False


@dataclass
class LoggingConfig:
    level: str
    format: str


@dataclass
class AppConfig:
    backtest: BacktestConfig
    symbols: list[str]
    data: DataConfig
    strategy: StrategyConfig
    risk: RiskConfig
    logging: LoggingConfig
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load and parse *config/settings.yaml* into an :class:`AppConfig`.

    Parameters
    ----------
    config_path:
        Explicit path to the YAML file.  When *None* the function looks for
        ``config/settings.yaml`` relative to the repository root (two levels
        above this file).

    Returns
    -------
    AppConfig
        Fully-populated configuration object.

    Raises
    ------
    FileNotFoundError
        When the config file cannot be located.
    KeyError / TypeError
        When a required key is missing or has the wrong type.
    """
    if config_path is None:
        # Resolve relative to repo root: src/config/loader.py → ../../config/
        here = Path(__file__).resolve().parent          # src/config/
        repo_root = here.parent.parent                   # trading-bot/
        config_path = repo_root / "config" / "settings.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    bt = raw["backtest"]
    backtest_cfg = BacktestConfig(
        start_date=bt["start_date"],
        end_date=bt["end_date"],
        initial_capital=float(bt["initial_capital"]),
        commission_per_share=float(bt["commission_per_share"]),
        slippage_per_share=float(bt["slippage_per_share"]),
    )

    d = raw["data"]
    data_cfg = DataConfig(
        provider=d["provider"],
        bar_interval=d["bar_interval"],
        timezone=d["timezone"],
        cache_enabled=bool(d.get("cache_enabled", True)),
        cache_dir=str(d.get("cache_dir", "data/cache")),
    )

    s = raw["strategy"]
    strategy_cfg = StrategyConfig(
        name=s["name"],
        params=s.get("params", {}),
    )

    r = raw.get("risk", {})
    raw_action = r.get("daily_loss_action", "block_new_entries")
    if raw_action not in _VALID_DAILY_LOSS_ACTIONS:
        raise ValueError(
            f"Invalid daily_loss_action={raw_action!r}. "
            f"Must be one of {sorted(_VALID_DAILY_LOSS_ACTIONS)}."
        )
    risk_cfg = RiskConfig(
        max_open_positions=r.get("max_open_positions", None),
        daily_loss_limit_pct=r.get("daily_loss_limit_pct") or None,
        daily_loss_action=raw_action,
    )

    lg = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        level=lg.get("level", "INFO"),
        format=lg.get("format", "%(asctime)s | %(levelname)s | %(message)s"),
    )

    ex = raw.get("execution", {})
    raw_mode = ex.get("mode", "backtest")
    if raw_mode not in _VALID_EXECUTION_MODES:
        raise ValueError(
            f"Invalid execution.mode={raw_mode!r}. "
            f"Must be one of {sorted(_VALID_EXECUTION_MODES)}."
        )
    execution_cfg = ExecutionConfig(
        mode=raw_mode,
        dry_run_broker=bool(ex.get("dry_run_broker", False)),
    )

    return AppConfig(
        backtest=backtest_cfg,
        symbols=list(raw["symbols"]),
        data=data_cfg,
        strategy=strategy_cfg,
        risk=risk_cfg,
        logging=logging_cfg,
        execution=execution_cfg,
    )
