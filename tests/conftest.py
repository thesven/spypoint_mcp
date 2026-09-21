import base64
import json

import httpx
import pytest

from spypoint_mcp.client import SpypointClient
from spypoint_mcp.config import Settings

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def photo(photo_id="p1", camera="c1", date="2026-09-20T12:00:00.000Z"):
    return {
        "id": photo_id,
        "camera": camera,
        "date": date,
        "originDate": date,
        "originName": "PICT001.PNG",
        "tag": ["deer"],
        **{
            size: {
                "verb": "GET",
                "host": "s3.amazonaws.com",
                "path": "bucket/image.png?signature=secret",
            }
            for size in ("small", "medium", "large")
        },
    }


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.owned = [{"id": "c1", "config": {"name": "Field"}, "status": {"model": "FLEX"}}]
        self.shared = []
        self.shared_details = {}
        self.rows = [photo()]
        self.overrides = {}

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path](request)
        if request.url.host == "s3.amazonaws.com":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if path.endswith("/user/login"):
            return httpx.Response(200, json={"uuid": "account", "token": "private-token"})
        assert request.headers["authorization"].startswith("Bearer ")
        if path.endswith("/camera/all"):
            return httpx.Response(200, json=self.owned)
        if path.endswith("/shared-cameras/all"):
            return httpx.Response(200, json=self.shared)
        if "/shared-cameras/" in path and not path.endswith("/photos"):
            return httpx.Response(200, json=self.shared_details[path.rsplit("/", 1)[1]])
        if path.endswith("/photo/filters"):
            return httpx.Response(200, json={"species": [{"nameId": "deer"}, {"nameId": "buck"}]})
        if path.endswith(("/photo/all", "/shared-cameras/photos")):
            body = json.loads(request.content)
            rows = [
                x
                for x in self.rows
                if x["camera"] in body["camera"] and x["originDate"] <= body["dateEnd"]
            ]
            return httpx.Response(
                200,
                json={
                    "photos": sorted(rows, key=lambda p: p["originDate"], reverse=True)[
                        : body["limit"]
                    ]
                },
            )
        raise AssertionError(f"Unexpected endpoint {path}")


@pytest.fixture
def fake():
    return FakeAPI()


@pytest.fixture
async def api(fake, tmp_path):
    client = SpypointClient(
        Settings(username="user", password="private-password", download_dir=tmp_path),
        httpx.MockTransport(fake),
    )
    yield client
    await client.close()
