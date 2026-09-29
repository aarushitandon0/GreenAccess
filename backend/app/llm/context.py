"""The only page content a prompt may carry (CLAUDE.md, MASTERSPEC §8.2).

Single responsibility: cut a small, bounded excerpt around one offending
element out of the page, with lxml: the element itself, at most two levels of
parent context (as opening tags), and a little nearby visible text.

"Never send full page HTML to the LLM" is enforced here, structurally: the
excerpt is assembled from opening tags and capped text, never from a subtree
larger than the element, and :func:`assert_prompt_is_bounded` rejects any
prompt that still grows past the cap or contains document-level markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from lxml import etree

from app.patcher.dom import opening_tag, outer_html, visible_text

__all__ = [
    "MAX_ELEMENT_CHARS",
    "MAX_PARENT_LEVELS",
    "MAX_PROMPT_CHARS",
    "ElementContext",
    "PromptTooLarge",
    "assert_prompt_is_bounded",
    "extract_context",
]

#: MASTERSPEC §8.2: "≤ 2 levels of parent context".
MAX_PARENT_LEVELS: Final[int] = 2
MAX_ELEMENT_CHARS: Final[int] = 600
MAX_NEARBY_CHARS: Final[int] = 300
#: Hard ceiling on the text part of any prompt built from page content.
MAX_PROMPT_CHARS: Final[int] = 6_000

_DOCUMENT_TAGS: Final[re.Pattern[str]] = re.compile(r"<\s*(body|head|!doctype)\b", re.IGNORECASE)


class PromptTooLarge(ValueError):
    """A prompt exceeded the page-content bounds; it must not be sent."""


@dataclass(frozen=True)
class ElementContext:
    """What the model may see about one element."""

    #: The offending element, content included, cut at MAX_ELEMENT_CHARS.
    element: str
    #: Opening tags of the parent, then the grandparent (≤ 2 entries).
    ancestors: tuple[str, ...]
    #: Visible text around the element, excluding the element's own text.
    nearby_text: str

    def render(self) -> str:
        lines = ["<element>", self.element, "</element>"]
        for depth, tag in enumerate(self.ancestors, start=1):
            lines.append(f"<ancestor level={depth}>{tag}</ancestor>")
        if self.nearby_text:
            lines += ["<nearby_text>", self.nearby_text, "</nearby_text>"]
        return "\n".join(lines)


def extract_context(el: etree._Element, *, levels: int = MAX_PARENT_LEVELS) -> ElementContext:
    """Bounded context for `el`: itself, ≤ `levels` ancestors, nearby text."""
    levels = min(levels, MAX_PARENT_LEVELS)
    ancestors: list[str] = []
    node = el
    scope = el
    for _ in range(levels):
        parent = node.getparent()
        if parent is None or not isinstance(parent.tag, str) or parent.tag.lower() == "html":
            break
        if parent.tag.lower() in {"body", "head"}:
            # Stop at the document level: the body's text is the whole page.
            break
        ancestors.append(opening_tag(parent))
        scope = parent
        node = parent

    if el.tag == "html":
        element = opening_tag(el)
        nearby = ""
    else:
        element = outer_html(el, limit=MAX_ELEMENT_CHARS)
        nearby = visible_text(scope, limit=MAX_NEARBY_CHARS, skip=el) if scope is not el else ""
    return ElementContext(element=element, ancestors=tuple(ancestors), nearby_text=nearby)


def assert_prompt_is_bounded(prompt: str) -> None:
    """Refuse a prompt that could be carrying the page rather than an excerpt."""
    if len(prompt) > MAX_PROMPT_CHARS:
        raise PromptTooLarge(f"prompt is {len(prompt):,} characters (limit {MAX_PROMPT_CHARS:,})")
    if _DOCUMENT_TAGS.search(prompt):
        raise PromptTooLarge("prompt contains document-level markup (<body>, <head> or doctype)")
