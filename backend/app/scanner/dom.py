"""DOM facts for the carbon detectors (MASTERSPEC §7.3).

Single responsibility: read, once and from the live page, every DOM fact the
carbon detectors need, and return it as plain pydantic data. The detectors in
:mod:`app.carbon.detectors` are pure functions over this data plus the network
summary, so they can be unit-tested without a browser.

Why the live page and not the HTML source: most §7.3 detectors depend on
layout. "Rendered width", "below the first viewport" and "natural size" do not
exist in the markup; only the browser knows them.

Facts are collected with the page scrolled to the top (the pipeline calls this
straight after the load step returns to the top), and element positions are
recorded in document coordinates so the value does not depend on where the
page happens to be scrolled.
"""

from __future__ import annotations

import logging

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Stylesheet text is only scanned for a handful of patterns; cap what we carry.
MAX_STYLESHEET_CHARS = 2_000_000


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageFact(_Fact):
    """One ``<img>`` element as rendered."""

    selector: str
    #: The URL the browser actually chose (``currentSrc``), absolute.
    src: str = ""
    #: ``None`` when the attribute is absent, which differs from ``alt=""``.
    alt: str | None = None
    has_width_attr: bool = False
    has_height_attr: bool = False
    #: The ``loading`` attribute, lower-cased; "" when absent.
    loading: str = ""
    natural_w: int = 0
    natural_h: int = 0
    rendered_w: int = 0
    rendered_h: int = 0
    #: Distance from the top of the document to the element's top edge, in CSS px.
    doc_top: int = 0


class VideoFact(_Fact):
    """One ``<video>`` element."""

    selector: str
    #: The media URL in use (``currentSrc``, else ``src``, else first ``<source>``).
    src: str = ""
    autoplay: bool = False
    loop: bool = False
    muted: bool = False
    has_poster: bool = False
    controls: bool = False
    preload: str = ""
    has_captions: bool = False
    doc_top: int = 0


class StylesheetFact(_Fact):
    """The rule text of one readable stylesheet or ``<style>`` element."""

    #: "" for an inline ``<style>`` block.
    href: str = ""
    #: Same origin as the page (inline blocks count as same origin).
    same_origin: bool = True
    #: Serialised rules (``cssRules[].cssText``). Comments are already gone.
    css_text: str = ""


class PageFacts(_Fact):
    """Everything the carbon detectors read from the DOM."""

    page_url: str = ""
    viewport_w: int = 0
    viewport_h: int = 0
    images: list[ImageFact] = Field(default_factory=list)
    videos: list[VideoFact] = Field(default_factory=list)
    stylesheets: list[StylesheetFact] = Field(default_factory=list)
    #: Stylesheets the browser refused to let us read (cross-origin, no CORS).
    unreadable_stylesheets: list[str] = Field(default_factory=list)
    #: Selectors of elements that act as buttons but are not buttons or links:
    #: an ``onclick`` attribute or ``role="button"`` on a non-interactive tag.
    clickable_non_buttons: list[str] = Field(default_factory=list)
    #: ``<meta name="color-scheme">`` content, "" when absent.
    meta_color_scheme: str = ""


_COLLECT_JS = r"""
(maxChars) => {
  const selectorFor = (node) => {
    const parts = [];
    let current = node;
    let depth = 0;
    while (current && current.nodeType === 1 && depth < 6) {
      let part = current.tagName.toLowerCase();
      if (current.id) {
        parts.unshift(part + '#' + CSS.escape(current.id));
        break;
      }
      const firstClass = current.classList && current.classList[0];
      if (firstClass) part += '.' + CSS.escape(firstClass);
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (c) => c.tagName === current.tagName);
        if (siblings.length > 1) {
          part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
        }
      }
      parts.unshift(part);
      if (current.tagName === 'BODY') break;
      current = current.parentElement;
      depth += 1;
    }
    return parts.join(' > ');
  };

  const docTop = (el) => Math.round(el.getBoundingClientRect().top + window.scrollY);

  const images = Array.from(document.images).map((img) => {
    const box = img.getBoundingClientRect();
    return {
      selector: selectorFor(img),
      src: img.currentSrc || img.src || '',
      alt: img.hasAttribute('alt') ? img.getAttribute('alt') : null,
      has_width_attr: img.hasAttribute('width'),
      has_height_attr: img.hasAttribute('height'),
      loading: (img.getAttribute('loading') || '').toLowerCase(),
      natural_w: img.naturalWidth || 0,
      natural_h: img.naturalHeight || 0,
      rendered_w: Math.round(box.width),
      rendered_h: Math.round(box.height),
      doc_top: docTop(img),
    };
  });

  const videos = Array.from(document.querySelectorAll('video')).map((video) => {
    const source = video.querySelector('source[src]');
    const tracks = Array.from(video.querySelectorAll('track'));
    return {
      selector: selectorFor(video),
      src: video.currentSrc || video.src || (source ? source.src : '') || '',
      autoplay: video.hasAttribute('autoplay') || video.autoplay === true,
      loop: video.hasAttribute('loop'),
      muted: video.hasAttribute('muted') || video.muted === true,
      has_poster: !!video.getAttribute('poster'),
      controls: video.hasAttribute('controls'),
      preload: (video.getAttribute('preload') || '').toLowerCase(),
      has_captions: tracks.some((t) => {
        const kind = (t.getAttribute('kind') || 'subtitles').toLowerCase();
        return kind === 'captions' || kind === 'subtitles';
      }),
      doc_top: docTop(video),
    };
  });

  const stylesheets = [];
  const unreadable = [];
  let budget = maxChars;
  const isSameOrigin = (href) => {
    if (!href) return true;
    try {
      return new URL(href).origin === window.location.origin;
    } catch (error) {
      return false;
    }
  };
  // Walks a sheet and, depth-first, every sheet it @imports: document.styleSheets
  // lists only top-level sheets, so an imported one would otherwise be missed.
  const visit = (sheet, depth) => {
    const href = sheet.href || '';
    let rules;
    try {
      rules = sheet.cssRules;
    } catch (error) {
      unreadable.push(href);
      return;
    }
    if (!rules) return;
    let text = '';
    const imported = [];
    for (const rule of Array.from(rules)) {
      if (rule.styleSheet && depth < 4) imported.push(rule.styleSheet);
      if (text.length >= budget) break;
      text += rule.cssText + '\n';
    }
    budget = Math.max(0, budget - text.length);
    stylesheets.push({ href: href, same_origin: isSameOrigin(href), css_text: text });
    imported.forEach((child) => visit(child, depth + 1));
  };
  Array.from(document.styleSheets).forEach((sheet) => visit(sheet, 0));

  const interactive = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY', 'OPTION', 'LABEL']);
  const clickable = Array.from(
    document.querySelectorAll('[onclick], [role="button"]')
  ).filter((el) => !interactive.has(el.tagName)).map(selectorFor);

  const meta = document.querySelector('meta[name="color-scheme"]');

  return {
    page_url: window.location.href,
    viewport_w: window.innerWidth,
    viewport_h: window.innerHeight,
    images: images,
    videos: videos,
    stylesheets: stylesheets,
    unreadable_stylesheets: unreadable,
    clickable_non_buttons: clickable,
    meta_color_scheme: meta ? (meta.getAttribute('content') || '') : '',
  };
}
"""


async def collect_page_facts(page: Page) -> PageFacts:
    """Read :class:`PageFacts` from `page`. Returns empty facts on failure."""
    try:
        raw = await page.evaluate(_COLLECT_JS, MAX_STYLESHEET_CHARS)
    except PlaywrightError as exc:
        logger.warning("could not collect DOM facts: %s", exc)
        return PageFacts(page_url=page.url)
    if not isinstance(raw, dict):
        return PageFacts(page_url=page.url)
    return PageFacts.model_validate(raw)
