"""A watchdog for the server's main thread.

Everything newtube answers (a page, a video segment) goes through one event loop. If something slow runs ON that loop,
every other request waits for it: to you that looks like a video that will not start ("connection problem") although
nothing is wrong with the network. This watches for exactly that. A tiny task on the loop notes the time every 0.2 s; a
separate thread notices when the note goes stale, and logs what the loop was in the middle of. So when it happens, the log
(``journalctl --user -u newtube | grep "event loop"``) names the culprit instead of leaving a guess.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger("newtube.loop")
BEAT = 0.2             # how often the loop notes the time
STALL = 1.0            # a loop that has not answered for this long is blocked


class LoopWatch:
    def __init__(self, stall: float = STALL, beat: float = BEAT) -> None:
        self.stall, self.beat = stall, beat
        self.last = time.monotonic()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._main_id: int | None = None
        self.reports: list[str] = []            # (kept for tests)

    async def run(self) -> None:
        """Start on the event loop; runs until cancelled."""
        self._main_id = threading.get_ident()
        self.last = time.monotonic()
        self._thread = threading.Thread(target=self._watch, name="loopwatch", daemon=True)
        self._thread.start()
        try:
            while True:
                self.last = time.monotonic()
                await asyncio.sleep(self.beat)
        finally:
            self._stop.set()

    def _watch(self) -> None:
        reported_for = 0.0
        while not self._stop.wait(self.beat):
            behind = time.monotonic() - self.last
            if behind < self.stall:
                reported_for = 0.0
                continue
            if self.last == reported_for:        # one report per blocking episode
                continue
            reported_for = self.last
            frame = sys._current_frames().get(self._main_id)
            where = "".join(traceback.format_stack(frame)[-6:]) if frame is not None else "(unknown)"
            msg = f"event loop blocked for {behind:.1f}s so far; it is in the middle of:\n{where}"
            self.reports.append(msg)
            log.warning(msg)
