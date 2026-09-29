"""Fix generation and patch build against the demo source, with a scripted LLM.

The Anthropic SDK is replaced by a fake whose answers are test data derived
from each prompt, so the whole AI path runs offline: vision calls capped at 6,
batched naming calls, text-in-image replacement, the cache, and LLM_OFFLINE
replay. The scan result is a hand-built fixture shaped like the demo's real
findings; nothing here is presented as a real measurement.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.cache import LlmCache
from app.llm.client import LlmClient
from app.models import (
    A11yResult,
    CarbonResult,
    Detection,
    EngineVersions,
    GreenResult,
    ImageIssue,
    ImageIssueKind,
    Impact,
    KeyboardResult,
    MediaItem,
    Scan,
    ScanResult,
    Violation,
    ViolationNode,
)
from app.patcher.build import build_patch
from app.patcher.fixgen import MAX_VISION_IMAGES, fetch_source, generate_plan
from app.patcher.plan import load_plan, save_plan
from app.security.fetch import SafeFetcher

pytestmark = pytest.mark.integration  # needs the demo hosts on :8081/:8082

DEMO = "http://localhost:8081/"
A = "http://localhost:8081/assets/generated/"
MISSING_ALT = [1, 2, 4, 5, 6, 8]
SIZES = {1: 86_000, 2: 83_000, 3: 90_000, 4: 89_000, 5: 90_500, 6: 87_000, 7: 85_000, 8: 84_000}


def _node(selector: str, html: str, summary: str = "") -> ViolationNode:
    return ViolationNode(selector=selector, html=html, failure_summary=summary)


def _contrast(selector: str, html: str, fg: str) -> ViolationNode:
    return _node(
        selector,
        html,
        f"Element has insufficient color contrast of 2.0 (foreground color: {fg}, background "
        "color: #ffffff, font size: 9.8pt (13px), font weight: normal). Expected contrast "
        "ratio of 4.5:1",
    )


def demo_result() -> ScanResult:
    img_nodes = [
        _node(
            f'div[data-card-index="{n - 1}"] > .card__image',
            f'<img class="card__image" src="/assets/generated/article-0{n}.jpg" loading="eager">',
        )
        for n in MISSING_ALT
    ] + [
        _node(
            ".main-column > .banner",
            '<img class="banner" src="/assets/generated/banner-subscribe.png">',
        )
    ]
    violations = [
        Violation(
            rule_id="image-alt", impact=Impact.CRITICAL, help="Images need alt", nodes=img_nodes
        ),
        Violation(
            rule_id="label",
            impact=Impact.CRITICAL,
            help="Form elements need labels",
            nodes=[
                _node(
                    'input[name="name"]',
                    '<input type="text" name="name" class="newsletter__input">',
                ),
                _node(
                    'input[type="email"]',
                    '<input type="email" name="email" class="newsletter__input">',
                ),
                _node("#terms-box", '<input type="checkbox" id="terms-box" name="terms">'),
            ],
        ),
        Violation(
            rule_id="select-name",
            impact=Impact.CRITICAL,
            help="Select needs a name",
            nodes=[_node("select", '<select name="edition" class="newsletter__input">')],
        ),
        Violation(
            rule_id="button-name",
            impact=Impact.CRITICAL,
            help="Buttons need names",
            nodes=[_node(".chat__close", '<button class="chat__close" onclick="closeChat()">')],
        ),
        Violation(
            rule_id="link-name",
            impact=Impact.SERIOUS,
            help="Links need names",
            nodes=[_node(".nav__icon", '<a href="#search" class="nav__icon">')],
        ),
        Violation(
            rule_id="html-has-lang",
            impact=Impact.SERIOUS,
            help="lang",
            nodes=[_node("html", "<html>")],
        ),
        Violation(
            rule_id="color-contrast",
            impact=Impact.SERIOUS,
            help="Contrast",
            nodes=[
                _contrast(".masthead__date", '<div class="masthead__date">', "#b0b0b0"),
                _contrast(".main-column > .fine-print", '<p class="fine-print">', "#b8b8b8"),
                _contrast(".weather > .fine-print", '<p class="fine-print">', "#b8b8b8"),
                _node(".hero h4", "<h4>", "Element's background color could not be determined"),
            ],
        ),
        Violation(
            rule_id="heading-order",
            impact=Impact.MODERATE,
            help="Heading order",
            nodes=[_node(".hero__overlay > h4", "<h4>")],
        ),
        Violation(
            rule_id="region",
            impact=Impact.MODERATE,
            help="Content in landmarks",
            nodes=[_node(".hero", '<div class="hero">')],
        ),
    ]
    images = [
        ImageIssue(
            url=f"{A}article-0{n}.jpg",
            bytes=SIZES[n],
            natural_w=3000,
            natural_h=2000,
            rendered_w=400,
            rendered_h=260,
            format="jpeg",
            issues=[ImageIssueKind.OVERSIZED, ImageIssueKind.LEGACY_FORMAT]
            + ([ImageIssueKind.EAGER_BELOW_FOLD] if n > 2 else []),
            estimated_saving_bytes=70_000,
        )
        for n in range(1, 9)
    ] + [
        ImageIssue(
            url=f"{A}banner-sale.jpg",
            bytes=185_000,
            natural_w=1600,
            natural_h=400,
            rendered_w=820,
            rendered_h=205,
            format="jpeg",
            issues=[ImageIssueKind.LEGACY_FORMAT, ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED],
        ),
        ImageIssue(
            url=f"{A}banner-subscribe.png",
            bytes=194_000,
            natural_w=1600,
            natural_h=400,
            rendered_w=746,
            rendered_h=187,
            format="png",
            issues=[
                ImageIssueKind.LEGACY_FORMAT,
                ImageIssueKind.EAGER_BELOW_FOLD,
                ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED,
            ],
        ),
    ]
    detections = [
        Detection(detector=name, summary=name)
        for name in (
            "no_reduced_motion",
            "third_party_scripts",
            "no_color_scheme",
            "font_bloat",
            "uncompressed_text",
        )
    ]
    return ScanResult(
        a11y=A11yResult(violations=violations, unique_rules=len(violations)),
        keyboard=KeyboardResult(trap_detected=True, trap_container="div#promo"),
        carbon=CarbonResult(
            total_bytes=2_300_000,
            images=images,
            autoplay_media=[
                MediaItem(
                    url=f"{A}hero.mp4",
                    bytes=1_000_000,
                    selector="html > body > div:nth-of-type(2) > video",
                    autoplay=True,
                )
            ],
            detections=detections,
        ),
        green=GreenResult(host="localhost"),
        engine_versions=EngineVersions(playwright="t", axe="t", swd_model="3"),
    )


# --------------------------------------------------------------------------- #
# A scripted model
# --------------------------------------------------------------------------- #


def _answer(kwargs: dict[str, Any]) -> str:
    content = kwargs["messages"][0]["content"]
    prompt = content[-1]["text"]
    if content[0]["type"] == "image":
        name = re.search(r"File name: (\S+)", prompt).group(1)
        if name.startswith("banner-"):
            return json.dumps(
                {
                    "decorative": False,
                    "alt": f"Banner: {name}",
                    "visible_text": ["HEADLINE FROM " + name, "Second line"],
                    "background_color": "#14508c",
                    "text_color": "#2a6aaa",  # too little contrast: must be corrected
                    "confidence": 0.9,
                    "reason": "Banner carries text.",
                }
            )
        decorative = name == "article-06.jpg"  # inside the top-6 budget
        return json.dumps(
            {
                "decorative": decorative,
                "alt": "" if decorative else f"Photo for {name}",
                "visible_text": [],
                "background_color": "#000000",
                "text_color": "#ffffff",
                "confidence": 0.8,
                "reason": "Test data.",
            }
        )
    ids = re.findall(r'<item id="(c\d+)">', prompt)
    if ids:
        return json.dumps(
            {"items": [{"id": i, "name": f"AI name {i}", "confidence": 0.7} for i in ids]}
        )
    return json.dumps({"lang": "en", "confidence": 0.9})


@dataclass
class ScriptedMessages:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=_answer(kwargs))],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
        )


@dataclass
class ScriptedSdk:
    messages: ScriptedMessages = field(default_factory=ScriptedMessages)


def client(tmp_path: Path, *, offline: bool) -> tuple[LlmClient, ScriptedSdk]:
    sdk = ScriptedSdk()
    return (
        LlmClient(
            model="test-model",
            cache=LlmCache(tmp_path / "cache"),
            api_key=None if offline else "sk-test",
            offline=offline,
            sdk=None if offline else sdk,
        ),
        sdk,
    )


def fetcher() -> SafeFetcher:
    return SafeFetcher(allowed_local_hosts=("localhost", "127.0.0.1"))


@pytest.fixture(scope="module")
def scan() -> Scan:
    return Scan(id="fixgen", url=DEMO, host="localhost", before=demo_result())


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_live_ai_plan_then_identical_offline_replay(demo_servers, scan, tmp_path) -> None:
    live_client, sdk = client(tmp_path, offline=False)
    source = await fetch_source(DEMO, fetcher())
    live = await generate_plan(scan, source, live_client, fetcher())

    fixes = {p.fix.id: p.fix for p in live.fixes}
    usage = live.ai_usage
    assert usage.vision_calls == MAX_VISION_IMAGES
    assert usage.live_calls == MAX_VISION_IMAGES + 3  # 6 images + label + link + button batches
    assert usage.cached_calls == 0 and usage.input_tokens == usage.live_calls * 1000
    image_calls = [
        c for c in sdk.messages.calls if c["messages"][0]["content"][0]["type"] == "image"
    ]
    assert len(image_calls) == MAX_VISION_IMAGES

    alt = [f for f in fixes.values() if f.kind == "img_alt"]
    ai_alt = [f for f in alt if not f.manual_review]
    assert all(
        f.ai_generated and "AI-generated, review before use" in f.description for f in ai_alt
    )
    assert any('alt=""' in f.diff.after for f in ai_alt)  # decorative -> empty alt
    over_budget = [f for f in alt if f.manual_review]
    assert over_budget and all("vision budget" in f.description for f in over_budget)
    assert len(ai_alt) + len(over_budget) == 7

    banners = [f for f in fixes.values() if f.kind == "text_in_image"]
    assert len(banners) == 2 and all(not f.manual_review and f.ai_generated for f in banners)

    names = {f.kind: f for f in fixes.values() if f.kind in ("link_name", "button_name")}
    assert names["link_name"].ai_generated and "AI name" in names["link_name"].description
    terms = next(f for f in fixes.values() if f.kind == "form_label" and f.target == "#terms-box")
    assert not terms.ai_generated and "<label" in terms.diff.after  # visible text beats AI

    lang = next(f for f in fixes.values() if f.kind == "html_lang")
    assert not lang.ai_generated  # detected locally: no call spent
    manual_kinds = {f.kind for f in fixes.values() if f.manual_review}
    assert {
        "region",
        "keyboard_trap",
        "font_bloat",
        "uncompressed_text",
        "dark_mode_tokens",
    } <= manual_kinds

    # LLM_OFFLINE=1 replays the same plan from the cache without a single live call.
    offline_client, _ = client(tmp_path, offline=True)
    replay = await generate_plan(scan, source, offline_client, fetcher())
    assert [p.model_dump() for p in replay.fixes] == [p.model_dump() for p in live.fixes]
    assert replay.ai_usage.live_calls == 0
    assert replay.ai_usage.cached_calls == usage.live_calls
    assert replay.ai_usage.cached_input_tokens == usage.input_tokens
    assert replay.ai_usage.offline is True


async def test_without_any_llm_fixes_degrade_honestly(demo_servers, scan, tmp_path) -> None:
    offline_client, _ = client(tmp_path, offline=True)  # empty cache
    source = await fetch_source(DEMO, fetcher())
    plan = await generate_plan(scan, source, offline_client, fetcher())
    assert plan.ai_usage.unavailable_reason
    assert not any(p.fix.ai_generated for p in plan.fixes)
    alt = [p.fix for p in plan.fixes if p.fix.kind == "img_alt"]
    assert alt and all(f.manual_review for f in alt)
    button = next(p.fix for p in plan.fixes if p.fix.kind == "button_name")
    assert button.description.startswith('Accessible name "Close chat", derived from')
    assert all(p.fix.manual_review for p in plan.fixes if p.fix.kind == "text_in_image")


async def test_contrast_groups_and_unparseable_nodes(demo_servers, scan, tmp_path) -> None:
    offline_client, _ = client(tmp_path, offline=True)
    source = await fetch_source(DEMO, fetcher())
    plan = await generate_plan(scan, source, offline_client, fetcher())
    contrast = [p for p in plan.fixes if p.fix.kind == "contrast"]
    automatic = [p for p in contrast if not p.fix.manual_review]
    assert len(automatic) == 2  # #b0b0b0 and #b8b8b8 groups
    grey = next(p for p in automatic if "#b8b8b8" in p.fix.description)
    assert grey.params["rules"][0]["selectors"] == [
        ".main-column > .fine-print",
        ".weather > .fine-print",
    ]
    assert grey.params["rules"][0]["color"] == "#767676"
    assert any(p.fix.manual_review for p in contrast)  # the one over the video


async def test_build_applies_ai_fixes_and_writes_changes(demo_servers, scan, tmp_path) -> None:
    live_client, _ = client(tmp_path, offline=False)
    source = await fetch_source(DEMO, fetcher())
    plan = await generate_plan(scan, source, live_client, fetcher())
    save_plan(tmp_path / "work", plan, source.html)
    loaded = load_plan(tmp_path / "work")
    assert loaded is not None
    plan, html = loaded

    accepted = [p.fix.id for p in plan.fixes if not p.fix.manual_review]
    result = await build_patch(
        plan,
        html,
        accepted,
        out_dir=tmp_path / "patched" / "fixgen",
        zip_path=tmp_path / "zips" / "fixgen.zip",
        fetcher=fetcher(),
        patched_url="http://localhost:8000/patched/fixgen/index.html",
    )
    index = (tmp_path / "patched" / "fixgen" / "index.html").read_text()
    assert index.startswith("<!doctype html>")
    assert '<html lang="en">' in index
    assert "Photo for article-01.jpg" in index
    assert "banner-sale-" not in index and "HEADLINE FROM banner-sale.jpg" in index
    assert 'src="http://localhost:8082' not in index  # allow-listed trackers removed
    assert 'aria-label="AI name' in index
    assert '<label for="terms-box">I agree to the terms</label>' in index
    assert "autoplay" not in index.split('class="hero__video"')[1].split(">")[0]
    css = (tmp_path / "patched" / "fixgen" / "greenaccess-patch.css").read_text()
    assert "prefers-reduced-motion" in css and "#767676" in css
    # The banner text colour was corrected to reach 4.5:1 on the banner background.
    assert "#2a6aaa" not in css and ".ga-text-banner" in css

    # Every accepted fix is either applied or skipped with a reason.
    applied = {f.id for f in result.fixes if f.applied}
    skipped = {s.fix_id: s.reason for s in result.skipped}
    assert set(accepted) == applied | set(skipped)
    assert all("superseded" in reason for reason in skipped.values())

    with zipfile.ZipFile(result.zip_path) as bundle:
        names = bundle.namelist()
        changes = bundle.read("CHANGES.md").decode()
    assert {"CHANGES.md", "index.html", "greenaccess-patch.css"} <= set(names)
    assert "## Applied" in changes and "## Manual fix needed" in changes
    assert "AI-generated" in changes and "9 live call(s)" in changes
    assert "script-src 'self'" in result.csp and "connect-src 'none'" in result.csp


async def test_unknown_accepted_ids_are_reported(demo_servers, scan, tmp_path) -> None:
    offline_client, _ = client(tmp_path, offline=True)
    source = await fetch_source(DEMO, fetcher())
    plan = await generate_plan(scan, source, offline_client, fetcher())
    result = await build_patch(
        plan,
        source.html,
        ["nope-123"],
        out_dir=tmp_path / "p",
        zip_path=tmp_path / "z.zip",
        fetcher=fetcher(),
        patched_url="x",
    )
    assert result.applied_count == 0
    assert result.skipped[0].fix_id == "nope-123"
    with zipfile.ZipFile(io.BytesIO(result.zip_path.read_bytes())) as bundle:
        assert "CHANGES.md" in bundle.namelist()


def test_name_batches_respect_the_prompt_cap_and_drop_oversize_items() -> None:
    """Big elements make smaller batches; an element too big alone is not sent."""
    from app.llm.context import MAX_PROMPT_CHARS
    from app.llm.prompts import name_batch_request
    from app.patcher import dom
    from app.patcher.fixgen import NAME_BATCH_SIZE, _Located, _name_batches

    filler = "word " * 110  # ~550 chars of text inside each button
    buttons = "".join(
        f"<div><button id='b{i}'>{filler}</button><p>{filler}</p></div>" for i in range(20)
    )
    tree = dom.parse_document(f"<html><body>{buttons}</body></html>")
    jobs = [(_Located(f"#b{i}", el), el) for i, el in enumerate(dom.select(tree, "button"))]
    batches = _name_batches("button_name", jobs)
    assert sum(len(b) for b in batches) == 20
    assert all(len(b) <= NAME_BATCH_SIZE for b in batches)
    assert all(
        len(name_batch_request("button_name", b).prompt) <= MAX_PROMPT_CHARS for b in batches
    )
    assert len(batches) > 20 // NAME_BATCH_SIZE  # size, not count, limited them
