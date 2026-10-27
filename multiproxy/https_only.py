"""Downloads that follow a redirect only to https (security review S11, 2026-10-06).

A download address MultiProxy builds itself (a model on Hugging Face, an engine on GitHub, Claude on Anthropic's release site) is https, but the site may answer with a redirect
to somewhere else, and a plain http hop would let anyone on the way change the file (the SHA-256 would still catch it, but the file should never travel that way). `get` does
what `session.get(url, allow_redirects=True)` does, except that every redirect target must be https and there are at most a few hops. The first address is the caller's own.
"""
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urljoin, urlsplit

import aiohttp

REDIRECTS = (301, 302, 303, 307, 308)
MAX_HOPS = 5


class RedirectRefused(aiohttp.ClientError):
    """The site sent the download somewhere that isn't https (or sent it round in circles)."""


@asynccontextmanager
async def get(session: aiohttp.ClientSession, url: str, headers: Optional[dict] = None, hops: int = MAX_HOPS, **kw):
    """Like `async with session.get(url, headers=headers)`, following redirects by hand: each target must be https, at most `hops` of them."""
    resp = None
    for _ in range(hops + 1):
        resp = await session.get(url, headers=headers, allow_redirects=False, **kw)
        where = resp.headers.get("Location") if resp.status in REDIRECTS else None
        if not where:
            break
        target = urljoin(str(resp.url), where)
        resp.release()
        if urlsplit(target).scheme.lower() != "https":
            raise RedirectRefused("The download failed because the link isn't secure (it needs https).")
        url, resp = target, None
    if resp is None:
        raise RedirectRefused("The download failed because the link redirects in circles.")
    try:
        yield resp
    finally:
        resp.release()


# GitHub answers a release download with a Content-Security-Policy header longer than aiohttp's default limit (8,190 bytes): aiohttp then fails with
# "400, Got more than 8190 bytes when reading" before it reads the file (found 2026-10-23). Every download session lifts the limit.
MAX_HEADER_BYTES = 65536


def session(timeout: Optional[aiohttp.ClientTimeout] = None, **kw) -> aiohttp.ClientSession:
    """A `ClientSession` for downloads: the same as the default one, with room for long answer headers."""
    kw.setdefault("max_field_size", MAX_HEADER_BYTES)
    kw.setdefault("max_line_size", MAX_HEADER_BYTES)
    return aiohttp.ClientSession(timeout=timeout or aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120), **kw)
