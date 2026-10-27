"""Add a PC: one address, opened on the other PC, and it pairs itself (4.1, 2026-10-25).

Mockup `design/mockup-easier-installing.html` section 2, decisions `design/easier-installing-decisions.md`. The token only replaces TYPING the pairing code: the exchange
afterwards is the existing one (`RemoteServers.add_multiproxy` connects to the Courier with its code).

The flow:
  1. The person presses Add a PC on the Other devices page: `new_address()` makes a one-time token (15 minutes) and opens a small door on this PC's home-network
     address. A newer address makes the older one stop at once.
  2. On the other PC, `GET /add-a-pc?t=<token>` is a plain page with the download button for the right system. The download is the normal, unchanged Courier
     installer (or an official link to it). The pairing travels in a SEPARATE small file, `courier-pair.json` (this PC's address and the token), saved next to it.
  3. The Courier reads that file at its first start (courier.claim_pair_file), deletes it, and sends `POST /add-a-pc/register` with the token, its port, its pairing
     code and its name. The door checks the token and the source address, then adds the Courier with `add_multiproxy`. The token is used up.

A device that presents a live token does not pair by itself: the main PC asks first ("<NAME> wants to join." Add / No, `join_pending` / `answer`). Add completes the pairing as
before; No, no answer in 2 minutes, a newer address, Done or the 15-minute expiry refuse it (the Courier ends its try cleanly) and the address stays usable. Until it is answered
the claim holds, so a second device is turned away (409).

The door (a new network door, built to the standing rules; security review item follows): bound to ONE home-network address, nothing at all without a live token;
the exact Host (`<that address>:<port>`); the caller must be on a private network; an Origin, when there is one, must be this very page; no `Access-Control-*`
header is ever sent; a POST needs `X-MultiProxy: 1` and `application/json`; a per-address rate limit; a failed try writes one log line that never holds the token;
no answer names another device, a key or a setting. The door is closed again after the pairing, on Done, and at expiry.
"""
import asyncio
import hmac
import json
import re
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

import home_address
import other_pcs

PORT_DEFAULT = 8120
EXPIRES_SECONDS = 15 * 60
WAITING_WARNING_SECONDS = 60                    # nothing seen after about a minute: the screen adds the "same home network" line
RATE_LIMIT = (30, 60.0)                         # requests per source address per window
PAIR_FILE_NAME = "courier-pair.json"
JOIN_WAIT_SECONDS = 120.0                       # how long a device waits for the click on the main PC
PAIR_FILE_VERSION = 1
MAX_BODY = 4096                                 # the biggest body the door reads, with or without a Content-Length
INSTALLER_DIR_NAME = "courier-installers"       # next to the program: the Courier's normal installers this PC can hand out itself

# Every word the other PC's browser shows, in one place (the design side swaps them here).
TEXT = {
    "title": "Add this PC to MultiProxy on {pc}",
    "windows": "Download for Windows",
    "linux": "Download for Linux",
    "expired": "This address has expired. Ask for a new one on the main PC.",
    "network_only": "This address only works on your home network.",
    "too_many": "Too many tries. Wait a minute and try again.",
    "no_package": "There is nothing to download here. Ask for a new address on the main PC.",
    "no_ip": "Could not find this PC's home network address. Is it connected to your network?",
    "bad_choice": "This PC does not recognize that address. Press Add a PC again.",
}
WINDOWS_LINK = other_pcs.MODEL_SERVER_DOWNLOAD_URL
LINUX_LINK = "https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest/download/install-courier.sh"


def find_packages(program_dir: Path) -> list:
    """What this PC can hand out: [{"os", "name", "path" | "url"}]. A normal installer file kept in `courier-installers` next to the program is served from here; otherwise
    the official release link is used (a redirect, so the file is exactly the one everyone gets). One entry per system, never one for a file that is not there."""
    out = []
    folder = Path(program_dir) / INSTALLER_DIR_NAME
    for system, pattern in (("windows", "MultiProxy-Courier-Setup-*.exe"), ("linux", "MultiProxy-Courier-*.tar.gz")):
        try:
            files = sorted(p for p in folder.glob(pattern) if p.is_file()) if folder.is_dir() else []
        except OSError:
            files = []
        if files:
            out.append({"os": system, "name": files[-1].name, "path": files[-1]})
        else:
            out.append({"os": system, "name": "", "url": WINDOWS_LINK if system == "windows" else LINUX_LINK})
    return out


class Session:
    def __init__(self, token: str, now: float, ip: str, port: int, choices: Optional[list] = None):
        self.token, self.made_at, self.ip, self.port = token, now, ip, port
        self.choices = choices or []            # every home address this PC could be reached on, best first: [{"ip", "name", "samePc"}]
        self.expires_at = now + EXPIRES_SECONDS
        self.used = False
        self.pending: Optional[dict] = None     # the device waiting for the click: {"name", "future"}
        self.claiming = False                   # a pairing is under way: no second request may start one with the same token
        self.paired_name = ""


class AddAPc:
    def __init__(self, remotes, log, *, program_dir, clock: Callable[[], float] = time.time,
                 ips_fn: Optional[Callable[[], list]] = None, candidates_fn: Optional[Callable[[], list]] = None, this_pc: Callable[[], str] = other_pcs.short_hostname,
                 packages_fn: Optional[Callable[[], list]] = None, allow_loopback: bool = False, port: int = PORT_DEFAULT,
                 ask_first: bool = True, join_wait: float = JOIN_WAIT_SECONDS):
        self.remotes, self.log, self.clock = remotes, log, clock
        self.this_pc, self.allow_loopback, self.port_wanted = this_pc, allow_loopback, port
        # The address to show is picked by home_address (the real home network, never a virtual adapter). `ips_fn` (plain addresses) is for stand-ins that fix the list.
        if candidates_fn is None:
            candidates_fn = (lambda: [{"ip": i, "name": "", "samePc": False} for i in ips_fn() or []]) if ips_fn else self._home_candidates
        self.candidates_fn = candidates_fn
        self.ask_first, self.join_wait = ask_first, join_wait
        self.packages_fn = packages_fn or (lambda: find_packages(program_dir))
        self.session: Optional[Session] = None
        self._runner: Optional[web.AppRunner] = None
        self._hits: dict = {}
        self._expiry: Optional[asyncio.TimerHandle] = None

    # ---- the main PC's screen --------------------------------------------------------------------------------------------------
    def _live(self) -> bool:
        s = self.session
        return bool(s and not s.used and self.clock() < s.expires_at)

    def _paired_ips(self) -> list:
        out = []
        for r in self.remotes.all() if hasattr(self.remotes, "all") else []:
            m = re.match(r"https?://(\d+\.\d+\.\d+\.\d+)[:/]", getattr(r, "base", "") or "")
            if m:
                out.append(m.group(1))
        return out

    def _home_candidates(self) -> list:
        return home_address.home_candidates(self._paired_ips())

    def state(self) -> dict:
        s = self.session
        if not s or (self.clock() >= s.expires_at and not s.used):
            return {"active": False, "expired": bool(s), "pairedName": "", "url": ""}
        waited = self.clock() - s.made_at
        others = [{"ip": c["ip"], "label": home_address.label(c)} for c in s.choices if c["ip"] != s.ip]
        mine = next((c for c in s.choices if c["ip"] == s.ip), {})
        # the cue: other addresses exist and none of the paired PCs is on this one's network
        paired = self._paired_ips()
        wrong_net = bool(others and paired and not mine.get("samePc"))
        return {"active": True, "expired": False, "pairedName": s.paired_name, "joinName": (s.pending or {}).get("name", ""), "url": f"http://{s.ip}:{s.port}/add-a-pc?t={s.token}" if not s.used else "",
                "expiresIn": max(0, int(s.expires_at - self.clock())), "seen": s.used,
                "warnNetwork": (not s.used) and waited >= WAITING_WARNING_SECONDS,
                "others": [] if s.used else others, "wrongNetwork": wrong_net and not s.used}

    async def new_address(self, ip: str = "") -> dict:
        found = await asyncio.to_thread(self.candidates_fn)
        found = [c for c in found or [] if other_pcs.ip_class(c["ip"]) == "private" or (self.allow_loopback and other_pcs.ip_class(c["ip"]) == "loopback")]
        if not found:
            raise other_pcs.OtherPcsError(400, TEXT["no_ip"])
        pick = found[0]
        if ip:                                               # a chosen address must be one this PC really has
            pick = next((c for c in found if c["ip"] == ip), None)
            if pick is None:
                raise other_pcs.OtherPcsError(400, TEXT["bad_choice"])
        await self._close()                                  # the older address stops at once: its door is shut and its token is gone
        port = await self._open(pick["ip"])
        self.session = Session(secrets.token_urlsafe(32), self.clock(), pick["ip"], port, found)
        self._arm_expiry()
        self.log.info("Add a PC: a new address was made (works for 15 minutes).")
        return self.state()

    async def done(self) -> dict:
        self._resolve(False)
        self.session = None
        await self._close()
        return self.state()

    def answer(self, allow: bool) -> dict:
        """The click on the main PC: Add (True) or No (False) for the device that is waiting."""
        self._resolve(bool(allow))
        return self.state()

    def _resolve(self, allow: bool) -> None:
        s = self.session
        p = s.pending if s else None
        if p and not p["future"].done():
            p["future"].set_result(allow)

    async def _open(self, ip: str) -> int:
        app = web.Application(middlewares=[self._guard], client_max_size=MAX_BODY)
        app.on_response_prepare.append(self._headers)
        app.router.add_get("/add-a-pc", self._page)
        app.router.add_get("/add-a-pc/download/{system}", self._download)
        app.router.add_get("/add-a-pc/pair-file", self._pair_file)
        app.router.add_post("/add-a-pc/register", self._register)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        for port in (self.port_wanted, 0):                  # the usual port, else any free one
            try:
                site = web.TCPSite(runner, ip, port)
                await site.start()
                break
            except OSError:
                if port == 0:
                    await runner.cleanup()
                    raise other_pcs.OtherPcsError(500, "Couldn't open the address on this PC. Is another program using the port?")
        self._runner = runner
        return runner.addresses[0][1]

    async def _close(self) -> None:
        self._resolve(False)                                 # a device still waiting for the click is turned away
        if self._expiry:
            self._expiry.cancel()
            self._expiry = None
        runner, self._runner = self._runner, None
        if runner:
            try:
                await runner.cleanup()
            except Exception:
                pass

    def _arm_expiry(self) -> None:
        loop = asyncio.get_running_loop()
        left = max(0.0, self.session.expires_at - self.clock())
        self._expiry = loop.call_later(left + 0.5, lambda: loop.create_task(self._expire()))

    async def _expire(self) -> None:
        if self.session and not self._live() and not self.session.used:
            self.log.info("Add a PC: the address expired.")
        await self._close()

    # ---- the door ---------------------------------------------------------------------------------------------------------------
    def _refuse(self, status: int, text: str, why: str, ip: str = "") -> web.Response:
        self.log.info(f"Add a PC: refused a request{' from ' + ip if ip else ''} ({why}).")        # never the token, never the request body
        return web.Response(status=status, text=text, content_type="text/plain")

    def _rate_ok(self, ip: str) -> bool:
        n, window = RATE_LIMIT
        now = time.monotonic()
        q = self._hits.setdefault(ip, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= n:
            return False
        q.append(now)
        if len(self._hits) > 256:                           # never grows without bound
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window][:128]:
                self._hits.pop(k, None)
        return True

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        s = self.session
        ip = request.remote or ""
        if not s:
            return self._refuse(410, TEXT["expired"], "no live address", ip)
        host = request.headers.get("Host", "").strip().lower()
        if host != f"{s.ip}:{s.port}".lower():
            return self._refuse(403, TEXT["network_only"], "foreign Host", ip)
        cls = other_pcs.ip_class(ip)
        if not (cls == "private" or (self.allow_loopback and cls == "loopback")):
            return self._refuse(403, TEXT["network_only"], "not a home-network address", ip)
        origin = request.headers.get("Origin")
        if origin is not None and origin.strip().lower() != f"http://{host}":
            return self._refuse(403, TEXT["network_only"], "foreign Origin", ip)
        if request.headers.get("Sec-Fetch-Site", "").strip().lower() == "cross-site":
            return self._refuse(403, TEXT["network_only"], "cross-site request", ip)
        if not self._rate_ok(ip):
            return self._refuse(429, TEXT["too_many"], "too many tries", ip)
        if request.method == "POST":
            if request.headers.get("X-MultiProxy", "").strip() != "1":
                return self._refuse(403, TEXT["network_only"], "missing X-MultiProxy header", ip)
            if request.content_type != "application/json":
                return self._refuse(415, TEXT["network_only"], "body is not application/json", ip)
        if request.content_length and request.content_length > 4096:
            return self._refuse(413, TEXT["network_only"], "body too large", ip)
        return await handler(request)

    @staticmethod
    async def _headers(request: web.Request, response: web.StreamResponse) -> None:
        """On every answer, refusals and redirects too: the address holds the token, so no page or redirect may pass it on as a Referer, and nothing is kept."""
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

    def _token_ok(self, given: str) -> bool:
        s = self.session
        return bool(given and self._live() and hmac.compare_digest(str(given).encode(), s.token.encode()))

    def _expired(self, request: web.Request, why: str) -> web.Response:
        self.log.info(f"Add a PC: an address that no longer works was opened from {request.remote} ({why}).")
        return web.Response(status=410, text=TEXT["expired"], content_type="text/plain", charset="utf-8")

    async def _page(self, request: web.Request) -> web.Response:
        token = request.query.get("t", "")
        if not self._token_ok(token):
            return self._expired(request, "token")
        packages = self.packages_fn()
        mine = "linux" if re.search(r"linux|x11|android", request.headers.get("User-Agent", ""), re.I) and "windows" not in request.headers.get("User-Agent", "").lower() else "windows"
        shown = sorted(packages, key=lambda p: p["os"] != mine)
        if not shown:
            return web.Response(text=TEXT["no_package"], content_type="text/plain")
        buttons = "".join(
            f'<p><a class="btn{"" if i == 0 else " ghost"}" href="/add-a-pc/download/{p["os"]}?t={token}" data-pair="/add-a-pc/pair-file?t={token}">{TEXT[p["os"]]}</a></p>'
            for i, p in enumerate(shown))
        body = _PAGE.format(title=_esc(TEXT["title"].format(pc=self.this_pc())), buttons=buttons, pair=f"/add-a-pc/pair-file?t={token}")
        return web.Response(text=body, content_type="text/html", charset="utf-8", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def _download(self, request: web.Request) -> web.StreamResponse:
        if not self._token_ok(request.query.get("t", "")):
            return self._expired(request, "token")
        want = request.match_info["system"]
        pkg = next((p for p in self.packages_fn() if p["os"] == want), None)
        if not pkg:
            return web.Response(status=404, text=TEXT["no_package"], content_type="text/plain")
        if pkg.get("path"):
            return web.FileResponse(pkg["path"], headers={"Content-Disposition": f'attachment; filename="{pkg["name"]}"', "Cache-Control": "no-store"})
        raise web.HTTPFound(pkg["url"])

    async def _pair_file(self, request: web.Request) -> web.Response:
        if not self._token_ok(request.query.get("t", "")):
            return self._expired(request, "token")
        s = self.session
        body = json.dumps({"v": PAIR_FILE_VERSION, "main": f"http://{s.ip}:{s.port}", "token": s.token, "name": self.this_pc()}, indent=1)
        return web.Response(text=body, content_type="application/json", charset="utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{PAIR_FILE_NAME}"', "Cache-Control": "no-store"})

    async def _register(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            return self._refuse(400, "", "unreadable body", request.remote or "")
        if not isinstance(data, dict) or not self._token_ok(str(data.get("token", ""))):
            return self._expired(request, "token")
        try:
            port = int(data.get("port"))
            code = str(data.get("code", "")).strip()
            name = re.sub(r"[^\w .-]", "", str(data.get("name", ""))).strip()[:40]
            assert 1 <= port <= 65535 and 0 < len(code) <= 40
        except (TypeError, ValueError, AssertionError):
            return self._refuse(400, "", "bad details", request.remote or "")
        address = f"{request.remote}:{port}"            # the address the request really came from, never one it names
        s = self.session
        if s.claiming:                                  # one token, one device: a second request while the first is being paired is turned away (checked and set with no wait between)
            return self._refuse(409, TEXT["expired"], "a pairing is already under way", request.remote or "")
        s.claiming = True
        if self.ask_first:                              # the main PC asks before a PC joins (one click); no answer is a No
            fut = asyncio.get_running_loop().create_future()
            s.pending = {"name": name or "A PC", "future": fut}
            try:
                allowed = await asyncio.wait_for(asyncio.shield(fut), self.join_wait)
            except asyncio.TimeoutError:
                allowed = False
            finally:
                s.pending = None
            if not allowed or self.session is not s:
                s.claiming = False                      # the address stays usable for another try
                self.log.info("Add a PC: a device that asked to join was not added.")
                return web.json_response({"status": "declined"}, status=403)
        try:
            try:
                remote = await self.remotes.add_multiproxy(address, code, name)
            except other_pcs.OtherPcsError as e:
                if "nickname" not in str(e).lower():
                    raise
                remote = await self.remotes.add_multiproxy(address, code, f"{name or 'PC'} 2")
        except other_pcs.OtherPcsError as e:
            s.claiming = False                          # a pairing that failed leaves the address for another try
            self.log.info(f"Add a PC: couldn't pair {request.remote}: {e}")
            return web.json_response({"status": "failed", "message": str(e)}, status=502)
        except BaseException:
            s.claiming = False
            raise
        s.used, s.paired_name = True, remote.name
        self.log.info(f"Add a PC: {remote.name} paired.")
        asyncio.get_running_loop().call_later(1.0, lambda: asyncio.get_running_loop().create_task(self._close()))
        return web.json_response({"status": "paired", "name": self.this_pc()})


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>:root{{color-scheme:light dark}}body{{font:16px system-ui,sans-serif;margin:0;padding:48px 24px;max-width:560px}}h1{{font-size:1.4rem;margin:0 0 24px}}
.btn{{display:inline-block;padding:12px 22px;border-radius:8px;background:#2f6fed;color:#fff;text-decoration:none;font-weight:600}}.btn.ghost{{background:transparent;color:inherit;border:1px solid #888}}</style></head>
<body><h1>{title}</h1>{buttons}<noscript><p><a href="{pair}">courier-pair.json</a></p></noscript>
<script>document.querySelectorAll('a[data-pair]').forEach(function(a){{a.addEventListener('click',function(){{var f=document.createElement('a');f.href=a.dataset.pair;f.download='{pairname}';document.body.appendChild(f);setTimeout(function(){{f.click();f.remove()}},400)}})}})</script>
</body></html>""".replace("{pairname}", PAIR_FILE_NAME)
