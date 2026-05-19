"""
tools/live_shadow_preflight.py
-------------------------------
Read-only live shadow preflight CLI.

Usage::

    python -m src.tools.live_shadow_preflight \\
        --config config/settings.paper.local.yaml \\
        --output-dir output/live_shadow_preflight

This tool is **not** a live trading submission tool.  It runs the local
strategy preview to produce candidate intents, then reads live Alpaca account
state, and reports whether a hypothetical live submit would be safe.
No order is ever submitted or cancelled.

What it does
------------
1. Loads config and runs the local backtest/strategy pipeline to produce
   candidate order intents — same data path used by paper preview.
2. Filters intents to buy+market orders for the selected symbol.
3. Resolves live credentials from ``ALPACA_LIVE_API_KEY`` and
   ``ALPACA_LIVE_SECRET_KEY`` and opens a live ``TradingClient``
   (``paper=False``).
4. Reads account status, buying power, open positions, and open orders
   from the live account — all read-only.
5. Prints a structured report and exits 0 (PASS) or 1 (WARN / FAIL).

What it never does
------------------
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes a ledger row or any persistent artifact.
* Never reads paper credentials (``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``).
* Never modifies any position, order, or account state.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.live_account_check import (
    _bool,
    _get,
    _make_live_client,
    _normalize_enum_value,
    _resolve_live_credentials,
    _str,
    check_credentials,
)
from src.tools.paper_status import check_config


# ---------------------------------------------------------------------------
# Strategy preview
# ---------------------------------------------------------------------------

def _run_strategy_preview(cfg: Any, symbol: str) -> list:
    """Run the local backtest engine and return buy+market intents for *symbol*.

    This is a pure local operation — no credentials, no network (unless the
    data provider fetches prices, which is identical to the paper preview path).
    """
    from src.main import build_engine
    engine = build_engine(cfg)
    results = engine.run()
    intents = results.get("order_intents", [])
    return [
        i for i in intents
        if i.symbol == symbol and i.side == "buy" and i.order_type == "market"
    ]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _result(label: str, status: str, detail: str = "") -> dict[str, Any]:
    return {"label": label, "status": status, "detail": detail}


def check_candidates(intents: list, symbol: str) -> dict[str, Any]:
    """Verify at least one buy+market candidate exists for *symbol*.

    Returns PASS or FAIL (no candidates).  Quantity validation is handled
    separately by check_live_sizing.
    """
    if not intents:
        return _result(
            "candidates", "FAIL",
            f"no buy+market candidate intents for {symbol} — strategy produced no signal",
        )
    return _result(
        "candidates", "PASS",
        f"{len(intents)} buy+market candidate(s) for {symbol}",
    ) | {"candidate_count": len(intents)}


def _get_entry_price(intent: Any) -> float | None:
    """Extract entry_price from intent.metadata, handling dict and attribute forms."""
    metadata = getattr(intent, "metadata", {})
    if isinstance(metadata, dict):
        raw = metadata.get("entry_price")
    else:
        raw = getattr(metadata, "entry_price", None)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _sizing_params(cfg: Any) -> tuple:
    """Return sizing parameters from cfg as a 6-tuple.

    Returns (mode, max_qty, max_not, override, notional_override, max_order_not)
    where mode is "quantity" or "notional".
    """
    ex = cfg.execution
    mode              = str(getattr(ex, "live_sizing_mode",              "quantity"))
    max_qty           = float(getattr(ex, "live_max_quantity",            1.0))
    max_not           = getattr(ex, "live_max_notional",          500.0)
    override          = getattr(ex, "live_quantity_override",      1.0)
    notional_override = getattr(ex, "live_order_notional_override", None)
    max_order_not     = float(getattr(ex, "live_max_order_notional",     100.0))
    if max_not           is not None: max_not           = float(max_not)
    if override          is not None: override          = float(override)
    if notional_override is not None: notional_override = float(notional_override)
    return mode, max_qty, max_not, override, notional_override, max_order_not


def _size_candidate(intent: Any, cfg: Any) -> dict[str, Any]:
    """Compute sizing for one candidate intent; returns a CSV-ready row dict.

    Supports two modes controlled by cfg.execution.live_sizing_mode:
      "quantity" (default) — existing behaviour: quantity-based sizing.
      "notional"           — notional-based sizing via live_order_notional_override.
    """
    mode, max_qty, max_not, override, notional_override, max_order_not = _sizing_params(cfg)

    original_qty = float(getattr(intent, "quantity", 1.0))
    entry_price  = _get_entry_price(intent)
    issues: list[str] = []

    effective_qty:      float | None
    effective_notional: float | None
    estimated_notional: float | None

    if mode == "notional":
        if notional_override is None or notional_override <= 0:
            issues.append(
                f"live_sizing_mode=notional requires live_order_notional_override > 0 "
                f"(got {notional_override})"
            )
            effective_notional = None
            estimated_notional = None
            effective_qty      = None
        else:
            effective_notional = notional_override
            estimated_notional = effective_notional

            if effective_notional > max_order_not:
                issues.append(
                    f"effective_notional={effective_notional:.2f} > "
                    f"live_max_order_notional={max_order_not}"
                )
            if max_not is not None and effective_notional > max_not:
                issues.append(
                    f"effective_notional={effective_notional:.2f} > live_max_notional={max_not}"
                )
            if entry_price is None or entry_price <= 0:
                issues.append(
                    f"notional mode requires entry_price > 0 (got {entry_price}) — fail closed"
                )
                effective_qty = None
            else:
                effective_qty = effective_notional / entry_price
    else:
        # quantity mode — existing behaviour
        effective_qty      = override if override is not None else original_qty
        estimated_notional = None

        if effective_qty > max_qty:
            issues.append(f"effective_quantity={effective_qty} > live_max_quantity={max_qty}")

        if max_not is not None:
            if entry_price is None:
                issues.append(
                    f"live_max_notional={max_not} set but entry_price missing — fail closed"
                )
            else:
                estimated_notional = effective_qty * entry_price
                if estimated_notional > max_not:
                    issues.append(
                        f"estimated_notional={estimated_notional:.2f} > live_max_notional={max_not}"
                    )

        if entry_price is not None and max_not is None:
            estimated_notional = effective_qty * entry_price

        effective_notional = estimated_notional  # alias in quantity mode

    cid = getattr(intent, "client_order_id", None)

    return {
        "client_order_id":        str(cid) if cid is not None else "",
        "symbol":                 str(getattr(intent, "symbol", "")),
        "side":                   str(getattr(intent, "side", "")),
        "order_type":             str(getattr(intent, "order_type", "")),
        "live_sizing_mode":       mode,
        "original_quantity":      original_qty,
        "effective_quantity":     effective_qty,
        "entry_price":            entry_price,
        "effective_notional":     effective_notional,
        "estimated_notional":     estimated_notional,   # backward-compat alias
        "live_max_quantity":      max_qty,
        "live_max_notional":      max_not,
        "live_max_order_notional": max_order_not,
        "sizing_status":          "FAIL" if issues else "PASS",
        "sizing_reason":          "; ".join(issues) if issues else "within limits",
    }


def check_live_sizing(intents: list, cfg: Any) -> dict[str, Any]:
    """Evaluate hypothetical live order sizing against configured limits.

    In "quantity" mode (default): computes effective_quantity = live_quantity_override
    (if set) else original_quantity, then checks against live_max_quantity and
    live_max_notional.

    In "notional" mode: uses live_order_notional_override as the notional, checks
    against live_max_order_notional and live_max_notional, estimates effective_quantity
    = effective_notional / entry_price; fails closed if entry_price missing or <= 0.

    Returns PASS or FAIL with sizing detail fields (from the first candidate).
    """
    mode, max_qty, max_not, override, notional_override, max_order_not = _sizing_params(cfg)

    all_issues: list[str] = []
    for intent in intents:
        row = _size_candidate(intent, cfg)
        if row["sizing_status"] == "FAIL":
            all_issues.append(row["sizing_reason"])

    first_row = _size_candidate(intents[0], cfg) if intents else {}

    original_qty       = first_row.get("original_quantity",   0.0)
    effective_qty      = first_row.get("effective_quantity")
    entry_price        = first_row.get("entry_price")
    estimated_notional = first_row.get("estimated_notional")
    effective_notional = first_row.get("effective_notional")

    extra = {
        "live_sizing_mode":       mode,
        "original_quantity":      original_qty,
        "effective_quantity":     effective_qty,
        "entry_price":            entry_price,
        "effective_notional":     effective_notional,
        "estimated_notional":     estimated_notional,   # backward-compat alias
        "live_max_quantity":      max_qty,
        "live_max_notional":      max_not,
        "live_max_order_notional": max_order_not,
    }

    detail = (
        f"live_sizing_mode={mode} original_quantity={original_qty} "
        f"effective_quantity={effective_qty} entry_price={entry_price} "
        f"effective_notional={effective_notional} estimated_notional={estimated_notional} "
        f"live_max_quantity={max_qty} live_max_notional={max_not} "
        f"live_max_order_notional={max_order_not}"
    )

    if all_issues:
        return _result("live_sizing", "FAIL", detail + " — " + "; ".join(all_issues)) | extra
    return _result("live_sizing", "PASS", detail) | extra


def check_live_account(client: Any) -> dict[str, Any]:
    """Read live account state.

    Returns PASS, WARN (buying_power or portfolio_value is 0 on otherwise
    healthy account), or FAIL (inactive, blocked, or API error).
    """
    try:
        raw = client.get_account()
    except Exception as exc:
        return _result("live_account", "FAIL", f"get_account() failed — {exc}")

    status          = _normalize_enum_value(_get(raw, "status", "unknown"))
    trading_blocked = _bool(raw, "trading_blocked")
    account_blocked = _bool(raw, "account_blocked")
    buying_power    = _str(raw, "buying_power", "0")
    portfolio_value = _str(raw, "portfolio_value", "0")

    detail = (
        f"status={status} trading_blocked={trading_blocked} "
        f"account_blocked={account_blocked} "
        f"buying_power={buying_power} portfolio_value={portfolio_value}"
    )

    issues: list[str] = []
    if status != "active":
        issues.append(f"account status={status!r} (expected 'active')")
    if trading_blocked:
        issues.append("trading_blocked=true")
    if account_blocked:
        issues.append("account_blocked=true")

    if issues:
        return _result("live_account", "FAIL", detail) | {
            "issues": issues,
            "account_status": status,
            "trading_blocked": trading_blocked,
            "account_blocked": account_blocked,
            "buying_power": buying_power,
            "portfolio_value": portfolio_value,
        }

    # Warn if account is healthy but buying power or portfolio value is zero.
    warn_issues: list[str] = []
    try:
        if float(buying_power) == 0:
            warn_issues.append("buying_power=0")
    except (ValueError, TypeError):
        pass
    try:
        if float(portfolio_value) == 0:
            warn_issues.append("portfolio_value=0")
    except (ValueError, TypeError):
        pass

    if warn_issues:
        return _result("live_account", "WARN", detail + " — " + ", ".join(warn_issues)) | {
            "account_status": status,
            "trading_blocked": trading_blocked,
            "account_blocked": account_blocked,
            "buying_power": buying_power,
            "portfolio_value": portfolio_value,
        }

    return _result("live_account", "PASS", detail) | {
        "account_status": status,
        "trading_blocked": trading_blocked,
        "account_blocked": account_blocked,
        "buying_power": buying_power,
        "portfolio_value": portfolio_value,
    }


def check_live_position(client: Any, symbol: str) -> dict[str, Any]:
    """Return FAIL if a live open position exists for *symbol*, PASS otherwise."""
    try:
        if hasattr(client, "get_all_positions"):
            positions = client.get_all_positions()
        elif hasattr(client, "get_positions"):
            positions = client.get_positions()
        else:
            return _result(
                "live_position", "FAIL",
                "client exposes neither get_all_positions nor get_positions",
            )
    except Exception as exc:
        return _result("live_position", "FAIL", f"position lookup failed — {exc}")

    symbol_positions = [
        p for p in (positions or []) if _str(p, "symbol") == symbol
    ]
    if symbol_positions:
        return _result(
            "live_position", "FAIL",
            f"existing live position in {symbol} — close it before a live submit",
        ) | {"position_for_symbol": True}

    return _result("live_position", "PASS", f"no live position in {symbol}") | {
        "position_for_symbol": False,
    }


def check_live_orders(client: Any, symbol: str) -> dict[str, Any]:
    """Return FAIL if a live open order exists for *symbol*, PASS otherwise."""
    try:
        if hasattr(client, "get_orders"):
            orders = client.get_orders()
        elif hasattr(client, "list_orders"):
            orders = client.list_orders()
        else:
            return _result(
                "live_orders", "FAIL",
                "client exposes neither get_orders nor list_orders",
            )
    except Exception as exc:
        return _result("live_orders", "FAIL", f"order lookup failed — {exc}")

    symbol_orders = [
        o for o in (orders or []) if _str(o, "symbol") == symbol
    ]
    if symbol_orders:
        return _result(
            "live_orders", "FAIL",
            f"existing live open order(s) for {symbol} — cancel before a live submit",
        ) | {"open_orders_for_symbol": True}

    return _result("live_orders", "PASS", f"no live open orders for {symbol}") | {
        "open_orders_for_symbol": False,
    }


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

_CSV_COLUMNS = [
    "client_order_id", "symbol", "side", "order_type",
    "live_sizing_mode",
    "original_quantity", "effective_quantity", "entry_price",
    "effective_notional", "estimated_notional",
    "live_max_quantity", "live_max_notional", "live_max_order_notional",
    "sizing_status", "sizing_reason",
]


def write_report(
    output_dir: Path,
    symbol: str,
    config_path: str,
    checks: list[dict[str, Any]],
    intents: list,
    cfg: Any,
    final_status: str,
) -> tuple[Path, Path]:
    """Write JSON report and CSV candidates to *output_dir*.

    Returns (json_path, csv_path).  Never touches Alpaca; no ledger writes.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- build lookup helpers ---
    by_label: dict[str, dict] = {c["label"]: c for c in checks}

    def _field(label: str, key: str, default: Any = None) -> Any:
        return by_label.get(label, {}).get(key, default)

    # --- JSON ---
    report: dict[str, Any] = {
        "checked_at_utc":         datetime.now(timezone.utc).isoformat(),
        "selected_symbol":        symbol,
        "final_status":           final_status,
        "config_path":            str(config_path),
        "account_status":         _field("live_account", "account_status"),
        "trading_blocked":        _field("live_account", "trading_blocked"),
        "account_blocked":        _field("live_account", "account_blocked"),
        "buying_power":           _field("live_account", "buying_power"),
        "portfolio_value":        _field("live_account", "portfolio_value"),
        "position_for_symbol":    _field("live_position", "position_for_symbol"),
        "open_orders_for_symbol": _field("live_orders",   "open_orders_for_symbol"),
        "candidate_count":        _field("candidates",    "candidate_count", 0),
        "sizing_summary":         {
            k: v for k, v in by_label.get("live_sizing", {}).items()
            if k in ("status", "detail", "live_sizing_mode",
                     "original_quantity", "effective_quantity",
                     "entry_price", "effective_notional", "estimated_notional",
                     "live_max_quantity", "live_max_notional", "live_max_order_notional")
        } if "live_sizing" in by_label else None,
        "checks": [
            {k: v for k, v in c.items() if k != "rows"}
            for c in checks
        ],
    }

    json_path = output_dir / "live_shadow_preflight_report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # --- CSV ---
    csv_path = output_dir / "live_shadow_candidates.csv"
    rows = [_size_candidate(i, cfg) for i in intents] if cfg is not None else []
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in _CSV_COLUMNS})

    return json_path, csv_path


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(
    checks: list[dict[str, Any]],
    symbol: str,
    final_status: str,
) -> None:
    """Print a human-readable live shadow preflight report."""
    _ICON = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    print("\n=== Live Shadow Preflight ===")
    print(f"  selected_symbol : {symbol}")

    for c in checks:
        if c["label"] == "candidates":
            print(f"  candidate_count        : {c.get('candidate_count', '?')}")
        if c["label"] == "live_sizing":
            print(f"  live_sizing_mode       : {c.get('live_sizing_mode', '?')}")
            print(f"  original_quantity      : {c.get('original_quantity', '?')}")
            print(f"  effective_quantity     : {c.get('effective_quantity', '?')}")
            print(f"  entry_price            : {c.get('entry_price', '?')}")
            print(f"  effective_notional     : {c.get('effective_notional', '?')}")
            print(f"  estimated_notional     : {c.get('estimated_notional', '?')}")
            print(f"  live_max_quantity      : {c.get('live_max_quantity', '?')}")
            print(f"  live_max_notional      : {c.get('live_max_notional', '?')}")
            print(f"  live_max_order_notional: {c.get('live_max_order_notional', '?')}")
        if c["label"] == "live_account" and c["status"] != "FAIL":
            print(f"  account_status         : {c.get('account_status', '?')}")
            print(f"  trading_blocked        : {c.get('trading_blocked', '?')}")
            print(f"  account_blocked        : {c.get('account_blocked', '?')}")
            print(f"  buying_power           : {c.get('buying_power', '?')}")
            print(f"  portfolio_value        : {c.get('portfolio_value', '?')}")
        if c["label"] == "live_position":
            print(f"  position_for_symbol    : {c.get('position_for_symbol', '?')}")
        if c["label"] == "live_orders":
            print(f"  open_orders_for_symbol : {c.get('open_orders_for_symbol', '?')}")

    print()
    for c in checks:
        icon   = _ICON.get(c["status"], f"[{c['status']}]")
        detail = f"  ({c['detail']})" if c.get("detail") else ""
        print(f"  {icon} {c['label']}{detail}")
        if c.get("issues"):
            for issue in c["issues"]:
                print(f"         ! {issue}")

    print()
    print(f"  RESULT: {final_status}")
    print("=" * 30)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_shadow_preflight",
        description=(
            "Read-only live shadow preflight. "
            "Runs strategy preview locally and checks live account state. "
            "Never submits or cancels orders. "
            "Requires ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY."
        ),
    )
    parser.add_argument("--config",     required=True, help="Path to settings YAML")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--symbol",     default="SPY",  help="Symbol to check (default: SPY)")
    parser.add_argument(
        "--write-report",
        action="store_true",
        default=False,
        help=(
            "Write live_shadow_preflight_report.json and live_shadow_candidates.csv "
            "to --output-dir after all checks complete."
        ),
    )
    args = parser.parse_args(argv)

    symbol     = args.symbol.upper().strip()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []
    all_statuses: list[str] = []

    # --- 1. Config ---
    config_result, cfg = check_config(Path(args.config))
    checks.append(config_result)
    all_statuses.append(config_result["status"])

    # --- 2. Strategy preview (local, no credentials) ---
    intents: list = []
    if cfg is not None:
        try:
            intents = _run_strategy_preview(cfg, symbol)
        except Exception as exc:
            checks.append(_result("strategy_preview", "FAIL", f"engine error — {exc}"))
            all_statuses.append("FAIL")

    if cfg is not None and not any(c["label"] == "strategy_preview" for c in checks):
        cand_result = check_candidates(intents, symbol)
        checks.append(cand_result)
        all_statuses.append(cand_result["status"])

        if cand_result["status"] != "FAIL":
            sizing_result = check_live_sizing(intents, cfg)
            checks.append(sizing_result)
            all_statuses.append(sizing_result["status"])

    # --- 3. Live credentials ---
    cred_result, creds = check_credentials()
    checks.append(cred_result)
    all_statuses.append(cred_result["status"])

    # --- 4–6. Live account checks (only when credentials resolve) ---
    client = None
    if creds is not None:
        try:
            client = _make_live_client(*creds)
        except Exception as exc:
            checks.append(_result("live_client", "FAIL", f"TradingClient creation failed — {exc}"))
            all_statuses.append("FAIL")

    if client is not None:
        acct_result = check_live_account(client)
        checks.append(acct_result)
        all_statuses.append(acct_result["status"])

        pos_result = check_live_position(client, symbol)
        checks.append(pos_result)
        all_statuses.append(pos_result["status"])

        ord_result = check_live_orders(client, symbol)
        checks.append(ord_result)
        all_statuses.append(ord_result["status"])

    # --- Final status ---
    if "FAIL" in all_statuses:
        final_status = "FAIL"
    elif "WARN" in all_statuses:
        final_status = "WARN"
    else:
        final_status = "PASS"

    print_report(checks, symbol=symbol, final_status=final_status)

    if args.write_report:
        json_path, csv_path = write_report(
            output_dir=output_dir,
            symbol=symbol,
            config_path=args.config,
            checks=checks,
            intents=intents,
            cfg=cfg,
            final_status=final_status,
        )
        print(f"  report : {json_path}")
        print(f"  csv    : {csv_path}")

    if final_status != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
