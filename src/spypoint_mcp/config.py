"""Explicit configuration; never search parent directories for credentials."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values


class SpypointError(Exception):
    """An actionable message safe to expose through MCP."""


@dataclass(frozen=True)
class Settings:
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    token: str = field(default="", repr=False)
    download_dir: Path = field(default_factory=lambda: Path.cwd() / "downloads")
    include_shared: bool = True
    timeout: float = 30
    max_image_bytes: int = 10_000_000
    image_hosts: tuple[str, ...] = ()
    camera_timezone: str | None = None
    camera_time_mode: Literal["strict", "wall_clock_z"] = "strict"
    state_dir: Path | None = None
    http_token: str = field(default="", repr=False)
    max_video_bytes: int = 100_000_000
    default_latitude: float | None = None
    default_longitude: float | None = None

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        values: dict[str, str | None] = {}
        if env_file is not None:
            env_file = env_file.expanduser().resolve()
            if not env_file.is_file():
                raise SpypointError("Credential file is missing. Run spypoint-mcp configure first.")
            values.update(dotenv_values(env_file, interpolate=False))
        values.update(os.environ)

        def get(key: str, default: str = "") -> str:
            return values.get("SPYPOINT_" + key) or default

        root = env_file.parent if env_file else Path.cwd()
        target = Path(get("DOWNLOAD_DIR", "downloads")).expanduser()
        shared = get("INCLUDE_SHARED", "true").lower()
        camera_timezone = get("CAMERA_TIMEZONE") or None
        if camera_timezone:
            try:
                ZoneInfo(camera_timezone)
            except (ValueError, ZoneInfoNotFoundError):
                raise SpypointError(
                    "SPYPOINT_CAMERA_TIMEZONE must be a valid IANA timezone."
                ) from None
        camera_time_mode = get("CAMERA_TIME_MODE", "strict").lower()
        if camera_time_mode not in {"strict", "wall_clock_z"}:
            raise SpypointError("SPYPOINT_CAMERA_TIME_MODE must be strict or wall_clock_z.")
        if camera_time_mode == "wall_clock_z" and not camera_timezone:
            raise SpypointError(
                "SPYPOINT_CAMERA_TIME_MODE=wall_clock_z requires SPYPOINT_CAMERA_TIMEZONE."
            )
        state_dir = Path(get("STATE_DIR", ".spypoint-state")).expanduser()
        if shared not in {"true", "false"}:
            raise SpypointError("SPYPOINT_INCLUDE_SHARED must be true or false.")
        raw_lat, raw_lon = get("DEFAULT_LATITUDE").strip(), get("DEFAULT_LONGITUDE").strip()
        if bool(raw_lat) != bool(raw_lon):
            raise SpypointError(
                "Provide both SPYPOINT_DEFAULT_LATITUDE and SPYPOINT_DEFAULT_LONGITUDE or neither."
            )
        try:
            timeout = float(get("TIMEOUT_SECONDS", "30"))
            max_bytes = int(get("MAX_IMAGE_BYTES", "10000000"))
            max_video_bytes = int(get("MAX_VIDEO_BYTES", "100000000"))
            default_lat = float(raw_lat) if raw_lat else None
            default_lon = float(raw_lon) if raw_lon else None
        except ValueError:
            raise SpypointError("Invalid numeric SPYPOINT configuration.") from None
        if default_lat is not None and default_lon is not None:
            if (
                not math.isfinite(default_lat)
                or not math.isfinite(default_lon)
                or not -90 <= default_lat <= 90
                or not -180 <= default_lon <= 180
            ):
                raise SpypointError("Default latitude must be -90..90 and longitude -180..180.")
        if (
            not math.isfinite(timeout)
            or not 1 <= timeout <= 120
            or not 1024 <= max_bytes <= 50_000_000
        ):
            raise SpypointError("Timeout must be 1–120 seconds; image limit 1024–50000000 bytes.")
        if not 1024 <= max_video_bytes <= 500_000_000:
            raise SpypointError("SPYPOINT_MAX_VIDEO_BYTES must be between 1024 and 500000000.")
        return cls(
            username=get("USERNAME"),
            password=get("PASSWORD"),
            token=get("TOKEN"),
            download_dir=(root / target).resolve(),
            include_shared=shared == "true",
            timeout=timeout,
            max_image_bytes=max_bytes,
            image_hosts=tuple(
                x.strip().lower() for x in get("IMAGE_HOSTS").split(",") if x.strip()
            ),
            camera_timezone=camera_timezone,
            camera_time_mode=camera_time_mode,  # type: ignore[arg-type]
            state_dir=(root / state_dir).resolve(),
            http_token=get("MCP_HTTP_TOKEN"),
            max_video_bytes=max_video_bytes,
            default_latitude=default_lat,
            default_longitude=default_lon,
        )

    def require_credentials(self) -> None:
        if not self.token and not (self.username and self.password):
            raise SpypointError(
                "SPYPOINT credentials are not configured. Run spypoint-mcp configure locally, "
                "then launch with --env-file /absolute/path/.env. Do not paste passwords into chat."
            )
