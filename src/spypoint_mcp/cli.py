"""Local setup and diagnostics. Serving never writes diagnostics to stdout."""

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .client import SpypointClient
from .config import Settings, SpypointError
from .server import create_server
from .service import PhotoService
from .sync import SyncEngine
from .transport import http_app


def configure(path: Path) -> None:
    path = path.expanduser().resolve()
    if path.exists():
        raise SpypointError(
            "Credential file already exists. Edit it locally; configure will not overwrite it."
        )
    username = input("SPYPOINT email/username: ").strip()
    password = getpass.getpass("SPYPOINT password (hidden): ")
    if not username or not password:
        raise SpypointError("Username and password must both be nonempty.")

    def quoted(value: str) -> str:
        if "\n" in value or "\r" in value:
            raise SpypointError("Credentials may not contain newlines.")
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    contents = (
        f"SPYPOINT_USERNAME={quoted(username)}\n"
        f"SPYPOINT_PASSWORD={quoted(password)}\nSPYPOINT_DOWNLOAD_DIR=downloads\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(contents)
    print(f"Credentials saved locally to {path} (owner access only).")


async def doctor(settings: Settings, check_images: bool) -> int:
    settings.require_credentials()
    api = SpypointClient(settings)
    try:
        inventory = await api.cameras()
        report = {
            "server_version": __version__,
            "authenticated": True,
            "camera_count": len(inventory["cameras"]),
            "camera_discovery_complete": inventory["complete"],
            "warnings": inventory["warnings"],
            "verification": "live_account",
        }
        failed = not inventory["complete"]
        if check_images:
            results = []
            for camera in inventory["cameras"]:
                result = {"camera_id": camera["id"]}
                try:
                    page = await api.photos([camera["id"]], limit=1)
                    if page["photos"]:
                        data, mime = await api.image(page["photos"][0]["id"], "small")
                        result.update(status="ok", bytes=len(data), mime_type=mime)
                    else:
                        result["status"] = "no_photos"
                except SpypointError as exc:
                    result.update(status="error", error=str(exc))
                    failed = True
                results.append(result)
            report["image_checks"] = results
        print(json.dumps(report, indent=2))
        return 1 if failed else 0
    finally:
        await api.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only SPYPOINT MCP server")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "configure", "doctor", "client-config", "sync", "catalog"],
    )
    parser.add_argument("--env-file", type=Path, help="Explicit .env path (never auto-discovered)")
    parser.add_argument(
        "--check-images", action="store_true", help="doctor: check latest image on every camera"
    )
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port on 127.0.0.1 only")
    parser.add_argument("--job-id", help="sync: resume an existing job")
    parser.add_argument("--after", help="sync: inclusive capture-time lower bound with timezone")
    parser.add_argument("--before", help="sync: capture-time upper bound with timezone")
    parser.add_argument(
        "--camera-id", action="append", help="sync: select a camera; repeat as needed"
    )
    parser.add_argument(
        "--download", action="store_true", help="sync: save images as well as metadata"
    )
    parser.add_argument(
        "--page-budget", type=int, default=1, help="sync: upstream pages per invocation (1–20)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    # HTTP debug output can contain signed URLs; never enable it in this process.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        if args.command == "configure":
            configure(args.env_file or Path.cwd() / ".env")
        elif args.command == "client-config":
            path = (args.env_file or Path.cwd() / ".env").expanduser().resolve()
            print(
                json.dumps(
                    {
                        "mcpServers": {
                            "spypoint": {
                                "command": sys.executable,
                                "args": ["-m", "spypoint_mcp", "serve", "--env-file", str(path)],
                            }
                        }
                    },
                    indent=2,
                )
            )
        elif args.command == "doctor":
            sys.exit(asyncio.run(doctor(Settings.load(args.env_file), args.check_images)))
        elif args.command in {"sync", "catalog"}:
            if args.job_id and (args.after or args.before or args.camera_id or args.download):
                raise SpypointError(
                    "A resumed job keeps its original filters; use only --job-id and --page-budget."
                )

            async def operate():
                api = SpypointClient(Settings.load(args.env_file))
                service = PhotoService(api)
                try:
                    if args.command == "catalog":
                        result = await service.catalog.summary()
                    else:
                        engine = SyncEngine(service)
                        job_id = args.job_id
                        if not job_id:
                            job = await engine.start(
                                args.camera_id, args.after, args.before, args.download
                            )
                            job_id = job["id"]
                        result = await engine.run(job_id, args.page_budget)
                    print(json.dumps(result, indent=2))
                    return 2 if result.get("status") in {"needs_attention", "partial"} else 0
                finally:
                    await api.close()

            sys.exit(asyncio.run(operate()))
        else:
            settings = Settings.load(args.env_file)
            server = create_server(settings)
            if args.transport == "stdio":
                server.run(transport="stdio")
            else:
                if not 1 <= args.port <= 65535:
                    raise SpypointError("HTTP port must be between 1 and 65535.")
                import uvicorn

                uvicorn.run(
                    http_app(server, settings.http_token),
                    host="127.0.0.1",
                    port=args.port,
                    access_log=False,
                    log_level="warning",
                )
    except (SpypointError, OSError) as exc:
        message = (
            str(exc)
            if isinstance(exc, SpypointError)
            else "Local file operation failed; check paths and permissions."
        )
        print(message, file=sys.stderr)
        sys.exit(1)
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
