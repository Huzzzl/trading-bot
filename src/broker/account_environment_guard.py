from __future__ import annotations

import dataclasses
from enum import Enum


class AccountEnvironmentStatus(str, Enum):
    NOT_VERIFIED = "NOT_VERIFIED"
    VERIFIED_PAPER = "VERIFIED_PAPER"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_EXPECTED_ENVIRONMENT = "BLOCKED_EXPECTED_ENVIRONMENT"
    BLOCKED_CREDENTIAL_ENVIRONMENT = "BLOCKED_CREDENTIAL_ENVIRONMENT"
    BLOCKED_ADAPTER_ENVIRONMENT = "BLOCKED_ADAPTER_ENVIRONMENT"
    BLOCKED_BROKER_ENVIRONMENT = "BLOCKED_BROKER_ENVIRONMENT"
    BLOCKED_ENVIRONMENT_MISMATCH = "BLOCKED_ENVIRONMENT_MISMATCH"
    BLOCKED_LIVE_ENVIRONMENT = "BLOCKED_LIVE_ENVIRONMENT"
    BLOCKED_AMBIGUOUS_ENVIRONMENT = "BLOCKED_AMBIGUOUS_ENVIRONMENT"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


@dataclasses.dataclass(frozen=True)
class AccountEnvironmentVerificationResult:
    result: str
    status: AccountEnvironmentStatus
    blocker: str | None
    expected_environment: str | None
    credential_environment: str | None
    adapter_environment: str | None
    broker_reported_environment: str | None
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


def _blocked(
    status: AccountEnvironmentStatus,
    blocker: str,
    checked: list[str],
    failed_criterion: str,
    *,
    expected_environment: str | None = None,
    credential_environment: str | None = None,
    adapter_environment: str | None = None,
    broker_reported_environment: str | None = None,
) -> AccountEnvironmentVerificationResult:
    return AccountEnvironmentVerificationResult(
        result="BLOCKED",
        status=status,
        blocker=blocker,
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
        broker_reported_environment=broker_reported_environment,
        criteria_checked=tuple(checked),
        criteria_failed=(failed_criterion,),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _is_valid_env_string(value: object) -> bool:
    return isinstance(value, str) and len(value.strip()) > 0


def _check_paper_or_live(
    value: str,
    field_name: str,
    checked: list[str],
    criterion: str,
    blocked_status: AccountEnvironmentStatus,
    *,
    expected_environment: str | None = None,
    credential_environment: str | None = None,
    adapter_environment: str | None = None,
    broker_reported_environment: str | None = None,
) -> AccountEnvironmentVerificationResult | None:
    checked.append(criterion)
    if value == "paper":
        return None
    if value == "live":
        return _blocked(
            AccountEnvironmentStatus.BLOCKED_LIVE_ENVIRONMENT,
            f"{field_name} is live",
            checked,
            criterion,
            expected_environment=expected_environment,
            credential_environment=credential_environment,
            adapter_environment=adapter_environment,
            broker_reported_environment=broker_reported_environment,
        )
    return _blocked(
        blocked_status,
        f"{field_name} is not paper",
        checked,
        criterion,
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
        broker_reported_environment=broker_reported_environment,
    )


def verify_account_environment(
    *,
    expected_environment: str,
    credential_environment: str,
    adapter_environment: str,
    broker_reported_environment: str,
) -> AccountEnvironmentVerificationResult:
    checked: list[str] = []

    fields = {
        "expected_environment": expected_environment,
        "credential_environment": credential_environment,
        "adapter_environment": adapter_environment,
        "broker_reported_environment": broker_reported_environment,
    }

    # 1. environment.schema
    checked.append("environment.schema")
    for name, value in fields.items():
        if not _is_valid_env_string(value):
            return _blocked(
                AccountEnvironmentStatus.BLOCKED_SCHEMA,
                f"{name} is not a valid non-empty string",
                checked,
                "environment.schema",
            )

    # 2. environment.expected_paper
    result = _check_paper_or_live(
        expected_environment,
        "expected_environment",
        checked,
        "environment.expected_paper",
        AccountEnvironmentStatus.BLOCKED_EXPECTED_ENVIRONMENT,
        expected_environment=expected_environment,
    )
    if result is not None:
        return result

    # 3. environment.credential_paper
    result = _check_paper_or_live(
        credential_environment,
        "credential_environment",
        checked,
        "environment.credential_paper",
        AccountEnvironmentStatus.BLOCKED_CREDENTIAL_ENVIRONMENT,
        expected_environment=expected_environment,
        credential_environment=credential_environment,
    )
    if result is not None:
        return result

    # 4. environment.adapter_paper
    result = _check_paper_or_live(
        adapter_environment,
        "adapter_environment",
        checked,
        "environment.adapter_paper",
        AccountEnvironmentStatus.BLOCKED_ADAPTER_ENVIRONMENT,
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
    )
    if result is not None:
        return result

    # 5. environment.broker_reported_paper
    result = _check_paper_or_live(
        broker_reported_environment,
        "broker_reported_environment",
        checked,
        "environment.broker_reported_paper",
        AccountEnvironmentStatus.BLOCKED_BROKER_ENVIRONMENT,
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
        broker_reported_environment=broker_reported_environment,
    )
    if result is not None:
        return result

    # 6. environment.all_match
    checked.append("environment.all_match")
    all_values = set(fields.values())
    if len(all_values) != 1 or all_values != {"paper"}:
        return _blocked(
            AccountEnvironmentStatus.BLOCKED_ENVIRONMENT_MISMATCH,
            "environment values do not all match paper",
            checked,
            "environment.all_match",
            expected_environment=expected_environment,
            credential_environment=credential_environment,
            adapter_environment=adapter_environment,
            broker_reported_environment=broker_reported_environment,
        )

    # 7. environment.no_live
    checked.append("environment.no_live")
    for name, value in fields.items():
        if value == "live":
            return _blocked(
                AccountEnvironmentStatus.BLOCKED_LIVE_ENVIRONMENT,
                f"{name} is live",
                checked,
                "environment.no_live",
                expected_environment=expected_environment,
                credential_environment=credential_environment,
                adapter_environment=adapter_environment,
                broker_reported_environment=broker_reported_environment,
            )

    # 8. environment.safety_flags
    checked.append("environment.safety_flags")

    return AccountEnvironmentVerificationResult(
        result="PASS",
        status=AccountEnvironmentStatus.VERIFIED_PAPER,
        blocker=None,
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
        broker_reported_environment=broker_reported_environment,
        criteria_checked=tuple(checked),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
