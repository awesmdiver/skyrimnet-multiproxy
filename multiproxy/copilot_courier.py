"""GitHub Copilot's command-line program on a Courier device (2026-10-06): install, sign in, run and answer for the main device. Built to the same pattern as `claude_courier.py`.

What it does on this device, all behind the pairing code and the home-network rule like everything else the Courier answers:

  * **Install** GitHub's own program from its public release page (`github.com/github/copilot-cli`): the release's `SHA256SUMS.txt` is fetched, the download is checked
    against it (size and SHA-256), unpacked into Courier's own folder (`copilot/bin`) and started once with `--version`. No administrator, no windows, nothing outside
    that folder, no automatic updates (the player presses Update). GitHub publishes no signature for the file, so there is no signature check, only the published checksum.
  * **Sign in** with the program's own device-code sign-in (`copilot login --device-code`): the program prints a short sign-in code, the player types it at GitHub on any
    device, and the program saves its own sign-in. This module never sees that sign-in: it shows the code (a one-time code, not a secret) and waits for the program to finish.
    Everything that could override it (`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` and the other `COPILOT_` / `GH_` / `GITHUB_` variables) is removed from the
    program's environment, and it gets a configuration folder of its own (`COPILOT_HOME`), so a Copilot the player already uses on this device is not read or changed.
  * **Answer** one chat request per run of the real program (`copilot -p "..."`, in an empty folder, with its tools turned down), turning what it prints into
    OpenAI-style chunks. A sign-in that ran out, a plan without Copilot, a limit or any other trouble each end in ONE short line; a limit is passed on in GitHub's own words.

The secret rules this module keeps (and `tests/test_copilot_courier.py` scans for): a token never appears in a log line, a status, an error line or a response; `mask()` is
applied to everything that leaves this module as text. Evidence and the alternatives: `docs/plans/2026-10-06-copilot-on-another-pc.md`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp
from aiohttp import web

import claude_courier
import hidden_process
import program_uninstall
from claude_courier import Install, StartCheck, check_start, explain_start_failure, first_line, is_version, release_platform, version_tuple

API_RELEASE_URL = "https://api.github.com/repos/github/copilot-cli/releases/latest"      # the newest release: its tag and its files (name, size)
DOWNLOAD_BASE = "https://github.com/github/copilot-cli/releases/download"               # <base>/<tag>/<file>: built here, never taken from the answer
DEFAULT_MODEL = "default"                       # the model id that means "whatever the program picks" (no --model given)
MODEL_PREFIX = "copilot/"
LATEST_CACHE_SECONDS = 3600.0
MODELS_CACHE_SECONDS = 600.0
VERSION_TIMEOUT = 30.0
LOGOUT_TIMEOUT = 20.0
LIST_MODELS_TIMEOUT = 30.0
REQUEST_TIMEOUT = 600.0
CODE_WAIT_SECONDS = 45.0                         # how long the program has to print a sign-in code
LOGIN_SECONDS = 16 * 60.0                        # a GitHub device code lives 15 minutes
MAX_CONCURRENT = 4
MAX_UNPACKED_BYTES = 1500 * 1024 * 1024          # a release unpacks to a few hundred megabytes; more than this is not a Copilot release
ARGV_PROMPT_LIMIT = 20000                        # a longer prompt goes in through the program's input, not the command line (Windows stops near 32 000 characters)
VERIFY_URL = "https://github.com/login/device"
SECRET_RE = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
URL_RE = re.compile(r"https://github\.com/login/device\S*")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,60}$")

# What the program is started with for one request. Every flag is one GitHub documents (docs.github.com, Copilot CLI command reference, read 2026-10-06);
# `docs/plans/2026-10-06-copilot-on-another-pc.md` says which ones could only be read about, not run. A program that answers "unknown option" gets the short list instead.
FLAGS_FULL = ["-s", "--output-format", "json", "--no-ask-user", "--no-color", "--no-custom-instructions", "--disable-builtin-mcps", "--deny-tool=shell,write"]
FLAGS_SHORT = ["-s", "--no-ask-user"]
UNKNOWN_FLAG_RE = re.compile(r"unknown (option|argument|flag)|unrecognized (option|argument|flag)|invalid (option|argument)|too many arguments", re.I)

# The one short line for each thing that can go wrong (the design side runs the wording through Gemini; `docs` quote them).
# A backquoted word is a command: the dashboard shows it in code style. `<PC>` is the other device's name: the dashboard fills it in when it shows the line (copilot_ui, through claude_ui.ccWarn).
LINE_NOT_INSTALLED = "Copilot isn't installed on <PC> yet."
LINE_NEEDS_SIGN_IN = "Copilot on <PC> needs a sign-in."
LINE_EXPIRED = "The Copilot sign-in expired. Press Sign in to start a new one."
LINE_DOWNLOAD = "Couldn't download Copilot. Check that <PC> is online, then try again."
LINE_CHECKSUM = "The download failed a security check, so it was deleted. Try again."
LINE_WONT_START = "Copilot was downloaded but won't start on <PC>."          # (the reason comes from `explain_start_failure`: what the program printed; antivirus is only named on Windows when it printed nothing)
LINE_NO_PROGRAM = "The Copilot download was missing the actual program. Try again."
LINE_BUSY = "Copilot is busy on <PC> right now. Try again in a moment."
LINE_UNREACHABLE = "Copilot on <PC> couldn't reach GitHub."
LINE_NO_CODE = "Couldn't get a sign-in code from Copilot. Try again."
LINE_CODE_RAN_OUT = "The sign-in code ran out of time. Press Sign in to get a new one."
LINE_LOGIN_FAILED = "The sign-in didn't finish. Press Sign in to try again."
LINE_NO_PLAN = "That GitHub account doesn't have Copilot."
LINE_NO_ANSWER = "Copilot didn't answer."

_STRIP_PREFIXES = ("COPILOT_", "GH_", "GITHUB_")
_NOT_SIGNED_IN_RE = re.compile(r"not (logged|signed) in|not authenticated|no authentication|authentication (is )?required|please (log ?in|sign ?in)|run .{0,20}login|/login|"
                               r"unauthori[sz]ed|bad credentials|token (is )?(invalid|expired)|\b401\b", re.I)
_NO_PLAN_RE = re.compile(r"\b(no|not have|don't have|doesn't have|without) .{0,40}(copilot|subscription|plan|access|license|seat)|not (enabled|entitled)|copilot .{0,30}(is )?(disabled|not available)", re.I)
_LIMIT_RE = re.compile(r"rate.?limit|quota|limit (was )?(reached|exceeded)|exceeded|usage limit|out of (credits|requests)|no (remaining )?(credits|requests)|too many requests|\b429\b|\b402\b|credits", re.I)


class CopilotError(Exception):
    """A plain, player-readable reason, with the HTTP status the Courier answers with."""

    def __init__(self, status: int, message: str, kind: str = "error"):
        super().__init__(message)
        self.status, self.message, self.kind = status, message, kind


def mask(text: Any) -> str:
    """`text` with anything shaped like a GitHub token hidden. Applied to everything that leaves this module as text."""
    return SECRET_RE.sub("[token]", str(text))


def clean_line(text: str) -> str:
    return ANSI_RE.sub("", text or "").replace("\r", "").strip()


def child_env(config_dir: Path, environ: Optional[dict] = None) -> dict:
    """The environment the program runs in: this device's own, with every variable that could give it another sign-in or send it elsewhere removed, then ONLY what Copilot
    needs added. A token in this device's own environment would otherwise win over the sign-in this Courier made (GitHub documents the order)."""
    env = {k: v for k, v in (os.environ if environ is None else environ).items() if not k.upper().startswith(_STRIP_PREFIXES)}
    env["COPILOT_HOME"] = str(config_dir)                  # its own folder: a Copilot the player already has on this device is not read or changed
    env["GH_CONFIG_DIR"] = str(config_dir / "gh")          # and the GitHub command line's own sign-in is not a way in either
    env["COPILOT_AUTO_UPDATE"] = "false"                   # no automatic updates; the player presses Update
    env["NO_COLOR"] = "1"
    return env


def asset_name(platform_key: str) -> str:
    """GitHub's file name for this device's kind (`copilot-win32-x64.zip`, `copilot-linux-x64.tar.gz`)."""
    return f"copilot-{platform_key}" + (".zip" if platform_key.startswith("win32") else ".tar.gz")


def parse_sums(text: str) -> dict:
    """`SHA256SUMS.txt` ('<64 hex>  <file>' per line, the file maybe starting with '*') as {file: lower-case hash}."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(\S+)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def prompt_from(messages: list) -> str:
    """One chat request as the single prompt the program takes. A lone user message goes as it is; anything longer keeps who said what, with the system text first."""
    turns = [(("assistant" if m.get("role") == "assistant" else "user"), claude_courier._text_of(m.get("content"))) for m in messages if m.get("role") != "system"]
    system = "\n\n".join(claude_courier._text_of(m.get("content")) for m in messages if m.get("role") == "system").strip()
    if len(turns) == 1 and turns[0][0] == "user" and not system:
        return turns[0][1]
    parts = ["Reply with only the assistant's next message to the conversation below. Do not use any tools, run any commands or read any files."]
    if system:
        parts.append("Instructions:\n" + system)
    parts.append("Conversation:\n" + "\n\n".join(("Assistant: " if r == "assistant" else "User: ") + t for r, t in turns))
    return "\n\n".join(parts)


def label(model_id: str) -> str:
    """The name the model lists show for 'gpt-5.2' (the program's own name for it, tidied a little) and 'default' for the program's own pick."""
    mid = model_id[len(MODEL_PREFIX):] if model_id.startswith(MODEL_PREFIX) else model_id
    return "Default" if mid == DEFAULT_MODEL else mid


def parse_model_list(text: str) -> list:
    """Model names out of what `copilot --list-models` prints: a name per line, maybe after a bullet. Anything that is not one bare name is skipped."""
    ids: list = []
    for raw in (text or "").splitlines():
        line = clean_line(raw).lstrip("-*• ").strip()
        token = line.split()[0] if line else ""
        if token and len(line.split()) <= 6 and MODEL_RE.match(token) and token.lower() not in ("available", "models", "model", "usage", "options") and token not in ids:
            if any(c.isdigit() for c in token) or "-" in token:
                ids.append(token)
    return ids[:60]


def safe_extract(archive: Path, dest: Path) -> None:
    """Unpack a zip or tar.gz into `dest` only: no path that leaves it, no link, no device file, and no more than `MAX_UNPACKED_BYTES` in all."""
    dest.mkdir(parents=True, exist_ok=True)
    root = os.path.realpath(dest)
    total = 0

    def target(name: str) -> str:
        t = os.path.realpath(os.path.join(root, name))
        if not (t == root or t.startswith(root + os.sep)) or os.path.isabs(name):
            raise ValueError("a file that would land outside its folder")
        return t
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                t = target(info.filename)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("a link")
                total += info.file_size
                if total > MAX_UNPACKED_BYTES:
                    raise ValueError("too big")
                if info.is_dir():
                    os.makedirs(t, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(t), exist_ok=True)
                with z.open(info) as src, open(t, "wb") as out:
                    shutil.copyfileobj(src, out)
    else:
        with tarfile.open(archive, "r:*") as tf:
            for m in tf.getmembers():
                t = target(m.name)
                if not (m.isfile() or m.isdir()):
                    raise ValueError("a link or special file")
                total += m.size
                if total > MAX_UNPACKED_BYTES:
                    raise ValueError("too big")
                if m.isdir():
                    os.makedirs(t, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(t), exist_ok=True)
                src = tf.extractfile(m)
                with open(t, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(t, 0o755 if m.mode & 0o111 else 0o644)


def find_program(folder: Path, names: tuple) -> Optional[Path]:
    """The program file inside an unpacked release: at the top, or one folder down."""
    for depth in (0, 1):
        for n in names:
            pattern = n if depth == 0 else "*/" + n
            hit = sorted(p for p in folder.glob(pattern) if p.is_file())
            if hit:
                return hit[0]
    return None


class Login:
    """One sign-in under way, for the row."""

    def __init__(self):
        self.state = "starting"                 # starting | code | done | error | cancelled
        self.code = ""
        self.url = VERIFY_URL
        self.error = ""
        self.task: Optional[asyncio.Task] = None
        self.proc = None

    def to_json(self) -> dict:
        return {"state": self.state, "code": self.code if self.state == "code" else "", "url": self.url, "error": self.error}


class CopilotHost:
    def __init__(self, folder: Path, log, changed: Callable[[], None] = lambda: None, *, api_url: str = API_RELEASE_URL, download_base: str = DOWNLOAD_BASE,
                 platform_key: Optional[str] = None, environ: Optional[dict] = None, exe_names: Optional[tuple] = None):
        self.folder = Path(folder)
        self.log = log
        self.changed = changed
        self.api_url = api_url
        self.download_base = download_base.rstrip("/")
        self.platform_key = platform_key or release_platform()
        self._environ = environ
        self._exe_names = exe_names or (("copilot.exe", "copilot.cmd") if sys.platform == "win32" else ("copilot",))      # a test installs a stand-in under its own name
        self.install_job: Optional[Install] = None
        self.login: Optional[Login] = None
        self._latest: tuple = ("", 0.0)
        self._latest_task: Optional[asyncio.Task] = None
        self._models: tuple = ([], 0.0)
        self._models_task: Optional[asyncio.Task] = None
        self._start_check = StartCheck()
        self.warning = ""
        self.inflight = 0
        self._gate = asyncio.Semaphore(MAX_CONCURRENT)
        self._short_flags = False                             # the program refused a flag once: it gets the short list from now on
        self._size = program_uninstall.SizeCache()

    # ---- where things are ---------------------------------------------------------------------------------------
    @property
    def bin_dir(self) -> Path: return self.folder / "bin"

    @property
    def home(self) -> Path: return self.folder / "home"

    @property
    def state_file(self) -> Path: return self.folder / "state.json"

    @property
    def exe(self) -> Path:
        rel = self._state().get("exe")
        if rel:
            p = (self.bin_dir / str(rel)).resolve()
            if str(p).startswith(str(self.bin_dir.resolve()) + os.sep):
                return p
        return self.bin_dir / self._exe_names[0]

    def _say(self, message: str) -> None:
        self.log.info(mask(message))

    # ---- state ------------------------------------------------------------------------------------------------------
    def _state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, **changes) -> None:
        data = self._state()
        data.update(changes)
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def version(self) -> str:
        return str(self._state().get("version") or "")

    def installed(self) -> bool:
        return self.exe.is_file() and bool(self.version())

    def signed_in(self) -> bool:
        return bool(self._state().get("signedIn"))

    def state(self) -> str:
        job, lg = self.install_job, self.login
        if job and job.state == "running":
            return "installing"
        if not self.installed():
            return "not_installed"
        if lg and lg.state in ("starting", "code"):
            return "signing_in"
        if not self.signed_in():
            return "needs_signin"
        return "ready"

    def latest_known(self) -> str:
        return self._latest[0]

    def update_available(self) -> bool:
        have, new = version_tuple(self.version()), version_tuple(self._latest[0])
        return bool(have and new and new > have)

    def status_json(self, kick: bool = True) -> dict:
        """What the dashboard row needs. Never waits on the network: the newest version is a remembered answer, refreshed in the background (at most hourly)."""
        if kick and self.installed():
            self.refresh_latest_soon()
            self.refresh_models_soon()
        job, lg = self.install_job, self.login
        st = self.state()
        return {"state": st, "installed": self.installed(), "version": self.version() or None, "latestVersion": self._latest[0] or None,
                "updateAvailable": self.update_available() and st != "installing", "signedIn": self.signed_in(),
                "install": job.to_json() if job else None, "signin": lg.to_json() if lg and lg.state in ("starting", "code") else None,
                "warning": self.warning or (job.error if job and job.state == "error" else "") or (lg.error if lg and lg.state == "error" else ""),
                "models": [{"id": m, "name": label(m)} for m in (self._models[0] or [MODEL_PREFIX + DEFAULT_MODEL])] if st == "ready" else [], "busy": self.inflight > 0,
                "platform": self.platform_key, "diskBytes": self._size.get(self.folder) if self.installed() else 0}

    # ---- the newest version ------------------------------------------------------------------------------------------
    def refresh_latest_soon(self, force: bool = False) -> None:
        if self._latest_task and not self._latest_task.done():
            return
        if not force and self._latest[0] and time.monotonic() - self._latest[1] < LATEST_CACHE_SECONDS:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._latest_task = loop.create_task(self._refresh_latest())

    async def _refresh_latest(self) -> None:
        try:
            tag, _ = await self.fetch_latest()
            self._latest = (tag, time.monotonic())
            self.changed()
        except CopilotError:
            self._latest = (self._latest[0], time.monotonic())

    async def fetch_latest(self) -> tuple:
        """(version, {file: size}) of the newest release, from GitHub's release list."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={"Accept": "application/vnd.github+json", "User-Agent": "MultiProxy-Courier"}) as s:
                async with s.get(self.api_url) as r:
                    if r.status != 200:
                        raise CopilotError(502, LINE_DOWNLOAD)
                    data = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            raise CopilotError(502, LINE_DOWNLOAD)
        tag = str((data or {}).get("tag_name") or "").lstrip("v")
        if not is_version(tag):
            raise CopilotError(502, LINE_DOWNLOAD)
        sizes = {str(a.get("name")): int(a.get("size") or 0) for a in (data.get("assets") or []) if isinstance(a, dict)}
        return tag, sizes

    # ---- install / update -----------------------------------------------------------------------------------------------
    def start_install(self, updating: bool = False) -> Install:
        if self.install_job and self.install_job.state == "running":
            raise CopilotError(409, "Copilot is already installing on <PC>.")
        if updating and not self.installed():
            raise CopilotError(409, LINE_NOT_INSTALLED)
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
        staging = self.folder / "bin.new"
        archive = self.folder / "download.part"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            job.step = "Finding the newest version"
            self.changed()
            version, sizes = await self.fetch_latest()
            self._latest = (version, time.monotonic())
            job.version = version
            name = asset_name(self.platform_key)
            size = sizes.get(name, 0)
            if not size or "SHA256SUMS.txt" not in sizes:
                raise CopilotError(502, LINE_DOWNLOAD)
            sums = parse_sums(await self._get_text(f"{self.download_base}/v{version}/SHA256SUMS.txt"))
            checksum = sums.get(name, "")
            if not checksum:
                raise CopilotError(502, LINE_DOWNLOAD)
            job.total, job.done = size, 0
            job.step = "Downloading"
            self.changed()
            await self._download(f"{self.download_base}/v{version}/{name}", archive, job)
            job.step = "Checking the download"
            self.changed()
            await asyncio.to_thread(self._check_file, archive, size, checksum)
            job.step = "Unpacking"
            self.changed()
            shutil.rmtree(staging, ignore_errors=True)
            try:
                await asyncio.to_thread(safe_extract, archive, staging)
            except (ValueError, OSError, zipfile.BadZipFile, tarfile.TarError):
                shutil.rmtree(staging, ignore_errors=True)
                raise CopilotError(502, LINE_CHECKSUM)
            archive.unlink(missing_ok=True)
            found_exe = find_program(staging, self._exe_names)
            if not found_exe:
                raise CopilotError(500, LINE_NO_PROGRAM)
            job.step = "Starting it to check"
            self.changed()
            if sys.platform != "win32":
                found_exe.chmod(0o755)
            found = await self._run_version(found_exe)
            if not version_tuple(found):
                raise CopilotError(500, self._wont_start())
            rel = found_exe.relative_to(staging).as_posix()
            old = self.folder / "bin.old"
            try:
                shutil.rmtree(old, ignore_errors=True)
                if self.bin_dir.exists():                      # an update: the old program is kept until the new one is in place
                    os.replace(self.bin_dir, old)
                os.replace(staging, self.bin_dir)
            except PermissionError:
                if old.exists() and not self.bin_dir.exists():
                    os.replace(old, self.bin_dir)
                raise CopilotError(409, LINE_BUSY)
            shutil.rmtree(old, ignore_errors=True)
            self._write_state(version=found, exe=rel)
            self._models = ([], 0.0)
            job.state, job.step = "done", "Installed"
            self._say(f"Copilot {found} was installed on this device.")
        except asyncio.CancelledError:
            job.state, job.step = "cancelled", "Cancelled"
            archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            self._say("Installing Copilot was cancelled.")
        except CopilotError as e:
            job.state, job.error = "error", e.message
            archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            self._say(f"Installing Copilot failed: {e.message}")
        except Exception as e:                                 # never let a surprise leave the row stuck on "installing"
            job.state, job.error = "error", LINE_DOWNLOAD
            archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            self.log.error(mask(f"Installing Copilot failed: {e.__class__.__name__}: {e}"))
        finally:
            self.changed()

    async def _get_text(self, url: str) -> str:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), headers={"User-Agent": "MultiProxy-Courier"}) as s:
                async with s.get(url) as r:
                    if r.status != 200:
                        raise CopilotError(502, LINE_DOWNLOAD)
                    return await r.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            raise CopilotError(502, LINE_DOWNLOAD)

    async def _download(self, url: str, dest: Path, job: Install) -> None:
        """Into `dest` (a .part file, resumed when one is already there), with the real progress."""
        have = dest.stat().st_size if dest.exists() else 0
        if have > job.total:
            dest.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-", "User-Agent": "MultiProxy-Courier"} if have else {"User-Agent": "MultiProxy-Courier"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)) as s:
                async with s.get(url, headers=headers) as r:
                    if r.status == 200 and have:               # the server ignored the range: start again
                        have = 0
                    elif r.status not in (200, 206):
                        raise CopilotError(502, LINE_DOWNLOAD)
                    job.done = have
                    with open(dest, "ab" if have else "wb") as f:
                        async for chunk in r.content.iter_chunked(256 * 1024):
                            f.write(chunk)
                            job.done += len(chunk)
                            self.changed()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            raise CopilotError(502, LINE_DOWNLOAD)

    @staticmethod
    def _check_file(path: Path, size: int, checksum: str) -> None:
        bad = CopilotError(502, LINE_CHECKSUM)
        if path.stat().st_size != size:
            path.unlink(missing_ok=True)
            raise bad
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest().lower() != checksum.lower():
            path.unlink(missing_ok=True)
            raise bad

    async def _run_version(self, exe: Path) -> str:
        self._start_check = await check_start(exe, child_env(self.home, self._environ))
        return self._start_check.version

    def _wont_start(self) -> str:
        """The line for the program that was just started and did not work, from what it printed. The log keeps the first lines it said (a version check prints no secret)."""
        chk = self._start_check
        line = explain_start_failure("Copilot", self.folder, chk)
        shown = " | ".join(x.strip() for x in (chk.output or "").splitlines() if x.strip())[:300]
        self._say(f"Copilot didn't start when checked (exit code {chk.returncode}{', timed out' if chk.timed_out else ''}{', ' + str(chk.error) if chk.error else ''}). It printed: {mask(shown) or 'nothing'}")
        return line

    # ---- sign in / out ---------------------------------------------------------------------------------------------------
    def start_signin(self) -> Login:
        """Ask the program for a sign-in code. The row shows the code as soon as the program prints it, then waits for the program to finish."""
        if not self.installed():
            raise CopilotError(409, LINE_NOT_INSTALLED)
        if self.login and self.login.state in ("starting", "code"):
            return self.login
        lg = Login()
        self.login = lg
        self.warning = ""
        lg.task = asyncio.get_running_loop().create_task(self._login(lg))
        self.changed()
        return lg

    def cancel_signin(self) -> bool:
        lg = self.login
        if not lg or lg.state not in ("starting", "code"):
            return False
        lg.state = "cancelled"
        if lg.task:
            lg.task.cancel()
        self.changed()
        return True

    async def _login(self, lg: Login) -> None:
        env = child_env(self.home, self._environ)
        proc = None
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="copilot-login-", ignore_cleanup_errors=True) as tmp:
                proc = await hidden_process.create_subprocess_exec(str(self.exe), "login", "--device-code", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                                   stderr=asyncio.subprocess.STDOUT, env=env, cwd=tmp, limit=1024 * 1024)
                lg.proc = proc
                lines: list = []
                started = time.monotonic()

                async def read() -> None:
                    while True:
                        raw = await proc.stdout.readline()
                        if not raw:
                            break
                        text = clean_line(raw.decode("utf-8", "replace"))
                        if not text:
                            continue
                        lines.append(text)
                        if not lg.code:
                            m = CODE_RE.search(text)
                            if m:
                                lg.code = m.group(1)
                                u = next((URL_RE.search(x) for x in reversed(lines) if URL_RE.search(x)), None)
                                lg.url = u.group(0).rstrip(".,)") if u else VERIFY_URL
                                lg.state = "code"
                                self.changed()
                reader = asyncio.create_task(read())
                waiter = asyncio.create_task(proc.wait())
                try:
                    while not waiter.done():
                        waited = time.monotonic() - started
                        if waited > LOGIN_SECONDS or (not lg.code and waited > CODE_WAIT_SECONDS):
                            proc.kill()
                            await proc.wait()
                            raise CopilotError(504, LINE_CODE_RAN_OUT if lg.code else LINE_NO_CODE)
                        await asyncio.wait({waiter}, timeout=0.2)
                    await asyncio.wait_for(reader, timeout=5)
                except asyncio.TimeoutError:
                    pass
                finally:
                    if proc.returncode is None:
                        proc.kill()
                        await proc.wait()
                    for t in (reader, waiter):
                        if not t.done():
                            t.cancel()
                if proc.returncode != 0:
                    self.log.info(mask(f"Copilot's sign-in ended on this device (exit {proc.returncode}): {first_line(chr(10).join(lines[-3:]))}"))
                    raise CopilotError(502, LINE_LOGIN_FAILED if lg.code else LINE_NO_CODE)
            self._write_state(signedIn=True)
            self.warning = ""
            self._models = ([], 0.0)
            lg.state = "done"
            self._say("Copilot was signed in on this device.")
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                proc.kill()
            lg.state = "cancelled"
            self._say("Signing in to Copilot was cancelled.")
        except CopilotError as e:
            lg.state, lg.error = "error", e.message
            self._say(f"Signing in to Copilot failed: {e.message}")
        except OSError as e:
            lg.state, lg.error = "error", explain_start_failure("Copilot", self.folder, StartCheck(error=e))
            if proc and proc.returncode is None:
                proc.kill()
        except Exception as e:
            lg.state, lg.error = "error", LINE_LOGIN_FAILED
            self.log.error(mask(f"Signing in to Copilot failed: {e.__class__.__name__}: {e}"))
        finally:
            lg.proc = None
            self.changed()

    async def _logout(self) -> None:
        """Ask the program to forget its own sign-in (wherever it kept it). Best effort: whatever it wrote in Courier's folder goes with the folder anyway."""
        if not self.exe.is_file():
            return
        try:
            proc = await hidden_process.create_subprocess_exec(str(self.exe), "logout", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                                                               stderr=asyncio.subprocess.DEVNULL, env=child_env(self.home, self._environ))
            await asyncio.wait_for(proc.wait(), timeout=LOGOUT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            pass

    async def sign_out(self) -> dict:
        self.cancel_signin()
        await self._logout()
        shutil.rmtree(self.home, ignore_errors=True)         # whatever the program wrote about this sign-in goes too
        self._write_state(signedIn=False)
        self.warning = ""
        self._models = ([], 0.0)
        self.login = None
        self._say("Copilot was signed out on this device.")
        self.changed()
        return self.status_json(False)

    async def uninstall(self) -> dict:
        """Remove the program and everything it wrote (this device's whole Copilot folder, and nothing else). Busy answers a plain line and does nothing."""
        job, lg = self.install_job, self.login
        if self.inflight > 0 or (job and job.state == "running") or (lg and lg.state in ("starting", "code")):
            raise CopilotError(409, LINE_BUSY)
        for t in (self._latest_task, self._models_task):
            if t and not t.done():
                t.cancel()
        await self._logout()
        self.warning = ""
        self._models = ([], 0.0)
        self.install_job = None
        self.login = None
        self._size.forget()
        try:
            freed = await asyncio.to_thread(program_uninstall.remove_program, self.folder.parent, self.folder)
        except program_uninstall.UninstallError as e:
            self.changed()
            raise CopilotError(e.status, e.message)
        self._say(f"Copilot was uninstalled from this device (freed {freed} bytes).")
        self.changed()
        return {**self.status_json(False), "freedBytes": freed}

    # ---- models ---------------------------------------------------------------------------------------------------------------
    def refresh_models_soon(self) -> None:
        if self.state() != "ready" or (self._models[0] and time.monotonic() - self._models[1] < MODELS_CACHE_SECONDS):
            return
        if self._models_task and not self._models_task.done():
            return
        try:
            self._models_task = asyncio.get_running_loop().create_task(self.models())
        except RuntimeError:
            pass

    async def models(self) -> list:
        """`copilot/<name>` for each model the program lists (remembered for ten minutes), always with `copilot/default` first (the program's own pick)."""
        if self._models[0] and time.monotonic() - self._models[1] < MODELS_CACHE_SECONDS:
            return self._models[0]
        names: list = []
        try:
            with tempfile.TemporaryDirectory(prefix="copilot-models-", ignore_cleanup_errors=True) as tmp:
                proc = await hidden_process.create_subprocess_exec(str(self.exe), "--list-models", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                                   stderr=asyncio.subprocess.DEVNULL, env=child_env(self.home, self._environ), cwd=tmp)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=LIST_MODELS_TIMEOUT)
            if proc.returncode == 0:
                names = parse_model_list((out or b"").decode("utf-8", "replace"))
        except (OSError, asyncio.TimeoutError):
            pass
        ids = [MODEL_PREFIX + DEFAULT_MODEL] + [MODEL_PREFIX + n for n in names if n != DEFAULT_MODEL]
        self._models = (ids, time.monotonic())
        self.changed()
        return ids

    # ---- asking Copilot ----------------------------------------------------------------------------------------------------------
    def handles(self, model: str) -> bool:
        return (model or "").lower().startswith(MODEL_PREFIX)

    def _classify(self, text: str, status: Optional[int] = None) -> CopilotError:
        line = mask(first_line(text))
        if _NOT_SIGNED_IN_RE.search(text or ""):
            self._write_state(signedIn=False)
            self.warning = LINE_EXPIRED
            self.changed()
            return CopilotError(503, LINE_EXPIRED, "expired")
        if _NO_PLAN_RE.search(text or ""):
            return CopilotError(403, LINE_NO_PLAN, "no_plan")
        if _LIMIT_RE.search(text or ""):
            return CopilotError(429, line or "You reached Copilot's limit.", "limit")
        if re.search(r"ENOTFOUND|ECONNREFUSED|ETIMEDOUT|network|could not resolve|unable to connect|connect (timeout|error)", text or "", re.I):
            return CopilotError(502, LINE_UNREACHABLE, "unreachable")
        return CopilotError(status or 502, line or LINE_NO_ANSWER, "upstream")

    async def _spawn(self, prompt: str, model: str, tmp: str, short: bool):
        args = [str(self.exe)]
        use_stdin = len(prompt) > ARGV_PROMPT_LIMIT
        if not use_stdin:
            args += ["-p", prompt]
        args += list(FLAGS_SHORT if short else FLAGS_FULL)
        if model and model != DEFAULT_MODEL:
            args += ["--model", model]
        proc = await hidden_process.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if use_stdin else asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                           stderr=asyncio.subprocess.PIPE, env=child_env(self.home, self._environ), cwd=tmp,
                                                           limit=16 * 1024 * 1024)
        if use_stdin:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        return proc

    async def chat(self, request: web.Request, body: dict) -> web.StreamResponse:
        """One OpenAI-style chat request, answered by one run of the signed-in program on this device (streamed or whole, as asked)."""
        model = str(body.get("model") or MODEL_PREFIX + DEFAULT_MODEL)
        model = model[len(MODEL_PREFIX):] if model.lower().startswith(MODEL_PREFIX) else model
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        if not any(m.get("role") != "system" for m in messages):
            raise CopilotError(400, "That request has no message to answer.")
        if not self.installed():
            raise CopilotError(503, LINE_NOT_INSTALLED, "not_installed")
        if not self.signed_in():
            raise CopilotError(503, LINE_NEEDS_SIGN_IN, "needs_sign_in")
        prompt, stream = prompt_from(messages), bool(body.get("stream"))
        self.inflight += 1
        self.changed()
        try:
            async with self._gate:
                started = time.time()
                with tempfile.TemporaryDirectory(prefix="copilot-ask-", ignore_cleanup_errors=True) as tmp:            # an empty folder: there is nothing for the program to look at
                    for attempt in range(2):
                        proc = await self._spawn(prompt, model, tmp, self._short_flags)
                        result = await self._relay(request, proc, model, stream, started)
                        if result is not None:
                            return result
                        if attempt == 0 and not self._short_flags:                       # refused a flag: once more with the short list, remembered
                            self._short_flags = True
                            continue
                    raise CopilotError(502, LINE_NO_ANSWER, "upstream")
        finally:
            self.inflight -= 1
            self.changed()

    async def _relay(self, request, proc, model: str, stream: bool, started: float):
        """Read the program's output as it comes. Returns the response, or None when the program refused a flag (the caller tries again with fewer)."""
        cmpl_id, created = f"chatcmpl-{uuid.uuid4().hex[:16]}", int(time.time())
        shown = model if model != DEFAULT_MODEL else DEFAULT_MODEL
        full_model = MODEL_PREFIX + shown

        def chunk(delta: dict, finish: Optional[str] = None) -> str:
            return "data: " + json.dumps({"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": full_model,
                                          "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
        out: Optional[web.StreamResponse] = None
        text_parts: list = []
        final_text = ""
        plain: list = []
        err_lines: list = []
        seen_json = False

        async def emit(t: str) -> None:
            nonlocal out
            if not t:
                return
            text_parts.append(t)
            if stream:
                if out is None:
                    out = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
                    await out.prepare(request)
                    await out.write(chunk({"role": "assistant", "content": ""}).encode())
                await out.write(chunk({"content": t}).encode())

        async def drain_err() -> None:
            data = await proc.stderr.read()
            err_lines.extend(clean_line(x) for x in data.decode("utf-8", "replace").splitlines() if clean_line(x))
        err_task = asyncio.create_task(drain_err())
        try:
            async def read_out() -> None:
                nonlocal final_text, seen_json
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    line = clean_line(raw.decode("utf-8", "replace"))
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        plain.append(line)
                        continue
                    if not isinstance(ev, dict):
                        plain.append(line)
                        continue
                    seen_json = True
                    kind, data = str(ev.get("type") or ""), ev.get("data") if isinstance(ev.get("data"), dict) else {}
                    if kind == "assistant.message_delta":
                        await emit(str(data.get("deltaContent") or ""))
                    elif kind == "assistant.message":
                        final_text = str(data.get("content") or final_text)
                    elif kind in ("error", "session.error") or (kind.endswith(".error")):
                        err_lines.append(clean_line(str(data.get("message") or ev.get("message") or "")))
            await asyncio.wait_for(read_out(), timeout=REQUEST_TIMEOUT)
            await asyncio.wait_for(proc.wait(), timeout=30)
            await err_task
        except asyncio.TimeoutError:
            proc.kill()
            err_task.cancel()
            raise CopilotError(504, LINE_NO_ANSWER, "timeout")
        except asyncio.CancelledError:
            proc.kill()
            err_task.cancel()
            raise
        if not text_parts and final_text:
            await emit(final_text)
        if not text_parts and not seen_json and plain and proc.returncode == 0:
            await emit("\n".join(plain))
        said = "\n".join(err_lines + plain[-3:])
        if not text_parts:
            if UNKNOWN_FLAG_RE.search(said) and not self._short_flags:
                return None
            self.log.info(mask(f"Copilot did not answer on this device (exit {proc.returncode}): {first_line(said)}"))
            raise self._classify(said)
        self._say(f"Copilot answered a request on this device ({full_model}, {len(''.join(text_parts))} characters, {time.time() - started:.1f} s).")
        if out is not None:
            await out.write(chunk({}, "stop").encode())
            await out.write(b"data: [DONE]\n\n")
            await out.write_eof()
            return out
        return web.json_response({"id": cmpl_id, "object": "chat.completion", "created": created, "model": full_model,
                                  "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(text_parts)}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

    async def test(self) -> dict:
        """One tiny real request: {"ok": True, "model", "seconds"} or {"ok": False, "error": the one short line}."""
        started = time.time()
        try:
            resp = await self.chat(object(), {"model": MODEL_PREFIX + DEFAULT_MODEL, "messages": [{"role": "user", "content": "Reply with the single word ok."}]})
            ok = resp.status == 200
        except CopilotError as e:
            return {"ok": False, "error": e.message, "kind": e.kind}
        return {"ok": ok, "model": MODEL_PREFIX + DEFAULT_MODEL, "seconds": round(time.time() - started, 1)}
