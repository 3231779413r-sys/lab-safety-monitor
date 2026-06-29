from __future__ import annotations

from datetime import datetime
from typing import Any


TelemetryMap = dict[str, Any]


def telemetry_now(*, at: datetime | None = None) -> str:
    return (at or datetime.now()).isoformat(timespec="milliseconds")


def clone_telemetry(telemetry: TelemetryMap | None) -> TelemetryMap:
    return dict(telemetry or {})


def mark_telemetry(
    telemetry: TelemetryMap | None,
    key: str,
    *,
    at: datetime | None = None,
    value: Any | None = None,
) -> TelemetryMap:
    updated = clone_telemetry(telemetry)
    updated[key] = telemetry_now(at=at) if value is None else value
    return updated


def merge_telemetry(*parts: TelemetryMap | None) -> TelemetryMap:
    merged: TelemetryMap = {}
    for part in parts:
        if part:
            merged.update(part)
    return merged


def telemetry_duration_ms(
    telemetry: TelemetryMap | None,
    start_key: str,
    end_key: str,
) -> float | None:
    if not telemetry:
        return None
    start_value = telemetry.get(start_key)
    end_value = telemetry.get(end_key)
    if not start_value or not end_value:
        return None
    try:
        start_at = datetime.fromisoformat(str(start_value))
        end_at = datetime.fromisoformat(str(end_value))
    except ValueError:
        return None
    return round((end_at - start_at).total_seconds() * 1000.0, 1)
