"""Scanner command line.

    python -m app.cli scan <url> [--out result.json]

Prints a readable summary as the scan progresses and optionally writes the full
:class:`~app.models.ScanResult` as JSON. This is what ``make scan`` runs, and it
is the phase-1 way to exercise the whole pipeline without an API.

This module is the one place in the app allowed to write to stdout
(CLAUDE.md: "no print (use logging)").
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.models import ScanResult, StepStatus, TradeoffType
from app.scanner.pipeline import ScanFailed, ScanPipeline


def _enable_unicode_stdout() -> bool:
    """Switch stdout to UTF-8 if we can. Returns whether symbols are safe to use.

    A Windows console defaults to cp1252, which cannot encode the tick and
    cross marks below. Rather than mangling the output or crashing mid-scan, we
    reconfigure the stream where possible and fall back to ASCII where not.
    """
    stream = sys.stdout
    encoding = (getattr(stream, "encoding", "") or "").lower()
    if "utf" in encoding:
        return True
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return False
        return True
    return False


_UNICODE_OK = _enable_unicode_stdout()

STATUS_MARK = (
    {
        StepStatus.RUNNING: "…",
        StepStatus.OK: "✓",
        StepStatus.ERROR: "✗",
        StepStatus.SKIPPED: "-",
    }
    if _UNICODE_OK
    else {
        StepStatus.RUNNING: "..",
        StepStatus.OK: "ok",
        StepStatus.ERROR: "XX",
        StepStatus.SKIPPED: "--",
    }
)

#: CO2 subscript renders as mojibake on a cp1252 console.
CO2 = "CO₂" if _UNICODE_OK else "CO2"


def _human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1000 or unit == "GB":
            return f"{value:,.1f} {unit}" if unit != "B" else f"{int(value):,} B"
        value /= 1000
    return f"{value:,.1f} GB"


def print_summary(result: ScanResult, url: str) -> None:
    """Print the human-readable digest of a completed scan."""
    carbon = result.carbon

    print()
    print("=" * 72)
    print(f"  {url}")
    print("=" * 72)

    print("\nACCESSIBILITY (automated checks only)")
    print(f"  violated rules     {result.a11y.unique_rules}")
    print(f"  failing elements   {result.a11y.total_nodes}")
    print(f"  needs review       {result.a11y.incomplete_count} (recorded, not scored)")
    if result.a11y.counts_by_impact:
        by_impact = ", ".join(
            f"{impact.value}: {count}" for impact, count in result.a11y.counts_by_impact.items()
        )
        print(f"  by impact          {by_impact}")
    for violation in result.a11y.violations[:8]:
        print(f"    [{violation.impact.value:<8}] {violation.rule_id} ({violation.node_count})")
    if len(result.a11y.violations) > 8:
        print(f"    ... and {len(result.a11y.violations) - 8} more")

    keyboard = result.keyboard
    print("\nKEYBOARD (our own Tab crawl, not axe)")
    print(f"  tabs pressed       {keyboard.tabs_pressed}")
    print(f"  elements reached   {keyboard.reached_count}")
    print(
        f"  focus trap         {'YES — ' + (keyboard.trap_container or '?') if keyboard.trap_detected else 'none detected'}"
    )
    print(f"  never reached      {keyboard.unreachable_interactive_count}")
    print(f"  no focus indicator {keyboard.focus_visible_missing_count}")

    print("\nCARBON (estimates from the Sustainable Web Design model)")
    print(f"  transfer size      {_human_bytes(carbon.total_bytes)}")
    print(f"  requests           {carbon.request_count}")
    if carbon.by_type:
        for resource_type, byte_count in sorted(carbon.by_type.items(), key=lambda kv: -kv[1]):
            print(f"    {resource_type.value:<6} {_human_bytes(byte_count):>12}")
    print(
        f"  third party        {carbon.third_party.requests} request(s), "
        f"{_human_bytes(carbon.third_party.bytes)} from {', '.join(carbon.third_party.hosts) or 'none'}"
    )
    print(f"  fonts              {carbon.fonts.count} file(s), {_human_bytes(carbon.fonts.bytes)}")
    print(f"  g {CO2}e per view    {carbon.grams_per_view:.4f} g  (estimate)")
    print(f"  first visit        {carbon.grams_first_visit:.4f} g")
    print(f"  return visit       {carbon.grams_return_visit:.4f} g")

    green = result.green
    green_text = (
        "unknown (lookup unavailable)"
        if green.source.value == "unavailable"
        else (f"yes — {green.hosted_by}" if green.green else "not listed as green")
    )
    print(f"  green hosting      {green_text}")

    if carbon.detections:
        print("\nDETECTIONS (estimated savings, not measured)")
        for finding in sorted(carbon.detections, key=lambda d: -d.estimated_saving_bytes):
            saving = (
                _human_bytes(finding.estimated_saving_bytes) if finding.saves_bytes else "no bytes"
            )
            print(f"  {finding.detector:<26} {saving:>12}  {finding.summary[:70]}")

    print("\nSCORES")
    if result.scores.is_placeholder:
        print("  not computed — this result carries placeholder scores.")
    else:
        print(f"  accessibility      {result.scores.a11y}  (automated checks)")
        print(
            f"  carbon             {result.scores.carbon} "
            f"(grade {result.scores.carbon_grade.value}, estimate)"
        )
        print(f"  combined           {result.scores.combined}")

    synergies = [f for f in result.tradeoffs if f.type is TradeoffType.SYNERGY]
    print(
        f"\nTRADE-OFFS ({len(synergies)} synergy, "
        f"{len(result.tradeoffs) - len(synergies)} tension; byte and gram deltas are estimates)"
    )
    if not result.tradeoffs:
        print("  none")
    for finding in result.tradeoffs:
        delta = finding.carbon_delta_bytes
        sign = "-" if delta > 0 else ("+" if delta < 0 else " ")
        # carbon_delta_* is positive when the fix saves; print it as the change
        # to the page, so a saving reads as a minus in both columns.
        bytes_text = f"{sign}{_human_bytes(abs(delta))}" if delta else "0 B"
        grams_text = f"{sign}{abs(finding.carbon_delta_grams):.4f} g"
        print(
            f"  [{finding.type.value:<7}] {finding.rule_id:<20} {bytes_text:>12}  "
            f"{grams_text:>10}  fix: {finding.recommended_fix_id or '-'}"
        )

    print(f"\nengines: {result.engine_versions.model_dump()}")
    print()


async def _scan(url: str, out: Path | None, settings: Settings) -> int:
    pipeline = ScanPipeline(url=url, settings=settings)

    print(f"Scanning {url}\n")
    try:
        async for event in pipeline.run():
            if event.status is StepStatus.RUNNING:
                continue
            mark = STATUS_MARK.get(event.status, "?")
            timing = f"{event.ms:>6} ms" if event.ms else " " * 9
            print(f"  {mark} {event.name:<10} {timing}  {event.detail}")
    except ScanFailed as failure:
        print(f"\nerror: [{failure.code}] {failure}", file=sys.stderr)
        return 1

    if pipeline.result is None:
        print("\nerror: scan produced no result", file=sys.stderr)
        return 1

    print_summary(pipeline.result, url)

    if out is not None:
        # Filesystem work is blocking, so keep it off the event loop.
        payload = pipeline.result.model_dump_json(indent=2, by_alias=False)
        written = await asyncio.to_thread(_write_result, out, payload)
        print(f"wrote {out} ({written:,} bytes)")

    return 0


def _write_result(out: Path, payload: str) -> int:
    """Write the scan JSON and report its size. Runs in a worker thread."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    return out.stat().st_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="scan one URL")
    scan_parser.add_argument("url")
    scan_parser.add_argument("--out", type=Path, help="write the full ScanResult JSON here")
    scan_parser.add_argument(
        "--allow-local",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "permit a local host, as ALLOWED_LOCAL_HOSTS does. Repeatable. "
            "Needed to scan the demo site at localhost:8081."
        ),
    )
    scan_parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    settings = get_settings()
    if args.allow_local:
        extra = tuple(host.strip().lower() for host in args.allow_local if host.strip())
        settings = Settings(
            **{
                **settings.__dict__,
                "allowed_local_hosts": tuple({*settings.allowed_local_hosts, *extra}),
            }
        )

    if args.command == "scan":
        return asyncio.run(_scan(args.url, args.out, settings))

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
