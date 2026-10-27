"""The loop-lag probe (2026-10-02): finds anything that blocks MultiProxy's event loop, and says exactly where.

MultiProxy answers every request on one event loop, so one piece of slow synchronous work (a file read, a database query, a big JSON, a regular expression over a long
text) holds up every other request until it is done. This watches for that. A small thread asks the loop for a heartbeat every few milliseconds; when the heartbeat is
late by more than the threshold, the thread reads what the loop's own thread is executing right now (`sys._current_frames`) and logs the line it is stuck on. That is
the stack of the guilty code itself, not a guess.

**Off unless asked for:** set the environment variable `MULTIPROXY_LOOP_LAG_MS` to a number of milliseconds (for example `25`) before MultiProxy starts. The cost when on is a
thread that wakes every `threshold / 4` milliseconds (at least 2) and one loop callback per beat: about nothing. Each stall is logged once, when it ends, as
`The server was busy for 63 ms in: foo.py:123 in func  <-  bar.py:45 in caller`, and kept in `STALLS` (the opt-in log line the item asked for, also what the
performance guard test and the measuring scripts read).
"""
import asyncio
import os
import sys
import threading
import time
import traceback
from typing import Callable, Optional

ENV = "MULTIPROXY_LOOP_LAG_MS"
STALLS: list = []              # (when, milliseconds, where) of every stall seen, newest last (capped)
MAX_KEPT = 500


def threshold_from_env(env: Optional[dict] = None) -> Optional[float]:
    raw = (env if env is not None else os.environ).get(ENV, "").strip()
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v > 0 else None


def _where(frame, depth: int = 4) -> str:
    """The innermost few frames of the loop thread's stack, outermost last, skipping the event loop's own plumbing."""
    stack = traceback.extract_stack(frame)
    own = [f for f in stack if "asyncio" not in f.filename.replace("\\", "/").split("/")[-2:-1] and "site-packages" not in f.filename.replace("\\", "/")]
    pick = (own or stack)[-depth:]
    return "  <-  ".join(f"{os.path.basename(f.filename)}:{f.lineno} in {f.name}" for f in reversed(pick))


class LoopLagProbe:
    def __init__(self, threshold_ms: float, log: Callable[[str], None], clock: Callable[[], float] = time.perf_counter):
        self.threshold = threshold_ms / 1000.0
        self.log, self.clock = log, clock
        self._beat = clock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop_thread_id: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._caught: Optional[str] = None            # where the loop was stuck, taken while it was stuck
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._beat = self.clock()
        self._task = asyncio.ensure_future(self._heartbeat())
        self._thread = threading.Thread(target=self._watch, name="loop-lag-probe", daemon=True)
        self._thread.start()

    async def _heartbeat(self) -> None:
        step = max(0.002, self.threshold / 4)
        late_since = None
        while not self._stop.is_set():
            before = self.clock()
            await asyncio.sleep(step)
            now = self.clock()
            lag = now - before - step
            self._beat = now
            if lag >= self.threshold:
                where = self._caught or "(where it was stuck wasn't seen: it ended too quickly)"
                self._record(lag * 1000, where)
            self._caught = None

    def _watch(self) -> None:
        step = max(0.002, self.threshold / 4)
        while not self._stop.wait(step):
            if self.clock() - self._beat > self.threshold + step and self._caught is None:
                frame = sys._current_frames().get(self._loop_thread_id)
                if frame is not None:
                    self._caught = _where(frame)

    def _record(self, ms: float, where: str) -> None:
        STALLS.append((time.time(), round(ms, 1), where))
        del STALLS[:-MAX_KEPT]
        try:
            self.log(f"The server was busy for {ms:.0f} ms in: {where}")
        except Exception:
            pass

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._thread is not None:
            self._thread.join(timeout=1)


def start_from_env(log: Callable[[str], None], env: Optional[dict] = None) -> Optional[LoopLagProbe]:
    """Called from MultiProxy's startup (inside the running loop): a started probe when `MULTIPROXY_LOOP_LAG_MS` is set, else None and nothing runs."""
    ms = threshold_from_env(env)
    if ms is None:
        return None
    probe = LoopLagProbe(ms, log)
    probe.start()
    log(f"Watching for anything that blocks the server for more than {ms:g} ms ({ENV} is set).")
    return probe
