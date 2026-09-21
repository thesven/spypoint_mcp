from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from mcp import Client

from spypoint_mcp.config import SpypointError
from spypoint_mcp.server import create_server

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"fixture" * 8


async def test_video_download_and_schema_discovery(api, fake):
    fake.rows[0]["sdVideo"] = {"verb": "GET", "host": "s3.amazonaws.com", "path": "clip.mp4"}
    fake.overrides["/clip.mp4"] = lambda r: httpx.Response(
        200, content=MP4, headers={"content-type": "video/mp4"}
    )
    async with Client(create_server(client=api), raise_exceptions=True) as mcp:
        tools = {t.name: t for t in (await mcp.list_tools()).tools}
        assert "photos" in tools["list_photos"].output_schema["properties"]
        assert "sha256" in tools["download_video"].output_schema["properties"]
        await mcp.call_tool("list_photos", {})
        result = await mcp.call_tool("download_video", {"photo_id": "p1"})
        assert not result.is_error
        path = Path(result.structured_content["path"])
        assert path.suffix == ".mp4" and path.read_bytes() == MP4
        duplicate = await mcp.call_tool("download_video", {"photo_id": "p1"})
        assert duplicate.is_error
    media = [r for r in fake.calls if r.url.host == "s3.amazonaws.com"]
    assert all("authorization" not in r.headers and "cookie" not in r.headers for r in media)
    assert not any("order" in r.url.path.lower() for r in fake.calls)


@pytest.mark.parametrize(
    "status,mime,body,error",
    [
        (403, "video/mp4", MP4, "refresh"),
        (302, "video/mp4", MP4, "HTTP 302"),
        (200, "application/vnd.apple.mpegurl", b"#EXTM3U", "HLS"),
        (200, "video/mp4", b"not a video", "MP4"),
        (200, "video/mp4", MP4 * 100, "MAX_VIDEO_BYTES"),
    ],
)
async def test_video_rejects_unavailable_unsafe_and_oversized(api, fake, status, mime, body, error):
    api.settings = replace(api.settings, max_video_bytes=1024)
    fake.rows[0]["sdVideo"] = {"verb": "GET", "host": "s3.amazonaws.com", "path": "clip.mp4"}
    fake.overrides["/clip.mp4"] = lambda r: httpx.Response(
        status, content=body, headers={"content-type": mime}
    )
    await api.photos(["c1"])
    with pytest.raises(SpypointError, match=error):
        await api.video("p1")


@pytest.mark.parametrize("identifier", ["..", ".", "a/b", "a%2fb", "a?b", "a\\b"])
async def test_detail_identifiers_cannot_change_endpoint(api, fake, identifier):
    with pytest.raises(SpypointError, match="invalid identifier"):
        await api.photo("c1", identifier)
    assert not fake.calls


async def test_schema_failure_redacts_vendor_values(api, fake):
    fake.owned[0]["status"]["model"] = {"secret": "private-value"}
    async with Client(create_server(client=api), raise_exceptions=True) as mcp:
        result = await mcp.call_tool("list_cameras", {})
        assert result.is_error
        assert "compatibility" in result.content[0].text
        assert "private-value" not in str(result)


@pytest.mark.parametrize("mime", ["application/octet-stream", "binary/octet-stream"])
async def test_generic_binary_media_still_validates_signature(api, fake, mime):
    from conftest import PNG

    fake.overrides["/bucket/image.png"] = lambda r: httpx.Response(
        200, content=PNG, headers={"content-type": mime}
    )
    await api.photos(["c1"])
    data, detected = await api.image("p1", "small")
    assert data == PNG and detected == "image/png"
    fake.overrides["/bucket/image.png"] = lambda r: httpx.Response(
        200, content=b"<html>not an image</html>", headers={"content-type": mime}
    )
    with pytest.raises(SpypointError, match="not a supported image"):
        await api.image("p1", "small")


async def test_contact_sheet_respects_requested_rendition(api, fake):
    from conftest import PNG

    from spypoint_mcp.service import PhotoService

    fake.rows[0]["large"]["path"] = "large-image.png"
    fake.overrides["/large-image.png"] = lambda r: httpx.Response(
        200, content=PNG, headers={"content-type": "image/png"}
    )
    await api.photos(["c1"])
    _, metadata = await PhotoService(api).contact_sheet(["p1"])
    assert metadata["successful_images"] == 1
    assert fake.calls[-1].url.path == "/large-image.png"
    await PhotoService(api).contact_sheet(["p1"], size="medium")
    assert fake.calls[-1].url.path == "/bucket/image.png"
