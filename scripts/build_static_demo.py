#!/usr/bin/env python3
"""Build the cached-demo static site that Netlify serves.

Single responsibility: take one *recorded* run of the Daily Herald demo and the
built frontend, and emit a directory that any static host can serve with no
backend at all.

Why this exists
---------------
GreenAccess needs a real Chromium and about 2 GB of RAM to scan anything, and
no free host that takes no credit card offers that. A permanent URL for a
submission therefore cannot run the scanner. What it *can* do is show the real
recorded result of a real run, which is what this builds.

What it emits
-------------
    dist-static/
        index.html            the built UI, plus the cached-data banner
        assets/...            the built UI's bundles, unchanged
        cached-demo.json      the recorded scan, both chapters, labelled
        screenshots/          before.png / after.png, when recorded

The frontend reads ``cached-demo.json`` through ``src/lib/cachedDemo.ts`` when
built with ``VITE_CACHED_DEMO=1``, so no request ever leaves the page.

Honesty (CLAUDE.md rule 4)
--------------------------
Every number here comes from a real recorded scan; nothing is invented. The
recording carries its own label ("Cached run of ... Not live data.") and this
script refuses to build if that label is missing, injects it as a visible
banner, and writes it into the JSON the UI reads. The scan form is inert in
this build because there is no scanner behind it.

Usage:
    python scripts/build_static_demo.py
    python scripts/build_static_demo.py --out dist-static --skip-npm
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
BACKEND: Final = REPO_ROOT / "backend"
FRONTEND: Final = REPO_ROOT / "frontend"
DEFAULT_OUT: Final = REPO_ROOT / "dist-static"

#: Where the recorded screenshot pair lives (see app/fixtures/loader.py).
RECORDED_SHOTS: Final = BACKEND / "app" / "fixtures" / "static_demo" / "screenshots"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("build-static-demo")


def load_recording() -> dict[str, Any]:
    """Load both recorded chapters through the backend's own loader."""
    sys.path.insert(0, str(BACKEND))
    from app.fixtures.loader import load_demo

    demo = load_demo()
    if not demo.label:
        raise SystemExit("the recording has no label; refusing to build (CLAUDE.md rule 4)")

    return {
        "label": demo.label,
        "recorded_at": demo.recorded_at,
        "live": False,
        "fix_count": demo.fix_count,
        "before": demo.before.model_dump(mode="json"),
        "after": demo.after.model_dump(mode="json"),
    }


def build_frontend(skip_npm: bool) -> Path:
    """Run the production Vite build with the cached-demo flag set."""
    dist = FRONTEND / "dist"
    if skip_npm:
        if not dist.is_dir():
            raise SystemExit(f"--skip-npm given but {dist} does not exist")
        logger.info("skipping npm build, reusing %s", dist)
        return dist

    logger.info("building the frontend with VITE_CACHED_DEMO=1 ...")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("npm not found on PATH")
    subprocess.run(
        [npm, "run", "build"],
        cwd=FRONTEND,
        check=True,
        env={**_env(), "VITE_CACHED_DEMO": "1"},
    )
    return dist


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)


BANNER_ID: Final = "ga-cached-banner"


def banner_html(label: str) -> str:
    """The visible 'this is cached' notice.

    Deliberately inline and self-contained: it is injected into the built
    ``index.html`` rather than added to the component tree, so the hand-built UI
    under ``frontend/src`` is untouched by this build.
    """
    return (
        f'<div id="{BANNER_ID}" role="note" style="'
        "position:fixed;inset:0 0 auto 0;z-index:2147483647;"
        "background:#2b2000;color:#ffd978;border-bottom:1px solid #6b5200;"
        "font:500 13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;"
        "padding:8px 16px;text-align:center;letter-spacing:.01em;"
        '">'
        "<strong>Cached demo &mdash; not a live scan.</strong> "
        f"{label} "
        "Scanning your own URL needs the full app; see the repository."
        "</div>"
        f'<style>body{{padding-top:38px}}#{BANNER_ID}+*{{scroll-margin-top:38px}}</style>'
    )


def inject_banner(index: Path, label: str) -> None:
    """Put the notice directly after <body> in the built index.html."""
    html = index.read_text(encoding="utf-8")
    if BANNER_ID in html:
        logger.info("banner already present, leaving it alone")
        return
    marker = "<body>"
    if marker not in html:
        raise SystemExit("built index.html has no <body> to inject the banner into")
    html = html.replace(marker, marker + "\n" + banner_html(label), 1)
    index.write_text(html, encoding="utf-8")
    logger.info("injected the cached-data banner")


def copy_screenshots(out: Path) -> int:
    """Copy an exported before/after screenshot pair, if one was recorded."""
    if not RECORDED_SHOTS.is_dir():
        return 0
    target = out / "screenshots"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in ("before.png", "after.png"):
        source = RECORDED_SHOTS / name
        if source.is_file():
            shutil.copy2(source, target / name)
            copied += 1
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--skip-npm",
        action="store_true",
        help="reuse frontend/dist instead of rebuilding it",
    )
    args = parser.parse_args()

    recording = load_recording()
    logger.info("recording: %s", recording["label"])
    logger.info("  %d fixes recorded", recording["fix_count"])

    dist = build_frontend(args.skip_npm)

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(dist, out)
    logger.info("copied the built UI to %s", out)

    (out / "cached-demo.json").write_text(
        json.dumps(recording, indent=2), encoding="utf-8"
    )
    logger.info("wrote cached-demo.json")

    shots = copy_screenshots(out)
    logger.info("screenshots copied: %d", shots)
    if shots == 0:
        logger.warning(
            "no screenshots in %s - the before/after imagery will be absent",
            RECORDED_SHOTS,
        )

    inject_banner(out / "index.html", recording["label"])

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    logger.info("\n%s is ready: %d files, %.1f MB", out, len(list(out.rglob("*"))), total / 1e6)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
