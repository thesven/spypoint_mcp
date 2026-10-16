import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import PNG, photo
from mcp import Client

from spypoint_mcp.catalog import Catalog
from spypoint_mcp.client import SpypointClient, photo_summary
from spypoint_mcp.config import Settings, SpypointError
from spypoint_mcp.server import create_server
from spypoint_mcp.service import PhotoService
from spypoint_mcp.sync import SyncEngine


async def test_catalog_restart_isolation_and_secret_exclusion(tmp_path):
    settings = Settings(username="one", password="SECRET", state_dir=tmp_path)
    first = Catalog(settings)
    data = photo_summary(photo())
    data["token"] = "DO-NOT-PERSIST"
    data["large"] = {"path": "SIGNED-URL"}
    await first.remember([data])
    second = Catalog(settings)
    assert (await second.get("p1"))["camera_id"] == "c1"
    assert "DO-NOT-PERSIST" not in first.path.read_bytes().decode(errors="ignore")
    assert "SIGNED-URL" not in first.path.read_bytes().decode(errors="ignore")
    assert first.path.stat().st_mode & 0o777 == 0o600
    other = Catalog(replace(settings, username="two"))
    assert await other.get("p1") is None
    token = Catalog(replace(settings, token="another-account-token"))
    assert await token.get("p1") is None


async def test_catalog_keyset_no_duplicates_with_tied_dates(api):
    catalog = PhotoService(api).catalog
    await catalog.remember([photo_summary(photo(f"p{i}")) for i in range(7)])
    cursor, ids = None, []
    while True:
        page = await catalog.search(limit=2, cursor=cursor)
        ids += [p["id"] for p in page["photos"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert ids == [f"p{i}" for i in reversed(range(7))]
    assert (await catalog.summary())["photos"] == 7


async def test_catalog_cursor_filter_binding_and_sql_injection(api):
    catalog = PhotoService(api).catalog
    await catalog.remember([photo_summary(photo("p1")), photo_summary(photo("p2"))])
    cursor = (await catalog.search(limit=1))["next_cursor"]
    with pytest.raises(SpypointError, match="filters changed"):
        await catalog.search(cursor=cursor, tags=["buck"])
    assert not (await catalog.search(tags=["' OR 1=1 --"]))["photos"]
    assert not (await catalog.search(camera_ids=[]))["photos"]
    with pytest.raises(SpypointError):
        await catalog.search(cursor="not base64!")


async def test_catalog_all_tags_favorites_and_date_range(api):
    catalog = PhotoService(api).catalog
    first, second = photo(), photo("p2", date="2026-09-19T12:00:00.000Z")
    first["tag"] = ["deer", "night", "fav"]
    await catalog.remember([photo_summary(first), photo_summary(second)])
    result = await catalog.search(
        tags=["deer", "night"], favorites_only=True, after="2026-09-20T00:00:00Z"
    )
    assert [p["id"] for p in result["photos"]] == ["p1"]
    await catalog.remember([photo_summary(first)])
    assert (await catalog.summary())["photos"] == 2


async def test_sync_restart_fairness_and_verified_reuse(api, fake):
    fake.owned += [{"id": "c2"}]
    fake.rows = [photo("p1", "c1"), photo("p2", "c2")]
    service = PhotoService(api)
    job = await SyncEngine(service).start(download=True)
    result = await SyncEngine(service).run(job["id"])
    assert [x["status"] for x in result["cameras"]] == ["exhausted", "pending"]
    api2 = SpypointClient(api.settings, httpx.MockTransport(fake))
    try:
        resumed = await SyncEngine(PhotoService(api2)).run(job["id"])
        assert resumed["status"] == "finished"
        assert sum(x["downloaded"] for x in resumed["cameras"]) == 2
        assert (await service.catalog.summary())["photos"] == 2
        another = await SyncEngine(PhotoService(api2)).start(download=True)
        count = len([r for r in fake.calls if r.url.host == "s3.amazonaws.com"])
        await SyncEngine(PhotoService(api2)).run(another["id"], 2)
        assert len([r for r in fake.calls if r.url.host == "s3.amazonaws.com"]) == count
    finally:
        await api2.close()


async def test_sync_overlap_deduplicates_and_expands_tied_page(api, fake):
    fake.rows = [photo(f"p{i}") for i in range(6)]
    service = PhotoService(api)
    engine = SyncEngine(service)
    job = await engine.start(page_size=2)
    for _ in range(10):
        job = await engine.run(job["id"])
        if job["status"] != "pending":
            break
    assert job["status"] == "finished"
    assert job["cameras"][0]["indexed"] == 6
    assert (await service.catalog.summary())["photos"] == 6


async def test_sync_does_not_skip_saturated_timestamp(api, fake):
    fake.rows = [photo(f"p{i:03d}") for i in range(101)]
    engine = SyncEngine(PhotoService(api))
    job = await engine.start(page_size=100)
    for _ in range(4):
        job = await engine.run(job["id"])
    entry = job["cameras"][0]
    assert job["status"] == "needs_attention"
    assert entry["status"] == "pagination_stalled" and entry["indexed"] == 100
    assert entry["next_before"] == fake.rows[0]["originDate"]
    assert len([r for r in fake.calls if r.url.path.endswith("/photo/all")]) == 2


async def test_sync_cancellation_preserves_prior_checkpoint(api, fake):
    fake.owned += [{"id": "c2"}]
    engine = SyncEngine(PhotoService(api))
    job = await engine.start()
    first = await engine.run(job["id"])

    def cancel(request):
        raise asyncio.CancelledError

    fake.overrides["/api/v3/photo/all"] = cancel
    with pytest.raises(asyncio.CancelledError):
        await engine.run(job["id"])
    stored = await engine.service.catalog.get_job(job["id"])
    assert stored == first
    del fake.overrides["/api/v3/photo/all"]
    assert (await engine.run(job["id"]))["status"] == "finished"


async def test_sync_error_retries_and_corrupt_download_is_not_overwritten(api, fake):
    engine = SyncEngine(PhotoService(api))
    fake.overrides["/bucket/image.png"] = lambda r: httpx.Response(403)
    job = await engine.start(download=True)
    failed = await engine.run(job["id"])
    assert failed["cameras"][0]["status"] == "error"
    assert failed["cameras"][0]["indexed"] == 0
    del fake.overrides["/bucket/image.png"]
    assert (await engine.run(job["id"]))["status"] == "finished"
    record = await engine.service.catalog.download_record("p1", "large")
    target = Path(record["path"])
    target.write_bytes(b"corrupt")
    new_job = await engine.start(download=True)
    result = await engine.run(new_job["id"])
    assert "integrity" in result["cameras"][0]["error"]
    assert target.read_bytes() == b"corrupt"


async def test_sync_optimistic_checkpoint_conflict_rolls_back_metadata(api):
    engine = SyncEngine(PhotoService(api))
    job = await engine.start()
    catalog = engine.service.catalog
    await catalog.checkpoint(job, [])
    with pytest.raises(SpypointError, match="Another request"):
        await catalog.checkpoint(job, [photo_summary(photo("not-committed"))])
    assert await catalog.get("not-committed") is None


async def test_sync_recovers_file_published_before_catalog_write(api):
    service = PhotoService(api)
    await api.photos(["c1"])
    result = await service.download("p1")
    # New empty catalog namespace simulates a lost write but the immutable image remains.
    service._catalog = Catalog(replace(api.settings, username="different-local-namespace"))
    recovered = await service.download("p1", reuse_existing=True)
    assert recovered["sha256"] == result["sha256"]
    assert Path(recovered["path"]).read_bytes() == PNG


async def test_sync_mcp_resources_prompts_and_roundtrip(api):
    async with Client(create_server(client=api), raise_exceptions=True) as mcp:
        created = await mcp.call_tool("start_photo_sync", {})
        assert not created.is_error
        job_id = created.structured_content["id"]
        run = await mcp.call_tool("continue_photo_sync", {"job_id": job_id})
        assert not run.is_error and run.structured_content["status"] == "finished"
        jobs = await mcp.call_tool("list_sync_jobs", {})
        assert jobs.structured_content["jobs"][0]["id"] == job_id
        status = await mcp.call_tool("get_sync_status", {"job_id": job_id})
        assert status.structured_content["cameras"][0]["indexed"] == 1
        search = await mcp.call_tool("search_catalog", {"tags": ["deer"]})
        assert search.structured_content["photos"][0]["id"] == "p1"
        resource = await mcp.read_resource("spypoint://catalog/status")
        assert json.loads(resource.contents[0].text)["photos"] == 1
        guide = await mcp.read_resource("spypoint://guide")
        assert "timestamp" in guide.contents[0].text
        prompts = await mcp.list_prompts()
        assert {p.name for p in prompts.prompts} == {"review_camera_fleet", "archive_camera_photos"}


async def test_catalog_v1_migration_future_version_rejection_and_video_filter(api):
    import sqlite3
    from contextlib import closing

    service = PhotoService(api)
    await service.list_photos()
    status = await service.catalog.summary()
    assert status["schema_version"] == 3
    assert status["cameras"][0]["camera_name"] == "Field"

    raw_video = photo("p_vid", "c1", "2026-09-21T12:00:00.000Z")
    raw_video["sdVideo"] = {"host": "s3.amazonaws.com", "path": "clip.mp4"}
    await service.catalog.remember([photo_summary(raw_video)])
    videos_only = await service.catalog.search(has_video=True)
    assert [p["id"] for p in videos_only["photos"]] == ["p_vid"]
    stills_only = await service.catalog.search(has_video=False)
    assert [p["id"] for p in stills_only["photos"]] == ["p1"]

    with closing(sqlite3.connect(service.catalog.path)) as db, db:
        db.execute("DROP TABLE cameras")
        db.execute("PRAGMA user_version=1")
    migrated = await service.catalog.summary()
    assert migrated["schema_version"] == 3 and migrated["photos"] == 2

    with closing(sqlite3.connect(service.catalog.path)) as db, db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(SpypointError, match="newer server"):
        await service.catalog.summary()
