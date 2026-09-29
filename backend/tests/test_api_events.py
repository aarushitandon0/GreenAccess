"""Event bus tests: ordering, replay, resume, close, eviction (MASTERSPEC §12 SSE)."""

from __future__ import annotations

import asyncio

import pytest

from app.api.events import EventHub, ScanEventLog
from app.models import StepEvent, StepStatus


def step(name: str) -> StepEvent:
    return StepEvent(name=name, status=StepStatus.OK)


async def collect(log: ScanEventLog, after: int = 0) -> list[str]:
    return [item.event + ":" + item.data for item in [e async for e in log.subscribe(after)]]


async def test_ids_are_sequential_from_one() -> None:
    log = ScanEventLog()
    ids = [(await log.publish("step", step(n))).id for n in ("a", "b", "c")]
    assert ids == [1, 2, 3]


async def test_late_subscriber_replays_everything_then_stops_at_close() -> None:
    log = ScanEventLog()
    for name in ("validate", "load"):
        await log.publish("step", step(name))
    await log.close()
    items = [e async for e in log.subscribe()]
    assert [e.id for e in items] == [1, 2]
    assert '"validate"' in items[0].data


async def test_live_subscriber_sees_events_published_after_it_joined() -> None:
    log = ScanEventLog()
    await log.publish("step", step("validate"))
    reader = asyncio.create_task(collect(log))
    await asyncio.sleep(0)
    await log.publish("step", step("load"))
    await log.publish("done", step("x"))
    await log.close()
    events = await asyncio.wait_for(reader, 2)
    assert [e.split(":", 1)[0] for e in events] == ["step", "step", "done"]


async def test_many_subscribers_get_identical_streams() -> None:
    log = ScanEventLog()
    readers = [asyncio.create_task(collect(log)) for _ in range(5)]
    await asyncio.sleep(0)
    for name in ("a", "b", "c"):
        await log.publish("step", step(name))
    await log.close()
    results = await asyncio.wait_for(asyncio.gather(*readers), 2)
    assert all(result == results[0] for result in results)
    assert len(results[0]) == 3


@pytest.mark.parametrize(("after", "expected"), [(0, [1, 2, 3]), (2, [3]), (3, []), (99, [])])
async def test_subscribe_after_id_resumes(after: int, expected: list[int]) -> None:
    log = ScanEventLog()
    for name in ("a", "b", "c"):
        await log.publish("step", step(name))
    await log.close()
    assert [e.id async for e in log.subscribe(after)] == expected


async def test_open_log_with_nothing_new_waits_rather_than_ending() -> None:
    log = ScanEventLog()
    await log.publish("step", step("a"))
    reader = asyncio.create_task(collect(log, after=1))
    await asyncio.sleep(0.05)
    assert not reader.done()
    await log.close()
    assert await asyncio.wait_for(reader, 2) == []


async def test_publishing_after_close_is_an_error() -> None:
    log = ScanEventLog()
    await log.close()
    with pytest.raises(RuntimeError):
        await log.publish("step", step("late"))


async def test_hub_evicts_only_closed_logs_oldest_first() -> None:
    hub = EventHub(retain=2)
    first, second = hub.create("1"), hub.create("2")
    await first.close()
    hub.create("3")
    assert hub.get("1") is None, "oldest closed log is evicted"
    assert hub.get("2") is second, "open logs are never evicted"
    hub.create("4")
    assert hub.get("2") is second and hub.get("3") is not None and hub.get("4") is not None
