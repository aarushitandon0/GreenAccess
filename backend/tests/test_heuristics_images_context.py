"""Deterministic fallbacks, image optimisation, and the LLM context bounds."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.llm.context import (
    MAX_PROMPT_CHARS,
    PromptTooLarge,
    assert_prompt_is_bounded,
    extract_context,
)
from app.llm.prompts import NameItem, lang_request, name_batch_request
from app.llm.vision import MAX_VISION_SIDE_PX, VisionImageError, prepare_image
from app.patcher import dom
from app.patcher.heuristics import (
    control_name_from_context,
    detect_language,
    form_label_from_context,
    humanise,
)
from app.patcher.images import ImageSkipped, optimize_image, target_width

DEMO_HTML = (Path(__file__).resolve().parents[2] / "demo-site" / "index.html").read_text()


@pytest.fixture(scope="module")
def demo():
    return dom.parse_document(DEMO_HTML)


# --------------------------------------------------------------------------- #
# Heuristics
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("first_name", "First name"), ("firstName", "First name"), ("e-mail", "E mail"), ("", "")],
)
def test_humanise(raw: str, expected: str) -> None:
    assert humanise(raw) == expected


@pytest.mark.parametrize(
    ("selector", "text", "mode"),
    [
        ('input[name="name"]', "Name", "aria-label"),
        ('input[name="email"]', "Email address", "aria-label"),
        ('select[name="edition"]', "Edition", "aria-label"),
        ("#terms-box", "I agree to the terms", "label-for"),
        (".chat__input", "Type a message", "aria-label"),  # placeholder
    ],
)
def test_form_label_from_context(demo, selector: str, text: str, mode: str) -> None:
    proposal = form_label_from_context(dom.select(demo, selector)[0])
    assert proposal is not None
    assert (proposal.text, proposal.mode) == (text, mode)
    assert 0 < proposal.confidence < 1


def test_form_label_gives_up_rather_than_guessing() -> None:
    tree = dom.parse_document("<html><body><input type='text'></body></html>")
    assert form_label_from_context(dom.select(tree, "input")[0]) is None


@pytest.mark.parametrize(
    ("selector", "name"),
    [(".nav__icon", "Search"), (".chat__close", "Close chat")],
)
def test_control_name_from_context(demo, selector: str, name: str) -> None:
    found = control_name_from_context(dom.select(demo, selector)[0])
    assert found is not None and found[0] == name


def test_control_name_prefers_title_and_refuses_meaningless_targets() -> None:
    tree = dom.parse_document(
        "<html><body><a href='#' title='Home page'></a><a href='/42'></a>"
        "<button class='x'></button></body></html>"
    )
    first, second, button = dom.select(tree, "a, button")
    assert control_name_from_context(first) == ("Home page", 0.8, "its title attribute")
    assert control_name_from_context(second) is None
    assert control_name_from_context(button) is None


def test_detect_language_on_the_demo(demo) -> None:
    guess = detect_language(demo.getroot())
    assert guess.lang == "en" and guess.confidence >= 0.6


def test_detect_language_is_unsure_on_too_little_text() -> None:
    tree = dom.parse_document("<html><body><p>Hello</p></body></html>")
    assert detect_language(tree.getroot()).lang is None


def test_detect_language_french() -> None:
    text = " ".join(["Le chat est sur la table et le chien dans le jardin avec les enfants."] * 4)
    tree = dom.parse_document(f"<html><body><p>{text}</p></body></html>")
    assert detect_language(tree.getroot()).lang == "fr"


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


def _jpeg(width: int, height: int, quality: int = 95) -> bytes:
    image = Image.new("RGB", (width, height))
    for x in range(0, width, 7):  # some detail, so JPEG cannot trivially shrink it
        image.paste((x % 255, (x * 3) % 255, 90), (x, 0, x + 3, height))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("natural", "rendered", "expected"), [(3000, 400, 800), (1600, 820, 1600), (500, 0, 500)]
)
def test_target_width(natural: int, rendered: int, expected: int) -> None:
    assert target_width(natural, rendered) == expected


def test_optimize_resizes_to_twice_rendered_width_as_webp() -> None:
    original = _jpeg(3000, 2000)
    result = optimize_image(original, rendered_w=400)
    assert (result.width, result.height) == (800, 533)
    assert result.data[:4] == b"RIFF" and result.data[8:12] == b"WEBP"
    assert len(result.data) < len(original)
    assert result.saved_bytes > 0


def test_optimize_never_upscales() -> None:
    result = optimize_image(_jpeg(600, 300), rendered_w=500)
    assert result.width == 600


def test_optimize_refuses_a_bigger_result() -> None:
    """Already a heavily compressed WebP: quality 78 would only add bytes."""
    rough = io.BytesIO()
    Image.open(io.BytesIO(_jpeg(400, 300))).save(rough, format="WEBP", quality=5)
    with pytest.raises(ImageSkipped, match="not smaller"):
        optimize_image(rough.getvalue(), rendered_w=400)


def test_optimize_skips_animations_and_non_images() -> None:
    frames = [Image.new("RGB", (40, 40), c) for c in ((255, 0, 0), (0, 255, 0))]
    gif = io.BytesIO()
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:])
    with pytest.raises(ImageSkipped, match="animated"):
        optimize_image(gif.getvalue(), rendered_w=20)
    with pytest.raises(ImageSkipped):
        optimize_image(b"<svg/>", rendered_w=20)


def test_vision_image_is_downscaled_and_hash_is_stable() -> None:
    data = _jpeg(3000, 2000)
    first = prepare_image(data)
    assert max(first.width, first.height) <= MAX_VISION_SIDE_PX
    assert first.media_type == "image/webp"
    assert prepare_image(data).sha256 == first.sha256
    with pytest.raises(VisionImageError):
        prepare_image(b"not an image")


# --------------------------------------------------------------------------- #
# Context bounds: no full page HTML to the LLM, ever
# --------------------------------------------------------------------------- #


def test_context_is_the_element_plus_at_most_two_ancestors(demo) -> None:
    img = dom.select(demo, ".card__image")[0]
    context = extract_context(img)
    assert context.element.startswith('<img class="card__image"')
    assert len(context.ancestors) <= 2
    assert context.ancestors[0] == '<div class="card">'
    assert "Harbour redevelopment" in context.nearby_text


def test_every_demo_prompt_is_bounded_and_page_free(demo) -> None:
    targets = dom.select(demo, "img, input, select, a, button, h4")
    items = [NameItem(id=f"c{i}", context=extract_context(el)) for i, el in enumerate(targets[:8])]
    request = name_batch_request("button_name", items)
    assert len(request.prompt) <= MAX_PROMPT_CHARS
    for forbidden in ("<body", "<head", "<!doctype", "Daily Herald is published daily"):
        assert forbidden not in request.prompt
    assert DEMO_HTML[:2000] not in request.prompt


def test_context_of_a_body_child_stops_at_the_body(demo) -> None:
    chat = dom.select(demo, "#chat")[0]
    context = extract_context(chat)
    assert all("<body" not in a for a in context.ancestors)
    assert context.nearby_text == ""


def test_html_context_is_only_the_opening_tag(demo) -> None:
    context = extract_context(demo.getroot())
    assert context.element == "<html>"
    request = lang_request("T", dom.visible_text(demo.getroot(), limit=5000))
    assert len(request.prompt) < 1000


def test_oversized_or_page_level_prompts_are_refused() -> None:
    with pytest.raises(PromptTooLarge):
        assert_prompt_is_bounded("x" * (MAX_PROMPT_CHARS + 1))
    with pytest.raises(PromptTooLarge):
        assert_prompt_is_bounded("<body><p>everything</p></body>")
