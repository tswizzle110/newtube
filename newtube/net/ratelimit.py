"""Global rate limiting with jitter for requests to YouTube / SponsorBlock.

Two lanes:

* ``INTERACTIVE``: you clicked something. Always allowed, but spaced out.
* ``BACKGROUND``: RSS polling, metadata backfill, related-video lookups.
  Yields to interactive requests and is subject to a daily cap, so the
  server can never burst hundreds of requests at YouTube.

Time, sleeping and randomness are injected so the logic is unit-testable.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import enum
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable


class Lane(enum.Enum):
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


class DailyCapReached(Exception):
    """Background request budget for today is used up."""


class RateLimiter:
    def __init__(
        self,
        min_interval: float,
        jitter: float,
        daily_cap: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        today: Callable[[], dt.date] = dt.date.today,
    ) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self.daily_cap = daily_cap
        self._clock, self._sleep, self._rand, self._today = clock, sleep, rand, today
        self._lock = asyncio.Lock()
        self._next_ok = 0.0
        self._interactive_waiting = 0
        self._counts: dict[dt.date, int] = defaultdict(int)

    def used_today(self) -> int:
        return self._counts[self._today()]

    def remaining_today(self) -> int:
        return max(0, self.daily_cap - self.used_today())

    async def acquire(self, lane: Lane = Lane.INTERACTIVE) -> None:
        """Wait until a request may be sent. Raises DailyCapReached for background."""
        if lane is Lane.BACKGROUND:
            if self.remaining_today() <= 0:
                raise DailyCapReached(f"background cap of {self.daily_cap}/day reached")
            # Let any interactive request go first.
            while self._interactive_waiting:
                await self._sleep(0.05)
        else:
            self._interactive_waiting += 1
        try:
            async with self._lock:
                wait = self._next_ok - self._clock()
                if wait > 0:
                    await self._sleep(wait)
                self._next_ok = self._clock() + self.min_interval + self._rand() * self.jitter
                if lane is Lane.BACKGROUND:
                    # Re-check: the cap may have been consumed while waiting.
                    if self.remaining_today() <= 0:
                        raise DailyCapReached(f"background cap of {self.daily_cap}/day reached")
                    self._counts[self._today()] += 1
        finally:
            if lane is Lane.INTERACTIVE:
                self._interactive_waiting -= 1
