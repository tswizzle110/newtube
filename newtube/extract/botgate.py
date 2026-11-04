"""What to do after YouTube says "Sign in to confirm you're not a bot".

That answer is about THIS server's address, and every further request while it lasts only deepens the suspicion. So the
first bot check closes a gate:

* background work (backfill, related-video lookups, feed polling through the channel page) stays away for hours:
  6 h, then 12 h, then 24 h if it is refused again after each pause;
* what you click is allowed through as a single "probe" once a minute, so the moment YouTube relents, playback works
  again; between probes it fails at once, without touching YouTube, with a clear message.

Any request that succeeds opens the gate again. No cookies, no account: this only slows us down.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .base import BotCheck

BACKGROUND_PAUSE = (6 * 3600, 12 * 3600, 24 * 3600)
PROBE_EVERY = 60


class BotGate:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.trips = 0
        self.background_until = 0.0
        self.probe_after = 0.0

    @property
    def closed(self) -> bool:
        return self._clock() < max(self.background_until, self.probe_after)

    def retry_after(self) -> int:
        """Seconds until a click may try YouTube again."""
        return max(1, int(self.probe_after - self._clock()) + 1)

    def check(self, background: bool) -> None:
        """Raise BotCheck at once (without contacting YouTube) while the gate says to keep away."""
        now = self._clock()
        if background and now < self.background_until:
            raise BotCheck(f"background fetching is paused for {int((self.background_until - now) / 60) + 1} more minutes "
                           f"after a bot check")
        if not background and now < self.probe_after:
            raise BotCheck(f"waiting {self.retry_after()} s before asking YouTube again after a bot check")

    def trip(self, background: bool) -> None:
        now = self._clock()
        # Only a REFUSAL OF BACKGROUND WORK (or the first refusal of anything) lengthens the pause: a click probing
        # once a minute and being refused again does not mean the background should wait longer.
        if background or self.background_until <= now:
            self.trips += 1
            self.background_until = now + BACKGROUND_PAUSE[min(self.trips, len(BACKGROUND_PAUSE)) - 1]
        self.probe_after = now + PROBE_EVERY

    def ok(self) -> None:
        self.trips, self.background_until, self.probe_after = 0, 0.0, 0.0
