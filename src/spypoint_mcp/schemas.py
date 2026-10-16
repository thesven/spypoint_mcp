"""Public output contracts; telemetry remains vendor-shaped where units vary by model."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Camera(Output):
    id: str
    name: str
    shared: bool
    model: str | None = None
    last_update: str | None = None
    batteries: Any = None
    signal: Any = None
    memory: Any = None
    temperature: Any = None
    firmware: str | None = None
    video_capable: bool | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)


class CameraInventory(Output):
    cameras: list[Camera]
    complete: bool
    shared_included: bool
    warnings: list[str]


class Photo(Output):
    id: str
    camera_id: str
    date: str | None
    captured_at: str | None = None
    uploaded_at: str | None = None
    filename: str | None = None
    tags: list[str]
    favorite: bool
    available_sizes: list[str]
    available_video_sizes: list[str] = Field(default_factory=list)
    video_duration_seconds: float | None = None
    hd_status: Literal["available", "pending", "unavailable", "error"] = "unavailable"


class PhotoPage(Output):
    photos: list[Photo]
    next_before: str | None
    may_have_more: bool
    fetched: int
    camera_ids: list[str]
    camera_discovery_complete: bool
    warnings: list[str]
    pagination_note: str | None = None


class CatalogPage(Output):
    photos: list[Photo]
    next_cursor: str | None
    scope: Literal["local_catalog"]
    note: str


class Download(Output):
    path: str
    bytes: int = Field(ge=0)
    mime_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    photo: Photo
    size: str
    reused: bool = False


class PhotoFilters(Output):
    tags: list[str]
    media_types: list[str]
    time_of_day: list[str]


class CameraCoverage(Output):
    camera_id: str
    camera_name: str | None = None
    photos: int
    oldest: str
    newest: str


class CatalogStatus(Output):
    photos: int
    downloads: int
    jobs: int
    cameras: list[CameraCoverage]
    scope: Literal["local_catalog"]
    schema_version: int


class CameraCheckpoint(Output):
    camera_id: str
    status: Literal["pending", "error", "exhausted", "pagination_stalled"]
    next_before: str
    page_size: int
    pages: int
    indexed: int
    downloaded: int
    error: str | None = None


class SyncJob(Output):
    id: str
    revision: int
    created_at: str
    updated_at: str | None = None
    status: Literal["pending", "finished", "partial", "needs_attention"]
    after: str | None
    before: str
    download: bool
    size: str
    camera_discovery_complete: bool
    warnings: list[str]
    next_camera_index: int
    cameras: list[CameraCheckpoint]
    coverage_note: str


class SyncJobs(Output):
    jobs: list[SyncJob]


class CameraHealth(Output):
    camera_id: str
    name: str
    status: Literal["ok", "attention", "unknown"]
    findings: list[str]
    unknown_measurements: list[str]
    hours_since_update: float | None = None
    signal_percentage: float | None = None
    memory_used_percentage: float | None = None
    batteries: Any = None


class HealthThresholds(Output):
    stale_hours: float
    signal_below: float
    memory_at_or_above: float


class FleetHealth(Output):
    cameras: list[CameraHealth]
    checked_at: str
    complete: bool
    warnings: list[str]
    thresholds: HealthThresholds
    note: str


class CameraActivity(Output):
    camera_id: str
    camera_name: str | None = None
    photos: int
    tags: dict[str, int]
    first: str | None
    last: str | None


class ActivitySummary(Output):
    scope: Literal["local_catalog"]
    photo_count: int
    truncated: bool
    timezone: str
    reinterpret_utc_as_local: bool = False
    suspected_wall_clock_z_photos: int = 0
    timestamp_warnings: list[str] = Field(default_factory=list)
    by_day: dict[str, int]
    by_local_hour: dict[str, int]
    by_weekday: dict[str, int]
    hourly_timeline: dict[str, int]
    tag_counts: dict[str, int]
    by_camera: list[CameraActivity]
    interpretation: str
    queried_at: str
    after: str | None = None
    before: str | None = None


class CatalogExport(Output):
    format: Literal["csv", "json"]
    rows: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    truncated: bool
    scope: Literal["local_catalog"]
    queried_at: str
    after: str | None = None
    before: str | None = None
    data_file: str
    note: str
    path: str
    manifest_path: str
