"""Patcher transforms on HTML fixtures (MASTERSPEC §8.1 kinds 1-13, §15)."""

from __future__ import annotations

from typing import Any

import pytest

from app.models import Fix
from app.patcher import dom
from app.patcher.allowlist import load_allowlist
from app.patcher.plan import PlannedFix
from app.patcher.transforms import (
    INTRINSIC_SIZE_ID,
    REDUCED_MOTION_CSS,
    OptimizedAsset,
    PatchContext,
    SkipFix,
    apply_fix,
    contrast_css,
    dark_mode_css,
)

DEMO = "http://localhost:8081/"

PAGE = """<!DOCTYPE html>
<html><head><title>t</title>
<script src="http://localhost:8082/t/analytics.js"></script>
<script src="https://cdn.example.com/lib.js"></script>
</head><body>
<div class="wrap">
  <h1>Title</h1><h4 id="sub">Sub</h4>
  <div class="card"><h4 class="t">Card</h4>
    <img id="photo" src="/assets/generated/article-01.jpg" loading="eager">
  </div>
  <picture><source srcset="/a.avif"><img id="pic" src="/a.jpg"></picture>
  <img id="banner" src="/assets/generated/banner-sale.jpg" alt="old">
  <img id="other-banner" src="/elsewhere/banner.png">
  <video id="v" src="/v.mp4" autoplay loop muted preload="auto"></video>
  <form><input type="text" name="email" class="in">
    <div><input type="checkbox" id="terms"><span class="x">I agree</span></div>
    <div><input type="checkbox" name="news"><span>Send news</span></div>
  </form>
  <a href="#search" class="icon"><span></span></a>
  <button class="chat__close"><span></span></button>
</div></body></html>"""


def tree_and_ctx(page_url: str = DEMO) -> tuple[Any, PatchContext]:
    return dom.parse_document(PAGE), PatchContext(page_url=page_url, allowlist=load_allowlist())


def planned(kind: str, el: Any = None, **params: Any) -> PlannedFix:
    return PlannedFix(
        fix=Fix(id=f"{kind}-1", kind=kind, target="t", description="d"),
        path=list(dom.element_path(el)) if el is not None else None,
        params=params,
    )


def by_id(tree: Any, ident: str) -> Any:
    return dom.select(tree, f"#{ident}")[0]


def html(tree: Any) -> str:
    return dom.serialise_document(tree)


# --------------------------------------------------------------------------- #
# Accessibility kinds
# --------------------------------------------------------------------------- #


def test_img_alt_sets_text_and_decorative_empty() -> None:
    tree, ctx = tree_and_ctx()
    photo = by_id(tree, "photo")
    apply_fix(planned("img_alt", photo, alt='Harbour "at" dusk <b>'), photo, tree, ctx)
    assert photo.get("alt") == 'Harbour "at" dusk <b>'
    assert "&lt;b&gt;" in html(tree) or "<b>" not in html(tree).split("photo")[1][:80]
    apply_fix(planned("img_alt", photo, alt=""), photo, tree, ctx)
    assert photo.get("alt") == ""


def test_form_label_aria_label() -> None:
    tree, ctx = tree_and_ctx()
    email = dom.select(tree, "input.in")[0]
    apply_fix(
        planned("form_label", email, mode="aria-label", text="Email address"), email, tree, ctx
    )
    assert email.get("aria-label") == "Email address"


def test_form_label_converts_adjacent_text_into_a_label() -> None:
    tree, ctx = tree_and_ctx()
    box = by_id(tree, "terms")
    span = box.getnext()
    fix = planned(
        "form_label", box, mode="label-for", text="I agree", label_path=list(dom.element_path(span))
    )
    apply_fix(fix, box, tree, ctx)
    assert span.tag == "label" and span.get("for") == "terms"
    assert span.get("class") == "x"  # styling hooks kept


def test_form_label_generates_an_id_when_missing() -> None:
    tree, ctx = tree_and_ctx()
    box = dom.select(tree, 'input[name="news"]')[0]
    span = box.getnext()
    fix = planned(
        "form_label",
        box,
        mode="label-for",
        text="Send news",
        label_path=list(dom.element_path(span)),
    )
    apply_fix(fix, box, tree, ctx)
    assert box.get("id") == "ga-form_label-1" and span.get("for") == "ga-form_label-1"


def test_form_label_refuses_when_text_changed() -> None:
    tree, ctx = tree_and_ctx()
    box = by_id(tree, "terms")
    fix = planned(
        "form_label",
        box,
        mode="label-for",
        text="Something else",
        label_path=list(dom.element_path(box.getnext())),
    )
    with pytest.raises(SkipFix, match="no longer matches"):
        apply_fix(fix, box, tree, ctx)


def test_html_lang_and_names() -> None:
    tree, ctx = tree_and_ctx()
    apply_fix(planned("html_lang", lang="en"), None, tree, ctx)
    assert tree.getroot().get("lang") == "en"
    link = dom.select(tree, "a.icon")[0]
    button = dom.select(tree, "button.chat__close")[0]
    apply_fix(planned("link_name", link, name="Search"), link, tree, ctx)
    apply_fix(planned("button_name", button, name="Close chat"), button, tree, ctx)
    assert link.get("aria-label") == "Search" and button.get("aria-label") == "Close chat"


def test_heading_order_sets_aria_level_only() -> None:
    tree, ctx = tree_and_ctx()
    sub = by_id(tree, "sub")
    card = dom.select(tree, "h4.t")[0]
    levels = [[list(dom.element_path(sub)), 2], [list(dom.element_path(card)), 2]]
    apply_fix(planned("heading_order", levels=levels), None, tree, ctx)
    assert sub.tag == "h4" and sub.get("aria-level") == "2"
    assert card.get("aria-level") == "2"


def test_heading_order_refuses_stale_paths() -> None:
    tree, ctx = tree_and_ctx()
    photo = by_id(tree, "photo")
    with pytest.raises(SkipFix):
        apply_fix(
            planned("heading_order", levels=[[list(dom.element_path(photo)), 2]]), None, tree, ctx
        )


def test_contrast_css_uses_axe_selectors_with_important() -> None:
    css = contrast_css([{"selectors": [".a > .b", "p.c:nth-child(2)"], "color": "#767676"}])
    assert css == ".a > .b,\np.c:nth-child(2) {\n  color: #767676 !important;\n}"


@pytest.mark.parametrize(
    "selector", ["a { color: red } b", "a;b", "a/*x*/", "a\nb", "@import url(x)", "</style>"]
)
def test_contrast_css_refuses_selectors_that_could_inject(selector: str) -> None:
    with pytest.raises(SkipFix):
        contrast_css([{"selectors": [selector], "color": "#767676"}])


def test_contrast_css_refuses_bad_colour() -> None:
    with pytest.raises(SkipFix):
        contrast_css([{"selectors": [".a"], "color": "red;}"}])


# --------------------------------------------------------------------------- #
# Carbon kinds
# --------------------------------------------------------------------------- #


def test_lazy_load_only_below_the_fold() -> None:
    tree, ctx = tree_and_ctx()
    photo = by_id(tree, "photo")
    with pytest.raises(SkipFix, match="fold"):
        apply_fix(planned("lazy_load", photo, below_fold=False), photo, tree, ctx)
    assert photo.get("loading") == "eager"
    apply_fix(
        planned("lazy_load", photo, below_fold=True, url="x", natural_w=3000, natural_h=2000),
        photo,
        tree,
        ctx,
    )
    assert photo.get("loading") == "lazy"
    assert (photo.get("width"), photo.get("height")) == ("3000", "2000")


def test_image_compress_swaps_src_and_adds_aspect_only_dimensions() -> None:
    tree, ctx = tree_and_ctx()
    photo = by_id(tree, "photo")
    photo.set("srcset", "/big.jpg 2x")
    url = dom.normalise_url("/assets/generated/article-01.jpg", DEMO)
    ctx.optimized[url] = OptimizedAsset("assets/optimized/a.webp", 800, 533, 90_000, 4_000)
    apply_fix(planned("image_compress", photo, url=url), photo, tree, ctx)
    assert photo.get("src") == "assets/optimized/a.webp"
    assert photo.get("srcset") is None
    assert (photo.get("width"), photo.get("height")) == ("800", "533")
    assert photo.get("data-ga-size") == ""
    ids = [fix_id for fix_id, _ in ctx.css]
    assert ids.count(INTRINSIC_SIZE_ID) == 1
    css = dict(ctx.css)[INTRINSIC_SIZE_ID]
    assert ":where(img[data-ga-size])" in css and "height: auto" in css


def test_image_compress_skips_when_not_smaller_or_inside_picture() -> None:
    tree, ctx = tree_and_ctx()
    photo = by_id(tree, "photo")
    url = dom.normalise_url(photo.get("src"), DEMO)
    ctx.optimize_skipped[url] = "WebP would be larger"
    with pytest.raises(SkipFix, match="larger"):
        apply_fix(planned("image_compress", photo, url=url), photo, tree, ctx)
    pic = by_id(tree, "pic")
    pic_url = dom.normalise_url("/a.jpg", DEMO)
    ctx.optimized[pic_url] = OptimizedAsset("assets/optimized/p.webp", 10, 10, 100, 50)
    with pytest.raises(SkipFix, match="picture"):
        apply_fix(planned("image_compress", pic, url=pic_url), pic, tree, ctx)


def test_autoplay_video_stops_autoplay_and_keeps_content() -> None:
    tree, ctx = tree_and_ctx()
    video = by_id(tree, "v")
    url = dom.normalise_url("/v.mp4", DEMO)
    ctx.posters[url] = "assets/optimized/v-poster.webp"
    apply_fix(planned("autoplay_video", video, url=url), video, tree, ctx)
    assert "autoplay" not in video.attrib
    assert video.get("preload") == "none" and video.get("controls") == ""
    assert video.get("poster") == "assets/optimized/v-poster.webp"
    assert video.get("src") == "/v.mp4"  # the video itself is never removed


def test_reduced_motion_css_block() -> None:
    tree, ctx = tree_and_ctx()
    apply_fix(planned("reduced_motion"), None, tree, ctx)
    assert ctx.css == [("reduced_motion-1", REDUCED_MOTION_CSS)]
    assert "@media (prefers-reduced-motion: reduce)" in REDUCED_MOTION_CSS


def test_third_party_remove_only_for_allow_listed_demo_trackers() -> None:
    tree, ctx = tree_and_ctx()
    tracker, cdn = dom.select(tree, "script")
    apply_fix(planned("third_party_remove", tracker), tracker, tree, ctx)
    assert "analytics.js" not in html(tree)
    with pytest.raises(SkipFix, match="allow-list"):
        apply_fix(planned("third_party_remove", cdn), cdn, tree, ctx)
    assert "cdn.example.com" in html(tree)


def test_third_party_remove_refused_off_the_demo_site() -> None:
    tree, ctx = tree_and_ctx("https://news.example/")
    tracker = dom.select(tree, "script")[0]
    with pytest.raises(SkipFix):
        apply_fix(planned("third_party_remove", tracker), tracker, tree, ctx)


def test_text_in_image_replaces_allow_listed_banner_with_escaped_text() -> None:
    tree, ctx = tree_and_ctx()
    banner = by_id(tree, "banner")
    fix = planned(
        "text_in_image",
        banner,
        lines=["HALF-PRICE <SALE>", "This week only"],
        background="#14508c",
        color="#ffffff",
    )
    apply_fix(fix, banner, tree, ctx)
    out = html(tree)
    assert "banner-sale.jpg" not in out
    assert '<div class="ga-text-banner">' in out
    assert "HALF-PRICE &lt;SALE&gt;" in out
    assert any(".ga-text-banner" in css for _, css in ctx.css)


def test_text_in_image_still_applies_after_image_compress_swapped_src() -> None:
    tree, ctx = tree_and_ctx()
    banner = by_id(tree, "banner")
    banner.set("src", "assets/optimized/banner-sale-1.webp")  # image_compress ran first
    url = dom.normalise_url("/assets/generated/banner-sale.jpg", DEMO)
    fix = planned(
        "text_in_image", banner, url=url, lines=["X"], background="#000000", color="#ffffff"
    )
    apply_fix(fix, banner, tree, ctx)
    assert "ga-text-banner" in html(tree) and "banner-sale-1.webp" not in html(tree)


def test_text_in_image_refused_for_images_not_on_the_allow_list() -> None:
    tree, ctx = tree_and_ctx()
    other = by_id(tree, "other-banner")
    fix = planned("text_in_image", other, lines=["X"], background="#000000", color="#ffffff")
    with pytest.raises(SkipFix, match="allow-list"):
        apply_fix(fix, other, tree, ctx)


def test_dark_mode_tokens_need_variables() -> None:
    tree, ctx = tree_and_ctx()
    with pytest.raises(SkipFix, match="suggestion"):
        apply_fix(planned("dark_mode_tokens", variables={}), None, tree, ctx)
    apply_fix(
        planned("dark_mode_tokens", variables={"--bg": "#121212", "--ink": "#e8e8e8"}),
        None,
        tree,
        ctx,
    )
    css = ctx.css[-1][1]
    assert "prefers-color-scheme: dark" in css and "--bg: #121212" in css


def test_dark_mode_css_refuses_unexpected_values() -> None:
    with pytest.raises(SkipFix):
        dark_mode_css({"--bg": "red;} body{display:none"})
    with pytest.raises(SkipFix):
        dark_mode_css({"bad name": "#121212"})


# --------------------------------------------------------------------------- #
# Dispatcher guarantees
# --------------------------------------------------------------------------- #


def test_manual_fixes_are_never_applied() -> None:
    tree, ctx = tree_and_ctx()
    fix = planned("img_alt", by_id(tree, "photo"), alt="x")
    fix.fix.manual_review = True
    with pytest.raises(SkipFix, match="manual"):
        apply_fix(fix, by_id(tree, "photo"), tree, ctx)


def test_unknown_kind_and_missing_element_are_skipped_with_reasons() -> None:
    tree, ctx = tree_and_ctx()
    with pytest.raises(SkipFix, match="no automatic transform"):
        apply_fix(planned("region"), None, tree, ctx)
    with pytest.raises(SkipFix, match="could not be found"):
        apply_fix(planned("img_alt", alt="x"), None, tree, ctx)


def test_removed_element_is_reported_not_edited() -> None:
    tree, ctx = tree_and_ctx()
    photo = by_id(tree, "photo")
    photo.getparent().remove(photo)
    with pytest.raises(SkipFix, match="removed"):
        apply_fix(planned("img_alt", photo, alt="x"), photo, tree, ctx)


def test_missing_parameters_become_a_skip_not_a_crash() -> None:
    tree, ctx = tree_and_ctx()
    photo = by_id(tree, "photo")
    with pytest.raises(SkipFix, match="incomplete"):
        apply_fix(planned("img_alt", photo), photo, tree, ctx)
