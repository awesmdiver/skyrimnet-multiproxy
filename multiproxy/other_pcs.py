"""Local models, part 2 (2026-09-30): share models with your other devices, and use AI servers that run
somewhere else on your network.

Built to design/dashboard-local-models-mockup.html (section 4 and Revision 2). Like local_models.py it
has no dependency on multiproxy.py. Two halves:

  * THE MODEL SERVER side ("Let my main device use these models"): a small second web server, separate
    from the dashboard, bound only to this device's home-network (private) addresses. It answers only
    requests that carry the pairing code, and only ever runs this device's own local models. The
    dashboard and every key stay on 127.0.0.1 exactly as before. A tiny UDP "I'm here" reply lets
    the main device find it without typing an address.

  * THE GAMING device side ("Model servers"): a list of other devices, each either a paired MultiProxy or an
    AI server somebody already runs (llama.cpp, LM Studio, Ollama, LiteLLM, NVIDIA NIM, vLLM...),
    reached by address. Their models show up as `<nickname>/<model>`. Only private-network addresses
    are allowed (plus this device itself, for servers running here).

Works on Windows and Linux: nothing here uses a Windows-only call.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import re
import secrets
import socket
import subprocess
import hidden_process  # every program started from here goes through it: no console window flashing up
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from local_models import LOCAL_PREFIX, LocalModelError, slugify

SHARE_PORT_DEFAULT = 8100        # the model-server door; never the dashboard's 8000
DISCOVERY_PORT = 8099            # UDP "who's a model server here?"
DISCOVERY_ASK = "MULTIPROXY-WHO-IS-HERE-1"
DISCOVERY_ANSWER = "MULTIPROXY-HERE-1"
CODE_HEADER = "X-MultiProxy-Code"
POLL_SECONDS = 15.0
RETRY_AFTER_SECONDS = 30.0        # a device that stopped answering is tried again no sooner than this (the backup answers meanwhile)
POLL_TIMEOUT = 3.0
PAIR_TIMEOUT = 6.0
UNPAIR_LIMIT = 6.0                  # the longest a Remove click waits for the other device to be told it is unpaired
MAX_BAD_CODES = 10               # wrong codes allowed per address per minute before it's refused
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O/1/I: easy to read off a screen

MODEL_SERVER_DOWNLOAD_URL = "https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest/download/MultiProxy-Courier.zip"
LINUX_INSTALL_COMMAND = "curl -fsSL https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest/download/install-courier.sh | bash"

# One place for the "make a new pairing code?" question, so the Courier's tray menu and the Other devices tab ask it in the same words.
NEW_CODE_CONFIRM = {"question": "Make a new pairing code? Your main PC will need the new code to reconnect.",
                    "cancel": "Cancel", "confirm": "Make new code"}

# The line for a Courier that can't be reached, and for one that answers with a redirect (a Courier never redirects: something else is answering at that address, and the
# pairing code and the request must never be sent on to wherever it points).
COULDNT_CONNECT = "Couldn't reach Courier on that device. Is it running, and allowed through the Windows firewall?"

# The Courier's "Unpair from <name>?" question (its tray asks it) and the line shown when Remove couldn't reach the Courier.
UNPAIR_CONFIRM = {"title": "Unpair from {name}?", "question": "Your main PC will stop using this computer. You can pair it again later with a new code.",
                  "cancel": "Cancel", "confirm": "Unpair"}
REMOVED_NOT_REACHED = "Removed. That device wasn't reachable, so it still thinks it's paired. Choose Unpair from its tray menu, or pair it again."

# What a device says when it answers but refuses our code (its code changed): the row then asks for the new one instead of "not answering".
CODE_CHANGED_ERRORS = ("Wrong pairing code.", "That code doesn't match. Check the code shown on the other device.",
                       "That device generated a new pairing code. Enter it to reconnect.")
CODE_RATE_LIMITED = "That device is blocking this one for a minute because of too many wrong codes."

PRIVATE_NETWORK_MESSAGE = "That address isn't on a private network or Tailscale link. Check the address and try again."


class OtherPcsError(Exception):
    """A plain, player-readable reason something about another device didn't work, with an HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------------------------
# Pairing code
# --------------------------------------------------------------------------------------------
def new_pairing_code() -> str:
    """A fresh code like KX7-42M (six characters, easy to read and type)."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
    return f"{raw[:3]}-{raw[3:]}"


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def codes_match(given: str, real: str) -> bool:
    a, b = normalize_code(given), normalize_code(real)
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


# --------------------------------------------------------------------------------------------
# Which addresses are allowed
# --------------------------------------------------------------------------------------------
# A home network's three ranges, and 100.64.0.0/10: the range Tailscale (and other private overlay links) give their devices. IPv6 unique-local covers Tailscale's own IPv6 range too.
# The pairing code, the Host/Origin checks and the refusal of every public address are unchanged.
_PRIVATE_V4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")]
_ULA_V6 = ipaddress.ip_network("fc00::/7")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")        # also what ISPs use for carrier-grade NAT: listened on only on a real Tailscale adapter (see local_private_ips)

# Other private networks the player lists (a VPN that uses another range). One list per program (the main device and a Courier each keep their own, saved in their own settings).
BAD_NETWORK = "That doesn't look like a network. Write it like 10.8.0.0/16."
WILDCARD_NETWORK = "That range would let any address in, so it can't be added."
PUBLIC_NETWORK = "That range is on the public internet, so it can't be added. You can only add private ranges, like a VPN's addresses."
_extra_nets: list = []


def parse_private_network(text: str):
    """One typed range (or a single address) as a network. Refused with a plain line: malformed, a wildcard, or anything public (a range has to sit wholly inside private space)."""
    raw = (text or "").strip()
    try:
        net = ipaddress.ip_network(raw, strict=False)
    except ValueError:
        raise OtherPcsError(400, BAD_NETWORK)
    if net.prefixlen == 0 or net.prefixlen < (8 if net.version == 4 else 7):
        raise OtherPcsError(400, WILDCARD_NETWORK)
    if net.version == 4:
        known = any(net.subnet_of(n) for n in _PRIVATE_V4)
        private = net.network_address.is_private and net.broadcast_address.is_private
    else:
        known = net.subnet_of(_ULA_V6)
        private = net.network_address.is_private and net.broadcast_address.is_private
    if not known and (not private or net.network_address.is_loopback or net.network_address.is_unspecified or net.network_address.is_multicast):
        raise OtherPcsError(400, PUBLIC_NETWORK)
    return net


def clean_private_networks(items) -> list:
    """The list as saved: each entry checked, written the plain way (`10.8.0.0/16`), duplicates dropped. The first bad entry refuses the whole list."""
    if isinstance(items, str):
        items = re.split(r"[,\s;]+", items)
    out: list = []
    for item in items or []:
        if not str(item).strip():
            continue
        text = str(parse_private_network(str(item)))
        if text not in out:
            out.append(text)
    return out


def set_extra_networks(items) -> None:
    """Make this program honour that list (a saved entry that no longer passes is skipped, never trusted)."""
    nets = []
    for item in items or []:
        try:
            nets.append(parse_private_network(str(item)))
        except OtherPcsError:
            continue
    _extra_nets[:] = nets


def extra_networks() -> list:
    return [str(n) for n in _extra_nets]


def _in_extra(addr) -> bool:
    return any(addr.version == n.version and addr in n for n in _extra_nets)


def ip_class(ip: str) -> str:
    """"private" (a home-network, Tailscale-link or listed-VPN address), "loopback" (this device) or "other"."""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return "other"
    if addr.is_loopback:
        return "loopback"
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return ip_class(str(addr.ipv4_mapped))
        return "private" if addr in _ULA_V6 or _in_extra(addr) else "other"
    return "private" if any(addr in net for net in _PRIVATE_V4) or _in_extra(addr) else "other"


def resolve_host(host: str) -> list[str]:
    """Every address a name points at (a literal address is returned as-is). Blocking."""
    try:
        ipaddress.ip_address(host.split("%")[0])
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise OtherPcsError(400, f"Couldn't find {host}. Try its exact network address instead, like 192.168.1.50.")
    return sorted({i[4][0] for i in infos})


def check_host_allowed(host: str, *, allow_loopback: bool) -> None:
    """Refuse anything that isn't a home-network address (or, when allowed, this device). EVERY address
    the name resolves to must pass, so a name can't quietly point somewhere public."""
    for ip in resolve_host(host):
        cls = ip_class(ip)
        if cls == "private" or (cls == "loopback" and allow_loopback):
            continue
        if cls == "loopback":
            raise OtherPcsError(400, "That address is this device. Add it on the Local device tab instead.")
        raise OtherPcsError(400, PRIVATE_NETWORK_MESSAGE)


@dataclass
class Address:
    scheme: str
    host: str
    port: int
    path: str = ""

    @property
    def base(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{self.scheme}://{host}:{self.port}{self.path}"


def parse_address(text: str, default_port: Optional[int]) -> Address:
    """Turn what a player typed ("192.168.1.50", "den-pc:8100", "http://192.168.1.60:8080/v1") into
    its parts. A port is required unless `default_port` is given."""
    raw = (text or "").strip()
    if not raw:
        raise OtherPcsError(400, "Type the other device's network address, like 192.168.1.50.")
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise OtherPcsError(400, "That address doesn't look right. Try 192.168.1.50.")
    if parts.scheme not in ("http", "https") or not host:
        raise OtherPcsError(400, "That address doesn't look right. Try 192.168.1.50.")
    if port is None:
        if default_port is None:
            raise OtherPcsError(400, "Add the exact address, like 192.168.1.60:8080.")
        port = default_port
    path = parts.path.rstrip("/")
    return Address(parts.scheme, host, port, path)


# --------------------------------------------------------------------------------------------
# This device's own home-network addresses
# --------------------------------------------------------------------------------------------
def adapter_addresses(run=hidden_process.run) -> list:
    """[(adapter name, IPv4 address)] for every adapter this device has; [] when it can't be read (then no VPN address is listened on)."""
    out: list = []
    try:
        if sys.platform.startswith("linux"):
            text = run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
            for m in re.finditer(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/", text, re.M):
                out.append((m.group(1), m.group(2)))
        elif sys.platform == "win32":
            cmd = "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.InterfaceAlias + '|' + $_.IPAddress }"
            text = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True, timeout=15).stdout
            for line in text.splitlines():
                name, _, ip = line.strip().rpartition("|")
                if name and ip:
                    out.append((name, ip))
    except Exception:
        return []
    return out


def is_tailscale_adapter(name: str) -> bool:
    """An adapter Tailscale made: named like Tailscale on Windows, `tailscale0` on Linux."""
    return "tailscale" in (name or "").lower()


def local_private_ips(adapters_fn=adapter_addresses) -> list[str]:
    """This device's private IPv4 addresses (what the model-server door should listen on): its home-network addresses; a 100.64.0.0/10 address ONLY when it belongs to a real Tailscale
    adapter (ISPs hand the same range out for carrier-grade NAT, and listening on that would let their other customers knock); and an address inside a range the player listed, on any
    adapter that already has one. Only addresses the device really has are listed; nothing is listened on by guessing."""
    found: set[str] = set()
    for target in ("10.255.255.255", "172.31.255.255", "192.168.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 1))
                found.add(s.getsockname()[0])
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    if sys.platform.startswith("linux"):
        try:
            out = hidden_process.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
            found.update(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", out))
        except Exception:
            pass
    tailscale_ips, listed_ips = set(), set()
    for name, ip in adapters_fn() or []:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if _in_extra(addr):
            listed_ips.add(ip)
        elif is_tailscale_adapter(name) and addr in _CGNAT:
            tailscale_ips.add(ip)
    found |= tailscale_ips | listed_ips

    def may_listen(ip: str) -> bool:
        if ip_class(ip) != "private":
            return False
        addr = ipaddress.ip_address(ip)
        if addr in _CGNAT:
            return ip in tailscale_ips or ip in listed_ips
        return True
    return sorted(ip for ip in found if may_listen(ip))


def short_hostname() -> str:
    return socket.gethostname().split(".")[0] or "this-pc"


# --------------------------------------------------------------------------------------------
# Servers on other devices (the main device's side)
# --------------------------------------------------------------------------------------------
@dataclass
class Remote:
    id: str                          # the nickname, also the model prefix: <id>/<model>
    kind: str                        # "courier" | "multiproxy" (an older model server) | "openai" (LM Studio, Ollama, llama.cpp... by address)
    name: str                        # what to show (the other device's name, or the nickname)
    base: str                        # http://host:port  (a "multiproxy" one never has a path)
    code: str = ""                   # pairing code (multiproxy only)
    key: str = ""                    # optional API key (openai only)
    online: Optional[bool] = None    # None = not checked yet
    error: str = ""
    models: list = field(default_factory=list)     # [{"id", "name", "vision", "state"}]
    gpu: Optional[dict] = None
    checked_at: float = 0.0
    instance: str = ""              # the other device's permanent id (a Courier's), so a new router address doesn't lose it
    retry_at: float = 0.0           # while it's offline: don't send a request to it before this time (a backup answers instead)
    program: str = ""               # what runs its models: "Ollama", "LM Studio", "llama.cpp", "MultiProxy" (older model server), "AI server"
    report: Optional[dict] = None   # a Courier's last report (pushed over its live connection)
    report_at: float = 0.0          # when this device received it
    client: Any = field(default=None, repr=False)      # a Courier's live connection (courier_client.CourierClient)
    name_typed: bool = False        # the player typed this name: a report from that device doesn't replace it with the device's own
    announced: bool = False         # the online hooks have run for this coming-online (reset whenever the device is seen offline), so they run once per coming-online
    code_changed: bool = False      # it answers, but refuses our code: reachable, so the row asks for the new code instead of "not answering"

    def note_state(self) -> None:
        """Keep `code_changed` in step with the last check: set by a refused code, kept while that device rate-limits us, cleared by anything else."""
        if self.online is False:
            self.announced = False
        if self.kind not in ("courier", "multiproxy") or self.online is not False:
            self.code_changed = False
        elif self.error in CODE_CHANGED_ERRORS:
            self.code_changed = True
        elif self.error != CODE_RATE_LIMITED:
            self.code_changed = False

    @property
    def api_base(self) -> str:
        if self.kind == "courier":
            return self.base + "/v1"
        if self.kind == "multiproxy":
            return self.base + "/share/v1"
        return self.base if self.base.rstrip("/").endswith("/v1") else self.base.rstrip("/") + "/v1"

    def headers(self) -> dict:
        if self.kind == "courier":
            return {"Authorization": f"Bearer {self.code}"}
        if self.kind == "multiproxy":
            return {CODE_HEADER: self.code}
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def ready_models(self) -> list:
        if self.kind == "courier":
            return [m for m in self.models if m.get("state") != "error"]      # Ollama / LM Studio / llama.cpp all load a model when asked
        if self.kind == "multiproxy":
            return [m for m in self.models if m.get("state") in ("running", "starting")]
        return list(self.models)

    @property
    def label(self) -> str:
        """What the player sees for this device: its name as typed or as the device calls itself, with the lower-case id only when there is no name."""
        return self.name or self.id

    def to_json(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "name": self.name, "address": self.base,
            "online": self.online, "error": self.error, "codeChanged": self.code_changed, "gpu": self.gpu, "program": self.program,
            "report": self.report if self.kind == "courier" else None,
            "courierBuild": courier_build_of(self.report) if self.kind == "courier" else None,
            "canUpdate": bool(self.kind == "courier" and ((self.report or {}).get("capabilities") or {}).get("update") is True),          # this Courier can take a signed update from here (courier_update.py)
            "courierOlder": courier_is_older(self.report) if self.kind == "courier" else False,
            "models": [{"id": m["id"], "modelName": f"{self.id}/{m['id']}", "vision": bool(m.get("vision")),
                        "state": m.get("state", "running")} for m in self.models],
        }


# The oldest Courier build this MultiProxy fully supports. A Courier's report carries its build number (`courier.build`, an integer in courier.py that is bumped by hand
# whenever the Courier's abilities or protocol change); one with no number, or a lower one, is older and the player is told to update it on that device.
MIN_COURIER_BUILD = 1


def courier_version_of(build) -> str:
    """The one number a person sees for a Courier: 1.<build>. A Courier that reports only its build (an older one) reads the same."""
    return f"1.{build}"


def courier_build_of(report: Optional[dict]) -> Optional[int]:
    try:
        return int(((report or {}).get("courier") or {}).get("build"))
    except (TypeError, ValueError):
        return None


def courier_is_older(report: Optional[dict]) -> bool:
    """True for a Courier whose report says it is older than this MultiProxy supports. A device that has not reported yet is not called old."""
    if not report:
        return False
    b = courier_build_of(report)
    return b is None or b < MIN_COURIER_BUILD


def courier_voice_models(report: Optional[dict]) -> list:
    """The voice models a Courier says it can run. A Courier that says nothing (one built before the voice models) runs Higgs only; one with no voice block runs none."""
    v = (report or {}).get("voice")
    if v is None:
        return []
    listed = v.get("supportedModels")
    if isinstance(listed, list):
        return [str(k) for k in listed]
    if isinstance(v.get("licenses"), dict) and v["licenses"]:
        return [str(k) for k in v["licenses"]]
    return ["higgs"]


def log_offline_text(pc: str) -> str:
    return f"{pc} is offline, so its details can't be read right now."


def problem_of(r: "Remote") -> Optional[dict]:
    """The one problem a Courier device's latest report carries (a program that won't start, a model that failed to load), for the
    muted line on its row, or None. It clears by itself: the Courier forgets the message when the next attempt works."""
    rep = r.report if r.kind == "courier" else None
    for p in (rep or {}).get("programs", []):
        if p.get("error") and p.get("state") == "error":
            ago = p.get("error_ago_seconds")
            return {"program": p.get("name") or p.get("id"), "message": p["error"],
                    "agoSeconds": None if ago is None else int(ago + max(0.0, time.time() - (r.report_at or time.time())))}
        bad = next((m for m in p.get("models", []) if m.get("state") == "error" and m.get("error")), None)
        if bad:
            return {"program": p.get("name") or p.get("id"), "message": bad["error"], "agoSeconds": None}
    return None


def courier_program_label(report: Optional[dict]) -> str:
    """What to call a Courier device in the list: the AI programs it really has (LM Studio, Ollama), joined with " + ";
    MultiProxy's own llama.cpp when there's nothing else."""
    names = [p["name"] for p in (report or {}).get("programs", []) if p.get("installed") and p["id"] != "llamacpp"]
    if ((report or {}).get("claude") or {}).get("state") == "ready":
        names.append("Claude")                          # signed in on that device (claude_courier.py): its models are in the list below
    if ((report or {}).get("copilot") or {}).get("state") == "ready":
        names.append("Copilot")                         # the same for GitHub's program (copilot_courier.py)
    return " + ".join(names) or "llama.cpp"


def engines_of(report: Optional[dict]) -> list:
    """The programs a Courier really reports, one entry each, for the Overview: [{"id", "name", "running", "program"}]. Ollama, LM Studio and the built-in llama.cpp (the last
    only when it holds a model) as the report lists them; Claude and Copilot when signed in there. Nothing is made up: no report, no list."""
    out = []
    for p in (report or {}).get("programs", []):
        if p.get("id") == "llamacpp" and not p.get("models"):
            continue
        if p.get("installed") or p.get("models"):
            caps = p.get("capabilities") or {}
            out.append({"id": p["id"], "name": p.get("name") or p["id"], "running": bool(p.get("running")) or p.get("state") in ("running", "ready", "busy", "loading"),
                        "can": {k: bool(caps.get(k)) for k in ("start", "stop")}})          # what that program's Courier can do (an older Courier lists none: no button)
    for key, label in (("claude", "Claude"), ("copilot", "Copilot")):
        if ((report or {}).get(key) or {}).get("state") == "ready":
            out.append({"id": key, "name": label, "running": True})
    return out


def models_from_report(report: dict) -> list:
    """A Courier's models as the flat list the rest of MultiProxy routes with: every downloaded model, whichever program has it."""
    out = []
    for p in report.get("programs", []):
        for m in p.get("models", []):
            out.append({"id": m["id"], "name": m.get("name") or m["id"], "vision": bool(m.get("vision")), "program": p["id"],
                        "state": "running" if m.get("loaded") else ("starting" if m.get("state") == "loading" else
                                                                    ("error" if m.get("state") == "error" else "available"))})
    claude = report.get("claude") or {}
    if claude.get("state") == "ready":                  # Claude signed in on that device answers by name like any model there (claude_courier.py)
        for m in claude.get("models", []):
            out.append({"id": m["id"], "name": "Claude " + (m.get("name") or m["id"]), "vision": False, "program": "claude", "state": "running"})
    copilot = report.get("copilot") or {}
    if copilot.get("state") == "ready":                 # Copilot signed in on that device answers by name like any model there (copilot_courier.py)
        for m in copilot.get("models", []):
            out.append({"id": m["id"], "name": "Copilot " + (m.get("name") or m["id"]), "vision": False, "program": "copilot", "state": "running"})
    return out


def image_hint(status: int, message: str) -> str:
    """llama.cpp answers a picture it can't read with 'image input is not supported ... mmproj'. Say
    what to do about it in plain words."""
    low = (message or "").lower()
    if "image input is not supported" in low or "mmproj" in low:
        return "That provider can't read pictures. Check its settings and try again."
    return message


class RemoteServers:
    """The main device's list of other devices / AI servers, with live Online/Offline state."""

    def __init__(self, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], log,
                 reserved: Callable[[str], Optional[str]], forbidden_loopback_ports: Callable[[], set],
                 session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
                 client_factory: Optional[Callable[..., Any]] = None):
        self._load_cfg, self._save_cfg, self.log = load_cfg, save_cfg, log
        self._client_factory = client_factory          # builds a Courier's live connection (courier_client.CourierClient); None = no live connections
        self._reserved = reserved                      # nickname -> why it can't be used (or None)
        self._forbidden_ports = forbidden_loopback_ports
        self._session_factory = session_factory or aiohttp.ClientSession
        self._remotes: dict[str, Remote] = {}
        self._loaded = False
        self._poll_task: Optional[asyncio.Task] = None
        self.online_hooks: list = []                   # called with the Remote once per coming-online, on its first report (MultiProxy's own start counts: the first report after it)
        self.report_hooks: list = []                   # called with the Remote after each Courier report is taken in (the model settings sender uses this)

    # ---- list ------------------------------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        saved = self._load_cfg().get("remote_servers") or []
        for r in saved:
            if not isinstance(r, dict) or not r.get("id") or r["id"] in self._remotes:
                continue
            self._remotes[r["id"]] = Remote(id=r["id"], kind=r.get("kind", "openai"), name=r.get("name") or r["id"],
                                            base=r["base"], code=r.get("code", ""), key=r.get("key", ""), instance=r.get("instance", ""),
                                            program=r.get("program", ""), name_typed=bool(r.get("name_typed")))
        self._loaded = True

    def _persist(self) -> None:
        cfg = self._load_cfg()
        cfg["remote_servers"] = [{"id": r.id, "kind": r.kind, "name": r.name, "base": r.base,
                                  **({"code": r.code} if r.code else {}), **({"key": r.key} if r.key else {}),
                                  **({"instance": r.instance} if r.instance else {}), **({"program": r.program} if r.program else {}), **({"name_typed": True} if r.name_typed else {})}
                                 for r in self._remotes.values()]
        self._save_cfg(cfg)

    def all(self) -> list[Remote]:
        self._load()
        return list(self._remotes.values())

    def get(self, rid: str) -> Optional[Remote]:
        self._load()
        return self._remotes.get(rid.lower())

    def match(self, model: str) -> Optional[tuple[Remote, str]]:
        """`den-pc/gemma-4-31b` -> (that server, "gemma-4-31b"). None if the part before the first
        `/` isn't one of this device's saved nicknames."""
        if "/" not in model:
            return None
        prefix, _, rest = model.partition("/")
        r = self.get(prefix)
        return (r, rest) if r and rest else None

    # ---- adding / removing ---------------------------------------------------------------------
    def _check_nickname(self, nickname: str) -> str:
        nick = slugify(nickname)
        if not nickname.strip():
            raise OtherPcsError(400, "Give it a nickname.")
        why = self._reserved(nick)
        if why:
            raise OtherPcsError(400, why)
        if nick in self._remotes:
            raise OtherPcsError(400, "You already have a device with that nickname. Pick a different one.")
        return nick

    async def add_multiproxy(self, address: str, code: str, nickname: str = "") -> Remote:
        self._load()
        addr = parse_address(address, SHARE_PORT_DEFAULT)
        await asyncio.to_thread(check_host_allowed, addr.host, allow_loopback=False)
        if not normalize_code(code):
            raise OtherPcsError(400, "Type the pairing code shown on the other device.")
        probe = Remote(id="", kind="multiproxy", name="", base=addr.base, code=code)
        await self._poll(probe, timeout=PAIR_TIMEOUT)
        if probe.online is not True:
            raise OtherPcsError(502 if "code" not in probe.error.lower() else 403, probe.error or "Couldn't reach that device.")
        shown = (nickname or "").strip() or probe.name or addr.host           # what the device is called on screen: as typed, or as the device calls itself
        nick = self._check_nickname(shown)
        probe.id, probe.name, probe.name_typed = nick, shown, bool((nickname or "").strip())
        self._remotes[nick] = probe
        self._persist()
        self._ensure_client(probe)
        self.log.info(f"Added {'Courier device' if probe.kind == 'courier' else 'model server'} '{probe.name}' ({addr.base})")
        return probe

    async def add_openai(self, address: str, nickname: str, key: str = "") -> Remote:
        self._load()
        addr = parse_address(address, None)
        await asyncio.to_thread(check_host_allowed, addr.host, allow_loopback=True)
        if ip_class(resolve_host(addr.host)[0]) == "loopback" and addr.port in self._forbidden_ports():
            raise OtherPcsError(400, "MultiProxy is already using that address. Pick a different one.")
        if not nickname.strip():
            nickname = addr.host.replace(".", "-")
        nick = self._check_nickname(nickname)
        probe = Remote(id=nick, kind="openai", name=nickname.strip(), base=addr.base, key=key.strip(), name_typed=True)
        await self._poll(probe, timeout=PAIR_TIMEOUT)
        if probe.online is not True:
            raise OtherPcsError(502, probe.error or "Couldn't reach that AI server.")
        self._remotes[nick] = probe
        self._persist()
        self.log.info(f"Added AI server '{probe.name}' ({addr.base})")
        return probe

    async def unpair_and_remove(self, rid: str) -> bool:
        """Tell a Courier device to unpair (it goes back to "not paired" with a fresh code), then forget it here.
        Returns False when it couldn't be reached: it is still removed here, it just still thinks it is paired."""
        self._load()
        r = self._remotes.get((rid or "").lower())
        reached = True
        if r is not None and r.kind == "courier" and r.client is not None:
            try:
                await asyncio.wait_for(r.client.unpair(), UNPAIR_LIMIT)       # a device that won't answer never holds the click up
            except Exception:                    # any trouble over there (a refused code, a body that isn't JSON, no answer): still remove it here
                reached = False
        self.remove(rid)
        return reached

    def remove(self, rid: str) -> None:
        self._load()
        gone = self._remotes.pop(rid.lower(), None)
        if gone is not None:
            self._drop_client(gone)
            self._persist()

    # ---- a Courier's live connection ---------------------------------------------------------------
    def _drop_client(self, r: Remote) -> None:
        c, r.client = r.client, None
        if c is not None:
            try:
                asyncio.get_running_loop().create_task(c.stop())
            except RuntimeError:
                pass

    def _ensure_client(self, r: Remote) -> None:
        """One persistent connection to each Courier (the main device opens it; the Courier never opens anything toward this device). It follows
        a changed address or code; its reports keep `r` up to date and a dropped connection marks the device offline at once."""
        if r.kind != "courier" or self._client_factory is None:
            return
        c = r.client
        if c is not None and (c.base != r.base or c.code != r.code):
            self._drop_client(r)
            c = None
        if c is None:
            try:
                c = self._client_factory(r.base, r.code, on_report=lambda rep, r=r: self._apply_report(r, rep),
                                         on_offline=lambda why, r=r: self.mark(r, False, why))
            except OtherPcsError as e:
                self.mark(r, False, e.message)
                return
            r.client = c
        c.start()

    def _apply_report(self, r: Remote, rep: dict) -> None:
        if r.report and rep.get("generated_at", 0) < r.report.get("generated_at", 0):
            return                     # an older picture arriving late (a push that crossed an action's own answer)
        was = ((r.report or {}).get("courier") or {}).get("running_seconds") if r.report else None
        now_up = (rep.get("courier") or {}).get("running_seconds")
        if isinstance(was, (int, float)) and isinstance(now_up, (int, float)) and now_up + 5 < was:
            r.announced = False            # the Courier restarted between two of our looks (an update): that is a coming-online too, so what was running there starts again
        r.report = rep
        r.report_at = time.time()
        r.models = models_from_report(rep)
        r.program = courier_program_label(rep)
        if not r.name_typed:
            r.name = (rep.get("courier") or {}).get("name") or r.name
        r.instance = (rep.get("courier") or {}).get("id") or r.instance
        g = (rep.get("gpus") or [None])[0]
        r.gpu = {"name": g["name"], "vendor": g.get("vendor"), "memoryGb": round(g["memory_total_mb"] / 1024, 1) if g.get("memory_total_mb") else None} if g else None
        self.mark(r, True)
        for hook in list(self.report_hooks):
            try:
                hook(r)
            except Exception:
                self.log.exception("A hook for a Courier's report failed")
        if not r.announced:
            r.announced = True
            for hook in list(self.online_hooks):
                try:
                    hook(r)
                except Exception:
                    self.log.exception("A hook for a Courier coming online failed")

    def _courier(self, rid: str) -> Remote:
        self._load()
        r = self._remotes.get((rid or "").lower())
        if not r:
            raise OtherPcsError(404, "That device was removed.")
        if r.kind != "courier" or r.client is None:
            raise OtherPcsError(400, f"{r.label} doesn't have MultiProxy Courier installed. Install it there first.")
        return r

    async def courier_action(self, rid: str, program: str, action: str, model: str = "") -> None:
        r = self._courier(rid)
        await r.client.action(program, action, model)
        try:
            self._apply_report(r, await r.client.fetch_report())      # show the result now, not at the next push
        except OtherPcsError:
            pass

    async def courier_claude(self, rid: str, action: str, body: Optional[dict] = None) -> dict:
        """One thing the player asked of Claude on that device (install, update, cancel, signin, signout, test). The sign-in code only passes through here: it is
        sent on to that device at once and never kept, logged or returned."""
        r = self._courier(rid)
        timeout = {"signin": 120, "test": 120, "signin-start": 60, "signin-code": 170}.get(action, 30)
        result = await r.client.claude_post(action.replace("-", "/", 1) if action.startswith("signin-") else action, body or {}, timeout=timeout)
        try:
            self._apply_report(r, await r.client.fetch_report())
        except OtherPcsError:
            pass
        return result

    async def courier_copilot(self, rid: str, action: str) -> dict:
        """One thing the player asked of Copilot on that device (install, update, cancel, signin, cancelsignin, signout, test, uninstall). Nothing secret passes through here:
        the sign-in code that comes back is a one-time code for the player to type at GitHub."""
        r = self._courier(rid)
        result = await r.client.copilot_post(action, {}, timeout=120 if action in ("signout", "test", "uninstall") else 30)
        try:
            self._apply_report(r, await r.client.fetch_report())
        except OtherPcsError:
            pass
        return result

    async def courier_settings(self, rid: str, **changes) -> None:
        r = self._courier(rid)
        await r.client.settings(**changes)
        try:
            self._apply_report(r, await r.client.fetch_report())
        except OtherPcsError:
            pass

    async def courier_engine(self, rid: str) -> dict:
        """The model engine (llama.cpp) on that device as its Courier reports it: kind, build, whether a newer build exists, the faster versions its card is offered."""
        return await self._courier(rid).client.engine_get()

    async def courier_engine_post(self, rid: str, body: dict) -> dict:
        """Update the engine or switch its version on that device: only ever called after the player pressed the button."""
        r = self._courier(rid)
        result = await r.client.engine_post(body)
        try:
            self._apply_report(r, await r.client.fetch_report())
        except OtherPcsError:
            pass
        return result

    async def courier_find_models(self, rid: str, folder: str = "") -> dict:
        return await self._courier(rid).client.find_models(folder)

    async def courier_scan(self, rid: str, action: str) -> dict:
        return await self._courier(rid).client.scan(action)

    async def courier_drives(self, rid: str) -> dict:
        return await self._courier(rid).client.drives()

    async def courier_add_models(self, rid: str, paths: list) -> dict:
        r = self._courier(rid)
        result = await r.client.add_models(paths)
        try:
            self._apply_report(r, await r.client.fetch_report())      # the new models show now, not at the next push
        except OtherPcsError:
            pass
        return result

    async def courier_set_models_folder(self, rid: str, path: str) -> dict:
        r = self._courier(rid)
        result = await r.client.set_models_folder(path)
        try:
            self._apply_report(r, await r.client.fetch_report())
        except OtherPcsError:
            pass
        return result

    async def courier_log(self, rid: str) -> list:
        """A Courier device's recent log, for the Log button. An offline device says so plainly instead."""
        r = self._courier(rid)
        if r.online is False:
            raise OtherPcsError(502, log_offline_text(r.id))
        try:
            return await r.client.log()
        except OtherPcsError as e:
            raise OtherPcsError(502, log_offline_text(r.id)) if e.status == 502 else e

    async def courier_new_code(self, rid: str) -> None:
        """A new pairing code on that device; this device keeps the new one (the old one stops working at once)."""
        r = self._courier(rid)
        r.code = await r.client.new_pairing_code()
        self._persist()
        self._ensure_client(r)

    async def set_code(self, rid: str, code: str) -> None:
        """Give a saved device the new pairing code it now shows. The code is tried on that device first, so a wrong one is never saved;
        the device keeps its id, name, address and instance id. The code is never logged or echoed."""
        self._load()
        r = self._remotes.get((rid or "").lower())
        if not r:
            raise OtherPcsError(404, "That device was removed.")
        if r.kind not in ("courier", "multiproxy"):
            raise OtherPcsError(400, f"{r.label} doesn't use a pairing code.")
        if not normalize_code(code):
            raise OtherPcsError(400, "Type the pairing code shown on the other device.")
        probe = Remote(id="", kind=r.kind, name=r.name, base=r.base, code=code)
        await self._poll(probe, timeout=PAIR_TIMEOUT)
        if probe.online is not True:
            if probe.error in CODE_CHANGED_ERRORS:
                raise OtherPcsError(403, "Wrong pairing code.")
            raise OtherPcsError(502, probe.error or "Couldn't reach that device.")
        r.code = code
        self._persist()
        self.log.info(f"The pairing code for '{r.label}' was updated")
        self._ensure_client(r)
        await self._poll(r)

    # ---- checking whether they're up -------------------------------------------------------------
    async def _poll(self, r: Remote, timeout: float = POLL_TIMEOUT) -> None:
        was = r.online
        await self._poll_inner(r, timeout)
        r.note_state()
        if r.online is False and was is not False:
            r.retry_at = time.time() + RETRY_AFTER_SECONDS          # it just went quiet: the backup covers it for a while
        elif r.online:
            r.retry_at = 0.0

    async def _poll_inner(self, r: Remote, timeout: float = POLL_TIMEOUT) -> None:
        r.checked_at = time.time()
        if r.kind == "courier" and r.client is not None and r.client.online:
            return                                         # its live connection is the check: it marks the device offline the moment it drops
        try:
            await asyncio.to_thread(check_host_allowed, urlsplit(r.base).hostname or "", allow_loopback=(r.kind == "openai"))
        except OtherPcsError as e:
            r.online, r.error = False, e.message
            return
        t = aiohttp.ClientTimeout(total=timeout)
        try:
            async with self._session_factory() as session:
                if r.kind in ("multiproxy", "courier"):
                    async with session.get(r.base + "/share/info", headers=r.headers(), timeout=t, allow_redirects=False) as resp:
                        if 300 <= resp.status < 400:
                            r.online, r.error = False, COULDNT_CONNECT
                            return
                        if resp.status in (401, 403):
                            r.online, r.error = False, "That code doesn't match. Check the code shown on the other device."
                            return
                        if resp.status == 429:
                            r.online, r.error = False, CODE_RATE_LIMITED
                            return
                        if resp.status != 200:
                            r.online, r.error = False, f"That device answered with an error ({resp.status})."
                            return
                        data = await resp.json(content_type=None)
                    if data.get("product") != "multiproxy":
                        r.online, r.error = False, "That isn't a MultiProxy model server."
                        return
                    if not r.name_typed:
                        r.name = data.get("name") or r.name
                    r.instance = str(data.get("instance") or r.instance or "")
                    if data.get("kind") == "courier" and r.kind == "multiproxy":
                        r.kind = "courier"                 # paired before the Courier screens existed: it is a Courier, so manage it as one
                        r.program = ""
                        if r.id in self._remotes:           # (not while it's only a pairing probe)
                            self._persist()
                            self._ensure_client(r)
                    r.models = [m for m in (data.get("models") or []) if isinstance(m, dict) and m.get("id")]
                    r.gpu = data.get("gpu")
                    if r.kind == "multiproxy":
                        r.program = "MultiProxy"
                else:
                    async with session.get(r.api_base + "/models", headers=r.headers(), timeout=t, allow_redirects=False) as resp:
                        if 300 <= resp.status < 400:
                            r.online, r.error = False, COULDNT_CONNECT
                            return
                        if resp.status in (401, 403):
                            r.online, r.error = False, "That AI server wants a key. Add it and try again."
                            return
                        if resp.status != 200:
                            r.online, r.error = False, f"That AI server answered with an error ({resp.status}). Is the address right?"
                            return
                        data = await resp.json(content_type=None)
                    rows = data.get("data") if isinstance(data, dict) else None
                    if rows is None and isinstance(data, list):
                        rows = data
                    r.models = [{"id": str(m.get("id") or m.get("name")), "name": str(m.get("id") or m.get("name")),
                                 "vision": False, "state": "running"} for m in (rows or []) if isinstance(m, dict) and (m.get("id") or m.get("name"))]
                    if not r.program or r.program == "AI server":
                        r.program = await self._detect_program(session, r, rows)
            r.online, r.error = True, ""
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            r.online = False
            r.error = ("Couldn't reach that device. Is MultiProxy running there with sharing turned on? "
                       "Windows may also need to allow it through its firewall." if r.kind == "multiproxy"
                       else "Couldn't reach that AI server. Is it running, and is the address right?")

    async def _detect_program(self, session: aiohttp.ClientSession, r: Remote, rows) -> str:
        """Which program an AI server added by address is: Ollama, LM Studio and llama.cpp each answer a route of their own.
        Anything else (LiteLLM, NVIDIA NIM, vLLM...) is just "AI server"."""
        root = r.base.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        t = aiohttp.ClientTimeout(total=1.5)
        for label, path, check in (("Ollama", "/api/version", lambda d: isinstance(d, dict) and "version" in d),
                                   ("LM Studio", "/api/v0/models", lambda d: isinstance(d, dict) and "data" in d),
                                   ("llama.cpp", "/props", lambda d: isinstance(d, dict))):
            try:
                async with session.get(root + path, headers=r.headers(), timeout=t) as resp:
                    if resp.status == 200 and check(await resp.json(content_type=None)):
                        return label
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
                continue
        owner = str(((rows or [{}])[0] or {}).get("owned_by", "")).lower() if rows else ""
        return "llama.cpp" if "llamacpp" in owner else "AI server"

    async def poll_all(self) -> None:
        rs = self.all()
        if rs:
            await asyncio.gather(*(self._poll(r) for r in rs), return_exceptions=True)
            await self.follow_moved(rs)

    async def follow_moved(self, rs: Optional[list] = None, scan: Optional[Callable[..., list]] = None) -> int:
        """A paired device that went offline may just have been given a new address by the home router. Its permanent id
        (`instance`) is how it's recognised: look for it on the network and, if it's found somewhere else, follow it."""
        lost = [r for r in (rs if rs is not None else self.all()) if r.kind in ("multiproxy", "courier") and r.online is False and r.instance]
        if not lost:
            return 0
        try:
            found = await asyncio.to_thread(scan or scan_network)
        except Exception:
            return 0
        by_instance = {f.get("instance"): f for f in found if f.get("instance")}
        moved = 0
        for r in lost:
            f = by_instance.get(r.instance)
            if f and f["address"] != r.base:
                self.log.info(f"'{r.label}' has a new address ({f['address']}); following it.")
                r.base = f["address"]
                self._ensure_client(r)
                await self._poll(r)
                moved += 1
        if moved:
            self._persist()
        return moved

    def mark(self, r: Remote, online: bool, error: str = "") -> None:
        if not online and r.online is not False:
            r.retry_at = time.time() + RETRY_AFTER_SECONDS
        elif online:
            r.retry_at = 0.0
        r.online = online
        r.error = error if not online else ""
        r.checked_at = time.time()
        r.note_state()

    def start_polling(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return

        for r in self.all():
            self._ensure_client(r)

        async def loop():
            while True:
                try:
                    await self.poll_all()
                except Exception:
                    self.log.exception("Checking other devices failed")
                await asyncio.sleep(POLL_SECONDS)
        self._poll_task = asyncio.create_task(loop())

    async def stop_polling(self) -> None:
        for r in self.all():
            c, r.client = r.client, None
            if c is not None:
                await c.stop()
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None

    async def memory(self, r: Remote) -> dict:
        """The graphics-memory picture of another device, for the Local models tab's picker.
        `state`: ok (with the figures) / own (your own AI server: nothing to show) / offline / older (a
        MultiProxy without the memory route yet)."""
        if r.kind == "openai":
            return {"state": "own", "name": r.label}
        if r.online is False:
            return {"state": "offline", "name": r.label}
        try:
            async with self._session_factory() as session:
                async with session.get(r.base + "/share/memory", headers=r.headers(), timeout=aiohttp.ClientTimeout(total=POLL_TIMEOUT), allow_redirects=False) as resp:
                    if 300 <= resp.status < 400:
                        return {"state": "offline", "name": r.label}
                    if resp.status == 404:
                        return {"state": "older", "name": r.label}
                    if resp.status != 200:
                        return {"state": "offline", "name": r.label}
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            return {"state": "offline", "name": r.label}
        if (not isinstance(data, dict) or not data.get("available")) and r.kind == "courier":
            data = self._memory_from_report(r)                # the Courier's own monitor can't read it: the card's own numbers will do
        if not isinstance(data, dict) or not data.get("available"):
            return {"state": "older", "name": r.label}
        return {"state": "ok", "name": r.label, **data}

    @staticmethod
    def _memory_from_report(r: Remote) -> dict:
        g = next((g for g in ((r.report or {}).get("gpus") or []) if g.get("memory_total_mb") and g.get("memory_used_mb") is not None), None)
        if not g:
            return {"available": False}
        total, used = int(g["memory_total_mb"]), int(g["memory_used_mb"])
        return {"available": True, "card": g["name"], "totalMb": total, "usedMb": used, "segments": [], "otherMb": used,
                "freeMb": max(0, total - used), "skyrimShown": False, "skyrimRunning": False, "peakMb": None, "peakWhen": ""}

    def online_model_names(self) -> list[tuple[str, str, bool]]:
        """(`nickname/model`, display name, sees pictures) for every model that's ready on an online server."""
        out = []
        for r in self.all():
            if r.online:
                out.extend((f"{r.id}/{m['id']}", m.get("name") or m["id"], bool(m.get("vision"))) for m in r.ready_models())
        return out


# --------------------------------------------------------------------------------------------
# Finding model servers on the home network (UDP)
# --------------------------------------------------------------------------------------------
def _broadcast_targets() -> list[str]:
    targets = ["255.255.255.255"]
    for ip in local_private_ips():
        targets.append(".".join(ip.split(".")[:3] + ["255"]))
    return sorted(set(targets))


SCAN_ASK_AGAIN_SECONDS = 0.5


def scan_network(timeout: float = 1.5, port: int = DISCOVERY_PORT, own_instance: str = "",
                 targets: Optional[list] = None) -> list[dict]:
    """Ask the home network "who's a MultiProxy model server?" and collect answers for `timeout`
    seconds. Blocking: call it with asyncio.to_thread."""
    found: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        tgts = targets if targets is not None else _broadcast_targets()

        def ask() -> None:
            for t in tgts:
                try:
                    sock.sendto(DISCOVERY_ASK.encode(), (t, port))
                except OSError:
                    pass
        ask()
        end = time.monotonic() + timeout
        next_ask = time.monotonic() + SCAN_ASK_AGAIN_SECONDS
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            if time.monotonic() >= next_ask:               # one datagram can be lost (Wi-Fi, a busy device): ask again while we wait
                ask()
                next_ask = time.monotonic() + SCAN_ASK_AGAIN_SECONDS
            sock.settimeout(max(0.05, min(left, next_ask - time.monotonic())))
            try:
                data, (ip, _p) = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if ip_class(ip) not in ("private", "loopback"):
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if not isinstance(msg, dict) or msg.get("magic") != DISCOVERY_ANSWER or msg.get("instance") == own_instance:
                continue
            found[msg.get("instance") or ip] = {
                "name": str(msg.get("name") or ip), "address": f"http://{ip}:{int(msg.get('port') or SHARE_PORT_DEFAULT)}",
                "host": ip, "gpu": msg.get("gpu"), "models": msg.get("models") or [], "instance": msg.get("instance"),
                "courier": msg.get("courier"), "programs": msg.get("programs") or [],
            }
    finally:
        sock.close()
    return sorted(found.values(), key=lambda f: f["name"].lower())


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, answer: Callable[[], dict]):
        self.answer = answer
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() != DISCOVERY_ASK.encode() or ip_class(addr[0]) not in ("private", "loopback"):
            return
        try:
            self.transport.sendto(json.dumps(self.answer(addr[0])).encode("utf-8"), addr)
        except Exception:
            pass


# --------------------------------------------------------------------------------------------
# The model-server door (this device, when sharing is on)
# --------------------------------------------------------------------------------------------
BLOCKED_PARAMS = {"thinking", "reasoning", "reasoning_effort"}


def _err(status: int, message: str, **kw) -> web.Response:
    return web.json_response({"error": {"message": message, "type": "api_error", "code": None}}, status=status, **kw)


class ShareManager:
    """Owns the model-server door and the "I'm here" reply. `local_svc` is part 1's LocalModels."""

    def __init__(self, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], log, local_svc,
                 gpu_getter: Callable[[], Any], version: str = "", discovery_port: int = DISCOVERY_PORT,
                 memory_fn: Optional[Callable[[], Any]] = None):
        self._load_cfg, self._save_cfg, self.log = load_cfg, save_cfg, log
        self.local = local_svc
        self._gpu_getter = gpu_getter
        self.version = version
        self.discovery_port = discovery_port
        self._memory_fn = memory_fn          # async: this device's graphics-memory figures (no Skyrim), or None
        self._runner: Optional[web.AppRunner] = None
        self._transport = None
        self.addresses: list[str] = []
        self.error: str = ""
        self.last_seen: float = 0.0
        self.last_seen_ip: str = ""
        self.last_chat: float = 0.0
        self._bad: dict[str, deque] = {}
        self._gpu_json: Optional[dict] = None

    # ---- config ------------------------------------------------------------------------------
    def _cfg(self) -> dict:
        return dict(self._load_cfg().get("share") or {})

    def _update(self, **changes) -> None:
        cfg = self._load_cfg()
        sh = dict(cfg.get("share") or {})
        sh.update(changes)
        cfg["share"] = sh
        self._save_cfg(cfg)

    @property
    def enabled(self) -> bool:
        return bool(self._cfg().get("enabled"))

    @property
    def port(self) -> int:
        try:
            return int(self._cfg().get("port") or SHARE_PORT_DEFAULT)
        except (TypeError, ValueError):
            return SHARE_PORT_DEFAULT

    @property
    def code(self) -> str:
        return self._cfg().get("code") or ""

    @property
    def instance(self) -> str:
        inst = self._cfg().get("instance")
        if not inst:
            inst = uuid.uuid4().hex
            self._update(instance=inst)
        return inst

    def ensure_code(self) -> str:
        if not self.code:
            self._update(code=new_pairing_code())
        return self.code

    def new_code(self) -> str:
        self._update(code=new_pairing_code())
        self._bad.clear()
        return self.code

    @property
    def running(self) -> bool:
        return self._runner is not None

    # ---- on / off ------------------------------------------------------------------------------
    async def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.ensure_code()
        self._update(enabled=bool(enabled))
        await self.apply()

    async def apply(self) -> None:
        """Make reality match the saved setting: start the door if sharing is on, stop it if it's off."""
        if self.enabled and not self.running:
            await self._start()
        elif not self.enabled and self.running:
            await self.stop()

    async def _start(self) -> None:
        self.error = ""
        self.ensure_code()
        self.instance                                   # make sure it exists
        ips = await asyncio.to_thread(local_private_ips)
        if not ips:
            self.error = "Couldn't find this device's home-network address. Is it connected to your router?"
            return
        app = web.Application(client_max_size=128 * 1024 * 1024)
        app.router.add_get("/share/info", self._h_info)
        app.router.add_get("/share/memory", self._h_memory)
        app.router.add_post("/share/v1/chat/completions", self._h_chat)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        bound, problems = [], []
        for ip in ips:
            try:
                await web.TCPSite(runner, ip, self.port).start()
                bound.append(ip)
            except OSError as e:
                problems.append(f"{ip}:{self.port} ({e.strerror or e})")
        if not bound:
            await runner.cleanup()
            self.error = "Couldn't open the sharing port: " + "; ".join(problems)
            return
        self._runner = runner
        self.addresses = [f"{ip}:{self.port}" for ip in bound]
        try:
            loop = asyncio.get_running_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.discovery_port))
            self._transport, _ = await loop.create_datagram_endpoint(lambda: _DiscoveryProtocol(self._discovery_answer), sock=sock)
        except OSError as e:
            self.log.warning(f"Other devices can't find this one automatically ({e.strerror or e}); they can still add it by address.")
        self.log.info(f"Sharing local models with devices on your home network at {', '.join(self.addresses)}")

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self.addresses = []
        self.log.info("Stopped sharing local models")

    # ---- what the other device sees ------------------------------------------------------------------
    def _gpu_summary(self) -> Optional[dict]:
        try:
            g = self._gpu_getter()
        except Exception:
            g = None
        return g.to_json() if g else None

    def _models(self) -> list[dict]:
        out = []
        for m in self.local.installed():
            state, _ = self.local.state_of(m.id)
            out.append({"id": m.id, "name": m.name, "vision": m.vision, "state": state})
        return out

    def _discovery_answer(self, host: str = "") -> dict:
        return {"magic": DISCOVERY_ANSWER, "name": short_hostname(), "instance": self.instance, "port": self.port,
                "gpu": self._gpu_summary(),
                "models": [m["name"] for m in self._models() if m["state"] in ("running", "starting")]}

    # ---- the door ----------------------------------------------------------------------------------
    def _check(self, request: web.Request) -> Optional[web.Response]:
        ip = request.remote or ""
        if ip_class(ip) not in ("private", "loopback"):
            return _err(403, "This only answers devices on your home network.")
        window = self._bad.setdefault(ip, deque())
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= MAX_BAD_CODES:
            return _err(429, "Too many wrong codes. Wait a minute and try again.", headers={"Retry-After": "60"})
        if not codes_match(request.headers.get(CODE_HEADER, ""), self.code):
            window.append(now)
            return _err(403, "Wrong pairing code.")
        self.last_seen, self.last_seen_ip = time.time(), ip
        return None

    async def _h_info(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied:
            return denied
        return web.json_response({"product": "multiproxy", "name": short_hostname(), "instance": self.instance,
                                  "version": self.version, "gpu": self._gpu_summary(), "models": self._models()})

    async def _h_memory(self, request: web.Request) -> web.Response:
        """Read-only: this device's graphics card, total, per-model and everything-else figures. Same code
        check and home-network rule as the rest of the door; nothing else about this device is exposed."""
        denied = self._check(request)
        if denied:
            return denied
        if self._memory_fn is None:
            return _err(404, "Couldn't check the graphics memory on this device.")
        data = await self._memory_fn()
        if not data or not data.get("available"):
            return web.json_response({"available": False})
        return web.json_response(data)

    async def _h_chat(self, request: web.Request) -> web.StreamResponse:
        denied = self._check(request)
        if denied:
            return denied
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "That wasn't a valid request.")
        if not isinstance(body, dict):
            return _err(400, "That wasn't a valid request.")
        want = str(body.get("model") or "")
        try:
            inst = self.local.find(want if want.lower().startswith(LOCAL_PREFIX) else LOCAL_PREFIX + want)
            if not inst:
                return _err(404, f"This device doesn't have a model called '{want}'.")
            base = await self.local.ensure_ready(inst.id)
        except LocalModelError as e:
            return _err(e.status, e.message)
        self.last_chat = time.time()
        body["model"] = inst.id
        for k in BLOCKED_PARAMS:
            body.pop(k, None)
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=600))
        try:
            async with session.post(base + "/chat/completions", json=body) as up:
                ctype = up.headers.get("Content-Type", "application/json")
                if not body.get("stream") or up.status != 200:
                    data = await up.read()
                    return web.Response(status=up.status, body=data, content_type=ctype.split(";")[0], charset="utf-8")
                resp = web.StreamResponse(status=200, headers={"Content-Type": ctype, "Cache-Control": "no-cache"})
                await resp.prepare(request)
                async for chunk in up.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"The model on this device didn't answer: {e}")
        finally:
            await session.close()

    # ---- what the dashboard shows ---------------------------------------------------------------------
    def state(self) -> dict:
        now = time.time()
        connected = self.running and self.last_seen and now - self.last_seen < POLL_SECONDS * 3
        return {
            "enabled": self.enabled, "running": self.running, "port": self.port,
            "code": self.code if self.enabled else "", "addresses": self.addresses,
            "error": self.error, "name": short_hostname(),
            "connected": bool(connected), "connectedFrom": self.last_seen_ip if connected else "",
            "lastUsedSecondsAgo": int(now - self.last_chat) if self.last_chat else None,
        }
