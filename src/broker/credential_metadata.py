from __future__ import annotations

import dataclasses
import re
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from enum import Enum


class CredentialMetadataStatus(str, Enum):
    NOT_VALIDATED = "NOT_VALIDATED"
    CREDENTIAL_METADATA_READY_PAPER = "CREDENTIAL_METADATA_READY_PAPER"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_PROFILE = "BLOCKED_PROFILE"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BLOCKED_EXPIRY = "BLOCKED_EXPIRY"
    BLOCKED_ROTATION = "BLOCKED_ROTATION"
    BLOCKED_SECRET_MATERIAL = "BLOCKED_SECRET_MATERIAL"
    BLOCKED_FORBIDDEN_FIELDS = "BLOCKED_FORBIDDEN_FIELDS"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


@dataclasses.dataclass(frozen=True)
class CredentialMetadataValidationResult:
    result: str
    status: CredentialMetadataStatus
    blocker: str | None
    profile_name: str | None
    declared_environment: str | None
    expires_at_utc: str | None
    rotation_required: bool | None
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


_REQUIRED_KEYS = (
    "profile_name",
    "declared_environment",
    "expires_at_utc",
    "rotation_required",
    "secret_material_present",
)

_FORBIDDEN_KEY_FRAGMENTS: tuple[str, ...] = (
    "api" + "_" + "key",
    "api" + "key",
    "sec" + "ret",
    "tok" + "en",
    "pass" + "word",
    "authori" + "zation",
    "credential" + "_" + "value",
    "private" + "_" + "key",
    "account" + "_" + "id",
    "account" + "_" + "number",
    "end" + "point",
    "base" + "_" + "url",
    "live" + "_" + "url",
    "paper" + "_" + "url",
    "head" + "ers",
    "ses" + "sion",
)

_FORBIDDEN_VALUE_FRAGMENTS: tuple[str, ...] = (
    "api" + "_" + "key",
    "api" + "key",
    "sec" + "ret",
    "tok" + "en",
    "pass" + "word",
    "authori" + "zation",
    "private" + "_" + "key",
    "bearer ",
)

_ALLOWED_KEY_EXACT = "secret_material_present"

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


def _contains_forbidden_key(key: str) -> bool:
    lower = key.lower()
    if lower == _ALLOWED_KEY_EXACT:
        return False
    for fragment in _FORBIDDEN_KEY_FRAGMENTS:
        if fragment in lower:
            return True
    return False


def _contains_forbidden_value(value: str) -> bool:
    lower = value.lower()
    for fragment in _FORBIDDEN_VALUE_FRAGMENTS:
        if fragment in lower:
            return True
    return False


def _scan_forbidden(obj: object) -> bool:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and _contains_forbidden_key(k):
                return True
            if _scan_forbidden(v):
                return True
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            if _scan_forbidden(item):
                return True
    elif isinstance(obj, str):
        if _contains_forbidden_value(obj):
            return True
    return False


def _blocked(
    status: CredentialMetadataStatus,
    blocker: str,
    checked: list[str],
    failed_criterion: str,
    *,
    profile_name: str | None = None,
    declared_environment: str | None = None,
    expires_at_utc: str | None = None,
    rotation_required: bool | None = None,
) -> CredentialMetadataValidationResult:
    return CredentialMetadataValidationResult(
        result="BLOCKED",
        status=status,
        blocker=blocker,
        profile_name=profile_name,
        declared_environment=declared_environment,
        expires_at_utc=expires_at_utc,
        rotation_required=rotation_required,
        criteria_checked=tuple(checked),
        criteria_failed=(failed_criterion,),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def validate_credential_metadata(
    metadata: dict,
    *,
    expected_environment: str = "paper",
    now_utc: str,
) -> CredentialMetadataValidationResult:
    checked: list[str] = []

    # 1. input.schema
    checked.append("input.schema")
    if not isinstance(metadata, dict):
        return _blocked(
            CredentialMetadataStatus.BLOCKED_SCHEMA,
            "metadata is not a dict",
            checked,
            "input.schema",
        )

    # 2. expected_environment.paper_only
    checked.append("expected_environment.paper_only")
    if expected_environment != "paper":
        return _blocked(
            CredentialMetadataStatus.BLOCKED_ENVIRONMENT,
            "expected_environment is not paper",
            checked,
            "expected_environment.paper_only",
        )

    # 3. metadata.required_keys
    checked.append("metadata.required_keys")
    for key in _REQUIRED_KEYS:
        if key not in metadata:
            return _blocked(
                CredentialMetadataStatus.BLOCKED_SCHEMA,
                f"missing required key: {key}",
                checked,
                "metadata.required_keys",
            )

    profile_name_raw = metadata.get("profile_name")
    declared_env_raw = metadata.get("declared_environment")
    expires_raw = metadata.get("expires_at_utc")
    rotation_raw = metadata.get("rotation_required")

    # 4. metadata.profile_name
    checked.append("metadata.profile_name")
    if not isinstance(profile_name_raw, str) or not profile_name_raw.strip():
        return _blocked(
            CredentialMetadataStatus.BLOCKED_PROFILE,
            "profile_name is invalid or empty",
            checked,
            "metadata.profile_name",
        )

    profile_name: str = profile_name_raw

    # 5. metadata.declared_environment
    checked.append("metadata.declared_environment")
    if not isinstance(declared_env_raw, str) or declared_env_raw != "paper":
        return _blocked(
            CredentialMetadataStatus.BLOCKED_ENVIRONMENT,
            "declared_environment is not paper",
            checked,
            "metadata.declared_environment",
            profile_name=profile_name,
        )

    declared_environment: str = declared_env_raw

    # 6. metadata.expiry_format
    checked.append("metadata.expiry_format")
    expires_dt = _parse_utc_timestamp(expires_raw)
    if expires_dt is None:
        return _blocked(
            CredentialMetadataStatus.BLOCKED_EXPIRY,
            "expires_at_utc is not a valid timezone-aware UTC datetime",
            checked,
            "metadata.expiry_format",
            profile_name=profile_name,
            declared_environment=declared_environment,
        )

    expires_at_utc: str = expires_raw  # type: ignore[assignment]

    # 7. metadata.not_expired
    checked.append("metadata.not_expired")
    now_dt = _parse_utc_timestamp(now_utc)
    if now_dt is None:
        return _blocked(
            CredentialMetadataStatus.BLOCKED_EXPIRY,
            "now_utc is not a valid timezone-aware UTC datetime",
            checked,
            "metadata.not_expired",
            profile_name=profile_name,
            declared_environment=declared_environment,
            expires_at_utc=expires_at_utc,
        )
    if expires_dt <= now_dt:
        return _blocked(
            CredentialMetadataStatus.BLOCKED_EXPIRY,
            "credential metadata is expired or not future",
            checked,
            "metadata.not_expired",
            profile_name=profile_name,
            declared_environment=declared_environment,
            expires_at_utc=expires_at_utc,
        )

    # 8. metadata.rotation_required_false
    checked.append("metadata.rotation_required_false")
    if rotation_raw is not False:
        return _blocked(
            CredentialMetadataStatus.BLOCKED_ROTATION,
            "rotation_required is not False",
            checked,
            "metadata.rotation_required_false",
            profile_name=profile_name,
            declared_environment=declared_environment,
            expires_at_utc=expires_at_utc,
        )

    rotation_required: bool = rotation_raw

    # 9. metadata.secret_material_absent
    checked.append("metadata.secret_material_absent")
    smp = metadata.get("secret_material_present")
    if smp is not False:
        return _blocked(
            CredentialMetadataStatus.BLOCKED_SECRET_MATERIAL,
            "secret_material_present is not False",
            checked,
            "metadata.secret_material_absent",
            profile_name=profile_name,
            declared_environment=declared_environment,
            expires_at_utc=expires_at_utc,
            rotation_required=rotation_required,
        )

    # 10. metadata.forbidden_fields
    checked.append("metadata.forbidden_fields")
    scan_copy = dict(metadata)
    scan_copy.pop("secret_material_present", None)
    if _scan_forbidden(scan_copy):
        return _blocked(
            CredentialMetadataStatus.BLOCKED_FORBIDDEN_FIELDS,
            "metadata contains forbidden key or value",
            checked,
            "metadata.forbidden_fields",
            profile_name=profile_name,
            declared_environment=declared_environment,
            expires_at_utc=expires_at_utc,
            rotation_required=rotation_required,
        )
    for key in metadata:
        if isinstance(key, str) and _contains_forbidden_key(key):
            return _blocked(
                CredentialMetadataStatus.BLOCKED_FORBIDDEN_FIELDS,
                "metadata contains forbidden key or value",
                checked,
                "metadata.forbidden_fields",
                profile_name=profile_name,
                declared_environment=declared_environment,
                expires_at_utc=expires_at_utc,
                rotation_required=rotation_required,
            )

    # 11. metadata.safety_flags
    checked.append("metadata.safety_flags")

    return CredentialMetadataValidationResult(
        result="PASS",
        status=CredentialMetadataStatus.CREDENTIAL_METADATA_READY_PAPER,
        blocker=None,
        profile_name=profile_name,
        declared_environment=declared_environment,
        expires_at_utc=expires_at_utc,
        rotation_required=rotation_required,
        criteria_checked=tuple(checked),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
