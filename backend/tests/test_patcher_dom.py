"""Source-HTML plumbing: parsing fidelity, selector subset, locating findings."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.patcher import dom

DEMO_HTML = (Path(__file__).resolve().parents[2] / "demo-site" / "index.html").read_text()
BASE = "http://localhost:8081/"


@pytest.fixture(scope="module")
def demo():
    return dom.parse_document(DEMO_HTML)


def test_doctype_is_preserved_exactly_or_left_absent() -> None:
    with_doctype = dom.parse_document("<!doctype html><html><body><p>x</p></body></html>")
    assert dom.serialise_document(with_doctype).startswith("<!doctype html>\n<html>")
    without = dom.parse_document("<html><body><p>x</p></body></html>")
    # lxml would invent an HTML 4.0 doctype and change the rendering mode.
    assert "DOCTYPE" not in dom.serialise_document(without).upper()


def test_html5_void_elements_are_not_nested_or_closed() -> None:
    tree = dom.parse_document(
        "<html><body><picture><source srcset='/a.avif'><img id=pic src='/a.jpg'></picture>"
        "<video><source src=a.mp4><track kind=captions src=c.vtt>Fallback</video></body></html>"
    )
    assert dom.select(tree, "#pic")[0].getparent().tag == "picture"
    out = dom.serialise_document(tree)
    assert "</source>" not in out and "</track>" not in out
    assert "Fallback</video>" in out


def test_round_trip_keeps_the_demo_page_intact(demo) -> None:
    out = dom.serialise_document(demo)
    assert out.startswith("<!doctype html>")
    again = dom.parse_document(out)
    assert len(list(dom.iter_elements(again.getroot()))) == len(
        list(dom.iter_elements(demo.getroot()))
    )


@pytest.mark.parametrize(
    ("selector", "count"),
    [
        (".card__image", 8),
        ("div.card > img", 8),
        (".columns .card__title a", 8),
        ("#terms-box", 1),
        ('input[name="email"]', 1),
        ("input[name=email]", 1),
        ("img[alt]", 3),
        ("html > body > div:nth-of-type(2) > video", 1),
        (".ranked > p:first-child", 1),
        (".ranked > p:last-child", 1),
        (".pager__btn:nth-child(2)", 1),
        ("h1, h4.promo__title", 2),
    ],
)
def test_selector_subset(demo, selector: str, count: int) -> None:
    assert len(dom.select(demo, selector)) == count


@pytest.mark.parametrize("selector", [".a:not(.b)", "a ~ b", "a + b", "[x^=y]", ""])
def test_unsupported_selectors_raise(demo, selector: str) -> None:
    with pytest.raises(dom.SelectorError):
        dom.select(demo, selector)


def test_locate_by_axe_snippet_when_selector_uses_script_added_attributes(demo) -> None:
    el = dom.locate(
        demo,
        snippet='<img class="card__image" src="/assets/generated/article-04.jpg" loading="eager">',
        selector='div[data-card-index="3"] > .card__image[loading="eager"]',
        base_url=BASE,
    )
    assert el is not None and el.get("src").endswith("article-04.jpg")


def test_locate_by_src_resolves_relative_urls(demo) -> None:
    el = dom.locate(
        demo, src="http://localhost:8081/assets/generated/banner-subscribe.png", base_url=BASE
    )
    assert el is not None and el.tag == "img"


def test_locate_html_and_structural_selector(demo) -> None:
    assert dom.locate(demo, snippet="<html>", selector="html") is demo.getroot()
    video = dom.locate(demo, selector="html > body > div:nth-of-type(2) > video")
    assert video is not None and video.tag == "video"


def test_locate_disambiguates_with_the_selector(demo) -> None:
    footer = dom.locate(demo, snippet='<p class="fine-print">', selector=".fine-print:nth-child(1)")
    assert footer is not None and "All rights reserved" in dom.visible_text(footer)


def test_ambiguous_findings_are_not_guessed(demo) -> None:
    """Eight identical <p class="card__meta"> and a selector only the live DOM matches."""
    assert (
        dom.locate(
            demo,
            snippet='<p class="card__meta">Culture</p>',
            selector='div[data-card-index="9"] > .card__meta',
        )
        is None
    )


def test_element_path_round_trips(demo) -> None:
    for el in dom.select(demo, "img, input, h4"):
        assert dom.element_at(demo, dom.element_path(el)) is el


def test_opening_tag_and_outer_html(demo) -> None:
    button = dom.select(demo, ".chat__close")[0]
    assert dom.opening_tag(button) == '<button class="chat__close" onclick="closeChat()">'
    assert dom.outer_html(button).endswith("</button>")
    assert len(dom.outer_html(demo.getroot(), limit=100)) == 100


def test_normalise_url() -> None:
    assert dom.normalise_url("/a.jpg#x", "http://Example.com:80/p/") == "http://example.com/a.jpg"
    assert dom.normalise_url("b.png", "http://h:8081/dir/") == "http://h:8081/dir/b.png"
