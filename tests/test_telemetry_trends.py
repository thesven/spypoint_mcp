import pytest
from mcp import Client

from spypoint_mcp.analysis import _extract_battery_level, telemetry_trends
from spypoint_mcp.config import SpypointError
from spypoint_mcp.server import create_server
from spypoint_mcp.service import PhotoService


def test_extract_battery_level_variants_and_threshold_validation():
    assert _extract_battery_level(85) == (85.0, None)
    assert _extract_battery_level([72.5]) == (72.5, None)
    assert _extract_battery_level([{"percentage": 64, "type": "lithium"}]) == (64.0, "lithium")
    assert _extract_battery_level({"level": 50, "type": "alkaline"}) == (50.0, "alkaline")
    assert _extract_battery_level(True) == (None, None)
    assert _extract_battery_level(150) == (None, None)

    with pytest.raises(SpypointError, match="critical_battery_threshold"):
        telemetry_trends({}, critical_battery_threshold=100.0)


async def test_telemetry_trends_discharge_regression_and_replacement_reset(api, fake):
    fake.owned[0]["status"].update(
        lastUpdate="2026-09-25T12:00:00.000Z",
        signal={"processed": {"percentage": 45}},
        memory={"used": 20, "size": 100},
        batteries=[{"percentage": 35, "type": "alkaline"}],
    )
    service = PhotoService(api)
    await service.catalog.record_telemetry(
        "c1", "2026-09-20T12:00:00Z", signal_percentage=90, battery_level=20
    )
    # Battery replacement jump (+75%) resets the window to start at 95%:
    await service.catalog.record_telemetry(
        "c1", "2026-09-21T12:00:00Z", signal_percentage=85, battery_level=95
    )
    await service.catalog.record_telemetry(
        "c1", "2026-09-23T12:00:00Z", signal_percentage=65, battery_level=65
    )

    async with Client(create_server(client=api)) as client:
        res = (
            await client.call_tool(
                "telemetry_trends",
                {"camera_ids": ["c1"], "critical_battery_threshold": 15.0},
            )
        ).structured_content
        cam = res["cameras"][0]
        assert cam["camera_id"] == "c1"
        assert cam["camera_name"] == "Field"
        assert cam["observations"] == 4
        assert cam["latest_battery_level"] == 35.0
        assert cam["battery_drop_per_day"] == 15.0
        assert cam["estimated_days_to_critical"] == 1.3
        assert "rapid_battery_drain" in cam["findings"]
        assert "critical_battery_soon" in cam["findings"]
        assert "unstable_signal" in cam["findings"]

    empty = await service.catalog.telemetry_history(camera_ids=[])
    assert empty["records_by_camera"] == {}
    with pytest.raises(SpypointError, match="limit_per_camera"):
        await service.catalog.telemetry_history(limit_per_camera=0)
