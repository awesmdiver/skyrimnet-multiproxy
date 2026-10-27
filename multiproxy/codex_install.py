"""Installing Codex for the ChatGPT card (2026-10-26): one button, like the Claude and Copilot rows, on this PC.

Why: `npx codex login` signs a player in without installing anything, and MultiProxy looks for an installed `codex` program. The card used to say "Not installed" and only
point at a terminal. Now it can fetch the program itself.

Route (chosen from OpenAI's public GitHub releases page, not its source): the standalone download that OpenAI publishes with every release, so Node.js is not needed. A release
lists each file's size and SHA-256 (GitHub's `digest`); a file without one is refused, and `local_models.download_file` checks both before the file is used. The newest release
is picked by its number (`rust-vX.Y.Z`; alphas and drafts are never chosen), never by GitHub's "latest" pointer. It goes into `<settings folder>/codex/current/` (a place
MultiProxy owns); nothing in PATH, the registry or the player's own Codex folders is changed. The program is started once with `--version` before it counts as installed.

Windows: the zip holds the program (`codex-<arch>-pc-windows-msvc.exe`) and the helper files it needs beside it, so the whole zip is unpacked (`unpack_archive` refuses any entry
that would land outside the folder). Linux: the tar holds one file.

The sign-in is never touched: `signed_in_file_exists` only asks whether Codex's sign-in file is there (never opens it).
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import aiohttp

import local_models

RELEASES_URL = "https://api.github.com/repos/openai/codex/releases?per_page=30"
TAG = re.compile(r"^rust-v(\d+)\.(\d+)\.(\d+)$")
DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
DOWNLOAD_HOSTS = ("https://github.com/openai/codex/releases/download/",)

# The words on the card (quoted in the handoff for the design side's Gemini pass).
LINE_SIGNED_IN_NO_PROGRAM = "You are signed in to Codex, but the program isn't installed on this PC. Press Install."
LINE_NOT_INSTALLED = "Codex isn't installed. Press Install."
LINE_DOWNLOAD = "Couldn't download Codex. Check your connection and press Install to try again."
LINE_CHECK = "The Codex download failed its safety check. Press Install to try again."
LINE_NO_BUILD = "Codex cannot be installed from here on this computer. See Help to install it yourself."
LINE_WONT_START = "Codex is installed but will not start on this PC. See Help."
LINE_BUSY = "Codex is running right now. Close it, then press Install again."


class CodexInstallError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message, self.status = message, status


def parse_tag(tag) -> tuple:
    m = TAG.match(str(tag or ""))
    return tuple(int(x) for x in m.groups()) if m else ()


def newest_release(releases) -> Optional[dict]:
    """The release with the highest `rust-vX.Y.Z` number. Alphas (`-alpha.N`), pre-releases, drafts and anything else are never chosen."""
    best, best_n = None, ()
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        n = parse_tag(r.get("tag_name"))
        if n and n > best_n:
            best, best_n = r, n
    return best


def platform_key(system: Optional[str] = None, machine: Optional[str] = None) -> Optional[str]:
    """The part of a Codex download's name that says which computer it is for, or None for a computer there is no download for."""
    system = system or sys.platform
    machine = (machine or os.environ.get("PROCESSOR_ARCHITECTURE") or __import__("platform").machine() or "").lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("amd64", "x86_64", "x64") else None
    if arch is None:
        return None
    if system.startswith("win"):
        return f"{arch}-pc-windows-msvc"
    if system.startswith("linux"):
        return f"{arch}-unknown-linux-musl"
    return None


def asset_name(key: str) -> str:
    return f"codex-{key}.exe.zip" if "windows" in key else f"codex-{key}.tar.gz"


def exe_name(key: str) -> str:
    return f"codex-{key}.exe" if "windows" in key else f"codex-{key}"


def asset_for(release: dict, key: str, hosts: tuple = DOWNLOAD_HOSTS) -> tuple:
    """(url, size, sha256) for this computer's file in the release; raises CodexInstallError when it is not listed with a checksum."""
    want = asset_name(key)
    for a in release.get("assets") or []:
        if isinstance(a, dict) and a.get("name") == want:
            m = DIGEST.match(str(a.get("digest") or ""))
            url, size = str(a.get("browser_download_url") or ""), a.get("size")
            if not m or not isinstance(size, int) or size <= 0 or not url.startswith(hosts):
                raise CodexInstallError(LINE_CHECK)
            return url, size, m.group(1)
    raise CodexInstallError(LINE_NO_BUILD)


def codex_home(environ: Optional[dict] = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("CODEX_HOME") or (Path(env.get("USERPROFILE") or env.get("HOME") or Path.home()) / ".codex"))


def signed_in_file_exists(environ: Optional[dict] = None) -> bool:
    """Whether Codex's sign-in file is on this PC. Only asks that: the file is never opened, read or logged."""
    try:
        return (codex_home(environ) / "auth.json").is_file()
    except OSError:
        return False


class Job:
    def __init__(self):
        self.state = "running"                 # running | done | error | cancelled
        self.step = "Starting"
        self.done = 0
        self.total = 0
        self.error = ""
        self.version = ""
        self.task: Optional[asyncio.Task] = None

    def to_json(self) -> dict:
        pct = int(min(100, self.done * 100 / self.total)) if self.total else 0
        return {"state": self.state, "step": self.step, "percent": pct, "error": self.error, "version": self.version}


class CodexInstaller:
    def __init__(self, folder: Path, log, changed: Callable[[], None] = lambda: None, *, releases_url: str = RELEASES_URL, key: Optional[str] = "auto",
                 hosts: tuple = DOWNLOAD_HOSTS):
        self.folder, self.log, self.changed, self.releases_url = Path(folder), log, changed, releases_url
        self.key = platform_key() if key == "auto" else key
        self.hosts = hosts
        self.job: Optional[Job] = None

    @property
    def current(self) -> Path:
        return self.folder / "current"

    def exe(self) -> Optional[Path]:
        return (self.current / exe_name(self.key)) if self.key else None

    def exe_if_installed(self) -> Optional[str]:
        e = self.exe()
        return str(e) if e and e.is_file() else None

    def running(self) -> bool:
        return bool(self.job and self.job.state == "running")

    def start(self) -> Job:
        if self.running():
            raise CodexInstallError("Codex is already installing.", 409)
        if not self.key:
            raise CodexInstallError(LINE_NO_BUILD, 409)
        self.job = Job()
        self.job.task = asyncio.get_running_loop().create_task(self._install(self.job))
        self.changed()
        return self.job

    def cancel(self) -> bool:
        if not self.running():
            return False
        self.job.task.cancel()
        return True

    async def _releases(self) -> list:
        try:
            async with local_models.https_only.session(aiohttp.ClientTimeout(total=30)) as s:
                async with local_models.https_only.get(s, self.releases_url, {"Accept": "application/vnd.github+json"}) as r:
                    if r.status != 200:
                        raise CodexInstallError(LINE_DOWNLOAD)
                    return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            raise CodexInstallError(LINE_DOWNLOAD)

    async def _run_version(self, exe: Path) -> str:
        import hidden_process
        try:
            r = await asyncio.to_thread(hidden_process.run, [str(exe), "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
        return out if r.returncode == 0 and re.search(r"\d+\.\d+\.\d+", out) else ""

    async def _install(self, job: Job) -> None:
        archive = self.folder / "download" / asset_name(self.key)
        staging = self.folder / "staging"
        try:
            job.step = "Finding the newest version"
            self.changed()
            release = newest_release(await self._releases())
            if release is None:
                raise CodexInstallError(LINE_DOWNLOAD)
            job.version = ".".join(str(x) for x in parse_tag(release["tag_name"]))
            url, size, sha = asset_for(release, self.key, self.hosts)
            job.total, job.done, job.step = size, 0, "Downloading"
            self.changed()

            def on_bytes(n: int) -> None:
                job.done = max(0, job.done + n)
                self.changed()
            async with local_models.https_only.session() as s:
                await local_models.download_file(s, url, archive, size, sha, on_bytes)       # size and SHA-256 are checked before the file is renamed into place
            job.step = "Unpacking"
            self.changed()
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            await asyncio.to_thread(local_models.unpack_archive, archive, staging)
            new_exe = staging / exe_name(self.key)
            if not new_exe.is_file():
                raise CodexInstallError(LINE_CHECK)
            if sys.platform != "win32":
                new_exe.chmod(0o755)
            job.step = "Starting it to check"
            self.changed()
            if not await self._run_version(new_exe):
                raise CodexInstallError(LINE_WONT_START, 500)
            old = self.folder / "old"
            try:
                if self.current.exists():
                    shutil.rmtree(old, ignore_errors=True)
                    os.replace(self.current, old)
                os.replace(staging, self.current)
            except PermissionError:
                raise CodexInstallError(LINE_BUSY, 409)
            shutil.rmtree(old, ignore_errors=True)
            archive.unlink(missing_ok=True)
            job.state, job.step = "done", "Installed"
            self.log.info(f"Codex {job.version} was installed into {self.current}")
        except asyncio.CancelledError:
            job.state, job.step = "cancelled", "Cancelled"
            shutil.rmtree(staging, ignore_errors=True)
            Path(str(archive) + ".part").unlink(missing_ok=True)
        except CodexInstallError as e:
            job.state, job.error = "error", e.message
            shutil.rmtree(staging, ignore_errors=True)
        except local_models.LocalModelError as e:
            job.state, job.error = "error", LINE_CHECK if "safety check" in e.message or "wrong size" in e.message else LINE_DOWNLOAD
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as e:                       # never leave the card stuck on "installing"
            job.state, job.error = "error", LINE_DOWNLOAD
            self.log.error(f"Installing Codex failed: {e.__class__.__name__}")
        finally:
            self.changed()

    def card(self, installed: bool, signed_in_file: bool) -> dict:
        """What the ChatGPT card needs to draw the install part: shown only while Codex is not installed, is installing, or the last install failed."""
        job = self.job.to_json() if self.job and self.job.state in ("running", "error") else None
        line = LINE_SIGNED_IN_NO_PROGRAM if signed_in_file else LINE_NOT_INSTALLED
        return {"show": (not installed) or bool(job), "job": job, "line": line, "canInstall": bool(self.key)}
