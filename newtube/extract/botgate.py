"""What to do after YouTube says "Sign in to confirm you're not a bot".

That answer is about THIS server's address, and every further request while it lasts only deepens the suspicion. So the
first bot check closes a gate:

* background work (backfill, related-video lookups, feed polling through the channel page) stays away for hours:
  6 h, then 12 h, then 24 h if it is refused again after each pause;
* what you click is allowed through as a single "probe" once a minute, so the moment YouTube relents, playback works
  again; between probes it fails at once, without touching YouTube, with a clear message.

Any request that succeeds opens the gate again. No cookies, no account: this only slows us down.

The state is kept in the database, so a restart (or a crash loop, or the self-test command) does not forget it: otherwise
every restart would be a fresh chance to ask again while YouTube is still refusing.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable

from .base import BotCheck

BACKGROUND_PAUSE = (6 * 3600, 12 * 3600, 24 * 3600)
PROBE_EVERY = 60


class BotGate:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock          # wall-clock time: it must mean the same thing after a restart
        self.trips = 0
        self.background_until = 0.0
        self.probe_after = 0.0
        self._conn: sqlite3.Connection | None = None

    # ---- remembering across restarts ---------------------------------------------------------------
    def persist_to(self, conn: sqlite3.Connection) -> None:
        """Load what an earlier run recorded, and keep the database up to date from now on."""
        self._conn = conn
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='bot_gate'").fetchone()
            if row:
                s = json.loads(row[0])
                self.trips, self.background_until, self.probe_after = int(s["trips"]), float(s["background_until"]), float(s["probe_after"])
        except (sqlite3.Error, ValueError, KeyError, TypeError):
            pass                         # a missing or damaged record means "no known trouble"

    def _save(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("INSERT INTO settings(key, value) VALUES ('bot_gate', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (json.dumps({"trips": self.trips, "background_until": self.background_until, "probe_after": self.probe_after}),))
        except sqlite3.Error:
            pass                         # never let bookkeeping break a request

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
        self._save()

    def ok(self) -> None:
        if (self.trips, self.background_until, self.probe_after) == (0, 0.0, 0.0):
            return                       # nothing to clear (the usual case): no database write per request
        self.trips, self.background_until, self.probe_after = 0, 0.0, 0.0
        self._save()
