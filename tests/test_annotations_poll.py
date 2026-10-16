import pytest
from conftest import photo
from mcp import Client

from spypoint_mcp.config import SpypointError
from spypoint_mcp.server import create_server
from spypoint_mcp.service import PhotoService


async def test_photo_annotations_and_user_tag_filtering(api, fake):
    fake.rows = [
        photo("p1", "c1", "2026-09-20T12:00:00.000Z"),
        photo("p2", "c1", "2026-09-21T12:00:00.000Z"),
    ]
    async with Client(create_server(client=api)) as client:
        await client.call_tool("list_photos", {})
        empty_ann = (
            await client.call_tool("annotate_photo", {"photo_id": "p1"})
        ).structured_content
        assert empty_ann["note"] == "" and empty_ann["user_tags"] == []

        saved = (
            await client.call_tool(
                "annotate_photo",
                {
                    "photo_id": "p1",
                    "note": "8-pointer Split-Brow heading north",
                    "user_tags": ["split-brow", "target-buck", "split-brow"],
                },
            )
        ).structured_content
        assert saved["note"] == "8-pointer Split-Brow heading north"
        assert saved["user_tags"] == ["split-brow", "target-buck"]

        # Partial update preserves existing user_tags when only note is updated:
        updated = (
            await client.call_tool(
                "annotate_photo",
                {"photo_id": "p1", "note": "Confirmed Split-Brow"},
            )
        ).structured_content
        assert updated["note"] == "Confirmed Split-Brow"
        assert updated["user_tags"] == ["split-brow", "target-buck"]

        found = (
            await client.call_tool("search_catalog", {"user_tags": ["target-buck"]})
        ).structured_content
        assert [p["id"] for p in found["photos"]] == ["p1"]
        assert found["photos"][0]["user_note"] == "Confirmed Split-Brow"
        assert found["photos"][0]["user_tags"] == ["split-brow", "target-buck"]

    service = PhotoService(api)
    with pytest.raises(SpypointError, match="4000 characters"):
        await service.catalog.annotate("p1", note="x" * 4001)
    with pytest.raises(SpypointError, match="50 user_tags"):
        await service.catalog.annotate("p1", user_tags=[f"t{i}" for i in range(51)])
    with pytest.raises(SpypointError, match="nonempty string"):
        await service.catalog.annotate("p1", user_tags=["   "])


async def test_check_new_photos_high_water_mark_polling(api, fake):
    fake.rows = [
        photo("p1", "c1", "2026-09-20T12:00:00.000Z"),
        photo("p2", "c1", "2026-09-21T12:00:00.000Z"),
    ]
    async with Client(create_server(client=api)) as client:
        first_poll = (
            await client.call_tool("check_new_photos", {"watermark_key": "cron-feed"})
        ).structured_content
        assert first_poll["previous_watermark"] is None
        assert first_poll["new_watermark"] == "2026-09-21T12:00:00.000Z"
        assert first_poll["new_photos_count"] == 2

        second_poll = (
            await client.call_tool("check_new_photos", {"watermark_key": "cron-feed"})
        ).structured_content
        assert second_poll["previous_watermark"] == "2026-09-21T12:00:00.000Z"
        assert second_poll["new_photos_count"] == 0

        fake.rows.append(photo("p3", "c1", "2026-09-22T08:30:00.000Z"))
        third_poll = (
            await client.call_tool("check_new_photos", {"watermark_key": "cron-feed"})
        ).structured_content
        assert third_poll["new_photos_count"] == 1
        assert [p["id"] for p in third_poll["photos"]] == ["p3"]
        assert third_poll["new_watermark"] == "2026-09-22T08:30:00.000Z"
