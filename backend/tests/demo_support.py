"""Shared helpers for the tests that need the Daily Herald demo running."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPO_ROOT / "demo-site"
GENERATED = DEMO_ROOT / "assets" / "generated"
DEMO_PORT = 8081
TRACKER_PORT = 8082


def load_server_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_server_it", DEMO_ROOT / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assets_present() -> bool:
    return (GENERATED / "hero.mp4").exists() and (GENERATED / "article-01.jpg").exists()


def chromium_available() -> bool:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            )
            browser.close()
        return True
    except PlaywrightError:
        return False
