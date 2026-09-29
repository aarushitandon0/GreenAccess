"""DOM and CSS transforms for each fix kind (MASTERSPEC §8.1).

Single responsibility: apply one planned fix to the parsed source document,
or refuse with a reason. Element changes are made with lxml; style changes are
collected as CSS blocks for ``greenaccess-patch.css``, never written inline.

Every transform is conservative by construction (MASTERSPEC phase rules:
"never apply a fix that would degrade the page"):

* no content is removed, except a demo-allow-listed tracker script (kind 12)
  or a demo-allow-listed text banner that is replaced by the same text as
  real HTML (kind 11);
* lazy loading is only added to images the scan measured below the fold;
* images are only swapped for a variant that is actually smaller;
* values from the LLM only ever become attribute values or text nodes, which
  lxml escapes; they are never parsed as markup or CSS.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

import lxml.html
from lxml import etree

from app.patcher.allowlist import DemoAllowList
from app.patcher.dom import ElementPath, element_at, normalise_url
from app.patcher.plan import PlannedFix

__all__ = [
    "KIND_ORDER",
    "REDUCED_MOTION_CSS",
    "OptimizedAsset",
    "PatchContext",
    "SkipFix",
    "apply_fix",
    "contrast_css",
    "dark_mode_css",
    "text_banner_css",
]

#: Application order. Attribute fixes first; image swaps before lazy loading
#: (which reads the new dimensions); structural replacements and removals
#: after every fix that might target the same element.
KIND_ORDER: Final[tuple[str, ...]] = (
    "html_lang",
    "heading_order",
    "img_alt",
    "form_label",
    "link_name",
    "button_name",
    "image_compress",
    "lazy_load",
    "autoplay_video",
    "third_party_remove",
    "text_in_image",
    "contrast",
    "reduced_motion",
    "dark_mode_tokens",
)

#: MASTERSPEC §8.1 kind 9: neutralise animation and transition for visitors
#: who asked their OS for reduced motion. 0.01ms rather than none, so
#: animationend/transitionend handlers still fire and scripts do not stall.
REDUCED_MOTION_CSS: Final[str] = """@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}"""

#: MASTERSPEC §8.1 kind 13 / §13 dark theme values.
DARK_BACKGROUND: Final[str] = "#121212"
DARK_TEXT: Final[str] = "#e8e8e8"

#: Characters that could end the selector list or the rule and start new CSS.
#: ``>`` is the child combinator and stays allowed.
#: Width/height attributes the patch adds must only reserve the aspect ratio.
#: As presentational hints they would otherwise set a fixed size wherever the
#: page's CSS sizes only one axis. ``:where()`` has zero specificity, so every
#: size the page's own CSS sets still wins over this rule.
INTRINSIC_SIZE_CSS: Final[str] = ":where(img[data-ga-size]) {\n  width: auto;\n  height: auto;\n}"
INTRINSIC_SIZE_ID: Final[str] = "intrinsic-size"

_UNSAFE_SELECTOR: Final[re.Pattern[str]] = re.compile(r"[{};<@]|/\*|\*/|[\r\n]")
_CUSTOM_PROPERTY: Final[re.Pattern[str]] = re.compile(r"^--[A-Za-z0-9_-]{1,64}$")


class SkipFix(Exception):
    """The fix was not applied; ``str(exc)`` is the reason, shown in CHANGES.md."""


@dataclass(frozen=True)
class OptimizedAsset:
    """An optimised image already written into the patch directory."""

    rel_path: str
    width: int
    height: int
    original_bytes: int
    new_bytes: int


@dataclass
class PatchContext:
    page_url: str
    allowlist: DemoAllowList
    #: Optimised images by normalised original URL.
    optimized: dict[str, OptimizedAsset] = field(default_factory=dict)
    #: Why an image could not be optimised, by normalised original URL.
    optimize_skipped: dict[str, str] = field(default_factory=dict)
    #: Poster paths by normalised video URL.
    posters: dict[str, str] = field(default_factory=dict)
    #: ``(fix_id, css)`` blocks for greenaccess-patch.css, in application order.
    css: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pure CSS builders
# --------------------------------------------------------------------------- #


def contrast_css(rules: list[dict[str, Any]]) -> str:
    """``selector, … { color: #xxxxxx !important; }`` per colour group.

    Selectors are axe's own, so they match exactly the nodes it measured in
    the live DOM the re-scan will see. Anything that could break out of a
    selector list is refused rather than escaped.
    """
    blocks: list[str] = []
    for rule in rules:
        selectors = [str(s).strip() for s in rule.get("selectors", []) if str(s).strip()]
        color = str(rule.get("color", ""))
        if not re.fullmatch(r"#[0-9a-f]{6}", color):
            raise SkipFix(f"invalid colour {color!r}")
        if not selectors or any(_UNSAFE_SELECTOR.search(s) for s in selectors):
            raise SkipFix("selector contains characters that are unsafe in a stylesheet")
        blocks.append(",\n".join(selectors) + f" {{\n  color: {color} !important;\n}}")
    return "\n\n".join(blocks)


def dark_mode_css(variables: dict[str, str]) -> str:
    for name, value in variables.items():
        if not _CUSTOM_PROPERTY.match(name) or value not in (DARK_BACKGROUND, DARK_TEXT):
            raise SkipFix(f"refusing unexpected custom property {name!r}: {value!r}")
    body = "\n".join(f"    {name}: {value};" for name, value in sorted(variables.items()))
    return f"@media (prefers-color-scheme: dark) {{\n  :root {{\n{body}\n  }}\n}}"


def text_banner_css(background: str, color: str) -> str:
    for value in (background, color):
        if not re.fullmatch(r"#[0-9a-f]{6}", value):
            raise SkipFix(f"invalid colour {value!r}")
    return (
        ".ga-text-banner {\n"
        "  display: block;\n  max-width: 820px;\n  margin: 0 0 26px;\n"
        f"  padding: 24px 28px;\n  background: {background};\n  color: {color};\n"
        "}\n"
        ".ga-text-banner__line {\n  margin: 0;\n  font-size: 18px;\n  line-height: 1.4;\n}\n"
        ".ga-text-banner__line:first-child {\n"
        "  font-size: 30px;\n  font-weight: 700;\n  line-height: 1.15;\n  margin-bottom: 6px;\n}"
    )


# --------------------------------------------------------------------------- #
# Element transforms
# --------------------------------------------------------------------------- #


def _set_intrinsic_size(img: etree._Element, width: int, height: int, ctx: PatchContext) -> None:
    img.set("width", str(width))
    img.set("height", str(height))
    img.set("data-ga-size", "")
    if all(fix_id != INTRINSIC_SIZE_ID for fix_id, _ in ctx.css):
        ctx.css.append((INTRINSIC_SIZE_ID, INTRINSIC_SIZE_CSS))


def _require(el: etree._Element | None, *tags: str) -> etree._Element:
    if el is None:
        raise SkipFix("the target element could not be found in the page source")
    if el.getparent() is None and el.tag.lower() != "html":
        raise SkipFix("the target element was removed by an earlier fix")
    if tags and el.tag.lower() not in tags:
        raise SkipFix(f"expected <{'|'.join(tags)}>, found <{el.tag}>")
    return el


def _img_alt(fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext) -> None:
    target = _require(el, "img")
    target.set("alt", str(fix.params["alt"]))


def _form_label(fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext) -> None:
    control = _require(el, "input", "select", "textarea")
    text = str(fix.params["text"])
    if fix.params.get("mode") == "label-for":
        label_path = fix.params.get("label_path")
        label_el = element_at(tree, tuple(label_path)) if label_path is not None else None
        if label_el is None or re.sub(r"\s+", " ", label_el.text or "").strip() != text:
            raise SkipFix("the adjacent text no longer matches; not converting it to a label")
        control_id = control.get("id") or f"ga-{fix.fix.id}"
        control.set("id", control_id)
        label_el.tag = "label"
        label_el.set("for", control_id)
    else:
        control.set("aria-label", text)


def _html_lang(fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext) -> None:
    tree.getroot().set("lang", str(fix.params["lang"]))


def _accessible_name(
    fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext
) -> None:
    target = _require(el, "a", "button", "div", "span", "input")
    target.set("aria-label", str(fix.params["name"]))


def _heading_order(
    fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext
) -> None:
    changes: list[tuple[etree._Element, int]] = []
    for path, level in fix.params["levels"]:
        heading = element_at(tree, tuple(path))
        if heading is None or not re.fullmatch(r"h[1-6]", heading.tag.lower()):
            raise SkipFix("the heading outline no longer matches the page source")
        changes.append((heading, int(level)))
    for heading, level in changes:
        heading.set("aria-level", str(level))


def _image_compress(
    fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext
) -> None:
    img = _require(el, "img")
    key = normalise_url(str(fix.params["url"]))
    asset = ctx.optimized.get(key)
    if asset is None:
        raise SkipFix(ctx.optimize_skipped.get(key, "the image could not be optimised"))
    parent = img.getparent()
    if parent is not None and parent.tag.lower() == "picture":
        raise SkipFix("inside <picture>: its <source> candidates would win over a new src")
    img.set("src", asset.rel_path)
    # The WebP is sized for 2x the rendered width, which covers DPR <= 2.
    for attribute in ("srcset", "sizes"):
        if attribute in img.attrib:
            del img.attrib[attribute]
    _set_intrinsic_size(img, asset.width, asset.height, ctx)


def _lazy_load(fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext) -> None:
    img = _require(el, "img")
    if not fix.params.get("below_fold"):
        raise SkipFix("not measured below the fold; lazy loading it would delay visible content")
    img.set("loading", "lazy")
    if "width" not in img.attrib or "height" not in img.attrib:
        asset = ctx.optimized.get(normalise_url(str(fix.params.get("url", ""))))
        width = asset.width if asset else int(fix.params.get("natural_w") or 0)
        height = asset.height if asset else int(fix.params.get("natural_h") or 0)
        if width and height:
            # Intrinsic size reserves the box and fixes layout shift; CSS
            # still controls the rendered size.
            _set_intrinsic_size(img, width, height, ctx)


def _autoplay_video(
    fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext
) -> None:
    video = _require(el, "video")
    if "autoplay" in video.attrib:
        del video.attrib["autoplay"]
    video.set("preload", "none")
    video.set("controls", "")
    poster = ctx.posters.get(normalise_url(str(fix.params.get("url", ""))))
    if poster and not video.get("poster"):
        video.set("poster", poster)


def _third_party_remove(
    fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext
) -> None:
    script = _require(el, "script")
    src = normalise_url(script.get("src") or "", ctx.page_url)
    if not ctx.allowlist.script_removal_allowed(ctx.page_url, src):
        raise SkipFix("not on the demo allow-list of removable scripts; suggestion only")
    script.drop_tree()


def _text_in_image(
    fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext
) -> None:
    img = _require(el, "img")
    # The URL the fix was planned for: an earlier image_compress may already
    # have pointed src at the optimised copy.
    src = normalise_url(str(fix.params.get("url") or img.get("src") or ""), ctx.page_url)
    if not ctx.allowlist.text_in_image_allowed(ctx.page_url, src):
        raise SkipFix("not on the demo allow-list for text-in-image replacement; manual fix")
    lines = [str(line) for line in fix.params.get("lines", []) if str(line).strip()]
    if not lines:
        raise SkipFix("no transcribed text to replace the image with")
    banner = lxml.html.Element("div", {"class": "ga-text-banner"})
    for line in lines:
        paragraph = etree.SubElement(banner, "p", {"class": "ga-text-banner__line"})
        paragraph.text = line  # a text node: escaped, never parsed
    banner.tail = img.tail
    img.getparent().replace(img, banner)
    ctx.css.append(
        (fix.fix.id, text_banner_css(str(fix.params["background"]), str(fix.params["color"])))
    )


def _contrast(fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext) -> None:
    ctx.css.append((fix.fix.id, contrast_css(fix.params["rules"])))


def _reduced_motion(
    fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext
) -> None:
    ctx.css.append((fix.fix.id, REDUCED_MOTION_CSS))


def _dark_mode(fix: PlannedFix, el: etree._Element | None, tree: Any, ctx: PatchContext) -> None:
    variables = {str(k): str(v) for k, v in fix.params.get("variables", {}).items()}
    if not variables:
        raise SkipFix("the page defines no colour variables to override; suggestion only")
    ctx.css.append((fix.fix.id, dark_mode_css(variables)))


_TRANSFORMS: Final[
    dict[str, Callable[[PlannedFix, etree._Element | None, Any, PatchContext], None]]
] = {
    "img_alt": _img_alt,
    "form_label": _form_label,
    "html_lang": _html_lang,
    "link_name": _accessible_name,
    "button_name": _accessible_name,
    "heading_order": _heading_order,
    "image_compress": _image_compress,
    "lazy_load": _lazy_load,
    "autoplay_video": _autoplay_video,
    "third_party_remove": _third_party_remove,
    "text_in_image": _text_in_image,
    "contrast": _contrast,
    "reduced_motion": _reduced_motion,
    "dark_mode_tokens": _dark_mode,
}


def apply_fix(
    planned: PlannedFix,
    element: etree._Element | None,
    tree: etree._ElementTree,
    ctx: PatchContext,
) -> None:
    """Apply `planned` to `tree`. Raises :class:`SkipFix` with the reason if not."""
    if planned.fix.manual_review:
        raise SkipFix("manual fix needed: not applied automatically")
    transform = _TRANSFORMS.get(planned.fix.kind)
    if transform is None:
        raise SkipFix(f"no automatic transform for {planned.fix.kind!r}; manual fix needed")
    try:
        transform(planned, element, tree, ctx)
    except (KeyError, TypeError, ValueError) as exc:
        raise SkipFix(f"fix parameters were incomplete ({exc})") from exc


def resolve_path(tree: etree._ElementTree, path: list[int] | None) -> etree._Element | None:
    return element_at(tree, tuple(path)) if path is not None else None


ElementPathList = list[ElementPath]
