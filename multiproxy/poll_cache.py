"""Small caches that keep the dashboard's polls and every chat request off the disk and off slow programs (2026-10-02).

Measured (perf check, part_requests.md): `config.json` was opened and parsed on the event loop 3+ times per chat request (27-63 ms stalls
in a 100-request burst), the Routing tab's 2 s poll started `codex login status` on the loop every time (30-135 ms freezes), and a PATH scan
(`shutil.which`) ran on the loop on every poll. Nothing here changes an answer: each cache is either checked against the file's own
modified time, or short-lived and cleared by the Refresh buttons.

  * `ConfigReader`  : the parsed config.json, re-read whenever the file changes (and always for 2 s after any change, and every 30 s
                      regardless), so a saved change is seen by the very next request.
  * `cached_which`  : `shutil.which` with a short memory; a stale answer is returned at once while a thread refreshes it.
  * `shared`        : one async computation per key, remembered for a number of seconds, shared by everyone asking at the same time.
"""
import asyncio
import json
import os
import shutil
import threading
import time
from typing import Any, Awaitable, Callable, Optional

# A file changed less than this long ago is always re-read (the modified time of a very recent write can be too coarse to tell two
# same-sized writes apart); every file is re-read at least this often even when nothing seems to have changed.
RECENT_SECONDS = 2.0
MAX_AGE_SECONDS = 30.0


class ConfigReader:
    """Reads one JSON file, keeping the text between reads while the file hasn't changed.

    The text is kept (not the parsed object), and parsed again on every read, so every caller gets its own fresh dict to change:
    the callers do `cfg = load(); cfg["x"] = ...; save(cfg)`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._path: Optional[str] = None
        self._sig: Optional[tuple] = None
        self._text: Optional[str] = None
        self._read_at = 0.0
        self.disk_reads = 0                       # (for the tests)

    def forget(self) -> None:
        with self._lock:
            self._path = self._sig = self._text = None

    def text(self, path: str) -> str:
        """The file's text. Raises FileNotFoundError / OSError exactly as `open` would."""
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
        now = time.time()
        with self._lock:
            if (self._path == path and self._sig == sig and self._text is not None
                    and now - st.st_mtime > RECENT_SECONDS and time.monotonic() - self._read_at < MAX_AGE_SECONDS):
                return self._text
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        with self._lock:
            self.disk_reads += 1
            self._path, self._sig, self._text, self._read_at = path, sig, text, time.monotonic()
        return text

    def load(self, path: str) -> dict:
        """Parsed config, or {} when the file is missing or isn't valid JSON (what `_load_config` always did)."""
        try:
            return json.loads(self.text(path))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


# ---------------------------------------------------------------------------------------------
# shutil.which with a memory
# ---------------------------------------------------------------------------------------------
_which: dict = {}
_which_lock = threading.Lock()
WHICH_TTL = 20.0

# A program installed after MultiProxy started (a Codex or Claude the player just set up) puts its folder on Windows' SAVED path; a running
# process never sees that. So when the normal lookup finds nothing, the saved path is read from the registry (read only, never written)
# and searched too. The reader is a module-level name so a test can swap in a stand-in.
_SAVED_PATH_KEYS = (
    ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ("HKEY_CURRENT_USER", "Environment"),
)


def _read_registry_path(hive_name: str, sub: str) -> Optional[str]:
    """The raw `Path` value of one registry key (read only), or None. Windows only."""
    try:
        import winreg
        with winreg.OpenKey(getattr(winreg, hive_name), sub, 0, winreg.KEY_READ) as k:
            return str(winreg.QueryValueEx(k, "Path")[0] or "")
    except (ImportError, OSError):
        return None


def read_saved_path() -> str:
    """Windows' saved PATH: the system's entries, then the user's (the order Windows builds it in), with %VARIABLES% expanded. "" off Windows or when unreadable."""
    if os.name != "nt":
        return ""
    parts = [os.path.expandvars(v) for v in (_read_registry_path(h, sub) for h, sub in _SAVED_PATH_KEYS) if v]
    return os.pathsep.join(parts)


_saved_path_reader: Callable[[], str] = read_saved_path


def _find(name: str) -> Optional[str]:
    """`shutil.which(name)`; when that finds nothing, once more in the saved PATH. Runs on a lookup thread, never the event loop."""
    found = shutil.which(name)
    if found:
        return found
    try:
        saved = _saved_path_reader()
        return shutil.which(name, path=saved) if saved else None
    except Exception:
        return None


def _refresh_which(name: str) -> None:
    value = _find(name)
    with _which_lock:
        _which[name] = (time.monotonic(), value)
        _which_busy.discard(name)


_which_busy: set = set()


def cached_which(name: str, ttl: float = WHICH_TTL) -> Optional[str]:
    """`shutil.which(name)`, remembered for `ttl` seconds. The first call looks it up right away; after that a stale answer is
    returned at once and a thread looks again (a PATH scan on the event loop was a 16-34 ms stall on some devices)."""
    now = time.monotonic()
    with _which_lock:
        hit = _which.get(name)
        if hit is None:
            first = True
        else:
            first = False
            if now - hit[0] < ttl or name in _which_busy:
                return hit[1]
            _which_busy.add(name)
    if first:
        value = _find(name)
        with _which_lock:
            _which[name] = (time.monotonic(), value)
        return value
    threading.Thread(target=_refresh_which, args=(name,), daemon=True, name=f"which-{name}").start()
    return hit[1]


def forget_which(name: Optional[str] = None) -> None:
    with _which_lock:
        if name is None:
            _which.clear()
        else:
            _which.pop(name, None)


# ---------------------------------------------------------------------------------------------
# One shared async answer per key
# ---------------------------------------------------------------------------------------------
_shared: dict = {}          # key -> (at, value)
_inflight: dict = {}        # key -> Future
_refreshing: set = set()    # keys with a background refresh started


def forget(key: Optional[str] = None) -> None:
    if key is None:
        _shared.clear()
    else:
        _shared.pop(key, None)


async def shared(key: str, ttl: Callable[[Any], float], factory: Callable[[], Awaitable[Any]], *, stale_ok: bool = False, max_stale: Optional[float] = None) -> Any:
    """`await factory()` at most once per `ttl(value)` seconds for this key; callers that arrive while it runs wait for the same answer.
    `ttl` gets the value so a good answer can be kept longer than a bad one. With `stale_ok`, an answer that has run out is still handed
    out at once while one background task makes the next (only the very first ask ever waits). With `max_stale` too, an answer older than
    that many seconds is not handed out: a page opened again after a while gets a fresh picture, not the one from when it was last open."""
    hit = _shared.get(key)
    if hit is not None and time.monotonic() - hit[0] < ttl(hit[1]):
        return hit[1]
    if hit is not None and stale_ok and (max_stale is None or time.monotonic() - hit[0] < max_stale):
        if key not in _inflight and key not in _refreshing:
            _refreshing.add(key)
            task = asyncio.ensure_future(_compute_shared(key, factory))

            def done(t, key=key):
                _refreshing.discard(key)
                t.cancelled() or t.exception()            # (a failed refresh keeps the old answer; mark it read)
            task.add_done_callback(done)
        return hit[1]
    return await _compute_shared(key, factory)


async def _compute_shared(key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
    fut = _inflight.get(key)
    if fut is not None:
        return await asyncio.shield(fut)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _inflight[key] = fut
    try:
        value = await factory()
    except BaseException as e:
        _inflight.pop(key, None)
        if isinstance(e, asyncio.CancelledError):
            fut.cancel()
        else:
            fut.set_exception(e)
            fut.exception()                    # (nobody else may be waiting: mark it read, so no "never retrieved" noise)
        raise
    _shared[key] = (time.monotonic(), value)
    _inflight.pop(key, None)
    fut.set_result(value)
    return value


# ---------------------------------------------------------------------------------------------
# A slow read that a poll must never wait for
# ---------------------------------------------------------------------------------------------
class SlowValue:
    """A value that takes a while to read (it starts a program), wanted by a page that polls every 2 s.

    The first `ensure()` waits for a worker thread to read it. After that the last value is handed out at once, and once it is older than
    `ttl` seconds a worker thread reads it again in the background: no poll ever waits for the program (perf check 2026-10-02: a poll
    waited 0.2-1 s for the graphics card read every 30 s)."""

    def __init__(self, compute: Callable[[], Any], ttl: float):
        self._compute, self._ttl = compute, ttl
        self._lock = threading.Lock()
        self._value: Any = None
        self._at: Optional[float] = None
        self._busy = False

    def _refresh(self) -> None:
        try:
            v = self._compute()
            with self._lock:
                self._value, self._at = v, time.monotonic()
        finally:
            with self._lock:
                self._busy = False

    def get(self) -> Any:
        """The last value (read now, on this thread, only if it was never read)."""
        if self._at is None:
            self._refresh()
        return self._value

    async def ensure(self) -> None:
        if self._at is None:
            await asyncio.to_thread(self._refresh)
            return
        with self._lock:
            if time.monotonic() - self._at < self._ttl or self._busy:
                return
            self._busy = True
        threading.Thread(target=self._refresh_quietly, daemon=True, name="slow-value").start()

    def _refresh_quietly(self) -> None:
        try:
            self._refresh()
        except Exception:
            pass                                  # (a failed read keeps showing the last good value; the next poll after `ttl` tries again)
