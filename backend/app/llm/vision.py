"""Prepare an image for a vision call (MASTERSPEC §8.2: downscale to ≤ 768 px).

Single responsibility: decode an image, shrink it so its longest side is at
most 768 px, and re-encode it deterministically, so the same source bytes
always hash to the same cache key.
"""

from __future__ import annotations

import hashlib
import io
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

from app.llm.client import PreparedImage

__all__ = ["MAX_VISION_SIDE_PX", "VisionImageError", "prepare_image"]

#: MASTERSPEC §8.2.
MAX_VISION_SIDE_PX: Final[int] = 768
#: WebP keeps baked-in text legible at a fraction of PNG's size.
_QUALITY: Final[int] = 85
#: Decompression-bomb guard: refuse anything above ~50 megapixels.
_MAX_PIXELS: Final[int] = 50_000_000


class VisionImageError(ValueError):
    """The bytes are not an image we can send."""


def prepare_image(data: bytes) -> PreparedImage:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.width * opened.height > _MAX_PIXELS:
                raise VisionImageError(f"image too large: {opened.width}x{opened.height}")
            opened.seek(0)  # first frame of an animation
            image = ImageOps.exif_transpose(opened)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise VisionImageError(f"not a decodable image: {exc}") from exc

    image.thumbnail((MAX_VISION_SIDE_PX, MAX_VISION_SIDE_PX), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=_QUALITY, method=6)
    encoded = buffer.getvalue()
    return PreparedImage(
        media_type="image/webp",
        data=encoded,
        sha256=hashlib.sha256(encoded).hexdigest(),
        width=image.width,
        height=image.height,
    )
