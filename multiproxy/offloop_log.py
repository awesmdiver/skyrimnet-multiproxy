"""Log file writing off the request path (2026-10-02).

Measured (perf check): writing the log file on the event loop stalled it 30-50 ms now and then in a 100-request burst (3 log lines per
request). `OffLoopFileHandler` is a `QueueHandler` whose records are written by a `QueueListener` thread into the same kind of file
handler as before: the same file, the same line format (the formatter runs in that thread too, so masking of keys still happens before
anything reaches the file) and the same rotation. Stopping drains the queue, so no line is lost at a normal shutdown; `drain_all()` does
the same before a hard `os._exit`.

Used from multiproxy.py's uvicorn log config (`"()": offloop_log.OffLoopFileHandler`) and by the "write the log now" switch.
"""
import atexit
import logging
import logging.handlers
import queue
import threading
import weakref
from typing import Optional

_live: "weakref.WeakSet" = weakref.WeakSet()


class OffLoopFileHandler(logging.handlers.QueueHandler):
    def __init__(self, filename: str, mode: str = "a", encoding: str = "utf-8", maxBytes: int = 0, backupCount: int = 0):
        super().__init__(queue.SimpleQueue())
        if maxBytes:
            inner: logging.Handler = logging.handlers.RotatingFileHandler(filename, mode=mode, maxBytes=maxBytes, backupCount=backupCount, encoding=encoding)
        else:
            inner = logging.FileHandler(filename, mode=mode, encoding=encoding)
        self.inner = inner
        self.baseFilename = inner.baseFilename               # (the "write the log now" switch finds its own handler by this)
        self.listener = logging.handlers.QueueListener(self.queue, inner, respect_handler_level=False)
        self._closed = False
        self._stop_lock = threading.Lock()
        self.listener.start()
        _live.add(self)
        atexit.register(self.close)

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # QueueHandler's own prepare() formats the line here, on the caller's thread: leave the record alone and let the file thread format it.
        return record

    def setFormatter(self, fmt: Optional[logging.Formatter]) -> None:
        self.inner.setFormatter(fmt)
        super().setFormatter(fmt)

    def flush(self) -> None:
        self.drain()                                          # (a caller that flushes wants the lines in the file now)

    def drain(self) -> None:
        """Wait until every line queued so far is in the file."""
        with self._stop_lock:
            if self._closed:
                return
            self.listener.stop()                              # (writes everything still queued, then ends the thread)
            self.inner.flush()
            self.listener.start()

    def close(self) -> None:
        with self._stop_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.listener.stop()
            except Exception:
                pass
            try:
                self.inner.close()
            except Exception:
                pass
        super().close()


def drain_all() -> None:
    """Everything logged so far is in the files (call before `os._exit`, which skips the normal shutdown)."""
    for h in list(_live):
        try:
            h.drain()
        except Exception:
            pass
