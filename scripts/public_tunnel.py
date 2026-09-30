#!/usr/bin/env python3
"""Publish the GreenAccess production stack on a free public HTTPS URL.

Every hosting free tier that needs no credit card is too small for this
application: it wants a real Chromium and roughly 2 GB of RAM. So rather than
host it, this runs the production image locally through docker-compose.public.yml
and publishes it with a Cloudflare quick tunnel, which is free, needs no account
and issues a real HTTPS URL.

The tunnel has to come up first. PATCHED_BASE_URL is handed to the visitor's
browser in the After chapter, so it has to be the public URL, and the URL is not
known until cloudflared has issued one. The order is therefore:

    1. start cloudflared against the host port
    2. read the trycloudflare.com URL out of its log
    3. bring the stack up with PUBLIC_BASE_URL and PATCHED_BASE_URL set to it

Ctrl-C stops the tunnel and the stack.

Usage:
    python scripts/public_tunnel.py
    python scripts/public_tunnel.py --port 8100
    python scripts/public_tunnel.py --keep-up      # leave containers running
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.public.yml"
IS_WINDOWS = os.name == "nt"

URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# cloudflared is signed, so Windows Smart App Control permits it, unlike flyctl.
WINDOWS_CLOUDFLARED = Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe")


def find_cloudflared() -> str:
    """Locate the cloudflared binary, or explain how to install it."""
    found = shutil.which("cloudflared")
    if found:
        return found
    if IS_WINDOWS and WINDOWS_CLOUDFLARED.exists():
        return str(WINDOWS_CLOUDFLARED)
    sys.exit(
        "cloudflared not found. Install it with one of:\n"
        "  winget install Cloudflare.cloudflared      (Windows)\n"
        "  brew install cloudflared                   (macOS)\n"
        "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    )


def compose(*args: str, env: dict[str, str] | None = None) -> int:
    """Run docker compose against the public compose file."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    return subprocess.call(cmd, cwd=REPO_ROOT, env=env)


def start_tunnel(binary: str, port: int, log: Path) -> subprocess.Popen[bytes]:
    """Start a quick tunnel pointed at the host port."""
    handle = log.open("wb")
    return subprocess.Popen(
        [binary, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
        stdout=handle,
        stderr=subprocess.STDOUT,
        cwd=REPO_ROOT,
    )


def await_url(proc: subprocess.Popen[bytes], log: Path, timeout: float = 60.0) -> str:
    """Read the issued URL out of the cloudflared log, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            print(log.read_text(encoding="utf-8", errors="replace")[-2000:], file=sys.stderr)
            sys.exit(f"cloudflared exited with code {proc.returncode} before issuing a URL")
        match = URL_RE.search(log.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(0)
        time.sleep(1.0)
    proc.terminate()
    sys.exit(f"cloudflared did not issue a URL within {timeout:.0f}s. See {log}")


def main() -> int:
    # Unbuffered, so the URL banner appears when it is printed rather than when
    # the process exits. Without this a redirected stdout holds it until the end,
    # which hides the one line the operator is waiting for.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PUBLIC_PORT", "8100")),
        help="host port the stack is published on (default 8100; 8000 is taken by make dev)",
    )
    parser.add_argument(
        "--keep-up",
        action="store_true",
        help="leave the containers running after the tunnel closes",
    )
    args = parser.parse_args()

    binary = find_cloudflared()
    log = Path(tempfile.gettempdir()) / "greenaccess-cloudflared.log"

    print(f"starting tunnel via {binary}")
    proc = start_tunnel(binary, args.port, log)
    url = await_url(proc, log)

    env = {
        **os.environ,
        "PUBLIC_PORT": str(args.port),
        "PUBLIC_BASE_URL": url,
        # The After chapter hands this to the visitor's browser, so it must be
        # the public URL and not the container's own loopback.
        "PATCHED_BASE_URL": f"{url}/patched",
    }

    print("bringing up the stack (first run builds the image; that takes a while)")
    if compose("up", "-d", env=env) != 0:
        proc.terminate()
        return 1

    print()
    print("=" * 68)
    print(f"  GreenAccess is live at:  {url}")
    print("=" * 68)
    print()
    print("  This URL works while this process runs. Anyone with it can start")
    print("  scans, rate limited to 10 per minute per IP. The LLM stays offline")
    print("  unless you set LLM_OFFLINE=0 and a key, which would let any visitor")
    print("  spend your Anthropic credits.")
    print()
    print("  Ctrl-C stops the tunnel and the stack.")
    print()

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nstopping tunnel")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not args.keep_up:
            print("stopping the stack")
            compose("down", env=env)
        else:
            print(f"containers left running on http://localhost:{args.port}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        sys.exit(130)
