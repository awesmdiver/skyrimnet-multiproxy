"""The Speeds history file, kept off the request path (2026-10-02): one background writer, cheap readers, and a short shared answer cache.

Why this exists (measured, see docs/plans): the old code opened a new connection per request, inserted, committed (a disk sync) and made the chat reply wait for
all of that, and every Speeds poll read the whole table on the event loop. Now:

* WRITING: `enqueue_row` puts the row on a queue and returns at once. ONE long-lived thread owns the write connection (WAL journal, `synchronous=NORMAL`), takes
  whatever is queued, inserts it in one batch and commits once. A failed write is logged and never reaches a request. `flush()` / `wait_idle()` wait until
  everything queued so far is on disk (tests, shutdown); `stop()` flushes and closes (called at shutdown and by `atexit`).
* READING: `reader(path)` is a short, separate connection (WAL lets it read while the writer writes, so neither waits for the other).
* UPGRADING: `prepare(conn)` makes the indexes the dashboard and routing queries need (`IF NOT EXISTS`, so an old file is upgraded in place on first start).
* SHARING ANSWERS: `AnswerCache` runs a slow read in a thread once, lets a second poll that arrives meanwhile share that run (single flight), and hands a
  recent answer back instead of re-reading. An answer is reused while no write has been committed since (the generation counter the writer bumps) or while it
  is only a few seconds old, so two open tabs cost one read.

Nothing from MultiProxy is imported here (tests use it on its own).
"""
import asyncio
import hashlib
import json
import logging
import queue
import sqlite3
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("proxy")

QUEUE_MAX = 50000                     # a dead disk must never grow memory without end: past this, new rows are dropped (and logged)
BATCH_MAX = 1000                      # rows per commit at most
KEEP_CONNECTIONS = 3                  # write connections kept open (one per history file in use; tests switch files)

# The indexes. `ts` serves every "last hour / today / 7 days / ORDER BY ts" read; the partial one serves the Routing view's "last 600 jobs" read without
# walking rows that have no job. (model, ts) already existed and serves the per-model reads.
INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_speed_history_ts ON speed_history (ts)",
    "CREATE INDEX IF NOT EXISTS idx_speed_history_job_ts ON speed_history (ts) WHERE job IS NOT NULL",
    # One small row per decision the local model answered first (2026-10-05): its confidence, whether a stronger AI was asked, and who gave the answer.
    "CREATE TABLE IF NOT EXISTS decision_log (ts REAL NOT NULL, confidence REAL, asked INTEGER NOT NULL, answered_by TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_decision_log_ts ON decision_log (ts)",
    # One small row per spoken line (2026-10-05, for the Overview's Voice row): how many seconds it took from SkyrimNet asking to the finished line, and whether it worked.
    "CREATE TABLE IF NOT EXISTS voice_log (ts REAL NOT NULL, seconds REAL NOT NULL, ok INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_voice_log_ts ON voice_log (ts)",
)


def prepare(conn: sqlite3.Connection) -> None:
    """Make an (old or new) history file ready: the WAL journal (persists in the file) and every index. Safe to run on every start."""
    conn.commit()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error as e:                      # another program has it open right now: the old journal keeps working
        log.warning(f"Speeds history: couldn't switch the file to WAL mode now ({e})")
    for sql in INDEX_SQL:
        conn.execute(sql)
    have = {r[1] for r in conn.execute("PRAGMA table_info(voice_log)")}
    for col, kind in (("waited", "REAL"), ("spoke", "REAL"), ("audio", "REAL"), ("chars", "INTEGER")):     # (2026-10-23) per spoken line: seconds in the voice queue, seconds the engine spent on it,
        if col not in have:                                                                                  # seconds of sound it made, characters in; an older file gets them, its old rows stay NULL
            try:
                conn.execute(f"ALTER TABLE voice_log ADD COLUMN {col} {kind}")
            except sqlite3.Error as e:
                log.warning(f"Speeds history: couldn't add voice_log.{col} ({e})")
    conn.commit()


def reader(path: str) -> sqlite3.Connection:
    """A short-lived connection for reading. Close it when the read is done (`with` does not close a sqlite connection: use contextlib.closing)."""
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=1")
    except sqlite3.Error:
        pass
    return conn


# ---------------------------------------------------------------- the writer

class _Writer:
    def __init__(self) -> None:
        self.q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.conns: dict = {}                       # path -> connection (insertion order = oldest first)
        self.generation = 0
        self.written = 0
        self.failed = 0
        self.dropped = 0
        self.sync = False                           # tests: every enqueue waits until it is written, and the connection is closed again
        self.commits = 0

    # -- public
    def enqueue(self, path: str, columns: tuple, values: tuple, table: str = "speed_history") -> bool:
        self._ensure_thread()
        try:
            self.q.put_nowait(("row", path, columns, values, table))
        except queue.Full:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                log.warning(f"Speeds history: the write queue is full ({QUEUE_MAX} rows), {self.dropped} dropped so far (is the disk stuck?)")
            return False
        if self.sync:
            self.flush()
        return True

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until every row queued BEFORE this call is committed. False on timeout. Never raises."""
        t = self.thread
        if t is None or not t.is_alive():
            return self.q.empty()
        done = threading.Event()
        try:
            self.q.put(("flush", done), timeout=timeout)
        except queue.Full:
            return False
        return done.wait(timeout)

    def stop(self, timeout: float = 15.0) -> None:
        """Write what is queued, then close. A later enqueue starts a fresh thread (so tests and reloads are fine)."""
        t = self.thread
        if t is not None and t.is_alive():
            try:
                self.q.put(("stop",), timeout=timeout)
            except queue.Full:
                pass
            t.join(timeout)
        self.thread = None
        self._close_all()

    # -- the thread
    def _ensure_thread(self) -> None:
        t = self.thread
        if t is not None and t.is_alive():
            return
        with self.lock:
            t = self.thread
            if t is not None and t.is_alive():
                return
            self.thread = threading.Thread(target=self._run, name="speed-history-writer", daemon=True)
            self.thread.start()

    def _conn(self, path: str) -> sqlite3.Connection:
        conn = self.conns.get(path)
        if conn is not None:
            self.conns[path] = self.conns.pop(path)         # most recently used goes last
            return conn
        conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        self.conns[path] = conn
        while len(self.conns) > KEEP_CONNECTIONS:
            old_path = next(iter(self.conns))
            self._close(old_path)
        return conn

    def _close(self, path: str) -> None:
        conn = self.conns.pop(path, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _close_all(self) -> None:
        for path in list(self.conns):
            self._close(path)

    def _run(self) -> None:
        stopping = False
        while not stopping:
            item = self.q.get()
            batch = [item]
            while len(batch) < BATCH_MAX:                   # take what else is already waiting: one commit for all of it
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            rows = [b for b in batch if b[0] == "row"]
            if rows:
                self._write(rows)
            for b in batch:
                if b[0] == "stop":
                    stopping = True
            for b in batch:
                if b[0] == "flush":
                    b[1].set()                              # after the commit above: the rows this flush waited for are on disk
            if self.sync:
                self._close_all()
        self._close_all()

    def _write(self, rows: list) -> None:
        by_target: dict = {}
        for _kind, path, columns, values, table in rows:
            by_target.setdefault((path, columns, table), []).append(values)
        committed = 0
        for (path, columns, table), batch in by_target.items():
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
            try:
                conn = self._conn(path)
                conn.executemany(sql, batch)
                conn.commit()
                committed += len(batch)
            except Exception as e:
                # One bad row must not lose its neighbours: retry them one at a time, log each that still fails.
                try:
                    self._conn(path).rollback()
                except Exception:
                    self._close(path)
                for values in batch:
                    try:
                        conn = self._conn(path)
                        conn.execute(sql, values)
                        conn.commit()
                        committed += 1
                    except Exception as e2:
                        self.failed += 1
                        log.warning(f"Speed history write failed (non-fatal, Today's view is unaffected): {e2}")
                        try:
                            self._conn(path).rollback()
                        except Exception:
                            self._close(path)
        self.written += committed
        if committed:
            self.commits += 1
            self.generation += 1


_writer = _Writer()


def enqueue_row(path: str, columns: tuple, values: tuple, table: str = "speed_history") -> bool:
    """Queue one row for the history file at `path`; returns at once. False only when the queue is full (the row was dropped and logged)."""
    return _writer.enqueue(path, tuple(columns), tuple(values), table)


def flush(timeout: float = 30.0) -> bool:
    return _writer.flush(timeout)


wait_idle = flush


def stop(timeout: float = 15.0) -> None:
    _writer.stop(timeout)


def set_sync(on: bool) -> None:
    """Tests only: make every enqueue wait for its own write (and close the connection after), as the old awaited insert did."""
    _writer.sync = bool(on)


def generation() -> int:
    """Bumped every time the writer commits. A cached answer from an older generation may be out of date."""
    return _writer.generation


def stats() -> dict:
    w = _writer
    return {"written": w.written, "failed": w.failed, "dropped": w.dropped, "commits": w.commits, "queued": w.q.qsize()}


# ---------------------------------------------------------------- the shared answer cache

class AnswerCache:
    """Runs `fn(*args)` in a thread for a key, shares the run with every caller that arrives meanwhile, and keeps the answer for reuse.

    An answer is reused when (a) no write has been committed since it was made AND it is younger than `still_s`, or (b) it is younger than the TTL, which is
    `ttl_s` but at least `slow_factor` times as long as the read took (a read that takes 3 s is not repeated more often than every 6 s, so polling can't take
    more than about a third of one core however many tabs are open). Callers must not change a returned value (copy before editing)."""

    def __init__(self, ttl_s: float = 3.0, still_s: float = 20.0, slow_factor: float = 2.0, max_ttl_s: float = 60.0, max_entries: int = 64,
                 ignore_test_mode: bool = False):
        self.ignore_test_mode = ignore_test_mode      # in test mode (set_sync) answers are never reused, so a test that seeds rows between two reads sees them
        self.ttl_s, self.still_s, self.slow_factor, self.max_ttl_s, self.max_entries = ttl_s, still_s, slow_factor, max_ttl_s, max_entries
        self._entries: dict = {}                    # key -> (value, made_at, generation, took_s)
        self._inflight: dict = {}                   # key -> asyncio.Future
        self.runs = 0
        self.hits = 0
        self.shared = 0

    def _fresh(self, entry, ttl_s: Optional[float]) -> bool:
        if _writer.sync and not self.ignore_test_mode:
            return False
        value, made, gen, took = entry
        age = time.monotonic() - made
        ttl = min(max(ttl_s if ttl_s is not None else self.ttl_s, took * self.slow_factor), self.max_ttl_s)
        if age < ttl:
            return True
        return gen == generation() and age < max(self.still_s, ttl)

    async def get(self, key, fn: Callable, *args, ttl_s: Optional[float] = None) -> Any:
        entry = self._entries.get(key)
        if entry is not None and self._fresh(entry, ttl_s):
            self.hits += 1
            return entry[0]
        task = self._inflight.get(key)
        if task is not None:
            self.shared += 1
            return await asyncio.shield(task)
        task = asyncio.ensure_future(self._compute(key, fn, args))
        self._inflight[key] = task
        # shield: a caller that goes away (tab closed mid-read) must not cancel the read the others are waiting on
        return await asyncio.shield(task)

    async def _compute(self, key, fn: Callable, args: tuple) -> Any:
        gen = generation()
        t0 = time.monotonic()
        try:
            value = await asyncio.to_thread(fn, *args)
        finally:
            self._inflight.pop(key, None)
        took = time.monotonic() - t0
        self.runs += 1
        self._entries[key] = (value, time.monotonic(), gen, took)
        if len(self._entries) > self.max_entries:
            for old in sorted(self._entries, key=lambda k: self._entries[k][1])[: len(self._entries) - self.max_entries]:
                self._entries.pop(old, None)
        return value

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------- ETag answers

def etag_of(body: bytes) -> str:
    return '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'


def json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def answer(if_none_match: Optional[str], payload):
    """(status, body bytes, headers) for a polled JSON answer: 304 with no body when the caller already has exactly this body (its ETag matches)."""
    body = json_bytes(payload)
    tag = etag_of(body)
    headers = {"ETag": tag, "Cache-Control": "no-cache"}
    if if_none_match:
        wanted = {t.strip().removeprefix("W/") for t in if_none_match.split(",")}
        if tag in wanted or "*" in wanted:
            return 304, b"", headers
    return 200, body, headers
