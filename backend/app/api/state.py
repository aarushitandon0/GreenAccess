"""The objects every route shares, created once per application lifespan.

Single responsibility: hold them in one typed place, so routes do not reach
into ``app.state`` by string.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from app.api.events import EventHub
from app.api.ratelimit import SlidingWindowLimiter
from app.api.runner import ScanRunner
from app.config import Settings
from app.db.repository import ScanRepository
from app.security.ssrf import ValidatedUrl

__all__ = ["AppState", "Preflight", "get_state"]

#: Validates a submitted URL and its redirect chain; raises ``UrlBlocked``.
Preflight = Callable[[str, tuple[str, ...]], Awaitable[ValidatedUrl]]


@dataclass
class AppState:
    settings: Settings
    repository: ScanRepository
    hub: EventHub
    runner: ScanRunner
    limiter: SlidingWindowLimiter
    preflight: Preflight


def get_state(request: Request) -> AppState:
    """FastAPI dependency: the running application's shared state."""
    state: AppState = request.app.state.greenaccess
    return state
