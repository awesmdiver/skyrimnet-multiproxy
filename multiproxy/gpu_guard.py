"""The graphics memory guard (2026-10-26): stops the built-in chat model before Skyrim runs out of graphics memory and crashes.

The old memory check only runs when a model STARTS. The game's own use then grows (a heavy area, a new cell, weather) and nothing was watching. This watches, while Skyrim is running:

  * It sleeps until the Skyrim mod says "started" (local_channel) and goes back to sleep when the game is closed (the "closed" notice, or the game's process gone, which is
    what a crash looks like: no notice ever comes). While the game is closed it reads nothing at all, and neither does it while no built-in model is running.
  * Every READ_SECONDS it reads the free memory of the card each running built-in chat model uses (gpu_monitor.GpuMonitor.card_memory: the driver's own library in this
    process on Windows, so no program is started and no console window can flash up).
  * When a card has had less free memory than the reserve (the larger of RESERVE_PERCENT of the card and RESERVE_FLOOR_MB) for TRIP_SECONDS in a row, it stops the built-in
    chat models on that card with the very call the Stop button makes (LocalModels.stop). Nothing else is MultiProxy's to stop here: not the quick-decisions model, not the
    voice, not Ollama or LM Studio, not anything on a Courier.
  * It never starts the model again by itself. LocalModels.guard_stops remembers the stop so the Overview and the Local models tab can say so and offer Start; the note ends when
    the player presses Start or when the game has closed.

Every number below is an estimate, not a measurement; change them here. Nothing in this module imports MultiProxy.
"""
import asyncio
import datetime
import json
import os
import time
from typing import Callable, Optional

READ_SECONDS = 2.0               # how often the free memory is read while the game is running
TRIP_SECONDS = 5.0               # free memory must stay below the reserve this long, in a row: one dip while the game loads a cell must not trip it
RESERVE_PERCENT = 5.0            # estimate: a card with less than this share of its memory free is nearly full ...
RESERVE_FLOOR_MB = 1536          # ... and so is any card with less than this free (1.5 GB: on a 24 GB or 16 GB card 5 percent is less than this)
PROCESS_CHECK_EVERY = 5          # reads between "is the game's program still there?" (that is how a crash with no "closed" notice is noticed)
EVENTS_FILE = "graphics-guard-events.jsonl"      # one line per stop, kept for good: never pruned

# Every word the player sees from this feature. Kept here so the design side's wording pass has one place to read and change.
WORDS = {
    "notice": "Stopped {model}: Skyrim's graphics memory was nearly full.",
    "switchTitle": "Protect Skyrim's graphics memory",
    "switchHint": "On: Stops the local chat model if Skyrim runs low on graphics memory.<br>Off: Never stops the model automatically.",
}


def notice(model_name: str) -> str:
    return WORDS["notice"].format(model=model_name)


def threshold_mb(total_mb: float) -> float:
    """Free memory below this is "nearly full" on a card of `total_mb`."""
    return max(total_mb * RESERVE_PERCENT / 100.0, float(RESERVE_FLOOR_MB))


def append_event(path: str, event: dict) -> None:
    """Add one line to the events file. Blocking (run it in a thread). Lines are only ever added."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


class GraphicsGuard:
    """`local` is LocalModels (servers, stop, guard_stops, installed); `monitor` is GpuMonitor (card_memory, skyrim_alive, sample, last_skyrim_mb).
    `game_running()` says whether the mod's "started" notice is in force; `enabled()` is the Settings switch; `cards_fn()` lists the cards (only asked when a model has a card chosen).
    `clock` / `wall` / `sleep` are for tests."""

    def __init__(self, local, monitor, *, game_running: Callable[[], bool], enabled: Callable[[], bool], events_path: str, log,
                 cards_fn: Optional[Callable[[], list]] = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime.datetime] = datetime.datetime.now, sleep=asyncio.sleep):
        self.local, self.monitor, self.log = local, monitor, log
        self._game_running, self._enabled, self.events_path = game_running, enabled, events_path
        self._cards_fn = cards_fn or (lambda: [])
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self._low_since: dict = {}                    # card index -> when its free memory first went below the reserve (this run of low readings)
        self._wake = asyncio.Event()
        self._gone = False                            # the game's program disappeared with no "closed" notice (a crash)
        self._assume = False                          # MultiProxy started after the game: the program was there at the start
        self._ticks = 0
        self._task: Optional[asyncio.Task] = None
        self.reads = 0                                # how many times memory was read (tests: none while the game is closed or the switch is off)

    # ---- the game ------------------------------------------------------------------------------------------
    def notify_started(self) -> None:
        """The mod said "started"."""
        self._gone = False
        self._wake.set()

    def game_up(self) -> bool:
        return (bool(self._game_running()) or self._assume) and not self._gone

    def _game_closed(self) -> None:
        self._assume = False
        self._low_since.clear()
        self.local.guard_stops.clear()                # the note is only for while the game runs: afterwards the model is just stopped

    # ---- one look ------------------------------------------------------------------------------------------
    def _running_models(self) -> list:
        return [mid for mid, s in self.local.servers.items() if s.state in ("starting", "running") and s.proc is not None and s.proc.returncode is None]

    async def _card_of(self, model_id: str, readings: list) -> Optional[int]:
        """Which reading is the card this model's engine uses: the one the player chose (counted among the NVIDIA cards, in the driver's own order), else the biggest card."""
        choice = self.local._choice_for(model_id)
        if choice:
            try:
                cards = await asyncio.to_thread(self._cards_fn)
            except Exception:
                cards = []
            nvidia = [c for c in cards if getattr(c, "vendor", "") == "nvidia"]
            idx = next((i for i, c in enumerate(nvidia) if c.id == choice["id"]), None)
            if idx is not None and idx < len(readings) and readings[idx]:
                return idx
        best = [(r[1], i) for i, r in enumerate(readings) if r]
        return max(best)[1] if best else None

    async def tick(self) -> list:
        """One read and its decision -> the ids of the models stopped by it."""
        running = self._running_models()
        if not running:
            self._low_since.clear()
            return []
        self.reads += 1
        readings = await asyncio.to_thread(self.monitor.card_memory)
        if not readings or not any(readings):
            self._low_since.clear()                    # no reading is no news: a gap must not add up to "5 seconds in a row"
            return []
        by_card: dict = {}
        for mid in running:
            idx = await self._card_of(mid, readings)
            if idx is not None:
                by_card.setdefault(idx, []).append(mid)
        now = self.clock()
        for idx in [i for i in self._low_since if i not in by_card]:
            del self._low_since[idx]
        stopped = []
        for idx, mids in by_card.items():
            used, total = readings[idx]
            free = total - used
            if free >= threshold_mb(total):
                self._low_since.pop(idx, None)
                continue
            since = self._low_since.setdefault(idx, now)
            if now - since >= TRIP_SECONDS:
                self._low_since.pop(idx, None)
                stopped += await self._trip(mids, free, total)
        return stopped

    async def _trip(self, model_ids: list, free_mb: float, total_mb: float) -> list:
        names = {m.id: m.name for m in self.local.installed()}
        done = []
        for mid in model_ids:
            name = names.get(mid, mid)
            try:
                await self.local.stop(mid)
            except Exception:
                self.log.exception(f"Graphics memory guard could not stop {name}")
                continue
            self.local.guard_stops[mid] = {"at": self.wall().isoformat(timespec="seconds"), "name": name}
            done.append(mid)
            skyrim_mb = await self._skyrim_use()
            event = {"time": self.wall().isoformat(timespec="seconds"), "model": name, "modelId": mid, "freeMb": int(free_mb), "totalMb": int(total_mb),
                     "reserveMb": int(threshold_mb(total_mb)), "skyrimMb": skyrim_mb}
            self.log.warning(f"Graphics memory guard stopped {name}: {int(free_mb)} MB remaining out of {int(total_mb)} MB total (Skyrim last measured at {skyrim_mb if skyrim_mb is not None else 'unknown'} MB)")
            try:
                await asyncio.to_thread(append_event, self.events_path, event)
            except OSError:
                self.log.exception("Graphics memory guard could not save its event")
        return done

    async def _skyrim_use(self) -> Optional[int]:
        """The game's graphics memory, from a fresh reading when there is one, else the last one seen."""
        try:
            s = await asyncio.wait_for(self.monitor.sample(force=True), timeout=5)
            if s and s.skyrim_mb:
                return int(s.skyrim_mb)
        except Exception:
            pass
        return getattr(self.monitor, "last_skyrim_mb", None)

    # ---- the loop ------------------------------------------------------------------------------------------
    async def step(self) -> None:
        """One turn of the loop (a test drives this directly)."""
        if not self.game_up():
            self._game_closed()
            return await self._wait_for_game()
        self._ticks += 1
        if self._ticks % PROCESS_CHECK_EVERY == 0:
            alive = await asyncio.to_thread(self.monitor.skyrim_alive)
            if alive is False:
                self._gone = True
                return
        if self._enabled():
            await self.tick()
        await self.sleep(READ_SECONDS)

    async def _wait_for_game(self) -> None:
        await self._wake.wait()
        self._wake.clear()

    async def run(self) -> None:
        try:
            self._assume = (await asyncio.to_thread(self.monitor.skyrim_alive)) is True      # MultiProxy started after the game: one look, then the mod's notices decide
        except Exception:
            self._assume = False
        while True:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("Graphics memory guard failed")
                await self.sleep(READ_SECONDS)

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
