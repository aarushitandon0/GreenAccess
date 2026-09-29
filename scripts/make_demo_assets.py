#!/usr/bin/env python3
"""Generate the deterministic media assets for the Daily Herald demo (MASTERSPEC §11).

The demo site is the backbone of the demo, so its first-load weight has to land
in a known window (2.0-2.4 MB) on every machine. Rather than hoping a fixed
encoder quality produces the same bytes everywhere, each asset declares a target
size and the encoder setting is binary-searched until the output lands within
tolerance. That makes the byte budget reproducible instead of incidental.

Images are drawn with Pillow. Text is baked into the two banner images on
purpose -- that is planted defect IMG-TEXT-01/02, which the text_in_image
detector is expected to catch.

The hero video is produced with H.264 via, in order of preference:
  1. a local `ffmpeg` binary,
  2. `docker run mwader/static-ffmpeg` if Docker is available,
  3. the committed fallback at demo-site/assets/fallback/hero.mp4.
MASTERSPEC §11 requires the fallback path, since many machines have no ffmpeg.

Usage:
    python scripts/make_demo_assets.py            # generate everything
    python scripts/make_demo_assets.py --check    # report sizes, generate nothing
    python scripts/make_demo_assets.py --force    # ignore the cache and redraw
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = REPO_ROOT / "demo-site"
ASSETS = DEMO_ROOT / "assets"
GENERATED = ASSETS / "generated"
FALLBACK = ASSETS / "fallback"
FONTS_DIR = ASSETS / "fonts"

FFMPEG_DOCKER_IMAGE = "mwader/static-ffmpeg:7.1"

# Accept +/-12% around a target. Tight enough to keep the total in its window,
# loose enough that a quality step never fails to converge.
SIZE_TOLERANCE = 0.12

# The window MASTERSPEC §11 requires for a first load.
TOTAL_MIN_BYTES = 2_000_000
TOTAL_MAX_BYTES = 2_400_000


# --------------------------------------------------------------------------- #
# Asset manifest
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ArticleImage:
    """A large article photo: natural 3000px, rendered ~400px (defect IMG-OVERSIZE)."""

    name: str
    target_bytes: int
    hue: tuple[int, int, int]
    seed: int


# 8 article photos. MASTERSPEC §11: natural 3000px, rendered 400px.
ARTICLE_IMAGES: tuple[ArticleImage, ...] = (
    ArticleImage("article-01.jpg", 86_000, (41, 78, 122), 11),
    ArticleImage("article-02.jpg", 84_000, (122, 66, 41), 22),
    ArticleImage("article-03.jpg", 88_000, (46, 96, 74), 33),
    ArticleImage("article-04.jpg", 83_000, (104, 52, 92), 44),
    ArticleImage("article-05.jpg", 87_000, (120, 98, 40), 55),
    ArticleImage("article-06.jpg", 85_000, (48, 84, 110), 66),
    ArticleImage("article-07.jpg", 86_000, (92, 44, 58), 77),
    ArticleImage("article-08.jpg", 84_000, (60, 88, 52), 88),
)

NATURAL_WIDTH = 3000
NATURAL_HEIGHT = 2000

# The two text-baked banners (defects IMG-TEXT-01 / IMG-TEXT-02).
BANNER_SALE = "banner-sale.jpg"
BANNER_SUBSCRIBE = "banner-subscribe.png"
BANNER_SALE_TARGET = 190_000
BANNER_SUBSCRIBE_TARGET = 200_000
BANNER_WIDTH = 1600
BANNER_HEIGHT = 400

HERO_VIDEO = "hero.mp4"
HERO_TARGET_BYTES = 1_010_000
HERO_WIDTH = 1280
HERO_HEIGHT = 480
HERO_SECONDS = 8
HERO_FPS = 24


# --------------------------------------------------------------------------- #
# Drawing helpers (deterministic -- no randomness without an explicit seed)
# --------------------------------------------------------------------------- #


def _lcg(seed: int) -> Callable[[], float]:
    """A tiny deterministic PRNG, so output does not depend on Python's hashing."""
    state = seed & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal state
        state = (1_103_515_245 * state + 12_345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return nxt


def _gradient(
    size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    """A smooth vertical gradient. Smooth content keeps JPEG sizes predictable."""
    width, height = size
    base = Image.new("RGB", (1, height))
    pixels = base.load()
    assert pixels is not None
    for y in range(height):
        t = y / max(1, height - 1)
        pixels[0, y] = (
            round(top[0] + (bottom[0] - top[0]) * t),
            round(top[1] + (bottom[1] - top[1]) * t),
            round(top[2] + (bottom[2] - top[2]) * t),
        )
    return base.resize((width, height), Image.Resampling.BILINEAR)


def _draw_article(image_spec: ArticleImage) -> Image.Image:
    """Draw a soft, photo-like image at full natural resolution."""
    rand = _lcg(image_spec.seed)
    r, g, b = image_spec.hue
    img = _gradient(
        (NATURAL_WIDTH, NATURAL_HEIGHT),
        (min(255, r + 70), min(255, g + 70), min(255, b + 70)),
        (max(0, r - 30), max(0, g - 30), max(0, b - 30)),
    )
    draw = ImageDraw.Draw(img, "RGBA")

    # Large soft blobs: enough structure to look like a photo, smooth enough to
    # stay cheap to encode.
    for _ in range(14):
        cx = rand() * NATURAL_WIDTH
        cy = rand() * NATURAL_HEIGHT
        radius = 180 + rand() * 620
        alpha = 26 + int(rand() * 50)
        tint = (
            min(255, int(r + rand() * 150)),
            min(255, int(g + rand() * 150)),
            min(255, int(b + rand() * 150)),
            alpha,
        )
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=tint)

    # A soft horizon line gives the eye something to read as a photograph.
    horizon = int(NATURAL_HEIGHT * (0.55 + rand() * 0.1))
    draw.rectangle([0, horizon, NATURAL_WIDTH, NATURAL_HEIGHT], fill=(r, g, b, 90))

    return img.filter(ImageFilter.GaussianBlur(radius=6))


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Pillow's bundled scalable font: deterministic and license-clean."""
    return ImageFont.load_default(size=size)


def _draw_banner_with_text(
    *,
    headline: str,
    subline: str,
    cta: str,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> Image.Image:
    """Draw a banner with its message baked into the pixels.

    This is a planted defect: the text is unreadable to a screen reader and
    unselectable, and it is exactly what the text_in_image detector looks for.
    """
    img = _gradient((BANNER_WIDTH, BANNER_HEIGHT), top, bottom)

    # A photographic backdrop behind the text. A flat gradient compresses down to
    # almost nothing, which would put the banner under the 150 KB the defect
    # needs to be worth reporting; real detail keeps the weight honest.
    backdrop = ImageDraw.Draw(img, "RGBA")
    rand_bg = _lcg(7171)
    for _ in range(26):
        cx = rand_bg() * BANNER_WIDTH
        cy = rand_bg() * BANNER_HEIGHT
        radius = 40 + rand_bg() * 190
        backdrop.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(
                min(255, int(top[0] + rand_bg() * 120)),
                min(255, int(top[1] + rand_bg() * 120)),
                min(255, int(top[2] + rand_bg() * 120)),
                40 + int(rand_bg() * 55),
            ),
        )

    draw = ImageDraw.Draw(img)

    headline_font = _font(84)
    sub_font = _font(38)
    cta_font = _font(34)

    draw.text((70, 84), headline, font=headline_font, fill=(255, 255, 255))
    draw.text((74, 196), subline, font=sub_font, fill=(238, 238, 238))

    # A fake "button" drawn into the image, with no real control behind it.
    box = [74, 268, 74 + 300, 268 + 66]
    draw.rounded_rectangle(box, radius=8, fill=(255, 255, 255))
    draw.text((104, 286), cta, font=cta_font, fill=(20, 20, 20))

    # Fine grain, applied per pixel. This is what carries the file into the
    # 150-250 KB band MASTERSPEC §11 asks for.
    rand = _lcg(4242)
    noise = Image.new("RGB", (BANNER_WIDTH, BANNER_HEIGHT))
    noise_pixels = noise.load()
    assert noise_pixels is not None
    for y in range(BANNER_HEIGHT):
        for x in range(BANNER_WIDTH):
            v = int(90 + rand() * 165)
            noise_pixels[x, y] = (v, v, v)
    return Image.blend(img, noise, 0.14)


def _draw_hero_frame_source() -> Image.Image:
    """A still that the hero video pans across."""
    img = _gradient((HERO_WIDTH * 2, HERO_HEIGHT), (26, 58, 92), (12, 24, 38))
    draw = ImageDraw.Draw(img, "RGBA")
    rand = _lcg(909)
    # A skyline silhouette: high-contrast edges give the encoder real work to do,
    # which is what makes the file heavy enough to be worth flagging.
    x = 0
    while x < HERO_WIDTH * 2:
        w = 40 + int(rand() * 110)
        h = 80 + int(rand() * 260)
        draw.rectangle([x, HERO_HEIGHT - h, x + w, HERO_HEIGHT], fill=(8, 14, 22, 235))
        for wy in range(HERO_HEIGHT - h + 14, HERO_HEIGHT - 10, 26):
            for wx in range(x + 8, x + w - 8, 20):
                if rand() > 0.45:
                    draw.rectangle([wx, wy, wx + 8, wy + 12], fill=(250, 214, 130, 220))
        x += w + 8
    return img


# --------------------------------------------------------------------------- #
# Size-targeted encoding
# --------------------------------------------------------------------------- #


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=False)
    return buf.getvalue()


def _encode_png(img: Image.Image, colors: int) -> bytes:
    buf = BytesIO()
    img.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors).save(
        buf, format="PNG", optimize=True
    )
    return buf.getvalue()


def encode_to_target(img: Image.Image, target: int, fmt: str) -> tuple[bytes, str]:
    """Binary-search the encoder setting until the output is near `target` bytes.

    Returns the encoded bytes and a human-readable note about the setting used.
    """
    if fmt == "JPEG":
        low, high, encode = 5, 95, _encode_jpeg
    elif fmt == "PNG":
        low, high, encode = 8, 256, _encode_png
    else:
        raise ValueError(f"unsupported format {fmt}")

    best: bytes | None = None
    best_setting = low
    for _ in range(12):
        mid = (low + high) // 2
        data = encode(img, mid)
        if best is None or abs(len(data) - target) < abs(len(best) - target):
            best, best_setting = data, mid
        if len(data) > target:
            high = mid - 1
        else:
            low = mid + 1
        if low > high:
            break

    assert best is not None
    setting_name = "quality" if fmt == "JPEG" else "colors"
    drift = abs(len(best) - target) / target
    note = f"{setting_name}={best_setting}"
    if drift > SIZE_TOLERANCE:
        note += f" (off target by {drift:.0%})"
    return best, note


# --------------------------------------------------------------------------- #
# Hero video
# --------------------------------------------------------------------------- #


def _ffmpeg_command() -> tuple[list[str], str] | None:
    """Return (command prefix, description) for an available ffmpeg, or None."""
    local = shutil.which("ffmpeg")
    if local:
        return ([local], f"local ffmpeg ({local})")

    docker = shutil.which("docker")
    if docker:
        try:
            probe = subprocess.run(
                [docker, "image", "inspect", FFMPEG_DOCKER_IMAGE],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if probe.returncode == 0:
            return (
                [
                    docker,
                    "run",
                    "--rm",
                    "-v",
                    f"{GENERATED.as_posix()}:/work",
                    "-w",
                    "/work",
                    FFMPEG_DOCKER_IMAGE,
                ],
                f"docker {FFMPEG_DOCKER_IMAGE}",
            )
    return None


def _write_hero_source_frames(source: Image.Image) -> Path:
    """Write the pan frames the encoder consumes. Returns the frame directory."""
    frames_dir = GENERATED / "_hero_frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    total = HERO_SECONDS * HERO_FPS
    travel = source.width - HERO_WIDTH
    for index in range(total):
        # Ease in and out so the loop does not visibly jerk at the seam.
        t = 0.5 - 0.5 * math.cos(2 * math.pi * index / total)
        left = int(t * travel)
        frame = source.crop((left, 0, left + HERO_WIDTH, HERO_HEIGHT))
        frame.save(frames_dir / f"frame-{index:04d}.png", format="PNG")
    return frames_dir


def build_hero_video(*, force: bool) -> tuple[Path, str]:
    """Produce the hero video, or fall back to the committed copy."""
    out = GENERATED / HERO_VIDEO
    if out.exists() and not force:
        return out, "cached"

    tooling = _ffmpeg_command()
    if tooling is None:
        fallback = FALLBACK / HERO_VIDEO
        if not fallback.exists():
            raise SystemExit(
                "No ffmpeg (local or Docker) and no committed fallback at "
                f"{fallback}. Cannot produce the hero video."
            )
        shutil.copyfile(fallback, out)
        return out, f"committed fallback ({fallback.stat().st_size:,} bytes)"

    prefix, description = tooling
    source = _draw_hero_frame_source()
    frames_dir = _write_hero_source_frames(source)

    # Force true CBR. With plain -b:v, x264 undershoots badly on easy content
    # (a slow pan compresses to a third of the requested rate), which would put
    # the page under its byte window. nal-hrd=cbr makes x264 pad to the rate, so
    # the output size is a deterministic function of bitrate x duration.
    bitrate_kbps = int((HERO_TARGET_BYTES * 8) / HERO_SECONDS / 1000)
    frame_glob = f"{frames_dir.name}/frame-%04d.png"

    command = [
        *prefix,
        "-y",
        "-framerate",
        str(HERO_FPS),
        "-i",
        frame_glob,
        "-c:v",
        "libx264",
        "-b:v",
        f"{bitrate_kbps}k",
        "-minrate",
        f"{bitrate_kbps}k",
        "-maxrate",
        f"{bitrate_kbps}k",
        "-bufsize",
        f"{bitrate_kbps}k",
        "-x264-params",
        "nal-hrd=cbr:force-cfr=1",
        "-pix_fmt",
        "yuv420p",
        "-an",
        # faststart puts the index first so the browser starts playing sooner --
        # realistic for a site that autoplays a hero.
        "-movflags",
        "+faststart",
        HERO_VIDEO,
    ]
    result = subprocess.run(command, cwd=GENERATED, capture_output=True, timeout=600, check=False)
    shutil.rmtree(frames_dir, ignore_errors=True)

    if result.returncode != 0 or not out.exists():
        stderr = result.stderr.decode("utf-8", "replace")[-1200:]
        raise SystemExit(f"ffmpeg failed ({description}):\n{stderr}")

    return out, description


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #

# MASTERSPEC §11 asks for 4 web font files "including unused weights". We copy
# four real woff2 files out of the frontend's node_modules. Two of them are the
# latin-ext subsets, which an English-language page never matches -- genuinely
# dead weight, which is the point of the defect. See defect FONT-BLOAT-01.
FONT_SOURCES = (
    ("fraunces-latin-wght-normal.woff2", "@fontsource-variable/fraunces"),
    ("fraunces-latin-ext-wght-normal.woff2", "@fontsource-variable/fraunces"),
    ("inter-latin-wght-normal.woff2", "@fontsource-variable/inter"),
    ("inter-latin-ext-wght-normal.woff2", "@fontsource-variable/inter"),
)


def copy_fonts() -> list[tuple[str, int]]:
    """Copy the demo's font files out of node_modules. Returns (name, bytes)."""
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    node_modules = REPO_ROOT / "frontend" / "node_modules"
    copied: list[tuple[str, int]] = []
    for filename, package in FONT_SOURCES:
        source = node_modules / package / "files" / filename
        target = FONTS_DIR / filename
        if source.exists():
            shutil.copyfile(source, target)
        elif not target.exists():
            raise SystemExit(
                f"Font {filename} not found at {source}. Run `npm install` in frontend/ first."
            )
        copied.append((filename, target.stat().st_size))
    return copied


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _write(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def generate(*, force: bool) -> dict[str, int]:
    """Generate every asset and return {relative path: bytes}."""
    GENERATED.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, int] = {}

    print("Article photos (natural 3000x2000, rendered ~400px wide):")
    for spec in ARTICLE_IMAGES:
        out = GENERATED / spec.name
        if out.exists() and not force:
            sizes[spec.name] = out.stat().st_size
            print(f"  {spec.name:<20} {out.stat().st_size:>9,} bytes  (cached)")
            continue
        data, note = encode_to_target(_draw_article(spec), spec.target_bytes, "JPEG")
        sizes[spec.name] = _write(out, data)
        print(f"  {spec.name:<20} {len(data):>9,} bytes  target {spec.target_bytes:,}  {note}")

    print("\nBanners (text baked into the pixels -- planted defect):")
    banner_specs = (
        (
            BANNER_SALE,
            BANNER_SALE_TARGET,
            "JPEG",
            {
                "headline": "HALF-PRICE SALE",
                "subline": "Every subscription tier, this week only.",
                "cta": "CLAIM OFFER",
                "top": (150, 46, 52),
                "bottom": (84, 18, 26),
            },
        ),
        (
            BANNER_SUBSCRIBE,
            BANNER_SUBSCRIBE_TARGET,
            "PNG",
            {
                "headline": "SUBSCRIBE TODAY",
                "subline": "Unlimited articles from just 2 a month.",
                "cta": "SIGN ME UP",
                "top": (28, 62, 104),
                "bottom": (12, 28, 54),
            },
        ),
    )
    for name, target, fmt, kwargs in banner_specs:
        out = GENERATED / name
        if out.exists() and not force:
            sizes[name] = out.stat().st_size
            print(f"  {name:<24} {out.stat().st_size:>9,} bytes  (cached)")
            continue
        banner = _draw_banner_with_text(**kwargs)  # type: ignore[arg-type]
        data, note = encode_to_target(banner, target, fmt)
        sizes[name] = _write(out, data)
        print(f"  {name:<24} {len(data):>9,} bytes  target {target:,}  {note}")

    print("\nHero video:")
    video_path, how = build_hero_video(force=force)
    sizes[HERO_VIDEO] = video_path.stat().st_size
    print(f"  {HERO_VIDEO:<24} {sizes[HERO_VIDEO]:>9,} bytes  via {how}")

    print("\nFonts:")
    for name, size in copy_fonts():
        sizes[f"fonts/{name}"] = size
        print(f"  {name:<40} {size:>9,} bytes")

    return sizes


def measure_static() -> dict[str, int]:
    """Bytes of the hand-written demo files a first load also pulls down."""
    sizes: dict[str, int] = {}
    for relative in ("index.html", "css/main.css", "js/main.js"):
        path = DEMO_ROOT / relative
        if path.exists():
            sizes[relative] = path.stat().st_size
    third_party = DEMO_ROOT / "third-party"
    if third_party.exists():
        for path in sorted(third_party.glob("*.js")):
            sizes[f"third-party/{path.name}"] = path.stat().st_size
    return sizes


def report(sizes: dict[str, int]) -> int:
    generated_total = sum(sizes.values())
    static = measure_static()
    static_total = sum(static.values())
    total = generated_total + static_total

    print("\n" + "=" * 62)
    print(f"  generated media      {generated_total:>12,} bytes")
    print(f"  html + css + js      {static_total:>12,} bytes")
    print(f"  {'-' * 58}")
    print(f"  FIRST-LOAD TOTAL     {total:>12,} bytes  ({total / 1_000_000:.2f} MB)")
    print(f"  required window      {TOTAL_MIN_BYTES:>12,} .. {TOTAL_MAX_BYTES:,}")
    print("=" * 62)

    if not TOTAL_MIN_BYTES <= total <= TOTAL_MAX_BYTES:
        print(
            f"\nFAIL: total {total:,} bytes is outside the MASTERSPEC §11 window.",
            file=sys.stderr,
        )
        return 1
    print("\nWithin the MASTERSPEC §11 window.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report existing sizes without generating"
    )
    parser.add_argument("--force", action="store_true", help="redraw even if assets exist")
    args = parser.parse_args()

    if args.check:
        if not GENERATED.exists():
            print("No generated assets. Run without --check first.", file=sys.stderr)
            return 1
        sizes = {
            path.name: path.stat().st_size for path in sorted(GENERATED.iterdir()) if path.is_file()
        }
        sizes.update(
            {
                f"fonts/{path.name}": path.stat().st_size
                for path in sorted(FONTS_DIR.glob("*.woff2"))
            }
        )
        return report(sizes)

    return report(generate(force=args.force))


if __name__ == "__main__":
    raise SystemExit(main())
