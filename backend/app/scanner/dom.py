"""DOM facts the carbon detectors need (MASTERSPEC §7.3).

The detectors in :mod:`app.carbon.detectors` are pure functions over data. This
module is the one place that talks to the page to gather that data: rendered
versus natural image sizes, where each element sits relative to the fold, media
attributes, and whether any stylesheet honours ``prefers-reduced-motion``.

Everything is collected in a single ``page.evaluate`` call so the page is only
measured once, at a consistent moment, after scrolling has settled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class ImageFact:
    """One ``<img>`` as the browser actually rendered it."""

    src: str
    selector: str
    natural_w: int = 0
    natural_h: int = 0
    rendered_w: int = 0
    rendered_h: int = 0
    alt: str | None = None
    has_alt_attribute: bool = False
    has_width_attribute: bool = False
    has_height_attribute: bool = False
    loading: str = ""
    #: Distance from the top of the document, in CSS pixels.
    document_top: float = 0.0
    below_fold: bool = False
    #: True for CSS background images, which have no alt and cannot be lazy.
    is_background: bool = False


@dataclass
class MediaFact:
    """One ``<video>`` or ``<audio>`` element."""

    src: str
    selector: str
    tag: str = "video"
    autoplay: bool = False
    loop: bool = False
    muted: bool = False
    has_poster: bool = False
    preload: str = ""
    below_fold: bool = False
    #: A <track kind="captions"> or kind="subtitles" child (MASTERSPEC §10).
    has_captions: bool = False


@dataclass
class ClickableDivFact:
    """A non-button element wired up as if it were one (MASTERSPEC §10)."""

    selector: str
    role: str = ""
    has_tabindex: bool = False
    has_onclick: bool = False
    text: str = ""


@dataclass
class DomFacts:
    """Everything gathered from the page in one pass."""

    viewport_width: int = 0
    viewport_height: int = 0
    document_height: int = 0
    device_pixel_ratio: float = 1.0

    images: list[ImageFact] = field(default_factory=list)
    media: list[MediaFact] = field(default_factory=list)
    clickable_divs: list[ClickableDivFact] = field(default_factory=list)

    #: Any CSS animation or transition declared anywhere on the page.
    has_animations: bool = False
    #: A ``@media (prefers-reduced-motion...)`` rule in any same-origin sheet.
    has_reduced_motion_rule: bool = False
    #: Sheets we could not read because of cross-origin restrictions. Recorded
    #: so `no_reduced_motion` can say when its answer is incomplete.
    unreadable_stylesheets: int = 0

    #: Any `prefers-color-scheme` media query, `color-scheme` declaration or
    #: `<meta name="color-scheme">`. The dark-mode trade-off rule fires only
    #: when this is False (MASTERSPEC §10).
    has_color_scheme_support: bool = False

    html_lang: str = ""
    title: str = ""


_COLLECT_JS = r"""
() => {
  const selectorFor = (node) => {
    const parts = [];
    let current = node;
    let depth = 0;
    while (current && current.nodeType === 1 && depth < 6) {
      let part = current.tagName.toLowerCase();
      if (current.id) { parts.unshift(part + '#' + current.id); break; }
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children)
          .filter((c) => c.tagName === current.tagName);
        if (siblings.length > 1) {
          part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
        }
      }
      parts.unshift(part);
      current = current.parentElement;
      depth += 1;
    }
    return parts.join(' > ');
  };

  const scrollTop = window.scrollY || document.documentElement.scrollTop || 0;
  const viewportH = window.innerHeight;

  // ---- Images -------------------------------------------------------- //
  const images = Array.from(document.querySelectorAll('img')).map((img) => {
    const box = img.getBoundingClientRect();
    const documentTop = box.top + scrollTop;
    return {
      src: img.currentSrc || img.src || '',
      selector: selectorFor(img),
      natural_w: img.naturalWidth || 0,
      natural_h: img.naturalHeight || 0,
      rendered_w: Math.round(box.width),
      rendered_h: Math.round(box.height),
      alt: img.hasAttribute('alt') ? img.getAttribute('alt') : null,
      has_alt_attribute: img.hasAttribute('alt'),
      has_width_attribute: img.hasAttribute('width'),
      has_height_attribute: img.hasAttribute('height'),
      loading: img.getAttribute('loading') || '',
      document_top: documentTop,
      // "Below the fold" means below the FIRST viewport, measured from the top
      // of the document -- not below the current scroll position.
      below_fold: documentTop >= viewportH,
      is_background: false,
    };
  });

  // ---- Media --------------------------------------------------------- //
  const media = Array.from(document.querySelectorAll('video, audio')).map((el) => {
    const box = el.getBoundingClientRect();
    let src = el.currentSrc || el.getAttribute('src') || '';
    if (!src) {
      const source = el.querySelector('source');
      if (source) src = source.getAttribute('src') || '';
    }
    return {
      src: src,
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      autoplay: el.hasAttribute('autoplay') || el.autoplay === true,
      loop: el.hasAttribute('loop'),
      muted: el.hasAttribute('muted') || el.muted === true,
      has_poster: el.hasAttribute('poster') && !!el.getAttribute('poster'),
      preload: el.getAttribute('preload') || '',
      below_fold: (box.top + scrollTop) >= viewportH,
      has_captions: !!el.querySelector(
        'track[kind="captions"], track[kind="subtitles"]'),
    };
  });

  // ---- Clickable non-buttons ----------------------------------------- //
  const clickableSelector =
    'div[onclick], span[onclick], li[onclick], a:not([href])[onclick], ' +
    'div[role="button"], span[role="button"]';
  const clickableDivs = Array.from(document.querySelectorAll(clickableSelector))
    .map((el) => ({
      selector: selectorFor(el),
      role: el.getAttribute('role') || '',
      has_tabindex: el.hasAttribute('tabindex'),
      has_onclick: el.hasAttribute('onclick'),
      text: (el.textContent || '').trim().slice(0, 80),
    }));

  // ---- Motion -------------------------------------------------------- //
  let hasAnimations = false;
  let hasReducedMotionRule = false;
  let hasColorScheme = false;
  let unreadable = 0;

  // A page counts as handling colour schemes if it declares one anywhere:
  // the meta tag, or a color-scheme property in any readable sheet, or a
  // prefers-color-scheme media query.
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta && (meta.getAttribute('content') || '').trim()) hasColorScheme = true;

  const scanRules = (rules) => {
    for (const rule of rules) {
      // CSSMediaRule
      if (rule.media && rule.media.mediaText &&
          rule.media.mediaText.includes('prefers-reduced-motion')) {
        hasReducedMotionRule = true;
      }
      if (rule.media && rule.media.mediaText &&
          rule.media.mediaText.includes('prefers-color-scheme')) {
        hasColorScheme = true;
      }
      if (rule.cssRules) {
        try { scanRules(rule.cssRules); } catch (e) { /* ignore */ }
      }
      // CSSKeyframesRule
      if (rule.type === 7 || (rule.constructor &&
          rule.constructor.name === 'CSSKeyframesRule')) {
        hasAnimations = true;
      }
      if (rule.style) {
        const colorScheme = rule.style.colorScheme ||
                            rule.style.getPropertyValue('color-scheme') || '';
        if (colorScheme && colorScheme !== 'normal') hasColorScheme = true;
        const animation = rule.style.animationName || rule.style.animation || '';
        const transition = rule.style.transitionProperty ||
                           rule.style.transition || '';
        if (animation && animation !== 'none') hasAnimations = true;
        if (transition && transition !== 'none' && transition !== 'all 0s ease 0s') {
          hasAnimations = true;
        }
      }
    }
  };

  for (const sheet of Array.from(document.styleSheets)) {
    try {
      scanRules(Array.from(sheet.cssRules));
    } catch (error) {
      // Cross-origin stylesheets cannot be read.
      unreadable += 1;
    }
  }

  return {
    viewport_width: window.innerWidth,
    viewport_height: viewportH,
    document_height: document.documentElement.scrollHeight,
    device_pixel_ratio: window.devicePixelRatio || 1,
    images: images,
    media: media,
    clickable_divs: clickableDivs,
    has_animations: hasAnimations,
    has_reduced_motion_rule: hasReducedMotionRule,
    has_color_scheme_support: hasColorScheme,
    unreadable_stylesheets: unreadable,
    html_lang: document.documentElement.getAttribute('lang') || '',
    title: document.title || '',
  };
}
"""


async def collect(page: Page) -> DomFacts:
    """Gather every DOM fact the detectors need, in one pass."""
    try:
        raw = await page.evaluate(_COLLECT_JS)
    except PlaywrightError as exc:
        logger.warning("DOM fact collection failed: %s", exc)
        return DomFacts()

    if not isinstance(raw, dict):
        return DomFacts()

    return DomFacts(
        viewport_width=int(raw.get("viewport_width") or 0),
        viewport_height=int(raw.get("viewport_height") or 0),
        document_height=int(raw.get("document_height") or 0),
        device_pixel_ratio=float(raw.get("device_pixel_ratio") or 1.0),
        images=[
            ImageFact(
                src=str(item.get("src") or ""),
                selector=str(item.get("selector") or ""),
                natural_w=int(item.get("natural_w") or 0),
                natural_h=int(item.get("natural_h") or 0),
                rendered_w=int(item.get("rendered_w") or 0),
                rendered_h=int(item.get("rendered_h") or 0),
                alt=item.get("alt"),
                has_alt_attribute=bool(item.get("has_alt_attribute")),
                has_width_attribute=bool(item.get("has_width_attribute")),
                has_height_attribute=bool(item.get("has_height_attribute")),
                loading=str(item.get("loading") or ""),
                document_top=float(item.get("document_top") or 0.0),
                below_fold=bool(item.get("below_fold")),
                is_background=bool(item.get("is_background")),
            )
            for item in (raw.get("images") or [])
            if isinstance(item, dict)
        ],
        media=[
            MediaFact(
                src=str(item.get("src") or ""),
                selector=str(item.get("selector") or ""),
                tag=str(item.get("tag") or "video"),
                autoplay=bool(item.get("autoplay")),
                loop=bool(item.get("loop")),
                muted=bool(item.get("muted")),
                has_poster=bool(item.get("has_poster")),
                preload=str(item.get("preload") or ""),
                below_fold=bool(item.get("below_fold")),
                has_captions=bool(item.get("has_captions")),
            )
            for item in (raw.get("media") or [])
            if isinstance(item, dict)
        ],
        clickable_divs=[
            ClickableDivFact(
                selector=str(item.get("selector") or ""),
                role=str(item.get("role") or ""),
                has_tabindex=bool(item.get("has_tabindex")),
                has_onclick=bool(item.get("has_onclick")),
                text=str(item.get("text") or ""),
            )
            for item in (raw.get("clickable_divs") or [])
            if isinstance(item, dict)
        ],
        has_animations=bool(raw.get("has_animations")),
        has_reduced_motion_rule=bool(raw.get("has_reduced_motion_rule")),
        has_color_scheme_support=bool(raw.get("has_color_scheme_support")),
        unreadable_stylesheets=int(raw.get("unreadable_stylesheets") or 0),
        html_lang=str(raw.get("html_lang") or ""),
        title=str(raw.get("title") or ""),
    )
