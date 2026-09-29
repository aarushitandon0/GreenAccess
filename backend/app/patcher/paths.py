"""Where fetched and generated files live inside a patched copy.

Single responsibility: map a URL to a safe, deterministic relative path under
``patched/{scan_id}/``. Paths never contain ``..`` or absolute segments, so a
hostile URL cannot write outside the patch directory.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from typing import Final
from urllib.parse import unquote, urlsplit

__all__ = ["ASSET_DIR", "mirrored_path", "optimized_path", "poster_path", "relative_to"]

ASSET_DIR: Final[str] = "assets"
_SAFE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]+")


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]


def _safe_segments(path: str) -> list[str]:
    segments: list[str] = []
    for raw in unquote(path).split("/"):
        if raw in ("", ".", ".."):
            continue
        cleaned = _SAFE.sub("_", raw)[:100].lstrip(".") or "_"
        segments.append(cleaned)
    return segments


def mirrored_path(url: str) -> str:
    """``assets/<path of the URL>``; a query string adds a hash to the name."""
    parts = urlsplit(url)
    segments = _safe_segments(parts.path) or ["index"]
    if parts.query:
        stem, dot, ext = segments[-1].rpartition(".")
        segments[-1] = f"{stem}-{_digest(url)}.{ext}" if dot else f"{ext}-{_digest(url)}"
    return posixpath.join(ASSET_DIR, *segments)


def optimized_path(url: str) -> str:
    stem = _safe_segments(urlsplit(url).path)[-1:] or ["image"]
    name = stem[0].rsplit(".", 1)[0]
    return posixpath.join(ASSET_DIR, "optimized", f"{name}-{_digest(url)}.webp")


def poster_path(url: str) -> str:
    stem = _safe_segments(urlsplit(url).path)[-1:] or ["video"]
    name = stem[0].rsplit(".", 1)[0]
    return posixpath.join(ASSET_DIR, "optimized", f"{name}-poster-{_digest(url)}.webp")


def relative_to(target: str, from_file: str) -> str:
    """Relative URL from the file at `from_file` to `target` (both patch-relative)."""
    return posixpath.relpath(target, posixpath.dirname(from_file) or ".")
