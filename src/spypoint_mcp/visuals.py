"""Bounded local image decoding and labeled contact sheets."""

import io
import math

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
