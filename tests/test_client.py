import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest
from conftest import PNG, photo

from spypoint_mcp.client import SpypointClient, timestamp
from spypoint_mcp.config import Settings, SpypointError
from spypoint_mcp.service import PhotoService


async def test_login_reused_and_account_fields_not_exposed(api, fake):
    fake.owned[0]["token"] = "secret-account-field"
    await api.cameras()
    result = await api.cameras()
    assert result["cameras"][0]["name"] == "Field"
    assert "secret" not in json.dumps(result)
    logins = [r for r in fake.calls if r.url.path.endswith("/login")]
    assert len(logins) == 1
    assert json.loads(logins[0].content) == {"username": "user", "password": "private-password"}
    assert "authorization" not in logins[0].headers


async def test_refresh_once_after_401(api, fake):
    attempts = 0

    def cameras(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401) if attempts == 1 else httpx.Response(200, json=fake.owned)

    fake.overrides["/api/v3/camera/all"] = cameras
    assert (await api.cameras())["cameras"][0]["id"] == "c1"
    assert len([r for r in fake.calls if r.url.path.endswith("/login")]) == 2


async def test_concurrent_first_use_single_login(api, fake):
    await asyncio.gather(api.cameras(), api.cameras(), api.filters())
    assert len([r for r in fake.calls if r.url.path.endswith("/login")]) == 1


async def test_token_only_cannot_refresh(fake):
    fake.overrides["/api/v3/camera/all"] = lambda r: httpx.Response(401, text="private-token")
    api = SpypointClient(Settings(token="private-token"), httpx.MockTransport(fake))
    try:
        with pytest.raises(SpypointError, match="token expired"):
            await api.cameras()
        assert not any(r.url.path.endswith("/login") for r in fake.calls)
    finally:
        await api.close()


async def test_all_shared_groups_and_deduplication(api, fake):
    fake.shared = [
        {"sharedCameras": [{"cameraId": "c1"}, {"cameraId": "c2"}]},
        {"sharedCameras": [{"cameraId": "c3"}]},
    ]
    fake.shared_details = {"c2": {"config": {"name": "Woods"}}, "c3": {"config": {"name": "Creek"}}}
    result = await api.cameras()
    assert [c["id"] for c in result["cameras"]] == ["c1", "c2", "c3"]
    assert [c["shared"] for c in result["cameras"]] == [False, True, True]
    assert result["complete"]


async def test_shared_failure_preserves_owned_but_reports_incomplete(api, fake):
    fake.overrides["/api/v3/shared-cameras/all"] = lambda r: httpx.Response(403, text="secret")
    result = await api.cameras()
    assert not result["complete"]
    assert len(result["cameras"]) == 1
    assert "denied" in result["warnings"][0]
    assert "secret" not in json.dumps(result)


async def test_individual_shared_camera_failure_does_not_hide_remaining(api, fake):
    fake.shared = [{"sharedCameras": [{"cameraId": "c2"}, {"cameraId": "c3"}]}]
    fake.shared_details["c3"] = {"config": {"name": "Creek"}}
    fake.overrides["/api/v3/shared-cameras/c2"] = lambda r: httpx.Response(404)
    result = await api.cameras()
    assert [c["id"] for c in result["cameras"]] == ["c1", "c3"]
    assert not result["complete"]


async def test_photo_filters_all_camera_selection_and_no_signed_urls(api, fake):
    fake.owned.append({"id": "c2"})
    fake.rows += [photo("p2", "c2", "2026-09-19T12:00:00.000Z")]
    result = await PhotoService(api).list_photos(
        limit=2,
        before="2026-09-21T00:00:00Z",
        after="2026-09-20T00:00:00Z",
        tags=["deer"],
        favorites_only=True,
    )
    assert [p["id"] for p in result["photos"]] == ["p1"]
    req = next(r for r in fake.calls if r.url.path.endswith("/photo/all"))
    body = json.loads(req.content)
    assert body == {
        "camera": ["c1", "c2"],
        "dateEnd": "2026-09-21T00:00:00.000Z",
        "dateStart": "2026-09-20T00:00:00.000Z",
        "favorite": True,
        "hd": False,
        "limit": 2,
        "tag": ["deer"],
    }
    assert not result["may_have_more"]
    assert "signature" not in json.dumps(result)


async def test_before_pagination_and_empty_camera_selection(api, fake):
    result = await PhotoService(api).list_photos(limit=1)
    assert result["next_before"] == "2026-09-20T12:00:00.000Z"
    assert result["may_have_more"]
    fake.calls.clear()
    assert (await PhotoService(api).list_photos([]))["photos"] == []
    assert not any(r.url.path.endswith("/photo/all") for r in fake.calls)


async def test_unknown_camera_rejected_without_photo_request(api, fake):
    with pytest.raises(SpypointError, match="Unknown camera"):
        await PhotoService(api).list_photos(["not-owned"])
    assert not any(r.url.path.endswith("/photo/all") for r in fake.calls)


async def test_unexpected_photo_camera_rejected(api, fake):
    fake.overrides["/api/v3/photo/all"] = lambda r: httpx.Response(
        200, json={"photos": [photo(camera="other")]}
    )
    with pytest.raises(SpypointError, match="outside"):
        await api.photos(["c1"])


async def test_images_have_no_account_auth_and_valid_binary(api, fake):
    await api.photos(["c1"])
    data, mime = await api.image("p1")
    assert data == PNG and mime == "image/png"
    req = fake.calls[-1]
    assert req.url.host == "s3.amazonaws.com"
    assert "authorization" not in req.headers and "cookie" not in req.headers


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "169.254.169.254",
        "s3.amazonaws.com.evil.test",
        "user:password@s3.amazonaws.com",
        "s3.amazonaws.com:8000",
        "s3.amazonaws.com/evil",
        "s3.amazonaws.com\\@evil.test",
    ],
)
async def test_disallowed_cdn_rejected_before_fetch(api, fake, host):
    fake.rows[0]["medium"]["host"] = host
    await api.photos(["c1"])
    count = len(fake.calls)
    with pytest.raises(SpypointError, match="unsafe"):
        await api.image("p1")
    assert len(fake.calls) == count


@pytest.mark.parametrize(
    "status,content,mime,match",
    [
        (302, b"", "image/png", "HTTP 302"),
        (403, b"", "image/png", "expired"),
        (200, b"<html>secret</html>", "text/html", "non-image"),
        (200, b"garbage", "image/png", "not a supported image"),
        (200, PNG, "image/jpeg", "content type"),
    ],
)
async def test_bad_image_responses_are_actionable(api, fake, status, content, mime, match):
    fake.overrides["/bucket/image.png"] = lambda r: httpx.Response(
        status, content=content, headers={"content-type": mime}
    )
    await api.photos(["c1"])
    with pytest.raises(SpypointError, match=match):
        await api.image("p1")


async def test_image_size_and_cache_expiration(api):
    api.settings = replace(api.settings, max_image_bytes=10)
    await api.photos(["c1"])
    with pytest.raises(SpypointError, match="byte limit"):
        await api.image("p1")
    api._photos["p1"] = (time.monotonic() - 901, photo())
    with pytest.raises(SpypointError, match="expired"):
        await api.image("p1")


async def test_unavailable_hd_never_orders_capture(api, fake):
    await api.photos(["c1"])
    count = len(fake.calls)
    with pytest.raises(SpypointError, match="unavailable"):
        await api.image("p1", "hd")
    assert len(fake.calls) == count


async def test_rate_limit_respects_long_retry_after_without_waiting(api, fake):
    fake.overrides["/api/v3/camera/all"] = lambda r: httpx.Response(
        429, headers={"Retry-After": "60"}
    )
    with pytest.raises(SpypointError, match="rate limiting"):
        await api.cameras()
    assert len([r for r in fake.calls if r.url.path.endswith("/camera/all")]) == 1


async def test_transient_server_failure_retried(api, fake, monkeypatch):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("spypoint_mcp.client.asyncio.sleep", sleep)
    attempts = 0

    def cameras(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503) if attempts < 3 else httpx.Response(200, json=fake.owned)

    fake.overrides["/api/v3/camera/all"] = cameras
    assert (await api.cameras())["complete"]
    assert waits == [0.5, 1.0]


@pytest.mark.parametrize("value", ["2026-09-21", "2026-09-21T10:00:00", "bad"])
def test_dates_require_timezones(value):
    with pytest.raises(SpypointError, match="timezone"):
        timestamp(value)


def test_dates_normalized_to_utc():
    assert timestamp("2026-09-21T10:00:00-04:00") == "2026-09-21T14:00:00.000Z"


@pytest.mark.parametrize(
    "response", [httpx.Response(200, text="bad-json"), httpx.Response(200, json={"unexpected": []})]
)
async def test_api_drift_does_not_look_like_no_cameras(api, fake, response):
    fake.overrides["/api/v3/camera/all"] = lambda r: response
    with pytest.raises(SpypointError):
        await api.cameras()


async def test_include_shared_false_skips_shared_camera_endpoints(api, fake):
    fake.shared = [{"sharedCameras": [{"cameraId": "c2"}]}]
    api.settings = replace(api.settings, include_shared=False)
    result = await api.cameras()
    assert result["shared_included"] is False
    assert [c["id"] for c in result["cameras"]] == ["c1"]
    assert not any("/shared-cameras" in r.url.path for r in fake.calls)


@pytest.mark.parametrize("retry_after", ["nan", "inf", "-1", "Wed, 21 Oct 2026 07:28:00 GMT"])
async def test_invalid_or_http_date_retry_after_rejected_cleanly(api, fake, retry_after):
    fake.overrides["/api/v3/camera/all"] = lambda r: httpx.Response(
        429, headers={"Retry-After": retry_after}
    )
    with pytest.raises(SpypointError, match="wait"):
        await api.cameras()
