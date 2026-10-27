"""A download that fails says the real reason and tries again by itself (2026-10-23, redone after the first build was lost).

The report: the Decisions tab said "Check your internet" for a runner download that failed because GitHub's answer carried a header longer than the download library allows
(`https_only.session` lifts that limit). The same message was shown for every failure, whatever it was. Now:

  * `reason_text` is the plain reason per case: the site's own answer (429, 403, 404, 5xx, any other status), a connection that could not be made (the only case that says "check the
    internet"), a timeout, a redirect that could not be followed. `full_text` puts "Couldn't download <what> on <device>." in front.
  * `run` tries an operation up to four times in all: waits of 3 s, 10 s and 30 s (the site's `Retry-After` first, up to 120 s) for 429, 500 to 504, a lost connection and a timeout;
    403 and 404 one more time; any other answer, a bad checksum or a full disk is not repeated.
  * Every failure is written to the log with the status, host, file, device, tries and seconds. The query string of the address is cut: a release asset address carries a signed token.
"""
import asyncio
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

import aiohttp

import https_only

WAITS = (3.0, 10.0, 30.0)
MAX_RETRY_AFTER = 120.0
TRANSIENT_STATUSES = (429, 500, 501, 502, 503, 504)
ONE_MORE = (403, 404)


class HttpStatus(Exception):
    """The site answered with a status that is not a download (`url` is the address that answered; `retry_after` is the site's own wait, in seconds, when it sent one)."""

    def __init__(self, status: int, url: str = "", retry_after: Optional[float] = None):
        super().__init__(f"The site answered {status}.")
        self.http_status, self.url, self.retry_after = status, url, retry_after      # (`status` stays free for a class that is also a LocalModelError)


def strip_query(url: str) -> str:
    """The address without its query string (and fragment): the signed token of a release asset lives there."""
    try:
        p = urlsplit(url)
    except ValueError:
        return ""
    return f"{p.scheme}://{p.netloc}{p.path}" if p.scheme else p.path


def host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def site_name(host: str) -> str:
    """"GitHub" / "Hugging Face" for the sites the app downloads from, otherwise the address itself."""
    h = (host or "").lower()
    if h == "github.com" or h.endswith(".github.com") or h.endswith("githubusercontent.com"):
        return "GitHub"
    if h == "huggingface.co" or h.endswith(".huggingface.co") or h == "hf.co" or h.endswith(".hf.co"):
        return "Hugging Face"
    return host or "the download site"


def retry_after_seconds(value) -> Optional[float]:
    """A `Retry-After` header in seconds (the date form is ignored), capped."""
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return min(max(v, 0.0), MAX_RETRY_AFTER)


def status_of(e: BaseException) -> Optional[int]:
    if isinstance(e, HttpStatus):
        return e.http_status
    if isinstance(e, aiohttp.ClientResponseError):
        return e.status
    return None


def kind_of(e: BaseException) -> str:
    """status | timeout | redirect | connection | other"""
    if status_of(e) is not None:
        return "status"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(e, https_only.RedirectRefused):
        return "redirect"
    if isinstance(e, (aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError, ConnectionError)):
        return "connection"
    if isinstance(e, aiohttp.ClientError):
        return "connection"
    return "other"


def _host_for(e: BaseException, url: str) -> str:
    return host_of(getattr(e, "url", "") or "") or host_of(str(getattr(getattr(e, "request_info", None), "url", "") or "")) or host_of(url)


def reason_text(e: BaseException, url: str, device: str) -> str:
    """The plain reason, one or two short sentences. Never an address with a token, a class name or a path."""
    host = _host_for(e, url)
    site = site_name(host)
    kind = kind_of(e)
    if kind == "status":
        n = status_of(e)
        if n == 429:
            return f"{site} answered 429: too many requests. Try again in a few minutes."
        if n == 403:
            return f"{site} answered 403: it refused the download. Try again in a few minutes."
        if n == 404:
            return f"{site} answered 404: this file is not on the site."
        if n is not None and 500 <= n <= 599:
            return f"{site} answered {n}: the site has a problem on its end. Try again in a few minutes."
        return f"{site} answered {n}."
    if kind == "timeout":
        return f"{site} didn't answer in time."
    if kind == "redirect":
        return f"{site} sent the download somewhere it could not follow."
    if kind == "connection":
        return f"Couldn't connect to {host or site}. Check the internet connection on {device}."
    return "Something went wrong with the download."


def full_text(e: BaseException, url: str, what: str, device: str) -> str:
    return f"Couldn't download {what} on {device}. {reason_text(e, url, device)}"


def wait_for(e: BaseException, tries_so_far: int, waits=None) -> Optional[float]:
    """Seconds to wait before the next try, or None when this failure is not tried again."""
    waits = WAITS if waits is None else waits            # read now, not when the module loaded: tests shorten the waits
    kind = kind_of(e)
    if kind == "status":
        n = status_of(e)
        if n in TRANSIENT_STATUSES:
            ra = getattr(e, "retry_after", None)
            if ra is None and isinstance(e, aiohttp.ClientResponseError):
                ra = retry_after_seconds((e.headers or {}).get("Retry-After"))
            if tries_so_far > len(waits):
                return None
            return ra if ra is not None else waits[tries_so_far - 1]
        if n in ONE_MORE:
            return waits[0] if tries_so_far == 1 else None
        return None
    if kind in ("connection", "timeout"):
        return waits[tries_so_far - 1] if tries_so_far <= len(waits) else None
    return None


class DownloadFailed(Exception):
    """The download did not work after the tries it was allowed. `message` is the whole plain line (it starts "Couldn't download")."""

    def __init__(self, message: str, cause: BaseException):
        super().__init__(message)
        self.message, self.cause = message, cause


async def run(op: Callable[[], Awaitable], *, url: str, what: str, device: str, file: str = "", log: Callable[[str], None] = lambda m: None,
              sleep: Callable[[float], Awaitable] = asyncio.sleep, waits=None):
    """Run `op()` (one whole download) until it works or may not be tried again. Raises `DownloadFailed` with the plain text; anything that is not a download problem
    (a bad checksum, a full disk, a cancel) goes through untouched."""
    start = time.monotonic()
    tries = 0
    while True:
        tries += 1
        try:
            return await op()
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            if kind_of(e) == "other":
                raise
            wait = wait_for(e, tries, waits)
            host = _host_for(e, url)
            line = (f"A download failed on {device}: {status_of(e) or kind_of(e)} from {host or 'the site'} for {file or strip_query(url).rsplit('/', 1)[-1]}"
                    f" (try {tries}, {time.monotonic() - start:.0f} s)" + ("; trying again in %.0f s" % wait if wait is not None else "; giving up"))
            log(line)
            if wait is None:
                raise DownloadFailed(full_text(e, url, what, device), e) from e
            await sleep(wait)
