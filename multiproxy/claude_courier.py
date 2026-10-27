"""Claude on a Courier device (2026-10-05): install, sign in, run and answer for the main device. One module, written for the Courier (it imports nothing from MultiProxy).

What it does on this device, all behind the pairing code and the home-network rule like everything else the Courier answers:

  * **Install** Anthropic's own program the way their installer does, minus the part that changes the device: it fetches the version number, the checksum list and
    `claude.exe` from Anthropic's release site, checks the size, the SHA-256 and (Windows) the signature, and keeps ONE file in Courier's own folder. No administrator,
    no windows, nothing outside that folder, no automatic updates (the player presses Update). Evidence and the alternatives: `docs/plans/2026-10-05-claude-on-another-pc.md`.
  * **Sign in** with the one-year code `claude setup-token` prints. The code is kept only in this device's Courier folder, is never logged, shown or sent anywhere, and is
    given to the program as `CLAUDE_CODE_OAUTH_TOKEN` in that program's own environment only. Everything that could override it (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
    the cloud-provider switches, a profile) is removed from that environment, and the program gets a configuration folder of its own, so a Claude the player already uses
    on this device is not read or touched.
  * **Answer** like the main device's own Claude does (multiproxy.py `capture_auth`): run the program once through a loopback interceptor to keep the request headers and a body
    template, then talk to Anthropic's servers directly and turn the stream into OpenAI-style chunks. A refused code, an expired code, a limit or a billing error each
    end in ONE short line; a limit or billing error is passed on exactly as Anthropic says it (the same thing the main device's own Claude shows).

The secret rules this module keeps (and `tests/test_claude_courier.py` scans for): the code never appears in a log line, a status, an error line or a response; `mask()` is applied to
everything that leaves this module as text.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp
from aiohttp import web

import hidden_process
import https_only
import program_uninstall
import terminal_process
from urllib.parse import parse_qs, urlsplit

RELEASES_URL = "https://downloads.claude.ai/claude-code-releases"
API_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-5-5"           # the same default MultiProxy uses (multiproxy.DEFAULT_MODEL)
LATEST_CACHE_SECONDS = 3600.0                 # "is there a newer one?" is asked at most this often (and when the player asks)
MODELS_CACHE_SECONDS = 600.0
CAPTURE_TIMEOUT = 60.0
VERSION_TIMEOUT = 30.0
RECAPTURE_COOLDOWN = 30.0                     # after a failed capture, the same failure is reused for this long (as AuthCache does)
# Who signs the Windows program. Anthropic's own page says so: https://code.claude.com/docs/en/setup, "Binary integrity and code signing" > "Platform code signatures":
# "Windows: signed by "Anthropic, PBC". Verify with `Get-AuthenticodeSignature .\claude.exe`." (read 2026-10-06). The page gives no thumbprint, so none is pinned; this is the
# certificate's simple name (what Windows shows as the signer), compared exactly. If the real file shows another name, the director sees "The download isn't signed by
# Anthropic, so it was deleted." and this one constant is what to change.
EXPECTED_SIGNER = "Anthropic, PBC"
SIGNATURE_TIMEOUT = 60
# The path reaches PowerShell in an environment variable set for that one child process, never inside the command text.
SIGNATURE_ENV = "MULTIPROXY_FILE_TO_CHECK"
SIGNATURE_SCRIPT = ("$ErrorActionPreference = 'Stop'; $s = Get-AuthenticodeSignature -LiteralPath $env:" + SIGNATURE_ENV + "; $c = $s.SignerCertificate; "
                    "if ($null -eq $c) { @{ status = [string]$s.Status; signer = '' } | ConvertTo-Json -Compress } else { "
                    "@{ status = [string]$s.Status; signer = $c.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } | ConvertTo-Json -Compress }")
TOKEN_MIN_CHARS = 100                         # a real code is 108 characters; a copy cut at the terminal's width is 79
TOKEN_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]+$")
SECRET_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")

# The one short line for each thing that can go wrong (the design side runs the wording through Gemini; `docs` quote them).
# A backquoted word is a command: the dashboard shows it in code style. `<PC>` is the other device's name: the dashboard fills it in when it shows the line (claude_ui.ccWarn).
LINE_REFUSED = "The sign-in code was refused. Make a new one with `claude setup-token` and paste it again."
LINE_EXPIRED = "The sign-in code has expired. Make a new one with `claude setup-token` and paste it again."
LINE_CUT_OFF = "That sign-in code looks cut off. Copy all of it again, even if it runs over two lines."
LINE_NOT_A_CODE = "That doesn't look like a sign-in code. Run `claude setup-token`, approve the sign-in and paste what the terminal shows."
LINE_API_KEY = "That is an API key, not a sign-in code. Run `claude setup-token` and paste what the terminal shows."
LINE_NOT_INSTALLED = "Claude isn't installed on <PC> yet."
LINE_NEEDS_SIGN_IN = "Claude on <PC> needs a sign-in."
LINE_DOWNLOAD = "Couldn't download Claude. Check that <PC> is online, then try again."
LINE_CHECKSUM = "The download failed a security check, so it was deleted. Try again."
LINE_SIGNATURE = "The download didn't pass Anthropic's security check, so it was deleted."
LINE_WONT_START = "Claude was downloaded but won't start on <PC>."          # (the reason, from what the program printed, comes from `explain_start_failure`; antivirus is only ever named on Windows when it printed nothing)
LINE_BUSY = "Claude is busy on <PC> right now. Try again in a moment."
LINE_UNREACHABLE = "Claude on <PC> couldn't reach Anthropic."

# Variables that would send the program somewhere else, or make it use another login (the order is in Anthropic's authentication page).
_STRIP_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "CLAUDE_CODE_OAUTH")

# ---- Signing in from the main device (2026-10-06): this device runs `claude setup-token` itself on a hidden terminal and relays the sign-in link and the one-time code.
# Research and the real program's behaviour: docs/plans/2026-10-06-claude-signin-from-the-gaming-pc.md.
SIGNIN_LINK_WAIT = 45.0                       # the program should print its link within this long (it starts, then draws its sign-in screen)
SIGNIN_TOTAL = 15 * 60.0                      # a sign-in nobody finishes is stopped after this long (the link's own life is not documented)
SIGNIN_CODE_WAIT = 45.0                       # after the code is typed, the program prints the long code (or says nothing at all when it was wrong)
SIGNIN_QUIET_KEEP = 2.0                       # a whole code has been seen and nothing more was printed for this long (the program waits for a key, or never ends): keep it and stop the program
SIGNIN_DIE_GRACE = 3.0                        # after the long code is read, the program is given this long to end by itself before it is stopped
# The only addresses a sign-in link may point at (Anthropic's own sign-in pages). Anything else is never sent to a browser.
LINK_HOSTS = frozenset({"claude.com", "www.claude.com", "claude.ai", "www.claude.ai", "platform.claude.com", "console.anthropic.com", "anthropic.com", "www.anthropic.com"})
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]")
CURSOR_JUMP_RE = re.compile(r"\x1b\[\d*(?:;\d*)?[Hf]|\x1b\[\d*[BEd]")      # the terminal moved to another line: a line break for our purposes
OSC8_RE = re.compile(r"\x1b\]8;[^;\x07\x1b]*;([^\x07\x1b]*)(?:\x07|\x1b\\)")
URL_TEXT_RE = re.compile(r"https://[^\s\x1b\x07\"'<>]+")
TOKEN_START_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")
TOKEN_CHARS_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
ENTER_PROMPT_RE = re.compile(r"press\s+(enter|return|any key)\b[^?\[\]]{0,40}$", re.I)      # "Press Enter to continue": answered once. A question (ends in ?) or a choice ([y/N]) never is
CODE_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{8,2000}$")

LINE_NO_TERMINAL = "You can't sign in to Claude from <PC>. Use the other way."
LINE_NO_LINK = "We couldn't get a sign-in link from Claude on <PC>. Try again, or use the other way."
LINE_BAD_LINK = "Claude on <PC> gave a sign-in link that didn't come from Anthropic, so it was blocked. Use the other way."
LINE_LINK_RAN_OUT = "The sign-in took too long and was stopped. Press Sign in to start again."
LINE_CODE_NOT_ACCEPTED = "Claude didn't accept that code. Press Sign in to get a new link."
LINE_CODE_OTHER_LINK = "That code is from a different sign-in. Open the page again from this row, or press Cancel and Sign in to start again."
LINE_NO_CODE_YET = "There's no sign-in waiting. Press Sign in first."
LINE_SIGNIN_ENDED = "The sign-in didn't finish. Press Sign in to try again."


class ClaudeError(Exception):
    """A plain, player-readable reason, with the HTTP status the Courier answers with."""

    def __init__(self, status: int, message: str, kind: str = "error"):
        super().__init__(message)
        self.status, self.message, self.kind = status, message, kind


def mask(text: Any, token: str = "") -> str:
    """`text` with the code (and anything shaped like an Anthropic token) hidden. Applied to everything that leaves this module as text."""
    s = str(text)
    if token:
        s = s.replace(token, "[code]")
    return SECRET_RE.sub("[code]", s)


def clean_token(raw: str) -> str:
    """The pasted text with every space and line break taken out: a code that wrapped over two lines in the terminal is one code again."""
    return re.sub(r"\s+", "", raw or "")


def check_token(raw: str) -> str:
    """The code ready to save, or a ClaudeError that says what is wrong. Catches the real trap first: a copy cut at the terminal's width."""
    t = clean_token(raw)
    if not t:
        raise ClaudeError(400, LINE_NOT_A_CODE, "bad_code")
    if t.startswith("sk-ant-api"):
        raise ClaudeError(400, LINE_API_KEY, "bad_code")
    if not t.startswith("sk-ant-") or not TOKEN_RE.match(t):
        raise ClaudeError(400, LINE_NOT_A_CODE, "bad_code")
    if len(t) < TOKEN_MIN_CHARS:
        raise ClaudeError(400, LINE_CUT_OFF, "cut_off")
    return t


def strip_terminal(text: str) -> str:
    """What the program printed, as plain lines: colour and cursor codes removed (a jump to another line becomes a line break), \r\n and \r made \n."""
    text = OSC8_RE.sub("", text)
    text = CURSOR_JUMP_RE.sub("\n", text)
    text = ANSI_RE.sub("", text)
    return re.sub(r"\r+\n", "\n", text).replace("\r", "\n")          # (a Linux terminal turns each \r\n the program prints into \r\r\n)


def link_allowed(url: str) -> bool:
    """Is this Anthropic's own sign-in page? https only, a host from `LINK_HOSTS`, no name and password in it, no odd port."""
    try:
        u = urlsplit(url)
        return u.scheme == "https" and (u.hostname or "") in LINK_HOSTS and not u.username and not u.password and u.port in (None, 443)
    except ValueError:
        return False


def find_link(raw: str) -> str:
    """The sign-in link in what the program printed. The hyperlink the program attaches to it (OSC 8) is exact, so it comes first; otherwise the printed text, joined
    over the lines a narrow terminal broke it into (a line with a space in it is not part of a link). '' while there is none yet."""
    for m in OSC8_RE.finditer(raw):
        if m.group(1).startswith("https://"):
            return m.group(1)
    lines = strip_terminal(raw).split("\n")
    for i, line in enumerate(lines):
        m = URL_TEXT_RE.search(line)
        if m:
            url = m.group(0)
            if m.end() == len(line.rstrip()):                    # the line ends with the link: it may go on over the next lines
                for nxt in lines[i + 1:]:
                    t = nxt.strip()
                    if not t or " " in t:
                        break
                    url += t
            return url
    return ""


def find_token(raw: str) -> tuple:
    """(the long code, whole?) from what the program printed. A code that the terminal broke over two lines is joined. `whole` is False while it could still be growing
    (nothing has been printed after it yet): the caller waits for the next line (or the program's end) before it trusts it."""
    lines = strip_terminal(raw).split("\n")
    for i, line in enumerate(lines):
        m = TOKEN_START_RE.search(line)
        if not m:
            continue
        if m.end() != len(line.rstrip()):                        # more text follows the code on the same line ("...token: sk-ant-... Store it safely"): the code ends where it ends
            return m.group(0), True
        token, j = m.group(0), i + 1
        while j < len(lines) and TOKEN_CHARS_RE.match(lines[j].strip() or " "):
            token += lines[j].strip()
            j += 1
        return token, any(x.strip() for x in lines[j:])
    return "", False


def shape_of(raw: str, last: int = 20) -> list:
    """What the program printed, as SHAPES only, for the log when a sign-in ends without a kept code: for each of the last lines its length and its characters collapsed
    (every run of letters is `a`, every run of digits `9`, spaces and punctuation kept, anything else `~`, cut at 80). A code or a link query is a run of letters and digits,
    so none of its content can be in here."""
    out = []
    for line in [x for x in strip_terminal(raw).split("\n") if x.strip()][-last:]:
        s, prev = [], ""
        for ch in line.rstrip():
            c = "a" if ch.isalpha() and ch.isascii() else "9" if ch.isdigit() and ch.isascii() else ch if (ch == " " or (32 < ord(ch) < 127)) else "~"
            if c in "a9" and prev == c:
                continue
            s.append(c)
            prev = c
        out.append(f"len={len(line.rstrip())} shape={''.join(s)[:80]}")
    return out


def link_state(url: str) -> str:
    """The `state` value in the link (the program's own check that a pasted code belongs to this sign-in), or ''."""
    try:
        return (parse_qs(urlsplit(url).query).get("state") or [""])[0]
    except ValueError:
        return ""


def link_for_log(url: str) -> str:
    """The link without its query (it is never logged whole)."""
    try:
        u = urlsplit(url)
        return f"{u.scheme}://{u.hostname}{u.path}"
    except ValueError:
        return "(a link)"


class Login:
    """One sign-in in progress on this device (`claude setup-token` on a hidden terminal)."""

    def __init__(self) -> None:
        self.state = "starting"                      # starting | link | checking | done | error | cancelled
        self.url = ""
        self.error = ""
        self.kind = ""
        self.started = time.monotonic()
        self.term = None
        self.task: Optional[asyncio.Task] = None
        self.link_ready = asyncio.Event()
        self.result: asyncio.Future = asyncio.get_running_loop().create_future()   # set when the sign-in finished: None, or the ClaudeError
        self.code_sent = 0.0
        self.link_state = ""
        self.browser = "unknown"                     # "opened": the program opens its sign-in page on this device itself | "none": nothing opens here (a headless device) | "unknown"
        self.outcome: Optional[Exception] = None     # how it ended (None = signed in); handed to whoever waits only after the program is stopped
        self.ended = False

    def active(self) -> bool:
        return self.state in ("starting", "link", "checking")

    def to_json(self) -> dict:
        return {"state": self.state, "url": self.url if self.state in ("link", "checking") else "", "error": self.error, "browser": self.browser}


VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?")


class StartCheck:
    """What happened when a downloaded program was started once with `--version`: the version (empty when it did not work), the exit code, what it printed (both streams),
    whether it timed out, and the operating-system error when it could not even be started."""

    def __init__(self, version: str = "", returncode: Optional[int] = None, output: str = "", timed_out: bool = False, error: Optional[OSError] = None):
        self.version, self.returncode, self.output, self.timed_out, self.error = version, returncode, output, timed_out, error


async def check_start(exe: Path, env: dict) -> StartCheck:
    """Start `exe --version` the way the Courier does and keep everything it says, so a failure can be explained from the program's own words."""
    try:
        proc = await hidden_process.create_subprocess_exec(str(exe), "--version", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                           stderr=asyncio.subprocess.PIPE, env=env)
    except OSError as e:
        return StartCheck(error=e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=VERSION_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except OSError:
            pass
        return StartCheck(timed_out=True)
    text = (out or b"").decode("utf-8", "replace") + "\n" + (err or b"").decode("utf-8", "replace")
    m = VERSION_RE.search(text)
    version = m.group(0).rstrip(".-+") if m else ""                 # ("GitHub Copilot CLI 1.0.92." ends its sentence with a full stop: that is not part of the number)
    return StartCheck(version if version and proc.returncode == 0 and is_version(version) else "", proc.returncode, text.strip())


# What a missing system library is called in each family of Linux (the one command the person needs). Only libraries a downloaded program is known to need.
_LIB_PACKAGES = {"libstdc++.so.6": ("libstdc++6", "libstdc++", "gcc-libs"), "libgcc_s.so.1": ("libgcc-s1", "libgcc", "gcc-libs"), "libatomic.so.1": ("libatomic1", "libatomic", "gcc-libs"),
                 "libz.so.1": ("zlib1g", "zlib", "zlib"), "libssl.so.3": ("libssl3", "openssl-libs", "openssl"), "libcrypto.so.3": ("libssl3", "openssl-libs", "openssl")}
_MISSING_LIB_RE = re.compile(r"error while loading shared libraries: (\S+?):", re.I)
_OLD_GLIBC_RE = re.compile(r"(GLIBC|GLIBCXX|CXXABI)_[0-9.]+' not found", re.I)
_WRONG_BUILD_RE = re.compile(r"exec format error|cannot execute binary file|bad cpu type|not a valid win32", re.I)


def explain_start_failure(name: str, folder: Any, check: "StartCheck", platform: Optional[str] = None) -> str:
    """One plain line for a downloaded program that would not start, from the program's OWN words first. The antivirus advice is only for Windows, and only when the program
    printed nothing at all. `<PC>` is the device's name, filled in by the page."""
    plat = platform or sys.platform
    text = check.output or ""
    err = check.error
    if check.timed_out:
        return f"{name} didn't start within {int(VERSION_TIMEOUT)} seconds on <PC>. Try again: the first start of a new program can be slow."
    m = _MISSING_LIB_RE.search(text)
    if m:
        lib = m.group(1)
        pk = _LIB_PACKAGES.get(lib)
        fix = (f" On Debian or Ubuntu: `sudo apt install {pk[0]}`. On Fedora: `sudo dnf install {pk[1]}`. On Arch: `sudo pacman -S {pk[2]}`." if pk else "")
        return f"{name} can't start on <PC>: a system library is missing ({lib}).{fix} Then press Install again."
    if _OLD_GLIBC_RE.search(text):
        return f"{name} needs a newer Linux than <PC> has (its system libraries are too old). Update the system, then press Install again."
    if _WRONG_BUILD_RE.search(text) or (err is not None and getattr(err, "errno", None) == 8):
        return f"This download of {name} is built for a different kind of computer than <PC>. Press Install again, or ask for help with this one."
    if err is not None and getattr(err, "errno", None) in (1, 13):
        return f"{name} isn't allowed to run from its folder on <PC>. The folder might be set to block programs."
    lines = [clean for clean in (re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", x).strip() for x in text.splitlines()) if clean]
    if lines:
        first = lines[0][:110]
        return f"{name} was downloaded but won't start on <PC>: {first}"
    if plat == "win32":
        return f"{name} was downloaded but won't start on <PC>, and printed nothing. Antivirus software might have blocked it. Allow the folder `{folder}`, then press Install again."
    code = f" (exit code {check.returncode})" if check.returncode is not None else ""
    return f"{name} was downloaded but didn't start on <PC>, and printed nothing{code}. The Courier's log has more."


def is_version(text: str) -> bool:
    """A whole version number (`2.1.211`, `2.2.0-beta.1`): nothing before it and nothing after it. The number goes into a download address, so a prefix match is not enough."""
    return bool(VERSION_RE.fullmatch(text or ""))


def version_tuple(text: str) -> tuple:
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else ()


def _powershell() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    own = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(own) if own.is_file() else "powershell"


def signature_ok(path, expected: str = EXPECTED_SIGNER) -> bool:
    """True only when Windows says the signature on `path` is valid AND the signer is exactly `expected`. Anything else, including any error, a timeout or an answer that
    can't be read, is "not trusted". PowerShell only reports what it sees; the decision is made here."""
    # PSModulePath is left out: a device where the Courier was started from PowerShell 7 hands Windows PowerShell 7's module folders, and it then can't load its own security module.
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    env[SIGNATURE_ENV] = str(path)
    try:
        r = hidden_process.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", SIGNATURE_SCRIPT], capture_output=True, text=True, timeout=SIGNATURE_TIMEOUT,
                               stdin=subprocess.DEVNULL, env=env)
        got = json.loads((r.stdout or "").strip())
        return r.returncode == 0 and got.get("status") == "Valid" and bool(expected) and got.get("signer") == expected
    except Exception:
        return False


def release_platform() -> str:
    """Anthropic's name for this device's kind, as in the release list (win32-x64, win32-arm64, linux-x64, darwin-arm64 ...)."""
    import platform as _p
    machine = (os.environ.get("PROCESSOR_ARCHITECTURE") or _p.machine() or "").lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "win32-arm64" if arm else "win32-x64"
    if sys.platform == "darwin":
        return "darwin-arm64" if arm else "darwin-x64"
    return "linux-arm64" if arm else "linux-x64"


def child_env(token: str, config_dir: Path, base_url: str = "", environ: Optional[dict] = None) -> dict:
    """The environment the program runs in: this device's own, with everything that could override the code or redirect the program removed, then ONLY what Claude needs added.
    The code is in this dictionary and nowhere else (never in this process's own environment)."""
    env = {k: v for k, v in (os.environ if environ is None else environ).items() if not k.upper().startswith(_STRIP_PREFIXES)}
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)             # its own folder: a Claude the player already has on this device is not read or touched
    env["DISABLE_AUTOUPDATER"] = "1"                       # no automatic updates; the player presses Update
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url               # only for the one capture run, pointing at the loopback interceptor
    return env


def flat_text(value: Any) -> str:
    """Whatever sits in a text part's `text` as one plain string. A caching-format message can put a list of parts there (or null), and Anthropic refuses anything but a string."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict):
        return flat_text(value.get("text"))
    if isinstance(value, (list, tuple)):
        return "\n".join(t for t in (flat_text(v) for v in value) if t)
    return str(value)


def _text_of(content: Any) -> str:
    """OpenAI message content as plain text (a list of parts keeps only its text parts: the program's request here takes text only)."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, list):
        return "".join(flat_text(p.get("text")) if isinstance(p, dict) else (p if isinstance(p, str) else "")
                       for p in content if not isinstance(p, dict) or p.get("type") in ("text", None))
    return "" if content is None else str(content)


# --- Prompt caching (2026-10-21): the markers a caching-format message carries, and the plain text blocks Anthropic takes -------------------------------------------------------
MAX_CACHE_MARKERS = 4                   # Anthropic refuses a request with more cache breakpoints than this


class MarkedText(str):
    """A system text that carried a cache marker in the request (`.marker` is the clean marker); everywhere else it is a plain string."""
    marker: dict = {"type": "ephemeral"}

    def __new__(cls, text: str, marker: dict):
        obj = super().__new__(cls, text)
        obj.marker = marker
        return obj


def clean_marker(value: Any) -> Optional[dict]:
    """The `cache_control` value Anthropic takes (`{"type": "ephemeral"}`, with a `ttl` of 5m or 1h when one was given), or None when the part carried none."""
    if not isinstance(value, dict):
        return None
    if value.get("type", "ephemeral") != "ephemeral":
        return None
    marker: dict = {"type": "ephemeral"}
    if value.get("ttl") in ("5m", "1h"):
        marker["ttl"] = value["ttl"]
    return marker


def _content_parts(content: Any) -> list:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, dict):
        return [content]
    if isinstance(content, (list, tuple)):
        return [p if isinstance(p, dict) else {"type": "text", "text": flat_text(p)} for p in content if p is not None]
    return [{"type": "text", "text": str(content)}]


def text_blocks(content: Any) -> list:
    """Any message content as clean Anthropic text blocks: every `text` a string, no empty block (Anthropic refuses one), pictures and other parts left out (this lane takes text only),
    and a `cache_control` marker kept on its block. A marker on a block that had to be dropped moves to the block before it."""
    out: list = []
    for part in _content_parts(content):
        kind = part.get("type")
        if kind not in ("text", None) or (kind is None and "text" not in part):
            continue
        marker = clean_marker(part.get("cache_control"))
        text = flat_text(part.get("text"))
        if not text.strip():
            if marker and out and "cache_control" not in out[-1]:
                out[-1]["cache_control"] = marker
            continue
        block = {"type": "text", "text": text}
        if marker:
            block["cache_control"] = marker
        out.append(block)
    return out


def anthropic_messages(messages: list) -> list:
    """The chat as Anthropic wants it: `{"role", "content": [text blocks]}`, no message left empty (its neighbours of the same role join, so the roles still alternate) and the first one from the user."""
    out: list = []
    for m in messages:
        role = "assistant" if m.get("role") == "assistant" else "user"
        blocks = text_blocks(m.get("content"))
        if not blocks:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return out


def cap_cache_markers(body: dict, limit: int = MAX_CACHE_MARKERS) -> tuple:
    """Keeps at most `limit` cache markers in a request body, the LAST ones (the nearest the end cover the longest start of the prompt) and takes the earlier ones off. Returns (found, kept)."""
    blocks: list = []
    for item in [body.get("system")] + [m.get("content") for m in body.get("messages") or [] if isinstance(m, dict)]:
        if isinstance(item, list):
            blocks.extend(b for b in item if isinstance(b, dict) and "cache_control" in b)
    found = len(blocks)
    for b in blocks[:max(0, found - limit)]:
        del b["cache_control"]
    return found, min(found, limit)


def upstream_message(raw: str, fallback: str = "Anthropic refused the request.") -> str:
    """Anthropic's own message out of an error body, the way the main device's own Claude says it (multiproxy._extract_upstream_error_message)."""
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str):
            return err[:300]
        if parsed.get("message"):
            return str(parsed["message"])[:300]
    return text[:300]


class Install:
    """One install (or update) under way, for the row's progress."""

    def __init__(self, updating: bool = False):
        self.state = "running"                  # running | done | error | cancelled
        self.step = "Starting"
        self.done = 0
        self.total = 0
        self.error = ""
        self.updating = updating
        self.version = ""
        self.task: Optional[asyncio.Task] = None
        self.cancelled = False

    def to_json(self) -> dict:
        pct = int(min(100, self.done * 100 / self.total)) if self.total else 0
        return {"state": self.state, "step": self.step, "percent": pct, "done": self.done, "total": self.total, "error": self.error, "updating": self.updating, "version": self.version}


class ClaudeHost:
    def __init__(self, folder: Path, log, changed: Callable[[], None] = lambda: None, *, releases_url: str = RELEASES_URL, api_url: str = API_URL,
                 verify_signature: Optional[bool] = None, platform_key: Optional[str] = None, environ: Optional[dict] = None, exe_name: Optional[str] = None):
        self.folder = Path(folder)
        self.log = log
        self.changed = changed
        self.releases_url = releases_url.rstrip("/")
        self.api_url = api_url.rstrip("/")
        self.verify_signature = (sys.platform == "win32") if verify_signature is None else verify_signature
        self.platform_key = platform_key or release_platform()
        self._environ = environ
        self.expected_signer = EXPECTED_SIGNER                # a test points it at the signer of a stand-in file
        self._exe_name = exe_name or ("claude.exe" if sys.platform == "win32" else "claude")      # a test installs a stand-in program under its own name
        self.install_job: Optional[Install] = None
        self._latest: tuple = ("", 0.0)                       # (version, when asked)
        self._latest_task: Optional[asyncio.Task] = None
        self._headers: Optional[dict] = None                  # the captured request headers (hold the bearer code): memory only, never written, never logged
        self._template: Optional[dict] = None
        self._capture_lock = asyncio.Lock()
        self._last_attempt = 0.0
        self._last_failure: Optional[ClaudeError] = None
        self._models: tuple = ([], 0.0)
        self._models_task: Optional[asyncio.Task] = None
        self._start_check = StartCheck()                      # the last time a downloaded program was started once to see that it works (see `_wont_start`)
        self.warning = ""                                     # the one short line for what is wrong now ("" = nothing)
        self._was_ready = False                               # a capture worked with the saved code: a later refusal means it expired
        self.inflight = 0
        self._checking = False                                # a code was just saved and is being tried
        self.login: Optional[Login] = None                    # a sign-in from the main device in progress (or the last one's end)
        self._size = program_uninstall.SizeCache()

    # ---- where things are ---------------------------------------------------------------------------------------
    @property
    def bin_dir(self) -> Path: return self.folder / "bin"

    @property
    def exe(self) -> Path: return self.bin_dir / self._exe_name

    @property
    def home(self) -> Path: return self.folder / "home"

    @property
    def token_file(self) -> Path: return self.folder / "signin.dat"

    @property
    def state_file(self) -> Path: return self.folder / "state.json"

    def _say(self, message: str) -> None:
        self.log.info(mask(message, self._token()))

    def _token(self) -> str:
        try:
            return self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # ---- state ------------------------------------------------------------------------------------------------------
    def installed(self) -> bool:
        return self.exe.is_file() and bool(self.version())

    def version(self) -> str:
        try:
            return str(json.loads(self.state_file.read_text(encoding="utf-8")).get("version") or "")
        except (OSError, ValueError):
            return ""

    def _write_state(self, **changes) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.update(changes)
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def signed_in(self) -> bool:
        return bool(self._token())

    def ready(self) -> bool:
        return self.installed() and self.signed_in() and not self.warning_blocks()

    def warning_blocks(self) -> bool:
        return self.warning in (LINE_REFUSED, LINE_EXPIRED)

    def state(self) -> str:
        job = self.install_job
        if job and job.state == "running":
            return "installing"
        if not self.installed():
            return "not_installed"
        if self.login and self.login.active():
            return "signing_in"
        if self._checking:
            return "checking"
        if not self.signed_in() or self.warning_blocks():
            return "needs_signin"
        return "ready"

    def latest_known(self) -> str:
        return self._latest[0]

    def update_available(self) -> bool:
        have, new = version_tuple(self.version()), version_tuple(self._latest[0])
        return bool(have and new and new > have)

    def status_json(self, kick: bool = True) -> dict:
        """What the dashboard row needs. Never waits on the network: the newest version is a remembered answer, refreshed in the background (at most hourly)."""
        if kick and self.installed():                         # nothing to update (and nothing to ask the network) until it is installed
            self.refresh_latest_soon()
            self.refresh_models_soon()
        job = self.install_job
        st = self.state()
        return {"state": st, "installed": self.installed(), "version": self.version() or None, "latestVersion": self._latest[0] or None,
                "updateAvailable": self.update_available() and st != "installing", "signedIn": self.signed_in(),
                "install": job.to_json() if job else None, "warning": self.warning or (job.error if job and job.state == "error" else ""),
                "models": [{"id": m, "name": label(m)} for m in (self._models[0] or [DEFAULT_MODEL])] if st == "ready" else [], "busy": self.inflight > 0,
                "platform": self.platform_key, "signin": self.login.to_json() if self.login and self.login.active() else None,
                "diskBytes": self._size.get(self.folder) if self.installed() else 0}

    # ---- the newest version ------------------------------------------------------------------------------------------
    def refresh_latest_soon(self, force: bool = False) -> None:
        if self._latest_task and not self._latest_task.done():
            return
        if not force and self._latest[0] and time.monotonic() - self._latest[1] < LATEST_CACHE_SECONDS:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                                           # asked from a worker thread: the next ask from the loop does it
        self._latest_task = loop.create_task(self._refresh_latest())

    async def _refresh_latest(self) -> None:
        try:
            v = await self.fetch_latest()
            self._latest = (v, time.monotonic())
            self.changed()
        except ClaudeError:
            self._latest = (self._latest[0], time.monotonic())      # try again in an hour, not every second

    async def fetch_latest(self) -> str:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            try:
                async with https_only.get(s, f"{self.releases_url}/latest") as r:
                    text = (await r.text()).strip()
                    if r.status != 200 or not is_version(text):
                        raise ClaudeError(502, LINE_DOWNLOAD)
                    return text
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                raise ClaudeError(502, LINE_DOWNLOAD)

    # ---- install / update -----------------------------------------------------------------------------------------------
    def start_install(self, updating: bool = False) -> Install:
        if self.install_job and self.install_job.state == "running":
            raise ClaudeError(409, "Claude is already installing on <PC>.")
        if updating and not self.installed():
            raise ClaudeError(409, LINE_NOT_INSTALLED)
        job = Install(updating)
        self.install_job = job
        job.task = asyncio.get_running_loop().create_task(self._install(job))
        self.changed()
        return job

    def cancel_install(self) -> bool:
        job = self.install_job
        if not job or job.state != "running":
            return False
        job.cancelled = True
        if job.task:
            job.task.cancel()
        return True

    async def _install(self, job: Install) -> None:
        part = self.exe.with_name(self.exe.name + ".part")
        try:
            self.bin_dir.mkdir(parents=True, exist_ok=True)
            job.step = "Finding the newest version"
            self.changed()
            version = await self.fetch_latest()
            self._latest = (version, time.monotonic())
            job.version = version
            manifest = await self._get_json(f"{self.releases_url}/{version}/manifest.json")
            plat = ((manifest.get("platforms") or {}).get(self.platform_key)) or {}
            checksum, size, binary = str(plat.get("checksum") or "").lower(), int(plat.get("size") or 0), plat.get("binary") or self.exe.name
            if not checksum or not size:
                raise ClaudeError(502, LINE_DOWNLOAD)
            job.total, job.done = size, 0
            job.step = "Downloading"
            self.changed()
            await self._download(f"{self.releases_url}/{version}/{self.platform_key}/{binary}", part, job)
            job.step = "Checking the download"
            self.changed()
            await asyncio.to_thread(self._check_file, part, size, checksum)
            if self.verify_signature:
                await self._check_signature(part)
            job.step = "Starting it to check"
            self.changed()
            new = self.exe.with_name(f"{self.exe.stem}.new{self.exe.suffix}")      # keeps the extension: Windows runs a program by it
            os.replace(part, new)
            if sys.platform != "win32":
                new.chmod(0o755)                               # a download has no run permission: without this the check below can never start it on Linux
            found = await self._run_version(new)
            if not version_tuple(found):
                new.unlink(missing_ok=True)
                raise ClaudeError(500, self._wont_start())
            old = self.exe.with_name(self.exe.name + ".old")
            try:
                if self.exe.exists():                          # an update: the old file is kept until the new one is in place
                    os.replace(self.exe, old)
                os.replace(new, self.exe)
            except PermissionError:
                new.unlink(missing_ok=True)
                raise ClaudeError(409, LINE_BUSY)
            old.unlink(missing_ok=True)
            if sys.platform != "win32":
                self.exe.chmod(0o755)
            self._write_state(version=found)
            self._models = ([], 0.0)
            self.invalidate()                                  # a new program: the next request takes a fresh capture
            job.state, job.step = "done", "Installed"
            self._say(f"Claude {found} was installed on this device.")
        except asyncio.CancelledError:
            job.state, job.step = "cancelled", "Cancelled"
            part.unlink(missing_ok=True)
            self._say("Installing Claude was cancelled.")
        except ClaudeError as e:
            job.state, job.error = "error", e.message
            part.unlink(missing_ok=True)
            self._say(f"Installing Claude failed: {e.message}")
        except Exception as e:                                 # never let a surprise leave the row stuck on "installing"
            job.state, job.error = "error", LINE_DOWNLOAD
            part.unlink(missing_ok=True)
            self.log.error(mask(f"Installing Claude failed: {e.__class__.__name__}: {e}", self._token()))
        finally:
            self.changed()

    async def _get_json(self, url: str) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with https_only.get(s, url) as r:
                    if r.status != 200:
                        raise ClaudeError(502, LINE_DOWNLOAD)
                    return json.loads(await r.text())
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            raise ClaudeError(502, LINE_DOWNLOAD)

    async def _download(self, url: str, dest: Path, job: Install) -> None:
        """Into `dest` (a .part file, resumed when one is already there), with the real progress."""
        have = dest.stat().st_size if dest.exists() else 0
        if have > job.total:
            dest.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)) as s:
                async with https_only.get(s, url, headers) as r:
                    if r.status == 200 and have:               # the server ignored the range: start again
                        have = 0
                    elif r.status not in (200, 206):
                        raise ClaudeError(502, LINE_DOWNLOAD)
                    job.done = have
                    with open(dest, "ab" if have else "wb") as f:
                        async for chunk in r.content.iter_chunked(256 * 1024):
                            f.write(chunk)
                            job.done += len(chunk)
                            self.changed()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            raise ClaudeError(502, LINE_DOWNLOAD)

    @staticmethod
    def _check_file(path: Path, size: int, checksum: str) -> None:
        if path.stat().st_size != size:
            path.unlink(missing_ok=True)
            raise ClaudeError(502, LINE_CHECKSUM)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest().lower() != checksum.lower():
            path.unlink(missing_ok=True)
            raise ClaudeError(502, LINE_CHECKSUM)

    async def _check_signature(self, path: Path) -> None:
        """Windows: the file must carry a valid signature whose signer is exactly Anthropic's (`signature_ok`). A device that can't be asked is not trusted."""
        if not await asyncio.to_thread(signature_ok, path, self.expected_signer):
            path.unlink(missing_ok=True)
            raise ClaudeError(502, LINE_SIGNATURE)

    async def _run_version(self, exe: Path) -> str:
        self._start_check = await check_start(exe, child_env("", self.home, environ=self._environ))
        return self._start_check.version

    def _wont_start(self) -> str:
        """The line for the program that was just started and did not work, from what it printed. The log keeps the first lines it said (a version check prints no secret)."""
        chk = self._start_check
        line = explain_start_failure("Claude", self.folder, chk)
        shown = " | ".join(x.strip() for x in (chk.output or "").splitlines() if x.strip())[:300]
        self._say(f"Claude didn't start when checked (exit code {chk.returncode}{', timed out' if chk.timed_out else ''}{', ' + str(chk.error) if chk.error else ''}). It printed: {shown or 'nothing'}")
        return line

    # ---- sign in / out ---------------------------------------------------------------------------------------------------
    async def sign_in(self, raw: str) -> dict:
        """Check the pasted code, keep it (only here), and try it with one real capture. A code that is refused is not kept."""
        if not self.installed():
            raise ClaudeError(409, LINE_NOT_INSTALLED)
        token = check_token(raw)
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_suffix(".tmp")
        tmp.write_text(token, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp, self.token_file)
        self.warning = ""
        self._was_ready = False
        self.invalidate()
        self._checking = True
        self.changed()
        try:
            await self.ensure_ready(force=True)
        except ClaudeError as e:
            if e.kind in ("refused", "expired"):
                self.token_file.unlink(missing_ok=True)       # a refused code is no use to anyone: don't keep it
            raise
        finally:
            self._checking = False
            self.changed()
        self._say("Claude was signed in on this device.")
        return self.status_json(False)

    # ---- sign in from the main device: this device runs `claude setup-token` itself -----------------------------------------------------------------------
    def signin_argv(self) -> list:
        return [str(self.exe), "setup-token"]

    def _signin_env(self) -> dict:
        env = child_env("", self.home, environ=self._environ)
        if sys.platform != "win32":
            env["BROWSER"] = "true"                              # nothing opens on this device: the person opens the link on the device they are using
        return env

    async def start_signin(self) -> dict:
        """Start `claude setup-token` on a hidden terminal and wait for its sign-in link. The long-lived code it prints later never leaves this device."""
        if not self.installed():
            raise ClaudeError(409, LINE_NOT_INSTALLED)
        if self.login and self.login.active():
            if self.login.state == "starting":
                await self._wait_link(self.login)
            return self.status_json(False)
        lg = Login()
        self.login = lg
        self.warning = ""
        self.home.mkdir(parents=True, exist_ok=True)
        env = self._signin_env()
        lg.browser = "none" if env.get("BROWSER") == "true" else "opened"          # (a device that is not Windows has the browser step switched off in `_signin_env`)
        try:
            lg.term = terminal_process.open_terminal(self.signin_argv(), env, str(self.home))
        except terminal_process.TerminalUnavailable:
            self._signin_failed(lg, LINE_NO_TERMINAL, "no_terminal")
            raise ClaudeError(501, LINE_NO_TERMINAL, "no_terminal")
        except OSError as e:
            line = explain_start_failure("Claude", self.folder, StartCheck(error=e))
            self._signin_failed(lg, line, "wont_start")
            raise ClaudeError(502, line, "wont_start")
        lg.task = asyncio.get_running_loop().create_task(self._drive(lg))
        self.changed()
        await self._wait_link(lg)
        return self.status_json(False)

    async def _wait_link(self, lg: Login) -> None:
        try:
            await asyncio.wait_for(lg.link_ready.wait(), SIGNIN_LINK_WAIT + 5)
        except asyncio.TimeoutError:
            pass
        if lg.state == "error":
            raise ClaudeError(502, lg.error, lg.kind or "no_link")

    def _log_signin_end(self, lg: Login, raw: str, term) -> None:
        """One plain line for why a sign-in ended without a kept code, and the SHAPE of what the program printed (never its content: see `shape_of`)."""
        what = {"ended": "The sign-in program ended before giving a code.", "ran_out": "The sign-in program gave no code in time.", "no_link": "The sign-in program gave no link in time.",
                "refused": "The code was not accepted.", "bad_link": "The sign-in link wasn't Anthropic's address, so it wasn't used."}.get(lg.kind, "The sign-in did not finish.")
        alive = bool(term is not None and term.alive())
        code = getattr(term, "returncode", None)
        self.log.info(f"{what} (exit code {code if code is not None else ('none: still running' if alive else 'unknown')}, ran {time.monotonic() - lg.started:.0f} s)")
        cleaned = strip_terminal(raw)
        lines = [x for x in cleaned.split("\n") if x.strip()]
        self.log.info("What the program printed, as shapes only (letters a, digits 9): " + str(len(lines)) + " lines, the last " + str(min(20, len(lines))) + " follow")
        for s in shape_of(raw):
            self.log.info("  " + s)
        question = any(x.rstrip().endswith(("?", ":")) for x in lines[-20:])
        self.log.info(f"  a long code was in the output: {'yes' if TOKEN_START_RE.search(cleaned) else 'no'}; the program was still running: {'yes' if alive else 'no'}; "
                      f"a line looked like a question or a prompt: {'yes' if question else 'no'}; terminal size: {terminal_process.COLUMNS}x{terminal_process.ROWS}")

    def _signin_failed(self, lg: Login, line: str, kind: str) -> None:
        lg.state, lg.error, lg.kind = "error", line, kind
        if kind in ("ended", "ran_out"):
            self.warning = line                          # nobody may be waiting on the answer (the person was on the other device): the row says it, instead of quietly going back to "Needs sign-in"
        lg.outcome, lg.ended = ClaudeError(502, line, kind), True
        lg.link_ready.set()
        self.changed()

    async def _drive(self, lg: Login) -> None:
        """Read what the program prints: first its link, then (after submit_code) its long-lived code. Nothing it prints is ever logged."""
        raw, term = "", lg.term
        grew, enter_sent = time.monotonic(), False
        try:
            while lg.active():
                chunk = term.read()
                if chunk:
                    raw = (raw + chunk.decode("utf-8", "replace"))[-200_000:]
                    grew = time.monotonic()
                if not enter_sent and raw and lg.state in ("link", "checking"):             # "Press Enter to continue" after the browser approval: answered once (never a question or a choice)
                    tail = [x.strip() for x in strip_terminal(raw[-2000:]).split("\n") if x.strip()][-1:]
                    if tail and ENTER_PROMPT_RE.search(tail[0]):
                        enter_sent = True
                        try:
                            term.write("\r")
                            self._say("The sign-in program asked to press Enter, so Enter was pressed once.")
                        except OSError:
                            pass
                waited = time.monotonic() - lg.started
                if lg.state == "starting":
                    url = find_link(raw)
                    if url:
                        if not link_allowed(url):
                            self._say("The sign-in link wasn't Anthropic's address, so it wasn't used.")
                            self._signin_failed(lg, LINE_BAD_LINK, "bad_link")
                            break
                        lg.url, lg.link_state, lg.state = url, link_state(url), "link"
                        self._say(f"A sign-in link is ready ({link_for_log(url)}).")
                        lg.link_ready.set()
                        self.changed()
                    elif not term.alive() and term.ended():
                        self._signin_failed(lg, LINE_NO_LINK, "no_link")
                        break
                    elif waited > SIGNIN_LINK_WAIT:
                        self._signin_failed(lg, LINE_NO_LINK, "no_link")
                        break
                elif lg.state in ("link", "checking"):
                    # The program prints its long-lived code both after a pasted one-time code AND when the sign-in finished by itself (approved in a browser on this very
                    # device, the way `claude setup-token` does when it can open one): either way it is kept the same.
                    token, whole = find_token(raw)
                    if len(token) >= TOKEN_MIN_CHARS and (whole or term.ended() or time.monotonic() - grew >= SIGNIN_QUIET_KEEP):
                        await self._finish_signin(lg, token)
                        break
                    if not term.alive() and term.ended():
                        self._signin_failed(lg, LINE_CODE_NOT_ACCEPTED if lg.state == "checking" else LINE_SIGNIN_ENDED, "refused" if lg.state == "checking" else "ended")
                        break
                    if lg.state == "checking" and time.monotonic() - lg.code_sent > SIGNIN_CODE_WAIT:
                        self._signin_failed(lg, LINE_CODE_NOT_ACCEPTED, "refused")
                        break
                    if waited > SIGNIN_TOTAL:
                        self._signin_failed(lg, LINE_LINK_RAN_OUT, "ran_out")
                        break
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log.error(mask(f"Signing in to Claude failed: {e.__class__.__name__}"))
            self._signin_failed(lg, LINE_SIGNIN_ENDED, "ended")
        finally:
            if lg.state == "error" and lg.kind:
                try:
                    self._log_signin_end(lg, raw, term)
                except Exception:
                    pass
            raw = ""
            await self._stop_terminal(lg)
            if lg.ended and not lg.result.done():            # the program is stopped now: only then is the answer given
                lg.result.set_result(lg.outcome)
            self.changed()

    async def _finish_signin(self, lg: Login, token: str) -> None:
        """The program printed its long-lived code: keep it exactly as a pasted one is kept (checked, saved only here, tried with one real capture)."""
        try:
            await self.sign_in(token)
        except ClaudeError as e:
            lg.state, lg.error, lg.kind = "error", e.message, e.kind
            lg.outcome, lg.ended = e, True
        else:
            lg.state, lg.outcome, lg.ended = "done", None, True
            self._say(f"The sign-in program gave a code and it was kept (ran {time.monotonic() - lg.started:.0f} s).")
        finally:
            lg.link_ready.set()

    async def _stop_terminal(self, lg: Login) -> None:
        term, lg.term = lg.term, None
        if term is None:
            return
        end = time.monotonic() + (SIGNIN_DIE_GRACE if lg.state == "done" else 0)
        while term.alive() and time.monotonic() < end:
            await asyncio.sleep(0.1)
        await asyncio.to_thread(term.kill)

    async def submit_code(self, raw: str) -> dict:
        """Type the one-time code the player pasted into the waiting program, and wait for the long-lived code it then prints. The one-time code is not kept or logged."""
        lg = self.login
        if self.ready() and not (lg and lg.active()):
            return self.status_json(False)                  # it finished by itself (or the code came twice): signed in is signed in, nothing to complain about
        if lg and lg.state == "error" and lg.error:
            raise ClaudeError(409, lg.error, lg.kind or "ended")
        if not lg or lg.state not in ("link",) or lg.term is None:
            raise ClaudeError(409, LINE_NO_CODE_YET, "no_signin")
        code = "".join((raw or "").split())
        if not CODE_RE.match(code):
            raise ClaudeError(400, LINE_NOT_A_CODE, "bad_code")
        if "#" in code and lg.link_state and code.split("#", 1)[1] != lg.link_state:
            raise ClaudeError(400, LINE_CODE_OTHER_LINK, "other_link")
        lg.state, lg.code_sent = "checking", time.monotonic()
        self.changed()
        try:
            lg.term.write(code + "\r")
        except OSError:
            if self.ready():
                return self.status_json(False)                # the program had just finished by itself
            self._signin_failed(lg, LINE_SIGNIN_ENDED, "ended")
            raise ClaudeError(502, LINE_SIGNIN_ENDED, "ended")
        code = ""
        try:
            outcome = await asyncio.wait_for(asyncio.shield(lg.result), SIGNIN_CODE_WAIT + 90)
        except asyncio.TimeoutError:
            outcome = ClaudeError(504, LINE_CODE_NOT_ACCEPTED, "refused")
        if isinstance(outcome, ClaudeError):
            if self.ready():
                return self.status_json(False)                # it finished by itself while the code was on its way
            raise outcome
        return self.status_json(False)

    def cancel_signin(self) -> bool:
        lg = self.login
        if not lg or not lg.active():
            return False
        lg.state = "cancelled"
        if not lg.result.done():
            lg.result.set_result(ClaudeError(409, LINE_SIGNIN_ENDED, "cancelled"))
        lg.link_ready.set()
        if lg.term is not None:
            try:
                lg.term.kill()
            except Exception:
                pass
        self._say("Signing in to Claude was cancelled.")
        self.changed()
        return True

    async def shutdown(self) -> None:
        """The Courier is quitting: a sign-in still waiting is stopped, and the program with it."""
        self.cancel_signin()

    def sign_out(self) -> dict:
        self.cancel_signin()
        self.token_file.unlink(missing_ok=True)
        shutil.rmtree(self.home, ignore_errors=True)         # whatever the program wrote about this sign-in goes too
        self.invalidate()
        self.warning = ""
        self._was_ready = False
        self._models = ([], 0.0)
        self._say("Claude was signed out on this device.")
        self.changed()
        return self.status_json(False)

    async def uninstall(self) -> dict:
        """Remove the program, the saved sign-in code and everything the program wrote (this device's whole Claude folder, and nothing else). Busy answers a plain line and
        does nothing. The folder is removed by `program_uninstall` (inside the Courier's own data folder only)."""
        job = self.install_job
        if self.inflight > 0 or self._capture_lock.locked() or (job and job.state == "running"):
            raise ClaudeError(409, LINE_BUSY)
        for t in (self._latest_task, self._models_task):
            if t and not t.done():
                t.cancel()
        self.token_file.unlink(missing_ok=True)               # the sign-in code first: it must be gone even if something else in the folder is locked
        self.invalidate()
        self.warning = ""
        self._was_ready = False
        self._models = ([], 0.0)
        self.install_job = None
        self._size.forget()
        try:
            freed = await asyncio.to_thread(program_uninstall.remove_program, self.folder.parent, self.folder)
        except program_uninstall.UninstallError as e:
            self.changed()
            raise ClaudeError(e.status, e.message)
        self._say(f"Claude was uninstalled from this device (freed {freed} bytes).")
        self.changed()
        return {**self.status_json(False), "freedBytes": freed}

    def invalidate(self) -> None:
        self._headers = None
        self._template = None

    # ---- the capture (as multiproxy.capture_auth does) -----------------------------------------------------------------------
    async def ensure_ready(self, *, force: bool = False) -> None:
        if not self.installed():
            raise ClaudeError(503, LINE_NOT_INSTALLED, "not_installed")
        token = self._token()
        if not token:
            raise ClaudeError(503, LINE_NEEDS_SIGN_IN, "needs_sign_in")
        async with self._capture_lock:
            if self._headers and self._template and not force:
                return
            now = time.monotonic()
            if not force and self._last_failure and now - self._last_attempt < RECAPTURE_COOLDOWN:
                raise self._last_failure
            self._last_attempt = now
            try:
                await self._capture(token)
            except ClaudeError as e:
                self._last_failure = e
                self._note_failure(e)
                raise
            self._last_failure = None
            self._was_ready = True
            if self.warning in (LINE_REFUSED, LINE_EXPIRED, LINE_UNREACHABLE):
                self.warning = ""
            self.changed()

    def _note_failure(self, e: ClaudeError) -> None:
        if e.kind in ("refused", "expired", "unreachable"):
            self.warning = e.message
            self.changed()

    def _classify(self, status: Optional[int], text: str) -> ClaudeError:
        low = (text or "").lower()
        if status in (401, 403) or "invalid bearer" in low or "failed to authenticate" in low or "not logged in" in low or "login expired" in low:
            if self._was_ready or "expired" in low:
                return ClaudeError(503, LINE_EXPIRED, "expired")
            return ClaudeError(503, LINE_REFUSED, "refused")
        if status == 429 or "limit" in low or "credit balance" in low:
            return ClaudeError(status or 429, mask(upstream_message(text, "You reached Claude's limit."), self._token()), "limit")
        return ClaudeError(status or 502, LINE_UNREACHABLE, "unreachable")

    async def _capture(self, token: str) -> None:
        """One real `claude --print` through a loopback interceptor: keeps the request headers and a body template (tools and thinking stripped)."""
        got: dict = {}

        async def handler(request: web.Request) -> web.Response:
            body = await request.read()
            headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
            try:
                parsed = json.loads(body) if body else {}
            except ValueError:
                parsed = {}
            if "messages" in parsed and "headers" not in got and "haiku" not in str(parsed.get("model", "")) and "count_tokens" not in request.path:
                for k in ("tools", "thinking", "context_management"):
                    parsed.pop(k, None)
                got["headers"], got["template"] = dict(headers), parsed
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.request(request.method, self.api_url + request.path_qs, data=body, headers=headers) as up:
                        data = await up.read()
                        return web.Response(body=data, status=up.status, headers={"Content-Type": up.headers.get("Content-Type", "application/json")})
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                return web.Response(status=502, text="unreachable")

        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.home.mkdir(parents=True, exist_ok=True)
            env = child_env(token, self.home, f"http://127.0.0.1:{port}", self._environ)
            with tempfile.TemporaryDirectory(prefix="claude-capture-") as tmp:
                proc = await hidden_process.create_subprocess_exec(
                    str(self.exe), "--print", "--output-format", "text", "--model", DEFAULT_MODEL, "--no-session-persistence",
                    "--system-prompt", "Say ok", "ok", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=tmp)
                try:
                    out, err = await asyncio.wait_for(proc.communicate(), timeout=CAPTURE_TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise ClaudeError(504, LINE_UNREACHABLE, "unreachable")
        except OSError as e:
            raise ClaudeError(500, explain_start_failure("Claude", self.folder, StartCheck(error=e)), "wont_start")
        finally:
            await runner.cleanup()
        text = (out or b"").decode("utf-8", "replace") + "\n" + (err or b"").decode("utf-8", "replace")
        if proc.returncode != 0 or "headers" not in got:
            e = self._classify(None, text)
            self.log.info(mask(f"Claude's capture failed on this device (exit {proc.returncode}): {first_line(text)}", token))
            raise e
        self._headers, self._template = got["headers"], got["template"]

    # ---- asking Anthropic ------------------------------------------------------------------------------------------------------
    def _api_headers(self) -> dict:
        return {k: v for k, v in (self._headers or {}).items() if k.lower() not in {"content-length", "content-type", "accept-encoding", "host", "connection"}}

    def refresh_models_soon(self) -> None:
        """The model list is read in the background (it may need the one-time capture), never while a report is being built."""
        if self.state() != "ready" or (self._models[0] and time.monotonic() - self._models[1] < MODELS_CACHE_SECONDS):
            return
        if self._models_task and not self._models_task.done():
            return
        try:
            self._models_task = asyncio.get_running_loop().create_task(self.models())
        except RuntimeError:
            pass

    async def models(self) -> list:
        """Anthropic's model ids for this sign-in (remembered for ten minutes); the default one when the list can't be read."""
        if self._models[0] and time.monotonic() - self._models[1] < MODELS_CACHE_SECONDS:
            return self._models[0]
        ids: list = []
        try:
            await self.ensure_ready()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(self.api_url + "/v1/models", headers=self._api_headers(), params={"limit": "100"}) as r:
                    if r.status == 200:
                        ids = [str(m.get("id")) for m in (await r.json()).get("data") or [] if str(m.get("id", "")).startswith("claude-")]
        except (ClaudeError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
            pass
        ids = ids or [DEFAULT_MODEL]
        self._models = (ids, time.monotonic())
        self.changed()
        return ids

    def build_body(self, messages: list, model: str, max_tokens: int) -> dict:
        """Anthropic's request body from the template (as multiproxy._build_api_body): the template's own first system block, the chat's system text after it."""
        body = json.loads(json.dumps(self._template))
        system_text = "\n\n".join(_text_of(m.get("content")) for m in messages if m.get("role") == "system")
        first = (body.get("system") or [None])[0]
        body["system"] = ([first] if first else []) + ([{"type": "text", "text": system_text}] if system_text else [])
        template_first = body["messages"][0] if body.get("messages") else {}
        auth_blocks = [b for b in (template_first.get("content") or []) if isinstance(b, dict) and b.get("type") == "text" and "<system-reminder>" in b.get("text", "")] \
            if isinstance(template_first.get("content"), list) else []
        sys_marker = next((mk for m in messages if m.get("role") == "system" for p in _content_parts(m.get("content"))
                           for mk in [clean_marker(p.get("cache_control"))] if mk), None)
        if sys_marker and system_text:
            body["system"][-1]["cache_control"] = sys_marker
        out = anthropic_messages([m for m in messages if m.get("role") != "system"])
        if out and out[0]["role"] == "user":
            out[0]["content"] = auth_blocks + out[0]["content"]
        body["messages"] = out
        found, kept = cap_cache_markers(body)
        if found > kept:
            self.log.info(f"[cache] kept {kept} of {found} cache markers (Anthropic takes at most {MAX_CACHE_MARKERS})")
        body["model"], body["max_tokens"], body["stream"] = model, max_tokens, True
        body.pop("thinking", None)
        return body

    async def _open(self, session: aiohttp.ClientSession, body: dict):
        data = json.dumps(body).encode("utf-8")
        return session.post(self.api_url + "/v1/messages?beta=true", data=data, headers={**self._api_headers(), "Content-Type": "application/json", "Content-Length": str(len(data))})

    async def chat(self, request: web.Request, body: dict) -> web.StreamResponse:
        """One OpenAI-style chat request, answered with the signed-in Claude on this device (streamed or whole, as asked)."""
        model = str(body.get("model") or DEFAULT_MODEL)
        if model.lower().startswith("claude/"):
            model = model[7:]
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        if not any(m.get("role") != "system" for m in messages):
            raise ClaudeError(400, "That request has no message to answer.")
        max_tokens = int(body.get("max_tokens") or body.get("max_completion_tokens") or 4096)
        stream = bool(body.get("stream"))
        self.inflight += 1
        self.changed()
        try:
            for attempt in range(2):
                await self.ensure_ready()
                started = time.time()
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=600)) as session:
                    try:
                        cm = await self._open(session, self.build_body(messages, model, max_tokens))
                        async with cm as resp:
                            if resp.status in (401, 403) and attempt == 0:
                                await resp.read()
                                self.invalidate()                  # the captured sign-in was thrown away upstream: take a fresh one, once
                                await self.ensure_ready(force=True)
                                continue
                            if resp.status != 200:
                                raw = (await resp.read()).decode("utf-8", "replace")
                                err = self._classify(resp.status, raw)
                                if err.kind in ("refused", "expired"):
                                    self.invalidate()
                                    self._note_failure(err)
                                if err.kind == "unreachable":
                                    err = ClaudeError(resp.status, mask(upstream_message(raw), self._token()), "upstream")
                                raise err
                            return await self._relay(request, resp, model, stream, started)
                    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                        raise ClaudeError(502, LINE_UNREACHABLE, "unreachable")
            raise ClaudeError(503, LINE_REFUSED, "refused")
        finally:
            self.inflight -= 1
            self.changed()

    async def _relay(self, request: web.Request, resp, model: str, stream: bool, started: float) -> web.StreamResponse:
        cmpl_id, created = f"chatcmpl-{uuid.uuid4().hex[:16]}", int(time.time())

        def chunk(delta: dict, finish: Optional[str] = None) -> str:
            return "data: " + json.dumps({"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
                                          "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
        out: Optional[web.StreamResponse] = None
        if stream:
            out = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
            await out.prepare(request)
            await out.write(chunk({"role": "assistant", "content": ""}).encode())
        text_parts: list = []
        usage = {"in": 0, "out": 0}
        stop = None
        buf = ""
        async for raw in resp.content.iter_any():
            buf += raw.decode("utf-8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "message_start":
                    usage["in"] = (ev.get("message") or {}).get("usage", {}).get("input_tokens", 0)
                elif kind == "message_delta":
                    usage["out"] = (ev.get("usage") or {}).get("output_tokens", 0)
                    stop = (ev.get("delta") or {}).get("stop_reason") or stop
                elif kind == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                    t = ev["delta"].get("text", "")
                    if t:
                        text_parts.append(t)
                        if out is not None:
                            await out.write(chunk({"content": t}).encode())
        finish = "length" if stop == "max_tokens" else "stop"
        self._say(f"Claude answered a request on this device ({model}, {len(''.join(text_parts))} characters, {time.time() - started:.1f} s).")
        if out is not None:
            await out.write(chunk({}, finish).encode())
            await out.write(b"data: [DONE]\n\n")
            await out.write_eof()
            return out
        return web.json_response({"id": cmpl_id, "object": "chat.completion", "created": created, "model": model,
                                  "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(text_parts)}, "finish_reason": finish}],
                                  "usage": {"prompt_tokens": usage["in"], "completion_tokens": usage["out"], "total_tokens": usage["in"] + usage["out"]}})

    async def test(self) -> dict:
        """One tiny real request: {"ok": True, "model", "seconds"} or {"ok": False, "error": the one short line}."""
        started = time.time()

        class _Req:                                          # `chat` only needs something to stream to when asked to stream; a test never does
            pass
        try:
            ids = await self.models()
            model = DEFAULT_MODEL if DEFAULT_MODEL in ids else ids[0]
            resp = await self.chat(_Req(), {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with the single word ok."}]})
            ok = resp.status == 200
        except ClaudeError as e:
            return {"ok": False, "error": e.message, "kind": e.kind}
        return {"ok": ok, "model": model, "seconds": round(time.time() - started, 1)}

    def handles(self, model: str) -> bool:
        m = (model or "").lower()
        return m.startswith("claude-") or m.startswith("claude/")


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:300]
    return ""


def label(model_id: str) -> str:
    """'Sonnet 5.5' for 'claude-sonnet-5-5' (the same rule as multiproxy._claude_model_label's fallback)."""
    parts = [p for p in model_id.split("-") if not (len(p) == 8 and p.isdigit())]
    return f"{parts[1].title()} {'.'.join(parts[2:])}" if len(parts) >= 3 and parts[0] == "claude" else model_id
