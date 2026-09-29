#!/usr/bin/env python3
"""Run the GreenAccess development stack.

Starts, and keeps together in one terminal:

    backend       http://localhost:8000    FastAPI (uvicorn, reload)
    frontend      http://localhost:5173    Vite dev server
    demo-site     http://localhost:8081    The Daily Herald
    demo-trackers http://localhost:8082    third-party tracker host

Every child's output is prefixed with its name. Ctrl-C stops all of them.

This exists so the Makefile does not have to orchestrate background processes
in shell, which is not portable between cmd.exe and sh.

Usage:
    python scripts/dev.py
    python scripts/dev.py --only demo
    python scripts/dev.py --only backend --only frontend
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"

VENV_PYTHON = REPO_ROOT / "backend" / ".venv" / ("Scripts" if IS_WINDOWS else "bin") / "python"
if IS_WINDOWS:
    VENV_PYTHON = VENV_PYTHON.with_suffix(".exe")

NPM = "npm.cmd" if IS_WINDOWS else "npm"

# ANSI colours, one per service, so interleaved logs stay readable.
COLOURS = {
    "backend": "\033[36m",
    "frontend": "\033[35m",
    "demo-site": "\033[32m",
    "demo-trackers": "\033[33m",
}
RESET = "\033[0m"


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: Path
    group: str


def _python() -> str:
    """The venv interpreter if it exists, otherwise whatever is running us."""
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def services() -> list[Service]:
    python = _python()
    return [
        Service(
            name="backend",
            command=[
                python,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--reload",
            ],
            cwd=REPO_ROOT / "backend",
            group="app",
        ),
        Service(
            name="frontend",
            command=[NPM, "run", "dev"],
            cwd=REPO_ROOT / "frontend",
            group="app",
        ),
        Service(
            name="demo-site",
            command=[python, "demo-site/server.py", "--port", "8081", "--quiet"],
            cwd=REPO_ROOT,
            group="demo",
        ),
        Service(
            name="demo-trackers",
            command=[
                python,
                "demo-site/server.py",
                "--port",
                "8082",
                "--root",
                "demo-site/third-party",
                # Given without a leading slash on purpose: see server.py.
                "--strip-prefix",
                "t",
                "--quiet",
            ],
            cwd=REPO_ROOT,
            group="demo",
        ),
    ]


def _pump(service: Service, process: subprocess.Popen[str], use_colour: bool) -> None:
    """Forward one child's output, prefixed with the service name."""
    colour = COLOURS.get(service.name, "") if use_colour else ""
    reset = RESET if use_colour and colour else ""
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(f"{colour}[{service.name}]{reset} {line.rstrip()}\n")
        sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="run only these services or groups (backend, frontend, demo, app)",
    )
    parser.add_argument("--no-colour", action="store_true")
    args = parser.parse_args()

    wanted = {item.lower() for item in args.only}
    selected = [
        service
        for service in services()
        if not wanted or service.name in wanted or service.group in wanted
    ]
    if not selected:
        print(f"No services matched {sorted(wanted)}", file=sys.stderr)
        return 2

    use_colour = not args.no_colour and sys.stdout.isatty()
    running: list[tuple[Service, subprocess.Popen[str]]] = []

    print("Starting:")
    for service in selected:
        print(f"  {service.name}")

    try:
        for service in selected:
            try:
                process = subprocess.Popen(
                    service.command,
                    cwd=service.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                print(
                    f"could not start {service.name}: {service.command[0]} not found.\n"
                    "Run `make install` first.",
                    file=sys.stderr,
                )
                raise SystemExit(1) from None
            running.append((service, process))
            threading.Thread(target=_pump, args=(service, process, use_colour), daemon=True).start()

        print("\nCtrl-C to stop everything.\n")

        # Exit as soon as any child dies, so a crash is not hidden by the others.
        while True:
            for service, process in running:
                code = process.poll()
                if code is not None:
                    print(f"\n[{service.name}] exited with code {code}", file=sys.stderr)
                    return code or 1
            time.sleep(0.4)

    except KeyboardInterrupt:
        print("\nstopping...")
        return 0
    finally:
        for _, process in running:
            if process.poll() is None:
                try:
                    if IS_WINDOWS:
                        process.terminate()
                    else:
                        process.send_signal(signal.SIGTERM)
                except OSError:
                    pass
        deadline = time.time() + 6
        for _, process in running:
            remaining = max(0.0, deadline - time.time())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
