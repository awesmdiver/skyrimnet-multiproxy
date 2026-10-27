"""The time left on a download, from the speed really measured (2026-10-26). One place for the maths and for the words, so every bar says the same thing.

`Meter.note(n)` is called with each piece that arrived. The speed is what arrived over the last 30 seconds (it follows a slow patch). Nothing is said until about 10 seconds of
data exist, and when nothing arrived for 15 seconds the text is "waiting for network". A piece bigger than a normal chunk is a credit (a resumed file's bytes already on disk,
a file that was already complete, a copy from another device), not network speed, so it never counts toward the speed.
"""
import time
from collections import deque
from typing import Callable, Optional

WINDOW_SECONDS = 30.0
MIN_SECONDS = 10.0
STALL_SECONDS = 15.0
MAX_CHUNK = 2 * 1024 * 1024        # downloads read 1 MiB at a time; anything bigger is a credit, not speed

WORDS = {
    "waiting": "waiting for network",
    "under_a_minute": "less than a minute left",
    "minutes": "about {m} min left",
    "hours": "about {h} h {m} min left",
    "hours_even": "about {h} h left",
}


def left_text(seconds: float) -> str:
    """A calm, rounded figure: no seconds, no fake precision."""
    if seconds < 60:
        return WORDS["under_a_minute"]
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        if minutes >= 10:
            minutes = int(round(minutes / 5.0)) * 5 or 5      # past ten minutes, whole fives
        return WORDS["minutes"].format(m=minutes)
    hours, minutes = divmod(int(round(seconds / 300.0)) * 5, 60)       # whole fives of minutes
    if minutes == 0:
        return WORDS["hours_even"].format(h=hours)
    return WORDS["hours"].format(h=hours, m=minutes)


class Meter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.started = clock()
        self.last_data = self.started
        self.samples: deque = deque([(self.started, 0)])        # (time, bytes fetched so far)
        self.fetched = 0

    def note(self, n: int) -> None:
        if n <= 0 or n > MAX_CHUNK:
            return
        now = self.clock()
        self.fetched += n
        self.last_data = now
        self.samples.append((now, self.fetched))
        while len(self.samples) > 2 and now - self.samples[1][0] >= WINDOW_SECONDS:
            self.samples.popleft()

    def observe(self, done: int) -> None:
        """For a download that reports its running total instead of each piece: the first total seen is the starting point (bytes already on disk are not speed)."""
        last = getattr(self, "_last_seen", None)
        self._last_seen = done
        if last is None or done <= last:
            return
        now = self.clock()
        self.fetched += done - last
        self.last_data = now
        self.samples.append((now, self.fetched))
        while len(self.samples) > 2 and now - self.samples[1][0] >= WINDOW_SECONDS:
            self.samples.popleft()

    def speed(self) -> float:
        """Bytes per second over the last 30 seconds (0 when nothing arrived)."""
        now = self.clock()
        if not self.samples or now - self.last_data >= STALL_SECONDS:
            return 0.0
        t0, b0 = self.samples[0]
        if now - t0 > WINDOW_SECONDS:
            t0, b0 = now - WINDOW_SECONDS, self._at(now - WINDOW_SECONDS)
        span = now - t0
        return (self.fetched - b0) / span if span > 0 else 0.0

    def _at(self, when: float) -> int:
        before = 0
        for t, b in self.samples:
            if t > when:
                break
            before = b
        return before

    def text(self, done: int, total: int) -> str:
        """"" until there is something honest to say; else "waiting for network" or the time left."""
        now = self.clock()
        if total <= 0 or done >= total or now - self.started < MIN_SECONDS:
            return ""
        if now - self.last_data >= STALL_SECONDS:
            return WORDS["waiting"]
        speed = self.speed()
        if speed <= 0:
            return ""
        return left_text((total - done) / speed)
