"""In-memory event bus for scan progress (MASTERSPEC §12 SSE).

Single responsibility: record every event a scan emits, in order, and let any
number of subscribers read them, from the start or from where a reconnecting
client left off.

Replay is the point. A client usually opens the SSE stream a moment after
``POST /api/scans`` returns, by which time ``validate`` may already be over;
without replay it would never see it. Each event carries a sequence id (from 1)
that is sent as the SSE ``id`` field, so a browser ``EventSource`` that drops
and reconnects sends ``Last-Event-ID`` and resumes without duplicates.

Logs are kept in memory only. A scan whose log has been evicted, or that
finished before a restart, is still served from the database by the route,
which synthesises its terminal event.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from typing import Final, Literal

from pydantic import BaseModel

__all__ = ["DEFAULT_RETAIN", "EventHub", "EventName", "ScanEvent", "ScanEventLog"]

EventName = Literal["step", "done", "error"]

#: Closed logs kept for replay before the oldest are dropped.
DEFAULT_RETAIN: Final[int] = 256


@dataclass(frozen=True)
class ScanEvent:
    """One SSE event: sequence id, event name, JSON payload."""

    id: int
    event: EventName
    data: str


class ScanEventLog:
    """Every event for one scan, append-only, closed after the terminal event."""

    def __init__(self) -> None:
        self._events: list[ScanEvent] = []
        self._closed = False
        self._changed = asyncio.Condition()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def events(self) -> tuple[ScanEvent, ...]:
        return tuple(self._events)

    async def publish(self, event: EventName, payload: BaseModel) -> ScanEvent:
        async with self._changed:
            if self._closed:
                raise RuntimeError("cannot publish to a closed event log")
            item = ScanEvent(id=len(self._events) + 1, event=event, data=payload.model_dump_json())
            self._events.append(item)
            self._changed.notify_all()
            return item

    async def close(self) -> None:
        """No more events will come. Subscribers drain what is left and stop."""
        async with self._changed:
            self._closed = True
            self._changed.notify_all()

    def _has_news(self, index: int) -> bool:
        return len(self._events) > index or self._closed

    async def subscribe(self, after_id: int = 0) -> AsyncIterator[ScanEvent]:
        """Yield every event with id > `after_id`, then follow live until closed."""
        index = max(0, after_id)
        while True:
            async with self._changed:
                await self._changed.wait_for(partial(self._has_news, index))
                batch = self._events[index:]
                closed = self._closed
            for item in batch:
                yield item
            index += len(batch)
            # Nothing is published after close, so the snapshot was the end.
            if closed:
                return


class EventHub:
    """Event logs for recent scans, by scan id."""

    def __init__(self, retain: int = DEFAULT_RETAIN) -> None:
        self.retain = retain
        self._logs: OrderedDict[str, ScanEventLog] = OrderedDict()

    def create(self, scan_id: str) -> ScanEventLog:
        log = ScanEventLog()
        self._logs[scan_id] = log
        self._evict()
        return log

    def get(self, scan_id: str) -> ScanEventLog | None:
        return self._logs.get(scan_id)

    def _evict(self) -> None:
        """Drop the oldest *closed* logs beyond `retain`. Open ones always stay."""
        excess = len(self._logs) - self.retain
        if excess <= 0:
            return
        for scan_id in [sid for sid, log in self._logs.items() if log.closed][:excess]:
            del self._logs[scan_id]
