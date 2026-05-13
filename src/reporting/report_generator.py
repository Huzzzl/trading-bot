"""
reporting/report_generator.py
------------------------------
Generates all post-backtest artefacts: metrics JSON, trade-log CSV,
daily-summary CSV, Markdown report, and a suite of validation checks.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, time
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.trade import Trade
from src.config.loader import AppConfig
from src.execution.broker import OrderResult
from src.execution.order_intent import OrderIntent
from src.reporting.reconciliation import build_reconciliation
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

TRADE_LOG_COLUMNS: list[str] = [
    "symbol",
    "direction",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "shares",
    "pnl",
    "pnl_pct",
    "commission",
    "exit_reason",
    "or_high",
    "or_low",
    "breakout_trigger",
    "trigger_val",
    "stop_execution",
]

# Columns shown in the Markdown Trade Detail table (subset of TRADE_LOG_COLUMNS,
# ordered for ORB audit readability).
_TRADE_DETAIL_COLS: list[str] = [
    "symbol",
    "direction",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "shares",
    "pnl",
    "pnl_pct",
    "exit_reason",
    "or_high",
    "or_low",
    "breakout_trigger",
    "trigger_val",
    "stop_execution",
]

# ---------------------------------------------------------------------------
# Known metrics display spec: (dict_key, display_label, format_string)
# ---------------------------------------------------------------------------

_METRIC_DISPLAY: list[tuple[str, str, str]] = [
    ("final_equity",          "Final equity",      "${:,.2f}"),
    ("total_return_pct",      "Total return",      "{:.2f}%"),
    ("annualized_return_pct", "Annualised return", "{:.2f}%"),
    ("max_drawdown_pct",      "Max drawdown",      "{:.2f}%"),
    ("sharpe_ratio",          "Sharpe ratio",      "{:.4f}"),
    ("num_trades",            "Number of trades",  "{}"),
    ("win_rate_pct",          "Win rate",          "{:.2f}%"),
    ("avg_winning_trade",     "Avg winning trade", "${:,.2f}"),
    ("avg_losing_trade",      "Avg losing trade",  "${:,.2f}"),
    ("total_commission",      "Total commission",  "${:,.2f}"),
]

# ---------------------------------------------------------------------------
# Validation result helpers
# ---------------------------------------------------------------------------

_PASS = "PASS"
_WARN = "WARN"


def _check(label: str, passed: bool, detail: str = "") -> dict[str, str]:
    return {"check": label, "status": _PASS if passed else _WARN, "detail": detail}


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------


class ReportGenerator:
    """Generate all reporting artefacts from a completed backtest.

    Parameters
    ----------
    metrics:
        Dict returned by ``compute_metrics()``.
    trades:
        Completed trade list from ``Portfolio.trades``.
    equity_curve:
        DataFrame from ``Portfolio.equity_curve``.
    config:
        Loaded application configuration.
    output_dir:
        Directory to write all output files.
    open_positions_count:
        Number of positions still open after the backtest ended.  The engine
        normally force-closes everything, so this should be 0.  Pass
        ``len(engine._portfolio.positions)`` after ``engine.run()`` to make
        the validation check real.
    """

    def __init__(
        self,
        metrics: dict[str, Any],
        trades: list[Trade],
        equity_curve: pd.DataFrame,
        config: AppConfig,
        output_dir: str | Path,
        open_positions_count: int = 0,
        order_intents: list[OrderIntent] | None = None,
        order_results: list[OrderResult] | None = None,
    ) -> None:
        self._metrics               = metrics
        self._trades                = trades
        self._equity_curve          = equity_curve
        self._config                = config
        self._output_dir            = Path(output_dir)
        self._open_positions_count  = open_positions_count
        self._order_intents: list[OrderIntent]        = order_intents or []
        self._order_results: list[OrderResult] | None = order_results
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._force_exit_time: str = config.strategy.params.get("force_exit_time", "15:55")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate_all(self) -> None:
        """Write all artefacts and log output paths."""
        self._write_metrics_json()
        self._write_trade_log_csv()
        self._write_daily_summary_csv()
        self._write_order_intents_csv()
        reconciliation: dict | None = None
        if self._order_results is not None:
            self._write_order_results_csv()
            reconciliation = self._build_reconciliation()
            self._write_reconciliation_json(reconciliation)
        validation_results = self._run_validation_checks()
        self._write_markdown_report(validation_results, reconciliation)

    # ------------------------------------------------------------------
    # metrics.json
    # ------------------------------------------------------------------

    def _write_metrics_json(self) -> None:
        path = self._output_dir / "metrics.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self._metrics, fh, indent=2, default=str)
        logger.info("Metrics JSON saved to %s", path)

    # ------------------------------------------------------------------
    # trade_log.csv
    # ------------------------------------------------------------------

    def _build_trade_log_df(self) -> pd.DataFrame:
        """Build the trade-log DataFrame.

        Always returns a DataFrame with the columns defined in
        ``TRADE_LOG_COLUMNS``, indexed by ``entry_time``.  The DataFrame is
        empty (zero rows) when there are no trades, but the schema is stable.
        """
        if not self._trades:
            non_index = [c for c in TRADE_LOG_COLUMNS if c != "entry_time"]
            df = pd.DataFrame(columns=non_index)
            df.index.name = "entry_time"
            return df

        rows: list[dict[str, Any]] = []
        for t in self._trades:
            cost_basis = t.entry_price * t.shares
            pnl_pct    = (t.pnl / cost_basis * 100.0) if cost_basis else 0.0
            rows.append({
                "symbol":           t.symbol,
                "direction":        t.direction,
                "entry_time":       t.entry_time,
                "exit_time":        t.exit_time,
                "entry_price":      t.entry_price,
                "exit_price":       t.exit_price,
                "shares":           t.shares,
                "pnl":              round(t.pnl, 4),
                "pnl_pct":          round(pnl_pct, 4),
                "commission":       t.commission,
                "exit_reason":      t.exit_reason,
                "or_high":          t.meta.get("or_high", ""),
                "or_low":           t.meta.get("or_low", ""),
                "breakout_trigger": t.meta.get("breakout_trigger", ""),
                "trigger_val":      t.meta.get("trigger_val", ""),
                "stop_execution":   t.meta.get("stop_execution", ""),
            })

        df = pd.DataFrame(rows)
        df.set_index("entry_time", inplace=True)
        return df

    def _write_trade_log_csv(self) -> None:
        df   = self._build_trade_log_df()
        path = self._output_dir / "trade_log.csv"
        df.to_csv(path)
        if df.empty:
            logger.info("No trades — empty trade log (header only) saved to %s", path)
        else:
            logger.info("Trade log (%d trades) saved to %s", len(df), path)

    # ------------------------------------------------------------------
    # order_intents.csv
    # ------------------------------------------------------------------

    def _write_order_intents_csv(self) -> None:
        rows: list[dict[str, Any]] = []
        for oi in self._order_intents:
            rows.append({
                "client_order_id": oi.client_order_id,
                "timestamp":   str(oi.timestamp),
                "symbol":      oi.symbol,
                "side":        oi.side,
                "quantity":    oi.quantity,
                "order_type":  oi.order_type,
                "reason":      oi.reason,
                "limit_price": oi.limit_price,
                "stop_price":  oi.stop_price,
                "metadata_json": json.dumps(
                    {k: v for k, v in oi.metadata.items() if v is not None},
                    default=str,
                ),
            })

        columns = [
            "client_order_id", "timestamp", "symbol", "side", "quantity", "order_type",
            "reason", "limit_price", "stop_price", "metadata_json",
        ]
        df   = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
        path = self._output_dir / "order_intents.csv"
        df.to_csv(path, index=False)
        logger.info("Order intents (%d) saved to %s", len(rows), path)

    # ------------------------------------------------------------------
    # Order reconciliation
    # ------------------------------------------------------------------

    def _build_reconciliation(self) -> dict:
        """Compare order_intents with order_results and return a summary dict.

        Delegates to :func:`~src.reporting.reconciliation.build_reconciliation`.
        """
        return build_reconciliation(
            self._order_intents,
            self._order_results or [],
        )

    def _write_reconciliation_json(self, reconciliation: dict) -> None:
        path = self._output_dir / "order_reconciliation.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(reconciliation, fh, indent=2)
        logger.info("Order reconciliation saved to %s", path)

    # ------------------------------------------------------------------
    # order_results.csv
    # ------------------------------------------------------------------

    def _write_order_results_csv(self) -> None:
        columns = [
            "client_order_id", "order_id", "symbol", "side", "quantity", "status",
            "submitted_at", "filled_at", "filled_price", "reason", "metadata_json",
        ]
        rows: list[dict[str, Any]] = []
        for r in self._order_results:
            rows.append({
                "client_order_id": r.client_order_id,
                "order_id":      r.order_id,
                "symbol":        r.symbol,
                "side":          r.side,
                "quantity":      r.quantity,
                "status":        r.status,
                "submitted_at":  str(r.submitted_at),
                "filled_at":     str(r.filled_at) if r.filled_at is not None else None,
                "filled_price":  r.filled_price,
                "reason":        r.reason,
                "metadata_json": json.dumps(dict(r.metadata), default=str),
            })
        df   = pd.DataFrame(rows, columns=columns)
        path = self._output_dir / "order_results.csv"
        df.to_csv(path, index=False)
        logger.info("Order results (%d) saved to %s", len(rows), path)

    # ------------------------------------------------------------------
    # daily_summary.csv
    # ------------------------------------------------------------------

    def _build_daily_summary_df(self) -> pd.DataFrame:
        if not self._trades:
            return pd.DataFrame()

        groups: dict[date, list[Trade]] = defaultdict(list)
        for t in self._trades:
            day = t.exit_time.date() if hasattr(t.exit_time, "date") else t.exit_time.to_pydatetime().date()
            groups[day].append(t)

        rows: list[dict[str, Any]] = []
        for day in sorted(groups):
            day_trades   = groups[day]
            pnls         = [t.pnl for t in day_trades]
            winners      = [p for p in pnls if p > 0]
            gross_pnl    = sum(pnls)
            avg_pnl      = gross_pnl / len(pnls) if pnls else 0.0
            win_rate     = len(winners) / len(pnls) * 100.0 if pnls else 0.0
            symbols      = ", ".join(sorted({t.symbol for t in day_trades}))
            exit_reasons = ", ".join(sorted({t.exit_reason for t in day_trades}))
            rows.append({
                "date":           day.isoformat(),
                "num_trades":     len(day_trades),
                "gross_pnl":      round(gross_pnl, 4),
                "avg_pnl":        round(avg_pnl, 4),
                "win_rate_pct":   round(win_rate, 2),
                "symbols_traded": symbols,
                "exit_reasons":   exit_reasons,
            })

        return pd.DataFrame(rows).set_index("date")

    def _write_daily_summary_csv(self) -> None:
        df   = self._build_daily_summary_df()
        path = self._output_dir / "daily_summary.csv"
        df.to_csv(path)
        logger.info("Daily summary saved to %s", path)

    # ------------------------------------------------------------------
    # Validation checks
    # ------------------------------------------------------------------

    def _run_validation_checks(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        trades  = self._trades

        # 1. No open positions at backtest end
        n = self._open_positions_count
        results.append(_check(
            "No open positions at end",
            passed=n == 0,
            detail=f"{n} position(s) still open at backtest end" if n > 0 else "OK",
        ))

        # 2. No exit before (or equal to) entry
        bad_times = [t for t in trades if t.exit_time <= t.entry_time]
        results.append(_check(
            "Exit time after entry time",
            passed=len(bad_times) == 0,
            detail=f"{len(bad_times)} trade(s) with exit_time <= entry_time" if bad_times else "OK",
        ))

        # 3. No overnight positions unless exit_reason == "session_end"
        overnight = [
            t for t in trades
            if _date_of(t.exit_time) != _date_of(t.entry_time)
            and t.exit_reason != "session_end"
        ]
        results.append(_check(
            "No unexpected overnight positions",
            passed=len(overnight) == 0,
            detail=f"{len(overnight)} trade(s) span midnight without session_end reason" if overnight else "OK",
        ))

        # 4. Force-exit trades exit at or after the configured force_exit_time
        force_exit_t = _parse_hhmm(self._force_exit_time)
        bad_force = [
            t for t in trades
            if t.exit_reason == "force_exit"
            and _time_of(t.exit_time) < force_exit_t
        ]
        results.append(_check(
            f"Force-exit at or after {self._force_exit_time}",
            passed=len(bad_force) == 0,
            detail=f"{len(bad_force)} force_exit trade(s) exited before {self._force_exit_time}" if bad_force else "OK",
        ))

        # 5. Session-end exits occurred on same date as entry
        bad_session = [
            t for t in trades
            if t.exit_reason == "session_end"
            and _date_of(t.exit_time) != _date_of(t.entry_time)
        ]
        results.append(_check(
            "Session-end exits on entry date",
            passed=len(bad_session) == 0,
            detail=f"{len(bad_session)} session_end trade(s) exit on different date than entry" if bad_session else "OK",
        ))

        # 6. No missing / zero prices (entry or exit)
        zero_prices = [t for t in trades if t.entry_price <= 0 or t.exit_price <= 0]
        results.append(_check(
            "No zero or negative prices",
            passed=len(zero_prices) == 0,
            detail=f"{len(zero_prices)} trade(s) with zero/negative price" if zero_prices else "OK",
        ))

        # 7. No zero or negative share counts
        bad_shares = [t for t in trades if t.shares <= 0]
        results.append(_check(
            "No zero or negative share counts",
            passed=len(bad_shares) == 0,
            detail=f"{len(bad_shares)} trade(s) with zero/negative shares" if bad_shares else "OK",
        ))

        # 8. ORB trades have or_high and or_low in meta
        if "opening_range_breakout" in self._config.strategy.name:
            missing_meta = [t for t in trades if "or_high" not in t.meta or "or_low" not in t.meta]
            results.append(_check(
                "ORB trades have or_high and or_low",
                passed=len(missing_meta) == 0,
                detail=f"{len(missing_meta)} ORB trade(s) missing or_high/or_low in meta" if missing_meta else "OK",
            ))

        warns = [r for r in results if r["status"] == _WARN]
        if warns:
            for w in warns:
                logger.warning("VALIDATION WARN — %s: %s", w["check"], w["detail"])
        else:
            logger.info("All %d validation checks passed", len(results))

        return results

    # ------------------------------------------------------------------
    # backtest_report.md
    # ------------------------------------------------------------------

    def _write_markdown_report(
        self,
        validation_results: list[dict[str, str]],
        reconciliation: dict | None = None,
    ) -> None:
        cfg  = self._config
        m    = self._metrics
        path = self._output_dir / "backtest_report.md"

        lines: list[str] = []

        lines += ["# Backtest Report", ""]

        # --- Configuration ---
        lines += [
            "## Configuration",
            "",
            "| Parameter | Value |",
            "| --- | --- |",
            f"| Strategy | `{cfg.strategy.name}` |",
            f"| Symbols | {', '.join(cfg.symbols)} |",
            f"| Start date | {cfg.backtest.start_date} |",
            f"| End date | {cfg.backtest.end_date} |",
            f"| Initial capital | ${cfg.backtest.initial_capital:,.2f} |",
            f"| Commission/share | ${cfg.backtest.commission_per_share:.4f} |",
            f"| Slippage/share | ${cfg.backtest.slippage_per_share:.4f} |",
            f"| Bar interval | {cfg.data.bar_interval} |",
        ]
        for k, v in cfg.strategy.params.items():
            lines.append(f"| strategy.{k} | `{v}` |")
        if cfg.risk.max_open_positions is not None:
            lines.append(f"| Max open positions | {cfg.risk.max_open_positions} |")
        if cfg.risk.daily_loss_limit_pct is not None:
            lines.append(f"| Daily loss limit | {cfg.risk.daily_loss_limit_pct:.2f}% |")
            lines.append(f"| Daily loss action | `{cfg.risk.daily_loss_action}` |")
        lines.append("")

        # --- Performance Metrics ---
        # Render known keys with formatting; fall back to string for unknowns.
        lines += [
            "## Performance Metrics",
            "",
            "| Metric | Value |",
            "| --- | --- |",
        ]
        known_keys: set[str] = set()
        for key, label, fmt in _METRIC_DISPLAY:
            known_keys.add(key)
            val = m.get(key)
            if val is None:
                rendered = "N/A"
            else:
                try:
                    rendered = fmt.format(val)
                except (ValueError, TypeError):
                    rendered = str(val)
            lines.append(f"| {label} | {rendered} |")
        for key, val in m.items():
            if key not in known_keys:
                lines.append(f"| {key} | {val} |")
        lines.append("")

        # --- Trade Summary ---
        lines += ["## Trade Summary", ""]
        trade_df = self._build_trade_log_df()
        if trade_df.empty:
            lines.append("_No trades were executed during this backtest._")
        else:
            trades = self._trades
            pnls   = [t.pnl for t in trades]
            winners = [p for p in pnls if p > 0]
            losers  = [p for p in pnls if p <= 0]

            by_symbol: dict[str, int] = {}
            for t in trades:
                by_symbol[t.symbol] = by_symbol.get(t.symbol, 0) + 1

            by_reason: dict[str, int] = {}
            for t in trades:
                by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1

            best  = max(trades, key=lambda t: t.pnl)
            worst = min(trades, key=lambda t: t.pnl)
            avg_win  = sum(winners) / len(winners) if winners else 0.0
            avg_loss = sum(losers)  / len(losers)  if losers  else 0.0

            symbol_str = ", ".join(f"{sym}: {cnt}" for sym, cnt in sorted(by_symbol.items()))
            reason_str = ", ".join(f"{r}: {cnt}"   for r, cnt  in sorted(by_reason.items()))

            lines += [
                "| Stat | Value |",
                "| --- | --- |",
                f"| Total trades | {len(trades)} |",
                f"| Trades by symbol | {symbol_str} |",
                f"| Trades by exit reason | {reason_str} |",
                f"| Best trade (PnL) | ${best.pnl:,.2f} ({best.symbol}, {_date_of(best.exit_time)}) |",
                f"| Worst trade (PnL) | ${worst.pnl:,.2f} ({worst.symbol}, {_date_of(worst.exit_time)}) |",
                f"| Avg winning trade | ${avg_win:,.2f} |",
                f"| Avg losing trade | ${avg_loss:,.2f} |",
                "",
                "### Trade Detail",
                "",
            ]
            lines.append(_df_to_md(trade_df.reset_index()[_TRADE_DETAIL_COLS]))
        lines.append("")

        # --- Daily Summary ---
        lines += ["## Daily Summary", ""]
        daily_df = self._build_daily_summary_df()
        if daily_df.empty:
            lines.append("_No trades to summarise by day._")
        else:
            lines.append(_df_to_md(daily_df.reset_index()))
        lines.append("")

        # --- Validation Checks ---
        lines += [
            "## Validation Checks",
            "",
            "| Check | Status | Detail |",
            "| --- | --- | --- |",
        ]
        for r in validation_results:
            badge = "✅ PASS" if r["status"] == _PASS else "⚠️ WARN"
            lines.append(f"| {r['check']} | {badge} | {r['detail']} |")
        lines.append("")

        # --- Order Reconciliation (dry-run only) ---
        if reconciliation is not None:
            rc     = reconciliation
            badge  = "✅ PASS" if rc["overall_status"] == "PASS" else "⚠️ WARN"
            lines += [
                "## Order Reconciliation",
                "",
                f"Overall status: **{badge}**",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| Intent count | {rc['intent_count']} |",
                f"| Result count | {rc['result_count']} |",
                f"| Unmatched count | {rc['unmatched_count']} |",
                f"| Filled | {rc['filled_count']} |",
                f"| Accepted | {rc['accepted_count']} |",
                f"| Rejected | {rc['rejected_count']} |",
                f"| Mismatch count | {rc['mismatch_count']} |",
                f"| Missing IDs warn | {rc.get('missing_ids_warn', False)} |",
                "",
            ]

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Markdown report saved to %s", path)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _date_of(ts: pd.Timestamp) -> date:
    if hasattr(ts, "date"):
        return ts.date()
    return ts.to_pydatetime().date()


def _time_of(ts: pd.Timestamp) -> time:
    if hasattr(ts, "time"):
        return ts.time().replace(tzinfo=None)
    return ts.to_pydatetime().time().replace(tzinfo=None)


def _parse_hhmm(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _df_to_md(df: pd.DataFrame) -> str:
    """Convert a DataFrame to a GitHub-flavoured Markdown table string."""
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep    = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows   = [
        "| " + " | ".join(str(v) for v in row) + " |"
        for row in df.itertuples(index=False)
    ]
    return "\n".join([header, sep] + rows)
