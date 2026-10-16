"""Tests for weather provider, meteorological conversions, caching, and MCP tools."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import photo
from mcp import Client

from spypoint_mcp.analysis import (
    categorize_temperature,
    categorize_wind_speed,
    summarize_weather_activity,
)
from spypoint_mcp.catalog import Catalog
from spypoint_mcp.client import SpypointClient, photo_summary
from spypoint_mcp.config import Settings, SpypointError
from spypoint_mcp.server import create_server
from spypoint_mcp.service import PhotoService
from spypoint_mcp.weather import (
    OpenMeteoClient,
    build_tracking_summary,
    c_to_f,
    calculate_pressure_trend,
    degrees_to_compass_8,
    degrees_to_compass_16,
    f_to_c,
    hpa_to_inhg,
    interpret_wmo_code,
    kmh_to_mph,
    mm_to_inches,
    normalize_hour_utc,
    round_grid_coordinate,
)


def test_meteorological_conversions_and_formatting():
    assert c_to_f(0.0) == 32.0
    assert c_to_f(10.0) == 50.0
    assert f_to_c(32.0) == 0.0
    assert f_to_c(50.0) == 10.0
    assert kmh_to_mph(10.0) == 6.2
    assert mm_to_inches(25.4) == 1.0
    assert hpa_to_inhg(1013.25) == 29.92

    # Compass 16
    assert degrees_to_compass_16(0) == "N"
    assert degrees_to_compass_16(45) == "NE"
    assert degrees_to_compass_16(90) == "E"
    assert degrees_to_compass_16(180) == "S"
    assert degrees_to_compass_16(270) == "W"
    assert degrees_to_compass_16(315) == "NW"
    assert degrees_to_compass_16(360) == "N"
    assert degrees_to_compass_16(720) == "N"

    # Compass 8
    assert degrees_to_compass_8(0) == "N"
    assert degrees_to_compass_8(45) == "NE"
    assert degrees_to_compass_8(90) == "E"
    assert degrees_to_compass_8(135) == "SE"
    assert degrees_to_compass_8(180) == "S"
    assert degrees_to_compass_8(225) == "SW"
    assert degrees_to_compass_8(270) == "W"
    assert degrees_to_compass_8(315) == "NW"

    # WMO codes
    desc, cat = interpret_wmo_code(0)
    assert desc == "Clear" and cat == "clear"
    desc, cat = interpret_wmo_code(71)
    assert desc == "Slight Snow" and cat == "snow"
    desc, cat = interpret_wmo_code(9999)
    assert desc == "Unknown" and cat == "unknown"

    # Grid rounding & hour normalization
    assert round_grid_coordinate(44.12345) == 44.12
    assert round_grid_coordinate(-78.9876) == -78.99

    dt = datetime(2026, 10, 10, 14, 23, 45, tzinfo=UTC)
    assert normalize_hour_utc(dt) == "2026-10-10T14:00:00Z"
    with pytest.raises(SpypointError, match="timezone-aware"):
        normalize_hour_utc(datetime(2026, 10, 10, 14, 23))

    # Pressure trend
    delta, trend = calculate_pressure_trend(1010.0, None)
    assert delta is None and trend == "steady"
    delta, trend = calculate_pressure_trend(1010.0, 1013.0)
    assert delta == -3.0 and trend == "rapidly_falling"
    delta, trend = calculate_pressure_trend(1010.0, 1011.0)
    assert delta == -1.0 and trend == "falling"
    delta, trend = calculate_pressure_trend(1010.0, 1010.2)
    assert delta == -0.2 and trend == "steady"
    delta, trend = calculate_pressure_trend(1012.0, 1011.0)
    assert delta == 1.0 and trend == "rising"
    delta, trend = calculate_pressure_trend(1014.0, 1011.0)
    assert delta == 3.0 and trend == "rapidly_rising"

    # Summary string
    summary = build_tracking_summary(
        temp_c=5.0,
        apparent_temp_c=2.0,
        wind_speed_kmh=15.0,
        wind_gusts_kmh=25.0,
        wind_dir_deg=315.0,
        pressure_hpa=1012.5,
        pressure_trend="falling",
        pressure_delta=-1.2,
        weather_desc="Overcast",
        humidity=75.0,
        dew_point_c=1.0,
    )
    assert "41.0°F" in summary
    assert "Wind NW" in summary
    assert "Barometer 1012.5 hPa ↘ falling (-1.2 hPa/3h)" in summary
    assert "Overcast" in summary


def _sample_open_meteo_payload(times: list[str]) -> dict:
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [10.5 + i for i in range(len(times))],
            "relative_humidity_2m": [70.0 for _ in times],
            "dew_point_2m": [5.0 for _ in times],
            "apparent_temperature": [9.0 for _ in times],
            "surface_pressure": [1015.0 - i for i in range(len(times))],
            "precipitation": [0.0 for _ in times],
            "snowfall": [0.0 for _ in times],
            "weather_code": [0 for _ in times],
            "cloud_cover": [20.0 for _ in times],
            "wind_speed_10m": [12.0 for _ in times],
            "wind_direction_10m": [270.0 for _ in times],
            "wind_gusts_10m": [18.0 for _ in times],
        }
    }


async def test_open_meteo_client_archive_and_forecast_split():
    mock_responses = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        mock_responses.append(url)
        if "start_date" in request.url.params:
            start = request.url.params["start_date"]
            return httpx.Response(
                200, json=_sample_open_meteo_payload([f"{start}T00:00", f"{start}T01:00"])
            )
        return httpx.Response(200, json={"hourly": {"time": []}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        weather = OpenMeteoClient(client=client, api_key="TEST_KEY")
        # Split query: 10 days ago to today
        today = datetime.now(UTC).date()
        obs = await weather.fetch_hourly_range(45.0, -75.0, today - timedelta(days=10), today)
        assert len(obs) == 4
        assert any("archive-api.open-meteo.com" in url for url in mock_responses)
        assert any("api.open-meteo.com/v1/forecast" in url for url in mock_responses)
        assert all("apikey=TEST_KEY" in url for url in mock_responses)


async def test_open_meteo_client_error_handling():
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Rate limit exceeded")

    transport = httpx.MockTransport(error_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        weather = OpenMeteoClient(client=client)
        today = datetime.now(UTC).date()
        with pytest.raises(SpypointError, match="Weather provider error 429"):
            await weather.fetch_hourly_range(45.0, -75.0, today - timedelta(days=2), today)

    # Transport failure
    def fail_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Network unreachable")

    transport_fail = httpx.MockTransport(fail_handler)
    async with httpx.AsyncClient(transport=transport_fail) as client:
        weather = OpenMeteoClient(client=client)
        with pytest.raises(SpypointError, match="Weather request failed"):
            await weather.fetch_hourly_range(45.0, -75.0, today - timedelta(days=2), today)


async def test_catalog_weather_caching_and_summary(tmp_path):
    settings = Settings(username="hunter", password="SECRET", state_dir=tmp_path)
    service = PhotoService(SpypointClient(settings))
    cat = service.catalog
    observations = [
        {
            "grid_lat": 44.5,
            "grid_lon": -78.2,
            "hour_utc": "2026-10-10T12:00:00Z",
            "temperature_c": 12.0,
            "apparent_temperature_c": 10.0,
            "relative_humidity": 65.0,
            "dew_point_c": 5.0,
            "pressure_hpa": 1018.0,
            "wind_speed_kmh": 14.0,
            "wind_direction_deg": 315.0,
            "wind_gusts_kmh": 22.0,
            "precipitation_mm": 0.0,
            "snowfall_cm": 0.0,
            "cloud_cover_pct": 10.0,
            "weather_code": 0,
            "weather_condition": "Clear",
            "weather_category": "clear",
            "fetched_at": "2026-10-10T12:05:00Z",
        },
        {
            "grid_lat": 44.5,
            "grid_lon": -78.2,
            "hour_utc": "2026-10-10T09:00:00Z",
            "temperature_c": 8.0,
            "apparent_temperature_c": 6.0,
            "relative_humidity": 80.0,
            "dew_point_c": 4.0,
            "pressure_hpa": 1020.0,
            "wind_speed_kmh": 8.0,
            "wind_direction_deg": 300.0,
            "wind_gusts_kmh": 12.0,
            "precipitation_mm": 0.0,
            "snowfall_cm": 0.0,
            "cloud_cover_pct": 5.0,
            "weather_code": 0,
            "weather_condition": "Clear",
            "weather_category": "clear",
            "fetched_at": "2026-10-10T12:05:00Z",
        },
    ]
    stored = await cat.store_weather_observations(observations)
    assert stored == 2

    # Query cached
    cached = await cat.get_cached_weather(
        44.5, -78.2, ["2026-10-10T12:00:00Z", "2026-10-10T09:00:00Z", "2026-10-10T00:00:00Z"]
    )
    assert len(cached) == 2
    assert cached["2026-10-10T12:00:00Z"]["temperature_c"] == 12.0

    # Coverage
    cov = await cat.get_weather_coverage(44.5, -78.2)
    assert cov["count"] == 2
    assert cov["oldest"] == "2026-10-10T09:00:00Z"
    assert cov["newest"] == "2026-10-10T12:00:00Z"

    # Summary
    summary = await cat.summary()
    assert summary["schema_version"] == 4
    assert summary["weather_observations"] == 2


async def test_photo_weather_and_sync_weather_service(api, fake):
    service = PhotoService(api)
    fake.rows = [
        photo("p1", "c1", date="2026-10-10T14:25:00.000Z"),
        photo("p2", "c1", date="2026-10-10T15:10:00.000Z"),
    ]
    await service.catalog.remember([photo_summary(r) for r in fake.rows])

    # Missing coordinates raises SpypointError
    with pytest.raises(SpypointError, match="GPS coordinates required"):
        await service.photo_weather("p1")

    # Mock OpenMeteoClient
    mock_weather = AsyncMock(spec=OpenMeteoClient)
    sample_obs = [
        {
            "grid_lat": 44.5,
            "grid_lon": -78.2,
            "hour_utc": "2026-10-10T14:00:00Z",
            "temperature_c": 15.0,
            "apparent_temperature_c": 14.0,
            "relative_humidity": 60.0,
            "dew_point_c": 7.0,
            "pressure_hpa": 1012.0,
            "wind_speed_kmh": 12.0,
            "wind_direction_deg": 315.0,
            "wind_gusts_kmh": 20.0,
            "precipitation_mm": 0.0,
            "snowfall_cm": 0.0,
            "cloud_cover_pct": 30.0,
            "weather_code": 2,
            "weather_condition": "Partly Cloudy",
            "weather_category": "partly_cloudy",
            "fetched_at": "2026-10-10T16:00:00Z",
        },
        {
            "grid_lat": 44.5,
            "grid_lon": -78.2,
            "hour_utc": "2026-10-10T11:00:00Z",
            "temperature_c": 11.0,
            "apparent_temperature_c": 10.0,
            "relative_humidity": 75.0,
            "dew_point_c": 6.0,
            "pressure_hpa": 1015.0,
            "wind_speed_kmh": 8.0,
            "wind_direction_deg": 300.0,
            "wind_gusts_kmh": 14.0,
            "precipitation_mm": 0.0,
            "snowfall_cm": 0.0,
            "cloud_cover_pct": 10.0,
            "weather_code": 1,
            "weather_condition": "Mainly Clear",
            "weather_category": "clear",
            "fetched_at": "2026-10-10T16:00:00Z",
        },
    ]
    mock_weather.fetch_hourly_range.return_value = sample_obs
    service._weather = mock_weather

    # First fetch (not cached)
    report = await service.photo_weather("p1", latitude=44.5, longitude=-78.2)
    assert report["photo_id"] == "p1"
    assert report["temperature_c"] == 15.0
    assert report["temperature_f"] == 59.0
    assert report["pressure_trend_3h"] == "rapidly_falling"
    assert report["pressure_delta_3h_hpa"] == -3.0
    assert report["wind_compass"] == "NW"
    assert report["cached"] is False
    assert "Wind NW" in report["tracking_summary"]

    # Second fetch should hit SQLite cache
    mock_weather.fetch_hourly_range.reset_mock()
    cached_report = await service.photo_weather("p1", latitude=44.5, longitude=-78.2)
    assert cached_report["cached"] is True
    mock_weather.fetch_hourly_range.assert_not_called()

    # Sync weather
    sync_res = await service.sync_weather(camera_ids=["c1"], latitude=44.5, longitude=-78.2)
    assert sync_res["observations_stored"] == 2
    assert sync_res["grid_latitude"] == 44.5


def test_categorize_weather_activity():
    assert categorize_temperature(-5.0) == "below_freezing"
    assert categorize_temperature(5.0) == "cold_32_45"
    assert categorize_temperature(12.0) == "mild_45_60"
    assert categorize_temperature(20.0) == "warm_60_75"
    assert categorize_temperature(30.0) == "hot_above_75"

    assert categorize_wind_speed(4.0) == "calm"
    assert categorize_wind_speed(10.0) == "light"
    assert categorize_wind_speed(20.0) == "moderate"
    assert categorize_wind_speed(35.0) == "high"

    photos = [
        {"id": "p1", "camera_id": "c1", "date": "2026-10-10T14:00:00.000Z"},
        {"id": "p2", "camera_id": "c1", "date": "2026-10-10T15:00:00.000Z"},
        {"id": "p3", "camera_id": "c1", "date": "2026-10-10T16:00:00.000Z"},
    ]
    weather_by_hour = {
        "2026-10-10T14:00:00Z": {
            "temperature_c": 5.0,
            "pressure_trend_3h": "falling",
            "wind_direction_deg": 315.0,
            "wind_speed_kmh": 12.0,
            "weather_category": "overcast",
        },
        "2026-10-10T15:00:00Z": {
            "temperature_c": 7.0,
            "pressure_trend_3h": "rapidly_falling",
            "wind_direction_deg": 315.0,
            "wind_speed_kmh": 15.0,
            "weather_category": "rain",
        },
    }
    summary = summarize_weather_activity(
        photos=photos,
        weather_by_hour=weather_by_hour,
        timezone_name="America/Toronto",
        truncated=False,
        latitude=44.5,
        longitude=-78.2,
    )
    assert summary["photo_count"] == 3
    assert summary["photos_with_weather"] == 2
    assert summary["by_temperature_band"]["cold_32_45"] == 2
    assert summary["by_pressure_trend"]["falling"] == 1
    assert summary["by_pressure_trend"]["rapidly_falling"] == 1
    assert summary["by_wind_direction"]["NW"] == 2
    assert summary["by_weather_condition"]["rain"] == 1


async def test_mcp_weather_tools_roundtrip(api, fake):
    fake.rows = [
        photo("p1", "c1", date="2026-10-10T14:25:00.000Z"),
    ]
    settings = replace(api.settings, default_latitude=45.12, default_longitude=-78.45)
    api_with_coords = SpypointClient(settings, httpx.MockTransport(fake))

    mock_weather = AsyncMock(spec=OpenMeteoClient)
    mock_weather.fetch_hourly_range.return_value = [
        {
            "grid_lat": 45.12,
            "grid_lon": -78.45,
            "hour_utc": "2026-10-10T14:00:00Z",
            "temperature_c": 8.0,
            "apparent_temperature_c": 5.0,
            "relative_humidity": 70.0,
            "dew_point_c": 3.0,
            "pressure_hpa": 1016.0,
            "wind_speed_kmh": 16.0,
            "wind_direction_deg": 270.0,
            "wind_gusts_kmh": 24.0,
            "precipitation_mm": 0.0,
            "snowfall_cm": 0.0,
            "cloud_cover_pct": 50.0,
            "weather_code": 2,
            "weather_condition": "Partly Cloudy",
            "weather_category": "partly_cloudy",
            "fetched_at": "2026-10-10T16:00:00Z",
        },
        {
            "grid_lat": 45.12,
            "grid_lon": -78.45,
            "hour_utc": "2026-10-10T11:00:00Z",
            "temperature_c": 4.0,
            "apparent_temperature_c": 1.0,
            "relative_humidity": 80.0,
            "dew_point_c": 1.0,
            "pressure_hpa": 1018.0,
            "wind_speed_kmh": 10.0,
            "wind_direction_deg": 260.0,
            "wind_gusts_kmh": 15.0,
            "precipitation_mm": 0.0,
            "snowfall_cm": 0.0,
            "cloud_cover_pct": 20.0,
            "weather_code": 1,
            "weather_condition": "Mainly Clear",
            "weather_category": "clear",
            "fetched_at": "2026-10-10T16:00:00Z",
        },
    ]

    server = create_server(client=api_with_coords, weather_client=mock_weather)
    async with Client(server, raise_exceptions=True) as mcp:
        tools = (await mcp.list_tools()).tools
        tool_names = {t.name for t in tools}
        assert "get_photo_weather" in tool_names
        assert "sync_weather" in tool_names
        assert "weather_activity_summary" in tool_names

        # Index photo into local catalog
        await mcp.call_tool("list_photos", {})

        # Test sync_weather
        sync_res = await mcp.call_tool(
            "sync_weather",
            {"camera_ids": ["c1"], "latitude": 45.12, "longitude": -78.45},
        )
        assert not sync_res.is_error
        assert sync_res.structured_content["hours_fetched"] == 2
        assert sync_res.structured_content["observations_stored"] == 2
        assert sync_res.structured_content["truncated"] is False

        # Test get_photo_weather
        photo_res = await mcp.call_tool("get_photo_weather", {"photo_id": "p1"})
        assert not photo_res.is_error
        data = photo_res.structured_content
        assert data["temperature_c"] == 8.0
        assert data["temperature_f"] == 46.4
        assert data["wind_compass"] == "W"
        assert data["pressure_trend_3h"] == "rapidly_falling"
        assert data["pressure_delta_3h_hpa"] == -2.0
        assert "Wind W" in data["tracking_summary"]

        # Test weather_activity_summary
        summary_res = await mcp.call_tool(
            "weather_activity_summary",
            {"camera_ids": ["c1"], "timezone_name": "America/Toronto"},
        )
        assert not summary_res.is_error
        sdata = summary_res.structured_content
        assert sdata["photo_count"] == 1
        assert sdata["photos_with_weather"] == 1
        assert sdata["by_temperature_band"]["cold_32_45"] == 0
        assert sdata["by_temperature_band"]["mild_45_60"] == 1
        assert sdata["by_wind_direction"]["W"] == 1
        assert sdata["by_pressure_trend"]["rapidly_falling"] == 1


async def test_service_weather_client_lifecycle_and_ownership(api):
    # Injected client should NOT be closed by service.close()
    injected_client = AsyncMock(spec=OpenMeteoClient)
    service = PhotoService(api, weather_client=injected_client)
    assert service.weather_client is injected_client
    await service.close()
    injected_client.close.assert_not_called()

    # Internally created client SHOULD be closed by service.close()
    service_internal = PhotoService(api)
    internal_client = service_internal.weather_client
    assert isinstance(internal_client, OpenMeteoClient)
    internal_mock_close = AsyncMock()
    internal_client.close = internal_mock_close
    await service_internal.close()
    internal_mock_close.assert_awaited_once()


async def test_catalog_photo_date_bounds(api):
    catalog = Catalog(api.settings)
    # Empty catalog
    empty = await catalog.photo_date_bounds()
    assert empty["count"] == 0
    assert empty["oldest"] is None
    assert empty["newest"] is None
    assert empty["camera_ids"] == []

    # Remember multiple photos across cameras
    await catalog.remember(
        [
            photo_summary(photo("p1", "cam-a", date="2026-09-01T10:00:00.000Z")),
            photo_summary(photo("p2", "cam-b", date="2026-09-15T12:00:00.000Z")),
            photo_summary(photo("p3", "cam-a", date="2026-09-30T18:00:00.000Z")),
        ]
    )

    bounds = await catalog.photo_date_bounds()
    assert bounds["count"] == 3
    assert bounds["oldest"] == "2026-09-01T10:00:00.000Z"
    assert bounds["newest"] == "2026-09-30T18:00:00.000Z"
    assert bounds["camera_ids"] == ["cam-a", "cam-b"]

    # Filter by camera
    bounds_cam_a = await catalog.photo_date_bounds(camera_ids=["cam-a"])
    assert bounds_cam_a["count"] == 2
    assert bounds_cam_a["camera_ids"] == ["cam-a"]
    assert bounds_cam_a["oldest"] == "2026-09-01T10:00:00.000Z"
    assert bounds_cam_a["newest"] == "2026-09-30T18:00:00.000Z"

    # Filter by date window
    bounds_filtered = await catalog.photo_date_bounds(
        after="2026-09-10T00:00:00Z", before="2026-09-20T00:00:00Z"
    )
    assert bounds_filtered["count"] == 1
    assert bounds_filtered["oldest"] == "2026-09-15T12:00:00.000Z"
    assert bounds_filtered["camera_ids"] == ["cam-b"]

    # Invalid range
    with pytest.raises(SpypointError, match="after must not be later than before"):
        await catalog.photo_date_bounds(after="2026-10-01T00:00:00Z", before="2026-09-01T00:00:00Z")


async def test_open_meteo_client_large_range_chunking():
    requests_made = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append((request.url.params["start_date"], request.url.params["end_date"]))
        return httpx.Response(200, json={"hourly": {"time": ["2026-01-01T00:00"]}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        om_client = OpenMeteoClient(client=client)
        # 200 days range: should produce 2 chunk requests (180 days and 20 days)
        start = date(2025, 1, 1)
        end = date(2025, 7, 20)
        results = await om_client._query_endpoint(om_client.ARCHIVE_URL, 45.0, -78.0, start, end)
        assert len(requests_made) == 2
        assert requests_made[0][0] == "2025-01-01"
        assert requests_made[0][1] == "2025-06-30"  # 180 days
        assert requests_made[1][0] == "2025-07-01"
        assert requests_made[1][1] == "2025-07-20"
        assert len(results) == 2


async def test_sync_weather_no_matching_photos(api):
    service = PhotoService(api)
    res = await service.sync_weather(
        camera_ids=["nonexistent"],
        latitude=45.0,
        longitude=-78.0,
    )
    assert res["hours_fetched"] == 0
    assert res["observations_stored"] == 0
    assert res["truncated"] is False
    assert "No indexed photos found" in res["note"]
