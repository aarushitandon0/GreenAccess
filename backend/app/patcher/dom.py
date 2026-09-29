"""Parse, locate and serialise elements of a page's source HTML with lxml.

Single responsibility: the DOM plumbing every fix needs. Find the source
element a scan finding points at, give it a stable address, and print it.

Why locating is not just "run the selector": axe and the carbon detectors
describe the *live* DOM, after the page's scripts ran, while the patcher edits
the *source* HTML the server sends. Scripts add attributes (the demo adds
``data-card-index`` to every card), so a live selector can match nothing in the
source. An element is therefore located from every clue available, strictest
first: the opening tag axe recorded, a resolved ``src``, and the selector
(retried without attribute tests on ``data-*``, which scripts commonly add).
Anything not pinned to exactly one element is reported as not located, and
the fix is skipped with that reason instead of being applied to a guess.

The selector engine covers the CSS subset axe and our DOM collector emit:
type, ``#id``, ``.class``, ``[attr]``, ``[attr=value]``, ``:nth-child(n)``,
``:nth-of-type(n)``, ``:first-child``, ``:last-child``, and the descendant
and child combinators. ``cssselect`` is not a dependency of this project.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

import lxml.html
from lxml import etree

__all__ = [
    "ElementPath",
    "SelectorError",
    "element_at",
    "element_path",
    "iter_elements",
    "locate",
    "normalise_url",
    "opening_tag",
    "outer_html",
    "parse_document",
    "select",
    "serialise_document",
    "visible_text",
]

ElementPath = tuple[int, ...]

#: Attributes a page's scripts commonly add at runtime; ignored when an exact
#: attribute match of the recorded snippet finds nothing in the source.
_VOLATILE_ATTR: Final[re.Pattern[str]] = re.compile(r"^(data-.*|style)$")

_NON_TEXT_TAGS: Final[frozenset[str]] = frozenset(
    {"script", "style", "noscript", "template", "svg", "head"}
)


class SelectorError(ValueError):
    """The selector uses syntax outside the supported subset."""


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


def parse_document(html: str) -> etree._ElementTree:
    """Parse a whole document, keeping its doctype."""
    root = lxml.html.document_fromstring(html)
    return root.getroottree()


def serialise_document(tree: etree._ElementTree) -> str:
    doctype = tree.docinfo.doctype or "<!DOCTYPE html>"
    body = lxml.html.tostring(tree.getroot(), encoding="unicode", method="html")
    return f"{doctype}\n{body}\n"


def iter_elements(root: etree._Element) -> Iterator[etree._Element]:
    """Every element (comments and processing instructions skipped)."""
    for node in root.iter():
        if isinstance(node.tag, str):
            yield node


def _element_children(el: etree._Element) -> list[etree._Element]:
    return [child for child in el if isinstance(child.tag, str)]


def element_path(el: etree._Element) -> ElementPath:
    """Child-index address of `el` from the root; stable for the same source."""
    path: list[int] = []
    node = el
    while (parent := node.getparent()) is not None:
        path.append(_element_children(parent).index(node))
        node = parent
    return tuple(reversed(path))


def element_at(tree: etree._ElementTree, path: ElementPath) -> etree._Element | None:
    node = tree.getroot()
    for index in path:
        children = _element_children(node)
        if index >= len(children):
            return None
        node = children[index]
    return node


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def opening_tag(el: etree._Element) -> str:
    """``<tag attr="…">`` for `el`, children and text omitted."""
    shell = lxml.html.Element(el.tag, dict(el.attrib))
    html = lxml.html.tostring(shell, encoding="unicode", method="html")
    closing = f"</{el.tag}>"
    return html[: -len(closing)] if html.endswith(closing) else html


def outer_html(el: etree._Element, *, limit: int = 600) -> str:
    """`el` with its content, comments dropped, cut at `limit` characters."""
    html = lxml.html.tostring(el, encoding="unicode", method="html", with_tail=False)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"\s+", " ", html).strip()
    return html if len(html) <= limit else html[: limit - 1] + "…"


def visible_text(
    el: etree._Element, *, limit: int = 300, skip: etree._Element | None = None
) -> str:
    """Text a reader would see inside `el`, whitespace collapsed.

    `skip` excludes one descendant (the offending element itself) so "nearby
    text" does not repeat what the element already says.
    """
    parts: list[str] = []

    def walk(node: etree._Element) -> None:
        if node is skip:
            return
        if isinstance(node.tag, str) and node.tag.lower() in _NON_TEXT_TAGS:
            return
        if isinstance(node.tag, str) and node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #


def normalise_url(url: str, base: str = "") -> str:
    """Absolute URL without fragment, default ports dropped, for comparison."""
    absolute = urljoin(base, url.strip()) if base else url.strip()
    parts = urlsplit(absolute)
    host = (parts.hostname or "").lower()
    port = parts.port
    default = {"http": 80, "https": 443}.get(parts.scheme)
    netloc = host if port in (None, default) else f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


# --------------------------------------------------------------------------- #
# Selector subset
# --------------------------------------------------------------------------- #

_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"""
    (?P<ws>\s*>\s*|\s+)                                  # combinator
    | (?P<tag>\*|[a-zA-Z][\w-]*)
    | \#(?P<id>(?:\\.|[\w-])+)
    | \.(?P<cls>(?:\\.|[\w-])+)
    | \[\s*(?P<attr>[\w:-]+)\s*
        (?:(?P<op>[~|^$*]?=)\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\]\s]+))\s*)?\]
    | :(?P<pseudo>nth-child|nth-of-type)\(\s*(?P<n>\d+)\s*\)
    | :(?P<simple>first-child|last-child)
    """,
    re.VERBOSE,
)


@dataclass
class _Compound:
    tag: str | None = None
    ids: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    attrs: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    nth_child: int | None = None
    nth_of_type: int | None = None
    first: bool = False
    last: bool = False


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _parse_selector(selector: str) -> list[tuple[str, _Compound]]:
    """``[(combinator, compound), …]`` left to right; first combinator is ``""``."""
    parts: list[tuple[str, _Compound]] = []
    combinator = ""
    current: _Compound | None = None
    pos = 0
    text = selector.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise SelectorError(f"unsupported selector syntax at {text[pos:]!r}")
        pos = match.end()
        if match.group("ws") is not None:
            if current is not None:
                parts.append((combinator, current))
                current = None
            combinator = ">" if ">" in match.group("ws") else " "
            continue
        if current is None:
            current = _Compound()
        if (tag := match.group("tag")) is not None:
            current.tag = None if tag == "*" else tag.lower()
        elif (ident := match.group("id")) is not None:
            current.ids.append(_unescape(ident))
        elif (cls := match.group("cls")) is not None:
            current.classes.append(_unescape(cls))
        elif (attr := match.group("attr")) is not None:
            op = match.group("op")
            value = match.group("dq")
            if value is None:
                value = match.group("sq")
            if value is None:
                value = match.group("bare")
            if op not in (None, "="):
                raise SelectorError(f"attribute operator {op!r} is not supported")
            current.attrs.append((attr.lower(), op, value))
        elif match.group("pseudo") == "nth-child":
            current.nth_child = int(match.group("n"))
        elif match.group("pseudo") == "nth-of-type":
            current.nth_of_type = int(match.group("n"))
        elif match.group("simple") == "first-child":
            current.first = True
        elif match.group("simple") == "last-child":
            current.last = True
    if current is None:
        raise SelectorError(f"empty or dangling selector {selector!r}")
    parts.append((combinator, current))
    return parts


def _matches(el: etree._Element, comp: _Compound) -> bool:
    if not isinstance(el.tag, str):
        return False
    tag = el.tag.lower()
    if comp.tag is not None and tag != comp.tag:
        return False
    if comp.ids and any(el.get("id") != ident for ident in comp.ids):
        return False
    if comp.classes:
        have = set((el.get("class") or "").split())
        if not set(comp.classes) <= have:
            return False
    for name, op, value in comp.attrs:
        actual = el.get(name)
        if actual is None or (op == "=" and actual != value):
            return False
    if comp.nth_child or comp.nth_of_type or comp.first or comp.last:
        parent = el.getparent()
        siblings = _element_children(parent) if parent is not None else [el]
        if comp.nth_child and siblings.index(el) + 1 != comp.nth_child:
            return False
        if comp.first and siblings[0] is not el:
            return False
        if comp.last and siblings[-1] is not el:
            return False
        if comp.nth_of_type:
            same = [s for s in siblings if isinstance(s.tag, str) and s.tag.lower() == tag]
            if same.index(el) + 1 != comp.nth_of_type:
                return False
    return True


def _matches_chain(el: etree._Element, chain: list[tuple[str, _Compound]]) -> bool:
    combinator, comp = chain[-1]
    if not _matches(el, comp):
        return False
    if len(chain) == 1:
        return True
    rest = chain[:-1]
    parent = el.getparent()
    if combinator == ">":
        return parent is not None and _matches_chain(parent, rest)
    while parent is not None:
        if _matches_chain(parent, rest):
            return True
        parent = parent.getparent()
    return False


def _split_list(selector: str) -> list[str]:
    parts, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate(selector):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(selector[start:i])
            start = i + 1
    parts.append(selector[start:])
    return [p.strip() for p in parts if p.strip()]


def select(tree: etree._ElementTree, selector: str) -> list[etree._Element]:
    """Elements matching `selector`, document order. Raises SelectorError."""
    chains = [_parse_selector(part) for part in _split_list(selector)]
    return [
        el for el in iter_elements(tree.getroot()) if any(_matches_chain(el, c) for c in chains)
    ]


def _without_volatile_attr_tests(selector: str) -> str:
    return re.sub(r"\[\s*data-[\w-]*\s*(?:=\s*(?:\"[^\"]*\"|'[^']*'|[^\]]*))?\]", "", selector)


# --------------------------------------------------------------------------- #
# Locating a finding's element
# --------------------------------------------------------------------------- #

_OPENING: Final[re.Pattern[str]] = re.compile(
    r"<\s*([a-zA-Z][\w-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>"
)


def _snippet_attrs(snippet: str) -> tuple[str, dict[str, str]] | None:
    match = _OPENING.search(snippet or "")
    if match is None:
        return None
    tag = match.group(1).lower()
    if tag in {"html", "head", "body"}:
        return tag, {}
    try:
        shell = lxml.html.fragment_fromstring(f"<{tag}{match.group(2)}></{tag}>")
    except etree.ParserError:
        return None
    if not isinstance(shell.tag, str) or shell.tag.lower() != tag:
        return None
    return tag, {k.lower(): v for k, v in shell.attrib.items()}


def _by_snippet(tree: etree._ElementTree, snippet: str) -> list[etree._Element] | None:
    parsed = _snippet_attrs(snippet)
    if parsed is None:
        return None
    tag, attrs = parsed
    if tag == "html":
        return [tree.getroot()]
    candidates = [el for el in iter_elements(tree.getroot()) if el.tag.lower() == tag]
    exact = [el for el in candidates if all(el.get(k) == v for k, v in attrs.items())]
    if exact:
        return exact
    stable = {k: v for k, v in attrs.items() if not _VOLATILE_ATTR.match(k)}
    return [el for el in candidates if all(el.get(k) == v for k, v in stable.items())]


def _by_selector(tree: etree._ElementTree, selector: str) -> list[etree._Element] | None:
    for attempt in (selector, _without_volatile_attr_tests(selector)):
        try:
            found = select(tree, attempt)
        except SelectorError:
            return None
        if found:
            return found
    return []


def _by_src(tree: etree._ElementTree, src: str, base_url: str) -> list[etree._Element]:
    wanted = normalise_url(src, base_url)
    found: list[etree._Element] = []
    for el in iter_elements(tree.getroot()):
        if el.tag.lower() not in {"img", "video", "audio", "source", "script"}:
            continue
        raw = el.get("src")
        if raw and normalise_url(raw, base_url) == wanted:
            found.append(el)
    return found


def locate(
    tree: etree._ElementTree,
    *,
    snippet: str = "",
    selector: str = "",
    src: str = "",
    base_url: str = "",
) -> etree._Element | None:
    """The one source element the clues point at, or None if not exactly one."""
    pools: list[list[etree._Element]] = []
    if src:
        pools.append(_by_src(tree, src, base_url))
    if snippet:
        by_snippet = _by_snippet(tree, snippet)
        if by_snippet is not None:
            pools.append(by_snippet)
    by_selector = _by_selector(tree, selector) if selector else None

    candidates: list[etree._Element] | None = None
    for pool in pools:
        candidates = pool if candidates is None else [el for el in candidates if el in pool]
    if candidates is None:
        return by_selector[0] if by_selector is not None and len(by_selector) == 1 else None
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 and by_selector:
        narrowed = [el for el in candidates if el in by_selector]
        if len(narrowed) == 1:
            return narrowed[0]
        candidates = narrowed or candidates
    if len(candidates) > 1 and snippet:
        # Last resort: the recorded markup itself, content included.
        wanted = re.sub(r"\s+", " ", snippet).strip().rstrip("…")
        same = [el for el in candidates if outer_html(el, limit=10_000).startswith(wanted)]
        if len(same) == 1:
            return same[0]
    return None


def unique(elements: Iterable[etree._Element]) -> list[etree._Element]:
    """De-duplicate by identity, keeping order."""
    seen: set[int] = set()
    out: list[etree._Element] = []
    for el in elements:
        if id(el) not in seen:
            seen.add(id(el))
            out.append(el)
    return out
