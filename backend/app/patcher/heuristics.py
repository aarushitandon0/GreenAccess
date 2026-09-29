"""Deterministic, context-derived fix values (no LLM).

Single responsibility: propose an accessible name or a document language from
what the page itself says, for when the LLM is unavailable or unsure.

These are the "from context" and "detect" options MASTERSPEC §8.1 names for
kinds 2, 3 and 4. They only ever read the element and its immediate
surroundings: a visible adjacent label, a placeholder, a ``name``/``id``, a
link's fragment, a control's class names or click handler. Fixes built from
them carry ``ai_generated: false`` and a deliberately modest confidence, and
when nothing trustworthy is found they return None and the finding is left
for manual review rather than guessed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

from lxml import etree

from app.patcher.dom import visible_text

__all__ = [
    "LabelProposal",
    "LangGuess",
    "control_name_from_context",
    "detect_language",
    "form_label_from_context",
    "humanise",
]

#: Words a control's class, id or handler must contain to be trusted as its name.
_ACTION_WORDS: Final[dict[str, str]] = {
    "close": "Close",
    "dismiss": "Dismiss",
    "search": "Search",
    "menu": "Menu",
    "next": "Next",
    "prev": "Previous",
    "previous": "Previous",
    "play": "Play",
    "pause": "Pause",
    "share": "Share",
    "cart": "Cart",
    "login": "Log in",
    "signin": "Sign in",
    "home": "Home",
    "back": "Back",
    "open": "Open",
    "expand": "Expand",
    "collapse": "Collapse",
    "subscribe": "Subscribe",
    "download": "Download",
}

_FIELD_NAMES: Final[dict[str, str]] = {
    "email": "Email address",
    "e-mail": "Email address",
    "tel": "Phone number",
    "phone": "Phone number",
    "q": "Search",
    "query": "Search",
    "search": "Search",
    "fname": "First name",
    "lname": "Last name",
    "name": "Name",
    "zip": "Postcode",
    "postcode": "Postcode",
}


def humanise(raw: str) -> str:
    """``first_name`` / ``firstName`` / ``first-name`` → ``First name``."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
    words = [w for w in re.split(r"[\s_\-.\[\]]+", spaced) if w]
    if not words:
        return ""
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:]


@dataclass(frozen=True)
class LabelProposal:
    text: str
    #: ``label-for``: turn an adjacent text element into ``<label for>``;
    #: ``aria-label``: set the attribute on the control.
    mode: str
    confidence: float
    source: str
    #: The adjacent element to convert, for ``label-for``.
    label_element: etree._Element | None = None


def _adjacent_text_element(control: etree._Element) -> etree._Element | None:
    """A sibling span/div right after a checkbox or radio that holds its text."""
    sibling = control.getnext()
    while sibling is not None and not isinstance(sibling.tag, str):
        sibling = sibling.getnext()
    if sibling is None or sibling.tag.lower() not in {"span", "div", "p"}:
        return None
    if len(sibling) or not (sibling.text or "").strip():
        return None
    return sibling


def form_label_from_context(control: etree._Element) -> LabelProposal | None:
    tag = control.tag.lower()
    kind = (control.get("type") or "").lower()
    if tag == "input" and kind in {"checkbox", "radio"}:
        adjacent = _adjacent_text_element(control)
        if adjacent is not None:
            text = re.sub(r"\s+", " ", adjacent.text or "").strip()
            return LabelProposal(text, "label-for", 0.85, "the text next to it", adjacent)
        tail = re.sub(r"\s+", " ", control.tail or "").strip()
        if tail:
            return LabelProposal(tail[:80], "aria-label", 0.75, "the text next to it")
    placeholder = (control.get("placeholder") or "").strip()
    if placeholder:
        return LabelProposal(placeholder[:80], "aria-label", 0.6, "its placeholder")
    for attribute in ("name", "id"):
        raw = (control.get(attribute) or "").strip()
        if not raw:
            continue
        known = _FIELD_NAMES.get(raw.lower())
        if kind == "email" and not known:
            known = "Email address"
        text = known or humanise(raw)
        if text:
            return LabelProposal(text, "aria-label", 0.5, f"its {attribute} attribute")
    return None


def _words(value: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def control_name_from_context(el: etree._Element) -> tuple[str, float, str] | None:
    """A name for an icon-only link or button: (name, confidence, source)."""
    title = (el.get("title") or "").strip()
    if title:
        return title[:80], 0.8, "its title attribute"

    tag = el.tag.lower()
    if tag == "a":
        href = (el.get("href") or "").strip()
        parts = urlsplit(href)
        target = parts.fragment or unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
        target = re.sub(r"\.\w{1,5}$", "", target)
        if target and not target.isdigit():
            name = humanise(target)
            if name:
                return name, 0.55, "where it links to"

    handler = el.get("onclick") or ""
    match = re.match(r"\s*([A-Za-z_$][\w$]*)\s*\(", handler)
    if match:
        words = _words(match.group(1))
        if words and words[0] in _ACTION_WORDS:
            rest = " ".join(words[1:])
            name = _ACTION_WORDS[words[0]] + (f" {rest}" if rest else "")
            return name, 0.55, "its click handler"

    tokens = _words(" ".join([el.get("class") or "", el.get("id") or ""]))
    for index, token in enumerate(tokens):
        if token in _ACTION_WORDS:
            # BEM "chat__close": the block before the action names the object.
            subject = (
                tokens[index - 1] if index > 0 and tokens[index - 1] not in _ACTION_WORDS else ""
            )
            name = _ACTION_WORDS[token] + (f" {subject}" if subject else "")
            return name, 0.5, "its class names"
    return None


# --------------------------------------------------------------------------- #
# Language
# --------------------------------------------------------------------------- #

#: High-frequency function words per language. A short, fixed list is enough
#: to separate these languages on a page's worth of text, and it is auditable.
_STOPWORDS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset(
        [
            "the",
            "and",
            "of",
            "to",
            "a",
            "in",
            "is",
            "that",
            "for",
            "it",
            "on",
            "with",
            "as",
            "was",
            "by",
            "at",
            "are",
            "this",
            "from",
        ]
    ),
    "fr": frozenset(
        [
            "le",
            "la",
            "les",
            "et",
            "des",
            "est",
            "une",
            "un",
            "du",
            "que",
            "pour",
            "dans",
            "qui",
            "sur",
            "pas",
            "au",
            "avec",
        ]
    ),
    "de": frozenset(
        [
            "der",
            "die",
            "das",
            "und",
            "ist",
            "nicht",
            "ein",
            "eine",
            "zu",
            "den",
            "von",
            "mit",
            "sich",
            "auf",
            "für",
            "im",
        ]
    ),
    "es": frozenset(
        [
            "el",
            "la",
            "los",
            "las",
            "y",
            "de",
            "que",
            "en",
            "un",
            "una",
            "es",
            "por",
            "con",
            "para",
            "del",
            "se",
            "al",
        ]
    ),
    "it": frozenset(
        [
            "il",
            "lo",
            "la",
            "gli",
            "le",
            "e",
            "di",
            "che",
            "un",
            "una",
            "per",
            "non",
            "con",
            "del",
            "della",
            "sono",
        ]
    ),
    "pt": frozenset(
        [
            "o",
            "a",
            "os",
            "as",
            "e",
            "de",
            "que",
            "em",
            "um",
            "uma",
            "para",
            "com",
            "não",
            "do",
            "da",
            "por",
            "são",
        ]
    ),
    "nl": frozenset(
        [
            "de",
            "het",
            "een",
            "en",
            "van",
            "is",
            "dat",
            "op",
            "te",
            "in",
            "voor",
            "niet",
            "met",
            "zijn",
            "aan",
        ]
    ),
}

#: Minimum stopword hits, and how far ahead of the runner-up the winner must be.
_MIN_HITS: Final[int] = 12
_MIN_LEAD: Final[float] = 2.0


@dataclass(frozen=True)
class LangGuess:
    lang: str | None
    confidence: float
    hits: dict[str, int]


def detect_language(root: etree._Element) -> LangGuess:
    """Guess the page language from its visible text; lang None if unsure."""
    body = root.find(".//body")
    text = visible_text(body if body is not None else root, limit=20_000)
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    hits = Counter()
    for lang, stops in _STOPWORDS.items():
        hits[lang] = sum(1 for w in words if w in stops)
    ranked = hits.most_common()
    if not ranked or ranked[0][1] < _MIN_HITS:
        return LangGuess(None, 0.0, dict(hits))
    best, best_hits = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best_hits < _MIN_LEAD * max(runner_up, 1):
        return LangGuess(None, 0.0, dict(hits))
    confidence = min(0.95, 0.6 + 0.35 * (1 - runner_up / best_hits))
    return LangGuess(best, round(confidence, 2), dict(hits))
