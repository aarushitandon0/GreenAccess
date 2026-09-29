"""Smoke tests for the Daily Herald demo server (MASTERSPEC §11).

Starts both hosts on ephemeral ports and asserts the demo page and the
third-party tracker scripts are actually served. Also pins the handful of
planted defects that live in the served markup itself, so DEFECTS.md cannot
drift away from the page without a test failing.
"""

from __future__ import annotations

import importlib.util
import re
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPO_ROOT / "demo-site"
THIRD_PARTY_ROOT = DEMO_ROOT / "third-party"

TRACKERS = ("analytics.js", "adtech.js", "heatmap.js", "social.js")


def _load_server_module() -> ModuleType:
    """Import demo-site/server.py, which is a script rather than a package."""
    spec = importlib.util.spec_from_file_location("demo_server", DEMO_ROOT / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo_server = _load_server_module()


def _start(root: Path, strip_prefix: str = "") -> Iterator[str]:
    """Run a demo server on an ephemeral port and yield its base URL."""
    server: HTTPServer = demo_server.build_server(
        port=0, root=root, compress=False, strip_prefix=strip_prefix, quiet=True
    )
    demo_server.serve_forever_in_thread(server)
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def demo_base() -> Iterator[str]:
    yield from _start(DEMO_ROOT)


@pytest.fixture(scope="module")
def third_party_base() -> Iterator[str]:
    yield from _start(THIRD_PARTY_ROOT, strip_prefix="t")


def _get(url: str) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, response.read(), dict(response.headers)


# The demo files carry long comments describing their own defects. Those
# comments quote the very markup the assertions below look for, so they have to
# come out before anything is counted.

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _html_without_comments(body: bytes) -> str:
    return _HTML_COMMENT.sub("", body.decode("utf-8"))


def _css_without_comments(body: bytes) -> str:
    return _CSS_COMMENT.sub("", body.decode("utf-8"))


# --------------------------------------------------------------------------- #
# The demo page itself
# --------------------------------------------------------------------------- #


def test_demo_server_serves_index_html(demo_base: str):
    status, body, headers = _get(demo_base + "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"The Daily Herald" in body


def test_demo_server_serves_css_and_js(demo_base: str):
    for path, marker in (("/css/main.css", b".card__image"), ("/js/main.js", b"installPromoTrap")):
        status, body, _ = _get(demo_base + path)
        assert status == 200, path
        assert marker in body, path


def test_css_and_js_are_served_uncompressed(demo_base: str):
    """Defect UNCOMPRESSED-01: the default must not compress text resources."""
    for path in ("/css/main.css", "/js/main.js", "/"):
        _, _, headers = _get(demo_base + path)
        assert "Content-Encoding" not in headers, f"{path} should not be compressed"


def test_compression_can_be_turned_on():
    """--compress removes UNCOMPRESSED-01, which is how the fix is demonstrated."""
    server: HTTPServer = demo_server.build_server(
        port=0, root=DEMO_ROOT, compress=True, strip_prefix="", quiet=True
    )
    demo_server.serve_forever_in_thread(server)
    try:
        base = f"http://{server.server_address[0]}:{server.server_address[1]}"
        _, _, headers = _get(base + "/css/main.css")
        assert headers.get("Content-Encoding") == "gzip"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# The third-party host
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tracker", TRACKERS)
def test_third_party_host_serves_trackers(third_party_base: str, tracker: str):
    """Defect THIRD-PARTY-01: four trackers on a separate host."""
    status, body, _ = _get(f"{third_party_base}/t/{tracker}")
    assert status == 200
    assert len(body) > 500, f"{tracker} looks empty"


def test_third_party_host_does_not_serve_the_demo_page(third_party_base: str):
    """The tracker host must not expose the demo tree."""
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(f"{third_party_base}/t/index.html")
    assert excinfo.value.code == 404


# --------------------------------------------------------------------------- #
# Planted defects visible in the served markup
# --------------------------------------------------------------------------- #


def test_html_element_has_no_lang_attribute(demo_base: str):
    """Defect HTML-LANG-01."""
    _, body, _ = _get(demo_base + "/")
    html = _html_without_comments(body)
    assert "<html>" in html
    assert "<html lang" not in html


def test_hero_video_autoplays_and_loops(demo_base: str):
    """Defect VIDEO-AUTOPLAY-01."""
    _, body, _ = _get(demo_base + "/")
    html = _html_without_comments(body)
    video_start = html.index("<video")
    video_tag = html[video_start : html.index(">", video_start)]
    for attribute in ("autoplay", "loop", "muted"):
        assert attribute in video_tag, f"hero video should carry {attribute}"
    assert "poster=" not in video_tag


def test_most_article_images_have_no_alt(demo_base: str):
    """Defect IMG-ALT-01: at least 5 of the 8 article images lack alt."""
    _, body, _ = _get(demo_base + "/")
    html = _html_without_comments(body)
    missing = 0
    for index in range(1, 9):
        marker = f"article-0{index}.jpg"
        tag_start = html.rindex("<img", 0, html.index(marker))
        tag = html[tag_start : html.index(">", html.index(marker))]
        if "alt=" not in tag:
            missing += 1
    assert missing >= 5, f"expected >=5 article images without alt, found {missing}"


def test_all_images_are_eager_and_undimensioned(demo_base: str):
    """Defects IMG-EAGER-01 and IMG-DIM-01."""
    _, body, _ = _get(demo_base + "/")
    html = _html_without_comments(body)
    assert 'loading="lazy"' not in html
    assert html.count('loading="eager"') == 8
    # No img carries width/height, so no intrinsic size is reserved.
    assert "width=" not in html.split("<body")[1]
    assert "height=" not in html.split("<body")[1]


def test_stylesheet_requests_four_fonts_and_has_no_reduced_motion(demo_base: str):
    """Defects FONT-BLOAT-01 and MOTION-01."""
    _, body, _ = _get(demo_base + "/css/main.css")
    css = _css_without_comments(body)
    assert css.count("@font-face") == 4
    assert css.count("@keyframes") >= 4
    assert "prefers-reduced-motion" not in css


def test_page_loads_four_third_party_trackers(demo_base: str):
    """Defect THIRD-PARTY-01, as referenced from the page."""
    _, body, _ = _get(demo_base + "/")
    html = _html_without_comments(body)
    assert html.count('<script src="http://localhost:8082') == len(TRACKERS)


def test_generated_assets_are_present_and_sized(demo_base: str):
    """The byte budget depends on these existing; fail loudly if they do not."""
    expectations = {
        "/assets/generated/hero.mp4": (700_000, 1_100_000),
        "/assets/generated/banner-sale.jpg": (150_000, 250_000),
        "/assets/generated/banner-subscribe.png": (150_000, 250_000),
    }
    for path, (low, high) in expectations.items():
        status, body, _ = _get(demo_base + path)
        assert status == 200, path
        assert low <= len(body) <= high, f"{path} is {len(body):,} bytes, want {low:,}..{high:,}"
