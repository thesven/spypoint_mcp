"""Bounded local image decoding, ROI cropping, burst strips, and labeled contact sheets."""

import io
import math
from datetime import UTC, datetime
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .config import SpypointError

MAX_PIXELS = 24_000_000


def decode(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise SpypointError(
                    "Image exceeds the decoded pixel budget; choose a smaller size."
                )
            image.load()
            return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise SpypointError(
            "Image could not be safely decoded; choose another photo or size."
        ) from None


def resize(data: bytes, max_edge: int = 1600) -> tuple[bytes, dict]:
    if not 256 <= max_edge <= 4096:
        raise SpypointError("max_edge must be between 256 and 4096 pixels.")
    image = decode(data)
    original = image.size
    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue(), {
        "original_width": original[0],
        "original_height": original[1],
        "width": image.width,
        "height": image.height,
        "transformed": True,
        "format": "JPEG",
        "exif_removed": True,
    }


def crop(
    data: bytes,
    box: tuple[float, float, float, float],
    max_edge: int = 1600,
) -> tuple[bytes, dict]:
    if not 256 <= max_edge <= 4096:
        raise SpypointError("max_edge must be between 256 and 4096 pixels.")
    if len(box) != 4 or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box
    ):
        raise SpypointError("Crop box requires four finite normalized coordinates.")
    x_min, y_min, x_max, y_max = (float(v) for v in box)
    if not (0.0 <= x_min < x_max <= 1.0 and 0.0 <= y_min < y_max <= 1.0):
        raise SpypointError(
            "Crop coordinates must satisfy 0.0 <= x_min < x_max <= 1.0 and "
            "0.0 <= y_min < y_max <= 1.0."
        )
    image = decode(data)
    orig_w, orig_h = image.size
    left = round(x_min * orig_w)
    top = round(y_min * orig_h)
    right = round(x_max * orig_w)
    bottom = round(y_max * orig_h)
    if right - left < 32 or bottom - top < 32:
        raise SpypointError("Crop region must be at least 32x32 pixels.")
    cropped = image.crop((left, top, right, bottom))
    cropped.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    cropped.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue(), {
        "original_width": orig_w,
        "original_height": orig_h,
        "crop_box": [round(x_min, 4), round(y_min, 4), round(x_max, 4), round(y_max, 4)],
        "pixel_box": [left, top, right, bottom],
        "width": cropped.width,
        "height": cropped.height,
        "transformed": True,
        "format": "JPEG",
        "exif_removed": True,
    }


def _parse_capture_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def burst_strip(
    tiles: list[tuple[dict, bytes | None]],
    direction: Literal["horizontal", "vertical"] = "horizontal",
    tile_height: int = 360,
) -> tuple[bytes, dict]:
    if (
        not 2 <= len(tiles) <= 8
        or direction not in {"horizontal", "vertical"}
        or not 240 <= tile_height <= 720
    ):
        raise SpypointError(
            "Use 2–8 photos, direction horizontal or vertical, and tile_height between 240 and 720."
        )
    tile_width = tile_height * 4 // 3
    label_height, gap = 44, 10
    count = len(tiles)
    if direction == "horizontal":
        width = gap + count * (tile_width + gap)
        height = gap + tile_height + label_height + gap
    else:
        width = gap + tile_width + gap
        height = gap + count * (tile_height + label_height + gap)
    canvas = Image.new("RGB", (width, height), "#eff1f3")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=14)
    dts = [_parse_capture_dt(meta.get("date")) for meta, _ in tiles]
    valid_dts = [dt for dt in dts if dt is not None]
    base_dt = min(valid_dts) if valid_dts else None
    span_seconds = (
        round((max(valid_dts) - min(valid_dts)).total_seconds(), 2) if len(valid_dts) >= 2 else 0.0
    )
    frames = []
    success = 0
    for index, ((metadata, data), dt) in enumerate(zip(tiles, dts, strict=True)):
        if direction == "horizontal":
            x = gap + index * (tile_width + gap)
            y = gap
        else:
            x = gap
            y = gap + index * (tile_height + label_height + gap)
        draw.rectangle((x, y, x + tile_width, y + tile_height), fill="#20262c")
        delta = round((dt - base_dt).total_seconds(), 1) if dt and base_dt else None
        frame = {"frame": index + 1, "delta_seconds": delta, **metadata}
        if data is not None:
            try:
                image = decode(data)
                image.thumbnail((tile_width, tile_height), Image.Resampling.LANCZOS)
                canvas.paste(
                    image,
                    (x + (tile_width - image.width) // 2, y + (tile_height - image.height) // 2),
                )
                success += 1
            except SpypointError as exc:
                frame["error"] = str(exc)
        if data is None or frame.get("error"):
            draw.text((x + 16, y + 24), "Image unavailable", fill="white", font=font)
        delta_text = f"T+{delta:.1f}s" if delta is not None else "T+?"
        stamp = (
            str(metadata.get("date") or "Unknown capture time")[:24]
            .encode("ascii", "replace")
            .decode()
        )
        draw.text(
            (x, y + tile_height + 5),
            f"#{index + 1:02d} {delta_text}  {stamp}",
            fill="#17212b",
            font=font,
        )
        frames.append(frame)
    if not success:
        raise SpypointError("No requested photos could be decoded; refresh the listings and retry.")
    output = io.BytesIO()
    canvas.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue(), {
        "frames": frames,
        "direction": direction,
        "width": width,
        "height": height,
        "successful_images": success,
        "span_seconds": span_seconds,
        "transformed": True,
        "note": (
            "Chronological burst strip; frame numbers and T+ deltas map to photo IDs in frames."
        ),
    }


def contact_sheet(
    tiles: list[tuple[dict, bytes | None]], columns: int = 3, tile_width: int = 480
) -> tuple[bytes, dict]:
    if not 1 <= len(tiles) <= 12 or not 1 <= columns <= 4 or not 256 <= tile_width <= 768:
        raise SpypointError("Use 1–12 photos, 1–4 columns, and tile_width between 256 and 768.")
    columns = min(columns, len(tiles))
    tile_height = tile_width * 3 // 4
    label_height, gap = 44, 12
    width = gap + columns * (tile_width + gap)
    height = gap + math.ceil(len(tiles) / columns) * (tile_height + label_height + gap)
    canvas = Image.new("RGB", (width, height), "#eff1f3")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=14)
    labels = []
    success = 0
    for index, (metadata, data) in enumerate(tiles):
        x = gap + (index % columns) * (tile_width + gap)
        y = gap + (index // columns) * (tile_height + label_height + gap)
        draw.rectangle((x, y, x + tile_width, y + tile_height), fill="#20262c")
        label = {"tile": index + 1, **metadata}
        if data is not None:
            try:
                image = decode(data)
                image.thumbnail((tile_width, tile_height), Image.Resampling.LANCZOS)
                canvas.paste(
                    image,
                    (x + (tile_width - image.width) // 2, y + (tile_height - image.height) // 2),
                )
                success += 1
            except SpypointError as exc:
                label["error"] = str(exc)
        if data is None or label.get("error"):
            draw.text((x + 16, y + 24), "Image unavailable", fill="white", font=font)
        # Only generated indices and bounded timestamp text go into the raster label.
        stamp = (
            str(metadata.get("date") or "Unknown capture time")[:32]
            .encode("ascii", "replace")
            .decode()
        )
        draw.text((x, y + tile_height + 5), f"{index + 1:02d}  {stamp}", fill="#17212b", font=font)
        labels.append(label)
    if not success:
        raise SpypointError("No requested photos could be decoded; refresh the listings and retry.")
    output = io.BytesIO()
    canvas.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue(), {
        "tiles": labels,
        "width": width,
        "height": height,
        "successful_images": success,
        "transformed": True,
        "note": "Tile numbers map to photo and camera IDs in this metadata.",
    }
