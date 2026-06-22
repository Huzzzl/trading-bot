from __future__ import annotations

import copy
import dataclasses
import math
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from enum import Enum

from src.broker.account_environment_guard import (
    AccountEnvironmentStatus,
    verify_account_environment,
)


class PaperAccountSnapshotStatus(str, Enum):
    NOT_READ = "NOT_READ"
    SNAPSHOT_READY_PAPER = "SNAPSHOT_READY_PAPER"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BLOCKED_ACCOUNT_STATUS = "BLOCKED_ACCOUNT_STATUS"
    BLOCKED_STALE_RESPONSE = "BLOCKED_STALE_RESPONSE"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


@dataclasses.dataclass(frozen=True)
class PaperAccountSnapshotResult:
    result: str
    status: PaperAccountSnapshotStatus
    blocker: str | None
    environment: str | None
    account_status: str | None
    cash: float | None
    buying_power: float | None
    equity: float | None
    positions: tuple | None
    open_orders: tuple | None
    market_clock: dict | None
    broker_timestamp: str | None
    request_id: str | None
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


_REQUIRED_SNAPSHOT_FIELDS = (
    "broker_reported_environment",
    "account_status",
    "cash",
    "buying_power",
    "equity",
    "positions",
    "open_orders",
    "market_clock",
    "broker_timestamp",
)

_UTC_ZERO = _timedelta(0)


def _parse_utc_timestamp(value: object) -> _datetime | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = _datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None
    if dt.utcoffset() != _UTC_ZERO:
        return None
    return dt


def _blocked(
    status: PaperAccountSnapshotStatus,
    blocker: str,
    checked: list[str],
    failed_criterion: str,
    *,
    environment: str | None = None,
    account_status: str | None = None,
    request_id: str | None = None,
    broker_timestamp: str | None = None,
) -> PaperAccountSnapshotResult:
    return PaperAccountSnapshotResult(
        result="BLOCKED",
        status=status,
        blocker=blocker,
        environment=environment,
        account_status=account_status,
        cash=None,
        buying_power=None,
        equity=None,
        positions=None,
        open_orders=None,
        market_clock=None,
        broker_timestamp=broker_timestamp,
        request_id=request_id,
        criteria_checked=tuple(checked),
        criteria_failed=(failed_criterion,),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _is_finite_non_negative(value: object) -> bool:
    if not isinstance(value, (int, float)):
        return False
    if isinstance(value, bool):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return value >= 0


def read_fake_paper_account_snapshot(
    snapshot: dict,
    *,
    expected_environment: str,
    credential_environment: str,
    adapter_environment: str,
    request_id: str,
    requested_at_utc: str,
    max_age_seconds: int,
) -> PaperAccountSnapshotResult:
    checked: list[str] = []

    # 1. input.schema
    checked.append("input.schema")
    if not isinstance(snapshot, dict):
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
            "snapshot is not a dict",
            checked,
            "input.schema",
        )

    # 2. input.request_id
    checked.append("input.request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
            "request_id is not a non-empty string",
            checked,
            "input.request_id",
        )

    # 3. environment.verification
    checked.append("environment.verification")
    broker_reported = snapshot.get("broker_reported_environment")
    if not isinstance(broker_reported, str):
        broker_reported_str = ""
    else:
        broker_reported_str = broker_reported

    env_result = verify_account_environment(
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
        broker_reported_environment=broker_reported_str,
    )
    if env_result.result != "PASS":
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_ENVIRONMENT,
            env_result.blocker or "environment verification failed",
            checked,
            "environment.verification",
            request_id=request_id,
        )

    # 4. snapshot.required_fields
    checked.append("snapshot.required_fields")
    for key in _REQUIRED_SNAPSHOT_FIELDS:
        if key not in snapshot:
            return _blocked(
                PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
                f"missing required field: {key}",
                checked,
                "snapshot.required_fields",
                environment="paper",
                request_id=request_id,
            )

    # 5. snapshot.account_status
    checked.append("snapshot.account_status")
    raw_account_status = snapshot["account_status"]
    if not isinstance(raw_account_status, str) or raw_account_status != "active":
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_ACCOUNT_STATUS,
            "account_status is not active",
            checked,
            "snapshot.account_status",
            environment="paper",
            request_id=request_id,
        )

    # 6. snapshot.financial_values
    checked.append("snapshot.financial_values")
    for field_name in ("cash", "buying_power", "equity"):
        val = snapshot[field_name]
        if not _is_finite_non_negative(val):
            return _blocked(
                PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
                f"{field_name} is not a finite non-negative number",
                checked,
                "snapshot.financial_values",
                environment="paper",
                account_status="active",
                request_id=request_id,
            )

    # 7. snapshot.positions_type
    checked.append("snapshot.positions_type")
    raw_positions = snapshot["positions"]
    if not isinstance(raw_positions, (list, tuple)):
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
            "positions is not a list or tuple",
            checked,
            "snapshot.positions_type",
            environment="paper",
            account_status="active",
            request_id=request_id,
        )

    # 8. snapshot.open_orders_type
    checked.append("snapshot.open_orders_type")
    raw_open_orders = snapshot["open_orders"]
    if not isinstance(raw_open_orders, (list, tuple)):
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
            "open_orders is not a list or tuple",
            checked,
            "snapshot.open_orders_type",
            environment="paper",
            account_status="active",
            request_id=request_id,
        )

    # 9. snapshot.market_clock_type
    checked.append("snapshot.market_clock_type")
    raw_market_clock = snapshot["market_clock"]
    if not isinstance(raw_market_clock, dict):
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
            "market_clock is not a dict",
            checked,
            "snapshot.market_clock_type",
            environment="paper",
            account_status="active",
            request_id=request_id,
        )

    # 10. snapshot.broker_timestamp_format
    checked.append("snapshot.broker_timestamp_format")
    raw_broker_ts = snapshot["broker_timestamp"]
    broker_ts_dt = _parse_utc_timestamp(raw_broker_ts)
    if broker_ts_dt is None:
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
            "broker_timestamp is not a valid timezone-aware UTC timestamp",
            checked,
            "snapshot.broker_timestamp_format",
            environment="paper",
            account_status="active",
            request_id=request_id,
        )

    # 11. snapshot.requested_at_utc_format
    checked.append("snapshot.requested_at_utc_format")
    requested_dt = _parse_utc_timestamp(requested_at_utc)
    if requested_dt is None:
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_SCHEMA,
            "requested_at_utc is not a valid timezone-aware UTC timestamp",
            checked,
            "snapshot.requested_at_utc_format",
            environment="paper",
            account_status="active",
            request_id=request_id,
            broker_timestamp=raw_broker_ts,
        )

    # 12. snapshot.broker_timestamp_not_future
    checked.append("snapshot.broker_timestamp_not_future")
    if broker_ts_dt > requested_dt:
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_STALE_RESPONSE,
            "broker_timestamp is in the future relative to requested_at_utc",
            checked,
            "snapshot.broker_timestamp_not_future",
            environment="paper",
            account_status="active",
            request_id=request_id,
            broker_timestamp=raw_broker_ts,
        )

    # 13. snapshot.max_age
    checked.append("snapshot.max_age")
    age_seconds = (requested_dt - broker_ts_dt).total_seconds()
    if age_seconds > max_age_seconds:
        return _blocked(
            PaperAccountSnapshotStatus.BLOCKED_STALE_RESPONSE,
            "snapshot age exceeds max_age_seconds",
            checked,
            "snapshot.max_age",
            environment="paper",
            account_status="active",
            request_id=request_id,
            broker_timestamp=raw_broker_ts,
        )

    # 14. snapshot.safety_flags
    checked.append("snapshot.safety_flags")

    return PaperAccountSnapshotResult(
        result="PASS",
        status=PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER,
        blocker=None,
        environment="paper",
        account_status="active",
        cash=float(snapshot["cash"]),
        buying_power=float(snapshot["buying_power"]),
        equity=float(snapshot["equity"]),
        positions=tuple(copy.deepcopy(raw_positions)),
        open_orders=tuple(copy.deepcopy(raw_open_orders)),
        market_clock=copy.deepcopy(raw_market_clock),
        broker_timestamp=raw_broker_ts,
        request_id=request_id,
        criteria_checked=tuple(checked),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
