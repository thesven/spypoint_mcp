"""Small read-only adapter for the community-documented SPYPOINT v3 API."""

import asyncio
import math
import re
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx

from .config import Settings, SpypointError

API = "https://restapi.spypoint.com/api/v3"
SIZES = ("small", "medium", "large", "hd")
VIDEO_SIZES = ("sdVideo", "hdVideo", "streamVideo")


def timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise SpypointError("Dates must be ISO 8601 timestamps with a timezone.") from None


def object_list(data: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
        raise SpypointError(
            f"Unexpected SPYPOINT {label} response; API compatibility needs checking."
        )
    return data


def identifier(data: dict, key: str = "id") -> str:
    value = data.get(key)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
    ):
        raise SpypointError("SPYPOINT returned a missing or invalid identifier.")
    return value


def camera_summary(raw: dict, shared: bool = False) -> dict:
    config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    # Deliberately select fields rather than forwarding arbitrary account data.
    return {
        "id": identifier(raw),
        "name": config.get("name", raw.get("name", "")),
        "shared": shared,
        "model": status.get("model"),
        "last_update": status.get("lastUpdate"),
        "batteries": status.get("batteries"),
        "signal": status.get("signal"),
        "memory": status.get("memory"),
        "temperature": status.get("temperature"),
        "firmware": status.get("version"),
        "video_capable": raw.get("videoCapability"),
        "configuration": {
            key: config[key]
            for key in (
                "captureMode",
                "delay",
                "multiShot",
                "operationMode",
                "quality",
                "transmitFreq",
                "transmitTime",
                "transmitAuto",
                "triggerSpeed",
                "sensibility",
            )
            if key in config
        },
    }


def photo_summary(raw: dict) -> dict:
    tags = raw.get("tag", [])
    return {
        "id": identifier(raw),
        "camera_id": raw.get("camera"),
        "date": raw.get("originDate") or raw.get("date"),
        "uploaded_at": raw.get("date"),
        "captured_at": raw.get("originDate"),
        "filename": raw.get("originName"),
        "tags": tags,
        "favorite": "fav" in tags or raw.get("favorite") is True,
        "available_video_sizes": [
            size
            for size in ("sdVideo", "hdVideo", "streamVideo")
            if isinstance(raw.get(size), dict) and raw[size].get("host") and raw[size].get("path")
        ],
        "video_duration_seconds": raw.get("streamVideoDuration"),
        "hd_status": "available"
        if isinstance(raw.get("hd"), dict) and raw["hd"].get("host")
        else "pending"
        if raw.get("hdPending")
        else "error"
        if raw.get("hdFailed")
        else "unavailable",
        "available_sizes": [
            size
            for size in SIZES
            if isinstance(raw.get(size), dict) and raw[size].get("host") and raw[size].get("path")
        ],
    }


class SpypointClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._token = settings.token
        self._auth_lock = asyncio.Lock()
        # Separate clients: no account bearer token or cookies reach the image CDN.
        self._api = httpx.AsyncClient(
            timeout=settings.timeout, follow_redirects=False, transport=transport, trust_env=False
        )
        self._images = httpx.AsyncClient(
            timeout=settings.timeout, follow_redirects=False, transport=transport, trust_env=False
        )
        self._photos: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._camera_shared: dict[str, bool] | None = None

    async def close(self) -> None:
        await self._api.aclose()
        await self._images.aclose()
        self._token = ""
        self._photos.clear()
        self._camera_shared = None

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._api.request(method, API + path, **kwargs)
            except httpx.RequestError:
                if attempt == 2:
                    raise SpypointError(
                        "SPYPOINT could not be reached. Check connectivity."
                    ) from None
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                delay = 0.5 * (2**attempt)
                if response.headers.get("retry-after"):
                    try:
                        retry_after = float(response.headers["retry-after"])
                        if not math.isfinite(retry_after) or retry_after < 0:
                            raise ValueError
                        delay = max(delay, retry_after)
                    except ValueError:
                        # HTTP-date Retry-After values are not guessed or retried early.
                        raise SpypointError("SPYPOINT asked us to wait. Try again later.") from None
                if delay > 8 or attempt == 2:
                    raise SpypointError(
                        "SPYPOINT is busy or rate limiting requests. Try again later."
                    )
                await asyncio.sleep(delay)
                continue
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if response.status_code == 401:
            raise SpypointError("SPYPOINT authentication failed. Check local credentials or token.")
        if response.status_code == 403:
            raise SpypointError("SPYPOINT denied access. Check account permissions in its web app.")
        if response.status_code >= 300:
            raise SpypointError(f"SPYPOINT request failed (HTTP {response.status_code}).")
        try:
            return response.json()
        except ValueError:
            raise SpypointError(
                "SPYPOINT returned invalid JSON; API compatibility needs checking."
            ) from None

    async def _authenticate(self, rejected_token: str | None = None) -> None:
        async with self._auth_lock:
            if self._token and self._token != rejected_token:
                return
            self.settings.require_credentials()
            if not (self.settings.username and self.settings.password):
                raise SpypointError(
                    "SPYPOINT token expired. Replace it locally or configure login credentials."
                )
            response = await self._send(
                "POST",
                "/user/login",
                json={
                    "username": self.settings.username,
                    "password": self.settings.password,
                },
            )
            body = self._json(response)
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("token"), str)
                or not body["token"]
            ):
                raise SpypointError(
                    "SPYPOINT login did not return a token; check its web app for required steps."
                )
            self._token = body["token"]

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        await self._authenticate()
        token = self._token
        response = await self._send(
            method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
        if response.status_code == 401:
            await self._authenticate(rejected_token=token)
            response = await self._send(
                method, path, headers={"Authorization": f"Bearer {self._token}"}, **kwargs
            )
        return self._json(response)

    async def cameras(self) -> dict:
        own = object_list(await self._request("GET", "/camera/all"), "cameras")
        cameras = {identifier(c): camera_summary(c) for c in own}
        warnings: list[str] = []
        complete = True
        if self.settings.include_shared:
            try:
                groups = object_list(
                    await self._request("GET", "/shared-cameras/all"), "shared cameras"
                )
                for group in groups:
                    for entry in object_list(group.get("sharedCameras"), "shared cameras"):
                        camera_id = identifier(entry, "cameraId")
                        if camera_id in cameras:
                            continue
                        try:
                            raw = await self._request(
                                "GET", "/shared-cameras/" + quote(camera_id, safe="")
                            )
                            if not isinstance(raw, dict):
                                raise SpypointError("Unexpected shared camera detail response.")
                            cameras[camera_id] = camera_summary({**raw, "id": camera_id}, True)
                        except SpypointError as exc:
                            complete = False
                            warnings.append(f"Shared camera {camera_id}: {exc}")
            except SpypointError as exc:
                complete = False
                warnings.append(f"Shared camera discovery incomplete: {exc}")
        self._camera_shared = {key: value["shared"] for key, value in cameras.items()}
        return {
            "cameras": sorted(cameras.values(), key=lambda c: c["id"]),
            "complete": complete,
            "shared_included": self.settings.include_shared,
            "warnings": warnings,
        }

    async def filters(self, camera_model: str | None = None) -> dict:
        body = await self._request(
            "GET", "/photo/filters", params={"cameraModel": camera_model} if camera_model else {}
        )
        if not isinstance(body, dict):
            raise SpypointError("Unexpected SPYPOINT filter response.")
        species = object_list(body.get("species"), "filters")
        return {
            "tags": [item["nameId"] for item in species if isinstance(item.get("nameId"), str)],
            "media_types": body.get("mediaTypes", []),
            "time_of_day": body.get("timeOfDay", []),
        }

    def photo_timestamp(self, value: str) -> str:
        """Normalize upstream capture times without guessing a naive camera timezone."""
        try:
            if not isinstance(value, str):
                raise TypeError
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if (
                parsed.tzinfo is not None
                and self.settings.camera_time_mode == "wall_clock_z"
                and self.settings.camera_timezone
                and value.strip().endswith(("Z", "z", "+00:00", "-00:00"))
            ):
                parsed = parsed.replace(tzinfo=None)
            if parsed.tzinfo is not None:
                return timestamp(value)
            if not self.settings.camera_timezone:
                raise SpypointError(
                    "SPYPOINT returned a date without a timezone. Set SPYPOINT_CAMERA_TIMEZONE "
                    "to the camera's IANA timezone before filtering or syncing it."
                )
            zone = ZoneInfo(self.settings.camera_timezone)
            first, second = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
            if (
                first.utcoffset() != second.utcoffset()
                or first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != parsed
            ):
                raise SpypointError(
                    "Ambiguous or nonexistent local camera time; cannot safely page it."
                )
            return timestamp(first.isoformat())
        except (ValueError, TypeError):
            raise SpypointError("SPYPOINT returned an invalid photo timestamp.") from None

    def remember(self, row: dict) -> dict:
        photo_id = identifier(row)
        tags = row.get("tag", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise SpypointError("SPYPOINT returned invalid photo tags.")
        self._photos[photo_id] = (time.monotonic(), row)
        self._photos.move_to_end(photo_id)
        while len(self._photos) > 2000:
            self._photos.popitem(last=False)
        return photo_summary(row)

    async def photo(self, camera_id: str, photo_id: str) -> dict:
        identifier({"id": camera_id})
        identifier({"id": photo_id})
        """Owned-photo detail is documented in the current web app; shared detail is not."""
        if self._camera_shared is None:
            await self.cameras()
        if camera_id not in (self._camera_shared or {}):
            raise SpypointError("Camera is not accessible in the current account.")
        if self._camera_shared[camera_id]:
            raw = self.cached_photo(photo_id)
            if raw.get("camera") != camera_id:
                raise SpypointError("Photo does not belong to the selected camera.")
            return photo_summary(raw)
        raw = await self._request(
            "GET", "/photo/" + quote(camera_id, safe="") + "/" + quote(photo_id, safe="")
        )
        if not isinstance(raw, dict) or raw.get("id") != photo_id or raw.get("camera") != camera_id:
            raise SpypointError(
                "SPYPOINT photo detail did not match the requested photo and camera."
            )
        return self.remember(raw)

    async def photos(
        self,
        camera_ids: list[str],
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
        tags: list[str] | None = None,
        favorites_only: bool = False,
        species: list[str] | None = None,
        time_of_day: list[str] | None = None,
        media_types: list[str] | None = None,
        custom_tags: list[str] | None = None,
    ) -> dict:
        if not 1 <= limit <= 100:
            raise SpypointError("Photo limit must be between 1 and 100.")
        before = (
            timestamp(before)
            if before
            else datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        after = timestamp(after) if after else None
        if after and after > before:
            raise SpypointError("after must be earlier than or equal to before.")
        if not camera_ids:
            return {"photos": [], "next_before": None, "may_have_more": False, "fetched": 0}
        if self._camera_shared is None:
            await self.cameras()
        if not set(camera_ids) <= set(self._camera_shared or {}):
            raise SpypointError("Unknown camera id; refresh list_cameras before trying again.")
        rows = []
        upstream_full = False
        for shared in (False, True):
            selected = [cid for cid in camera_ids if self._camera_shared[cid] == shared]
            if not selected:
                continue
            if shared and custom_tags:
                raise SpypointError("Custom tag filters are not supported for shared cameras.")
            payload = {
                "camera": selected,
                "dateEnd": before,
                "favorite": favorites_only,
                "hd": False,
                "limit": limit,
                "tag": tags or [],
            }
            if after:
                payload["dateStart"] = after
            if shared:
                payload["tag"] = list(
                    dict.fromkeys(
                        (tags or []) + (species or []) + (time_of_day or []) + (media_types or [])
                    )
                )
            else:
                for key, value in (
                    ("species", species),
                    ("timeOfDay", time_of_day),
                    ("mediaTypes", media_types),
                    ("customTags", custom_tags),
                ):
                    if value:
                        payload[key] = value
            body = await self._request(
                "POST", "/shared-cameras/photos" if shared else "/photo/all", json=payload
            )
            if not isinstance(body, dict) or "photos" not in body:
                raise SpypointError("Unexpected SPYPOINT photos response; check API compatibility.")
            group = object_list(body["photos"], "photos")
            upstream_full |= len(group) >= limit
            for row in group:
                if row.get("camera") not in selected:
                    raise SpypointError("SPYPOINT returned a photo outside the requested cameras.")
            rows.extend(group)

        def row_date(row: dict) -> str:
            value = row.get("originDate") or row.get("date")
            if not value:
                raise SpypointError("A photo has no capture date; cannot safely page it.")
            return self.photo_timestamp(value)

        rows.sort(key=lambda row: (row_date(row), identifier(row)), reverse=True)
        fetched = len(rows)
        rows = rows[:limit]
        output = []
        dates = []
        seen: set[str] = set()
        for row in rows:
            photo_id = identifier(row)
            camera_id = row.get("camera")
            if camera_id not in camera_ids:
                raise SpypointError("SPYPOINT returned a photo outside the requested cameras.")
            date = row_date(row)
            if date:
                dates.append(date)
            if after and not date:
                raise SpypointError(
                    "A photo is missing its date; cannot apply the requested date filter."
                )
            if after and date < after:
                continue
            if photo_id in seen:
                continue
            seen.add(photo_id)
            if date > before:
                raise SpypointError("SPYPOINT returned photos newer than the requested boundary.")
            summary = self.remember(row)
            summary["date"] = date
            output.append(summary)
        oldest = min(dates) if dates else None
        more = (upstream_full or fetched > limit) and (
            not after or (oldest is not None and oldest >= after)
        )
        return {
            "photos": output,
            "fetched": fetched,
            "may_have_more": more,
            "next_before": oldest if more else None,
            "pagination_note": "before is a date boundary. "
            "Pages may overlap; deduplicate by photo id. "
            "Photos with identical boundary timestamps can prevent complete historical traversal. "
            "This tool does not claim a complete archive.",
        }

    def cached_photo(self, photo_id: str) -> dict:
        item = self._photos.get(photo_id)
        if item is None or time.monotonic() - item[0] > 900:
            self._photos.pop(photo_id, None)
            raise SpypointError(
                "Photo reference missing or expired. Call list_photos or latest_photos first."
            )
        return item[1]

    def _image_url(self, raw: dict, size: str) -> str:
        if size not in SIZES:
            raise SpypointError("Image size must be small, medium, large, or hd.")
        return self._media_url(raw, size)

    def _media_url(self, raw: dict, size: str) -> str:
        if size not in (*SIZES, *VIDEO_SIZES):
            raise SpypointError("Unknown media rendition.")
        descriptor = raw.get(size)
        if (
            not isinstance(descriptor, dict)
            or not descriptor.get("host")
            or not descriptor.get("path")
        ):
            raise SpypointError(
                "Requested image size is unavailable. Choose one of available_sizes."
            )
        if descriptor.get("verb", "GET") != "GET":
            raise SpypointError("SPYPOINT image requires an unsupported download method.")
        host, path = descriptor["host"], descriptor["path"]
        if not isinstance(host, str) or not isinstance(path, str):
            raise SpypointError("Invalid SPYPOINT image address.")
        url = "https://" + host + "/" + path.lstrip("/")
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or "").lower()
            valid_host = (
                hostname == "spypoint.com"
                or hostname.endswith(".spypoint.com")
                or bool(
                    re.fullmatch(
                        r"(?:[a-z0-9-]+\.)*s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", hostname
                    )
                )
                or hostname.endswith(".cloudfront.net")
                or hostname in self.settings.image_hosts
            )
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or not valid_host
                or "/" in host
                or "\\" in url
                or any(ord(c) < 33 for c in url)
            ):
                raise ValueError
        except ValueError:
            raise SpypointError(
                "Unrecognized or unsafe image CDN address. Check SPYPOINT_IMAGE_HOSTS locally."
            ) from None
        return url

    async def image(self, photo_id: str, size: str = "medium") -> tuple[bytes, str]:
        raw = self.cached_photo(photo_id)
        url = self._image_url(raw, size)
        try:
            async with self._images.stream("GET", url) as response:
                if response.status_code in (401, 403):
                    raise SpypointError(
                        "Photo link expired or access was denied. List photos again to refresh it."
                    )
                if response.status_code != 200:
                    raise SpypointError(f"Image download failed (HTTP {response.status_code}).")
                mime = response.headers.get("content-type", "").split(";")[0].lower()
                if mime not in {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                    "image/gif",
                    "application/octet-stream",
                    "binary/octet-stream",
                    "",
                }:
                    raise SpypointError("SPYPOINT returned a non-image response.")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.settings.max_image_bytes:
                        raise SpypointError(
                            "Image exceeds the configured byte limit. Try a smaller size."
                        )
        except httpx.RequestError:
            raise SpypointError(
                "Image download failed. Check connectivity and try listing photos again."
            ) from None
        detected = None
        if data.startswith(b"\xff\xd8\xff"):
            detected = "image/jpeg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            detected = "image/png"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            detected = "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            detected = "image/webp"
        if detected is None or (mime.startswith("image/") and detected != mime):
            raise SpypointError(
                "Downloaded data is not a supported image or its content type is incorrect."
            )
        return bytes(data), detected

    async def video(self, photo_id: str, size: str = "sdVideo") -> tuple[bytes, str]:
        if size not in VIDEO_SIZES:
            raise SpypointError("Video rendition must be sdVideo, hdVideo or streamVideo.")
        url = self._media_url(self.cached_photo(photo_id), size)
        try:
            async with self._images.stream("GET", url) as response:
                if response.status_code != 200:
                    raise SpypointError(
                        f"Video download failed (HTTP {response.status_code}); "
                        "refresh the photo reference."
                    )
                mime = response.headers.get("content-type", "").split(";")[0].lower()
                if mime not in {"video/mp4", "application/octet-stream", "binary/octet-stream", ""}:
                    raise SpypointError(
                        "Only existing MP4 video files are supported; "
                        "HLS streams are not downloaded."
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.settings.max_video_bytes:
                        raise SpypointError(
                            "Video exceeds SPYPOINT_MAX_VIDEO_BYTES; choose a smaller rendition."
                        )
        except httpx.RequestError:
            raise SpypointError(
                "Video download failed; check connectivity and refresh the photo reference."
            ) from None
        if len(data) < 12 or data[4:8] != b"ftyp":
            raise SpypointError("Downloaded video is not an MP4 file with a supported header.")
        return bytes(data), "video/mp4"
