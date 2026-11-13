"""What MultiProxy does when the tray app tells it to quit (4.2.1), in an order that frees its port at once.

Before: the web server kept port 8000 until every local model, then the voice engine, then the decision engine had been stopped one after another (each waits for its
program to really exit, and a program holding graphics memory can take many seconds to go), so the port stayed busy for the sum of them: 30 to 60 seconds.
Now: (1) the ports are let go first (the web server stops listening, the interceptor closes) and anything still arriving on an open connection gets a short "closing"
answer; (2) every engine is stopped at the same time, each with its own limit; (3) the program ends. Each step is timed so the log can say what was slow.

Plain functions with the work passed in, so the tests drive it with stand-ins and no real engine or port.
"""
import asyncio
import time
from typing import Awaitable, Callable, Optional

RELEASE_LIMIT_SECONDS = 3         # letting go of a port is instant; this only stops one stuck step from holding the rest
ENGINE_LIMIT_SECONDS = 30         # one engine's own limit (its stop already waits up to 15 s for the program to exit); the others do not wait for it
CLOSING_REPLY = {"detail": "MultiProxy is closing."}


async def _timed(what: str, work: Callable[[], Awaitable], limit: float, steps: list, log: Callable[[str], None]) -> None:
    t = time.monotonic()
    try:
        await asyncio.wait_for(work(), timeout=limit)
    except asyncio.TimeoutError:
        log(f"Closing: {what} had not finished after {limit:.0f}s; moving on")
    except Exception as e:
        log(f"Closing: {what} stopped with a problem ({type(e).__name__}); moving on")
    steps.append((what, time.monotonic() - t))


async def run(release: list, engines: list, *, log: Callable[[str], None], release_limit: Optional[float] = None, engine_limit: Optional[float] = None) -> list:
    """`release` and `engines` are lists of (name, async function). Returns [(name, seconds)] for every step, the port steps first."""
    steps: list = []
    rl = RELEASE_LIMIT_SECONDS if release_limit is None else release_limit
    el = ENGINE_LIMIT_SECONDS if engine_limit is None else engine_limit
    await asyncio.gather(*(_timed(n, f, rl, steps, log) for n, f in release))
    await asyncio.gather(*(_timed(n, f, el, steps, log) for n, f in engines))
    return steps


class ClosingGuard:
    """A plain ASGI middleware: once the program is closing, a request that still arrives on an open connection gets a short 503 and the connection is closed."""

    def __init__(self, app, is_closing: Callable[[], bool]):
        self.app = app
        self.is_closing = is_closing

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and self.is_closing():
            body = b'{"detail":"MultiProxy is closing."}'
            await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                                                                  (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
