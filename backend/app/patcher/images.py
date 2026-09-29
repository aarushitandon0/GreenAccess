"""Image optimisation for the patched copy (MASTERSPEC §8.1 kinds 8 and 10).

Single responsibility: turn original image bytes into a smaller WebP sized for
how the page actually renders it, and (when ffmpeg is present) a video poster.

Rules, from §8.1 kind 10:

* Width = 2 x rendered CSS width (one DPR-2 allowance), never upscaled.
* WebP at quality 78.
* Never degrade: if the result is not smaller than the original, the original
  is kept and the fix is reported as skipped.
* Animated images are left alone: a still WebP would drop the animation.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

__all__ = [
    "DPR_ALLOWANCE",
    "WEBP_QUALITY",
    "ImageSkipped",
    "OptimizedImage",
    "make_poster",
    "optimize_image",
    "target_width",
]

#: MASTERSPEC §8.1 kind 10.
WEBP_QUALITY: Final[int] = 78
DPR_ALLOWANCE: Final[int] = 2
_MAX_PIXELS: Final[int] = 60_000_000
_POSTER_TIMEOUT_S: Final[float] = 20.0
_POSTER_QUALITY: Final[int] = 70


class ImageSkipped(Exception):
    """This image cannot or should not be optimised; the message says why."""


@dataclass(frozen=True)
class OptimizedImage:
    data: bytes
    width: int
    height: int
    original_bytes: int
    original_width: int
    original_height: int

    @property
    def saved_bytes(self) -> int:
        return self.original_bytes - len(self.data)


def target_width(natural_w: int, rendered_w: int) -> int:
    """2 x rendered width, capped at the natural width (never upscale)."""
    if rendered_w <= 0:
        return natural_w
    return max(1, min(natural_w, rendered_w * DPR_ALLOWANCE))


def optimize_image(data: bytes, *, rendered_w: int, quality: int = WEBP_QUALITY) -> OptimizedImage:
    """Resize and re-encode `data`; raises ImageSkipped instead of degrading."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.width * opened.height > _MAX_PIXELS:
                raise ImageSkipped(f"image too large to process ({opened.width}x{opened.height})")
            if getattr(opened, "is_animated", False) and getattr(opened, "n_frames", 1) > 1:
                raise ImageSkipped("animated image; a still WebP would drop the animation")
            image = ImageOps.exif_transpose(opened)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageSkipped(f"not a raster image Pillow can read: {exc}") from exc

    original_w, original_h = image.width, image.height
    width = target_width(original_w, rendered_w)
    if width < original_w:
        height = max(1, round(original_h * width / original_w))
        image = image.resize((width, height), Image.Resampling.LANCZOS)

    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    image = image.convert("RGBA" if has_alpha else "RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=quality, method=6)
    encoded = buffer.getvalue()
    if len(encoded) >= len(data):
        raise ImageSkipped(
            f"WebP would be {len(encoded):,} bytes, not smaller than the original {len(data):,}"
        )
    return OptimizedImage(
        data=encoded,
        width=image.width,
        height=image.height,
        original_bytes=len(data),
        original_width=original_w,
        original_height=original_h,
    )


def make_poster(video: bytes, *, width: int = 1600) -> bytes | None:
    """A WebP of the video's first frame, or None when ffmpeg is unavailable.

    MASTERSPEC §8.1 kind 8: "generate poster from first frame if possible
    (else skip poster)". ffmpeg is optional; without it there is no poster.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "in.bin"
        frame = Path(tmp) / "frame.png"
        source.write_bytes(video)
        try:
            subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(source), "-frames:v", "1", str(frame)],
                check=True,
                timeout=_POSTER_TIMEOUT_S,
                capture_output=True,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.info("poster extraction failed: %s", exc)
            return None
        try:
            with Image.open(frame) as still:
                still.load()
                image = still.convert("RGB")
        except (UnidentifiedImageError, OSError):
            return None
    if image.width > width:
        image = image.resize(
            (width, max(1, round(image.height * width / image.width))), Image.Resampling.LANCZOS
        )
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=_POSTER_QUALITY, method=6)
    return buffer.getvalue()
