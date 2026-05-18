"""
tests/test_paper_order_poller.py
----------------------------------
Offline tests for src/execution/paper_order_poller.py and the polling
wiring in src/main.py.

No real network calls, no Alpaca credentials, no real time.sleep.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers for client mocks
# ---------------------------------------------------------------------------

def _order_response(status: str = "filled") -> mock.MagicMock:
    m = mock.MagicMock()
    m.status = status
    return m


def _poll_client(responses) -> mock.MagicMock:
    """Mock client whose get_order_by_id cycles through *responses*."""
    client = mock.MagicMock()
    client.get_order_by_id.side_effect = list(responses)
    client.submit_order.side_effect  = AssertionError("submit_order must not be called by poller")
    client.cancel_order.side_effect  = AssertionError("cancel_order must not be called by poller")
    return client


def _instant_mono(start: float = 0.0, timeout: int = 30) -> callable:
    """Return a monotonic function that never triggers the timeout."""
    values = iter([start] + [start + 1] * 100)
    return lambda: next(values)


def _immediate_timeout_mono(start: float = 0.0, timeout: int = 30) -> callable:
    """Return a monotonic function that triggers timeout on the first loop check."""
    # First call sets deadline = start + timeout.
    # Second call returns start + timeout + 1 → deadline already passed.
    values = iter([start, start + timeout + 1])
    return lambda: next(values)


# ---------------------------------------------------------------------------
# _normalize_status
# ---------------------------------------------------------------------------

class TestNormalizeStatus:
    @pytest.mark.parametrize("raw,expected", [
        ("filled",                "filled"),
        ("FILLED",                "filled"),
        ("partially_filled",      "filled"),
        ("OrderStatus.FILLED",    "filled"),
        ("new",                   "accepted"),
        ("pending_new",           "accepted"),
        ("accepted",              "accepted"),
        ("OrderStatus.NEW",       "accepted"),
        ("canceled",              "cancelled"),
        ("cancelled",             "cancelled"),
        ("expired",               "cancelled"),
        ("replaced",              "cancelled"),
        ("OrderStatus.CANCELED",  "cancelled"),
        ("rejected",              "rejected"),
        ("stopped",               "rejected"),
        ("OrderStatus.REJECTED",  "rejected"),
        ("some_future_status",    "some_future_status"),  # pass-through
    ])
    def test_known_and_unknown(self, raw, expected):
        from src.execution.paper_order_poller import _normalize_status
        assert _normalize_status(raw) == expected


# ---------------------------------------------------------------------------
# _is_terminal
# ---------------------------------------------------------------------------

class TestIsTerminal:
    @pytest.mark.parametrize("status,expected", [
        ("filled",    True),
        ("cancelled", True),
        ("rejected",  True),
        ("accepted",  False),
        ("",          False),
    ])
    def test_terminal_check(self, status, expected):
        from src.execution.paper_order_poller import _is_terminal
        assert _is_terminal(status) is expected


# ---------------------------------------------------------------------------
# poll_order_status — unit tests
# ---------------------------------------------------------------------------

class TestPollOrderStatus:
    def _call(
        self,
        tmp_path: Path,
        client,
        *,
        initial_status: str = "accepted",
        timeout_seconds: int = 30,
        interval_seconds: float = 0.0,
        sleep_fn=None,
        monotonic_fn=None,
    ) -> dict:
        from src.execution.paper_order_poller import poll_order_status
        return poll_order_status(
            client=client,
            alpaca_order_id="alp-aaa-111",
            client_order_id="BT-000035",
            initial_status=initial_status,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            output_dir=tmp_path,
            _sleep_fn=sleep_fn or (lambda _: None),
            _monotonic_fn=monotonic_fn or _instant_mono(),
        )

    # --- no-poll cases ---

    def test_initial_terminal_no_network_call(self, tmp_path):
        client = _poll_client([])
        self._call(tmp_path, client, initial_status="filled")
        client.get_order_by_id.assert_not_called()

    def test_initial_terminal_poll_count_zero(self, tmp_path):
        client = _poll_client([])
        result = self._call(tmp_path, client, initial_status="filled")
        assert result["poll_count"] == 0

    def test_initial_terminal_artifact_written(self, tmp_path):
        client = _poll_client([])
        self._call(tmp_path, client, initial_status="filled")
        assert (tmp_path / "paper_order_status_poll.json").exists()

    # --- polling cases ---

    def test_filled_after_one_poll(self, tmp_path):
        client = _poll_client([_order_response("filled")])
        result = self._call(tmp_path, client, initial_status="accepted")
        assert result["final_status"] == "filled"
        assert result["poll_count"] == 1

    def test_raw_statuses_contains_initial_and_polled(self, tmp_path):
        client = _poll_client([_order_response("pending_new"), _order_response("filled")])
        result = self._call(tmp_path, client, initial_status="accepted")
        # initial "accepted" + "accepted" (pending_new→accepted) + "filled"
        assert result["raw_statuses"][0] == "accepted"
        assert "filled" in result["raw_statuses"]

    def test_poll_count_increments_correctly(self, tmp_path):
        client = _poll_client([
            _order_response("pending_new"),
            _order_response("pending_new"),
            _order_response("filled"),
        ])
        result = self._call(tmp_path, client, initial_status="accepted")
        assert result["poll_count"] == 3

    # --- failure cases ---

    def test_rejected_raises_runtime_error(self, tmp_path):
        client = _poll_client([_order_response("rejected")])
        with pytest.raises(RuntimeError, match="rejected"):
            self._call(tmp_path, client, initial_status="accepted")

    def test_cancelled_raises_runtime_error(self, tmp_path):
        client = _poll_client([_order_response("canceled")])
        with pytest.raises(RuntimeError, match="cancelled"):
            self._call(tmp_path, client, initial_status="accepted")

    def test_rejected_artifact_written_before_raise(self, tmp_path):
        client = _poll_client([_order_response("rejected")])
        with pytest.raises(RuntimeError):
            self._call(tmp_path, client, initial_status="accepted")
        assert (tmp_path / "paper_order_status_poll.json").exists()

    def test_rejected_artifact_has_final_status(self, tmp_path):
        client = _poll_client([_order_response("rejected")])
        with pytest.raises(RuntimeError):
            self._call(tmp_path, client, initial_status="accepted")
        artifact = json.loads((tmp_path / "paper_order_status_poll.json").read_text(encoding="utf-8"))
        assert artifact["final_status"] == "rejected"

    # --- timeout cases ---

    def test_timeout_sets_timed_out_flag(self, tmp_path):
        client = _poll_client([])
        result = self._call(
            tmp_path, client,
            initial_status="accepted",
            monotonic_fn=_immediate_timeout_mono(),
        )
        assert result["timed_out"] is True

    def test_timeout_accepted_does_not_raise(self, tmp_path):
        client = _poll_client([])
        result = self._call(
            tmp_path, client,
            initial_status="accepted",
            monotonic_fn=_immediate_timeout_mono(),
        )
        assert result["final_status"] == "accepted"  # no raise

    def test_timeout_artifact_written(self, tmp_path):
        client = _poll_client([])
        self._call(tmp_path, client, initial_status="accepted",
                   monotonic_fn=_immediate_timeout_mono())
        assert (tmp_path / "paper_order_status_poll.json").exists()

    def test_timeout_cancel_never_called(self, tmp_path):
        client = _poll_client([])
        self._call(tmp_path, client, initial_status="accepted",
                   monotonic_fn=_immediate_timeout_mono())
        client.cancel_order.assert_not_called()

    def test_timeout_with_rejected_final_raises(self, tmp_path):
        """If timeout fires but last polled status is already rejected, still raise."""
        # Simulate: one poll returns rejected, then timeout on next loop check
        mono_values = iter([0.0, 1.0, 100.0])
        client = _poll_client([_order_response("rejected")])
        with pytest.raises(RuntimeError, match="rejected"):
            self._call(tmp_path, client, initial_status="accepted",
                       monotonic_fn=lambda: next(mono_values))

    # --- safety invariants ---

    def test_submit_order_never_called(self, tmp_path):
        client = _poll_client([_order_response("filled")])
        self._call(tmp_path, client, initial_status="accepted")
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        client = _poll_client([_order_response("filled")])
        self._call(tmp_path, client, initial_status="accepted")
        client.cancel_order.assert_not_called()

    # --- artifact contents ---

    def test_artifact_has_all_required_fields(self, tmp_path):
        client = _poll_client([_order_response("filled")])
        self._call(tmp_path, client, initial_status="accepted")
        artifact = json.loads((tmp_path / "paper_order_status_poll.json").read_text(encoding="utf-8"))
        for field in [
            "client_order_id", "alpaca_order_id", "initial_status", "final_status",
            "poll_count", "timeout_seconds", "timed_out", "checked_at_utc", "raw_statuses",
        ]:
            assert field in artifact, f"missing field: {field}"

    def test_artifact_client_order_id(self, tmp_path):
        client = _poll_client([_order_response("filled")])
        self._call(tmp_path, client, initial_status="accepted")
        artifact = json.loads((tmp_path / "paper_order_status_poll.json").read_text(encoding="utf-8"))
        assert artifact["client_order_id"] == "BT-000035"

    def test_artifact_alpaca_order_id(self, tmp_path):
        client = _poll_client([_order_response("filled")])
        self._call(tmp_path, client, initial_status="accepted")
        artifact = json.loads((tmp_path / "paper_order_status_poll.json").read_text(encoding="utf-8"))
        assert artifact["alpaca_order_id"] == "alp-aaa-111"

    def test_artifact_raw_statuses_is_list(self, tmp_path):
        client = _poll_client([_order_response("filled")])
        self._call(tmp_path, client, initial_status="accepted")
        artifact = json.loads((tmp_path / "paper_order_status_poll.json").read_text(encoding="utf-8"))
        assert isinstance(artifact["raw_statuses"], list)

    # --- error handling ---

    def test_lookup_error_continues(self, tmp_path):
        """A transient network error is recorded in raw_statuses and polling continues."""
        client = mock.MagicMock()
        client.submit_order.side_effect  = AssertionError("must not call")
        client.cancel_order.side_effect  = AssertionError("must not call")
        client.get_order_by_id.side_effect = [
            Exception("network error"),
            _order_response("filled"),
        ]
        result = self._call(tmp_path, client, initial_status="accepted")
        assert result["final_status"] == "filled"
        assert any("error:" in s for s in result["raw_statuses"])

    def test_error_in_raw_statuses_does_not_prevent_artifact(self, tmp_path):
        client = mock.MagicMock()
        client.get_order_by_id.side_effect = [
            Exception("blip"),
            _order_response("filled"),
        ]
        self._call(tmp_path, client, initial_status="accepted")
        assert (tmp_path / "paper_order_status_poll.json").exists()


# ---------------------------------------------------------------------------
# Config field defaults and parsing
# ---------------------------------------------------------------------------

class TestPollConfig:
    def _make_execution_config(self, **kwargs):
        from src.config.loader import ExecutionConfig
        return ExecutionConfig(**kwargs)

    def test_poll_order_status_default_false(self):
        cfg = self._make_execution_config()
        assert cfg.paper_poll_order_status is False

    def test_poll_timeout_default_30(self):
        cfg = self._make_execution_config()
        assert cfg.paper_poll_timeout_seconds == 30

    def test_poll_interval_default_1(self):
        cfg = self._make_execution_config()
        assert cfg.paper_poll_interval_seconds == 1.0

    def test_poll_fields_parsed_from_yaml(self, tmp_path):
        import textwrap
        from src.config.loader import load_config
        cfg_text = textwrap.dedent("""\
            backtest:
              start_date: "2024-01-01"
              end_date: "2024-01-31"
              initial_capital: 100000
              commission_per_share: 0.005
              slippage_per_share: 0.01
            symbols: [SPY]
            data:
              provider: yahoo
              bar_interval: "5m"
              timezone: "America/New_York"
            strategy:
              name: opening_range_breakout
              params: {}
            risk: {}
            logging:
              level: WARNING
              format: "%(message)s"
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
              paper_poll_order_status: true
              paper_poll_timeout_seconds: 60
              paper_poll_interval_seconds: 2.5
        """)
        p = tmp_path / "settings.yaml"
        p.write_text(cfg_text, encoding="utf-8")
        cfg = load_config(p)
        assert cfg.execution.paper_poll_order_status is True
        assert cfg.execution.paper_poll_timeout_seconds == 60
        assert cfg.execution.paper_poll_interval_seconds == 2.5


# ---------------------------------------------------------------------------
# Main.py integration — buy flow
# ---------------------------------------------------------------------------

# Re-use the _make_config / _run pattern from test_paper_close.py

_FIXED_TS         = pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York")
_FIXED_TS_LABEL   = "20240115100000"
_FIXED_DATE_LABEL = "20240115"
_BUY_CID          = f"BT-{_FIXED_TS_LABEL}-SPY"


def _make_buy_config(*, poll_enabled: bool = False, timeout: int = 5, interval: float = 0.0):
    from src.config.loader import (
        AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
        LoggingConfig, RiskConfig, StrategyConfig,
    )
    from src.execution.order_intent import OrderIntent
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-15", end_date="2024-01-15",
            initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(name="opening_range_breakout", params={
            "opening_range_start": "09:30", "opening_range_end": "10:00",
            "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
        }),
        risk=RiskConfig(),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(
            mode="paper",
            paper_trading_enabled=True,
            paper_preview_only=False,
            paper_selected_client_order_id=_BUY_CID,
            paper_poll_order_status=poll_enabled,
            paper_poll_timeout_seconds=timeout,
            paper_poll_interval_seconds=interval,
        ),
    )


def _make_buy_result(status: str = "accepted") -> "OrderResult":
    from src.execution.broker import OrderResult
    return OrderResult(
        order_id="ALPACA-BUY-001",
        symbol="SPY", side="buy", quantity=1.0, status=status,
        submitted_at=_FIXED_TS, reason="breakout",
        client_order_id=_BUY_CID,
        metadata={"raw_status": status, "partial_fill": False},
    )


def _make_buy_intent():
    from src.execution.order_intent import OrderIntent
    return OrderIntent(
        symbol="SPY", side="buy", quantity=1.0, order_type="market",
        reason="breakout", timestamp=_FIXED_TS, client_order_id=_BUY_CID,
    )


def _pass_recon_generate(rg_self):
    rg_self._output_dir.mkdir(parents=True, exist_ok=True)
    import json as _j
    (rg_self._output_dir / "order_reconciliation.json").write_text(
        _j.dumps({"overall_status": "PASS"})
    )


def _run_buy(cfg, tmp_path, poll_client=None, submit_status: str = "accepted"):
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    submit_result = _make_buy_result(submit_status)
    mock_submit   = mock.MagicMock(return_value=submit_result)
    mock_cancel   = mock.MagicMock()
    mock_intent   = _make_buy_intent()
    mock_get_client = mock.MagicMock(return_value=poll_client or mock.MagicMock())

    fake_preflight = {
        "ok": True,
        "account": {"status": "ACTIVE"},
        "positions": {},
        "symbols":   ["SPY"],
    }
    fake_engine_results = {
        "order_intents": [mock_intent],
        "metrics": {}, "trades": [],
        "equity_curve": pd.DataFrame(),
    }
    mock_engine = mock.MagicMock()
    mock_engine.run.return_value = fake_engine_results
    mock_engine._portfolio.positions = {}

    with (
        mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS),
        mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None),
        mock.patch.object(AlpacaBrokerAdapter, "preflight_check", return_value=fake_preflight),
        mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit),
        mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel),
        mock.patch.object(AlpacaBrokerAdapter, "_get_client", mock_get_client),
        mock.patch("src.main.build_engine", return_value=mock_engine),
        mock.patch("src.reporting.report_generator.ReportGenerator.generate_all", _pass_recon_generate),
        mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"),
        mock.patch("src.execution.paper_ledger.append_ledger_row") as mock_ledger,
        mock.patch("src.execution.paper_daily_limits.assert_within_daily_limits"),
        mock.patch("src.main.load_config", return_value=cfg),
        mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]),
    ):
        src.main.main()

    return mock_submit, mock_cancel, mock_ledger, mock_get_client


class TestBuyFlowPolling:
    def test_polling_disabled_no_get_client_call(self, tmp_path):
        cfg = _make_buy_config(poll_enabled=False)
        _, _, _, mock_get_client = _run_buy(cfg, tmp_path)
        mock_get_client.assert_not_called()

    def test_polling_disabled_submit_called_once(self, tmp_path):
        cfg = _make_buy_config(poll_enabled=False)
        mock_submit, _, _, _ = _run_buy(cfg, tmp_path)
        mock_submit.assert_called_once()

    def test_polling_enabled_submit_called_exactly_once(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_buy_config(poll_enabled=True)
        mock_submit, _, _, _ = _run_buy(cfg, tmp_path, poll_client=poll_client)
        mock_submit.assert_called_once()

    def test_polling_enabled_cancel_never_called(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_buy_config(poll_enabled=True)
        _, mock_cancel, _, _ = _run_buy(cfg, tmp_path, poll_client=poll_client)
        mock_cancel.assert_not_called()

    def test_polling_enabled_ledger_gets_final_status(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_buy_config(poll_enabled=True)
        _, _, mock_ledger, _ = _run_buy(cfg, tmp_path, poll_client=poll_client,
                                        submit_status="accepted")
        call_kwargs = mock_ledger.call_args[0][1]  # second arg is the row dict
        assert call_kwargs["status"] == "filled"

    def test_polling_enabled_rejected_raises(self, tmp_path):
        poll_client = _poll_client([_order_response("rejected")])
        cfg = _make_buy_config(poll_enabled=True)
        with pytest.raises(RuntimeError, match="rejected"):
            _run_buy(cfg, tmp_path, poll_client=poll_client)

    def test_polling_enabled_poll_artifact_written(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_buy_config(poll_enabled=True)
        _run_buy(cfg, tmp_path, poll_client=poll_client)
        assert (tmp_path / "paper_order_status_poll.json").exists()


# ---------------------------------------------------------------------------
# Main.py integration — close flow
# ---------------------------------------------------------------------------

_CLOSE_CID      = f"BC-{_FIXED_DATE_LABEL}-SPY-CLOSE"


def _make_close_config(*, poll_enabled: bool = False, timeout: int = 5, interval: float = 0.0):
    from src.config.loader import (
        AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
        LoggingConfig, RiskConfig, StrategyConfig,
    )
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-15", end_date="2024-01-15",
            initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(name="opening_range_breakout", params={
            "opening_range_start": "09:30", "opening_range_end": "10:00",
            "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
        }),
        risk=RiskConfig(),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(
            mode="paper",
            paper_trading_enabled=True,
            paper_close_positions_enabled=True,
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_CLOSE_CID,
            paper_poll_order_status=poll_enabled,
            paper_poll_timeout_seconds=timeout,
            paper_poll_interval_seconds=interval,
        ),
    )


def _make_close_result(status: str = "accepted") -> "OrderResult":
    from src.execution.broker import OrderResult
    return OrderResult(
        order_id="ALPACA-CLOSE-001",
        symbol="SPY", side="sell", quantity=1.0, status=status,
        submitted_at=_FIXED_TS, reason="paper_close",
        client_order_id=_CLOSE_CID,
        metadata={"raw_status": status, "partial_fill": False},
    )


def _run_close(cfg, tmp_path, poll_client=None, submit_status: str = "accepted"):
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    submit_result = _make_close_result(submit_status)
    mock_submit   = mock.MagicMock(return_value=submit_result)
    mock_cancel   = mock.MagicMock()
    mock_get_client = mock.MagicMock(return_value=poll_client or mock.MagicMock())
    spy_pos = {"SPY": {"symbol": "SPY", "qty": 1.0, "market_value": 475.0,
                       "avg_entry_price": 470.0, "current_price": 475.0, "unrealized_pl": 5.0}}

    fake_preflight = {
        "ok": True,
        "account": {"status": "ACTIVE"},
        "positions": spy_pos,
        "symbols":   ["SPY"],
    }

    with (
        mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS),
        mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None),
        mock.patch.object(AlpacaBrokerAdapter, "preflight_check", return_value=fake_preflight),
        mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit),
        mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel),
        mock.patch.object(AlpacaBrokerAdapter, "_get_client", mock_get_client),
        mock.patch("src.reporting.report_generator.ReportGenerator.generate_all", _pass_recon_generate),
        mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"),
        mock.patch("src.execution.paper_ledger.append_ledger_row") as mock_ledger,
        mock.patch("src.execution.paper_daily_limits.assert_within_daily_limits"),
        mock.patch("src.main.load_config", return_value=cfg),
        mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]),
    ):
        src.main.main()

    return mock_submit, mock_cancel, mock_ledger, mock_get_client


class TestCloseFlowPolling:
    def test_polling_disabled_no_get_client_call(self, tmp_path):
        cfg = _make_close_config(poll_enabled=False)
        _, _, _, mock_get_client = _run_close(cfg, tmp_path)
        mock_get_client.assert_not_called()

    def test_polling_disabled_submit_called_once(self, tmp_path):
        cfg = _make_close_config(poll_enabled=False)
        mock_submit, _, _, _ = _run_close(cfg, tmp_path)
        mock_submit.assert_called_once()

    def test_polling_enabled_submit_called_exactly_once(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_close_config(poll_enabled=True)
        mock_submit, _, _, _ = _run_close(cfg, tmp_path, poll_client=poll_client)
        mock_submit.assert_called_once()

    def test_polling_enabled_cancel_never_called(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_close_config(poll_enabled=True)
        _, mock_cancel, _, _ = _run_close(cfg, tmp_path, poll_client=poll_client)
        mock_cancel.assert_not_called()

    def test_polling_enabled_ledger_gets_final_status(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_close_config(poll_enabled=True)
        _, _, mock_ledger, _ = _run_close(cfg, tmp_path, poll_client=poll_client,
                                          submit_status="accepted")
        call_kwargs = mock_ledger.call_args[0][1]
        assert call_kwargs["status"] == "filled"

    def test_polling_enabled_rejected_raises(self, tmp_path):
        poll_client = _poll_client([_order_response("rejected")])
        cfg = _make_close_config(poll_enabled=True)
        with pytest.raises(RuntimeError, match="rejected"):
            _run_close(cfg, tmp_path, poll_client=poll_client)

    def test_polling_enabled_poll_artifact_written(self, tmp_path):
        poll_client = _poll_client([_order_response("filled")])
        cfg = _make_close_config(poll_enabled=True)
        _run_close(cfg, tmp_path, poll_client=poll_client)
        assert (tmp_path / "paper_order_status_poll.json").exists()
