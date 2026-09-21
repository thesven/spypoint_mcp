import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from conftest import PNG, photo
from mcp import Client

from spypoint_mcp.config import Settings, SpypointError
from spypoint_mcp.server import create_server
from spypoint_mcp.service import PhotoService


async def test_mcp_discovery_validation_and_real_image_blocks(api, fake):
    async with Client(create_server(client=api), raise_exceptions=True) as mcp:
        tools = await mcp.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "list_cameras",
            "get_camera",
            "list_photos",
            "latest_photos",
            "view_photo",
            "download_photo",
            "download_video",
            "list_photo_tags",
            "get_photo",
            "search_catalog",
            "catalog_status",
            "start_photo_sync",
            "continue_photo_sync",
            "get_sync_status",
            "list_sync_jobs",
            "camera_health",
            "summarize_activity",
            "create_contact_sheet",
            "export_catalog",
        }
        cameras = await mcp.call_tool("list_cameras", {})
        assert not cameras.is_error
        assert cameras.structured_content["cameras"][0]["name"] == "Field"
        tags = await mcp.call_tool("list_photo_tags", {})
        assert tags.structured_content["tags"] == ["deer", "buck"]
        listing = await mcp.call_tool("list_photos", {"limit": 10})
        assert listing.structured_content["photos"][0]["id"] == "p1"
        viewed = await mcp.call_tool("view_photo", {"photo_id": "p1"})
        images = [x for x in viewed.content if x.type == "image"]
        assert len(images) == 1
        assert base64.b64decode(images[0].data) == PNG
        assert images[0].mime_type == "image/png"
        count = len(fake.calls)
        invalid = await mcp.call_tool("latest_photos", {"camera_limit": 500})
        assert invalid.is_error and len(fake.calls) == count
        missing = await mcp.call_tool("view_photo", {"photo_id": "unknown"})
        assert missing.is_error and "list_photos" in missing.content[0].text


async def test_camera_latest_and_download_through_mcp(api):
    async with Client(create_server(client=api), raise_exceptions=True) as mcp:
        camera = await mcp.call_tool("get_camera", {"camera_id": "c1"})
        assert not camera.is_error and camera.structured_content["model"] == "FLEX"
        missing = await mcp.call_tool("get_camera", {"camera_id": "absent"})
        assert missing.is_error
        latest = await mcp.call_tool("latest_photos", {})
        assert not latest.is_error and latest.structured_content["next_offset"] is None
        assert len([c for c in latest.content if c.type == "image"]) == 1
        saved = await mcp.call_tool("download_photo", {"photo_id": "p1"})
        assert not saved.is_error
        assert Path(saved.structured_content["path"]).read_bytes() == PNG
        duplicate = await mcp.call_tool("download_photo", {"photo_id": "p1"})
        assert duplicate.is_error
        tools = (await mcp.list_tools()).tools
        for tool in tools:
            assert tool.annotations.read_only_hint == (
                tool.name
                not in {
                    "download_photo",
                    "download_video",
                    "start_photo_sync",
                    "continue_photo_sync",
                    "export_catalog",
                }
            )
            assert "ctx" not in tool.input_schema["properties"]


async def test_latest_visits_every_camera_including_empty_and_errors(api, fake):
    fake.owned = [{"id": f"c{i}", "config": {"name": f"Camera {i}"}} for i in range(1, 8)]
    fake.rows = [photo(f"p{i}", f"c{i}") for i in range(1, 7)]
    # A failed first camera must not prevent the others from being visited.

    def photos(request):
        body = json.loads(request.content)
        if body["camera"] == ["c1"]:
            return httpx.Response(403)
        return httpx.Response(
            200, json={"photos": [r for r in fake.rows if r["camera"] in body["camera"]][:1]}
        )

    fake.overrides["/api/v3/photo/all"] = photos
    service = PhotoService(api)
    first, images = await service.latest()
    second, images2 = await service.latest(first["next_offset"])
    assert first["cameras"][0]["status"] == "error"
    assert len(images) == 4 and len(images2) == 1
    assert second["cameras"][-1]["status"] == "no_photos"
    assert second["next_offset"] is None
    assert [r["camera"]["id"] for r in first["cameras"] + second["cameras"]] == [
        f"c{i}" for i in range(1, 8)
    ]
    requests = [
        json.loads(r.content)["camera"] for r in fake.calls if r.url.path.endswith("/photo/all")
    ]
    assert requests == [[f"c{i}"] for i in range(1, 8)]


async def test_download_binary_hash_permissions_and_no_overwrite(api, tmp_path):
    await api.photos(["c1"])
    result = await PhotoService(api).download("p1")
    path = Path(result["path"])
    assert path.parent == tmp_path
    assert path.read_bytes() == PNG
    assert result["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SpypointError, match="no file was overwritten"):
        await PhotoService(api).download("p1")
    assert path.read_bytes() == PNG
    assert not list(tmp_path.glob(".download-*"))


async def test_download_hostile_photo_id_cannot_escape_directory(api, fake, tmp_path):
    fake.rows = [photo("../../escape")]
    with pytest.raises(SpypointError, match="invalid identifier"):
        await api.photos(["c1"])
    assert not list(tmp_path.glob("*.png"))


async def test_missing_credentials_is_actionable_mcp_error():
    async with Client(create_server(Settings()), raise_exceptions=True) as mcp:
        result = await mcp.call_tool("list_cameras", {})
        assert result.is_error
        assert "configure" in result.content[0].text


async def test_stdio_subprocess_tools_list():
    # Exercise the actual wire transport without credentials or any network access.
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "spypoint_mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={k: v for k, v in os.environ.items() if not k.startswith("SPYPOINT_")},
    )
    try:
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "integration-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        for message in messages:
            proc.stdin.write((json.dumps(message) + "\n").encode())
            await proc.stdin.drain()
            if "id" in message:
                response = json.loads(await asyncio.wait_for(proc.stdout.readline(), 10))
                assert response["id"] == message["id"] and "error" not in response
        assert len(response["result"]["tools"]) == 19
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
