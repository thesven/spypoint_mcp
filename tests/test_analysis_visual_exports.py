import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import PNG, photo
from mcp import Client
from PIL import Image

from spypoint_mcp.analysis import fleet_health, summarize
from spypoint_mcp.client import photo_summary, timestamp
from spypoint_mcp.config import SpypointError
from spypoint_mcp.exports import csv_cell, export
from spypoint_mcp.server import create_server
from spypoint_mcp.service import PhotoService
from spypoint_mcp.visuals import contact_sheet, decode, resize


def colored_image(color, size=(320, 240)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def test_activity_dst_repeated_hour_and_photo_not_animal_counts():
    rows = [
        photo_summary(photo("first", date="2026-11-01T05:30:00.000Z")),
        photo_summary(photo("second", date="2026-11-01T06:30:00.000Z")),
    ]
    rows[0]["tags"] = ["deer", "deer", "night"]
    result = summarize(rows, "America/Toronto", False)
    assert result["by_local_hour"]["1"] == 2
    assert result["hourly_timeline"] == {
        "2026-11-01T01:00:00-04:00": 1,
        "2026-11-01T01:00:00-05:00": 1,
    }
    assert result["tag_counts"] == {"deer": 2, "night": 1}
    assert result["by_weekday"]["6"] == 2
    assert result["by_camera"][0]["photos"] == 2
    assert "not unique animals" in result["interpretation"]


def test_activity_unknown_timezone_and_empty_selection():
    with pytest.raises(SpypointError, match="IANA"):
        summarize([], "not/a/timezone", False)
    assert summarize([], "UTC", False)["photo_count"] == 0


def test_fleet_health_does_not_call_missing_telemetry_healthy():
    inventory = {
        "cameras": [{"id": "c1", "name": "Field"}],
        "complete": False,
        "warnings": ["shared discovery failed"],
    }
    result = fleet_health(inventory, timestamp)
    assert result["cameras"][0]["status"] == "unknown"
    assert not result["complete"]
    inventory["cameras"][0].update(
        last_update="2020-01-01T00:00:00Z",
        signal={"processed": {"percentage": 10}},
        memory={"used": 98, "size": 100},
    )
    result = fleet_health(inventory, timestamp)
    assert set(result["cameras"][0]["findings"]) == {
        "stale_status",
        "low_signal",
        "memory_nearly_full",
    }
    inventory["cameras"][0].update(
        last_update=datetime.now(UTC).isoformat(),
        signal={"processed": {"percentage": 90}},
        memory={"used": 1, "size": 100},
    )
    assert fleet_health(inventory, timestamp)["cameras"][0]["status"] == "ok"


def test_contact_sheet_layout_mapping_and_failed_tile():
    data, metadata = contact_sheet(
        [
            ({"photo_id": "p1", "date": "2026-09-20"}, colored_image("red")),
            ({"photo_id": "p2", "error": "permission denied"}, None),
            ({"photo_id": "p3", "date": "2026-09-21"}, colored_image("blue")),
        ],
        columns=3,
        tile_width=320,
    )
    image = Image.open(io.BytesIO(data))
    assert image.size == (1008, 308)
    assert [tile["photo_id"] for tile in metadata["tiles"]] == ["p1", "p2", "p3"]
    assert metadata["successful_images"] == 2
    red, blue = image.getpixel((172, 132)), image.getpixel((836, 132))
    assert red[0] > 240 and red[2] < 10
    assert blue[2] > 240 and blue[0] < 10


def test_visual_decode_limits_and_no_blank_success(monkeypatch):
    with pytest.raises(SpypointError):
        contact_sheet([({"photo_id": "bad"}, b"not an image")])
    monkeypatch.setattr("spypoint_mcp.visuals.MAX_PIXELS", 100)
    with pytest.raises(SpypointError, match="pixel budget"):
        decode(colored_image("red"))


def test_resize_orientation_and_exif_removal():
    original = Image.new("RGB", (800, 400), "green")
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    original.save(buffer, "JPEG", exif=exif)
    data, metadata = resize(buffer.getvalue(), 256)
    image = Image.open(io.BytesIO(data))
    assert image.size == (128, 256)
    assert not image.getexif()
    assert metadata["transformed"] and metadata["exif_removed"]


@pytest.mark.parametrize("text", ["=1+1", "  =HYPERLINK(1)", "\t=1+1", "+cmd", "-cmd", "@SUM(1)"])
def test_csv_formula_escape(text):
    assert csv_cell(text).startswith("'")
    assert csv_cell("ordinary") == "ordinary"


@pytest.mark.parametrize("format", ["csv", "json"])
async def test_export_roundtrip_hash_manifest_private_permissions(api, format):
    service = PhotoService(api)
    raw = photo()
    raw["originName"] = '=HYPERLINK("evil")'
    await service.catalog.remember([photo_summary(raw)])
    window = await service.catalog.window()
    result = export(window, api.settings.download_dir, format)
    path = Path(result["path"])
    assert path.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result["sha256"]
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["rows"] == 1 and not manifest["truncated"]
    if format == "csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows[0]["filename"].startswith("'=")
    else:
        assert json.loads(path.read_text())["photos"][0]["id"] == "p1"


async def test_analysis_window_truncation_and_filters(api):
    catalog = PhotoService(api).catalog
    await catalog.remember([photo_summary(photo(f"p{i}")) for i in range(3)])
    result = await catalog.window(limit=1)
    assert len(result["photos"]) == 1 and result["truncated"]
    assert not (await catalog.window(camera_ids=["none"]))["photos"]


async def test_new_workflows_through_mcp(api):
    async with Client(create_server(client=api), raise_exceptions=True) as mcp:
        await mcp.call_tool("list_photos", {})
        sheet = await mcp.call_tool("create_contact_sheet", {"photo_ids": ["p1", "missing"]})
        assert not sheet.is_error and sheet.structured_content["successful_images"] == 1
        assert sheet.structured_content["tiles"][1]["error"]
        view = await mcp.call_tool("view_photo", {"photo_id": "p1", "max_edge": 256})
        assert not view.is_error and view.structured_content["transformation"]["width"] == 1
        health = await mcp.call_tool("camera_health", {})
        assert health.structured_content["cameras"][0]["status"] == "unknown"
        activity = await mcp.call_tool("summarize_activity", {"timezone_name": "America/Toronto"})
        assert activity.structured_content["photo_count"] == 1
        exported = await mcp.call_tool("export_catalog", {"format": "csv"})
        assert not exported.is_error and Path(exported.structured_content["path"]).exists()


async def test_view_refreshes_owned_reference_after_restart(api, fake):
    import httpx

    from spypoint_mcp.client import SpypointClient

    service = PhotoService(api)
    await service.list_photos()
    fake.overrides["/api/v3/photo/c1/p1"] = lambda r: httpx.Response(200, json=photo())
    other = SpypointClient(api.settings, httpx.MockTransport(fake))
    try:
        assert (await PhotoService(other).resolve_photo("p1"))["id"] == "p1"
        assert (await other.image("p1"))[0] == PNG
    finally:
        await other.close()


def test_nonfinite_memory_is_unknown():
    from spypoint_mcp.analysis import fleet_health
    from spypoint_mcp.client import timestamp

    result = fleet_health(
        {
            "cameras": [
                {"id": "c1", "name": "A", "memory": {"used": float("inf"), "size": float("inf")}}
            ],
            "complete": True,
            "warnings": [],
        },
        timestamp,
    )
    camera = result["cameras"][0]
    assert camera["memory_used_percentage"] is None
    assert "memory_usage" in camera["unknown_measurements"]
