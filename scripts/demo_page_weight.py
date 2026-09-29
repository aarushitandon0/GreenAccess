#!/usr/bin/env python3
"""Measure the Daily Herald demo's first-load transfer weight (MASTERSPEC §11).

Fetches the demo page over HTTP and then every subresource it references --
stylesheet, script, third-party trackers, images, video and the font files the
stylesheet pulls in -- and reports the total transfer size. MASTERSPEC §11
requires that total to land between 2.0 MB and 2.4 MB.

This measures bytes actually served, not bytes on disk, so it reflects the
demo server's (deliberate) lack of compression.

Usage:
    python scripts/demo_page_weight.py
    python scripts/demo_page_weight.py --base http://localhost:8081 --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TOTAL_MIN_BYTES = 2_000_000
TOTAL_MAX_BYTES = 2_400_000

TIMEOUT_S = 30

# Subresource references we care about in the HTML.
_HTML_PATTERNS = (
    re.compile(r'<link[^>]+href="([^"]+)"', re.I),
    re.compile(r'<script[^>]+src="([^"]+)"', re.I),
    re.compile(r'<img[^>]+src="([^"]+)"', re.I),
    re.compile(r'<video[^>]+src="([^"]+)"', re.I),
)
# url(...) references inside the stylesheet (fonts, background images).
_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


@dataclass
class Resource:
    url: str
    kind: str
    bytes: int = 0
    status: int = 0
    encoding: str = ""
    error: str = ""


@dataclass
class Measurement:
    resources: list[Resource] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(r.bytes for r in self.resources)

    def by_kind(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for resource in self.resources:
            totals[resource.kind] = totals.get(resource.kind, 0) + resource.bytes
        return totals


def _classify(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".css"):
        return "css"
    if path.endswith(".js"):
        return "js"
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")):
        return "img"
    if path.endswith((".woff2", ".woff", ".ttf", ".otf")):
        return "font"
    if path.endswith((".mp4", ".webm", ".ogv", ".m4v")):
        return "media"
    if path.endswith((".html", "/")) or "." not in path.rsplit("/", 1)[-1]:
        return "html"
    return "other"


def fetch(url: str, kind: str) -> Resource:
    resource = Resource(url=url, kind=kind)
    # Only http(s) is reachable here; the scheme is checked immediately below.
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GreenAccessBot/0.1 (demo weight check)",
            # Ask for compression so the measurement reflects what a browser
            # would actually receive.
            "Accept-Encoding": "gzip, deflate",
        },
    )
    if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
        resource.error = "unsupported scheme"
        return resource
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            body = response.read()
            resource.bytes = len(body)
            resource.status = response.status
            resource.encoding = response.headers.get("Content-Encoding", "") or ""
    except urllib.error.HTTPError as exc:
        resource.status = exc.code
        resource.error = f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        resource.error = str(exc)
    return resource


def measure(base: str) -> Measurement:
    measurement = Measurement()

    page = fetch(base, "html")
    measurement.resources.append(page)
    if page.error:
        return measurement

    with urllib.request.urlopen(
        urllib.request.Request(base, headers={"User-Agent": "GreenAccessBot/0.1"}),
        timeout=TIMEOUT_S,
    ) as response:
        html = response.read().decode("utf-8", "replace")

    seen: set[str] = {base}
    stylesheets: list[str] = []

    for pattern in _HTML_PATTERNS:
        for raw in pattern.findall(html):
            absolute = urllib.parse.urljoin(base, raw)
            if absolute in seen:
                continue
            seen.add(absolute)
            kind = _classify(absolute)
            resource = fetch(absolute, kind)
            measurement.resources.append(resource)
            if kind == "css" and not resource.error:
                stylesheets.append(absolute)

    # Follow url() references out of each stylesheet, which is how the demo's
    # four font files are requested.
    for sheet_url in stylesheets:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(sheet_url, headers={"User-Agent": "GreenAccessBot/0.1"}),
                timeout=TIMEOUT_S,
            ) as response:
                css = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            print(f"warning: could not re-read {sheet_url}: {exc}", file=sys.stderr)
            continue
        for raw in _CSS_URL.findall(css):
            if raw.startswith("data:"):
                continue
            absolute = urllib.parse.urljoin(sheet_url, raw)
            if absolute in seen:
                continue
            seen.add(absolute)
            measurement.resources.append(fetch(absolute, _classify(absolute)))

    return measurement


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8081/")
    parser.add_argument("--json", help="also write the measurement to this file")
    args = parser.parse_args()

    measurement = measure(args.base)

    failures = [r for r in measurement.resources if r.error]

    print(f"{'resource':<62} {'kind':<6} {'bytes':>10}")
    print("-" * 82)
    for resource in measurement.resources:
        label = resource.url.replace(args.base.rstrip("/"), "") or "/"
        if len(label) > 60:
            label = "..." + label[-57:]
        note = f"  [{resource.error}]" if resource.error else ""
        print(f"{label:<62} {resource.kind:<6} {resource.bytes:>10,}{note}")

    print("-" * 82)
    for kind, total in sorted(measurement.by_kind().items(), key=lambda kv: -kv[1]):
        print(f"{kind:<62} {'':<6} {total:>10,}")

    total = measurement.total
    print("=" * 82)
    print(f"{'TOTAL FIRST LOAD':<62} {len(measurement.resources):<6} {total:>10,}")
    print(f"{'':<62} {'':<6} {total / 1_000_000:>9.2f} MB")
    print(f"required window: {TOTAL_MIN_BYTES:,} .. {TOTAL_MAX_BYTES:,} bytes")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "total_bytes": total,
                    "by_kind": measurement.by_kind(),
                    "resources": [vars(r) for r in measurement.resources],
                },
                handle,
                indent=2,
            )
        print(f"wrote {args.json}")

    if failures:
        print(f"\n{len(failures)} resource(s) failed to load:", file=sys.stderr)
        for resource in failures:
            print(f"  - {resource.url}: {resource.error}", file=sys.stderr)
        return 1

    if not TOTAL_MIN_BYTES <= total <= TOTAL_MAX_BYTES:
        print(
            f"\nFAIL: {total:,} bytes is outside the MASTERSPEC §11 window.",
            file=sys.stderr,
        )
        return 1

    print("\nPASS: within the MASTERSPEC §11 window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
