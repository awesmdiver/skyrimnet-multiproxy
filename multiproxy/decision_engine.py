"""The local decision model's engine manager: install, run, answer, status. One module, written so the Courier can reuse it.

What it runs: **llama.cpp's own `llama-server`** (the engine "Local models" already downloads; nothing new for the player to install), loaded with one of ggml-org's
decision models (a single GGUF file), listening to this device only on a port of its own. llama.cpp answers `POST /v1/systemone` itself, in TypeSafe's System One
format, so a decision is **passed straight through** to it: MultiProxy builds no prompt and reads no probabilities of its own any more. What MultiProxy still
adds around it: who answers first, the waiting limit, passing on, the log line, the Speeds record, the Courier relay, Run on and the fit check.

Where the files come from (pinned by size and SHA-256, resumable, cancellable, kept in the shared "kept downloads" so another device can copy them): the decision model's GGUF
from ggml-org's Hugging Face page (Apache-2.0). Credit and the list of base models: `notices.py`.

Never crowds the graphics card: **Run on** is Automatic (the card only when the model fits with room to spare), Graphics card, or System memory
(`choose_run_on`). A decision the model hasn't answered within the waiting limit is abandoned at once (`DecisionTooSlow`) and handed to the next answerer.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp

import download_meter
import gpu_choice
import hidden_process
import local_engine_flags
from local_models import GpuInfo, LocalModelError, _free_port, _pid_is_llama_server, engine_words, kill_tree_sync
from voice_downloads import DownloadStore, FileSpec, InternetSource, Source, link_or_copy
from voice_engine import Hardware, VoiceEngineError, detect_hardware, gb, _nvidia_query

GIB = 1024 ** 3

# ---------------------------------------------------------------------------------------------------------------------------------
# What is installed, and where it comes from
# ---------------------------------------------------------------------------------------------------------------------------------
_HF = "https://huggingface.co/ggml-org/{repo}/resolve/{rev}/{file}"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    blurb: str
    model: FileSpec
    graphics_bytes: int          # graphics memory it takes on the card (measured)
    memory_bytes: int            # ordinary memory while it runs (measured, on system memory: the worst case)
    extra_args: tuple = ()       # more llama-server flags this model needs (a model that reads a whole scene at once needs bigger batches than the engine's default)
    encoder: bool = False        # reads a whole scene in one pass: its physical batch size must be at least the per-request context or the engine crashes (measured)

    def files(self) -> tuple:
        return (self.model,)

    def total_bytes(self) -> int:
        return sum(f.size for f in self.files())


MODELS = {
    "kev-4b": ModelSpec(
        "kev-4b", "Kev-4B", "Recommended: accurate and fast.",
        model=FileSpec("Kev-4B-Q4_K_M.gguf", _HF.format(repo="Kev-4B-GGUF", rev="d924f2e2c3872da8b8aaf3eb4453b4126deceb79", file="Kev-4B-Q4_K_M.gguf"),
                       3033489824, "33ae6b18926502b2209a1bf7d3b61a350d65938441515c686bb87d226a19eff9"),
        graphics_bytes=int(4.3 * GIB), memory_bytes=int(7.2 * GIB)),
    "laya": ModelSpec(
        "laya", "Laya (small)", "For devices with little or no graphics memory. Much smaller and quicker, but it gets noticeably more questions wrong, most of all with long scenes. English only.",
        model=FileSpec("Laya-Q8_0.gguf", _HF.format(repo="Laya-GGUF", rev="da4b4753d62197659d8c90103cd4c43bef9afea6", file="Laya-Q8_0.gguf"),
                       449397600, "c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2"),
        graphics_bytes=int(1.3 * GIB), memory_bytes=int(1.5 * GIB),
        extra_args=("-b", "4096", "-ub", "4096"), encoder=True),            # without these a scene longer than about 1,500 characters is refused by the engine (measured)
    "lev": ModelSpec(
        "lev", "Lev", "An alternative to Kev-4B that is a little lighter on the graphics card.",
        model=FileSpec("lev-Q4_K_M.gguf", _HF.format(repo="lev-GGUF", rev="3e9286a79ae857b4e1de051c92fab6dc574581ce", file="lev-Q4_K_M.gguf"),
                       3011777440, "3f61b27c00a098dbc79ed099cca3985cbe0b63d17ac7fedebf2dca1d84f7f3a8"),
        graphics_bytes=int(3.7 * GIB), memory_bytes=int(7.2 * GIB)),        # graphics: measured on b11361 (docs/plans/2026-10-02-native-decision-models.md); memory: Kev-4B's figure, same size and family, not measured separately
}
DEFAULT_MODEL = "kev-4b"

# ---------------------------------------------------------------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------------------------------------------------------------
SLOTS = 2                              # decision requests answered side by side by llama-server (-np)
CONTEXT_PER_SLOT = 4096                # tokens one request may use per slot: a state up to 12,000 characters (about 3,000 tokens) plus the questions. A bigger
                                       # request is answered with an error by the engine and handed on (it never gets a made-up answer)
PORT_RANGE = (18900, 18949)            # this device only, apart from every other port MultiProxy uses
LOAD_TIMEOUT_SECONDS = 180
CPU_THREADS = 4                        # what the CPU measurements used: leaves the rest of the processor to the game
RUN_ON_CHOICES = ("automatic", "gpu", "cpu")

# Automatic placement. The model takes about 3.1 GB of graphics memory (2.7 GB at rest, 3.1 GB with generous settings; the 4B 3.9 GB). It is put on the card only
# when that much plus the room below is FREE right now: Skyrim alone measured about 26 of this test card's 32 GB while playing and moves around as the game streams
# textures, Higgs voice takes about 5.5 GB and a chat model several more, so a model squeezed into the last gigabyte would be the thing that pushes the game
# off the card. 1.5 GB is about half the model again: enough that a game turning the textures up a notch doesn't tip it over.
AUTO_GPU_MARGIN_BYTES = int(1.5 * GIB)

# The waiting limit: how long a decision may take, counted from the moment it arrives, before it is abandoned and handed to the next answerer. SkyrimNet gives a
# decision a budget of about ten seconds and the next answerer (TypeSafe, a stand-in AI) needs its own time, so the local model must be quick or step aside.
# Measured on the test device (see TECHNICAL.md, "Local decisions, step 1"); one setting changes it.
GPU_LIMIT_SECONDS = 0.8
CPU_LIMIT_SECONDS = 6.0
ASSUMED_LIMIT_FLOOR = 0.2              # a setting below this is ignored (nothing answers that fast through a web request)

STEP_LABELS = {"engine": "Engine", "model": "Decision model", "warmup": "First warm-up"}


DOWNLOAD_FAILED = "Couldn't download the decision model's files. Check your internet connection and try again."


def plain_install_error(message: str) -> str:
    """The reason an install stopped, in words for the player: a download that couldn't be made says so without an address or a folder in it."""
    message = message.replace("Press Download", "Press Install")
    if message.startswith("Couldn't download"):
        return message                    # already the plain reason (download_retry.full_text): never replaced by a guess about the internet
    if any(h in message for h in ("Couldn't reach the download site", "download site answered", "isn't in ", "was interrupted")):
        return DOWNLOAD_FAILED
    return message


class Unanswerable(Exception):
    """The decision model can't take this question. The decision goes to the next answerer. `reason` is plain words for the log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DecisionError(Exception):
    """A plain, player-readable reason with an HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class DecisionNotReady(DecisionError):
    def __init__(self, message: str = "The local decision model isn't running."):
        super().__init__(503, message)


class DecisionTooSlow(DecisionError):
    """Not answered within the waiting limit: abandoned, the next answerer takes it."""

    def __init__(self, limit: float):
        super().__init__(503, f"The local decision model didn't answer within {limit:g} seconds.")
        self.limit = limit


def choose_run_on(setting: str, hw: Hardware, need_bytes: int, engine_kind: str = "", margin: int = AUTO_GPU_MARGIN_BYTES) -> tuple:
    """("gpu" | "cpu", the reason in plain words). It never refuses: with no graphics card, or no room, it runs on system memory."""
    gpu = hw.gpu
    if setting == "cpu":
        return "cpu", "You chose system memory."
    if not gpu:
        return "cpu", "Running on system memory (no graphics card found)."
    if engine_kind == "cpu":
        return "cpu", "Running on system memory."
    if setting == "gpu":
        return "gpu", "You chose the graphics card."
    free = hw.free_vram_bytes
    if free is None:
        return "cpu", "Running on system memory to leave your graphics card free for the game. You can choose the graphics card manually."
    if free >= need_bytes + margin:
        return "gpu", f"{gb(free)} of graphics memory is free. You have room to spare."
    return "cpu", "Running on system memory (the graphics card is too full)."


def limit_for(mode: str, setting: float = 0.0) -> float:
    """The waiting limit in seconds: the player's setting when there is one, otherwise the default for where it runs."""
    if setting and setting >= ASSUMED_LIMIT_FLOOR:
        return float(setting)
    return GPU_LIMIT_SECONDS if mode == "gpu" else CPU_LIMIT_SECONDS


def read_answer(data) -> dict:
    """The one reading of a /v1/systemone reply ({"answers", "usage"}), for every answerer that speaks that shape (the local engine, Ollama's decision models): the answers
    must be an object, and the usage is cut down to the two counts. Anything else is a DecisionError, which counts as "didn't answer"."""
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise DecisionError(502, "The decision engine gave an answer SkyrimNet couldn't read.")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {"answers": answers, "usage": {"input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": int(usage.get("output_tokens") or 0)}}


@dataclass
class InstallJob:
    state: str = "running"               # running | done | error | cancelled
    step: str = "engine"                 # engine | model | warmup
    done: int = 0
    total: int = 0
    error: str = ""
    runner_only: bool = False            # only the runner software is being fetched (the model's files are already there and are left alone)
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    meter: Any = field(default_factory=download_meter.Meter, repr=False)      # the speed really measured: the time left comes from it
    pausing: bool = field(default=False, repr=False)                          # set by Pause so the stop is told apart from Cancel

    def to_json(self) -> dict:
        return {"state": self.state, "step": self.step, "done": self.done, "total": self.total, "error": self.error, "runnerOnly": self.runner_only,
                "percent": int(self.done * 100 / self.total) if self.total else 0,
                "left": self.meter.text(self.done, self.total) if self.state == "running" and (self.step == "model" or self.runner_only) else ""}


def source_for(spec: FileSpec) -> Source:
    """Where a file comes from: the internet (tests hand in their own source)."""
    return InternetSource()


def physical_batch(cmd: list) -> int:
    """The physical batch size a llama-server command ends up with: `-ub` (default 512), never more than `-b` (default 2048); the last of a repeated flag wins."""
    b, ub = 2048, 512
    for i, tok in enumerate(cmd[:-1]):
        if str(cmd[i + 1]).isdigit():
            if tok in ("-b", "--batch-size"):
                b = int(cmd[i + 1])
            elif tok in ("-ub", "--ubatch-size"):
                ub = int(cmd[i + 1])
    return min(ub, b)


def batch_floor_problem(cmd: list, spec: "ModelSpec", per_request_tokens: int) -> Optional[str]:
    """A plain message when `cmd` would start an encoder decision model with a physical batch smaller than the scene it has to read (the engine then dies on the
    first long scene with no readable error), else None. Checked at every launch, from the real command, so no setting can slip past it."""
    if not spec.encoder:
        return None
    have = physical_batch(cmd)
    if have >= per_request_tokens:
        return None
    return (f"{spec.label} needs its batch size to be at least {per_request_tokens:,}, but this start would use {have:,}. "
            f"MultiProxy didn't start it because the engine would crash on a long scene.")


def default_launcher(exe: Path, model: Path, port: int, mode: str, slots: int, context: int) -> list:
    return [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(port), "-np", str(slots), "-c", str(context),
            "-t", str(CPU_THREADS), "--no-webui", "--alias", "decisions", "-ngl", "99" if mode == "gpu" else "0"]


def _process_token(pid: int) -> str:
    """Something that stays the same for one running process and differs for another process that is given the same number later: its start time. "" when no such process runs."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = wintypes.HANDLE
            handle = k32.OpenProcess(0x1000, False, int(pid))          # (query a little about it)
            if not handle:
                return ""
            try:
                created, a, b, c = (wintypes.FILETIME() for _ in range(4))
                code = wintypes.DWORD()
                if not k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(a), ctypes.byref(b), ctypes.byref(c)) or not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                    return ""
                return f"{created.dwHighDateTime}:{created.dwLowDateTime}"
            finally:
                k32.CloseHandle(handle)
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    try:
        out = hidden_process.run(["ps", "-p", str(int(pid)), "-o", "lstart="], capture_output=True, text=True, timeout=5).stdout.strip()
        return out
    except Exception:
        return ""


class DecisionEngine:
    def __init__(self, folder_fn: Callable[[], Path], store: DownloadStore, settings_fn: Callable[[], dict],
                 exe_fn: Callable[[], Optional[Path]], ensure_engine_fn: Callable[[Callable[[int, int], None]], Awaitable[Path]],
                 engine_kind_fn: Callable[[], str] = lambda: "", redact: Callable[[str], str] = lambda s: s,
                 hardware_fn: Optional[Callable[[Path], Hardware]] = None, launcher: Callable[..., list] = default_launcher,
                 models: Optional[dict] = None, port_range: tuple = PORT_RANGE, slots: int = SLOTS,
                 source_fn: Callable[[FileSpec], Source] = source_for,
                 gpu_choice_fn: Callable[[], Optional[dict]] = lambda: None, cards_fn: Callable[[], list] = gpu_choice.cached_cards,
                 devices_fn: Callable[[Any], list] = gpu_choice.list_devices):
        self._folder_fn = folder_fn
        self.store = store
        self._settings_fn = settings_fn
        self._exe_fn = exe_fn
        self._ensure_engine = ensure_engine_fn
        self._engine_kind_fn = engine_kind_fn
        self.redact = redact
        self._hardware_fn = hardware_fn or detect_hardware
        self._launcher = launcher
        self.models = models if models is not None else MODELS
        self._port_range = port_range
        self.slots = slots
        self._source_fn = source_fn
        self._gpu_choice_fn = gpu_choice_fn     # the remembered card choice for this AI (None = Automatic)
        self._cards_fn = cards_fn
        self._devices_fn = devices_fn
        self.gpu_note = ""                      # why Automatic was used at the last start ("" = nothing to say)
        self.gpu_card: Optional[str] = None     # name of the card the last start was kept on (None = Automatic)
        self.job: Optional[InstallJob] = None
        self.last_command: list = []        # the command the engine was last started with (for the tests and the log)
        self.state = "not_installed"
        self.detail = ""
        self.port: Optional[int] = None
        self.proc = None
        self.run_on: Optional[str] = None
        self.run_on_reason = ""
        self.started_at = 0.0
        self.warm_ms: Optional[float] = None
        self.last_ms: Optional[float] = None
        self.answered = 0
        self.limit_hits = 0
        self.passed_on = 0                 # decisions this model declined (a question it can't answer honestly)
        self.waiting = 0
        self.gpu_baseline_mb: Optional[int] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        self._stdout = None
        self._refresh_state()

    # ---- places ----
    @property
    def folder(self) -> Path:
        return Path(self._folder_fn())

    def model_key(self) -> str:
        k = str(self._settings_fn().get("model") or DEFAULT_MODEL)
        return k if k in self.models else DEFAULT_MODEL

    def spec(self, key: Optional[str] = None) -> ModelSpec:
        return self.models[key or self.model_key()]

    def model_label(self) -> str:
        """The model, as the log line names it."""
        return self.spec().label

    def model_dir(self, key: str) -> Path:
        return self.folder / key

    @property
    def state_file(self) -> Path:
        return self.folder / "decision-state.json"

    @property
    def log_file(self) -> Path:
        return self.folder / "decision-engine.log"

    @property
    def server_log(self) -> Path:
        return self.folder / "decision-server.log"

    @property
    def pid_file(self) -> Path:
        return self.folder / "decision-server.pid"

    def installed(self, key: Optional[str] = None) -> bool:
        spec = self.spec(key)
        d = self.model_dir(spec.key)
        try:
            return all((d / f.name).is_file() and (d / f.name).stat().st_size == f.size for f in spec.files())
        except OSError:
            return False

    def runner_missing(self) -> bool:
        """The model's files are all here but the program that runs them (llama.cpp's server, found by `exe_fn`) is not, or is too old for the decision model.
        The way out is to fetch the engine alone, never to download the model again."""
        try:
            return self.installed() and self._exe_fn() is None
        except Exception:
            return False

    def _refresh_state(self) -> None:
        if self.state in ("starting", "getting_ready", "ready", "installing", "died", "error"):
            return
        self.state = "stopped" if self.installed() else "not_installed"
        if self.state == "not_installed":
            self.detail = ""

    # ---- log ----
    def log(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.redact(message)}"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def log_tail(self, lines: int = 80) -> str:
        out = []
        for p in (self.log_file, self.server_log):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-20000:].splitlines()[-lines:]
                out.append(f"--- {p.name} ---\n" + "\n".join(self.redact(x) for x in tail))
            except OSError:
                continue
        return "\n".join(out)

    # ---- the fit check: never a refusal over memory ----
    def _choice(self) -> Optional[dict]:
        try:
            c = self._gpu_choice_fn()
        except Exception:
            return None
        return c if isinstance(c, dict) and c.get("id") else None

    def chosen_card(self):
        """The card remembered for this AI if this device still has it, else None (Automatic). No choice stored: no card is read at all."""
        choice = self._choice()
        if not choice:
            return None
        try:
            return next((c for c in self._cards_fn() if c.id == choice["id"]), None)
        except Exception:
            return None

    def hardware(self) -> Hardware:
        hw = self._hardware_fn(self.folder)
        card = self.chosen_card() if self._choice() else None
        return gpu_choice.apply_card(hw, card) if card is not None else hw

    def fit_check(self) -> dict:
        spec = self.spec()
        hw = self.hardware()
        engine_missing = self._exe_fn() is None
        need_disk = (0 if self.installed() else self.store.missing_bytes(list(spec.files()))) + (700 * 1024 * 1024 if engine_missing else 0) + 256 * 1024 * 1024
        reasons, warnings = [], []
        if not shutil_supported():
            reasons.append("The local decision model runs on Windows and Linux devices.")
        if hw.free_disk_bytes is not None and hw.free_disk_bytes < need_disk and not self.installed():
            reasons.append(f"There isn't enough room on that drive. The decision model needs about {gb(need_disk)} free, but only {gb(hw.free_disk_bytes)} is free.")
        if hw.free_ram_bytes is not None and hw.free_ram_bytes < spec.memory_bytes:
            warnings.append(f"Only about {gb(hw.free_ram_bytes)} of system memory is free, and the decision model likes about {gb(spec.memory_bytes)}. Close other programs if it won't start.")
        mode, why = choose_run_on(str(self._settings_fn().get("run_on") or "automatic"), hw, spec.graphics_bytes, self._engine_kind_fn())
        if mode == "cpu" and not hw.gpu:
            warnings.append("No graphics card was found. It will run on the processor: about a second per question, fine for background decisions.")
        verdict = "refuse" if reasons else ("warn" if warnings else "ok")
        return {"verdict": verdict, "reasons": reasons, "warnings": warnings, "needDiskBytes": need_disk, "runOn": mode, "runOnReason": why,
                "graphics": {"name": hw.gpu.name if hw.gpu else None, "totalMb": hw.gpu.memory_mb if hw.gpu else None,
                             "freeMb": int(hw.free_vram_bytes / 1024 / 1024) if hw.free_vram_bytes is not None else None},
                "model": spec.key, "modelBytes": spec.total_bytes()}

    # ---- install ----
    def start_install(self) -> InstallJob:
        if self.job and self.job.state == "running":
            raise DecisionError(409, "The decision model is already installing.")
        fit = self.fit_check()
        if fit["verdict"] == "refuse":
            raise DecisionError(400, " ".join(fit["reasons"]))
        spec = self.spec()
        job = InstallJob(total=spec.total_bytes())
        self.job = job
        self._set_paused(False)
        self.state, self.detail = "installing", ""
        job.task = asyncio.ensure_future(self._install(job, spec))
        return job

    def start_runner_install(self) -> InstallJob:
        """Get only the runner software for a model that is already installed. The model's files are not touched and not downloaded again."""
        if self.job and self.job.state == "running":
            raise DecisionError(409, "The decision model is already installing.")
        if not self.installed():
            raise DecisionError(409, "The decision model isn't installed yet.")
        if self._exe_fn() is not None:
            raise DecisionError(409, "The engine software is already here.")
        job = InstallJob(total=0, runner_only=True)
        self.job = job
        self._set_paused(False)
        self.state, self.detail = "installing", ""
        job.task = asyncio.ensure_future(self._install_runner(job))
        return job

    async def _install_runner(self, job: InstallJob) -> None:
        def progress(done: int, total: int) -> None:
            job.meter.observe(done)
            job.done, job.total = done, total

        try:
            self.log("Getting the engine software only (the model's files stay as they are)")
            await self._stop_quietly()
            job.step = "engine"
            await self._ensure_engine(progress)
            job.state = "done"
            self.state, self.detail = "stopped", ""
            self.log("The engine software is in place")
        except asyncio.CancelledError:
            job.state = "paused" if job.pausing else "cancelled"
            self.state = "stopped"
            self.log("Getting the engine was cancelled (what was downloaded is kept, so it carries on from there)")
            raise
        except (LocalModelError, DecisionError) as e:
            job.state, job.error = "error", plain_install_error(getattr(e, "message", str(e)))
            self.state = "stopped"
            self.log(f"Getting the engine failed: {job.error}")
        except Exception as e:
            job.state, job.error = "error", "Something went wrong while getting the engine software. Its log has the details."
            self.state = "stopped"
            self.log(f"Getting the engine failed: {e.__class__.__name__}: {e}")

    async def _install(self, job: InstallJob, spec: ModelSpec) -> None:
        def on_bytes(n: int) -> None:
            job.meter.note(n)
            job.done = max(0, job.done + n)

        try:
            self.log(f"Install started ({spec.key})")
            await self._stop_quietly()
            job.step = "engine"
            await self._ensure_engine(lambda done, total: None)
            d = self.model_dir(spec.key)
            d.mkdir(parents=True, exist_ok=True)
            job.step = "model"
            for f in spec.files():
                kept = await self.store.get(f, self._source_fn(f), on_bytes)
                how = await asyncio.to_thread(link_or_copy, kept, d / f.name)
                self.log(f"{f.name} in place ({how})")
            self._write_state(installed=True, model=spec.key, installed_at=int(time.time()))
            job.step = "warmup"
            await self.start()
            if self.state == "ready":
                job.state = "done"
            else:
                job.state, job.error = "error", self.detail or "The decision model didn't start."
        except asyncio.CancelledError:
            job.state = "paused" if job.pausing else "cancelled"
            self.state = "stopped" if self.installed() else "not_installed"
            self.log("Install cancelled (what was downloaded is kept, so it carries on from there)")
            raise
        except (LocalModelError, DecisionError) as e:
            job.state, job.error = "error", plain_install_error(getattr(e, "message", str(e)))
            self.state = "stopped" if self.installed() else "not_installed"
            self.log(f"Install failed: {job.error}")
        except Exception as e:
            job.state, job.error = "error", "Something went wrong while installing the decision model. Its log has the details."
            self.state = "stopped" if self.installed() else "not_installed"
            self.log(f"Install failed: {e.__class__.__name__}: {e}")

    def cancel_install(self) -> bool:
        paused = self._paused_now()
        self._set_paused(False)
        if self.job and self.job.state == "running" and self.job.task:
            self.job.task.cancel()
            return True
        return paused

    # ---- Pause and Resume (2026-10-26): stop fetching now and keep the partial files; Resume is the ordinary install call, which carries on from the byte it reached.
    # "Paused" is saved in the state file, so it still reads Paused after MultiProxy is closed and opened again (while the partial file is on disk).
    def pause_install(self) -> bool:
        if self.job and self.job.state == "running" and self.job.task and self.job.step != "warmup":
            self.job.pausing = True
            self.job.task.cancel()
            self._set_paused(True)
            return True
        return False

    def _set_paused(self, on: bool) -> None:
        if bool(self._read_state().get("paused")) != on:
            self._write_state(paused=on)

    def _paused_now(self) -> bool:
        return bool(self._read_state().get("paused")) and not (self.job and self.job.state == "running")

    def paused_json(self) -> Optional[dict]:
        """The bar of a paused install, read from the disk: None when nothing is paused or the partial file is gone."""
        if not self._paused_now() or self.installed():
            return None
        done = sum(p["size"] for p in self.store.partials())
        if done <= 0:
            self._set_paused(False)
            return None
        total = self.spec().total_bytes()
        return {"done": min(done, total) if total else done, "total": total}

    # ---- state file ----
    def _read_state(self) -> dict:
        try:
            d = json.loads(self.state_file.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, **changes) -> None:
        d = self._read_state()
        d.update(changes)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(d, indent=1), encoding="utf-8")

    # ---- run ----
    def _read_pid_file(self) -> dict:
        """What the pid file says: {"engine": the engine's process number, "owner": the number of the MultiProxy (or Courier) process that started it, "token": that process's start
        mark}. An older file holds only the engine's number (no owner)."""
        try:
            raw = self.pid_file.read_text().strip()
        except OSError:
            return {}
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                return {"engine": int(d.get("engine")), "owner": int(d.get("owner") or 0), "token": str(d.get("token") or "")}
            return {"engine": int(d)}
        except (ValueError, TypeError):
            return {}

    def _owns_pid_file(self) -> bool:
        d = self._read_pid_file()
        return not d or d.get("owner", 0) in (0, os.getpid())

    def cleanup_leftover(self) -> None:
        """An engine left over from an EARLIER run: its owner (the MultiProxy that started it) is gone, so it is stopped. An engine that a MultiProxy which is still running started
        (another copy using the same folder, a test copy) is never touched, and neither is the one this very process started."""
        d = self._read_pid_file()
        pid = d.get("engine")
        if not pid:
            if self.pid_file.exists():
                self.pid_file.unlink(missing_ok=True)
            return
        owner = d.get("owner", 0)
        if owner == os.getpid():
            return                                                      # this process's own engine
        if owner and d.get("token") and _process_token(owner) == d["token"]:
            self.log(f"Left alone: the decision engine (process ID {pid}) belongs to another MultiProxy that is still running (process ID {owner}).")
            return
        if _pid_is_llama_server(pid):
            kill_tree_sync(pid)
            self.log(f"Stopped (leftover from before): a decision engine from an earlier run (process ID {pid})")
        self.pid_file.unlink(missing_ok=True)

    def leftover_running(self) -> bool:
        """Is a decision engine from an earlier run still running while this process holds none? (The pid file names it and that process is alive: cheap, no program started.)
        It is only shown to the player, never stopped by itself."""
        if self.proc is not None and self.proc.poll() is None:
            return False
        pid = self._read_pid_file().get("engine")
        return bool(pid) and bool(_process_token(pid))

    def _find_by_command_line(self) -> list:
        """Process numbers of llama-servers started for decisions: one of this manager's own model files after `-m` AND a port in the decision range after `--port`.
        Only used when the pid file does not name a live engine; a program that matches only one of the two is not ours."""
        try:
            if os.name == "nt":
                out = hidden_process.run(["powershell", "-NoProfile", "-Command",
                                          "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
                                         capture_output=True, text=True, timeout=20).stdout
            else:
                out = hidden_process.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return []
        mine = str(self.folder).lower().replace("/", "\\")
        found = []
        for line in out.splitlines():
            parts = line.strip().split(None, 1) if os.name != "nt" else line.strip().split(chr(9), 1)
            if len(parts) != 2 or not parts[0].isdigit() or "llama-server" not in parts[1].lower():
                continue
            m = re.search(r"--port\s+(\d+)", parts[1])
            if not m or not (self._port_range[0] <= int(m.group(1)) <= self._port_range[1]):
                continue
            if mine and mine in parts[1].lower().replace("/", "\\"):
                found.append(int(parts[0]))
        return found

    def stop_leftover(self, by_button: bool = False) -> str:
        """Stop the one decision engine left from an earlier run. "stopped", "none" (nothing was running) or "unsure" (it cannot be told which program it is, or it belongs to a
        MultiProxy that is still running: nothing was stopped). The player's chat model and voice engine run the same program and are never touched.
        Only the engine the pid file names (the id MultiProxy recorded) is stopped on its own. A program found by its command line is stopped only when the player pressed
        Shut down for it (`by_button`); Move decisions, Cloud only and every other automatic path pass False and stop nothing without a record."""
        d = self._read_pid_file()
        pid = d.get("engine")
        if pid and _process_token(pid):
            owner = d.get("owner", 0)
            if owner and owner != os.getpid() and d.get("token") and _process_token(owner) == d["token"]:
                return "unsure"
            if not _pid_is_llama_server(pid):
                return "unsure"
        else:
            if not by_button:
                return "none"                                           # no record: nothing is looked up by its command line unless the player pressed the button
            found = self._find_by_command_line()
            if not found:
                self.pid_file.unlink(missing_ok=True)
                return "none"
            if len(found) != 1:
                return "unsure"
            pid = found[0]
        kill_tree_sync(pid)
        self.pid_file.unlink(missing_ok=True)
        self.log(f"Stopped (a decision engine left running from an earlier run, process ID {pid})")
        return "stopped"

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=3))
        return self._session

    def _gpu_used_mb(self) -> Optional[int]:
        row = _nvidia_query("memory.used")
        try:
            return int(float(row[0])) if row else None
        except (ValueError, TypeError):
            return None

    async def start(self) -> None:
        async with self._lock:
            if self.state in ("ready", "getting_ready", "starting") and self.proc and self.proc.poll() is None:
                return
            spec = self.spec()
            if not self.installed():
                raise DecisionError(409, "The decision model isn't installed yet.")
            exe = self._exe_fn()
            if exe is None:
                raise DecisionError(409, "The engine software is missing. Press Get the engine.")
            await self._stop_quietly(keep_state=True)
            hw = await asyncio.to_thread(self.hardware)
            self.run_on, self.run_on_reason = choose_run_on(str(self._settings_fn().get("run_on") or "automatic"), hw, spec.graphics_bytes, self._engine_kind_fn())
            self.port = _free_port(*self._port_range)
            self.state, self.detail = "starting", "Loading the model…"
            self.gpu_baseline_mb = self._gpu_used_mb() if self.run_on == "gpu" else None
            cmd = self._launcher(exe, self.model_dir(spec.key) / spec.model.name, self.port, self.run_on, self.slots, CONTEXT_PER_SLOT * self.slots) + list(spec.extra_args)
            plan = gpu_choice.Plan()
            choice = self._choice()
            if choice and self.run_on == "gpu":       # a decision model that runs on the processor has no use for a card
                try:
                    plan = await asyncio.to_thread(gpu_choice.plan_for, choice, self._cards_fn(), self._engine_kind_fn(), exe, self._devices_fn)
                except Exception:
                    plan = gpu_choice.Plan()
            self.gpu_note, self.gpu_card = plan.note, (plan.card.name if plan.card else None)
            if plan.note:
                self.log(plan.note)
            cmd = list(cmd) + list(plan.args)
            self.last_command = cmd
            problem = batch_floor_problem(cmd, spec, CONTEXT_PER_SLOT)
            if problem:
                self.state, self.detail = "error", problem
                self.log(problem)
                return
            self.log(f"Starting on port {self.port} ({'graphics card' if self.run_on == 'gpu' else 'system memory'}: {self.run_on_reason})")
            kwargs: dict = {}
            if os.name == "nt":
                kwargs["creationflags"] = 0x00000200 | (0x00004000 if self.run_on == "cpu" else 0)       # its own process group; below-normal priority when it uses the processor
                kwargs["env"] = local_engine_flags.clean_environment(gpu_choice.merge_env(None, plan))     # no LLAMA_ARG_* variable the player set may change this engine
            else:
                kwargs["start_new_session"] = True
                kwargs["env"] = local_engine_flags.clean_environment(
                    gpu_choice.merge_env({**os.environ, "LD_LIBRARY_PATH": f"{exe.parent}:{os.environ.get('LD_LIBRARY_PATH', '')}".rstrip(":")}, plan))
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                self._stdout = open(self.server_log, "wb")
                self.proc = hidden_process.popen(cmd, cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=self._stdout, stderr=subprocess.STDOUT, **kwargs)
            except OSError as e:
                self.state, self.detail = "died", f"The model wouldn't start ({e.strerror or e})."
                self.log(self.detail)
                return
            self.started_at = time.time()
            try:
                self.pid_file.write_text(json.dumps({"engine": self.proc.pid, "owner": os.getpid(), "token": _process_token(os.getpid())}))
            except OSError:
                pass
            if not await self._wait_healthy():
                return
            self.state, self.detail = "getting_ready", "Warming up…"
            if not await self._warm_up():
                return
            self.state, self.detail = "ready", ""
            self.log(f"Ready after {time.time() - self.started_at:.1f} s (first answer {self.warm_ms:.0f} ms)")

    async def _wait_healthy(self) -> bool:
        session = await self._http()
        deadline = time.time() + LOAD_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._died(self.proc.returncode)
                return False
            try:
                async with session.get(f"http://127.0.0.1:{self.port}/health", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status == 200:
                        return True
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.25)
        self.state, self.detail = "error", "The decision model took too long to load. Close other programs and press Start again."
        self.log(self.detail)
        kill_tree_sync(self.proc.pid)
        return False

    def _died(self, exit_code: Optional[int]) -> None:
        alive = time.time() - self.started_at if self.started_at else 0.0
        tail, raw = "", ""
        try:
            raw = self.log_tail(30)
            tail = raw.lower()
        except Exception:
            pass
        said = [w for w in engine_words(raw) if w]
        self.state = "died"
        if any(h in tail for h in ("out of memory", "cudamalloc failed", "failed to allocate", "unable to allocate", "vk_error_out_of_device_memory")):
            self.detail = "The decision model ran out of memory. Choose System memory in Run on, or close other programs, then press Start again."
        elif said:
            self.detail = f"The decision model didn't start. The engine said: \u201c{' '.join(said[:2])[:240]}\u201d"
        elif alive < 20:
            self.detail = ("The model stopped immediately. Your antivirus or Windows Smart App Control may have blocked it. "
                           "Add the models folder to your antivirus allowlist and try again.")
        else:
            self.detail = f"The model stopped unexpectedly{f' (code {exit_code})' if exit_code not in (None, 0) else ''}. Check the log."
        self.log(f"Engine stopped (exit code {exit_code}, {alive:.1f} s after start): {self.detail}")

    async def _warm_up(self) -> bool:
        """One real question: the engine must answer in System One's shape, or this isn't a decision model and it is not used."""
        t0 = time.perf_counter()
        try:
            res = await self._ask("The guard is standing at the gate and the player has just walked up to him.",
                                  {"q": {"type": "noul", "instructions": "Is someone standing at a gate?"}})
            p = res["answers"]["q"]["noul"]
            if not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
                raise ValueError("not a probability")
        except (Unanswerable, DecisionError, aiohttp.ClientError, asyncio.TimeoutError, OSError, KeyError, IndexError, ValueError, TypeError) as e:
            if self.proc and self.proc.poll() is not None:
                self._died(self.proc.returncode)
            else:
                self.state, self.detail = "error", "The decision model started, but its first question failed. Check the log for details."
                self.log(f"Warm-up failed: {getattr(e, 'reason', None) or getattr(e, 'message', None) or e.__class__.__name__}")
            return False
        self.warm_ms = (time.perf_counter() - t0) * 1000
        self.log(f"Warm-up answer: {round(p, 3)}")
        return True

    # ---- asking the engine ----
    async def _ask(self, state: Any, questions: dict) -> dict:
        """The engine's own answer to one decision: POST /v1/systemone, passed straight through ({"answers", "usage"})."""
        session = await self._http()
        async with session.post(f"http://127.0.0.1:{self.port}/v1/systemone", json={"state": state, "questions": questions},
                                timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status == 400:                       # the engine can't take this question as it is written: a clean pass-on, never a made-up answer
                detail = ""
                try:
                    err = (await resp.json()).get("error")
                    detail = str(err.get("message") if isinstance(err, dict) else err or "")
                except (ValueError, aiohttp.ClientError, AttributeError):
                    pass
                raise Unanswerable("The decision model couldn't answer this question" + (f" ({detail[:120]})." if detail else "."))
            if resp.status != 200:
                raise DecisionError(502, f"The decision engine returned an error ({resp.status}).")
            data = await resp.json()
        return read_answer(data)

    def limit_seconds(self) -> float:
        try:
            setting = float(self._settings_fn().get("limit_seconds") or 0)
        except (TypeError, ValueError):
            setting = 0.0
        return limit_for(self.run_on or "gpu", setting)

    async def answer(self, state: Any, questions: dict, limit: Optional[float] = None) -> dict:
        """{"answers": {...}, "usage": {...}} for one decision request, or DecisionNotReady / DecisionTooSlow / Unanswerable. Never a made-up probability.
        `limit` is the time left to answer, when the caller already spent some of it (a decision relayed from the main device: the wait counts from when MultiProxy
        received it); the setting is used when it is None."""
        if self.state != "ready" or not self.proc or self.proc.poll() is not None:
            if self.proc and self.proc.poll() is not None and self.state in ("ready", "getting_ready", "starting"):
                self._died(self.proc.returncode)
            raise DecisionNotReady()
        if not isinstance(questions, dict) or not questions:
            raise Unanswerable("There were no questions.")
        limit = self.limit_seconds() if limit is None else limit
        if limit <= 0:
            self.limit_hits += 1
            raise DecisionTooSlow(self.limit_seconds())            # the time was already used up before it got here
        self.waiting += 1
        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(self._ask(state, questions), timeout=limit)
        except (asyncio.TimeoutError, TimeoutError):
            self.limit_hits += 1
            self.log(f"A decision wasn't answered within {limit:g} s and was passed on ({self.limit_hits} so far)")
            raise DecisionTooSlow(limit)
        except Unanswerable as e:
            self.passed_on += 1
            self.log(f"A decision was passed on: {e.reason}")
            raise
        except (aiohttp.ClientError, OSError) as e:
            if self.proc and self.proc.poll() is not None:
                self._died(self.proc.returncode)
            raise DecisionNotReady(f"The local decision model stopped answering ({e.__class__.__name__}).")
        finally:
            self.waiting -= 1
        self.last_ms = (time.perf_counter() - t0) * 1000
        self.answered += 1
        return res

    # ---- stop, restart, uninstall ----
    async def _stop_quietly(self, keep_state: bool = False) -> None:
        if self.proc and self.proc.poll() is None:
            await asyncio.to_thread(kill_tree_sync, self.proc.pid)
            try:
                await asyncio.to_thread(self.proc.wait, 15)
            except Exception:
                pass
        self.proc = None
        try:
            if self._owns_pid_file():                     # (another process's pid file is not ours to remove)
                self.pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        if self._stdout:
            try:
                self._stdout.close()
            except OSError:
                pass
            self._stdout = None
        if not keep_state:
            self.state, self.detail = ("stopped", "") if self.installed() else ("not_installed", "")
        self.run_on = None if not keep_state else self.run_on

    async def stop(self, why: str = "", by_button: bool = False) -> None:
        """Stop the engine this manager started. A stop MultiProxy caused is logged as what it was ("Stopped (restart)"), never as an unexpected one.
        `by_button` is True only for the Shut down button on this device: it alone may stop an engine found by its command line."""
        async with self._lock:
            had_own = self.proc is not None and self.proc.poll() is None
            if not had_own and not why:                    # nothing of this process's own runs: the engine left from an earlier run, by the id recorded (read before the pid file is cleared)
                said = await asyncio.to_thread(self.stop_leftover, by_button)
                if said == "unsure":
                    raise DecisionError(409, "MultiProxy could not tell which program is the decision model, so nothing was stopped. Close it in Task Manager to remove it.")
                if said == "none":
                    return
            await self._stop_quietly()
        self.log(f"Stopped ({why})" if why else "Stopped")

    async def restart(self) -> None:
        await self.stop("restart")
        await self.start()

    async def uninstall(self) -> int:
        """Stop and delete every decision model on this device (not the kept downloads: clear those separately, not the llama.cpp engine: Local models owns it).
        Returns the bytes freed."""
        async with self._lock:
            await self._stop_quietly()
        freed = 0
        for key in self.models:
            d = self.model_dir(key)
            if d.is_dir():
                for p in d.rglob("*"):
                    try:
                        if p.is_file() and not p.is_symlink() and p.stat().st_nlink <= 1:
                            freed += p.stat().st_size
                    except OSError:
                        pass
                await asyncio.to_thread(shutil.rmtree, d, True)
        self.state_file.unlink(missing_ok=True)
        self.state, self.detail, self.job = "not_installed", "", None
        self.log("Uninstalled")
        return freed

    async def close(self) -> None:
        await self._stop_quietly()
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- status ----
    def status(self) -> dict:
        self._refresh_state()
        if self.proc is not None and self.proc.poll() is not None and self.state in ("ready", "getting_ready", "starting"):
            self._died(self.proc.returncode)
        gpu_mb = None
        if self.state in ("ready", "getting_ready") and self.run_on == "gpu" and self.gpu_baseline_mb is not None:
            used = self._gpu_used_mb()
            if used is not None:
                gpu_mb = max(0, used - self.gpu_baseline_mb)
        label = {"not_installed": "Not installed", "installing": "Installing", "stopped": "Stopped", "starting": "Starting", "getting_ready": "Getting ready",
                 "ready": "Running", "died": "Stopped unexpectedly", "error": "Problem"}.get(self.state, self.state)
        runner_missing = self.state in ("stopped", "died", "error") and self.runner_missing()
        if runner_missing:
            label = "Needs the engine"
        if self.state == "ready" and self.waiting:
            label = f"Busy ({self.waiting} decision{'s' if self.waiting != 1 else ''} waiting)"
        return {
            "state": self.state, "label": label, "detail": self.detail, "ready": self.state == "ready", "installed": self.installed(), "runnerMissing": runner_missing,
            "model": self.model_key(), "runOn": self.run_on, "runOnLabel": {"gpu": "Graphics card", "cpu": "System memory"}.get(self.run_on or ""),
            "runOnReason": self.run_on_reason if self.run_on else "", "gpuMemoryMb": gpu_mb, "gpuNote": self.gpu_note, "gpuCard": self.gpu_card,
            "lastAnswerMs": round(self.last_ms) if self.last_ms else None, "firstAnswerMs": round(self.warm_ms) if self.warm_ms else None,
            "answered": self.answered, "waiting": self.waiting, "limitSeconds": self.limit_seconds(), "limitHits": self.limit_hits, "passedOn": self.passed_on,
            "paused": self.paused_json(),
            "install": self.job.to_json() if self.job else None, "installStep": STEP_LABELS.get(self.job.step) if self.job and self.job.state == "running" else None,
            "downloads": self.store.listing(), "installedModels": [k for k in self.models if self.installed(k)],
            "leftoverRunning": self.leftover_running(),
        }


def shutil_supported() -> bool:
    return sys.platform.startswith("win") or sys.platform.startswith("linux")


def math_round(values: list) -> list:
    return [round(v, 3) for v in values]
