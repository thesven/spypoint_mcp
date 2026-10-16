"""Weather provider, meteorological conversions, and animal tracking environmental models."""

from datetime import UTC, date, datetime, timedelta

import httpx

from .config import SpypointError

# WMO Weather Interpretation Codes (WW)
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear", "clear"),
    1: ("Mainly Clear", "clear"),
    2: ("Partly Cloudy", "partly_cloudy"),
    3: ("Overcast", "overcast"),
    45: ("Fog", "fog"),
    48: ("Freezing Fog", "fog"),
    51: ("Light Drizzle", "drizzle"),
    53: ("Moderate Drizzle", "drizzle"),
    55: ("Dense Drizzle", "drizzle"),
    56: ("Light Freezing Drizzle", "drizzle"),
    57: ("Dense Freezing Drizzle", "drizzle"),
    61: ("Slight Rain", "rain"),
    63: ("Moderate Rain", "rain"),
    65: ("Heavy Rain", "rain"),
    66: ("Light Freezing Rain", "rain"),
    67: ("Heavy Freezing Rain", "rain"),
    71: ("Slight Snow", "snow"),
    73: ("Moderate Snow", "snow"),
    75: ("Heavy Snow", "snow"),
    77: ("Snow Grains", "snow"),
    80: ("Slight Rain Showers", "rain"),
    81: ("Moderate Rain Showers", "rain"),
    82: ("Violent Rain Showers", "rain"),
    85: ("Slight Snow Showers", "snow"),
    86: ("Heavy Snow Showers", "snow"),
    95: ("Thunderstorm", "thunderstorm"),
    96: ("Thunderstorm with Hail", "thunderstorm"),
    99: ("Severe Thunderstorm with Hail", "thunderstorm"),
}

COMPASS_16 = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)

COMPASS_8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def c_to_f(celsius: float) -> float:
    return round(celsius * 1.8 + 32.0, 1)


def f_to_c(fahrenheit: float) -> float:
    return round((fahrenheit - 32.0) / 1.8, 1)


def kmh_to_mph(kmh: float) -> float:
    return round(kmh * 0.621371, 1)


def mm_to_inches(mm: float) -> float:
    return round(mm * 0.0393701, 2)


def hpa_to_inhg(hpa: float) -> float:
    return round(hpa * 0.02953, 2)


def degrees_to_compass_16(degrees: float) -> str:
    deg = degrees % 360.0
    idx = int((deg + 11.25) // 22.5) % 16
    return COMPASS_16[idx]


def degrees_to_compass_8(degrees: float) -> str:
    deg = degrees % 360.0
    idx = int((deg + 22.5) // 45.0) % 8
    return COMPASS_8[idx]


def interpret_wmo_code(code: int) -> tuple[str, str]:
    return WMO_CODES.get(code, ("Unknown", "unknown"))


def round_grid_coordinate(coord: float) -> float:
    """Round coordinate to 2 decimal places (~1.1 km grid cell) for caching & privacy."""
    return round(coord, 2)


def normalize_hour_utc(dt: datetime) -> str:
    """Normalize datetime to top of the hour UTC in ISO 8601 format."""
    if dt.tzinfo is None:
        raise SpypointError("Datetime must be timezone-aware.")
    dt_utc = dt.astimezone(UTC)
    # Nearest or floor hour? Floor hour is standard for hourly meteorological intervals
    floored = dt_utc.replace(minute=0, second=0, microsecond=0)
    return floored.strftime("%Y-%m-%dT%H:00:00Z")


def calculate_pressure_trend(
    current_hpa: float, prior_3h_hpa: float | None
) -> tuple[float | None, str]:
    """Calculate 3-hour barometric delta and categorize movement trigger.

    In animal biology, falling pressure signals arriving storm fronts and spurs feeding.
    Rising pressure signals post-front clearing.
    """
    if prior_3h_hpa is None:
        return None, "steady"
    delta = round(current_hpa - prior_3h_hpa, 1)
    if delta <= -2.0:
        trend = "rapidly_falling"
    elif delta <= -0.5:
        trend = "falling"
    elif delta >= 2.0:
        trend = "rapidly_rising"
    elif delta >= 0.5:
        trend = "rising"
    else:
        trend = "steady"
    return delta, trend


def build_tracking_summary(
    temp_c: float,
    apparent_temp_c: float,
    wind_speed_kmh: float,
    wind_gusts_kmh: float,
    wind_dir_deg: float,
    pressure_hpa: float,
    pressure_trend: str,
    pressure_delta: float | None,
    weather_desc: str,
    humidity: float,
    dew_point_c: float,
) -> str:
    """Build a concise, expert animal-tracking weather summary."""
    temp_f = c_to_f(temp_c)
    app_f = c_to_f(apparent_temp_c)
    wind_mph = kmh_to_mph(wind_speed_kmh)
    gust_mph = kmh_to_mph(wind_gusts_kmh)
    compass = degrees_to_compass_16(wind_dir_deg)
    delta_str = f" ({pressure_delta:+.1f} hPa/3h)" if pressure_delta is not None else ""
    trend_symbol = {
        "rapidly_falling": "↓↓",
        "falling": "↘",
        "steady": "→",
        "rising": "↗",
        "rapidly_rising": "↑↑",
    }.get(pressure_trend, "→")
    return (
        f"{temp_f}°F (Apparent {app_f}°F) | "
        f"Wind {compass} {wind_mph} mph (Gust {gust_mph} mph) | "
        f"Barometer {pressure_hpa:.1f} hPa {trend_symbol} {pressure_trend}{delta_str} | "
        f"{weather_desc} | "
        f"Humidity {humidity:.0f}% (Dew Pt {c_to_f(dew_point_c)}°F)"
    )


class OpenMeteoClient:
    """Async client for fetching historical and forecast hourly meteorological data."""

    ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    HOURLY_VARIABLES = (
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "apparent_temperature",
        "surface_pressure",
        "precipitation",
        "snowfall",
        "weather_code",
        "cloud_cover",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
    )

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        api_key: str | None = None,
    ):
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "spypoint-mcp/0.2.0 (trail-camera-wildlife-tracking)"},
        )
        self.api_key = api_key

    async def close(self) -> None:
        if not self._external_client:
            await self._client.aclose()

    async def fetch_hourly_range(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        """Fetch hourly weather records for a coordinate bounding box and date range.

        Automatically selects the archive endpoint for historical queries (>5 days ago)
        and the forecast endpoint for recent/current dates.
        """
        grid_lat = round_grid_coordinate(latitude)
        grid_lon = round_grid_coordinate(longitude)
        today = datetime.now(UTC).date()
        five_days_ago = today - timedelta(days=5)

        observations: list[dict] = []
        # If the range spans both archive and forecast, split them
        if start_date <= five_days_ago and end_date > five_days_ago:
            archive_obs = await self._query_endpoint(
                self.ARCHIVE_URL, grid_lat, grid_lon, start_date, five_days_ago
            )
            forecast_start = five_days_ago + timedelta(days=1)
            forecast_obs = await self._query_endpoint(
                self.FORECAST_URL, grid_lat, grid_lon, forecast_start, end_date
            )
            observations = archive_obs + forecast_obs
        elif end_date <= five_days_ago:
            observations = await self._query_endpoint(
                self.ARCHIVE_URL, grid_lat, grid_lon, start_date, end_date
            )
        else:
            observations = await self._query_endpoint(
                self.FORECAST_URL, grid_lat, grid_lon, start_date, end_date
            )

        return observations

    async def _query_endpoint(
        self,
        base_url: str,
        lat: float,
        lon: float,
        start: date,
        end: date,
    ) -> list[dict]:
        results: list[dict] = []
        curr_start = start
        while curr_start <= end:
            curr_end = min(curr_start + timedelta(days=180), end)
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": curr_start.isoformat(),
                "end_date": curr_end.isoformat(),
                "hourly": ",".join(self.HOURLY_VARIABLES),
                "timezone": "UTC",
            }
            if self.api_key:
                params["apikey"] = self.api_key

            try:
                resp = await self._client.get(base_url, params=params)
            except httpx.RequestError as exc:
                raise SpypointError(f"Weather request failed: {exc}") from exc

            if resp.status_code != 200:
                raise SpypointError(f"Weather provider error {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            if not times:
                curr_start = curr_end + timedelta(days=1)
                continue

            temps = hourly.get("temperature_2m", [])
            rel_hum = hourly.get("relative_humidity_2m", [])
            dew_pts = hourly.get("dew_point_2m", [])
            app_temps = hourly.get("apparent_temperature", [])
            pressures = hourly.get("surface_pressure", [])
            precip = hourly.get("precipitation", [])
            snow = hourly.get("snowfall", [])
            w_codes = hourly.get("weather_code", [])
            clouds = hourly.get("cloud_cover", [])
            w_speeds = hourly.get("wind_speed_10m", [])
            w_dirs = hourly.get("wind_direction_10m", [])
            w_gusts = hourly.get("wind_gusts_10m", [])

            now_iso = datetime.now(UTC).isoformat()
            for i, time_str in enumerate(times):
                # Formats like "2026-10-10T14:00" -> normalized to "2026-10-10T14:00:00Z"
                hour_utc = f"{time_str}:00Z" if len(time_str) == 16 else time_str
                if not hour_utc.endswith("Z"):
                    hour_utc += "Z"

                w_code = int(w_codes[i]) if i < len(w_codes) and w_codes[i] is not None else 0
                desc, cat = interpret_wmo_code(w_code)

                val_temp = float(temps[i]) if i < len(temps) and temps[i] is not None else 0.0
                val_app = (
                    float(app_temps[i])
                    if i < len(app_temps) and app_temps[i] is not None
                    else val_temp
                )
                val_hum = float(rel_hum[i]) if i < len(rel_hum) and rel_hum[i] is not None else 0.0
                val_dew = (
                    float(dew_pts[i]) if i < len(dew_pts) and dew_pts[i] is not None else val_temp
                )
                val_press = (
                    float(pressures[i])
                    if i < len(pressures) and pressures[i] is not None
                    else 1013.25
                )
                val_precip = float(precip[i]) if i < len(precip) and precip[i] is not None else 0.0
                val_snow = float(snow[i]) if i < len(snow) and snow[i] is not None else 0.0
                val_cloud = float(clouds[i]) if i < len(clouds) and clouds[i] is not None else 0.0
                val_wspeed = (
                    float(w_speeds[i]) if i < len(w_speeds) and w_speeds[i] is not None else 0.0
                )
                val_wdir = float(w_dirs[i]) if i < len(w_dirs) and w_dirs[i] is not None else 0.0
                val_wgust = (
                    float(w_gusts[i]) if i < len(w_gusts) and w_gusts[i] is not None else val_wspeed
                )

                results.append(
                    {
                        "grid_lat": lat,
                        "grid_lon": lon,
                        "hour_utc": hour_utc,
                        "temperature_c": val_temp,
                        "apparent_temperature_c": val_app,
                        "relative_humidity": val_hum,
                        "dew_point_c": val_dew,
                        "pressure_hpa": val_press,
                        "wind_speed_kmh": val_wspeed,
                        "wind_direction_deg": val_wdir,
                        "wind_gusts_kmh": val_wgust,
                        "precipitation_mm": val_precip,
                        "snowfall_cm": val_snow,
                        "cloud_cover_pct": val_cloud,
                        "weather_code": w_code,
                        "weather_condition": desc,
                        "weather_category": cat,
                        "fetched_at": now_iso,
                    }
                )
            curr_start = curr_end + timedelta(days=1)
        return results
