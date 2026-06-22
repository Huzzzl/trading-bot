from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class FakePaperAdapterResult:
    result: str
    blocker: str | None
    adapter_environment: str | None
    broker_reported_environment: str | None
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


def create_fake_paper_adapter_metadata(
    *,
    adapter_environment: str = "paper",
    broker_reported_environment: str = "paper",
) -> FakePaperAdapterResult:
    if adapter_environment != "paper" or broker_reported_environment != "paper":
        return FakePaperAdapterResult(
            result="BLOCKED",
            blocker="fake adapter refuses non-paper environment",
            adapter_environment=adapter_environment,
            broker_reported_environment=broker_reported_environment,
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )

    return FakePaperAdapterResult(
        result="PASS",
        blocker=None,
        adapter_environment=adapter_environment,
        broker_reported_environment=broker_reported_environment,
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


_FAKE_BROKER_TIMESTAMP = "2026-06-01T00:00:00Z"


def create_fake_paper_account_snapshot(
    *,
    broker_reported_environment: str = "paper",
    account_status: str = "active",
    cash: float = 100000.0,
    buying_power: float = 100000.0,
    equity: float = 100000.0,
    positions: list | None = None,
    open_orders: list | None = None,
    market_clock: dict | None = None,
    broker_timestamp: str = _FAKE_BROKER_TIMESTAMP,
) -> dict:
    return {
        "broker_reported_environment": broker_reported_environment,
        "account_status": account_status,
        "cash": cash,
        "buying_power": buying_power,
        "equity": equity,
        "positions": list(positions) if positions is not None else [],
        "open_orders": list(open_orders) if open_orders is not None else [],
        "market_clock": dict(market_clock) if market_clock is not None else {"is_open": True},
        "broker_timestamp": broker_timestamp,
    }
