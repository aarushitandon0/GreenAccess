"""Which demo targets may get the assisted fixes, kinds 11 and 12 (MASTERSPEC §8.1).

Single responsibility: answer "is this the Daily Herald, and is this one of
its known banners or trackers?" from ``allowlist.json``. Nothing outside that
file is ever auto-replaced (text in images) or auto-removed (third-party
scripts); those fixes stay suggestions marked ``manual_review``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["DemoAllowList", "load_allowlist"]

_PATH = Path(__file__).with_name("allowlist.json")


@dataclass(frozen=True)
class DemoAllowList:
    demo_hosts: frozenset[str]
    demo_ports: frozenset[int]
    text_in_image_paths: frozenset[str]
    tracker_hosts: frozenset[str]
    tracker_ports: frozenset[int]
    removable_script_paths: frozenset[str]

    @staticmethod
    def _host_port(url: str) -> tuple[str, int | None, str]:
        parts = urlsplit(url)
        return (parts.hostname or "").lower(), parts.port, parts.path

    def is_demo_page(self, page_url: str) -> bool:
        host, port, _ = self._host_port(page_url)
        return host in self.demo_hosts and port in self.demo_ports

    def text_in_image_allowed(self, page_url: str, image_url: str) -> bool:
        if not self.is_demo_page(page_url):
            return False
        host, port, path = self._host_port(image_url)
        return (
            host in self.demo_hosts and port in self.demo_ports and path in self.text_in_image_paths
        )

    def script_removal_allowed(self, page_url: str, script_url: str) -> bool:
        if not self.is_demo_page(page_url):
            return False
        host, port, path = self._host_port(script_url)
        return (
            host in self.tracker_hosts
            and port in self.tracker_ports
            and path in self.removable_script_paths
        )


@lru_cache(maxsize=1)
def load_allowlist() -> DemoAllowList:
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    return DemoAllowList(
        demo_hosts=frozenset(raw["demo_hosts"]),
        demo_ports=frozenset(raw["demo_ports"]),
        text_in_image_paths=frozenset(raw["text_in_image_paths"]),
        tracker_hosts=frozenset(raw["tracker_hosts"]),
        tracker_ports=frozenset(raw["tracker_ports"]),
        removable_script_paths=frozenset(raw["removable_script_paths"]),
    )
