"""Operations shared by MCP tools and the local account check."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

from .catalog import Catalog
from .client import VIDEO_SIZES, SpypointClient, photo_summary
from .config import SpypointError


class PhotoService:
    def __init__(self, client: SpypointClient):
        self.client = client
        self._catalog: Catalog | None = None

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = Catalog(self.client.settings)
        return self._catalog

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
            return photo_summary(raw)
        except SpypointError:
            stored = await self.catalog.get(photo_id)
            selected = camera_id or (stored["camera_id"] if stored else None)
            if selected is None:
                raise SpypointError(
                    "Unknown photo reference. Call list_photos or supply camera_id."
                ) from None
            return await self.photo_detail(selected, photo_id)

    async def contact_sheet(
        self, photo_ids: list[str], columns: int = 3, tile_width: int = 480, size: str = "large"
    ) -> tuple[bytes, dict]:
        from .visuals import contact_sheet

        if not 1 <= len(photo_ids) <= 12 or len(set(photo_ids)) != len(photo_ids):
            raise SpypointError("Select 1–12 distinct photo IDs for a contact sheet.")
        tiles = []
        for photo_id in photo_ids:
            metadata = {"photo_id": photo_id}
            try:
                photo = await self.resolve_photo(photo_id)
                metadata.update(camera_id=photo["camera_id"], date=photo["date"])
                data, _ = await self.client.image(photo_id, size)
                tiles.append((metadata, data))
            except SpypointError as exc:
                tiles.append(({**metadata, "error": str(exc)}, None))
        return await asyncio.to_thread(contact_sheet, tiles, columns, tile_width)

    async def list_photos(self, camera_ids: list[str] | None = None, **filters) -> dict:
        inventory = await self.client.cameras()
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
        inventory = await self.client.cameras()
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
            "photo": photo_summary(raw),
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
