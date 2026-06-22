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
