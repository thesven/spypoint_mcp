"""Activity, solunar, and telemetry trend summaries. Counts are photos, never inferred animals."""

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import SpypointError

SYNODIC_MONTH = 29.530588853
LUNAR_EPOCH = datetime(2000, 1, 6, 18, 14, 0, tzinfo=UTC)
J2000_EPOCH = datetime(2000, 1, 1, 12, 0, 0, tzinfo=UTC)
MOON_PHASES = (
    "new",
    "waxing_crescent",
    "first_quarter",
    "waxing_gibbous",
    "full",
    "waning_gibbous",
    "last_quarter",
    "waning_crescent",
)
SUN_PHASES = (
    "day",
    "civil_twilight",
    "nautical_twilight",
    "astronomical_twilight",
    "night",
)


def timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise SpypointError("Use a valid IANA timezone such as America/Toronto or UTC.") from None


def validate_coordinates(
    latitude: float | None, longitude: float | None
) -> tuple[float, float] | None:
    if latitude is None and longitude is None:
        return None
    if latitude is None or longitude is None:
        raise SpypointError("Provide both latitude and longitude or neither.")
    if (
        isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not isinstance(latitude, (int, float))
        or not isinstance(longitude, (int, float))
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise SpypointError("Latitude must be between -90 and 90; longitude between -180 and 180.")
    return float(latitude), float(longitude)


def moon_phase(dt: datetime) -> dict:
    utc_dt = dt.astimezone(UTC)
    age_days = ((utc_dt - LUNAR_EPOCH).total_seconds() / 86400.0) % SYNODIC_MONTH
    fraction = age_days / SYNODIC_MONTH
    illumination = round(50.0 * (1.0 - math.cos(2.0 * math.pi * fraction)), 1)
    index = int(((fraction + 0.0625) % 1.0) * 8.0)
    return {
        "phase": MOON_PHASES[index],
        "age_days": round(age_days, 2),
        "illumination_percentage": illumination,
    }


def solar_elevation(dt: datetime, latitude: float, longitude: float) -> float:
    lat, lon = validate_coordinates(latitude, longitude)  # type: ignore[misc]
    utc_dt = dt.astimezone(UTC)
    n = (utc_dt - J2000_EPOCH).total_seconds() / 86400.0
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    mean_anom = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic_lon = math.radians(
        (mean_lon + 1.915 * math.sin(mean_anom) + 0.020 * math.sin(2.0 * mean_anom)) % 360.0
    )
    obliquity = math.radians(23.439 - 0.0000004 * n)
    declination = math.asin(math.sin(obliquity) * math.sin(ecliptic_lon))
    right_ascension = math.atan2(
        math.cos(obliquity) * math.sin(ecliptic_lon), math.cos(ecliptic_lon)
    )
    gmst_deg = (280.46061837 + 360.98564736629 * n) % 360.0
    hour_angle = math.radians((gmst_deg + lon - math.degrees(right_ascension)) % 360.0)
    lat_rad = math.radians(lat)
    sin_alt = math.sin(lat_rad) * math.sin(declination) + math.cos(lat_rad) * math.cos(
        declination
    ) * math.cos(hour_angle)
    return round(math.degrees(math.asin(max(-1.0, min(1.0, sin_alt)))), 2)


def sun_phase(elevation: float) -> str:
    if elevation >= -0.833:
        return "day"
    if elevation >= -6.0:
        return "civil_twilight"
    if elevation >= -12.0:
        return "nautical_twilight"
    if elevation >= -18.0:
        return "astronomical_twilight"
    return "night"


def _is_unshifted_wall_clock_z(photo: dict) -> bool:
    captured = photo.get("captured_at")
    date = photo.get("date")
    if not (isinstance(captured, str) and isinstance(date, str)):
        return False
    if not captured.strip().endswith(("Z", "z", "+00:00", "-00:00")):
        return False
    try:
        cap = datetime.fromisoformat(captured.replace("Z", "+00:00")).astimezone(UTC)
        norm = datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return False
    return abs((cap - norm).total_seconds()) < 1


def _suspected_wall_clock_z(photo: dict, zone: ZoneInfo) -> bool:
    uploaded = photo.get("uploaded_at")
    date = photo.get("date")
    if not (
        _is_unshifted_wall_clock_z(photo) and isinstance(uploaded, str) and isinstance(date, str)
    ):
        return False
    try:
        cap = datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(UTC)
        upl = datetime.fromisoformat(uploaded.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return False
    if (cap - upl).total_seconds() > 60:
        return True
    wall_local = cap.replace(tzinfo=zone)
    offset = wall_local.utcoffset()
    if not offset or abs(offset.total_seconds()) < 1800:
        return False
    raw_lag = abs((upl - cap).total_seconds())
    wall_lag = (upl - wall_local.astimezone(UTC)).total_seconds()
    return raw_lag >= 1800 and -60 <= wall_lag <= 900


def _photo_local_dt(photo: dict, zone: ZoneInfo, reinterpret_utc_as_local: bool) -> datetime:
    parsed = datetime.fromisoformat(photo["date"].replace("Z", "+00:00"))
    if (
        reinterpret_utc_as_local
        and photo["date"].endswith("Z")
        and (photo.get("captured_at") is None or _is_unshifted_wall_clock_z(photo))
    ):
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def summarize(
    photos: list[dict],
    timezone_name: str,
    truncated: bool,
    reinterpret_utc_as_local: bool = False,
    camera_names: dict[str, str] | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    zone = timezone(timezone_name)
    coords = validate_coordinates(latitude, longitude)
    hours = Counter()
    weekdays = Counter()
    days = Counter()
    buckets = Counter()
    tags = Counter()
    moon_counts = Counter()
    sun_counts = Counter()
    suspected_wall_clock_z = 0
    cameras = defaultdict(lambda: {"photos": 0, "tags": Counter(), "first": None, "last": None})
    for photo in photos:
        dt = _photo_local_dt(photo, zone, reinterpret_utc_as_local)
        if _suspected_wall_clock_z(photo, zone):
            suspected_wall_clock_z += 1
        day = dt.date().isoformat()
        hours[str(dt.hour)] += 1
        weekdays[str(dt.weekday())] += 1
        days[day] += 1
        # The offset distinguishes the two occurrences of 01:00 at DST fall-back.
        buckets[dt.replace(minute=0, second=0, microsecond=0).isoformat()] += 1
        lunar = moon_phase(dt)
        moon_counts[lunar["phase"]] += 1
        if coords is not None:
            elev = solar_elevation(dt, coords[0], coords[1])
            sun_counts[sun_phase(elev)] += 1
        unique_tags = set(photo.get("tags", []))
        tags.update(unique_tags)
        camera = cameras[photo["camera_id"]]
        camera["photos"] += 1
        camera["tags"].update(unique_tags)
        camera["first"] = min(camera["first"] or photo["date"], photo["date"])
        camera["last"] = max(camera["last"] or photo["date"], photo["date"])
    warnings: list[str] = []
    if suspected_wall_clock_z and not reinterpret_utc_as_local:
        warnings.append(
            f"{suspected_wall_clock_z} photo(s) have capture/upload skew suggesting local "
            "wall-clock times stamped with Z; verify burned-in image timestamps or set "
            "reinterpret_utc_as_local=true."
        )
    return {
        "scope": "local_catalog",
        "photo_count": len(photos),
        "truncated": truncated,
        "timezone": timezone_name,
        "reinterpret_utc_as_local": reinterpret_utc_as_local,
        "suspected_wall_clock_z_photos": suspected_wall_clock_z,
        "timestamp_warnings": warnings,
        "by_day": dict(sorted(days.items())),
        "by_local_hour": {str(i): hours[str(i)] for i in range(24)},
        "by_weekday": {str(i): weekdays[str(i)] for i in range(7)},
        "by_moon_phase": {phase: moon_counts[phase] for phase in MOON_PHASES},
        "by_sun_phase": (
            {phase: sun_counts[phase] for phase in SUN_PHASES} if coords is not None else {}
        ),
        "hourly_timeline": dict(sorted(buckets.items())),
        "tag_counts": dict(tags.most_common()),
        "by_camera": [
            {
                "camera_id": cid,
                "camera_name": (camera_names or {}).get(cid),
                **value,
                "tags": dict(value["tags"]),
            }
            for cid, value in sorted(cameras.items())
        ],
        "interpretation": "Counts describe indexed photographs, not unique animals, visits, or "
        "population estimates. Tags come from SPYPOINT metadata, not image analysis. "
        "Missing photos do not prove inactivity. Weekday 0 is Monday; tag counts can overlap.",
    }


def solunar_summary(
    photos: list[dict],
    timezone_name: str,
    truncated: bool,
    latitude: float | None = None,
    longitude: float | None = None,
    reinterpret_utc_as_local: bool = False,
    camera_names: dict[str, str] | None = None,
) -> dict:
    zone = timezone(timezone_name)
    coords = validate_coordinates(latitude, longitude)
    moon_counts = Counter()
    sun_counts = Counter()
    illuminations: list[float] = []
    full_window = 0
    new_window = 0
    per_camera = defaultdict(
        lambda: {"photos": 0, "moon_phases": Counter(), "sun_phases": Counter()}
    )
    for photo in photos:
        dt = _photo_local_dt(photo, zone, reinterpret_utc_as_local)
        lunar = moon_phase(dt)
        phase = lunar["phase"]
        illum = lunar["illumination_percentage"]
        moon_counts[phase] += 1
        illuminations.append(illum)
        if illum >= 85.0:
            full_window += 1
        elif illum <= 15.0:
            new_window += 1
        cam_entry = per_camera[photo["camera_id"]]
        cam_entry["photos"] += 1
        cam_entry["moon_phases"][phase] += 1
        if coords is not None:
            s_phase = sun_phase(solar_elevation(dt, coords[0], coords[1]))
            sun_counts[s_phase] += 1
            cam_entry["sun_phases"][s_phase] += 1
    crepuscular = sun_counts["civil_twilight"] + sun_counts["nautical_twilight"]
    return {
        "scope": "local_catalog",
        "photo_count": len(photos),
        "truncated": truncated,
        "timezone": timezone_name,
        "coordinates_used": (
            {"latitude": coords[0], "longitude": coords[1]} if coords is not None else None
        ),
        "by_moon_phase": {p: moon_counts[p] for p in MOON_PHASES},
        "by_sun_phase": {p: sun_counts[p] for p in SUN_PHASES} if coords is not None else {},
        "crepuscular_photos": crepuscular if coords is not None else None,
        "day_photos": sun_counts["day"] if coords is not None else None,
        "night_photos": (
            sun_counts["night"] + sun_counts["astronomical_twilight"]
            if coords is not None
            else None
        ),
        "full_moon_window_photos": full_window,
        "new_moon_window_photos": new_window,
        "average_moon_illumination": (
            round(sum(illuminations) / len(illuminations), 1) if illuminations else None
        ),
        "by_camera": [
            {
                "camera_id": cid,
                "camera_name": (camera_names or {}).get(cid),
                "photos": val["photos"],
                "by_moon_phase": dict(val["moon_phases"]),
                "by_sun_phase": dict(val["sun_phases"]),
            }
            for cid, val in sorted(per_camera.items())
        ],
        "note": "Moon phase is global; twilight/solar elevation requires latitude and longitude. "
        "Counts represent indexed photos, not unique animal visits.",
    }


def _extract_battery_level(batteries: object) -> tuple[float | None, str | None]:
    if isinstance(batteries, (int, float)) and not isinstance(batteries, bool):
        if math.isfinite(batteries) and 0 <= batteries <= 100:
            return float(batteries), None
        return None, None
    if isinstance(batteries, list) and batteries:
        first = batteries[0]
        if isinstance(first, (int, float)) and not isinstance(first, bool):
            if math.isfinite(first) and 0 <= first <= 100:
                return float(first), None
        if isinstance(first, dict):
            val = first.get("percentage", first.get("value", first.get("level")))
            btype = first.get("type")
            if (
                isinstance(val, (int, float))
                and not isinstance(val, bool)
                and math.isfinite(val)
                and 0 <= val <= 100
            ):
                return float(val), str(btype) if btype else None
    if isinstance(batteries, dict):
        val = batteries.get("percentage", batteries.get("value", batteries.get("level")))
        btype = batteries.get("type")
        if (
            isinstance(val, (int, float))
            and not isinstance(val, bool)
            and math.isfinite(val)
            and 0 <= val <= 100
        ):
            return float(val), str(btype) if btype else None
    return None, None


def telemetry_trends(
    records_by_camera: dict[str, list[dict]],
    camera_names: dict[str, str] | None = None,
    critical_battery_threshold: float = 15.0,
) -> dict:
    if (
        isinstance(critical_battery_threshold, bool)
        or not isinstance(critical_battery_threshold, (int, float))
        or not math.isfinite(critical_battery_threshold)
        or not 0 <= critical_battery_threshold < 100
    ):
        raise SpypointError("critical_battery_threshold must be between 0 and 100.")
    trends = []
    for cid, records in sorted(records_by_camera.items()):
        ordered = sorted(records, key=lambda r: r["observed_at"])
        battery_pts: list[tuple[datetime, float]] = []
        signal_vals: list[float] = []
        for rec in ordered:
            try:
                dt = datetime.fromisoformat(rec["observed_at"].replace("Z", "+00:00")).astimezone(
                    UTC
                )
            except ValueError:
                continue
            blevel = rec.get("battery_level")
            if (
                isinstance(blevel, (int, float))
                and not isinstance(blevel, bool)
                and math.isfinite(blevel)
                and 0 <= blevel <= 100
            ):
                if battery_pts and blevel - battery_pts[-1][1] > 5.0:
                    # Battery recharge or replacement resets the discharge regression window.
                    battery_pts = []
                battery_pts.append((dt, float(blevel)))
            slevel = rec.get("signal_percentage")
            if (
                isinstance(slevel, (int, float))
                and not isinstance(slevel, bool)
                and math.isfinite(slevel)
                and 0 <= slevel <= 100
            ):
                signal_vals.append(float(slevel))
        drop_per_day = None
        days_to_critical = None
        latest_battery = battery_pts[-1][1] if battery_pts else None
        if len(battery_pts) >= 2:
            t0 = battery_pts[0][0]
            xs = [(pt[0] - t0).total_seconds() / 86400.0 for pt in battery_pts]
            ys = [pt[1] for pt in battery_pts]
            span_days = xs[-1] - xs[0]
            if span_days >= 1.0 / 24.0:
                mean_x = sum(xs) / len(xs)
                mean_y = sum(ys) / len(ys)
                denom = sum((x - mean_x) ** 2 for x in xs)
                if denom > 0:
                    slope = (
                        sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
                        / denom
                    )
                    drop_per_day = round(max(0.0, -slope), 2)
                    if (
                        drop_per_day > 0
                        and latest_battery is not None
                        and latest_battery > critical_battery_threshold
                    ):
                        days_to_critical = round(
                            (latest_battery - critical_battery_threshold) / drop_per_day, 1
                        )
        findings = []
        if drop_per_day is not None and drop_per_day >= 3.0:
            findings.append("rapid_battery_drain")
        if days_to_critical is not None and days_to_critical <= 7.0:
            findings.append("critical_battery_soon")
        sig_min = round(min(signal_vals), 1) if signal_vals else None
        sig_max = round(max(signal_vals), 1) if signal_vals else None
        sig_avg = round(sum(signal_vals) / len(signal_vals), 1) if signal_vals else None
        if sig_min is not None and sig_max is not None and (sig_max - sig_min) >= 35.0:
            findings.append("unstable_signal")
        trends.append(
            {
                "camera_id": cid,
                "camera_name": (camera_names or {}).get(cid),
                "observations": len(ordered),
                "first_observed_at": ordered[0]["observed_at"] if ordered else None,
                "last_observed_at": ordered[-1]["observed_at"] if ordered else None,
                "latest_battery_level": latest_battery,
                "battery_drop_per_day": drop_per_day,
                "estimated_days_to_critical": days_to_critical,
                "signal_min": sig_min,
                "signal_max": sig_max,
                "signal_average": sig_avg,
                "findings": findings,
            }
        )
    return {
        "cameras": trends,
        "critical_battery_threshold": critical_battery_threshold,
        "scope": "local_catalog",
        "note": (
            "Trends require at least two telemetry snapshots spanning 1+ hours "
            "in the local catalog."
        ),
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
        if (
            isinstance(signal, (int, float))
            and not isinstance(signal, bool)
            and math.isfinite(signal)
            and 0 <= signal <= 100
        ):
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
