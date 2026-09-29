"""Pinned engine versions reported in `ScanResult.engine_versions` (MASTERSPEC §5)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

APP_VERSION = "0.1.0"

# Kept in lockstep with backend/pyproject.toml and backend/Dockerfile.
PLAYWRIGHT_VERSION = "1.63.0"

# The Sustainable Web Design model version we implement. See app/carbon/constants.py
# for why v3 is pinned rather than CO2.js's own v4 default.
SWD_MODEL_VERSION = "3"

_VENDOR_DIR = Path(__file__).parent / "scanner" / "vendor"


@lru_cache(maxsize=1)
def axe_version() -> str:
    """Read the vendored axe-core version recorded at vendoring time."""
    meta = _VENDOR_DIR / "axe-version.json"
    if not meta.exists():
        return "unvendored"
    try:
        return str(json.loads(meta.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


def engine_versions() -> dict[str, str]:
    return {
        "playwright": PLAYWRIGHT_VERSION,
        "axe": axe_version(),
        "swd_model": SWD_MODEL_VERSION,
    }
