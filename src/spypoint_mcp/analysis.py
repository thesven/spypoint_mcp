"""Metadata-only activity summaries. Counts are photos, never inferred animals."""

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import SpypointError


def timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise SpypointError("Use a valid IANA timezone such as America/Toronto or UTC.") from None


def summarize(photos: list[dict], timezone_name: str, truncated: bool) -> dict:
    zone = timezone(timezone_name)
    hours = Counter()
    weekdays = Counter()
    days = Counter()
    buckets = Counter()
    tags = Counter()
    cameras = defaultdict(lambda: {"photos": 0, "tags": Counter(), "first": None, "last": None})
    for photo in photos:
        dt = datetime.fromisoformat(photo["date"].replace("Z", "+00:00")).astimezone(zone)
        day = dt.date().isoformat()
        hours[str(dt.hour)] += 1
        weekdays[str(dt.weekday())] += 1
        days[day] += 1
        # The offset distinguishes the two occurrences of 01:00 at DST fall-back.
        buckets[dt.replace(minute=0, second=0, microsecond=0).isoformat()] += 1
        unique_tags = set(photo.get("tags", []))
        tags.update(unique_tags)
        camera = cameras[photo["camera_id"]]
        camera["photos"] += 1
        camera["tags"].update(unique_tags)
        camera["first"] = min(camera["first"] or photo["date"], photo["date"])
        camera["last"] = max(camera["last"] or photo["date"], photo["date"])
    return {
        "scope": "local_catalog",
        "photo_count": len(photos),
        "truncated": truncated,
        "timezone": timezone_name,
        "by_day": dict(sorted(days.items())),
        "by_local_hour": {str(i): hours[str(i)] for i in range(24)},
        "by_weekday": {str(i): weekdays[str(i)] for i in range(7)},
        "hourly_timeline": dict(sorted(buckets.items())),
        "tag_counts": dict(tags.most_common()),
        "by_camera": [
            {"camera_id": cid, **value, "tags": dict(value["tags"])}
            for cid, value in sorted(cameras.items())
        ],
        "interpretation": "Counts describe indexed photographs, not unique animals, visits, or "
        "population estimates. Tags come from SPYPOINT metadata, not image analysis. "
        "Missing photos do not prove inactivity. Weekday 0 is Monday; tag counts can overlap.",
    }


def fleet_health(
    inventory: dict,
    parse_timestamp,
    stale_hours: float = 24,
    signal_threshold: float = 20,
    memory_threshold: float = 90,
) -> dict:
    if (
        not 0 < stale_hours <= 8760
        or not 0 <= signal_threshold <= 100
        or not 0 <= memory_threshold <= 100
    ):
        raise SpypointError("Health thresholds are outside supported ranges.")
    reference = datetime.now(UTC)
    results = []
    for camera in inventory["cameras"]:
        findings, unknown = [], []
        last_update = camera.get("last_update")
        age = None
        if last_update:
            try:
                update = datetime.fromisoformat(parse_timestamp(last_update).replace("Z", "+00:00"))
                age = (reference - update).total_seconds() / 3600
                if age < 0:
                    findings.append("status_timestamp_in_future")
                elif age > stale_hours:
                    findings.append("stale_status")
            except SpypointError:
                unknown.append("last_update")
        else:
            unknown.append("last_update")
        signal = camera.get("signal")
        signal = signal.get("processed", {}) if isinstance(signal, dict) else {}
        signal = signal.get("percentage") if isinstance(signal, dict) else None
        if isinstance(signal, (int, float)) and not isinstance(signal, bool) and 0 <= signal <= 100:
            if signal < signal_threshold:
                findings.append("low_signal")
        else:
            signal = None
            unknown.append("signal_percentage")
        memory = camera.get("memory")
        memory_used = None
        if isinstance(memory, dict):
            used, size = memory.get("used"), memory.get("size")
            if (
                isinstance(used, (int, float))
                and isinstance(size, (int, float))
                and not isinstance(used, bool)
                and not isinstance(size, bool)
                and math.isfinite(used)
                and math.isfinite(size)
                and size > 0
                and 0 <= used <= size
            ):
                memory_used = 100 * used / size
        if memory_used is None:
            unknown.append("memory_usage")
        elif memory_used >= memory_threshold:
            findings.append("memory_nearly_full")
        results.append(
            {
                "camera_id": camera["id"],
                "name": camera["name"],
                "status": "attention" if findings else "unknown" if unknown else "ok",
                "findings": findings,
                "unknown_measurements": unknown,
                "hours_since_update": round(age, 2) if age is not None else None,
                "signal_percentage": signal,
                "memory_used_percentage": memory_used,
                "batteries": camera.get("batteries"),
            }
        )
    return {
        "cameras": results,
        "checked_at": reference.isoformat(),
        "complete": inventory["complete"],
        "warnings": inventory["warnings"],
        "thresholds": {
            "stale_hours": stale_hours,
            "signal_below": signal_threshold,
            "memory_at_or_above": memory_threshold,
        },
        "note": "Health is based on last reported telemetry, not a live ping. "
        "Battery values are preserved as reported; unknown units are not interpreted.",
    }
