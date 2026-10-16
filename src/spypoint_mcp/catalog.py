"""Private account-scoped SQLite catalog. Only sanitized metadata is persisted."""

import asyncio
import base64
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .client import timestamp
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
    return clauses, params


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
            if version not in (0, 1, 2):
                raise SpypointError(
                    "Catalog was created by a newer server; upgrade before opening it."
                )
            if version < 2:
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
                    PRAGMA user_version=2;
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

    async def remember(self, photos: list[dict]) -> None:
        await self._run(self._upsert, photos)

    async def remember_cameras(self, cameras: list[dict]) -> None:
        await self._run(self._upsert_cameras, cameras)

    async def get(self, photo_id: str) -> dict | None:
        def get(db):
            row = db.execute("SELECT metadata FROM photos WHERE id=?", (photo_id,)).fetchone()
            return json.loads(row[0]) if row else None

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
            clauses, params = selection(camera_ids, after, before, tags, favorites_only, has_video)
            if boundary:
                clauses.append("(date < ? OR (date = ? AND id < ?))")
                params.extend([boundary["date"], boundary["date"], boundary["id"]])
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute(
                "SELECT metadata FROM photos" + where + " ORDER BY date DESC, id DESC LIMIT ?",
                [*params, limit + 1],
            ).fetchall()
            photos = [json.loads(row[0]) for row in rows[:limit]]
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
    ) -> dict:
        """Read one consistent, bounded SQLite snapshot for analytics and export."""
        if not 1 <= limit <= 50000:
            raise SpypointError("Analysis/export limit must be between 1 and 50000.")
        after = timestamp(after) if after else None
        before = timestamp(before) if before else None
        if after and before and after > before:
            raise SpypointError("after must not be later than before.")

        def read(db):
            clauses, params = selection(camera_ids, after, before, tags, favorites_only, has_video)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute(
                "SELECT metadata FROM photos" + where + " ORDER BY date DESC, id DESC LIMIT ?",
                [*params, limit + 1],
            ).fetchall()
            camera_names = {
                row["id"]: row["name"]
                for row in db.execute("SELECT id, name FROM cameras").fetchall()
            }
            return {
                "photos": [json.loads(row[0]) for row in rows[:limit]],
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
                "schema_version": 2,
            }

        return await self._run(summary)
