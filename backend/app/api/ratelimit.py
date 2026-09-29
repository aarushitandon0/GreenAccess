"""Per-client rate limiting for scan creation (MASTERSPEC §12: 10 scans/min/IP).

Single responsibility: decide whether one more request from a key is allowed
inside a sliding window. In memory and per process, which is all a single
uvicorn worker needs; it resets on restart, and that is acceptable for a limit
whose job is to stop one client monopolising two scan slots.

Keyed on the TCP peer address. ``X-Forwarded-For`` is deliberately ignored:
it is set by the client unless a trusted proxy rewrites it, so honouring it
would let anyone pick a fresh key per request.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from typing import Final

__all__ = ["SCANS_PER_MINUTE", "WINDOW_S", "SlidingWindowLimiter"]

#: MASTERSPEC §12.
SCANS_PER_MINUTE: Final[int] = 10
WINDOW_S: Final[float] = 60.0


class SlidingWindowLimiter:
    """Allow at most ``limit`` hits per key in any ``window_s``-second span."""

    def __init__(
        self,
        limit: int = SCANS_PER_MINUTE,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        return hits

    def hit(self, key: str) -> int | None:
        """Record a request. Returns None if allowed, else seconds to wait.

        A refused request is not recorded, so a client that backs off for the
        advertised time is guaranteed a slot.
        """
        now = self._clock()
        hits = self._prune(key, now)
        if len(hits) >= self.limit:
            return max(1, math.ceil(self.window_s - (now - hits[0])))
        hits.append(now)
        self._sweep(now)
        return None

    def _sweep(self, now: float) -> None:
        """Forget keys with no hits in the window, so memory tracks active clients."""
        stale = [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window_s]
        for key in stale:
            del self._hits[key]
