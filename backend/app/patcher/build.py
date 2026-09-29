"""Build the patched copy of a page (MASTERSPEC §8.3).

Single responsibility: from the saved page source and the accepted fixes,
write ``patched/{scan_id}/`` (index.html, same-origin assets, optimised
images, ``greenaccess-patch.css``, ``CHANGES.md``) and its zip.

Order of work:

1. Optimise the images accepted ``image_compress`` fixes point at, and make a
   poster for an accepted ``autoplay_video`` fix when ffmpeg exists.
2. Apply every accepted fix in :data:`~app.patcher.transforms.KIND_ORDER`.
   Targets are resolved *before* any change, so one fix removing an element
   cannot shift another fix onto the wrong one.
3. Copy the same-origin assets the patched page still references, and rewrite
   those references to relative paths (no ``<base href>``, MASTERSPEC §8.3),
   inside HTML attributes, ``srcset``, inline styles, ``<style>`` blocks and
   stylesheets (``url()`` and ``@import``, recursively).
4. Write ``CHANGES.md`` (applied and skipped fixes, with reasons) and zip.

Third-party references are left absolute. The preview's Content Security
Policy (built here, served by the API) allows their images, styles, fonts,
media and frames, and, as MASTERSPEC §8.3 requires, only same-origin scripts.
Nothing here executes page content; the output is static files.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urljoin, urlsplit

import lxml.html
from lxml import etree

from app.models import Fix, SkippedFix
from app.patcher import dom
from app.patcher.allowlist import DemoAllowList, load_allowlist
from app.patcher.images import ImageSkipped, make_poster, optimize_image
from app.patcher.paths import mirrored_path, optimized_path, poster_path, relative_to
from app.patcher.plan import FixPlan, PlannedFix
from app.patcher.transforms import (
    KIND_ORDER,
    OptimizedAsset,
    PatchContext,
    SkipFix,
    apply_fix,
    resolve_path,
)
from app.security.fetch import FetchError, SafeFetcher

logger = logging.getLogger(__name__)

__all__ = ["PATCH_CSS", "BuildResult", "build_csp", "build_patch"]

PATCH_CSS: Final[str] = "greenaccess-patch.css"
CHANGES_MD: Final[str] = "CHANGES.md"
MAX_ASSETS: Final[int] = 400
_CSS_DEPTH: Final[int] = 3

_CSS_URL: Final[re.Pattern[str]] = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_CSS_IMPORT: Final[re.Pattern[str]] = re.compile(r"@import\s+(['\"])(.*?)\1", re.IGNORECASE)

#: (tag, attribute, CSP directive for a third-party origin, is a stylesheet)
_REFS: Final[tuple[tuple[str, str, str | None], ...]] = (
    ("img", "src", "img-src"),
    ("img", "srcset", "img-src"),
    ("source", "src", "media-src"),
    ("source", "srcset", "img-src"),
    ("video", "src", "media-src"),
    ("video", "poster", "img-src"),
    ("audio", "src", "media-src"),
    ("track", "src", "media-src"),
    ("input", "src", "img-src"),
    ("script", "src", None),
    ("iframe", "src", "frame-src"),
)


@dataclass
class BuildResult:
    patch_dir: Path
    zip_path: Path
    fixes: list[Fix]
    skipped: list[SkippedFix]
    csp: str
    applied_count: int
    notes: list[str] = field(default_factory=list)


def build_csp(third_party: dict[str, set[str]]) -> str:
    """The preview CSP: MASTERSPEC §8.3's base, plus observed non-script origins."""

    def sources(directive: str, *base: str) -> str:
        extra = sorted(third_party.get(directive, set()))
        return " ".join([directive, *base, *extra])

    return "; ".join(
        [
            "default-src 'self' data:",
            "script-src 'self'",
            sources("style-src", "'self'", "'unsafe-inline'"),
            sources("img-src", "'self'", "data:"),
            sources("font-src", "'self'", "data:"),
            sources("media-src", "'self'", "data:"),
            sources("frame-src", "'self'"),
            "connect-src 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        ]
    )


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@dataclass
class _AssetCopier:
    """Downloads same-origin assets into the patch dir and rewrites references."""

    page_url: str
    out_dir: Path
    fetcher: SafeFetcher
    third_party: dict[str, set[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    _done: dict[str, str | None] = field(default_factory=dict)

    def _same_origin(self, url: str) -> bool:
        return _origin(url) == _origin(self.page_url)

    async def local_path(self, url: str, *, css_depth: int = 0) -> str | None:
        """Patch-relative path of `url`'s copy, downloading it once; None on failure."""
        key = dom.normalise_url(url)
        if key in self._done:
            return self._done[key]
        if len(self._done) >= MAX_ASSETS:
            self.notes.append(f"asset limit of {MAX_ASSETS} reached; {url} left as is")
            self._done[key] = None
            return None
        self._done[key] = None
        try:
            fetched = await self.fetcher.get(key)
        except FetchError as exc:
            self.notes.append(f"could not copy {url}: {exc}")
            return None
        if fetched.status >= 400:
            self.notes.append(f"could not copy {url}: HTTP {fetched.status}")
            return None
        rel = mirrored_path(key)
        body = fetched.body
        is_css = "css" in fetched.content_type or key.lower().endswith(".css")
        if is_css and css_depth < _CSS_DEPTH:
            text = body.decode("utf-8", errors="replace")
            body = (
                await self.rewrite_css(text, base_url=key, file_rel=rel, depth=css_depth + 1)
            ).encode("utf-8")
        target = self.out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self._done[key] = rel
        return rel

    async def rewrite_url(
        self, raw: str, *, base_url: str, file_rel: str, directive: str | None, css_depth: int = 0
    ) -> str:
        value = raw.strip()
        if not value or value.startswith(
            ("data:", "#", "about:", "javascript:", "mailto:", "tel:")
        ):
            return raw
        if value.startswith(f"{PATCH_CSS}") or value.startswith("assets/optimized/"):
            return raw  # produced by the patcher itself
        absolute = urljoin(base_url, value)
        if urlsplit(absolute).scheme not in ("http", "https"):
            return raw
        if not self._same_origin(absolute):
            if directive is not None:
                self.third_party.setdefault(directive, set()).add(_origin(absolute))
            return absolute
        local = await self.local_path(absolute, css_depth=css_depth)
        if local is None:
            return absolute
        fragment = urlsplit(absolute).fragment
        return relative_to(local, file_rel) + (f"#{fragment}" if fragment else "")

    async def rewrite_srcset(self, value: str, *, directive: str) -> str:
        out: list[str] = []
        for candidate in value.split(","):
            parts = candidate.strip().split()
            if not parts:
                continue
            parts[0] = await self.rewrite_url(
                parts[0], base_url=self.page_url, file_rel="index.html", directive=directive
            )
            out.append(" ".join(parts))
        return ", ".join(out)

    async def rewrite_css(self, css: str, *, base_url: str, file_rel: str, depth: int) -> str:
        """Rewrite every ``@import`` and ``url()`` in `css` (fonts, images, sheets)."""

        async def replace_all(pattern: re.Pattern[str], text: str, directive: str) -> str:
            pieces: list[str] = []
            last = 0
            for match in pattern.finditer(text):
                new = await self.rewrite_url(
                    match.group(2),
                    base_url=base_url,
                    file_rel=file_rel,
                    directive=directive,
                    css_depth=depth,
                )
                start, end = match.span(2)
                pieces += [text[last:start], new]
                last = end
            pieces.append(text[last:])
            return "".join(pieces)

        css = await replace_all(_CSS_IMPORT, css, "style-src")
        css = await replace_all(_CSS_URL, css, "font-src")
        return css

    async def rewrite_document(self, tree: etree._ElementTree) -> None:
        root = tree.getroot()
        for el in list(dom.iter_elements(root)):
            tag = el.tag.lower()
            if tag == "link" and el.get("href"):
                rel = (el.get("rel") or "").lower()
                directive = "style-src" if "stylesheet" in rel else "img-src"
                el.set(
                    "href",
                    await self.rewrite_url(
                        el.get("href") or "",
                        base_url=self.page_url,
                        file_rel="index.html",
                        directive=directive,
                    ),
                )
            for ref_tag, attr, directive in _REFS:
                if tag != ref_tag or el.get(attr) is None:
                    continue
                value = el.get(attr) or ""
                if attr == "srcset":
                    el.set(attr, await self.rewrite_srcset(value, directive=directive or "img-src"))
                else:
                    el.set(
                        attr,
                        await self.rewrite_url(
                            value,
                            base_url=self.page_url,
                            file_rel="index.html",
                            directive=directive,
                        ),
                    )
            if el.get("style") and "url(" in (el.get("style") or ""):
                el.set(
                    "style",
                    await self.rewrite_css(
                        el.get("style") or "",
                        base_url=self.page_url,
                        file_rel="index.html",
                        depth=1,
                    ),
                )
            if tag == "style" and el.text:
                el.text = await self.rewrite_css(
                    el.text, base_url=self.page_url, file_rel="index.html", depth=1
                )


async def _prepare_media(
    planned: list[PlannedFix], ctx: PatchContext, fetcher: SafeFetcher, out_dir: Path
) -> None:
    """Optimised images and posters, before any DOM change."""
    for item in planned:
        if item.fix.kind == "image_compress":
            url = dom.normalise_url(str(item.params.get("url", "")))
            if url in ctx.optimized or url in ctx.optimize_skipped:
                continue
            try:
                fetched = await fetcher.get(url)
                optimized = await asyncio.to_thread(
                    optimize_image, fetched.body, rendered_w=int(item.params.get("rendered_w") or 0)
                )
            except (FetchError, ImageSkipped) as exc:
                ctx.optimize_skipped[url] = str(exc)
                continue
            rel = optimized_path(url)
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(optimized.data)
            ctx.optimized[url] = OptimizedAsset(
                rel_path=rel,
                width=optimized.width,
                height=optimized.height,
                original_bytes=optimized.original_bytes,
                new_bytes=len(optimized.data),
            )
        elif item.fix.kind == "autoplay_video":
            url = dom.normalise_url(str(item.params.get("url", "")))
            try:
                video = await fetcher.get(url)
            except FetchError:
                continue
            poster = await asyncio.to_thread(make_poster, video.body)
            if poster is not None:
                rel = poster_path(url)
                target = out_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(poster)
                ctx.posters[url] = rel


def _changes_md(
    plan: FixPlan,
    patched_url: str,
    applied: list[Fix],
    skipped: list[tuple[Fix, str]],
    manual: list[Fix],
    ctx: PatchContext,
    notes: list[str],
    poster_missing: bool,
) -> str:
    usage = plan.ai_usage
    lines = [
        "# GreenAccess patch",
        "",
        f"- Page: {plan.page_url}",
        f"- Scan: `{plan.scan_id}`",
        f"- Built: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- Preview: {patched_url}",
        "",
        "Fixes marked **AI** are AI-generated: review before use. Results come from",
        "automated checks; they are not a statement of WCAG conformance. Carbon",
        "figures are estimates from the Sustainable Web Design model.",
        "",
        f"AI usage: {usage.live_calls} live call(s), {usage.cached_calls} cached, "
        f"{usage.failed_calls} failed; {usage.input_tokens:,} input / "
        f"{usage.output_tokens:,} output tokens billed"
        + (f" (model {usage.model})" if usage.model else "")
        + "."
        + (f" AI unavailable: {usage.unavailable_reason}." if usage.unavailable_reason else ""),
        "",
        f"## Applied ({len(applied)})",
        "",
    ]
    for fix in applied:
        origin = "AI" if fix.ai_generated else "rule"
        lines.append(
            f"- **{fix.kind}** [{origin}, confidence {fix.confidence:.2f}] `{fix.target}`: {fix.description}"
        )
    if ctx.optimized:
        before = sum(a.original_bytes for a in ctx.optimized.values())
        after = sum(a.new_bytes for a in ctx.optimized.values())
        lines += ["", f"Images re-encoded: {before:,} → {after:,} bytes on disk."]
    lines += ["", f"## Skipped ({len(skipped)})", ""]
    for fix, reason in skipped:
        lines.append(f"- **{fix.kind}** `{fix.target}` ({fix.id}): {reason}")
    lines += ["", f"## Manual fix needed ({len(manual)})", ""]
    for fix in manual:
        lines.append(f"- **{fix.kind}** `{fix.target}`: {fix.description}")
    lines += [
        "",
        "## Preview notes",
        "",
        "- The preview is served with a Content Security Policy that allows only",
        "  same-origin scripts (MASTERSPEC §8.3). Third-party scripts and inline",
        "  scripts that remain in the page do not run in the preview.",
    ]
    if poster_missing:
        lines.append("- No video poster was generated: ffmpeg is not installed on the server.")
    lines += [f"- {note}" for note in notes]
    return "\n".join(lines) + "\n"


def _fresh_dir(path: Path) -> None:
    """An empty directory at `path`: a rebuild never keeps stale files."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _zip(patch_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in patch_dir.rglob("*") if p.is_file()):
            archive.write(path, path.relative_to(patch_dir).as_posix())
    tmp.replace(zip_path)


async def build_patch(
    plan: FixPlan,
    source_html: str,
    accepted_ids: list[str],
    *,
    out_dir: Path,
    zip_path: Path,
    fetcher: SafeFetcher,
    patched_url: str,
    allowlist: DemoAllowList | None = None,
) -> BuildResult:
    await asyncio.to_thread(_fresh_dir, out_dir)

    tree = dom.parse_document(source_html)
    base_el = tree.getroot().find(".//base[@href]")
    page_url = urljoin(plan.page_url, base_el.get("href")) if base_el is not None else plan.page_url
    if base_el is not None:
        base_el.drop_tree()  # every URL is rewritten; §8.3 avoids <base href>

    by_id = plan.by_id()
    accepted = set(accepted_ids)
    chosen = [p for p in plan.fixes if p.fix.id in accepted]
    chosen.sort(
        key=lambda p: KIND_ORDER.index(p.fix.kind) if p.fix.kind in KIND_ORDER else len(KIND_ORDER)
    )

    ctx = PatchContext(page_url=page_url, allowlist=allowlist or load_allowlist())
    await _prepare_media(chosen, ctx, fetcher, out_dir)

    # Resolve every target before anything moves.
    targets = {p.fix.id: resolve_path(tree, p.path) for p in chosen}
    applied_ids: set[str] = set()
    skipped: list[tuple[Fix, str]] = []
    replaced: set[int] = set()
    for item in chosen:
        el = targets[item.fix.id]
        if el is not None and id(el) in replaced:
            skipped.append((item.fix, "superseded: the element was replaced by text_in_image"))
            continue
        try:
            apply_fix(item, el, tree, ctx)
        except SkipFix as exc:
            skipped.append((item.fix, str(exc)))
            continue
        applied_ids.add(item.fix.id)
        if item.fix.kind == "text_in_image" and el is not None:
            replaced.add(id(el))

    # An img_alt or lazy_load applied before the banner replaced its image did
    # nothing lasting; report it as superseded rather than applied.
    for item in chosen:
        el = targets[item.fix.id]
        if (
            item.fix.id in applied_ids
            and item.fix.kind != "text_in_image"
            and el is not None
            and id(el) in replaced
        ):
            applied_ids.discard(item.fix.id)
            skipped.append((item.fix, "superseded: the element was replaced by text_in_image"))

    for fix_id in sorted(accepted - set(by_id)):
        skipped.append(
            (Fix(id=fix_id, kind="unknown", target="", description=""), "no such fix in the plan")
        )

    copier = _AssetCopier(page_url=page_url, out_dir=out_dir, fetcher=fetcher)
    await copier.rewrite_document(tree)

    if ctx.css:
        css = "/* GreenAccess patch: generated fixes, review before use. */\n\n" + "\n\n".join(
            f"/* {fix_id} */\n{block}" for fix_id, block in ctx.css
        )
        (out_dir / PATCH_CSS).write_text(css + "\n", encoding="utf-8")
        head = tree.getroot().find("head")
        if head is None:
            head = lxml.html.Element("head")
            tree.getroot().insert(0, head)
        head.append(lxml.html.Element("link", {"rel": "stylesheet", "href": PATCH_CSS}))

    (out_dir / "index.html").write_text(dom.serialise_document(tree), encoding="utf-8")

    fixes: list[Fix] = []
    for item in plan.fixes:
        fixes.append(item.fix.model_copy(update={"applied": item.fix.id in applied_ids}))
    applied = [f for f in fixes if f.applied]
    manual = [f for f in fixes if f.manual_review and f.id not in accepted]
    poster_missing = (
        any(p.fix.kind == "autoplay_video" and p.fix.id in applied_ids for p in chosen)
        and not ctx.posters
    )
    (out_dir / CHANGES_MD).write_text(
        _changes_md(plan, patched_url, applied, skipped, manual, ctx, copier.notes, poster_missing),
        encoding="utf-8",
    )
    await asyncio.to_thread(_zip, out_dir, zip_path)

    return BuildResult(
        patch_dir=out_dir,
        zip_path=zip_path,
        fixes=fixes,
        skipped=[SkippedFix(fix_id=fix.id, reason=reason) for fix, reason in skipped],
        csp=build_csp(copier.third_party),
        applied_count=len(applied),
        notes=copier.notes,
    )
