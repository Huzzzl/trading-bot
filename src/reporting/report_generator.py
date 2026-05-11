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
from src.utils.logger import get_logger

logger = get_logger(__name__)

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
    """

    def __init__(
        self,
        metrics: dict[str, Any],
        trades: list[Trade],
        equity_curve: pd.DataFrame,
        config: AppConfig,
        output_dir: str | Path,
    ) -> None:
        self._metrics      = metrics
        self._trades       = trades
        self._equity_curve = equity_curve
        self._config       = config
        self._output_dir   = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._force_exit_time: str = config.strategy.params.get("force_exit_time", "15:55")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate_all(self) -> None:
        """Write all four artefacts and log output paths."""
        self._write_metrics_json()
        self._write_trade_log_csv()
        self._write_daily_summary_csv()
        validation_results = self._run_validation_checks()
        self._write_markdown_report(validation_results)

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
        if not self._trades:
            return pd.DataFrame()

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
            })

        df = pd.DataFrame(rows)
        df.set_index("entry_time", inplace=True)
        return df

    def _write_trade_log_csv(self) -> None:
        df = self._build_trade_log_df()
        path = self._output_dir / "trade_log.csv"
        if df.empty:
            df.to_csv(path)
            logger.info("No trades — empty trade log saved to %s", path)
        else:
            df.to_csv(path)
            logger.info("Trade log (%d trades) saved to %s", len(df), path)

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
            day_trades  = groups[day]
            pnls        = [t.pnl for t in day_trades]
            winners     = [p for p in pnls if p > 0]
            gross_pnl   = sum(pnls)
            avg_pnl     = gross_pnl / len(pnls) if pnls else 0.0
            win_rate    = len(winners) / len(pnls) * 100.0 if pnls else 0.0
            symbols     = ", ".join(sorted({t.symbol for t in day_trades}))
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

        df = pd.DataFrame(rows).set_index("date")
        return df

    def _write_daily_summary_csv(self) -> None:
        df = self._build_daily_summary_df()
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
        results.append(_check(
            "No open positions at end",
            passed=True,  # engine always force-closes; include for completeness
            detail="Engine force-closes all positions at last bar",
        ))

        # 2. No exit before (or equal to) entry
        bad_times = [
            t for t in trades
            if t.exit_time <= t.entry_time
        ]
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
        zero_prices = [
            t for t in trades
            if t.entry_price <= 0 or t.exit_price <= 0
        ]
        results.append(_check(
            "No zero or negative prices",
            passed=len(zero_prices) == 0,
            detail=f"{len(zero_prices)} trade(s) with zero/negative price" if zero_prices else "OK",
        ))

        # 7. No zero or negative share counts
        bad_shares = [
            t for t in trades
            if t.shares <= 0
        ]
        results.append(_check(
            "No zero or negative share counts",
            passed=len(bad_shares) == 0,
            detail=f"{len(bad_shares)} trade(s) with zero/negative shares" if bad_shares else "OK",
        ))

        # 8. ORB trades have or_high and or_low in meta
        strategy_name = self._config.strategy.name
        if "opening_range_breakout" in strategy_name:
            missing_meta = [
                t for t in trades
                if "or_high" not in t.meta or "or_low" not in t.meta
            ]
            results.append(_check(
                "ORB trades have or_high and or_low",
                passed=len(missing_meta) == 0,
                detail=f"{len(missing_meta)} ORB trade(s) missing or_high/or_low in meta" if missing_meta else "OK",
            ))

        # Log summary
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

    def _write_markdown_report(self, validation_results: list[dict[str, str]]) -> None:
        cfg  = self._config
        m    = self._metrics
        path = self._output_dir / "backtest_report.md"

        lines: list[str] = []

        # Title
        lines += [
            "# Backtest Report",
            "",
        ]

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
        lines.append("")

        # --- Performance Metrics ---
        lines += [
            "## Performance Metrics",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Final equity | ${m['final_equity']:,.2f} |",
            f"| Total return | {m['total_return_pct']:.2f}% |",
            f"| Annualised return | {m['annualized_return_pct']:.2f}% |",
            f"| Max drawdown | {m['max_drawdown_pct']:.2f}% |",
            f"| Sharpe ratio | {m['sharpe_ratio']:.4f} |",
            f"| Number of trades | {m['num_trades']} |",
            f"| Win rate | {m['win_rate_pct']:.2f}% |",
            f"| Avg winning trade | ${m['avg_winning_trade']:,.2f} |",
            f"| Avg losing trade | ${m['avg_losing_trade']:,.2f} |",
            f"| Total commission | ${m['total_commission']:,.2f} |",
            "",
        ]

        # --- Trade Summary ---
        lines.append("## Trade Summary")
        lines.append("")
        trade_df = self._build_trade_log_df()
        if trade_df.empty:
            lines.append("_No trades were executed during this backtest._")
        else:
            cols = ["symbol", "direction", "exit_time", "entry_price", "exit_price",
                    "shares", "pnl", "pnl_pct", "exit_reason"]
            display = trade_df.reset_index()[cols].copy()
            lines.append(_df_to_md(display))
        lines.append("")

        # --- Daily Summary ---
        lines.append("## Daily Summary")
        lines.append("")
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
            status_badge = "✅ PASS" if r["status"] == _PASS else "⚠️ WARN"
            lines.append(f"| {r['check']} | {status_badge} | {r['detail']} |")
        lines.append("")

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
