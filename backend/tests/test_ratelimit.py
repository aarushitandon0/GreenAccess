"""Sliding-window rate limiter (MASTERSPEC §12: 10 scans/min/IP), table-driven."""

from __future__ import annotations

import pytest

from app.api.ratelimit import SCANS_PER_MINUTE, WINDOW_S, SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_spec_values() -> None:
    assert SCANS_PER_MINUTE == 10
    assert WINDOW_S == 60.0


@pytest.mark.parametrize(
    ("hits_at", "expected"),
    [
        # Ten in a burst pass, the eleventh waits for the first to age out.
        ([0.0] * 10 + [0.0], [None] * 10 + [60]),
        ([0.0] * 10 + [59.5], [None] * 10 + [1]),
        ([0.0] * 10 + [60.0], [None] * 11),
        # Sliding, not fixed: hits spread over the minute still cap at ten.
        ([float(t) for t in range(0, 50, 5)] + [55.0], [None] * 10 + [5]),
    ],
)
def test_sliding_window(hits_at: list[float], expected: list[int | None]) -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(clock=clock)
    results = []
    for t in hits_at:
        clock.now = t
        results.append(limiter.hit("ip"))
    assert results == expected


def test_refused_hits_are_not_recorded() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(limit=1, clock=clock)
    assert limiter.hit("ip") is None
    for t in (10.0, 20.0, 30.0):
        clock.now = t
        assert limiter.hit("ip") is not None
    clock.now = 60.0
    assert limiter.hit("ip") is None, "backing off for Retry-After must work"


def test_keys_are_independent_and_stale_keys_are_forgotten() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(limit=1, clock=clock)
    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None
    clock.now = 120.0
    limiter.hit("c")
    assert set(limiter._hits) == {"c"}


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(limit=0)
