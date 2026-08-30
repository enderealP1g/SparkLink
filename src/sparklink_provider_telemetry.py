"""Provider telemetry adapter contracts and safe snapshot normalization.

Provider capacity/traffic telemetry is deliberately separate from customer
metering.  This module contains the small adapter registry used by the local
operator collector.  Provider-specific authenticated clients can be added
behind this contract later, but they must return normalized, non-secret
telemetry.  When no authorized source is available the adapter emits an
explicit ``unknown`` snapshot with every telemetry value absent.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Mapping


SNAPSHOT_STATUSES = frozenset({"available", "stale", "unknown", "unavailable"})
SNAPSHOT_VALUE_FIELDS = (
    "capacity_bytes",
    "used_bytes",
    "remaining_bytes",
    "resource_cycle_start",
    "resource_cycle_end",
    "next_reset_at",
    "financial_cycle",
    "next_due_at",
)
SNAPSHOT_REQUIRED_FIELDS = frozenset({"resource_id", "observed_at", "source", "status"})
SNAPSHOT_ALLOWED_FIELDS = SNAPSHOT_REQUIRED_FIELDS | frozenset(SNAPSHOT_VALUE_FIELDS) | {"snapshot_id", "detail"}
_CREDENTIAL_TEXT_RE = re.compile(
    r"(?i)(?:vless|anytls)://|-----BEGIN [^-]*PRIVATE KEY-----|"
    r"\b(?:bearer|access[_-]?token|subscription[_-]?token|password|secret|private[_-]?key)\b\s*[:=]"
)


class ProviderTelemetryError(ValueError):
    """A non-secret validation error for provider telemetry input."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_time(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_byte(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderTelemetryError(f"{field}_invalid")
    return value


def _validate_time(value: object, field: str) -> str | None:
    if value is None:
        return None
    normalized = normalize_time(value)
    if normalized is None:
        raise ProviderTelemetryError(f"{field}_invalid")
    return normalized


def normalize_snapshot(snapshot: Mapping[str, object], resource_id: str | None = None) -> dict:
    """Validate and normalize one non-secret provider snapshot.

    ``unknown`` and ``unavailable`` records must not carry stale-looking
    numeric or cycle values.  ``available`` means all three byte counters are
    present and internally consistent.  ``stale`` may carry the last known
    complete values, but its freshness is still determined by the Control
    Plane's observed timestamp.
    """

    if not isinstance(snapshot, Mapping):
        raise ProviderTelemetryError("snapshot_invalid")
    unknown_fields = set(snapshot) - SNAPSHOT_ALLOWED_FIELDS
    if unknown_fields:
        raise ProviderTelemetryError("snapshot_fields_not_allowed")
    missing = SNAPSHOT_REQUIRED_FIELDS - set(snapshot)
    if missing:
        raise ProviderTelemetryError("snapshot_fields_incomplete")
    actual_resource_id = snapshot.get("resource_id")
    if not isinstance(actual_resource_id, str) or not actual_resource_id.strip():
        raise ProviderTelemetryError("resource_id_invalid")
    actual_resource_id = actual_resource_id.strip()
    if resource_id is not None and actual_resource_id != resource_id:
        raise ProviderTelemetryError("resource_id_mismatch")
    status = snapshot.get("status")
    if not isinstance(status, str) or status not in SNAPSHOT_STATUSES:
        raise ProviderTelemetryError("snapshot_status_invalid")
    source = snapshot.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ProviderTelemetryError("snapshot_source_invalid")
    observed_at = normalize_time(snapshot.get("observed_at"))
    if observed_at is None:
        raise ProviderTelemetryError("observed_at_invalid")

    detail = snapshot.get("detail", "")
    if detail is None:
        detail = ""
    if not isinstance(detail, str):
        raise ProviderTelemetryError("snapshot_detail_invalid")
    if _CREDENTIAL_TEXT_RE.search(detail):
        raise ProviderTelemetryError("snapshot_detail_must_not_contain_credentials")
    result: dict[str, object] = {
        "resource_id": actual_resource_id,
        "observed_at": observed_at,
        "source": source.strip()[:200],
        "status": status,
        "detail": detail[:500],
    }
    for field in ("capacity_bytes", "used_bytes", "remaining_bytes"):
        result[field] = _validate_byte(snapshot.get(field), field)
    for field in ("resource_cycle_start", "resource_cycle_end", "next_reset_at", "next_due_at"):
        result[field] = _validate_time(snapshot.get(field), field)
    financial_cycle = snapshot.get("financial_cycle")
    if financial_cycle is not None and not isinstance(financial_cycle, str):
        raise ProviderTelemetryError("financial_cycle_invalid")
    result["financial_cycle"] = financial_cycle[:200] if isinstance(financial_cycle, str) else None

    numeric = tuple(result[field] for field in ("capacity_bytes", "used_bytes", "remaining_bytes"))
    has_numeric = any(value is not None for value in numeric)
    if status in {"unknown", "unavailable"} and has_numeric:
        raise ProviderTelemetryError("unknown_snapshot_must_not_have_bytes")
    if status == "available" and any(value is None for value in numeric):
        raise ProviderTelemetryError("available_snapshot_requires_bytes")
    if status == "stale" and has_numeric and any(value is None for value in numeric):
        raise ProviderTelemetryError("stale_snapshot_requires_complete_bytes")
    if all(value is not None for value in numeric):
        capacity, used, remaining = (int(value) for value in numeric)
        if used > capacity or remaining != capacity - used:
            raise ProviderTelemetryError("snapshot_bytes_inconsistent")
    if status in {"unknown", "unavailable"} and any(result[field] is not None for field in (
        "resource_cycle_start", "resource_cycle_end", "next_reset_at", "next_due_at", "financial_cycle",
    )):
        raise ProviderTelemetryError("unknown_snapshot_must_not_have_cycle_values")

    snapshot_id = snapshot.get("snapshot_id")
    if snapshot_id is not None:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ProviderTelemetryError("snapshot_id_invalid")
        result["snapshot_id"] = snapshot_id.strip()[:160]
    return result


@dataclass(frozen=True)
class ProviderAdapter:
    """Provider identity plus the source order used by future integrations."""

    key: str
    provider_names: tuple[str, ...]
    display_name: str

    @property
    def source_priority(self) -> tuple[str, ...]:
        return ("official_api", "stable_endpoint", "dashboard_export")

    def unknown_snapshot(self, resource: Mapping[str, object], observed_at: str | None = None) -> dict:
        resource_id = resource.get("resource_id")
        if not isinstance(resource_id, str) or not resource_id:
            raise ProviderTelemetryError("resource_id_invalid")
        timestamp = observed_at or utc_now()
        source = f"provider-adapter:{self.key}:no-authorized-source"
        detail = (
            f"{self.display_name} telemetry is unknown: no authorized official API, stable endpoint, "
            "or operator dashboard export is configured. Inventory transfer/due fields are not "
            "telemetry; capacity, used, remaining, reset, and next due are not inferred."
        )
        return normalize_snapshot({
            "resource_id": resource_id,
            "observed_at": timestamp,
            "source": source,
            "status": "unknown",
            "detail": detail,
        }, resource_id=resource_id)


ADAPTERS = (
    ProviderAdapter("racknerd", ("racknerd",), "RackNerd"),
    ProviderAdapter("vmiss", ("vmiss",), "VMISS"),
    ProviderAdapter("qqgnet", ("qqgnet", "qqgnet.net", "qqg"), "QQGNet"),
    ProviderAdapter("dedirock", ("dedirock",), "DediRock"),
)
ADAPTER_BY_NAME = {
    name: adapter
    for adapter in ADAPTERS
    for name in adapter.provider_names
}


def adapter_for(provider_name: object) -> ProviderAdapter:
    if not isinstance(provider_name, str):
        raise ProviderTelemetryError("provider_name_invalid")
    adapter = ADAPTER_BY_NAME.get(provider_name.strip().lower())
    if adapter is None:
        raise ProviderTelemetryError("provider_adapter_unavailable")
    return adapter


def adapter_names() -> tuple[str, ...]:
    return tuple(adapter.key for adapter in ADAPTERS)
