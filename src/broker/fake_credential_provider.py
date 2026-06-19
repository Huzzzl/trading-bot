from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class FakeCredentialProviderResult:
    result: str
    blocker: str | None
    metadata: dict | None
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


_FUTURE_EXPIRY = "2099-01-01T00:00:00Z"


def create_fake_paper_credential_metadata(
    *,
    profile_name: str = "fake-paper-dev",
    declared_environment: str = "paper",
    expires_at_utc: str = _FUTURE_EXPIRY,
    rotation_required: bool = False,
    secret_material_present: bool = False,
    extra_fields: dict | None = None,
) -> FakeCredentialProviderResult:
    if declared_environment != "paper":
        return FakeCredentialProviderResult(
            result="BLOCKED",
            blocker="fake provider refuses non-paper environment",
            metadata=None,
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )

    metadata: dict = {
        "profile_name": profile_name,
        "declared_environment": declared_environment,
        "expires_at_utc": expires_at_utc,
        "rotation_required": rotation_required,
        "secret_material_present": secret_material_present,
    }

    if extra_fields is not None:
        metadata.update(extra_fields)

    return FakeCredentialProviderResult(
        result="PASS",
        blocker=None,
        metadata=metadata,
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
