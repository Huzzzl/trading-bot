# ARCHIVED in PR R4c. Historical manual/operator tool. Not part of active automated runtime.
# Moved from src/tools/paper_smoke_check.py. Not importable as src.tools.paper_smoke_check.
"""
tools/paper_smoke_check.py
--------------------------
Offline smoke test for the paper execution workflow.

Usage::

    python -m src.tools.paper_smoke_check \\
        --config config/settings.paper.local.yaml \\
        --output-dir output/paper_smoke_check

    # With replay reconciliation on existing artifacts
    python -m src.tools.paper_smoke_check \\
        --config config/settings.paper.local.yaml \\
        --output-dir output/paper_smoke_check \\
        --replay-dir output/paper_submit_BT000035

What it validates
-----------------
1. Config loads and passes validate_paper_config.
2. Buy preview path: fake broker + fake engine → paper_candidate_intents.csv written.
3. Close preview path: fake broker with SPY position → paper_close_candidate_intents.csv written.
4. (Optional) replay_order_reconciliation on existing artifacts in --replay-dir.

What it never does
------------------
* Never calls submit_order or cancel_order.
* Never places real orders.
* Never reads Alpaca credentials from the environment.
* Never requires network access.
* Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Fake broker infrastructure — no credentials, no network, no orders
# ---------------------------------------------------------------------------

class _FakeSmokeClient:
    """Minimal fake Alpaca client for offline smoke testing.

    Returns a static ACTIVE account with no positions.  Raises immediately on
    any submit or cancel call so tests can assert these are never reached.
    """

    def get_account(self) -> dict[str, Any]:
        return {
            "id":              "smoke-test-account",
            "status":          "ACTIVE",
            "currency":        "USD",
            "cash":            "100000.00",
            "equity":          "100000.00",
            "buying_power":    "100000.00",
            "trading_blocked": False,
            "account_blocked": False,
        }

    def get_all_positions(self) -> list:
        return []

    def submit_order(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "paper_smoke_check: submit_order must not be called during smoke test"
        )

    def cancel_order_by_id(self, order_id: str) -> None:
        raise RuntimeError(
            "paper_smoke_check: cancel_order must not be called during smoke test"
        )

    def cancel_order(self, order_id: str) -> None:
        raise RuntimeError(
            "paper_smoke_check: cancel_order must not be called during smoke test"
        )


class _FakeSmokeClientWithPosition(_FakeSmokeClient):
    """Fake client that reports a 1-share SPY paper position.

    Used by the close-preview smoke check so a candidate is always generated.
    """

    def get_all_positions(self) -> list:
        return [
            {
                "symbol":          "SPY",
                "qty":             "1.0",
                "market_value":    "475.00",
                "avg_entry_price": "470.00",
                "current_price":   "475.00",
                "unrealized_pl":   "5.00",
            }
        ]


class _FakeEngine:
    """Engine substitute: returns a single predictable SPY buy intent.

    Avoids downloading market data while still exercising the CSV-writing
    code path in the buy preview check.
    """

    class _FakePortfolio:
        positions: dict[str, Any] = {}

    _portfolio = _FakePortfolio()

    def run(self) -> dict[str, Any]:
        from src.execution.order_intent import OrderIntent

        intent = OrderIntent(
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            reason="smoke_test_entry",
            timestamp=pd.Timestamp.now(tz="America/New_York"),
            client_order_id="SMOKE-BUY-001",
        )
        return {
            "order_intents": [intent],
            "metrics":       {"total_return": 0.0},
            "trades":        [],
            "equity_curve":  pd.DataFrame(),
        }


# ---------------------------------------------------------------------------
# Individual check functions — each returns a result dict
# ---------------------------------------------------------------------------

def _result(label: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"label": label, "passed": passed, "detail": detail}


def check_config(config_path: Path) -> dict[str, Any]:
    """Load config from *config_path* and run validate_paper_config.

    Returns
    -------
    dict
        ``{"label": ..., "passed": bool, "detail": str}``
    """
    try:
        from src.config.loader import load_config, validate_paper_config
        cfg = load_config(config_path)
        validate_paper_config(cfg.execution)
        return _result(
            "config_load_and_validate", True,
            f"mode={cfg.execution.mode} paper_trading_enabled={cfg.execution.paper_trading_enabled}",
        )
    except Exception as exc:
        return _result("config_load_and_validate", False, str(exc))


def check_buy_preview(cfg: Any, output_dir: Path) -> dict[str, Any]:
    """Simulate the buy preview path with a fake broker and fake engine.

    Uses :class:`_FakeSmokeClient` (no credentials needed) and
    :class:`_FakeEngine` (no market data download).  Writes
    ``paper_candidate_intents.csv`` to *output_dir* via the same code path
    used by the real preview flow.

    Returns
    -------
    dict
        ``{"label": ..., "passed": bool, "detail": str}``
    """
    try:
        from src.execution.alpaca_broker import AlpacaBrokerAdapter

        broker = AlpacaBrokerAdapter(client=_FakeSmokeClient())
        broker.preflight_check(cfg.symbols, allow_existing_positions=True)

        engine = _FakeEngine()
        results = engine.run()
        candidate_intents = results.get("order_intents", [])

        cand_cols = [
            "client_order_id", "timestamp", "symbol", "side",
            "quantity", "order_type", "reason",
        ]
        cand_rows = [
            {
                "client_order_id": i.client_order_id,
                "timestamp":       str(i.timestamp),
                "symbol":          i.symbol,
                "side":            i.side,
                "quantity":        i.quantity,
                "order_type":      i.order_type,
                "reason":          i.reason,
            }
            for i in candidate_intents
        ]
        csv_path = output_dir / "paper_candidate_intents.csv"
        pd.DataFrame(cand_rows, columns=cand_cols).to_csv(csv_path, index=False)

        if not csv_path.exists():
            return _result("buy_preview", False, "paper_candidate_intents.csv not written")

        return _result(
            "buy_preview", True,
            f"{len(candidate_intents)} candidate(s) written → {csv_path.name}",
        )
    except Exception as exc:
        return _result("buy_preview", False, str(exc))


def check_close_preview(cfg: Any, output_dir: Path) -> dict[str, Any]:
    """Simulate the close preview path with a fake broker that holds 1 SPY share.

    Uses :class:`_FakeSmokeClientWithPosition` (no credentials needed).
    Writes ``paper_close_candidate_intents.csv`` to *output_dir*.

    Returns
    -------
    dict
        ``{"label": ..., "passed": bool, "detail": str}``
    """
    try:
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        from src.execution.order_intent import OrderIntent
        from zoneinfo import ZoneInfo

        _EASTERN          = ZoneInfo("America/New_York")
        _CLOSE_SYMBOL     = "SPY"
        _CLOSE_SIDE       = "sell"
        _CLOSE_ORDER_TYPE = "market"
        _CLOSE_REASON     = "paper_close"

        broker    = AlpacaBrokerAdapter(client=_FakeSmokeClientWithPosition())
        preflight = broker.preflight_check(cfg.symbols, allow_existing_positions=True)
        positions = preflight["positions"]

        now_ts   = pd.Timestamp.now(tz=_EASTERN)
        ts_label = now_ts.strftime("%Y%m%d%H%M%S")

        close_candidates: list[OrderIntent] = []
        for sym, pos in positions.items():
            if sym.upper() != _CLOSE_SYMBOL:
                continue
            qty = float(pos.get("qty") or 0.0)
            if qty <= 0:
                continue
            close_candidates.append(OrderIntent(
                symbol=_CLOSE_SYMBOL,
                side=_CLOSE_SIDE,
                quantity=qty,
                order_type=_CLOSE_ORDER_TYPE,
                reason=_CLOSE_REASON,
                timestamp=now_ts,
                client_order_id=f"BC-{ts_label}-{_CLOSE_SYMBOL}",
                metadata={"current_position_qty": qty},
            ))

        cand_cols = [
            "client_order_id", "timestamp", "symbol", "side",
            "quantity", "order_type", "reason", "current_position_qty",
        ]
        cand_rows = [
            {
                "client_order_id":      c.client_order_id,
                "timestamp":            str(c.timestamp),
                "symbol":               c.symbol,
                "side":                 c.side,
                "quantity":             c.quantity,
                "order_type":           c.order_type,
                "reason":               c.reason,
                "current_position_qty": c.metadata.get("current_position_qty"),
            }
            for c in close_candidates
        ]
        csv_path = output_dir / "paper_close_candidate_intents.csv"
        pd.DataFrame(cand_rows, columns=cand_cols).to_csv(csv_path, index=False)

        if not csv_path.exists():
            return _result("close_preview", False, "paper_close_candidate_intents.csv not written")

        return _result(
            "close_preview", True,
            f"{len(close_candidates)} candidate(s) written → {csv_path.name}",
        )
    except Exception as exc:
        return _result("close_preview", False, str(exc))


def check_replay(replay_dir: Path) -> dict[str, Any]:
    """Run offline reconciliation replay on artifacts in *replay_dir*.

    Skipped (with a PASS) if either CSV is absent.

    Returns
    -------
    dict
        ``{"label": ..., "passed": bool, "detail": str}``
    """
    intents_path = replay_dir / "order_intents.csv"
    results_path = replay_dir / "order_results.csv"

    if not intents_path.exists() or not results_path.exists():
        return _result(
            "replay_reconciliation", True,
            f"skipped — artifacts not found in {replay_dir.name}",
        )

    try:
        from src.tools.replay_order_reconciliation import replay

        recon  = replay(replay_dir)
        status = recon.get("overall_status", "UNKNOWN")
        passed = status in ("PASS", "N/A")
        return _result(
            "replay_reconciliation", passed,
            f"overall_status={status} "
            f"intent_count={recon.get('intent_count')} "
            f"result_count={recon.get('result_count')} "
            f"mismatch_count={recon.get('mismatch_count')}",
        )
    except Exception as exc:
        return _result("replay_reconciliation", False, str(exc))


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_summary(checks: list[dict[str, Any]]) -> None:
    """Print a short checklist summary to stdout."""
    print("\n=== Paper Smoke Check ===")
    for c in checks:
        icon   = "[PASS]" if c["passed"] else "[FAIL]"
        detail = f"  ({c['detail']})" if c["detail"] else ""
        print(f"  {icon} {c['label']}{detail}")
    all_passed = all(c["passed"] for c in checks)
    print()
    if all_passed:
        print("  RESULT: PASS")
    else:
        print("  RESULT: FAIL")
    print("=" * 25)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.paper_smoke_check",
        description=(
            "Offline smoke test for the paper execution workflow. "
            "No orders submitted. No Alpaca credentials required."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to paper settings YAML (e.g. config/settings.paper.local.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write smoke check artifacts",
    )
    parser.add_argument(
        "--replay-dir",
        default=None,
        help=(
            "Directory containing order_intents.csv and order_results.csv "
            "for offline reconciliation check (optional)"
        ),
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    output_dir  = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []

    # Check 1: config load + validation
    config_check = check_config(config_path)
    checks.append(config_check)

    if not config_check["passed"]:
        # Cannot run further checks without a valid config
        print_summary(checks)
        sys.exit(1)

    from src.config.loader import load_config
    cfg = load_config(config_path)

    # Check 2: buy preview
    checks.append(check_buy_preview(cfg, output_dir))

    # Check 3: close preview
    checks.append(check_close_preview(cfg, output_dir))

    # Check 4: replay reconciliation (optional)
    if args.replay_dir is not None:
        checks.append(check_replay(Path(args.replay_dir)))

    print_summary(checks)

    if any(not c["passed"] for c in checks):
        sys.exit(1)


if __name__ == "__main__":
    main()
