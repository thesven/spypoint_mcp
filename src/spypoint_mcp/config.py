"""Explicit configuration; never search parent directories for credentials."""

import os
from dataclasses import dataclass, field
from pathlib import Path
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
    state_dir: Path | None = None
    http_token: str = field(default="", repr=False)
    max_video_bytes: int = 100_000_000

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
        state_dir = Path(get("STATE_DIR", ".spypoint-state")).expanduser()
        if shared not in {"true", "false"}:
            raise SpypointError("SPYPOINT_INCLUDE_SHARED must be true or false.")
        try:
            timeout = float(get("TIMEOUT_SECONDS", "30"))
            max_bytes = int(get("MAX_IMAGE_BYTES", "10000000"))
            max_video_bytes = int(get("MAX_VIDEO_BYTES", "100000000"))
        except ValueError:
            raise SpypointError("Invalid numeric SPYPOINT configuration.") from None
        if not 1 <= timeout <= 120 or not 1024 <= max_bytes <= 50_000_000:
            raise SpypointError("Timeout must be 1–120 seconds; image limit 1024–50000000 bytes.")
        if not 1024 <= max_video_bytes <= 500_000_000:
            raise SpypointError("SPYPOINT_MAX_VIDEO_BYTES must be between 1024 and 500000000.")
        return cls(
            username=get("USERNAME"),
            password=get("PASSWORD"),
            token=get("TOKEN"),
            download_dir=(root / target).resolve(),
            include_shared=shared,
            timeout=timeout,
            max_image_bytes=max_bytes,
            image_hosts=tuple(
                x.strip().lower() for x in get("IMAGE_HOSTS").split(",") if x.strip()
            ),
            camera_timezone=camera_timezone,
            state_dir=(root / state_dir).resolve(),
            http_token=get("MCP_HTTP_TOKEN"),
            max_video_bytes=max_video_bytes,
        )

    def require_credentials(self) -> None:
        if not self.token and not (self.username and self.password):
            raise SpypointError(
                "SPYPOINT credentials are not configured. Run spypoint-mcp configure locally, "
                "then launch with --env-file /absolute/path/.env. Do not paste passwords into chat."
            )
