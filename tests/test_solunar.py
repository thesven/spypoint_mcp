from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import photo
from mcp import Client

from spypoint_mcp.analysis import (
    moon_phase,
    solar_elevation,
    solunar_summary,
    sun_phase,
    validate_coordinates,
)
from spypoint_mcp.config import Settings, SpypointError
from spypoint_mcp.server import create_server


def test_moon_phase_and_solar_elevation_calculations():
    new_moon = moon_phase(datetime(2000, 1, 6, 18, 14, tzinfo=UTC))
    assert new_moon["phase"] == "new"
    assert new_moon["illumination_percentage"] == 0.0

    full_moon = moon_phase(datetime(2000, 1, 21, 12, 0, tzinfo=UTC))
    assert full_moon["phase"] == "full"
    assert full_moon["illumination_percentage"] > 95.0

    noon_elev = solar_elevation(datetime(2026, 6, 21, 17, 0, tzinfo=UTC), 45.42, -75.69)
    assert noon_elev > 45.0 and sun_phase(noon_elev) == "day"

    midnight_elev = solar_elevation(datetime(2026, 12, 21, 5, 0, tzinfo=UTC), 45.42, -75.69)
    assert midnight_elev < -18.0 and sun_phase(midnight_elev) == "night"

    assert sun_phase(-3.0) == "civil_twilight"
    assert sun_phase(-9.0) == "nautical_twilight"
    assert sun_phase(-15.0) == "astronomical_twilight"


def test_coordinate_validation_and_settings_loading(tmp_path: Path, monkeypatch):
    assert validate_coordinates(None, None) is None
    assert validate_coordinates(45.0, -75.0) == (45.0, -75.0)
    with pytest.raises(SpypointError, match="both latitude and longitude"):
        validate_coordinates(45.0, None)
    with pytest.raises(SpypointError, match="Latitude must be"):
        validate_coordinates(95.0, 0.0)
    with pytest.raises(SpypointError, match="Latitude must be"):
        validate_coordinates(True, 0.0)  # type: ignore[arg-type]

    env_file = tmp_path / ".env"
    env_file.write_text("SPYPOINT_DEFAULT_LATITUDE=45.5\n")
    with pytest.raises(SpypointError, match="Provide both"):
        Settings.load(env_file)

    env_file.write_text("SPYPOINT_DEFAULT_LATITUDE=99\nSPYPOINT_DEFAULT_LONGITUDE=0\n")
    with pytest.raises(SpypointError, match="Default latitude"):
        Settings.load(env_file)

    env_file.write_text("SPYPOINT_DEFAULT_LATITUDE=45.5\nSPYPOINT_DEFAULT_LONGITUDE=-75.5\n")
    loaded = Settings.load(env_file)
    assert loaded.default_latitude == 45.5 and loaded.default_longitude == -75.5


async def test_solunar_summary_and_summarize_activity_with_coordinates(api, fake):
    fake.rows = [
        photo("p_day", "c1", "2026-06-21T16:30:00.000Z"),
        photo("p_night", "c1", "2026-06-21T05:30:00.000Z"),
    ]
    async with Client(create_server(client=api)) as client:
        await client.call_tool("list_photos", {})
        sol = (
            await client.call_tool(
                "solunar_summary",
                {
                    "timezone_name": "America/Toronto",
                    "latitude": 45.42,
                    "longitude": -75.69,
                },
            )
        ).structured_content
        assert sol["photo_count"] == 2
        assert sol["day_photos"] == 1
        assert sol["night_photos"] == 1
        assert sol["coordinates_used"] == {"latitude": 45.42, "longitude": -75.69}
        assert sum(sol["by_moon_phase"].values()) == 2

        act = (
            await client.call_tool(
                "summarize_activity",
                {
                    "timezone_name": "America/Toronto",
                    "latitude": 45.42,
                    "longitude": -75.69,
                },
            )
        ).structured_content
        assert act["by_sun_phase"]["day"] == 1
        assert sum(act["by_moon_phase"].values()) == 2

    no_coords = solunar_summary([], "UTC", False)
    assert no_coords["coordinates_used"] is None
    assert no_coords["day_photos"] is None
