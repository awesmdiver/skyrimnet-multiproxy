"""Keeps other websites out of MultiProxy's own local servers (security review before 4.0, 2026-10-06).

Listening on 127.0.0.1 does not stop a web page open in the player's own browser from calling it (the same finding SkyrimNet's author made about ModPacer and the
Vortex Bridge), so every request is checked before any route sees it:

  1. `Host` must be exactly 127.0.0.1:<port>, localhost:<port> or [::1]:<port> (the port the connection really arrived on). A foreign name pointed at 127.0.0.1
     (DNS rebinding) is refused.
  2. An `Origin`, when there is one, must be this very page. `Sec-Fetch-Site: cross-site` is refused too. No `Access-Control-*` header is ever sent, so a page on
     another site cannot read an answer either.
  3. A request that changes something on the dashboard side (`/config/...`, `/start-here/...`, `/quicktest/...`) must carry `X-MultiProxy: 1` (a custom header cannot
     be sent from another site without a CORS preflight, and none is ever granted) and, when it has a body, `application/json`.

The routes a program calls (SkyrimNet's `/v1/...`, the decisions and voice paths, `/health`, the tray's token-gated `/local/...`) send no `Origin`, `Referer` or
`Sec-Fetch-*` at all: a browser always sends at least `Origin` on a cross-site POST. So an unmarked request is a program on this device and is let through (after the
Host check); a marked one has to be this page. That is the whole rule for those routes.
"""
import json
import logging
from typing import Callable, Optional

CUSTOM_HEADER = b"x-multiproxy"
REFUSAL = "For security, MultiProxy only responds to its own page on this device."
NOT_JSON = "MultiProxy couldn't read the data sent to it."
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_DASHBOARD_WRITE_PREFIXES = ("/config/", "/start-here/", "/quicktest/")

# Tests drive the app through an in-memory client whose Host is "t" or "testserver"; tests/conftest.py turns the guard off for the whole suite and the guard's own tests
# turn it back on. Nothing outside the test suite ever sets this.
ENABLED = True


def allowed_hosts(port) -> list:
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
    if port == 80:
        hosts += ["127.0.0.1", "localhost", "[::1]"]
    return hosts


def refusal(method: str, path: str, headers: dict, port) -> Optional[tuple]:
    """(status, message, why) when the request must be refused, else None. `headers`: lower-cased name (bytes) to value (str)."""
    hosts = allowed_hosts(port)
    host = headers.get(b"host", "").strip().lower()
    if host not in hosts:
        return 403, REFUSAL, "foreign Host"
    origin = headers.get(b"origin")
    if origin is not None and origin.strip().lower() not in {f"http://{h}" for h in hosts}:
        return 403, REFUSAL, "foreign Origin"
    if headers.get(b"sec-fetch-site", "").strip().lower() == "cross-site":
        return 403, REFUSAL, "cross-site request"
    if method.upper() not in _READ_METHODS and path.startswith(_DASHBOARD_WRITE_PREFIXES):
        if headers.get(CUSTOM_HEADER, "").strip() != "1":
            return 403, REFUSAL, "missing X-MultiProxy header"
        has_body = int(headers.get(b"content-length", "0") or 0) > 0 or b"transfer-encoding" in headers
        ctype = headers.get(b"content-type", "").strip().lower()
        if has_body and not (ctype == "application/json" or ctype.startswith(("application/json;", "application/json "))):
            return 415, NOT_JSON, "body is not application/json"
    return None


def _headers(scope) -> dict:
    out = {}
    for k, v in scope.get("headers") or ():
        out.setdefault(bytes(k).lower(), bytes(v).decode("latin-1"))
    return out


class LocalGuard:
    """A plain ASGI middleware (no body is ever read here)."""

    def __init__(self, app, log: Optional[Callable[[str], None]] = None):
        self.app = app
        self.log = log or logging.getLogger("multiproxy").info

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not ENABLED:
            return await self.app(scope, receive, send)
        port = (scope.get("server") or (None, None))[1]
        try:
            bad = refusal(scope.get("method", "GET"), scope.get("path", ""), _headers(scope), port)
        except ValueError:                                  # a Content-Length that is not a number
            bad = (400, "Bad request.", "bad Content-Length")
        if bad is None:
            return await self.app(scope, receive, send)
        status, message, why = bad
        try:
            self.log(f"Refused {scope.get('method')} {scope.get('path')}: {why}")
        except Exception:                                   # the log is never a reason to fail
            pass
        body = json.dumps({"detail": message}).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
