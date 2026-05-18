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
    mode:                                    str          = "backtest"
    dry_run_broker:                          bool         = False
    paper_trading_enabled:                   bool         = False
    paper_order_quantity_override:           float | None = None
    paper_preview_only:                      bool         = True
    paper_selected_client_order_id:          str | None   = None
    paper_close_positions_enabled:           bool         = False
    paper_close_preview_only:                bool         = True
    paper_selected_close_client_order_id:    str | None   = None
    paper_close_quantity_override:           float | None = None
    paper_ledger_path:                       str          = "output/paper_execution_ledger.csv"
    paper_poll_order_status:                 bool         = False
    paper_poll_timeout_seconds:              int          = 30
    paper_poll_interval_seconds:             float        = 1.0
    paper_daily_max_orders:                  int          = 3
    paper_daily_max_buy_orders:              int          = 2
    paper_daily_max_close_orders:            int          = 2
    paper_daily_max_notional:                float | None = None


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
# Paper config validation
# ---------------------------------------------------------------------------

def validate_paper_config(execution: ExecutionConfig) -> None:
    """Validate paper-execution config combinations before any broker logic runs.

    Raises
    ------
    ValueError
        On any unsafe or contradictory combination of paper settings.

    Notes
    -----
    - Does not validate credentials.
    - Does not call Alpaca.
    - Does not change submit behaviour.
    - Safe to call multiple times (pure function).
    """
    if execution.mode != "paper" or not execution.paper_trading_enabled:
        return  # non-paper or disabled paper — nothing to validate here

    # --- Conflict: buy-submit mode + close path active ---
    if execution.paper_close_positions_enabled and not execution.paper_preview_only:
        raise ValueError(
            "Invalid paper config: paper_close_positions_enabled=true and "
            "paper_preview_only=false are mutually exclusive. "
            "The close/flatten path and the buy-submit path cannot both be active. "
            "Set paper_preview_only=true (its default) when "
            "paper_close_positions_enabled=true."
        )

    # --- Buy-submit path: selected ID required ---
    if not execution.paper_close_positions_enabled and not execution.paper_preview_only:
        coid = execution.paper_selected_client_order_id
        if not coid or not str(coid).strip():
            raise ValueError(
                "Invalid paper config: execution.paper_preview_only=false requires "
                "execution.paper_selected_client_order_id to be set to a non-empty value. "
                "Run in preview mode first (paper_preview_only=true) to generate candidates."
            )

    # --- Close-submit path: selected close ID required ---
    if execution.paper_close_positions_enabled and not execution.paper_close_preview_only:
        close_coid = execution.paper_selected_close_client_order_id
        if not close_coid or not str(close_coid).strip():
            raise ValueError(
                "Invalid paper config: execution.paper_close_preview_only=false requires "
                "execution.paper_selected_close_client_order_id to be set to a non-empty "
                "value. Run in close preview mode first (paper_close_preview_only=true) "
                "to generate candidates."
            )

    # --- Quantity overrides must be exactly 1.0 when set ---
    _ov = execution.paper_order_quantity_override
    if _ov is not None:
        if _ov <= 0 or _ov > 1 or _ov != 1.0:
            raise ValueError(
                f"Invalid paper config: execution.paper_order_quantity_override must be "
                f"exactly 1.0, got {_ov}. Only 1.0 is supported."
            )

    _cov = execution.paper_close_quantity_override
    if _cov is not None:
        if _cov <= 0 or _cov > 1 or _cov != 1.0:
            raise ValueError(
                f"Invalid paper config: execution.paper_close_quantity_override must be "
                f"exactly 1.0, got {_cov}. Only 1.0 is supported."
            )


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
    _override_raw               = ex.get("paper_order_quantity_override", None)
    _selected_coid_raw          = ex.get("paper_selected_client_order_id", None)
    _close_qty_override_raw     = ex.get("paper_close_quantity_override", None)
    _selected_close_coid_raw    = ex.get("paper_selected_close_client_order_id", None)
    execution_cfg = ExecutionConfig(
        mode=raw_mode,
        dry_run_broker=bool(ex.get("dry_run_broker", False)),
        paper_trading_enabled=bool(ex.get("paper_trading_enabled", False)),
        paper_order_quantity_override=float(_override_raw) if _override_raw is not None else None,
        paper_preview_only=bool(ex.get("paper_preview_only", True)),
        paper_selected_client_order_id=str(_selected_coid_raw) if _selected_coid_raw is not None else None,
        paper_close_positions_enabled=bool(ex.get("paper_close_positions_enabled", False)),
        paper_close_preview_only=bool(ex.get("paper_close_preview_only", True)),
        paper_selected_close_client_order_id=str(_selected_close_coid_raw) if _selected_close_coid_raw is not None else None,
        paper_close_quantity_override=float(_close_qty_override_raw) if _close_qty_override_raw is not None else None,
        paper_ledger_path=str(ex.get("paper_ledger_path", "output/paper_execution_ledger.csv")),
        paper_poll_order_status=bool(ex.get("paper_poll_order_status", False)),
        paper_poll_timeout_seconds=int(ex.get("paper_poll_timeout_seconds", 30)),
        paper_poll_interval_seconds=float(ex.get("paper_poll_interval_seconds", 1.0)),
        paper_daily_max_orders=int(ex.get("paper_daily_max_orders", 3)),
        paper_daily_max_buy_orders=int(ex.get("paper_daily_max_buy_orders", 2)),
        paper_daily_max_close_orders=int(ex.get("paper_daily_max_close_orders", 2)),
        paper_daily_max_notional=float(ex["paper_daily_max_notional"]) if ex.get("paper_daily_max_notional") is not None else None,
    )

    app_cfg = AppConfig(
        backtest=backtest_cfg,
        symbols=list(raw["symbols"]),
        data=data_cfg,
        strategy=strategy_cfg,
        risk=risk_cfg,
        logging=logging_cfg,
        execution=execution_cfg,
    )
    validate_paper_config(app_cfg.execution)
    return app_cfg
