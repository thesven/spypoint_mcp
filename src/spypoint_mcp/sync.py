"""Resumable, explicitly bounded per-camera photo indexing and download jobs."""

from uuid import uuid4

from .catalog import now
from .client import SIZES, timestamp
from .config import SpypointError
from .service import PhotoService


class SyncEngine:
    def __init__(self, service: PhotoService):
        self.service = service

    async def start(
        self,
        camera_ids: list[str] | None = None,
        after: str | None = None,
        before: str | None = None,
        download: bool = False,
        size: str = "large",
        page_size: int = 50,
    ) -> dict:
        if not 1 <= page_size <= 100 or size not in SIZES:
            raise SpypointError("Use a page_size from 1 to 100 and a supported image size.")
        before = timestamp(before) if before else now()
        after = timestamp(after) if after else None
        if after and after > before:
            raise SpypointError("after must not be later than before.")
        inventory = await self.service.cameras()
        known = {c["id"] for c in inventory["cameras"]}
        selected = sorted(known if camera_ids is None else set(camera_ids))
        if not set(selected) <= known:
            raise SpypointError("Unknown camera id; list cameras first.")
        state = {
            "id": str(uuid4()),
            "created_at": now(),
            "status": "pending" if selected else "finished" if inventory["complete"] else "partial",
            "after": after,
            "before": before,
            "download": download,
            "size": size,
            "camera_discovery_complete": inventory["complete"],
            "warnings": inventory["warnings"],
            "next_camera_index": 0,
            "cameras": [
                {
                    "camera_id": cid,
                    "status": "pending",
                    "next_before": before,
                    "page_size": page_size,
                    "pages": 0,
                    "indexed": 0,
                    "downloaded": 0,
                }
                for cid in selected
            ],
            "coverage_note": "A finished job exhausted the observed upstream pages for its "
            "snapshot and interval. It cannot prove historical retention or capture completeness. "
            "A timestamp boundary that cannot advance is reported as pagination_stalled.",
        }
        if camera_ids is None and not inventory["complete"]:
            state["warnings"] = [
                *state["warnings"],
                "Some cameras could not be discovered; this job covers only listed cameras.",
            ]
        return await self.service.catalog.create_job(state)

    async def run(self, job_id: str, page_budget: int = 1, progress=None) -> dict:
        if not 1 <= page_budget <= 20:
            raise SpypointError("page_budget must be between 1 and 20.")
        catalog = self.service.catalog
        state = await catalog.get_job(job_id)
        # Revalidate current account access after a restart or a revoked share.
        inventory = await self.service.cameras()
        known = {c["id"] for c in inventory["cameras"]}
        entries = state["cameras"]
        attempted: set[int] = set()
        for step in range(page_budget):
            eligible = [
                i
                for i, item in enumerate(entries)
                if item["status"] in {"pending", "error"} and i not in attempted
            ]
            if not eligible:
                break
            start = state["next_camera_index"]
            idx = next((i for i in eligible if i >= start), eligible[0])
            entry = entries[idx]
            state["next_camera_index"] = (idx + 1) % len(entries)
            photos = []
            try:
                if entry["camera_id"] not in known:
                    raise SpypointError("Camera is no longer accessible; no data was fetched.")
                page = await self.service.client.photos(
                    [entry["camera_id"]],
                    before=entry["next_before"],
                    after=state["after"],
                    limit=entry["page_size"],
                )
                photos = page["photos"]
                seen = await catalog.seen_in_job(job_id, [p["id"] for p in photos])
                fresh = [p for p in photos if p["id"] not in seen]
                if state["download"]:
                    for photo in fresh:
                        await self.service.download(photo["id"], state["size"], reuse_existing=True)
                entry["pages"] += 1
                entry["indexed"] += len(fresh)
                entry["downloaded"] += len(fresh) if state["download"] else 0
                entry.pop("error", None)
                next_before = page["next_before"]
                if not page["may_have_more"]:
                    entry["status"] = "exhausted"
                elif not next_before:
                    entry["status"] = "pagination_stalled"
                elif not fresh and next_before >= entry["next_before"]:
                    if entry["page_size"] < 100:
                        entry["page_size"] = min(100, entry["page_size"] * 2)
                        entry["status"] = "pending"
                    else:
                        entry["status"] = "pagination_stalled"
                        entry["error"] = (
                            "The maximum-size page repeats the same date boundary. "
                            "No timestamps were skipped."
                        )
                else:
                    entry["next_before"] = next_before
                    entry["status"] = "pending"
            except SpypointError as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)
                photos = []
                attempted.add(idx)
            state["status"] = (
                ("finished" if state["camera_discovery_complete"] else "partial")
                if all(c["status"] == "exhausted" for c in entries)
                else "pending"
                if any(c["status"] == "pending" for c in entries)
                else "needs_attention"
            )
            state["updated_at"] = now()
            state = await catalog.checkpoint(state, photos)
            entries = state["cameras"]
            if progress:
                await progress(step + 1, page_budget)
        return state
