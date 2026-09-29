"""Process configuration, read once from the environment (MASTERSPEC §17)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: Where runtime files live: screenshots now, patched sites later. Resolved
#: against the backend directory, so it is the same wherever the process is
#: started from; in Docker that is /app/data, the volume docker-compose mounts.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

#: MASTERSPEC §8.2: the disk cache lives in ``backend/.llm_cache/`` (gitignored).
DEFAULT_LLM_CACHE_DIR = Path(__file__).resolve().parent.parent / ".llm_cache"

#: MASTERSPEC §16: the demo run's cache, committed and labelled as cached.
DEFAULT_LLM_FIXTURE_CACHE_DIR = Path(__file__).resolve().parent / "fixtures" / "llm_cache"


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Everything the app reads from the environment.

    Defaults mirror MASTERSPEC §17 so the app runs with an empty environment.
    """

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    llm_offline: bool = True
    allowed_local_hosts: tuple[str, ...] = field(default_factory=tuple)
    max_concurrent_scans: int = 2
    scan_timeout_s: int = 90
    nav_timeout_s: int = 30
    max_page_bytes: int = 26_214_400
    database_url: str = "sqlite:///./greenaccess.db"
    demo_fallback: bool = False
    public_base_url: str = "http://localhost:5173"
    demo_url: str = "http://localhost:8081"
    data_dir: Path = DEFAULT_DATA_DIR
    #: Optional path to a Chromium binary. Only for hosts whose preinstalled
    #: browser build differs from the one the pinned Playwright expects; unset,
    #: Playwright uses its own managed browser.
    chromium_executable: str | None = None
    #: Where the backend serves patched copies (MASTERSPEC §8.3). The re-scan
    #: visits ``{patched_base_url}/{scan_id}/index.html``, so this host must be
    #: on ``ALLOWED_LOCAL_HOSTS`` when it is local.
    patched_base_url: str = "http://localhost:8000/patched"
    #: Writable LLM answer cache (MASTERSPEC §8.2: ``backend/.llm_cache/``).
    llm_cache_dir: Path = DEFAULT_LLM_CACHE_DIR
    #: Read-only cache committed with the demo run (MASTERSPEC §16).
    llm_fixture_cache_dir: Path = DEFAULT_LLM_FIXTURE_CACHE_DIR

    @property
    def screenshot_dir(self) -> Path:
        """Scan screenshots, one sub-directory per scan id."""
        return self.data_dir / "screenshots"

    @property
    def patched_dir(self) -> Path:
        """Patched copies served at ``/patched``, one sub-directory per scan id."""
        return self.data_dir / "patched"

    @property
    def work_dir(self) -> Path:
        """Per-scan fix plans and fetched source, never served."""
        return self.data_dir / "work"

    @property
    def zip_dir(self) -> Path:
        """Downloadable patch archives, outside the statically served tree."""
        return self.data_dir / "zips"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
            # Offline by default: a missing key must never turn into a live call.
            llm_offline=os.environ.get("LLM_OFFLINE", "1") not in {"0", "false", "False"},
            allowed_local_hosts=_csv(os.environ.get("ALLOWED_LOCAL_HOSTS", "")),
            max_concurrent_scans=_int("MAX_CONCURRENT_SCANS", 2),
            scan_timeout_s=_int("SCAN_TIMEOUT_S", 90),
            nav_timeout_s=_int("NAV_TIMEOUT_S", 30),
            max_page_bytes=_int("MAX_PAGE_BYTES", 26_214_400),
            database_url=os.environ.get("DATABASE_URL", "sqlite:///./greenaccess.db"),
            demo_fallback=os.environ.get("DEMO_FALLBACK", "0") not in {"0", "false", "False"},
            public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:5173"),
            demo_url=os.environ.get("DEMO_URL", "http://localhost:8081"),
            chromium_executable=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            patched_base_url=os.environ.get(
                "PATCHED_BASE_URL", "http://localhost:8000/patched"
            ).rstrip("/"),
            llm_cache_dir=Path(os.environ.get("LLM_CACHE_DIR") or DEFAULT_LLM_CACHE_DIR),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Call `get_settings.cache_clear()` in tests that patch env."""
    return Settings.from_env()
