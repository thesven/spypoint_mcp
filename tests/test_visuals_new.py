import io

import httpx
import pytest
from conftest import photo
from mcp import Client
from mcp.types import ImageContent
from PIL import Image

from spypoint_mcp.config import SpypointError
from spypoint_mcp.server import create_server
from spypoint_mcp.visuals import burst_strip, crop


def make_png(width: int = 200, height: int = 160, color: str = "#336699") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def test_crop_validation_and_dimensions():
    raw = make_png(400, 300)
    cropped_bytes, meta = crop(raw, (0.25, 0.2, 0.75, 0.8), max_edge=512)
    assert meta["original_width"] == 400 and meta["original_height"] == 300
    assert meta["width"] == 200 and meta["height"] == 180
    assert meta["pixel_box"] == [100, 60, 300, 240]
    with Image.open(io.BytesIO(cropped_bytes)) as img:
        assert img.size == (200, 180)

    with pytest.raises(SpypointError, match="max_edge"):
        crop(raw, (0.1, 0.1, 0.9, 0.9), max_edge=100)
    with pytest.raises(SpypointError, match="four finite"):
        crop(raw, (True, 0.1, 0.9, 0.9))  # type: ignore[arg-type]
    with pytest.raises(SpypointError, match="0.0 <= x_min"):
        crop(raw, (0.8, 0.1, 0.2, 0.9))
    with pytest.raises(SpypointError, match="32x32"):
        crop(raw, (0.1, 0.1, 0.12, 0.12))


def test_burst_strip_horizontal_and_vertical_and_validation():
    raw = make_png(160, 120)
    tiles = [
        ({"photo_id": "p1", "date": "2026-09-20T12:00:00.000Z"}, raw),
        ({"photo_id": "p2", "date": "2026-09-20T12:00:03.500Z"}, raw),
        ({"photo_id": "p3", "date": "invalid-date"}, b"not-an-image"),
    ]
    h_bytes, h_meta = burst_strip(tiles, direction="horizontal", tile_height=240)
    assert h_meta["direction"] == "horizontal"
    assert h_meta["successful_images"] == 2
    assert h_meta["span_seconds"] == 3.5
    assert h_meta["frames"][1]["delta_seconds"] == 3.5
    assert "error" in h_meta["frames"][2]
    with Image.open(io.BytesIO(h_bytes)) as img:
        assert img.width > img.height

    v_bytes, v_meta = burst_strip(tiles[:2], direction="vertical", tile_height=240)
    assert v_meta["direction"] == "vertical"
    with Image.open(io.BytesIO(v_bytes)) as img:
        assert img.height > img.width

    with pytest.raises(SpypointError, match="2–8 photos"):
        burst_strip([tiles[0]])
    with pytest.raises(SpypointError, match="No requested photos"):
        burst_strip([({"photo_id": "a"}, None), ({"photo_id": "b"}, None)])


async def test_mcp_crop_photo_and_create_burst_strip(api, fake):
    big_png = make_png(320, 240)
    fake.overrides["/bucket/image.png"] = lambda r: httpx.Response(
        200, content=big_png, headers={"content-type": "image/png"}
    )
    fake.rows = [
        photo("p2", "c1", "2026-09-20T12:00:04.000Z"),
        photo("p1", "c1", "2026-09-20T12:00:00.000Z"),
    ]
    async with Client(create_server(client=api)) as client:
        await client.call_tool("list_photos", {})
        crop_res = await client.call_tool(
            "crop_photo",
            {"photo_id": "p1", "x_min": 0.1, "y_min": 0.1, "x_max": 0.8, "y_max": 0.8},
        )
        assert not crop_res.is_error
        assert any(isinstance(c, ImageContent) for c in crop_res.content)
        assert crop_res.structured_content["transformation"]["transformed"] is True

        burst_res = await client.call_tool(
            "create_burst_strip",
            {"photo_ids": ["p2", "p1"], "direction": "horizontal", "tile_height": 240},
        )
        assert not burst_res.is_error
        assert any(isinstance(c, ImageContent) for c in burst_res.content)
        frames = burst_res.structured_content["frames"]
        assert [f["photo_id"] for f in frames] == ["p1", "p2"]
        assert frames[0]["delta_seconds"] == 0.0
        assert frames[1]["delta_seconds"] == 4.0
