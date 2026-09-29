"""Generate the fix plan for a finished scan (MASTERSPEC §8.1, §8.2).

Single responsibility: map every scan finding onto a fix kind, compute the
fix's parameters (deterministically where possible, with the LLM where it
adds judgement), and produce a :class:`~app.patcher.plan.FixPlan`.

Where each value comes from:

========================  ====================================================
kind                      source
========================  ====================================================
img_alt                   vision call, top N=6 images by bytes (MASTERSPEC §8.2)
form_label                visible adjacent text if any (deterministic), else
                          one batched LLM call, else name/placeholder heuristic
link_name, button_name    one batched LLM call per kind, else context heuristic
html_lang                 local detection; LLM only when inconclusive
heading_order             deterministic outline repair via ``aria-level``
contrast                  deterministic colour search (:mod:`.contrast`)
lazy_load, image_compress scan measurements (fold position, sizes)
autoplay_video            scan measurements
reduced_motion            fixed CSS block
text_in_image             vision transcription, demo allow-list only
third_party_remove        demo allow-list only
dark_mode_tokens          page's own CSS variables, else suggestion only
everything else           ``manual_review``: "manual fix needed"
========================  ====================================================

A value that comes from heuristics is labelled ``ai_generated: false``; an LLM
value ``true``. When neither source is trustworthy the fix is a manual one,
with the reason spelled out; nothing is guessed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

import lxml.html
from lxml import etree

from app.llm.client import LlmClient, LlmFailed, LlmUnavailable
from app.llm.context import PromptTooLarge, extract_context
from app.llm.prompts import (
    PROMPT_VERSION,
    NameItem,
    NameKind,
    alt_text_request,
    lang_request,
    name_batch_request,
)
from app.llm.schemas import AltTextAnswer, LangAnswer, NameBatchAnswer
from app.llm.vision import VisionImageError, prepare_image
from app.models import Fix, FixDiff, ImageIssue, ImageIssueKind, Scan, ScanResult, Violation
from app.patcher import dom
from app.patcher.allowlist import DemoAllowList, load_allowlist
from app.patcher.contrast import (
    NORMAL_TEXT_RATIO,
    ColorError,
    nearest_accessible_color,
    parse_axe_contrast_summary,
)
from app.patcher.heuristics import (
    control_name_from_context,
    detect_language,
    form_label_from_context,
)
from app.patcher.paths import optimized_path, poster_path
from app.patcher.plan import FixPlan, PlannedFix, fix_id
from app.patcher.transforms import DARK_BACKGROUND, DARK_TEXT, REDUCED_MOTION_CSS
from app.scanner.network import is_third_party
from app.security.fetch import FetchError, SafeFetcher

logger = logging.getLogger(__name__)

__all__ = ["MAX_VISION_IMAGES", "SourcePage", "fetch_source", "generate_plan"]

#: MASTERSPEC §8.2: vision calls for the top N=6 heaviest images.
MAX_VISION_IMAGES: Final[int] = 6
#: Controls per batched naming call; keeps each prompt well under its cap.
NAME_BATCH_SIZE: Final[int] = 8

_RULE_KIND: Final[dict[str, str]] = {
    "image-alt": "img_alt",
    "label": "form_label",
    "select-name": "form_label",
    "html-has-lang": "html_lang",
    "html-lang-valid": "html_lang",
    "link-name": "link_name",
    "button-name": "button_name",
    "heading-order": "heading_order",
    "color-contrast": "contrast",
}

#: Detectors with no reliable automatic fix: reported as manual.
_MANUAL_DETECTORS: Final[dict[str, str]] = {
    "font_bloat": "Subset the web fonts and drop faces the page never renders.",
    "uncompressed_text": "Enable gzip or Brotli for HTML, CSS and JS on the web server.",
    "div_soup_widgets": "Replace clickable <div>s with real <button> elements.",
}


@dataclass(frozen=True)
class SourcePage:
    url: str
    html: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8")).hexdigest()


async def fetch_source(url: str, fetcher: SafeFetcher) -> SourcePage:
    """The page's HTML as its server sends it (before any script runs)."""
    fetched = await fetcher.get(url)
    if fetched.status >= 400:
        raise FetchError(f"{url} answered HTTP {fetched.status}")
    charset = re.search(r"charset=([\w-]+)", fetched.content_type or "", re.IGNORECASE)
    encoding = charset.group(1) if charset else "utf-8"
    try:
        html = fetched.body.decode(encoding, errors="replace")
    except LookupError:
        html = fetched.body.decode("utf-8", errors="replace")
    return SourcePage(url=fetched.final_url, html=html)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _describe(el: etree._Element) -> str:
    """A short readable target: ``tag#id`` or ``tag.class``."""
    tag = el.tag.lower()
    if el.get("id"):
        return f"{tag}#{el.get('id')}"
    classes = (el.get("class") or "").split()
    return tag + "".join(f".{c}" for c in classes[:2])


def _preview(
    el: etree._Element,
    *,
    set_attrs: dict[str, str] | None = None,
    drop_attrs: tuple[str, ...] = (),
    tag: str | None = None,
) -> str:
    shell = lxml.html.Element(tag or el.tag, dict(el.attrib))
    for name in drop_attrs:
        shell.attrib.pop(name, None)
    for name, value in (set_attrs or {}).items():
        shell.set(name, value)
    return dom.opening_tag(shell)


def _kb(n: int) -> str:
    return f"{n / 1024:,.0f} KB"


@dataclass
class _Planner:
    page_url: str
    tree: etree._ElementTree
    allow: DemoAllowList
    fixes: list[PlannedFix] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def add(
        self,
        kind: str,
        target_key: str,
        *,
        target: str,
        description: str,
        before: str = "",
        after: str = "",
        ai: bool = False,
        confidence: float = 1.0,
        manual: bool = False,
        el: etree._Element | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        ident = fix_id(kind, target_key)
        if ident in self._seen:
            return
        self._seen.add(ident)
        self.fixes.append(
            PlannedFix(
                fix=Fix(
                    id=ident,
                    kind=kind,
                    target=target,
                    description=description,
                    diff=FixDiff(before=before, after=after),
                    ai_generated=ai,
                    confidence=round(max(0.0, min(1.0, confidence)), 2),
                    manual_review=manual,
                ),
                path=list(dom.element_path(el)) if el is not None else None,
                params=params or {},
            )
        )

    def manual(self, kind: str, target_key: str, target: str, why: str, **kw: Any) -> None:
        self.add(
            kind,
            target_key,
            target=target,
            description=f"Manual fix needed: {why}",
            manual=True,
            confidence=0.0,
            **kw,
        )


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #


@dataclass
class _Located:
    node_selector: str
    el: etree._Element


async def generate_plan(
    scan: Scan,
    source: SourcePage,
    client: LlmClient,
    fetcher: SafeFetcher,
    *,
    allowlist: DemoAllowList | None = None,
    max_vision_images: int = MAX_VISION_IMAGES,
) -> FixPlan:
    if scan.before is None:
        raise ValueError("the scan has no result to generate fixes from")
    result: ScanResult = scan.before
    tree = dom.parse_document(source.html)
    planner = _Planner(page_url=source.url, tree=tree, allow=allowlist or load_allowlist())
    images_by_url = {dom.normalise_url(i.url): i for i in result.carbon.images}

    # -- sort accessibility findings by kind ------------------------------ #
    located: dict[str, list[_Located]] = defaultdict(list)
    for violation in result.a11y.violations:
        kind = _RULE_KIND.get(violation.rule_id)
        if kind is None:
            _manual_rule(planner, violation)
            continue
        if kind in ("contrast", "heading_order", "html_lang"):
            continue  # document-level; handled below
        for node in violation.nodes:
            el = dom.locate(tree, snippet=node.html, selector=node.selector, base_url=source.url)
            if el is None:
                planner.manual(
                    kind,
                    f"unlocated:{node.selector}",
                    node.selector,
                    "this element is not in the page's source HTML (a script probably "
                    "adds it), so it cannot be patched reliably.",
                )
                continue
            located[kind].append(_Located(node.selector, el))

    rules = {v.rule_id: v for v in result.a11y.violations}

    # -- LLM work, run concurrently --------------------------------------- #
    vision_targets = _pick_vision_targets(
        planner, located["img_alt"], images_by_url, max_vision_images
    )
    vision_task = asyncio.gather(
        *(_ask_alt(client, fetcher, el, url, images_by_url.get(url)) for el, url in vision_targets)
    )
    name_jobs: dict[str, list[tuple[_Located, etree._Element]]] = {}
    label_plans: list[tuple[_Located, Any]] = []
    for item in located["form_label"]:
        proposal = form_label_from_context(item.el)
        label_plans.append((item, proposal))
    ask_labels = [
        (item, item.el)
        for item, proposal in label_plans
        if proposal is None or proposal.mode != "label-for"
    ]
    name_jobs["form_label"] = ask_labels
    name_jobs["link_name"] = [(item, item.el) for item in located["link_name"]]
    name_jobs["button_name"] = [(item, item.el) for item in located["button_name"]]
    name_task = asyncio.gather(
        *(_ask_names(client, kind, jobs) for kind, jobs in name_jobs.items())  # type: ignore[arg-type]
    )
    lang_needed = any(r in rules for r in ("html-has-lang", "html-lang-valid"))
    guess = detect_language(tree.getroot()) if lang_needed else None
    lang_task = (
        _ask_lang(client, tree)
        if lang_needed and guess is not None and guess.lang is None
        else _none()
    )
    vision_answers, name_answers_list, lang_answer = await asyncio.gather(
        vision_task, name_task, lang_task
    )
    name_answers: dict[int, tuple[str, float]] = {}
    for answers in name_answers_list:
        name_answers.update(answers)

    # -- img_alt ---------------------------------------------------------- #
    answered: dict[int, AltTextAnswer] = {}
    for (el, _url), outcome in zip(vision_targets, vision_answers, strict=True):
        if isinstance(outcome, AltTextAnswer):
            answered[id(el)] = outcome
        needs_alt = any(item.el is el for item in located["img_alt"])
        if not needs_alt:
            continue
        if isinstance(outcome, AltTextAnswer):
            alt = outcome.alt
            planner.add(
                "img_alt",
                dom.opening_tag(el),
                target=_describe(el),
                description=(
                    'AI-generated, review before use. Decorative image: alt="" so screen '
                    f"readers skip it. {outcome.reason}"
                    if outcome.decorative
                    else f'AI-generated, review before use. Alt text: "{alt}"'
                ),
                before=dom.opening_tag(el),
                after=_preview(el, set_attrs={"alt": alt}),
                ai=True,
                confidence=outcome.confidence,
                el=el,
                params={"alt": alt, "decorative": outcome.decorative},
            )
        else:
            planner.manual("img_alt", dom.opening_tag(el), _describe(el), outcome, el=el)
    targeted = {id(el) for el, _ in vision_targets}
    for item in located["img_alt"]:
        if id(item.el) not in targeted:
            planner.manual(
                "img_alt",
                dom.opening_tag(item.el),
                _describe(item.el),
                f"the vision budget covers the {max_vision_images} heaviest images; "
                'write alt text for this one by hand (alt="" if it is decorative).',
                el=item.el,
            )

    # -- form_label ------------------------------------------------------- #
    for item, proposal in label_plans:
        el = item.el
        if proposal is not None and proposal.mode == "label-for":
            assert proposal.label_element is not None
            planner.add(
                "form_label",
                dom.opening_tag(el),
                target=item.node_selector,
                description=(
                    f'Turn the visible text "{proposal.text}" next to this control into its '
                    "<label>, so it is announced and clickable. From page context, no AI."
                ),
                before=dom.opening_tag(proposal.label_element),
                after=_preview(
                    proposal.label_element,
                    tag="label",
                    set_attrs={"for": el.get("id") or "(generated id)"},
                ),
                confidence=proposal.confidence,
                el=el,
                params={
                    "mode": "label-for",
                    "text": proposal.text,
                    "label_path": list(dom.element_path(proposal.label_element)),
                },
            )
            continue
        _name_fix(planner, "form_label", item, name_answers.get(id(el)), proposal_text=proposal)

    # -- link_name / button_name ------------------------------------------ #
    for kind in ("link_name", "button_name"):
        for item in located[kind]:
            _name_fix(planner, kind, item, name_answers.get(id(item.el)), proposal_text=None)

    # -- html_lang -------------------------------------------------------- #
    if lang_needed:
        root = tree.getroot()
        if guess is not None and guess.lang is not None:
            planner.add(
                "html_lang",
                "html",
                target="html",
                description=(
                    f'Declare the page language: lang="{guess.lang}" (detected from the '
                    "page's own words, no AI)."
                ),
                before=dom.opening_tag(root),
                after=_preview(root, set_attrs={"lang": guess.lang}),
                confidence=guess.confidence,
                el=root,
                params={"lang": guess.lang},
            )
        elif isinstance(lang_answer, LangAnswer):
            planner.add(
                "html_lang",
                "html",
                target="html",
                description=f'AI-generated, review before use. lang="{lang_answer.lang}".',
                before=dom.opening_tag(root),
                after=_preview(root, set_attrs={"lang": lang_answer.lang}),
                ai=True,
                confidence=lang_answer.confidence,
                el=root,
                params={"lang": lang_answer.lang},
            )
        else:
            reason = lang_answer if isinstance(lang_answer, str) else "language unclear"
            planner.manual("html_lang", "html", "html", f"{reason}; add lang to <html>.")

    # -- heading_order ---------------------------------------------------- #
    if "heading-order" in rules:
        _heading_fix(planner, tree)

    # -- contrast --------------------------------------------------------- #
    if "color-contrast" in rules:
        _contrast_fixes(planner, rules["color-contrast"])

    # -- carbon ----------------------------------------------------------- #
    detectors = {d.detector for d in result.carbon.detections}
    _image_fixes(planner, result, answered, source.url)
    _media_fixes(planner, result)
    if "no_reduced_motion" in detectors:
        planner.add(
            "reduced_motion",
            "document",
            target="greenaccess-patch.css",
            description=(
                "Add a prefers-reduced-motion block that stills animations and transitions "
                "for visitors who asked their OS for less motion."
            ),
            after=REDUCED_MOTION_CSS,
        )
    if "third_party_scripts" in detectors:
        _third_party_fixes(planner)
    if "no_color_scheme" in detectors:
        await _dark_mode_fix(planner, fetcher)
    for detection in result.carbon.detections:
        if detection.detector in _MANUAL_DETECTORS:
            planner.manual(
                detection.detector,
                detection.detector,
                ", ".join(detection.evidence[:3]) or "page",
                _MANUAL_DETECTORS[detection.detector],
            )

    # -- keyboard (our own crawl, not axe) -------------------------------- #
    keyboard = result.keyboard
    if keyboard.trap_detected:
        planner.manual(
            "keyboard_trap",
            f"trap:{keyboard.trap_container}",
            keyboard.trap_container or "page",
            "the automated keyboard crawl found focus trapped here. Let Tab leave the "
            "widget and close it with Escape; this lives in the page's script.",
        )
    if keyboard.focus_visible_missing_count:
        planner.manual(
            "focus_visible",
            "focus-visible",
            "page",
            f"{keyboard.focus_visible_missing_count} control(s) show no focus indicator "
            "(automated keyboard crawl). Restore an outline or equivalent focus style.",
        )

    return FixPlan(
        scan_id=scan.id,
        page_url=source.url,
        source_sha256=source.sha256,
        prompt_version=PROMPT_VERSION,
        fixes=planner.fixes,
        ai_usage=client.meter.usage(),
    )


async def _none() -> None:
    return None


# --------------------------------------------------------------------------- #
# LLM calls (each returns an answer, or a reason string on failure)
# --------------------------------------------------------------------------- #


def _pick_vision_targets(
    planner: _Planner,
    missing_alt: list[_Located],
    images_by_url: dict[str, ImageIssue],
    budget: int,
) -> list[tuple[etree._Element, str]]:
    """Images for the vision model, heaviest first, at most `budget`.

    Candidates are images with no alt (an accessibility failure), plus demo
    banners allow-listed for the text-in-image replacement, which need their
    words transcribed. Other suspected text images cost no call: their fix is
    a manual suggestion anyway.
    """
    candidates: dict[int, tuple[int, etree._Element, str]] = {}
    for item in missing_alt:
        url = dom.normalise_url(item.el.get("src") or "", planner.page_url)
        size = images_by_url[url].bytes if url in images_by_url else 0
        candidates[id(item.el)] = (size, item.el, url)
    for url, issue in images_by_url.items():
        if ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED not in issue.issues:
            continue
        if not planner.allow.text_in_image_allowed(planner.page_url, url):
            continue
        el = dom.locate(planner.tree, src=url, base_url=planner.page_url, selector=issue.selector)
        if el is not None:
            candidates.setdefault(id(el), (issue.bytes, el, url))
    ranked = sorted(candidates.values(), key=lambda c: (-c[0], dom.element_path(c[1])))
    return [(el, url) for _, el, url in ranked[:budget]]


async def _ask_alt(
    client: LlmClient,
    fetcher: SafeFetcher,
    el: etree._Element,
    url: str,
    issue: ImageIssue | None,
) -> AltTextAnswer | str:
    try:
        fetched = await fetcher.get(url)
        image = prepare_image(fetched.body)
    except (FetchError, VisionImageError) as exc:
        return f"the image could not be prepared for the vision model ({exc})"
    try:
        request = alt_text_request(
            image,
            extract_context(el),
            file_name=urlsplit(url).path.rsplit("/", 1)[-1],
            rendered_w=issue.rendered_w if issue else 0,
            rendered_h=issue.rendered_h if issue else 0,
        )
    except PromptTooLarge as exc:
        return f"the element's context is too large to send safely ({exc})"
    try:
        return (await client.structured(request, AltTextAnswer)).value
    except LlmUnavailable as exc:
        return f"AI alt text is unavailable ({exc}); write alt text by hand"
    except LlmFailed as exc:
        return f"the AI answer failed ({exc}); write alt text by hand"


def _fits(kind: NameKind, items: list[NameItem]) -> bool:
    try:
        name_batch_request(kind, items)
    except PromptTooLarge:
        return False
    return True


def _name_batches(
    kind: NameKind, jobs: list[tuple[_Located, etree._Element]]
) -> list[list[NameItem]]:
    """Split controls into batches whose prompts stay inside the context cap.

    At most NAME_BATCH_SIZE per batch, fewer when elements are large. An
    element too large to send even on its own is left out; its fix falls back
    to the page-context heuristic or to manual review.
    """
    batches: list[list[NameItem]] = []
    current: list[NameItem] = []
    for index, (_, el) in enumerate(jobs, start=1):
        item = NameItem(id=f"c{index}", context=extract_context(el))
        trial = [*current, item]
        if len(trial) <= NAME_BATCH_SIZE and _fits(kind, trial):
            current = trial
            continue
        if current:
            batches.append(current)
        current = [item] if _fits(kind, [item]) else []
    if current:
        batches.append(current)
    return batches


async def _ask_names(
    client: LlmClient, kind: NameKind, jobs: list[tuple[_Located, etree._Element]]
) -> dict[int, tuple[str, float]]:
    answers: dict[int, tuple[str, float]] = {}
    elements = {f"c{index}": el for index, (_, el) in enumerate(jobs, start=1)}
    for items in _name_batches(kind, jobs):
        try:
            batch = (
                await client.structured(name_batch_request(kind, items), NameBatchAnswer)
            ).value
        except (LlmUnavailable, LlmFailed) as exc:
            logger.info("%s names not generated by AI: %s", kind, exc)
            continue
        sent = {item.id for item in items}
        for answer in batch.items:
            el = elements.get(answer.id) if answer.id in sent else None
            if el is not None:
                answers[id(el)] = (answer.name, answer.confidence)
    return answers


async def _ask_lang(client: LlmClient, tree: etree._ElementTree) -> LangAnswer | str:
    root = tree.getroot()
    body = root.find(".//body")
    title = root.findtext(".//title") or ""
    sample = dom.visible_text(body if body is not None else root, limit=600)
    try:
        return (await client.structured(lang_request(title, sample), LangAnswer)).value
    except (LlmUnavailable, LlmFailed, PromptTooLarge) as exc:
        return f"the language was not detectable locally and AI is unavailable ({exc})"


# --------------------------------------------------------------------------- #
# Fix builders
# --------------------------------------------------------------------------- #


def _manual_rule(planner: _Planner, violation: Violation) -> None:
    selectors = [n.selector for n in violation.nodes]
    planner.manual(
        violation.rule_id,
        f"rule:{violation.rule_id}",
        ", ".join(selectors[:3]) + (" …" if len(selectors) > 3 else ""),
        f"{violation.help} ({len(selectors)} element(s), axe rule {violation.rule_id}). "
        "No reliable automatic fix exists for this rule.",
    )


def _name_fix(
    planner: _Planner,
    kind: str,
    item: _Located,
    ai_answer: tuple[str, float] | None,
    *,
    proposal_text: Any,
) -> None:
    el = item.el
    attr = "aria-label"
    if ai_answer is not None:
        name, confidence = ai_answer
        planner.add(
            kind,
            dom.opening_tag(el),
            target=item.node_selector,
            description=f'AI-generated, review before use. Accessible name: "{name}".',
            before=dom.opening_tag(el),
            after=_preview(el, set_attrs={attr: name}),
            ai=True,
            confidence=confidence,
            el=el,
            params={"mode": attr, "text": name, "name": name},
        )
        return
    if kind == "form_label":
        name = proposal_text.text if proposal_text is not None else None
        confidence = proposal_text.confidence if proposal_text is not None else 0.0
        source = proposal_text.source if proposal_text is not None else ""
    else:
        found = control_name_from_context(el)
        name, confidence, source = found if found else (None, 0.0, "")
    if not name:
        planner.manual(
            kind,
            dom.opening_tag(el),
            item.node_selector,
            "no accessible name could be derived from the page and AI is unavailable.",
            el=el,
        )
        return
    planner.add(
        kind,
        dom.opening_tag(el),
        target=item.node_selector,
        description=f'Accessible name "{name}", derived from {source} (no AI). Review it.',
        before=dom.opening_tag(el),
        after=_preview(el, set_attrs={attr: name}),
        confidence=confidence,
        el=el,
        params={"mode": attr, "text": name, "name": name},
    )


def _heading_fix(planner: _Planner, tree: etree._ElementTree) -> None:
    """Repair skipped heading levels with aria-level, leaving visuals unchanged.

    A stack of (source level, repaired level): a heading at the same source
    level as an open one is its sibling; a deeper one is exactly one level
    below its parent. The first heading keeps its level.
    """
    headings = [
        el
        for el in dom.iter_elements(tree.getroot())
        if re.fullmatch(r"h[1-6]", el.tag.lower()) and el.get("aria-level") is None
    ]
    stack: list[tuple[int, int]] = []
    changes: list[tuple[etree._Element, int, int]] = []
    for el in headings:
        level = int(el.tag[1])
        while stack and stack[-1][0] > level:
            stack.pop()
        if not stack:
            new = level
            stack.append((level, new))
        elif stack[-1][0] == level:
            new = stack[-1][1]
        else:
            new = stack[-1][1] + 1
            stack.append((level, new))
        if new != level:
            changes.append((el, level, new))
    if not changes:
        return
    first = changes[0][0]
    planner.add(
        "heading_order",
        "document",
        target=", ".join(sorted({_describe(el) for el, _, _ in changes}))[:200],
        description=(
            f"Repair {len(changes)} skipped heading level(s) with aria-level, so screen "
            "readers hear a coherent outline. Visual styling is unchanged."
        ),
        before=dom.opening_tag(first),
        after=_preview(first, set_attrs={"aria-level": str(changes[0][2])}),
        confidence=0.75,
        params={"levels": [[list(dom.element_path(el)), new] for el, _, new in changes]},
    )


def _contrast_fixes(planner: _Planner, violation: Violation) -> None:
    groups: dict[tuple[str, str, float], list[str]] = defaultdict(list)
    for node in violation.nodes:
        facts = parse_axe_contrast_summary(node.failure_summary)
        if facts is None or not node.selector:
            planner.manual(
                "contrast",
                f"unparsed:{node.selector}",
                node.selector or "(unknown)",
                "axe could not determine the colours here (text over an image or a "
                "gradient), so no colour can be computed safely.",
            )
            continue
        groups[(facts.foreground, facts.background, facts.expected_ratio)].append(node.selector)
    for (fg, bg, target), selectors in sorted(groups.items()):
        try:
            result = nearest_accessible_color(fg, bg, target=target)
        except ColorError as exc:
            planner.manual("contrast", f"{fg}/{bg}", ", ".join(selectors[:3]), str(exc))
            continue
        rule = {"selectors": selectors, "color": result.color}
        planner.add(
            "contrast",
            f"{fg}|{bg}|{target}",
            target=", ".join(selectors[:3]) + (" …" if len(selectors) > 3 else ""),
            description=(
                f"Text colour {result.original} on {result.background} is "
                f"{result.ratio_before:.2f}:1; {result.color} is the nearest colour reaching "
                f"{target}:1 ({result.ratio_after:.2f}:1). Computed, not AI. "
                f"{len(selectors)} element(s)."
            ),
            before=f"color: {result.original}; /* {result.ratio_before:.2f}:1 */",
            after=",\n".join(selectors) + f" {{ color: {result.color} !important; }}",
            params={"rules": [rule]},
        )


def _image_fixes(
    planner: _Planner, result: ScanResult, answered: dict[int, AltTextAnswer], page_url: str
) -> None:
    for issue in result.carbon.images:
        url = dom.normalise_url(issue.url)
        el = dom.locate(planner.tree, src=url, base_url=page_url, selector=issue.selector)
        label = el.get("src") if el is not None else issue.url
        if el is None:
            if {ImageIssueKind.OVERSIZED, ImageIssueKind.EAGER_BELOW_FOLD} & set(issue.issues):
                planner.manual(
                    "image_compress",
                    f"unlocated:{url}",
                    issue.url,
                    "this image is not referenced by an <img> in the source HTML "
                    "(CSS background or script-inserted); optimise it at the source.",
                )
            continue
        same_origin = (
            not is_third_party(url, page_url) and urlsplit(url).netloc == urlsplit(page_url).netloc
        )
        if {ImageIssueKind.OVERSIZED, ImageIssueKind.LEGACY_FORMAT} & set(issue.issues):
            if same_origin:
                planner.add(
                    "image_compress",
                    url,
                    target=label or issue.url,
                    description=(
                        f"Resize to 2x the rendered {issue.rendered_w}px width and re-encode as "
                        f"WebP q78 (now {issue.natural_w}x{issue.natural_h}, {_kb(issue.bytes)}; "
                        f"estimated saving {_kb(issue.estimated_saving_bytes)}, measured on "
                        "re-scan). Kept only if actually smaller."
                    ),
                    before=dom.opening_tag(el),
                    after=_preview(
                        el,
                        set_attrs={"src": optimized_path(url)},
                        drop_attrs=("srcset", "sizes"),
                    ),
                    el=el,
                    params={"url": url, "rendered_w": issue.rendered_w},
                )
            else:
                planner.manual(
                    "image_compress", url, issue.url, "served by another origin; optimise it there."
                )
        if ImageIssueKind.EAGER_BELOW_FOLD in issue.issues:
            planner.add(
                "lazy_load",
                url,
                target=label or issue.url,
                description=(
                    'Measured below the first screen: add loading="lazy" and intrinsic '
                    "width/height so it loads only when scrolled to, without layout shift."
                ),
                before=dom.opening_tag(el),
                after=_preview(el, set_attrs={"loading": "lazy"}),
                el=el,
                params={
                    "url": url,
                    "below_fold": True,
                    "natural_w": issue.natural_w,
                    "natural_h": issue.natural_h,
                },
            )
        if ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED in issue.issues:
            _text_in_image_fix(planner, el, url, answered.get(id(el)))


def _text_in_image_fix(
    planner: _Planner, el: etree._Element, url: str, answer: AltTextAnswer | None
) -> None:
    if not planner.allow.text_in_image_allowed(planner.page_url, url):
        planner.manual(
            "text_in_image",
            url,
            el.get("src") or url,
            "suspected text baked into this image. Move the words into real HTML text "
            "styled with CSS; only known demo banners are replaced automatically.",
            el=el,
        )
        return
    if answer is None or not answer.visible_text:
        planner.manual(
            "text_in_image",
            url,
            el.get("src") or url,
            "the banner's text needs to be transcribed by the vision model, which was "
            "not available for it.",
            el=el,
        )
        return
    try:
        colors = nearest_accessible_color(
            answer.text_color, answer.background_color, target=NORMAL_TEXT_RATIO
        )
    except ColorError:
        colors = nearest_accessible_color(
            "#ffffff", answer.background_color, target=NORMAL_TEXT_RATIO
        )
    banner = lxml.html.Element("div", {"class": "ga-text-banner"})
    for line in answer.visible_text:
        etree.SubElement(banner, "p", {"class": "ga-text-banner__line"}).text = line
    planner.add(
        "text_in_image",
        url,
        target=el.get("src") or url,
        description=(
            "AI-generated, review before use. Replace the banner image with its own words "
            f"as real HTML text ({len(answer.visible_text)} line(s)): readable by screen "
            "readers, resizable, and a few hundred bytes instead of an image download."
        ),
        before=dom.opening_tag(el),
        after=dom.outer_html(banner),
        ai=True,
        confidence=answer.confidence,
        el=el,
        params={
            "url": url,
            "lines": answer.visible_text,
            "background": colors.background,
            "color": colors.color,
        },
    )


def _media_fixes(planner: _Planner, result: ScanResult) -> None:
    for media in result.carbon.autoplay_media:
        if media.kind != "video":
            planner.manual(
                "autoplay_video",
                media.url,
                media.url,
                "heavy animated GIF: replace it with a video with controls, or a still image.",
            )
            continue
        url = dom.normalise_url(media.url)
        el = dom.locate(planner.tree, src=url, base_url=planner.page_url, selector=media.selector)
        if el is None or el.tag.lower() != "video":
            planner.manual(
                "autoplay_video",
                url,
                media.url,
                "the video is not a plain <video src> in the source HTML.",
            )
            continue
        planner.add(
            "autoplay_video",
            url,
            target=_describe(el),
            description=(
                f'Stop autoplay: remove autoplay, add controls and preload="none", so the '
                f"{_kb(media.bytes)} video downloads only if someone presses play. A poster "
                "frame is added when ffmpeg is available."
            ),
            before=dom.opening_tag(el),
            after=_preview(
                el,
                set_attrs={"preload": "none", "controls": "", "poster": poster_path(url)},
                drop_attrs=("autoplay",),
            ),
            el=el,
            params={"url": url},
        )


def _third_party_fixes(planner: _Planner) -> None:
    for el in dom.iter_elements(planner.tree.getroot()):
        if el.tag.lower() != "script" or not el.get("src"):
            continue
        url = dom.normalise_url(el.get("src") or "", planner.page_url)
        if not is_third_party(url, planner.page_url):
            continue
        if planner.allow.script_removal_allowed(planner.page_url, url):
            planner.add(
                "third_party_remove",
                url,
                target=url,
                description=(
                    "Remove this third-party tracker script (on the demo allow-list of "
                    "removable scripts)."
                ),
                before=dom.opening_tag(el),
                after="",
                el=el,
                params={"url": url},
            )
        else:
            planner.manual(
                "third_party_remove",
                url,
                url,
                "third-party script. Load it on demand (after consent or interaction) or "
                "remove it if unused; only allow-listed demo trackers are removed automatically.",
                el=el,
            )


_ROOT_BLOCK: Final[re.Pattern[str]] = re.compile(r":root\s*\{([^}]*)\}", re.IGNORECASE)
_BG_NAME: Final[re.Pattern[str]] = re.compile(r"^--[\w-]*(bg|background|surface|canvas)[\w-]*$")
_TEXT_NAME: Final[re.Pattern[str]] = re.compile(r"^--[\w-]*(text|ink|fg|foreground)[\w-]*$")


async def _dark_mode_fix(planner: _Planner, fetcher: SafeFetcher) -> None:
    css_texts: list[str] = [
        el.text or "" for el in dom.iter_elements(planner.tree.getroot()) if el.tag == "style"
    ]
    for el in dom.iter_elements(planner.tree.getroot()):
        if el.tag.lower() == "link" and "stylesheet" in (el.get("rel") or "").lower():
            url = dom.normalise_url(el.get("href") or "", planner.page_url)
            if is_third_party(url, planner.page_url):
                continue
            try:
                css_texts.append((await fetcher.get(url)).body.decode("utf-8", errors="replace"))
            except FetchError:
                continue
    variables: dict[str, str] = {}
    for css in css_texts:
        for block in _ROOT_BLOCK.findall(css):
            for name in re.findall(r"(--[\w-]+)\s*:", block):
                if _BG_NAME.match(name):
                    variables[name] = DARK_BACKGROUND
                elif _TEXT_NAME.match(name):
                    variables[name] = DARK_TEXT
    has_both = DARK_BACKGROUND in variables.values() and DARK_TEXT in variables.values()
    suggestion = (
        "@media (prefers-color-scheme: dark) {\n  :root { color-scheme: dark; }\n"
        f"  body {{ background: {DARK_BACKGROUND}; color: {DARK_TEXT}; }}\n}}"
    )
    if has_both:
        body = "\n".join(f"    {k}: {v};" for k, v in sorted(variables.items()))
        planner.add(
            "dark_mode_tokens",
            "document",
            target="greenaccess-patch.css",
            description=(
                "Honour prefers-color-scheme: dark by overriding the page's own colour "
                f"variables ({', '.join(sorted(variables))}) with #121212 / #E8E8E8."
            ),
            after=f"@media (prefers-color-scheme: dark) {{\n  :root {{\n{body}\n  }}\n}}",
            confidence=0.6,
            params={"variables": variables},
        )
    else:
        planner.add(
            "dark_mode_tokens",
            "document",
            target="greenaccess-patch.css",
            description=(
                "Suggestion only: the page has no colour variables to override, so a dark "
                "theme needs design work. A starting point is shown; check every colour "
                "pair for contrast before shipping it."
            ),
            after=suggestion,
            confidence=0.0,
            manual=True,
        )
