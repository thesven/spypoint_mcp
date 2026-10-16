"""Private account-scoped SQLite catalog. Only sanitized metadata is persisted."""

import asyncio
import base64
import hashlib
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .analysis import _extract_battery_level
from .client import identifier, timestamp
from .config import Settings, SpypointError


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


PHOTO_FIELDS = {
    "id",
    "camera_id",
    "date",
    "captured_at",
    "uploaded_at",
    "filename",
    "tags",
    "favorite",
    "available_sizes",
    "available_video_sizes",
    "video_duration_seconds",
    "hd_status",
}


def selection(
    camera_ids,
    after,
    before,
    tags,
    favorites_only,
    has_video: bool | None = None,
    user_tags: list[str] | None = None,
):
    clauses, params = [], []
    if camera_ids is not None:
        if not camera_ids:
            clauses.append("0")
        else:
            if len(camera_ids) > 1000:
                raise SpypointError("Select at most 1000 cameras per catalog query.")
            clauses.append("camera_id IN (" + ",".join("?" for _ in camera_ids) + ")")
            params.extend(camera_ids)
    for value, op in ((after, ">="), (before, "<=")):
        if value:
            clauses.append("date " + op + " ?")
            params.append(value)
    if favorites_only:
        clauses.append("json_extract(metadata, '$.favorite') = 1")
    if has_video is True:
        clauses.append("json_array_length(photos.metadata, '$.available_video_sizes') > 0")
    elif has_video is False:
        clauses.append(
            "COALESCE(json_array_length(photos.metadata, '$.available_video_sizes'), 0) = 0"
        )
    for tag in set(tags or []):
        clauses.append("EXISTS (SELECT 1 FROM json_each(photos.metadata, '$.tags') WHERE value=?)")
        params.append(tag)
    for utag in set(user_tags or []):
        clauses.append(
            "EXISTS (SELECT 1 FROM photo_annotations pa, json_each(pa.user_tags) "
            "WHERE pa.photo_id = photos.id AND value = ?)"
        )
        params.append(utag)
    return clauses, params


def _attach_annotations(db, photos: list[dict]) -> list[dict]:
    if not photos:
        return photos
    ids = [p["id"] for p in photos]
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        "SELECT photo_id, note, user_tags FROM photo_annotations "
        f"WHERE photo_id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row["photo_id"]: (row["note"], json.loads(row["user_tags"])) for row in rows}
    for photo in photos:
        if photo["id"] in by_id:
            note, utags = by_id[photo["id"]]
            if note:
                photo["user_note"] = note
            if utags:
                photo["user_tags"] = utags
    return photos


class Catalog:
    def __init__(self, settings: Settings):
        settings.require_credentials()
        # Token mode is intentionally isolated from password mode and from other tokens.
        # This avoids trusting unverified JWT claims or a username paired with the wrong token.
        identity = (
            "token:" + settings.token
            if settings.token
            else "username:" + settings.username.strip().casefold()
        )
        namespace = hashlib.sha256(identity.encode()).hexdigest()
        self.root = (settings.state_dir or settings.download_dir / ".state") / namespace
        self.path = self.root / "catalog.sqlite3"

    @contextmanager
    def _connection(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise SpypointError("Catalog must not be a symbolic link.")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise SpypointError(
                    "Catalog was created by a newer server; upgrade before opening it."
                )
            if version < 4:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS photos (
                        id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, date TEXT NOT NULL,
                        metadata TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS photos_camera_date ON photos(camera_id, date, id);
                    CREATE INDEX IF NOT EXISTS photos_date ON photos(date, id);
                    CREATE TABLE IF NOT EXISTS downloads (
                        photo_id TEXT NOT NULL, size TEXT NOT NULL, record TEXT NOT NULL,
                        PRIMARY KEY(photo_id, size)
                    );
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY, state TEXT NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS job_photos (
                        job_id TEXT NOT NULL, photo_id TEXT NOT NULL,
                        PRIMARY KEY(job_id, photo_id)
                    );
                    CREATE TABLE IF NOT EXISTS cameras (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, shared INTEGER NOT NULL,
                        model TEXT, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS camera_telemetry (
                        camera_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                        signal_percentage REAL, memory_used_percentage REAL,
                        battery_level REAL, battery_type TEXT, raw TEXT NOT NULL,
                        PRIMARY KEY(camera_id, observed_at)
                    );
                    CREATE INDEX IF NOT EXISTS telemetry_camera_time
                        ON camera_telemetry(camera_id, observed_at);
                    CREATE TABLE IF NOT EXISTS photo_annotations (
                        photo_id TEXT NOT NULL PRIMARY KEY,
                        note TEXT NOT NULL,
                        user_tags TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sync_watermarks (
                        key TEXT PRIMARY KEY,
                        watermark TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS weather_observations (
                        grid_lat REAL NOT NULL,
                        grid_lon REAL NOT NULL,
                        hour_utc TEXT NOT NULL,
                        temperature_c REAL NOT NULL,
                        apparent_temperature_c REAL NOT NULL,
                        relative_humidity REAL NOT NULL,
                        dew_point_c REAL NOT NULL,
                        pressure_hpa REAL NOT NULL,
                        wind_speed_kmh REAL NOT NULL,
                        wind_direction_deg REAL NOT NULL,
                        wind_gusts_kmh REAL NOT NULL,
                        precipitation_mm REAL NOT NULL,
                        snowfall_cm REAL NOT NULL,
                        cloud_cover_pct REAL NOT NULL,
                        weather_code INTEGER NOT NULL,
                        weather_condition TEXT NOT NULL,
                        weather_category TEXT NOT NULL,
                        fetched_at TEXT NOT NULL,
                        PRIMARY KEY(grid_lat, grid_lon, hour_utc)
                    );
                    CREATE INDEX IF NOT EXISTS weather_grid_time
                        ON weather_observations(grid_lat, grid_lon, hour_utc);
                    PRAGMA user_version=4;
                """)
            with db:
                yield db
        finally:
            db.close()

    async def _run(self, operation, *args):
        def run():
            try:
                with self._connection() as db:
                    return operation(db, *args)
            except (sqlite3.Error, OSError):
                raise SpypointError(
                    "Local catalog operation failed; check disk space and permissions."
                ) from None

        return await asyncio.to_thread(run)

    @staticmethod
    def _upsert(db, photos: list[dict]) -> None:
        observed = now()
        for photo in photos:
            metadata = {k: v for k, v in photo.items() if k in PHOTO_FIELDS}
            # Explicitly forbid persistence of URL/token fields by using a fixed metadata contract.
            date = timestamp(metadata["date"])
            metadata["date"] = date
            db.execute(
                """
                INSERT INTO photos VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET camera_id=excluded.camera_id, date=excluded.date,
                    metadata=excluded.metadata, last_seen=excluded.last_seen
            """,
                (
                    metadata["id"],
                    metadata["camera_id"],
                    date,
                    json.dumps(metadata),
                    observed,
                    observed,
                ),
            )

    @staticmethod
    def _upsert_cameras(db, cameras: list[dict]) -> None:
        observed = now()
        for camera in cameras:
            db.execute(
                """
                INSERT INTO cameras VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, shared=excluded.shared,
                    model=excluded.model, updated_at=excluded.updated_at
            """,
                (
                    camera["id"],
                    str(camera.get("name") or ""),
                    1 if camera.get("shared") else 0,
                    camera.get("model"),
                    observed,
                ),
            )
            last_upd = camera.get("last_update")
            obs_time = observed
            if isinstance(last_upd, str) and last_upd:
                try:
                    obs_time = timestamp(last_upd)
                except SpypointError:
                    obs_time = observed
            sig = camera.get("signal")
            sig = sig.get("processed", {}) if isinstance(sig, dict) else {}
            sig_pct = sig.get("percentage") if isinstance(sig, dict) else None
            if not (
                isinstance(sig_pct, (int, float))
                and not isinstance(sig_pct, bool)
                and math.isfinite(sig_pct)
                and 0 <= sig_pct <= 100
            ):
                sig_pct = None
            mem = camera.get("memory")
            mem_pct = None
            if isinstance(mem, dict):
                used, size = mem.get("used"), mem.get("size")
                if (
                    isinstance(used, (int, float))
                    and isinstance(size, (int, float))
                    and not isinstance(used, bool)
                    and not isinstance(size, bool)
                    and math.isfinite(used)
                    and math.isfinite(size)
                    and size > 0
                    and 0 <= used <= size
                ):
                    mem_pct = round(100.0 * used / size, 2)
            bat_level, bat_type = _extract_battery_level(camera.get("batteries"))
            raw_telemetry = json.dumps(
                {
                    "signal_percentage": sig_pct,
                    "memory_used_percentage": mem_pct,
                    "battery_level": bat_level,
                    "battery_type": bat_type,
                }
            )
            db.execute(
                """
                INSERT INTO camera_telemetry VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id, observed_at) DO UPDATE SET
                    signal_percentage=excluded.signal_percentage,
                    memory_used_percentage=excluded.memory_used_percentage,
                    battery_level=excluded.battery_level,
                    battery_type=excluded.battery_type,
                    raw=excluded.raw
            """,
                (
                    camera["id"],
                    obs_time,
                    sig_pct,
                    mem_pct,
                    bat_level,
                    bat_type,
                    raw_telemetry,
                ),
            )

    async def remember(self, photos: list[dict]) -> None:
        await self._run(self._upsert, photos)

    async def remember_cameras(self, cameras: list[dict]) -> None:
        await self._run(self._upsert_cameras, cameras)

    async def record_telemetry(
        self,
        camera_id: str,
        observed_at: str,
        signal_percentage: float | None = None,
        memory_used_percentage: float | None = None,
        battery_level: float | None = None,
        battery_type: str | None = None,
    ) -> None:
        identifier({"id": camera_id})
        obs = timestamp(observed_at)

        def save(db):
            raw = json.dumps(
                {
                    "signal_percentage": signal_percentage,
                    "memory_used_percentage": memory_used_percentage,
                    "battery_level": battery_level,
                    "battery_type": battery_type,
                }
            )
            db.execute(
                """
                INSERT INTO camera_telemetry VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id, observed_at) DO UPDATE SET
                    signal_percentage=excluded.signal_percentage,
                    memory_used_percentage=excluded.memory_used_percentage,
                    battery_level=excluded.battery_level,
                    battery_type=excluded.battery_type,
                    raw=excluded.raw
            """,
                (
                    camera_id,
                    obs,
                    signal_percentage,
                    memory_used_percentage,
                    battery_level,
                    battery_type,
                    raw,
                ),
            )

        await self._run(save)

    async def telemetry_history(
        self,
        camera_ids: list[str] | None = None,
        limit_per_camera: int = 100,
    ) -> dict:
        if not 1 <= limit_per_camera <= 1000:
            raise SpypointError("limit_per_camera must be between 1 and 1000.")

        def read(db):
            camera_names = {
                row["id"]: row["name"]
                for row in db.execute("SELECT id, name FROM cameras").fetchall()
            }
            if camera_ids is not None:
                if not camera_ids:
                    return {"records_by_camera": {}, "camera_names": camera_names}
                if len(camera_ids) > 1000:
                    raise SpypointError("Select at most 1000 cameras per telemetry query.")
                cids = sorted(set(camera_ids))
            else:
                rows = db.execute(
                    "SELECT DISTINCT camera_id FROM camera_telemetry ORDER BY camera_id"
                ).fetchall()
                cids = [r[0] for r in rows]
            records_by_camera: dict[str, list[dict]] = {}
            for cid in cids:
                rows = db.execute(
                    "SELECT camera_id, observed_at, signal_percentage, memory_used_percentage, "
                    "battery_level, battery_type FROM camera_telemetry "
                    "WHERE camera_id=? ORDER BY observed_at DESC LIMIT ?",
                    (cid, limit_per_camera),
                ).fetchall()
                records_by_camera[cid] = [dict(r) for r in reversed(rows)]
            return {"records_by_camera": records_by_camera, "camera_names": camera_names}

        return await self._run(read)

    async def annotate(
        self,
        photo_id: str,
        note: str | None = None,
        user_tags: list[str] | None = None,
    ) -> dict:
        identifier({"id": photo_id})
        if note is not None and len(note) > 4000:
            raise SpypointError("Annotation note must be at most 4000 characters.")
        normalized_tags: list[str] | None = None
        if user_tags is not None:
            if len(user_tags) > 50:
                raise SpypointError("Provide at most 50 user_tags per photo.")
            cleaned = []
            for tag in user_tags:
                if not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 64:
                    raise SpypointError("Each user tag must be a nonempty string up to 64 chars.")
                cleaned.append(tag.strip())
            normalized_tags = sorted(set(cleaned))

        def save(db):
            existing = db.execute(
                "SELECT note, user_tags FROM photo_annotations WHERE photo_id=?", (photo_id,)
            ).fetchone()
            final_note = (
                note.strip()
                if note is not None
                else (existing["note"] if existing is not None else "")
            )
            final_tags = (
                normalized_tags
                if normalized_tags is not None
                else (json.loads(existing["user_tags"]) if existing is not None else [])
            )
            updated = now()
            db.execute(
                """
                INSERT INTO photo_annotations VALUES (?, ?, ?, ?)
                ON CONFLICT(photo_id) DO UPDATE SET
                    note=excluded.note, user_tags=excluded.user_tags, updated_at=excluded.updated_at
            """,
                (photo_id, final_note, json.dumps(final_tags), updated),
            )
            return {
                "photo_id": photo_id,
                "note": final_note,
                "user_tags": final_tags,
                "updated_at": updated,
                "scope": "local_catalog",
            }

        return await self._run(save)

    async def get_annotation(self, photo_id: str) -> dict:
        identifier({"id": photo_id})

        def read(db):
            row = db.execute(
                "SELECT note, user_tags, updated_at FROM photo_annotations WHERE photo_id=?",
                (photo_id,),
            ).fetchone()
            if row is None:
                return {
                    "photo_id": photo_id,
                    "note": "",
                    "user_tags": [],
                    "updated_at": None,
                    "scope": "local_catalog",
                }
            return {
                "photo_id": photo_id,
                "note": row["note"],
                "user_tags": json.loads(row["user_tags"]),
                "updated_at": row["updated_at"],
                "scope": "local_catalog",
            }

        return await self._run(read)

    async def get_watermark(self, key: str) -> dict | None:
        identifier({"id": key})

        def read(db):
            row = db.execute(
                "SELECT key, watermark, updated_at FROM sync_watermarks WHERE key=?", (key,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(read)

    async def set_watermark(self, key: str, watermark: str) -> dict:
        identifier({"id": key})
        wm = timestamp(watermark)

        def save(db):
            updated = now()
            db.execute(
                """
                INSERT INTO sync_watermarks VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    watermark=excluded.watermark, updated_at=excluded.updated_at
            """,
                (key, wm, updated),
            )
            return {"key": key, "watermark": wm, "updated_at": updated}

        return await self._run(save)

    async def get(self, photo_id: str) -> dict | None:
        def get(db):
            row = db.execute("SELECT metadata FROM photos WHERE id=?", (photo_id,)).fetchone()
            if not row:
                return None
            return _attach_annotations(db, [json.loads(row[0])])[0]

        return await self._run(get)

    async def search(
        self,
        camera_ids: list[str] | None = None,
        after: str | None = None,
        before: str | None = None,
        tags: list[str] | None = None,
        favorites_only: bool = False,
        limit: int = 50,
        cursor: str | None = None,
        has_video: bool | None = None,
        user_tags: list[str] | None = None,
    ) -> dict:
        if not 1 <= limit <= 500:
            raise SpypointError("Catalog page limit must be between 1 and 500.")
        after = timestamp(after) if after else None
        before = timestamp(before) if before else None
        if after and before and after > before:
            raise SpypointError("after must not be later than before.")
        spec = [
            sorted(set(camera_ids)) if camera_ids is not None else None,
            after,
            before,
            sorted(set(tags or [])),
            favorites_only,
            has_video,
            sorted(set(user_tags or [])),
        ]
        digest = hashlib.sha256(json.dumps(spec).encode()).hexdigest()
        boundary = None
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError
                boundary = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if (
                    boundary["query"] != digest
                    or not isinstance(boundary["id"], str)
                    or len(boundary["id"]) > 256
                ):
                    raise ValueError
                boundary["date"] = timestamp(boundary["date"])
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise SpypointError(
                    "Invalid catalog cursor or filters changed; start a new search."
                ) from None

        def search(db):
            clauses, params = selection(
                camera_ids, after, before, tags, favorites_only, has_video, user_tags
            )
            if boundary:
                clauses.append("(date < ? OR (date = ? AND id < ?))")
                params.extend([boundary["date"], boundary["date"], boundary["id"]])
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute(
                "SELECT metadata FROM photos" + where + " ORDER BY date DESC, id DESC LIMIT ?",
                [*params, limit + 1],
            ).fetchall()
            photos = _attach_annotations(db, [json.loads(row[0]) for row in rows[:limit]])
            next_cursor = None
            if len(rows) > limit:
                last = photos[-1]
                value = {"query": digest, "date": last["date"], "id": last["id"]}
                next_cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
            return {
                "photos": photos,
                "next_cursor": next_cursor,
                "scope": "local_catalog",
                "note": "Contains only locally indexed photos, not all photos in SPYPOINT.",
            }

        return await self._run(search)

    async def window(
        self,
        camera_ids: list[str] | None = None,
        after: str | None = None,
        before: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10000,
        favorites_only: bool = False,
        has_video: bool | None = None,
        user_tags: list[str] | None = None,
    ) -> dict:
        """Read one consistent, bounded SQLite snapshot for analytics and export."""
        if not 1 <= limit <= 50000:
            raise SpypointError("Analysis/export limit must be between 1 and 50000.")
        after = timestamp(after) if after else None
        before = timestamp(before) if before else None
        if after and before and after > before:
            raise SpypointError("after must not be later than before.")

        def read(db):
            clauses, params = selection(
                camera_ids, after, before, tags, favorites_only, has_video, user_tags
            )
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute(
                "SELECT metadata FROM photos" + where + " ORDER BY date DESC, id DESC LIMIT ?",
                [*params, limit + 1],
            ).fetchall()
            camera_names = {
                row["id"]: row["name"]
                for row in db.execute("SELECT id, name FROM cameras").fetchall()
            }
            photos = _attach_annotations(db, [json.loads(row[0]) for row in rows[:limit]])
            return {
                "photos": photos,
                "camera_names": camera_names,
                "truncated": len(rows) > limit,
                "scope": "local_catalog",
                "queried_at": now(),
                "after": after,
                "before": before,
            }

        return await self._run(read)

    async def download_record(self, photo_id: str, size: str) -> dict | None:
        def get(db):
            row = db.execute(
                "SELECT record FROM downloads WHERE photo_id=? AND size=?", (photo_id, size)
            ).fetchone()
            return json.loads(row[0]) if row else None

        return await self._run(get)

    async def record_download(self, record: dict) -> None:
        def save(db):
            db.execute(
                "INSERT OR REPLACE INTO downloads VALUES (?, ?, ?)",
                (record["photo"]["id"], record["size"], json.dumps(record)),
            )

        await self._run(save)

    async def create_job(self, state: dict) -> dict:
        def create(db):
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, 0, ?)", (state["id"], json.dumps(state), now())
            )
            return {**state, "revision": 0}

        return await self._run(create)

    async def get_job(self, job_id: str) -> dict:
        def get(db):
            row = db.execute("SELECT state, revision FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise SpypointError("Sync job not found in this account's local catalog.")
            return {**json.loads(row[0]), "revision": row[1]}

        return await self._run(get)

    async def list_jobs(self, limit: int = 20) -> list[dict]:
        def get(db):
            rows = db.execute(
                "SELECT state, revision FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 100),),
            ).fetchall()
            return [{**json.loads(row[0]), "revision": row[1]} for row in rows]

        return await self._run(get)

    async def seen_in_job(self, job_id: str, photo_ids: list[str]) -> set[str]:
        if not photo_ids:
            return set()

        def get(db):
            placeholders = ",".join("?" for _ in photo_ids)
            rows = db.execute(
                f"SELECT photo_id FROM job_photos WHERE job_id=? AND photo_id IN ({placeholders})",
                [job_id, *photo_ids],
            ).fetchall()
            return {row[0] for row in rows}

        return await self._run(get)

    async def checkpoint(self, state: dict, photos: list[dict]) -> dict:
        def save(db):
            revision = state["revision"]
            stored = {k: v for k, v in state.items() if k != "revision"}
            result = db.execute(
                "UPDATE jobs SET state=?, revision=revision+1, updated_at=? "
                "WHERE id=? AND revision=?",
                (json.dumps(stored), now(), state["id"], revision),
            )
            if result.rowcount != 1:
                raise SpypointError(
                    "Another request advanced this sync job; get its status and retry."
                )
            self._upsert(db, photos)
            db.executemany(
                "INSERT OR IGNORE INTO job_photos VALUES (?, ?)",
                [(state["id"], photo["id"]) for photo in photos],
            )
            return {**stored, "revision": revision + 1}

        return await self._run(save)

    async def summary(self) -> dict[str, Any]:
        def summary(db):
            return {
                "photos": db.execute("SELECT count(*) FROM photos").fetchone()[0],
                "downloads": db.execute("SELECT count(*) FROM downloads").fetchone()[0],
                "jobs": db.execute("SELECT count(*) FROM jobs").fetchone()[0],
                "weather_observations": db.execute(
                    "SELECT count(*) FROM weather_observations"
                ).fetchone()[0],
                "cameras": [
                    dict(row)
                    for row in db.execute(
                        "SELECT photos.camera_id, cameras.name AS camera_name, "
                        "count(*) AS photos, min(photos.date) AS oldest, "
                        "max(photos.date) AS newest FROM photos "
                        "LEFT JOIN cameras ON cameras.id = photos.camera_id "
                        "GROUP BY photos.camera_id ORDER BY photos.camera_id"
                    )
                ],
                "scope": "local_catalog",
                "schema_version": 4,
            }

        return await self._run(summary)

    async def store_weather_observations(self, observations: list[dict]) -> int:
        if not observations:
            return 0

        def save(db):
            for obs in observations:
                db.execute(
                    """
                    INSERT INTO weather_observations VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(grid_lat, grid_lon, hour_utc) DO UPDATE SET
                        temperature_c=excluded.temperature_c,
                        apparent_temperature_c=excluded.apparent_temperature_c,
                        relative_humidity=excluded.relative_humidity,
                        dew_point_c=excluded.dew_point_c,
                        pressure_hpa=excluded.pressure_hpa,
                        wind_speed_kmh=excluded.wind_speed_kmh,
                        wind_direction_deg=excluded.wind_direction_deg,
                        wind_gusts_kmh=excluded.wind_gusts_kmh,
                        precipitation_mm=excluded.precipitation_mm,
                        snowfall_cm=excluded.snowfall_cm,
                        cloud_cover_pct=excluded.cloud_cover_pct,
                        weather_code=excluded.weather_code,
                        weather_condition=excluded.weather_condition,
                        weather_category=excluded.weather_category,
                        fetched_at=excluded.fetched_at
                """,
                    (
                        obs["grid_lat"],
                        obs["grid_lon"],
                        obs["hour_utc"],
                        obs["temperature_c"],
                        obs["apparent_temperature_c"],
                        obs["relative_humidity"],
                        obs["dew_point_c"],
                        obs["pressure_hpa"],
                        obs["wind_speed_kmh"],
                        obs["wind_direction_deg"],
                        obs["wind_gusts_kmh"],
                        obs["precipitation_mm"],
                        obs["snowfall_cm"],
                        obs["cloud_cover_pct"],
                        obs["weather_code"],
                        obs["weather_condition"],
                        obs["weather_category"],
                        obs["fetched_at"],
                    ),
                )
            return len(observations)

        return await self._run(save)

    async def get_cached_weather(
        self, grid_lat: float, grid_lon: float, hours_utc: list[str]
    ) -> dict[str, dict]:
        if not hours_utc:
            return {}

        def read(db):
            placeholders = ",".join("?" for _ in hours_utc)
            rows = db.execute(
                f"""
                SELECT grid_lat, grid_lon, hour_utc, temperature_c, apparent_temperature_c,
                       relative_humidity, dew_point_c, pressure_hpa, wind_speed_kmh,
                       wind_direction_deg, wind_gusts_kmh, precipitation_mm, snowfall_cm,
                       cloud_cover_pct, weather_code, weather_condition, weather_category,
                       fetched_at
                FROM weather_observations
                WHERE grid_lat=? AND grid_lon=? AND hour_utc IN ({placeholders})
                """,
                [grid_lat, grid_lon, *hours_utc],
            ).fetchall()
            return {row["hour_utc"]: dict(row) for row in rows}

        return await self._run(read)

    async def get_weather_coverage(self, grid_lat: float, grid_lon: float) -> dict:
        def read(db):
            row = db.execute(
                """
                SELECT count(*) as count, min(hour_utc) as oldest, max(hour_utc) as newest
                FROM weather_observations
                WHERE grid_lat=? AND grid_lon=?
                """,
                (grid_lat, grid_lon),
            ).fetchone()
            return dict(row) if row else {"count": 0, "oldest": None, "newest": None}

        return await self._run(read)
