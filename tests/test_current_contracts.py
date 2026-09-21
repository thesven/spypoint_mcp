import json
from dataclasses import replace

import httpx
import pytest
from conftest import photo

from spypoint_mcp.client import photo_summary
from spypoint_mcp.config import SpypointError


async def test_owned_and_shared_photo_endpoints_merge_in_capture_order(api, fake):
    fake.shared = [{"sharedCameras": [{"cameraId": "c2"}]}]
    fake.shared_details = {"c2": {"config": {"name": "Shared"}}}
    fake.rows = [
        photo("old", "c1", "2026-09-18T00:00:00.000Z"),
        photo("new", "c2", "2026-09-20T00:00:00.000Z"),
    ]
    result = await api.photos(["c1", "c2"], limit=1)
    assert [p["id"] for p in result["photos"]] == ["new"]
    assert result["next_before"] == "2026-09-20T00:00:00.000Z"
    posts = [r for r in fake.calls if r.method == "POST" and not r.url.path.endswith("login")]
    assert [r.url.path for r in posts] == ["/api/v3/photo/all", "/api/v3/shared-cameras/photos"]
    assert [json.loads(r.content)["camera"] for r in posts] == [["c1"], ["c2"]]


async def test_capture_date_differs_from_upload_date(api, fake):
    fake.rows[0]["date"] = "2026-09-21T00:00:00.000Z"
    fake.rows[0]["originDate"] = "2026-09-19T00:00:00.000Z"
    page = await api.photos(["c1"], before="2026-09-20T00:00:00Z", limit=1)
    assert page["photos"][0]["date"] == "2026-09-19T00:00:00.000Z"
    assert page["photos"][0]["uploaded_at"] == "2026-09-21T00:00:00.000Z"
    assert page["next_before"] == "2026-09-19T00:00:00.000Z"


async def test_owned_photo_detail_refreshes_without_listing(api, fake):
    fake.overrides["/api/v3/photo/c1/p1"] = lambda r: httpx.Response(200, json=photo())
    result = await api.photo("c1", "p1")
    assert result["id"] == "p1"
    assert api.cached_photo("p1")["camera"] == "c1"
    assert not any(r.url.path.endswith("/photo/all") for r in fake.calls)


async def test_detail_identity_mismatch_rejected(api, fake):
    fake.overrides["/api/v3/photo/c1/p1"] = lambda r: httpx.Response(
        200, json=photo(camera="another")
    )
    with pytest.raises(SpypointError, match="did not match"):
        await api.photo("c1", "p1")


def test_favorites_and_available_video_descriptors():
    raw = photo()
    raw["tag"] = ["deer", "fav"]
    raw["sdVideo"] = {"host": "s3.amazonaws.com", "path": "clip.mp4?secret"}
    raw["hd"] = None
    result = photo_summary(raw)
    assert result["favorite"] is True
    assert result["available_video_sizes"] == ["sdVideo"]
    assert "secret" not in json.dumps(result)


async def test_rich_filters_sent_without_reinterpretation(api, fake):
    await api.photos(
        ["c1"],
        species=["buck"],
        time_of_day=["night"],
        media_types=["photo"],
        custom_tags=["Creek"],
    )
    body = json.loads(fake.calls[-1].content)
    assert body["species"] == ["buck"] and body["timeOfDay"] == ["night"]
    assert body["mediaTypes"] == ["photo"] and body["customTags"] == ["Creek"]


def test_naive_camera_time_requires_configuration_and_checks_dst(api):
    with pytest.raises(SpypointError, match="CAMERA_TIMEZONE"):
        api.photo_timestamp("2026-09-20 12:00:00")
    api.settings = replace(api.settings, camera_timezone="America/Toronto")
    assert api.photo_timestamp("2026-09-20 12:00:00") == "2026-09-20T16:00:00.000Z"
    for date in ("2026-11-01 01:30:00", "2026-03-08 02:30:00"):
        with pytest.raises(SpypointError, match="Ambiguous"):
            api.photo_timestamp(date)
