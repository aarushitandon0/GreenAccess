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
import socket
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


#: The demo hosts the backend may scan in development. The SSRF guard blocks
#: localhost by default; CLAUDE.md allows dev hosts only through an explicit
#: ALLOWED_LOCAL_HOSTS list, so this is that list, used when none is set.
#: localhost:8000 is the backend itself: the patch re-scan visits the patched
#: copy it serves at /patched (MASTERSPEC §8.3).
#: The demo servers listen on ::1, which is where `localhost` points first, so
#: they are named here as `localhost` and the demo's tracker host stays a
#: distinct origin the way MASTERSPEC 11 intends. The backend itself is named by
#: IPv4 literal because uvicorn binds 127.0.0.1: the patch re-scan fetches the
#: patched copy from it, and the SSRF guard pins the one address it validated
#: rather than falling back to another.
DEV_ALLOWED_LOCAL_HOSTS = "localhost:8081,localhost:8082,127.0.0.1:8000"


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: Path
    group: str
    #: The port this service listens on, checked before anything is started.
    port: int
    #: Extra environment for this service, applied only where not already set.
    env_defaults: tuple[tuple[str, str], ...] = ()


def _port_is_taken(port: int) -> bool:
    """True if something is already listening on `port` on the loopback.

    Both loopback families are tried because the services here do not agree:
    uvicorn binds 127.0.0.1 while the demo servers answer on ::1, which is
    where `localhost` points first. Connecting is a surer test than binding,
    which SO_REUSEADDR and dual-stack sockets both make ambiguous.
    """
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue  # No IPv6 on this host, say; the other family still answers.
    return False


def _check_ports(selected: list[Service]) -> list[Service]:
    """The selected services whose port is already in use.

    Checked up front rather than discovered on the way down. Starting four
    processes and then dying on the one that could not bind leaves a confusing
    mess: the survivors are killed on the way out, while whatever already held
    the port keeps answering, so the stack looks half alive and the real cause
    ("port 8000 is taken") is a single line in the middle of a combined log.
    """
    return [service for service in selected if _port_is_taken(service.port)]


def _python() -> str:
    """The venv interpreter if it exists, otherwise whatever is running us."""
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _backend_command(python: str) -> list[str]:
    """Uvicorn's argv for the dev backend.

    ``--reload`` is dropped on Windows, and the scanner is the reason. Uvicorn
    picks its event loop with ``asyncio_loop_factory(use_subprocess=...)``, and
    reload mode sets ``use_subprocess=True``, which on Windows selects
    ``SelectorEventLoop``. That loop raises ``NotImplementedError`` from
    ``create_subprocess_exec``, so Playwright can never launch a browser and
    every scan fails in ``load`` with an internal error.

    Reloading still works everywhere else, including the Docker image the
    project actually targets, which is Linux and unaffected. On Windows the
    trade is hot reload for a backend that can scan; restart `make dev` after
    changing backend code.
    """
    command = [
        python,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    if not IS_WINDOWS:
        command.append("--reload")
    return command


def services() -> list[Service]:
    python = _python()
    return [
        Service(
            name="backend",
            command=_backend_command(python),
            cwd=REPO_ROOT / "backend",
            group="app",
            port=8000,
            env_defaults=(
                ("ALLOWED_LOCAL_HOSTS", DEV_ALLOWED_LOCAL_HOSTS),
                # Uvicorn binds 127.0.0.1, so the patched copy has to be
                # advertised at that address for the re-scan to reach it.
                ("PATCHED_BASE_URL", "http://127.0.0.1:8000/patched"),
            ),
        ),
        Service(
            name="frontend",
            command=[NPM, "run", "dev"],
            cwd=REPO_ROOT / "frontend",
            group="app",
            port=5173,
        ),
        Service(
            name="demo-site",
            command=[python, "demo-site/server.py", "--port", "8081", "--quiet"],
            cwd=REPO_ROOT,
            group="demo",
            port=8081,
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
            port=8082,
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

    taken = _check_ports(selected)
    if taken:
        print("Cannot start: these ports are already in use.\n", file=sys.stderr)
        for service in taken:
            print(f"  {service.port}  needed by {service.name}", file=sys.stderr)
        print(
            "\nUsually a previous `make dev` that did not shut down. Stop it, or"
            " find the process holding the port:\n"
            "  Windows      Get-NetTCPConnection -LocalPort <port> -State Listen\n"
            "  macOS, Linux lsof -nP -iTCP:<port> -sTCP:LISTEN\n"
            "\nNothing was started.",
            file=sys.stderr,
        )
        return 1

    use_colour = not args.no_colour and sys.stdout.isatty()
    running: list[tuple[Service, subprocess.Popen[str]]] = []

    print("Starting:")
    for service in selected:
        print(f"  {service.name}")

    try:
        for service in selected:
            try:
                env = dict(os.environ)
                for key, value in service.env_defaults:
                    env.setdefault(key, value)
                process = subprocess.Popen(
                    service.command,
                    cwd=service.cwd,
                    env=env,
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
