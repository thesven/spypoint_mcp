"""Portable catalog exports with content hashes and spreadsheet-safe CSV cells."""

import csv
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from .config import SpypointError
from .service import PhotoService


def csv_cell(value) -> str:
    text = "" if value is None else str(value)
    # Leading whitespace/control characters can conceal spreadsheet formulas.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def export(window: dict, root: Path, format: str) -> dict:
    if format not in {"csv", "json"}:
        raise SpypointError("Export format must be csv or json.")
    if format == "json":
        data = json.dumps(window, indent=2, ensure_ascii=False).encode()
    else:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        keys = (
            "id",
            "camera_id",
            "date",
            "captured_at",
            "uploaded_at",
            "filename",
            "favorite",
            "tags",
        )
        writer.writerow(keys)
        for photo in window["photos"]:
            writer.writerow(
                [
                    csv_cell(
                        json.dumps(photo.get(key, []), ensure_ascii=False)
                        if key == "tags"
                        else photo.get(key)
                    )
                    for key in keys
                ]
            )
        data = buffer.getvalue().encode("utf-8-sig")
    export_id = str(uuid4())
    path = root / f"catalog-{export_id}.{format}"
    digest = hashlib.sha256(data).hexdigest()
    manifest = {
        "format": format,
        "rows": len(window["photos"]),
        "sha256": digest,
        "bytes": len(data),
        "truncated": window["truncated"],
        "scope": "local_catalog",
        "queried_at": window["queried_at"],
        "after": window["after"],
        "before": window["before"],
        "data_file": path.name,
        "note": "Export includes indexed metadata only, never signed URLs or credentials.",
    }
    PhotoService._save(root, path, data)
    manifest_path = root / f"catalog-{export_id}.manifest.json"
    try:
        PhotoService._save(root, manifest_path, json.dumps(manifest, indent=2).encode())
    except SpypointError:
        # No successful export is reported without its coverage/hash manifest.
        path.unlink(missing_ok=True)
        raise
    return {**manifest, "path": str(path), "manifest_path": str(manifest_path)}
