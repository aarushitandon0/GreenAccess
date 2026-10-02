"""Load the recorded demo scans that back ``DEMO_FALLBACK``.

Single responsibility: turn the JSON recorded by
``tests/test_patch_integration.py`` under ``GA_RECORD_DEMO=1`` into validated
:class:`~app.models.Scan` objects, and carry the label that says they are not
live data.

Two files are recorded, because the demo has two chapters and the UI asks for
them at different moments:

    demo_scan_before.json   the first scan: ``before`` filled, no patch
    demo_scan_after.json    after the fix loop: ``before``, ``after``, ``patch``

They are the same scan at two points in time, so the ids differ between the
recordings and this module re-stamps both onto one id (:data:`DEMO_SCAN_ID`).
A caller that serves them as one scan would otherwise hand the browser two
different ids for one story.

CLAUDE.md rule 4: these are real measurements of a real run, never invented
numbers, and every consumer must present them as cached rather than live. The
label travels with the data so that cannot be forgotten -- see :attr:`label`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from app.models import Scan

__all__ = [
    "DEMO_SCAN_ID",
    "RecordedDemo",
    "load_demo",
]

FIXTURE_DIR: Final = Path(__file__).resolve().parent

#: The one id both chapters are served under. Stable so a reload keeps working.
DEMO_SCAN_ID: Final = "demo"

_BEFORE_FILE: Final = "demo_scan_before.json"
_AFTER_FILE: Final = "demo_scan_after.json"

#: A single-file recording of one complete run, used in preference to the pair
#: above when it exists.
#:
#: The pair is written by ``tests/test_patch_integration.py`` and is what the
#: integration test asserts against, so it is left alone. It also has its
#: screenshots stripped (see that test), which a deployment wanting to *show*
#: the run cannot use. This recording is captured from a running instance
#: instead, keeps its screenshots beside it, and holds both chapters in one
#: ``scan`` object, so its numbers and its images are from the same run.
_SINGLE_DIR: Final = FIXTURE_DIR / "static_demo"
_SINGLE_FILE: Final = _SINGLE_DIR / "recording.json"
#: Screenshots belonging to :data:`_SINGLE_FILE`, named ``before``/``after``.
SCREENSHOT_DIR: Final = _SINGLE_DIR / "screenshots"


@dataclass(frozen=True)
class RecordedDemo:
    """One recorded run of the demo, in its two chapters."""

    #: The first scan: ``before`` only, as the UI sees it before any fixing.
    before: Scan
    #: The same scan after the fix loop: ``before``, ``after`` and ``patch``.
    after: Scan
    #: The recorded wording, e.g. "Cached run of 2026-09-29 ... Not live data."
    label: str
    #: ISO date the run was recorded.
    recorded_at: str

    @property
    def fix_count(self) -> int:
        """How many fixes the recorded run applied."""
        return len(self.after.patch.fixes) if self.after.patch else 0


def _read(name: str) -> dict[str, Any]:
    path = FIXTURE_DIR / name
    if not path.is_file():
        raise FileNotFoundError(
            f"demo fixture {name} is missing from {FIXTURE_DIR}. "
            "Record it with `make demo-record`."
        )
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


def _scan_with_demo_id(payload: dict[str, Any]) -> Scan:
    """Validate the recorded ``scan`` object onto the shared demo id."""
    raw = dict(payload["scan"])
    raw["id"] = DEMO_SCAN_ID
    return Scan.model_validate(raw)


def _load_single() -> RecordedDemo:
    """Read the one-file recording, splitting it into the two chapters.

    The file holds the finished run, so the "before" chapter is the same scan
    with its ``after`` and ``patch`` removed -- which is exactly what the API
    returned at that point in time.
    """
    with _SINGLE_FILE.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)

    label = str(payload.get("_label", "")).strip()
    if not label:
        raise ValueError(
            f"{_SINGLE_FILE.name} has no `_label`. CLAUDE.md rule 4 requires "
            "cached data to be labelled; refusing to serve it unlabelled."
        )

    finished = dict(payload["scan"])
    first = dict(finished)
    first["after"] = None
    first["patch"] = None

    return RecordedDemo(
        before=_scan_with_demo_id({"scan": first}),
        after=_scan_with_demo_id({"scan": finished}),
        label=label,
        recorded_at=str(payload.get("_recorded_at", "")),
    )


@lru_cache(maxsize=1)
def load_demo() -> RecordedDemo:
    """Read the recording. Cached: the files never change while serving."""
    if _SINGLE_FILE.is_file():
        return _load_single()

    before_payload = _read(_BEFORE_FILE)
    after_payload = _read(_AFTER_FILE)

    label = str(before_payload.get("_label", "")).strip()
    if not label:
        raise ValueError(
            f"{_BEFORE_FILE} has no `_label`. CLAUDE.md rule 4 requires cached "
            "data to be labelled as such; refusing to serve it unlabelled."
        )

    return RecordedDemo(
        before=_scan_with_demo_id(before_payload),
        after=_scan_with_demo_id(after_payload),
        label=label,
        recorded_at=str(before_payload.get("_recorded_at", "")),
    )
