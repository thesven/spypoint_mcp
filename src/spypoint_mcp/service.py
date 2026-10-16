"""Operations shared by MCP tools and the local account check."""

import asyncio
import hashlib
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from .analysis import (
    moon_phase,
    solar_elevation,
    sun_phase,
    telemetry_trends,
    validate_coordinates,
)
from .catalog import Catalog, now
from .client import VIDEO_SIZES, SpypointClient, photo_summary
from .config import SpypointError
from .schemas import CameraInventory
from .weather import (
    OpenMeteoClient,
    build_tracking_summary,
    c_to_f,
    calculate_pressure_trend,
    degrees_to_compass_16,
    hpa_to_inhg,
    kmh_to_mph,
    mm_to_inches,
    normalize_hour_utc,
    round_grid_coordinate,
)


class PhotoService:
    def __init__(self, client: SpypointClient, weather_client: OpenMeteoClient | None = None):
        self.client = client
        self._catalog: Catalog | None = None
        self._weather = weather_client
        self._owns_weather = weather_client is None

    @property
    def weather_client(self) -> OpenMeteoClient:
        if self._weather is None:
            self._weather = OpenMeteoClient(timeout=self.client.settings.timeout)
            self._owns_weather = True
        return self._weather

    async def close(self) -> None:
        if self._owns_weather and self._weather is not None:
            await self._weather.close()

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = Catalog(self.client.settings)
        return self._catalog

    async def cameras(self) -> dict:
        inventory = await self.client.cameras()
        CameraInventory.model_validate(inventory)
        await self.catalog.remember_cameras(inventory["cameras"])
        return {**inventory, "cameras": list(inventory["cameras"])}

    def _normalized_summary(self, raw: dict) -> dict:
        summary = photo_summary(raw)
        if summary.get("date"):
            summary["date"] = self.client.photo_timestamp(summary["date"])
        return summary

    async def photo_detail(self, camera_id: str, photo_id: str) -> dict:
        result = await self.client.photo(camera_id, photo_id)
        result["date"] = self.client.photo_timestamp(result["date"])
        await self.catalog.remember([result])
        return result

    async def resolve_photo(self, photo_id: str, camera_id: str | None = None) -> dict:
        """Resolve a recent reference or refresh an owned photo from local metadata."""
        try:
            raw = self.client.cached_photo(photo_id)
            if camera_id is not None and raw.get("camera") != camera_id:
                raise SpypointError("Photo does not belong to the requested camera.")
            return self._normalized_summary(raw)
        except SpypointError as exc:
            if "does not belong" in str(exc):
                raise
            stored = await self.catalog.get(photo_id)
            selected = camera_id or (stored["camera_id"] if stored else None)
            if selected is None:
                raise SpypointError(
                    "Unknown photo reference. Call list_photos or supply camera_id."
                ) from None
            return await self.photo_detail(selected, photo_id)

    async def crop_photo(
        self,
        photo_id: str,
        box: tuple[float, float, float, float],
        size: str = "large",
        camera_id: str | None = None,
        max_edge: int = 1600,
    ) -> tuple[bytes, dict]:
        from .visuals import crop

        photo = await self.resolve_photo(photo_id, camera_id)
        data, _ = await self.client.image(photo_id, size)
        cropped, transformation = await asyncio.to_thread(crop, data, box, max_edge)
        return cropped, {"photo": photo, "size": size, "transformation": transformation}

    async def burst_strip(
        self,
        photo_ids: list[str],
        direction: Literal["horizontal", "vertical"] = "horizontal",
        tile_height: int = 360,
        size: str = "large",
    ) -> tuple[bytes, dict]:
        from .visuals import burst_strip

        if not 2 <= len(photo_ids) <= 8 or len(set(photo_ids)) != len(photo_ids):
            raise SpypointError("Select 2–8 distinct photo IDs for a burst strip.")
        semaphore = asyncio.Semaphore(4)

        async def fetch_tile(photo_id: str) -> tuple[dict, bytes | None]:
            metadata = {"photo_id": photo_id}
            async with semaphore:
                try:
                    photo = await self.resolve_photo(photo_id)
                    metadata.update(camera_id=photo["camera_id"], date=photo["date"])
                    data, _ = await self.client.image(photo_id, size)
                    return (metadata, data)
                except SpypointError as exc:
                    return ({**metadata, "error": str(exc)}, None)

        tiles = list(await asyncio.gather(*(fetch_tile(pid) for pid in photo_ids)))
        ordered = sorted(
            enumerate(tiles),
            key=lambda item: (str(item[1][0].get("date") or "9999"), item[0]),
        )
        return await asyncio.to_thread(burst_strip, [t for _, t in ordered], direction, tile_height)

    async def annotate_photo(
        self,
        photo_id: str,
        note: str | None = None,
        user_tags: list[str] | None = None,
    ) -> dict:
        if note is None and user_tags is None:
            return await self.catalog.get_annotation(photo_id)
        return await self.catalog.annotate(photo_id, note, user_tags)

    async def telemetry_trends(
        self,
        camera_ids: list[str] | None = None,
        critical_battery_threshold: float = 15.0,
        refresh_live: bool = True,
    ) -> dict:
        if refresh_live:
            await self.cameras()
        history = await self.catalog.telemetry_history(camera_ids)
        return telemetry_trends(
            history["records_by_camera"],
            history["camera_names"],
            critical_battery_threshold,
        )

    async def check_new_photos(
        self,
        camera_ids: list[str] | None = None,
        watermark_key: str = "default",
        limit: int = 50,
        advance_watermark: bool = True,
    ) -> dict:
        wm_record = await self.catalog.get_watermark(watermark_key)
        prev_wm = wm_record["watermark"] if wm_record else None
        page = await self.list_photos(camera_ids, limit=limit, after=prev_wm)
        new_photos = [
            p for p in page["photos"] if prev_wm is None or (p.get("date") and p["date"] > prev_wm)
        ]
        dated = [p["date"] for p in new_photos if p.get("date")]
        new_wm = max(dated) if dated else (prev_wm or now())
        if advance_watermark:
            await self.catalog.set_watermark(watermark_key, new_wm)
        return {
            "watermark_key": watermark_key,
            "previous_watermark": prev_wm,
            "new_watermark": new_wm,
            "new_photos_count": len(new_photos),
            "photos": new_photos,
            "camera_ids": page["camera_ids"],
            "camera_discovery_complete": page["camera_discovery_complete"],
            "warnings": page["warnings"],
            "note": "High-water mark advances to the newest observed photo capture date.",
        }

    def _resolve_coordinates(
        self, latitude: float | None, longitude: float | None
    ) -> tuple[float, float]:
        coords = validate_coordinates(latitude, longitude)
        if coords is not None:
            return coords
        if (
            self.client.settings.default_latitude is not None
            and self.client.settings.default_longitude is not None
        ):
            return (
                self.client.settings.default_latitude,
                self.client.settings.default_longitude,
            )
        raise SpypointError(
            "GPS coordinates required. Provide latitude and longitude or configure "
            "SPYPOINT_DEFAULT_LATITUDE and SPYPOINT_DEFAULT_LONGITUDE."
        )

    async def photo_weather(
        self,
        photo_id: str,
        camera_id: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict:
        photo = await self.catalog.get(photo_id)
        if not photo:
            try:
                photo = self._normalized_summary(self.client.cached_photo(photo_id))
            except SpypointError:
                photo = await self.resolve_photo(photo_id, camera_id)
        raw_date = photo.get("date")
        if not raw_date:
            raise SpypointError("Photo does not contain a capture date.")
        lat, lon = self._resolve_coordinates(latitude, longitude)
        grid_lat = round_grid_coordinate(lat)
        grid_lon = round_grid_coordinate(lon)

        dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        hour_utc = normalize_hour_utc(dt)
        prior_3h_utc = normalize_hour_utc(dt - timedelta(hours=3))

        cached = await self.catalog.get_cached_weather(grid_lat, grid_lon, [hour_utc, prior_3h_utc])
        was_cached = hour_utc in cached

        if not was_cached or prior_3h_utc not in cached:
            start_d = (dt - timedelta(days=1)).date()
            end_d = (dt + timedelta(days=1)).date()
            fetched = await self.weather_client.fetch_hourly_range(
                grid_lat, grid_lon, start_d, end_d
            )
            await self.catalog.store_weather_observations(fetched)
            cached = await self.catalog.get_cached_weather(
                grid_lat, grid_lon, [hour_utc, prior_3h_utc]
            )

        if hour_utc not in cached:
            raise SpypointError(f"No weather observations available for {hour_utc}.")

        obs = cached[hour_utc]
        prior_obs = cached.get(prior_3h_utc)
        press_delta, press_trend = calculate_pressure_trend(
            obs["pressure_hpa"], prior_obs["pressure_hpa"] if prior_obs else None
        )

        lunar = moon_phase(dt)
        elev = solar_elevation(dt, grid_lat, grid_lon)
        s_phase = sun_phase(elev)
        summary = build_tracking_summary(
            temp_c=obs["temperature_c"],
            apparent_temp_c=obs["apparent_temperature_c"],
            wind_speed_kmh=obs["wind_speed_kmh"],
            wind_gusts_kmh=obs["wind_gusts_kmh"],
            wind_dir_deg=obs["wind_direction_deg"],
            pressure_hpa=obs["pressure_hpa"],
            pressure_trend=press_trend,
            pressure_delta=press_delta,
            weather_desc=obs["weather_condition"],
            humidity=obs["relative_humidity"],
            dew_point_c=obs["dew_point_c"],
        )

        return {
            "photo_id": photo["id"],
            "camera_id": photo["camera_id"],
            "captured_at": raw_date,
            "hour_utc": hour_utc,
            "grid_latitude": grid_lat,
            "grid_longitude": grid_lon,
            "temperature_c": obs["temperature_c"],
            "temperature_f": c_to_f(obs["temperature_c"]),
            "apparent_temperature_c": obs["apparent_temperature_c"],
            "apparent_temperature_f": c_to_f(obs["apparent_temperature_c"]),
            "relative_humidity": obs["relative_humidity"],
            "dew_point_c": obs["dew_point_c"],
            "dew_point_f": c_to_f(obs["dew_point_c"]),
            "pressure_hpa": obs["pressure_hpa"],
            "pressure_inhg": hpa_to_inhg(obs["pressure_hpa"]),
            "pressure_trend_3h": press_trend,
            "pressure_delta_3h_hpa": press_delta,
            "wind_speed_kmh": obs["wind_speed_kmh"],
            "wind_speed_mph": kmh_to_mph(obs["wind_speed_kmh"]),
            "wind_direction_deg": obs["wind_direction_deg"],
            "wind_compass": degrees_to_compass_16(obs["wind_direction_deg"]),
            "wind_gusts_kmh": obs["wind_gusts_kmh"],
            "wind_gusts_mph": kmh_to_mph(obs["wind_gusts_kmh"]),
            "precipitation_mm": obs["precipitation_mm"],
            "precipitation_inches": mm_to_inches(obs["precipitation_mm"]),
            "snowfall_cm": obs["snowfall_cm"],
            "cloud_cover_pct": obs["cloud_cover_pct"],
            "weather_code": obs["weather_code"],
            "weather_condition": obs["weather_condition"],
            "weather_category": obs["weather_category"],
            "moon_phase": lunar["phase"],
            "moon_illumination_pct": lunar["illumination_percentage"],
            "sun_phase": s_phase,
            "tracking_summary": summary,
            "cached": was_cached,
            "scope": "local_catalog",
        }

    async def sync_weather(
        self,
        camera_ids: list[str] | None = None,
        after: str | None = None,
        before: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict:
        lat, lon = self._resolve_coordinates(latitude, longitude)
        grid_lat = round_grid_coordinate(lat)
        grid_lon = round_grid_coordinate(lon)

        bounds = await self.catalog.photo_date_bounds(
            camera_ids=camera_ids, after=after, before=before
        )
        if bounds["count"] == 0 or not bounds["oldest"] or not bounds["newest"]:
            return {
                "camera_ids": camera_ids or [],
                "hours_fetched": 0,
                "observations_stored": 0,
                "grid_latitude": grid_lat,
                "grid_longitude": grid_lon,
                "oldest_hour": None,
                "newest_hour": None,
                "scope": "local_catalog",
                "note": "No indexed photos found matching the criteria.",
                "truncated": False,
            }

        min_dt = datetime.fromisoformat(bounds["oldest"].replace("Z", "+00:00"))
        max_dt = datetime.fromisoformat(bounds["newest"].replace("Z", "+00:00"))
        start_d = (min_dt - timedelta(days=1)).date()
        end_d = (max_dt + timedelta(days=1)).date()

        fetched = await self.weather_client.fetch_hourly_range(grid_lat, grid_lon, start_d, end_d)
        stored_count = await self.catalog.store_weather_observations(fetched)
        hours = [obs["hour_utc"] for obs in fetched]

        return {
            "camera_ids": bounds["camera_ids"],
            "hours_fetched": len(fetched),
            "observations_stored": stored_count,
            "grid_latitude": grid_lat,
            "grid_longitude": grid_lon,
            "oldest_hour": min(hours) if hours else None,
            "newest_hour": max(hours) if hours else None,
            "scope": "local_catalog",
            "note": "Weather observations successfully cached in local SQLite catalog.",
            "truncated": False,
        }

    async def contact_sheet(
        self, photo_ids: list[str], columns: int = 3, tile_width: int = 480, size: str = "large"
    ) -> tuple[bytes, dict]:
        from .visuals import contact_sheet

        if not 1 <= len(photo_ids) <= 12 or len(set(photo_ids)) != len(photo_ids):
            raise SpypointError("Select 1–12 distinct photo IDs for a contact sheet.")
        semaphore = asyncio.Semaphore(4)

        async def fetch_tile(photo_id: str) -> tuple[dict, bytes | None]:
            metadata = {"photo_id": photo_id}
            async with semaphore:
                try:
                    photo = await self.resolve_photo(photo_id)
                    metadata.update(camera_id=photo["camera_id"], date=photo["date"])
                    data, _ = await self.client.image(photo_id, size)
                    return (metadata, data)
                except SpypointError as exc:
                    return ({**metadata, "error": str(exc)}, None)

        tiles = list(await asyncio.gather(*(fetch_tile(pid) for pid in photo_ids)))
        return await asyncio.to_thread(contact_sheet, tiles, columns, tile_width)

    async def list_photos(self, camera_ids: list[str] | None = None, **filters) -> dict:
        inventory = await self.cameras()
        known = {c["id"] for c in inventory["cameras"]}
        selected = sorted(known) if camera_ids is None else list(dict.fromkeys(camera_ids))
        if not set(selected) <= known:
            raise SpypointError(
                "Unknown camera id. Call list_cameras and use an accessible camera id."
            )
        result = await self.client.photos(selected, **filters)
        await self.catalog.remember(result["photos"])
        return {
            **result,
            "camera_ids": selected,
            "camera_discovery_complete": inventory["complete"],
            "warnings": inventory["warnings"],
        }

    async def latest(
        self,
        offset: int = 0,
        camera_limit: int = 5,
        include_images: bool = True,
        size: str = "medium",
    ) -> tuple[dict, list[tuple[dict, bytes, str]]]:
        if offset < 0 or not 1 <= camera_limit <= 5:
            raise SpypointError("offset must be nonnegative and camera_limit between 1 and 5.")
        inventory = await self.cameras()
        cameras = inventory["cameras"]
        selected = cameras[offset : offset + camera_limit]
        entries = []
        images = []
        # Sequential requests avoid an account-wide burst and preserve result ordering.
        for camera in selected:
            entry = {"camera": camera}
            try:
                page = await self.client.photos([camera["id"]], limit=1)
                photo = next(iter(page["photos"]), None)
                await self.catalog.remember(page["photos"])
                entry["photo"] = photo
                entry["status"] = "ok" if photo else "no_photos"
                if photo and include_images:
                    data, mime = await self.client.image(photo["id"], size)
                    images.append(
                        (
                            {
                                "camera_id": camera["id"],
                                "camera_name": camera["name"],
                                "photo": photo,
                                "size": size,
                            },
                            data,
                            mime,
                        )
                    )
            except SpypointError as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)
            entries.append(entry)
        next_offset = offset + len(selected)
        result = {
            "cameras": entries,
            "total_cameras": len(cameras),
            "next_offset": next_offset if next_offset < len(cameras) else None,
            "camera_discovery_complete": inventory["complete"],
            "warnings": inventory["warnings"],
            "note": "Continue with next_offset to visit every camera. Camera order is by id; "
            "changes to account membership during paging can shift offsets.",
        }
        return result, images

    async def download(
        self, photo_id: str, size: str = "large", reuse_existing: bool = False
    ) -> dict:
        if reuse_existing:
            record = await self.catalog.download_record(photo_id, size)
            if record and await asyncio.to_thread(self._verify_download, record):
                return {**record, "reused": True}
        raw = self.client.cached_photo(photo_id)
        data, mime = (
            await self.client.video(photo_id, size)
            if size in VIDEO_SIZES
            else await self.client.image(photo_id, size)
        )
        # Photo ids and upstream filenames are never used as filesystem paths.
        key = hashlib.sha256((photo_id + ":" + size).encode()).hexdigest()
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "video/mp4": ".mp4",
        }[mime]
        root = self.client.settings.download_dir
        target = root / (key + suffix)
        try:
            await asyncio.to_thread(self._save, root, target, data)
        except SpypointError:
            # Recover if a previous process saved the file but died before recording it.
            expected = {
                "path": str(target),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            if not reuse_existing or not await asyncio.to_thread(self._verify_download, expected):
                raise
        result = {
            "path": str(target),
            "bytes": len(data),
            "mime_type": mime,
            "sha256": hashlib.sha256(data).hexdigest(),
            "photo": self._normalized_summary(raw),
            "size": size,
        }
        await self.catalog.record_download(result)
        return {**result, "reused": False}

    def _verify_download(self, record: dict) -> bool:
        path = Path(record["path"])
        root = self.client.settings.download_dir.resolve()
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise SpypointError("Stored download path is unsafe; inspect the local catalog.")
        if not path.exists():
            return False
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise SpypointError(
                "Existing download failed integrity verification; move it aside before retrying."
            )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        if digest.hexdigest() != record["sha256"]:
            raise SpypointError(
                "Existing download failed integrity verification; move it aside before retrying."
            )
        return True

    @staticmethod
    def _save(root: Path, target: Path, data: bytes) -> None:
        temp_name = None
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Link a completed temporary file into place without replacing an existing file.
            with tempfile.NamedTemporaryFile(dir=root, prefix=".download-", delete=False) as stream:
                temp_name = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp_name, target)
        except FileExistsError:
            raise SpypointError(
                "This photo and size already exist in the download directory; "
                "no file was overwritten."
            ) from None
        except OSError:
            raise SpypointError(
                "Cannot save photo. Check the configured download directory and available space."
            ) from None
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
