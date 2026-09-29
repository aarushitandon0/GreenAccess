#!/usr/bin/env python3
"""Remove build and cache artefacts. Generated demo assets are kept.

Run `python scripts/make_demo_assets.py --force` to rebuild those separately;
they are slow to produce and rarely the thing you want to throw away.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DIRECTORIES = (
    "backend/.pytest_cache",
    "backend/.ruff_cache",
    "backend/htmlcov",
    "frontend/dist",
    "frontend/.vite",
    "frontend/coverage",
)


def main() -> int:
    for relative in DIRECTORIES:
        path = REPO_ROOT / relative
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            print(f"removed {relative}")

    removed = 0
    for pycache in REPO_ROOT.rglob("__pycache__"):
        if ".venv" in pycache.parts or "node_modules" in pycache.parts:
            continue
        shutil.rmtree(pycache, ignore_errors=True)
        removed += 1
    if removed:
        print(f"removed {removed} __pycache__ directories")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
