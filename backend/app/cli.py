"""Command-line scanner (``python -m app.cli scan <url>``).

Single responsibility: run :class:`~app.scanner.pipeline.ScanPipeline` from a
terminal. Step progress and a short summary go to **stderr**; the ScanResult
JSON goes to **stdout**, or to ``--out`` when given, so the output can be piped
straight into ``jq``.

On failure the MASTERSPEC §12 error envelope ``{"error": {"code", "message"}}``
is printed to stdout and the exit code is 2 (blocked URL) or 1 (anything
else).

This is the one module allowed to ``print`` (see ``pyproject.toml``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.models import (
    ApiError,
    ApiErrorEnvelope,
    ErrorCode,
    GreenSource,
    ScanResult,
    StepEvent,
    StepStatus,
)
from app.scanner.pipeline import ScanError, ScanPipeline

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

_STATUS_MARK = {
    StepStatus.RUNNING: "..",
    StepStatus.OK: "ok",
    StepStatus.ERROR: "!!",
    StepStatus.SKIPPED: "--",
}


def _err(line: str = "") -> None:
    print(line, file=sys.stderr, flush=True)


def _print_event(event: StepEvent) -> None:
    if event.status is StepStatus.RUNNING:
        return
    timing = f"{event.ms:>6} ms" if event.status in {StepStatus.OK, StepStatus.ERROR} else " " * 9
    _err(f"  [{_STATUS_MARK[event.status]}] {event.name:<10}{timing}  {event.detail}")


def summarize(result: ScanResult) -> list[str]:
    """Human-readable summary lines. Wording follows CLAUDE.md accuracy rules."""
    carbon = result.carbon
    impacts = ", ".join(
        f"{count} {impact.value}" for impact, count in result.a11y.counts_by_impact.items()
    )
    lines = [
        "Accessibility (automated checks, not a conformance claim):",
        f"  {result.a11y.unique_rules} rules with issues detected on "
        f"{result.a11y.total_nodes} elements" + (f" ({impacts})" if impacts else ""),
        f"  keyboard crawl: {result.keyboard.tabs_pressed} Tab presses, "
        + (
            f"focus trap detected in {result.keyboard.trap_container}"
            if result.keyboard.trap_detected
            else "no focus trap detected"
        )
        + f", {result.keyboard.focus_visible_missing_count} elements without a visible focus"
        f" indicator, {result.keyboard.unreachable_interactive_count} not reached",
        f"Carbon (estimates, Sustainable Web Design model v{carbon.assumptions.model_version}):",
        f"  {carbon.total_bytes:,} bytes transferred in {carbon.request_count} requests",
        f"  ~{carbon.grams_per_view:.3f} g CO2e per view (estimate; "
        f"first visit ~{carbon.grams_first_visit:.3f} g)",
        f"  green hosting: {_green_label(result)}",
    ]
    for detection in carbon.detections:
        saving = f" ~{detection.estimated_saving_bytes:,} B est." if detection.saves_bytes else ""
        lines.append(f"  - {detection.detector}{saving}")
    lines.append("Scores: placeholders (scoring is not implemented in this phase)")
    return lines


def _green_label(result: ScanResult) -> str:
    green = result.green
    if green.source is GreenSource.UNAVAILABLE:
        return "unknown"
    if not green.green:
        return "no"
    return f"yes ({green.hosted_by})" if green.hosted_by else "yes"


def _error_json(code: ErrorCode, message: str) -> str:
    return ApiErrorEnvelope(error=ApiError(code=code, message=message)).model_dump_json(indent=2)


async def _scan(url: str, out: Path | None) -> int:
    settings = get_settings()
    pipeline = ScanPipeline(url, settings=settings)
    _err(f"Scanning {url}")
    try:
        async for event in pipeline.events():
            _print_event(event)
    except ScanError as exc:
        _err(f"Scan failed: {exc.code.value}: {exc.message}")
        print(_error_json(exc.code, exc.message), flush=True)
        return EXIT_BLOCKED if exc.code is ErrorCode.URL_BLOCKED else EXIT_FAILED

    result = pipeline.result
    assert result is not None
    payload = result.model_dump_json(indent=2)

    _err()
    for line in summarize(result):
        _err(line)

    if out is not None:
        await asyncio.to_thread(_write, out, payload + "\n")
        _err(f"\nScanResult JSON written to {out}")
    else:
        print(payload, flush=True)
    return EXIT_OK


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan one URL and emit a ScanResult as JSON")
    scan.add_argument("url", help="http(s) URL to scan")
    scan.add_argument(
        "--out", type=Path, default=None, help="write the JSON here instead of stdout"
    )
    scan.add_argument("-v", "--verbose", action="store_true", help="log scanner internals")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.command == "scan":
        return asyncio.run(_scan(args.url, args.out))
    return EXIT_FAILED  # pragma: no cover - argparse enforces the subcommand


if __name__ == "__main__":
    sys.exit(main())
