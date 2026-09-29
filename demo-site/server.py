#!/usr/bin/env python3
"""Static file server for the Daily Herald demo (MASTERSPEC §11).

Two instances run side by side:

    python demo-site/server.py --port 8081
    python demo-site/server.py --port 8082 --root demo-site/third-party --strip-prefix /t

Compression
-----------
By default this server sends CSS and JS **uncompressed**, on purpose. That is
planted defect UNCOMPRESSED-01, and it is what the uncompressed_text detector
(MASTERSPEC §7.3) is expected to find. ``--no-compress`` states that default
explicitly; ``--compress`` turns gzip on, which is how you can see the defect
disappear without editing anything.

Nothing here is meant for production use.
"""

from __future__ import annotations

import argparse
import gzip
import mimetypes
import socket
import sys
import threading
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "demo-site"

# Types we would compress if compression were enabled.
COMPRESSIBLE = {
    ".css",
    ".js",
    ".html",
    ".htm",
    ".json",
    ".svg",
    ".txt",
    ".xml",
    ".map",
}

# Below this, gzip costs more than it saves.
MIN_COMPRESS_BYTES = 256


class DemoRequestHandler(SimpleHTTPRequestHandler):
    """Serves the demo tree, optionally gzipping text responses."""

    server_version = "DailyHeraldDemo/1.0"
    protocol_version = "HTTP/1.1"

    def __init__(
        self,
        *args: object,
        directory: str,
        compress: bool,
        strip_prefix: str,
        **kwargs: object,
    ) -> None:
        self.compress = compress
        self.strip_prefix = strip_prefix
        super().__init__(*args, directory=directory, **kwargs)  # type: ignore[arg-type]

    # -- routing ---------------------------------------------------------- #

    def translate_path(self, path: str) -> str:
        """Drop the mount prefix (``/t`` for the third-party host) before lookup."""
        if self.strip_prefix and path.startswith(self.strip_prefix):
            path = path[len(self.strip_prefix) :] or "/"
        return super().translate_path(path)

    # -- response --------------------------------------------------------- #

    def _client_accepts_gzip(self) -> bool:
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def do_GET(self) -> None:
        target = Path(self.translate_path(self.path))
        if target.is_dir():
            target = target / "index.html"

        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        try:
            body = target.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type = f"{content_type}; charset=utf-8"

        encoding: str | None = None
        if (
            self.compress
            and self._client_accepts_gzip()
            and target.suffix.lower() in COMPRESSIBLE
            and len(body) >= MIN_COMPRESS_BYTES
        ):
            # mtime=0 keeps the output byte-identical between runs.
            body = gzip.compress(body, compresslevel=6, mtime=0)
            encoding = "gzip"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Vary", "Accept-Encoding")
        # No caching, so a rescan measures the same bytes every time.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        # The trackers are fetched cross-origin by the page on :8081.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def log_message(self, format: str, *args: object) -> None:
        if self.server_quiet:
            return
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    server_quiet = False


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    """A threading HTTP server on IPv6, for the ``::1`` that `localhost` names."""

    address_family = socket.AF_INET6


def build_server(
    *, port: int, root: Path, compress: bool, strip_prefix: str, quiet: bool
) -> ThreadingHTTPServer:
    if not root.is_dir():
        raise SystemExit(f"Root directory does not exist: {root}")

    # Accept "t", "/t" or "/t/" and normalise to "/t". Tolerating the bare form
    # matters because MSYS shells rewrite a leading-slash argument into a path.
    normalised_prefix = strip_prefix.strip("/")
    handler_class = partial(
        DemoRequestHandler,
        directory=str(root),
        compress=compress,
        strip_prefix=f"/{normalised_prefix}" if normalised_prefix else "",
    )
    DemoRequestHandler.server_quiet = quiet

    # Threading is not optional here. This handler speaks HTTP/1.1 with
    # keep-alive, and a browser opens several parallel connections and holds
    # them open. A single-threaded HTTPServer blocks on the first one and the
    # page never finishes loading -- curl survives it only because it makes one
    # request and closes.
    #
    # Bind loopback rather than 0.0.0.0: this server has no business being
    # reachable from the network.
    #
    # Prefer IPv6 loopback. `localhost` resolves to ::1 before 127.0.0.1 on
    # Windows, and the SSRF guard deliberately pins a request to the single
    # address it validated instead of falling back to the next one -- falling
    # back is precisely the hole that makes DNS rebinding work. Chromium's happy
    # eyeballs papers over an IPv4-only server during a scan, so the page scans
    # fine and then the patcher's plain HTTP fetch of the same URL fails with
    # "all connection attempts failed". Listening where the name actually points
    # fixes it at the source instead of spreading IPv4 literals through the
    # config. Falls back to IPv4 where IPv6 is unavailable.
    server: ThreadingHTTPServer
    try:
        server = _IPv6ThreadingHTTPServer(("::1", port), handler_class)  # type: ignore[arg-type]
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_class)  # type: ignore[arg-type]
    server.daemon_threads = True
    return server


def serve_forever_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    """Run `server` on a daemon thread. Used by the smoke tests."""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--strip-prefix",
        default="",
        help=(
            "URL path segment to remove before looking a file up, e.g. 't' for "
            "/t/analytics.js. Give it without a leading slash: MSYS-based shells "
            "on Windows rewrite a bare '/t' argument into a filesystem path."
        ),
    )
    compression = parser.add_mutually_exclusive_group()
    compression.add_argument(
        "--no-compress",
        dest="compress",
        action="store_false",
        help="serve CSS/JS uncompressed (the default; planted defect UNCOMPRESSED-01)",
    )
    compression.add_argument(
        "--compress",
        dest="compress",
        action="store_true",
        help="enable gzip, which removes defect UNCOMPRESSED-01",
    )
    parser.set_defaults(compress=False)
    parser.add_argument("--quiet", action="store_true", help="suppress the request log")
    args = parser.parse_args()

    server = build_server(
        port=args.port,
        root=args.root.resolve(),
        compress=args.compress,
        strip_prefix=args.strip_prefix,
        quiet=args.quiet,
    )

    state = "gzip enabled" if args.compress else "UNCOMPRESSED (defect UNCOMPRESSED-01)"
    print(f"Daily Herald demo on http://localhost:{args.port}/  root={args.root}  {state}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
