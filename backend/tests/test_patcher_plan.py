"""Round-trip guarantees for the fix plan (``app.patcher.plan``).

The plan carries fixes from ``POST /fixes`` to ``POST /patch`` alongside the
exact page source they were written against, and refuses to apply them if that
source has changed. The integrity check is the point, so what these tests pin
is that it fires when the page really differs and *only* then.

The newline case is a regression test. Writing the source in text mode
translates newlines on the way out and again on the way in, and the two are not
inverses: a page containing CRLF was written as ``\\r\\r\\n`` and read back as
``\\n\\n``. Every patch on Windows then failed with "the saved page source does
not match its fix plan", while the same code passed on Linux, where the
translation is a no-op.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.models import Fix, FixDiff
from app.patcher.plan import FixPlan, PlannedFix, fix_id, load_plan, save_plan


def _plan(source: str) -> FixPlan:
    return FixPlan(
        scan_id="scan-1",
        page_url="http://example.com/",
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        fixes=[
            PlannedFix(
                fix=Fix(
                    id=fix_id("img_alt", "img#hero"),
                    kind="img_alt",
                    target="img#hero",
                    description="Add alternative text",
                    diff=FixDiff(before="<img>", after='<img alt="A street at night">'),
                    ai_generated=True,
                    confidence=0.8,
                ),
                params={"alt": "A street at night"},
            )
        ],
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("unix newlines", "<html>\n<body>\n<p>one</p>\n</body>\n</html>\n"),
        ("windows newlines", "<html>\r\n<body>\r\n<p>one</p>\r\n</body>\r\n</html>\r\n"),
        ("mixed newlines", "<html>\r\n<body>\n<p>one</p>\r\n</body>\n</html>"),
        ("bare carriage returns", "<html>\r<body>\r<p>one</p>\r</html>"),
        ("no trailing newline", "<html><body><p>one</p></body></html>"),
        # Multi-byte text, to prove the digest is over decoded characters and
        # not over whatever the platform's default codec would have produced.
        ("non-ascii", "<html>\r\n<p>café naïve 中文 \U0001f331</p>\r\n</html>"),
    ],
)
def test_source_survives_the_round_trip(tmp_path: Path, label: str, source: str) -> None:
    """The source read back is byte-for-byte what was saved, newlines included."""
    save_plan(tmp_path, _plan(source), source)

    loaded = load_plan(tmp_path)

    assert loaded is not None, f"{label}: the plan should load"
    plan, restored = loaded
    assert restored == source, f"{label}: the source was altered by the round trip"
    assert plan.scan_id == "scan-1"
    assert plan.fixes[0].params == {"alt": "A street at night"}


def test_missing_plan_is_not_an_error(tmp_path: Path) -> None:
    """A scan whose fixes were never generated simply has no plan."""
    assert load_plan(tmp_path) is None


def test_a_changed_page_is_refused(tmp_path: Path) -> None:
    """The integrity check still fires when the source genuinely differs.

    The round-trip fix must not have been made by weakening this: applying fixes
    to HTML they were not written against is how a patcher corrupts a page.
    """
    source = "<html>\r\n<body>\r\n<p>one</p>\r\n</body>\r\n</html>\r\n"
    save_plan(tmp_path, _plan(source), source)

    # Something else rewrote the page after the fixes were generated.
    (tmp_path / "source.html").write_bytes(b"<html><body><p>two</p></body></html>")

    with pytest.raises(ValueError, match="does not match its fix plan"):
        load_plan(tmp_path)


def test_fix_id_is_stable(tmp_path: Path) -> None:
    """The same finding keeps its id, so a regenerated plan keeps selections."""
    assert fix_id("img_alt", "img#hero") == fix_id("img_alt", "img#hero")
    assert fix_id("img_alt", "img#hero") != fix_id("img_alt", "img#other")
    assert fix_id("img_alt", "img#hero") != fix_id("lazy_load", "img#hero")
