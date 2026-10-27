"""Higgs voice engine manager (2026-10-02): fit check, install, run, stop, uninstall, status. One module, written so the Courier can reuse it.

It knows nothing about MultiProxy's web app or SkyrimNet: give it a folder, a `DownloadStore` and (optionally) a log redactor, and it
installs and runs the engine ("audio.cpp", ShugoAI, Apache-2.0) with the voice model ("Higgs TTS 3", Boson AI) on this device. Nothing is
bundled or re-hosted: the engine build and the model are fetched from their own sources by the player's device (`voice_downloads.py` keeps them).

Numbers here come from `docs/plans/2026-10-01-higgs-measurements.md` (RTX 5090 test device; every limit is a constant with the reason beside it).
"""
import asyncio
import base64
import io
from contextlib import asynccontextmanager
import json
import os
import random
import re
import shutil
import struct
import sys
import threading
from collections import deque
import time
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

import download_meter
import gpu_choice
import hidden_process
import math
import program_uninstall
import safe_paths
import subprocess
from local_models import GpuInfo, LocalModelError, detect_gpu, kill_tree_sync, unpack_archive, _free_port
from voice_downloads import DownloadStore, FileSpec, InternetSource, Source, link_or_copy

# ---------------------------------------------------------------------------------------------------------------------------------
# What is installed, and where it comes from
# ---------------------------------------------------------------------------------------------------------------------------------
ENGINE_VERSION = "v0.9.0"          # pinned: tested with this release (git 795c45fb, 2026-09-30); upstream releases fast, so a newer one is only ever offered after it's been tested
_RELEASE = "https://github.com/0xShug0/audio.cpp/releases/download/" + ENGINE_VERSION + "/"

MODEL_NAME = "higgs-audio-v3-tts-4b-q8_0.gguf"
MODEL = FileSpec(
    name=MODEL_NAME,
    url="https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/main/Higgs-Audio-v3-TTS-4B-GGUF/" + MODEL_NAME,
    size=5_095_354_048,            # 4.75 GiB: the model sits in a subfolder on Hugging Face (the spec's "about 4.7 GB" was low)
    sha256="79746822045b5bf8f9ab2bda87b16cd3f8ea3d9e319cbcf887a87aa1b537a74a",   # Hugging Face's own LFS hash
)


# ---------------------------------------------------------------------------------------------------------------------------------
# Which voices the engine can speak with (the download is the same kind of thing for every one: a fixed HTTPS address, a pinned size and SHA-256)
# ---------------------------------------------------------------------------------------------------------------------------------
# The engine's own README at the v0.9.0 tag lists `qwen3_tts` and `chatterbox` as supported model families (tasks "TTS, Clone"; read 2026-10-06), and the model files below were all
# uploaded to the engine's Hugging Face repository before this engine build (v0.9.0, 2026-09-30) was cut. The address names the repository's commit of 2026-10-05,
# so it can never move under us; the SHA-256 is Hugging Face's own LFS hash for the file.
_HF_VOICES = "https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/e36610ac69b5262e914a52635324050bee8f1ad2/"


# The model that works out the words spoken in a Qwen3-TTS reference clip (the engine's own `qwen3_asr` family, task `asr`; Apache-2.0, the Qwen team). Installed with the
# Qwen3-TTS voice, from the same repository commit. The 16-bit file (not the 8-bit one) because the engine's own notes say 8-bit can make the language guess less reliable and
# the player's clips are in any language; it is a 0.6B model, so the extra size costs little speed. Size and SHA-256 are Hugging Face's own listing for the file.
ASR_ID = "qwen3asr"
ASR_FILE = FileSpec(name="qwen3-asr-0.6b-f16.gguf", url=_HF_VOICES + "Qwen3-ASR-0.6B-GGUF/qwen3-asr-0.6b-f16.gguf",
                    size=1_880_642_016, sha256="5472337d26df8e58fdfabc3ae58a149fbb200578c576b4c660f2985c07da1736")
ASR_LOAD_SECONDS = 60             # waiting for the transcription program to load its model (a 1.9 GB file)
ASR_RUN_SECONDS = 60              # one clip's transcription (a clip is at most 30 s of sound)


TASKS = ("vad", "asr", "diar", "sep", "gen", "tts", "clon", "vc", "s2s", "align", "vdes", "spk", "svc", "midi")   # the task names the real engine (audio.cpp v0.9.0) accepts in server.json


@dataclass(frozen=True)
class VoiceModel:
    key: str
    name: str                 # how a sentence names it: "Higgs voice"
    label: str                # the picker's short name
    one_line: str             # the picker's plain line
    file: FileSpec
    family: str               # the engine's own family name (server.json)
    task: str                 # the engine's task in server.json: "tts" for Higgs and Qwen3-TTS (a request's `voice_ref` makes them copy a voice); "clon" for Chatterbox, which the engine refuses to load as "tts" (its loader: "Chatterbox supports VoiceCloning and VoiceConversion"). The real v0.9.0 engine's own list is exactly TASKS below; it spells the task "clon", and refuses "clone"
    engine_id: str            # the model id in server.json and in every speech request
    vram_bytes: int           # graphics memory it takes (a warning, never a refusal)
    vram_vulkan_bytes: int
    ram_bytes: int
    license_version: str      # bump when the license text changes: every PC is asked again for that model
    license_who: str          # whose license the player accepts
    license_url: str
    license_note: str = ""    # one plain extra line shown with the license (empty: none)
    uses_tags: bool = False   # only Higgs understands <|emotion:fear|> style tags; for the others they are removed before the line is spoken
    needs_words: bool = False # Qwen3-TTS: its voice-copy mode refuses a clip without the words spoken in it (the engine: "voice clone ICL mode requires reference text")
    needs_clip: bool = False  # the two that always speak in a sample's voice: a line with no voice clip is refused
    options: tuple = ()       # extra settings the engine wants for this model: (("load_options", {...}), ("session_options", {...})); none are known to be needed
    companion: Optional[FileSpec] = None   # a second model installed WITH this voice and shown as part of it (Qwen3-TTS: the one that works out a clip's words)


GIB = 1024**3
VOICE_MODELS = {
    "higgs": VoiceModel(
        "higgs", "Higgs voice", "Higgs", "Expressive voices with emotions. Needs a recent graphics card with about 6 GB of memory.", MODEL, "higgs_audio_tts", "tts", "higgs",
        int(5.5 * GIB), int(6.1 * GIB), 6 * GIB, "higgs-audio-v3-2026-10", "Boson AI", "https://huggingface.co/bosonai/higgs-tts-3-4b/blob/main/LICENSE",
        "Higgs is for personal, non-commercial use. MultiProxy downloads the voice model for you.",
        uses_tags=True),
    # Measured by the engine's own report (CUDA, Q8): 6.4 GiB for Qwen3-TTS, 4.3 GiB for Chatterbox; the Vulkan figures and the ordinary-memory figures are estimates.
    "qwen3tts": VoiceModel(
        "qwen3tts", "Qwen3-TTS voice", "Qwen3-TTS", "Clear voices that copy a short clip. Many languages. Needs about 7 GB of graphics memory.",
        FileSpec(name="qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf", url=_HF_VOICES + "Qwen3-TTS-12Hz-1.7B-Base-GGUF/qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf",
                 size=2_695_175_104, sha256="b55e06c7890d43c208d15aed8b4ed3f18215f295e47d5960e061b15bff338ab0"),
        "qwen3_tts", "tts", "qwen3tts", int(6.4 * GIB), int(7.0 * GIB), 6 * GIB, "qwen3-tts-apache-2026-10", "Qwen team (Alibaba)",
        "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base", needs_clip=True, needs_words=True, companion=ASR_FILE),
    "chatterbox": VoiceModel(
        "chatterbox", "Chatterbox voice", "Chatterbox", "Natural voices that copy a short clip. Needs about 5 GB of graphics memory.",
        FileSpec(name="chatterbox-q8_0.gguf", url=_HF_VOICES + "Chatterbox-GGUF/chatterbox-q8_0.gguf",
                 size=2_088_393_668, sha256="d586dd1aa59613cab8046176fb7ca5ba191c02a9b10ffa5b0d892ed22b470656"),
        "chatterbox", "clon", "chatterbox", int(4.3 * GIB), int(4.9 * GIB), 5 * GIB, "chatterbox-mit-2026-10", "Resemble AI",
        "https://github.com/resemble-ai/chatterbox/blob/master/LICENSE", needs_clip=True),
}
DEFAULT_VOICE_MODEL = "higgs"


def _asset(name: str, size: int, sha: str) -> FileSpec:
    return FileSpec(name=name, url=_RELEASE + name, size=size, sha256=sha)


@dataclass(frozen=True)
class Build:
    key: str
    label: str                       # plain words for the player
    backend: str                     # the engine's own --backend name
    files: tuple                     # FileSpec to fetch; every archive is unpacked into the same folder (the CUDA build is the zip plus the runtime zip)
    unpacked_bytes: int              # about how much room they take once unpacked
    system: str = "windows"
    needs_speed_check: bool = False  # Vulkan is only kept if a timed line is fast enough on THIS card (measured only on a top-end card)
    min_glibc: tuple = ()            # Linux builds: the oldest C library they run on, measured from the program's own symbol versions (e.g. (2, 38))


BUILDS = {
    "win-cuda13.3": Build("win-cuda13.3", "NVIDIA graphics card (recent driver)", "cuda", (
        _asset("audio-v0.9.0-bin-windows-x64-cuda13.3.zip", 272_814_336, "46eb928af284babb3526fc5198da12a4fe4e4445812d60e90a04e82cb5a87786"),
        _asset("audio-v0.9.0-cudart-windows-x64-cuda13.3.zip", 575_457_454, "2c7b1054df88a3cb357c005d5284a5d9d6faf14a13885f6cf0e74d966d74c013")),
        1_100_000_000),                                                  # measured
    # Not run by us (only 13.3 was measured). Same publisher, checksums from the release page; the release notes say it adds older (Pascal) cards.
    "win-cuda12.4": Build("win-cuda12.4", "NVIDIA graphics card (older driver)", "cuda", (
        _asset("audio-v0.9.0-bin-windows-x64-cuda12.4.zip", 461_193_013, "f935d399b1cabb96422d9b35278dcabbbe7c16638a0e100a9b983421511eeb91"),
        _asset("audio-v0.9.0-cudart-windows-x64-cuda12.4.zip", 607_273_675, "155377e0b18d568002a6cb252a36dc2b96aaa93606c0e44ace945ec3123564fe")),
        1_500_000_000),                                                  # an estimate (not measured)
    "win-vulkan": Build("win-vulkan", "other graphics card (AMD, Intel)", "vulkan", (
        _asset("audio-v0.9.0-bin-windows-x64-vulkan.zip", 60_320_440, "f884538138e44528a0bf17bb75dbe7ff7350cb91a14cb72c3e9cc1d4a31d6f1f"),),
        170_000_000, needs_speed_check=True),
}

# The Linux builds (2026-10-05). Same publisher and release; size and SHA-256 are the values GitHub shows for the release's own files (the Windows
# ones above match that listing exactly, so it is the publisher's own value). Each tarball holds one program (`audiocpp_server`) and no executable bits
# (`_install` sets them). The NVIDIA one is the publisher's only Linux CUDA build, named "cuda12.8-colab": its GPU code is for compute 7.5 to 9.0
# (T4 and RTX 20-series through H100 and RTX 40-series), and it needs NVIDIA's CUDA 12 runtime libraries on the device (it bundles none).
# "Leave it enabled only where measured" (the director, 2026-10-02): the NVIDIA Linux build was run and timed (see TECHNICAL.md); the Vulkan one was run too, but
# the only Linux machine here has no Vulkan device (WSL2 passes the NVIDIA card through for CUDA only), so it never made a line. Flip this when it has, on a real card.
LINUX_VULKAN_MEASURED = False
LINUX_CUDA_LIBS = ("libcudart.so.12", "libcublas.so.12", "libcufft.so.11", "libnccl.so.2")      # the program's own list of needed libraries
BUILDS["linux-cuda12.8"] = Build("linux-cuda12.8", "NVIDIA graphics card", "cuda", (
    _asset("audio-v0.9.0-bin-ubuntu-x64-cuda12.8-colab.tar.gz", 224_004_027, "c9ed906f918246669c324f0d31f1b7dd80cbe003c35cf8a54932f333b57ca3f6"),),
    280_000_000, system="linux", min_glibc=(2, 35))                                 # unpacked size measured: 273 MB
# The Vulkan one is the publisher's "portable" build, not the plain one: the plain build died with an illegal instruction ("signal 4") 33 s after
# starting on the test device's processor (Core Ultra 9 285K, no AVX-512), the same crash the plain Windows CPU build has on it; the portable build loads
# the processor code that fits the machine (`libggml-cpu-*.so` next to the program), so it can't hit that.
BUILDS["linux-vulkan"] = Build("linux-vulkan", "graphics card (Vulkan)", "vulkan", (
    _asset("audio-v0.9.0-bin-ubuntu-x64-vulkan-portable.tar.gz", 69_363_955, "ce661a7b39add12e91f1c7cc590d55854a39ec0a2fd04c004b3fed29cb978398"),),
    190_000_000, system="linux", needs_speed_check=True, min_glibc=(2, 38))         # unpacked size measured: 184 MB

# Which NVIDIA build by driver version. NVIDIA's own documentation (CUDA compatibility, looked up 2026-10-02): CUDA 13.x runs on drivers
# 580 or newer; CUDA 12.x runs on 528.33 or newer on Windows (minor-version compatibility), and 12.4 itself shipped with 551.61.
# Older than 528.33 can't run either build.
DRIVER_MIN_CUDA13 = 580.0
DRIVER_MIN_CUDA12 = 528.33
DRIVER_MIN_CUDA12_LINUX = 525.60      # NVIDIA's CUDA compatibility table: a CUDA 12.x program runs on Linux drivers 525.60.13 or newer

# What it takes. Measured on the RTX 5090 test device: +4.9 GB after load and +5.5 GB peak of graphics memory (Vulkan 6.1 GB peak), 5.2-5.9 GB of
# ordinary memory in use, 6.2 GB of disk on the CUDA path (the model alone is 5.1 GB). Memory figures are warnings, never refusals: graphics
# memory reports are fuzzy and a tight fit sometimes works.
VRAM_NEEDED_BYTES = int(5.5 * 1024**3)
VRAM_NEEDED_VULKAN_BYTES = int(6.1 * 1024**3)
RAM_NEEDED_BYTES = 6 * 1024**3
DISK_MARGIN_BYTES = 256 * 1024 * 1024
# The license the player accepts for the voice model before it is put on a device (the step comes with the Voice tab; the version is part of the saved
# record "which device, when, which version"). Bump it when the license text changes: every device then has to be asked again.
LICENSE_VERSION = "higgs-audio-v3-2026-10"

# After the warm-up, a Vulkan device times one real line. If making a line takes longer than this share of its spoken length the card is too slow
# for live talk. (The builder's suggestion in the measurements, accepted as the starting value: ONE constant.) Measured: CUDA 0.12, Vulkan
# 0.15-0.18 on the 5090.
SPEED_LIMIT_RTF = 0.5

# Waiting for the engine. The port doesn't listen until the model is loaded (9-10 s on a fast card; a cold disk takes longer), and the very first
# line on a device builds shaders/caches (measured 11.5 s CUDA, 22.9 s Vulkan, once). 40 s leaves room above the measured 23 s; 120 s for the load.
LOAD_TIMEOUT_SECONDS = 120
WARMUP_FIRST_RUN_SECONDS = 40
WARMUP_SECONDS = 20
DIED_AT_ONCE_SECONDS = 20          # an engine that is gone this soon after starting was most likely stopped by security software
PORT_RANGE = (18950, 18999)        # engine ports (loopback only), apart from every other port MultiProxy uses

IS_WINDOWS = os.name == "nt"
ENGINE_EXE = "audiocpp_server.exe" if IS_WINDOWS else "audiocpp_server"
REFERENCE_MAX_BYTES = 5 * 1024 * 1024        # the engine's own limit on a decoded reference clip
_ANY_TAG = re.compile(r"\s*<\|[^|>]*\|>")


class VoiceEngineError(Exception):
    """A plain, player-readable reason, with an HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


NEEDS_WORDS = "Type the exact words spoken in the voice clip."
WARMUP_WORDS = "Hello there. This is a short test of the voice."        # said to be what the warm-up's stand-in clip says (it is a tone; the engine only needs some text)


_ASR_TAG = re.compile(r"<[^>\n]{1,40}>")


def clean_transcript(text: str) -> str:
    """What the transcription model said, as plain words. It may lead with `language English<asr_text>`; markup is dropped; "" for an empty or obviously empty result."""
    t = text or ""
    if "<asr_text>" in t:
        t = t.split("<asr_text>", 1)[1]
    t = " ".join(_ASR_TAG.sub(" ", t).split())
    if len(re.findall(r"\w", t)) < 2 or re.fullmatch(r"language\s+\w+", t, re.IGNORECASE):
        return ""
    return t


class NeedsWords(VoiceEngineError):
    def __init__(self):
        super().__init__(400, NEEDS_WORDS)


class EngineTokenLimit(VoiceEngineError):
    """The engine stopped at its token limit before the end of the line ("generation reached max_tokens ... before EOC": its own error, HTTP 500)."""

    def __init__(self):
        super().__init__(502, "The voice ran out of memory before finishing.")


class EngineBusy(VoiceEngineError):
    """The engine refused a line because it waited too long for its turn (the engine's own `busy_timeout_ms`)."""

    def __init__(self, message: str = "The voice is busy right now."):
        super().__init__(503, message)


# ---------------------------------------------------------------------------------------------------------------------------------
# Hardware and the fit check
# ---------------------------------------------------------------------------------------------------------------------------------
@dataclass
class Hardware:
    system: str                          # "windows" | "linux" | "mac"
    gpu: Optional[GpuInfo] = None
    driver: Optional[float] = None       # NVIDIA driver version, e.g. 617.14
    free_vram_bytes: Optional[int] = None
    free_ram_bytes: Optional[int] = None
    free_disk_bytes: Optional[int] = None
    glibc: Optional[tuple] = None        # Linux: this device's C library version, e.g. (2, 39)
    cuda_missing: Optional[list] = None  # Linux NVIDIA: the CUDA 12 libraries this device lacks ([] = all there, None = couldn't tell)


@dataclass
class Fit:
    verdict: str                         # "ok" | "warn" | "refuse"
    build: Optional[str] = None          # BUILDS key, when one fits
    reasons: list = field(default_factory=list)      # why it can't be installed (refuse)
    warnings: list = field(default_factory=list)     # install anyway, with the consequence spelled out
    need_disk_bytes: int = 0
    speed_check: bool = False
    flags: list = field(default_factory=list)        # what the warnings are about: "vram" | "ram" | "speed" (so the Voice tab can word a tight card its own way)

    def to_json(self) -> dict:
        return {"verdict": self.verdict, "build": self.build, "reasons": self.reasons, "warnings": self.warnings,
                "needDiskBytes": self.need_disk_bytes, "speedCheck": self.speed_check, "flags": self.flags}


def gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def _system() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "mac"


def free_ram_bytes() -> Optional[int]:
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullAvailPhys)
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        return None
    return None


def linux_glibc() -> Optional[tuple]:
    """This Linux's C library version, e.g. (2, 39); None when it can't be read (or this isn't glibc)."""
    try:
        m = re.search(r"(\d+)\.(\d+)", os.confstr("CS_GNU_LIBC_VERSION"))
        return (int(m.group(1)), int(m.group(2))) if m else None
    except (AttributeError, ValueError, OSError):
        return None


def linux_cuda_missing(run: Callable[..., Any] = hidden_process.run) -> Optional[list]:
    """Which of the NVIDIA program's CUDA 12 libraries aren't on this device, looked for where the program itself looks: the system's library list
    (`ldconfig -p`), /usr/local/cuda*/lib64 (the path built into it) and LD_LIBRARY_PATH. None when it can't tell (no `ldconfig` and no folder to read)."""
    listed = None
    try:
        r = run(["ldconfig", "-p"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            listed = r.stdout
    except Exception:
        pass
    dirs = [Path("/usr/local/cuda/lib64")] + sorted(Path("/usr/local").glob("cuda-12*/lib64")) + [Path(d) for d in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if d]
    readable = listed is not None or any(d.is_dir() for d in dirs)
    if not readable:
        return None
    return [lib for lib in LINUX_CUDA_LIBS if not ((listed and lib in listed) or any((d / lib).exists() for d in dirs))]


def _nvidia_query(fields: str) -> Optional[list]:
    try:
        r = hidden_process.run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
    except Exception:
        pass
    return None


def detect_hardware(folder: Path) -> Hardware:
    hw = Hardware(system=_system())
    hw.gpu = detect_gpu()
    if hw.gpu and hw.gpu.vendor == "nvidia":
        row = _nvidia_query("driver_version,memory.free")
        if row and len(row) >= 2:
            try:
                hw.driver = float(row[0])
            except ValueError:
                pass
            try:
                hw.free_vram_bytes = int(float(row[1])) * 1024 * 1024
            except ValueError:
                pass
    hw.free_ram_bytes = free_ram_bytes()
    if hw.system == "linux":
        hw.glibc = linux_glibc()
        if hw.gpu and hw.gpu.vendor == "nvidia":
            hw.cuda_missing = linux_cuda_missing()
    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        hw.free_disk_bytes = shutil.disk_usage(probe).free
    except OSError:
        pass
    return hw


def choose_build(hw: Hardware, name: str = "Higgs voice") -> tuple:
    """(build key, reasons it can't be) for this device. CPU is never offered: measured at over two minutes a line on Windows."""
    if hw.system not in ("windows", "linux"):
        return None, [f"{name} isn't ready for this kind of device yet. It works on Windows and Linux devices with a graphics card."]
    if not hw.gpu:
        return None, [f"{name} needs a graphics card, and this device doesn't seem to have a suitable one."]
    if hw.system == "linux":
        return _choose_linux_build(hw, name)
    if hw.gpu.vendor == "nvidia":
        if hw.driver is None:
            return "win-cuda13.3", []        # driver unreadable: take the newer build and let the warm-up say if it can't run
        if hw.driver >= DRIVER_MIN_CUDA13:
            return "win-cuda13.3", []
        if hw.driver >= DRIVER_MIN_CUDA12:
            return "win-cuda12.4", []
        return None, [f"This device's NVIDIA graphics driver ({hw.driver:g}) is too old. Update to version {DRIVER_MIN_CUDA12:g} or newer, then try again."]
    return "win-vulkan", []


def _choose_linux_build(hw: Hardware, name: str = "Higgs voice") -> tuple:
    """NVIDIA cards use the CUDA build when this device has NVIDIA's CUDA 12 libraries (the program bundles none). Vulkan is pinned but only offered once it has been
    measured on a Linux graphics card (`LINUX_VULKAN_MEASURED`): until then an AMD or Intel card, or an NVIDIA card without the libraries, gets a plain reason."""
    if hw.gpu.vendor == "nvidia":
        if hw.driver is not None and hw.driver < DRIVER_MIN_CUDA12_LINUX:
            return None, [f"This device's NVIDIA graphics driver ({hw.driver:g}) is too old. Update to version {DRIVER_MIN_CUDA12_LINUX:g} or newer, then try again."]
        if not hw.cuda_missing:               # nothing missing, or couldn't tell (the warm-up says if it can't run)
            return "linux-cuda12.8", []
        if not LINUX_VULKAN_MEASURED:
            return None, [f"{name} needs NVIDIA's CUDA 12 libraries on this device (missing: " + ", ".join(hw.cuda_missing) + "). "
                          "Install your distribution's CUDA 12 runtime packages (cuBLAS, cuFFT and NCCL), then try again."]
    elif not LINUX_VULKAN_MEASURED:
        return None, [f"On Linux, {name} works with NVIDIA graphics cards today. AMD and Intel cards haven't been tested yet."]
    return "linux-vulkan", []


def assess(hw: Hardware, vm: Optional[VoiceModel] = None, kept_fn: Optional[Callable[[list], int]] = None) -> Fit:
    vm = vm or VOICE_MODELS[DEFAULT_VOICE_MODEL]
    build, reasons = choose_build(hw, vm.name)
    fit = Fit(verdict="ok", build=build, reasons=list(reasons))
    if not build:
        fit.verdict = "refuse"
        return fit
    b = BUILDS[build]
    if b.min_glibc and hw.glibc is not None and hw.glibc < b.min_glibc:
        fit.reasons.append(f"This Linux is too old for Higgs voice (its system libraries are version {hw.glibc[0]}.{hw.glibc[1]}, but {b.min_glibc[0]}.{b.min_glibc[1]} or newer is needed). "
                           "Ubuntu 24.04 or newer works.")
        fit.verdict = "refuse"
        return fit
    fit.speed_check = b.needs_speed_check
    fit.need_disk_bytes = vm.file.size + (vm.companion.size if vm.companion else 0) + sum(f.size for f in b.files) + b.unpacked_bytes + DISK_MARGIN_BYTES
    if kept_fn is not None:               # a file already in the kept downloads is already there: only what is still missing needs room
        fit.need_disk_bytes -= kept_fn([vm.file] + ([vm.companion] if vm.companion else []) + list(b.files))
    # (downloads are kept, so the first install needs room for the downloads AND the unpacked engine; the model is linked, not copied)
    if hw.free_disk_bytes is not None and hw.free_disk_bytes < fit.need_disk_bytes:
        fit.reasons.append(f"There isn't enough room on that drive. {vm.name} needs about {gb(fit.need_disk_bytes)} free, but only {gb(hw.free_disk_bytes)} is free.")
        fit.verdict = "refuse"
        return fit
    need_vram = vm.vram_vulkan_bytes if b.backend == "vulkan" else vm.vram_bytes
    if hw.free_vram_bytes is not None and hw.free_vram_bytes < need_vram:
        fit.warnings.append(f"Warning: Only {gb(hw.free_vram_bytes)} of graphics memory is free, but it needs {gb(need_vram)}. Closing Skyrim or other local models helps it run.")
        fit.flags.append("vram")
    if hw.free_ram_bytes is not None and hw.free_ram_bytes < vm.ram_bytes:
        fit.warnings.append(f"Warning: Only {gb(hw.free_ram_bytes)} of system memory is free. Close other programs if it won't start.")
        fit.flags.append("ram")
    if hw.system == "linux" and hw.gpu and hw.gpu.vendor == "nvidia" and hw.cuda_missing and b.backend == "vulkan":
        fit.warnings.append("Warning: NVIDIA's CUDA 12 libraries (" + ", ".join(hw.cuda_missing) + ") aren't installed here, so Higgs voice will use Vulkan, which is slower. "
                            "Install your distribution's CUDA 12 runtime packages (cuBLAS, cuFFT and NCCL) and install Higgs voice again to speed it up.")
        fit.flags.append("cuda-libs")
    if b.needs_speed_check:
        fit.warnings.append("This device will test one spoken line after installing to make sure it is fast enough.")
        fit.flags.append("speed")
    fit.verdict = "warn" if fit.warnings else "ok"
    return fit


# ---------------------------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------------------------
def plain_install_reason(e: BaseException) -> str:
    """An install that failed for a reason nobody planned for, as one short plain clause for the screen ("" when it can't be told). Never raw error text, paths or codes."""
    import errno
    import zipfile
    if isinstance(e, zipfile.BadZipFile):
        return "a downloaded file was damaged."
    if isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32):
        return "a file the voice needs was still in use by another program."
    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
        return "there wasn't enough free space on the drive."
    if isinstance(e, OSError):
        return "a file couldn't be written."
    return ""


STILL_RUNNING = "The old voice is still running and holding onto its files. Press **Stop**, then try again."
STOP_WAIT_SECONDS = 10.0
NOISY_LINE = b"CUDA graph warmup complete"          # the engine prints this hundreds of times; it buries the real lines


def process_image(pid: int) -> str:
    """The program name running under this id right now ("" when nothing runs there). Used to be sure a recorded id still belongs to the engine we started."""
    try:
        if os.name == "nt":
            out = hidden_process.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout.strip()
            return out.split('","')[0].lstrip('"').lower() if out.startswith('"') else ""
        out = (hidden_process.run(["ps", "-p", str(pid), "-o", "stat=", "-o", "comm="], capture_output=True, text=True, timeout=10).stdout or "").strip()
        state, _, name = out.partition(" ")
        return "" if state.startswith("Z") else name.strip().lower()          # a program that has ended but not been collected yet (Z) is gone
    except Exception:
        return ""


def same_image(running: str, recorded: str) -> bool:
    return bool(running) and bool(recorded) and running[:15] == recorded.lower()[:15]


OUTPUT_LINES_MAX = 5000          # the live "Engine output" window keeps this many lines in memory (the oldest go first)
OUTPUT_LINE_CHARS = 2000         # one printed line is cut here (a runaway line must not fill the memory)


class OutputBuffer:
    """The last lines the voice engine printed (what a terminal would show) and the lines MultiProxy wrote about it, numbered, for the Engine output window
    (2026-10-23). Memory only, never written anywhere: the window asks "everything after line N" about once a second while it is open and nothing runs while it is closed."""

    def __init__(self, cap: int = OUTPUT_LINES_MAX):
        self.cap = cap
        self._lines: deque = deque(maxlen=cap)
        self._seq = 0
        self._lock = threading.Lock()

    def add(self, source: str, text: str) -> None:
        text = (text or "").rstrip("\r\n")
        if not text.strip():
            return
        with self._lock:
            self._seq += 1
            self._lines.append((self._seq, time.time(), source, text[:OUTPUT_LINE_CHARS]))

    def since(self, since: int = 0, limit: int = 200) -> dict:
        limit = max(1, min(int(limit or 200), self.cap))
        with self._lock:
            items, last = list(self._lines), self._seq
        reset = since > last                       # the engine host started again: its numbers began again, so the window starts over
        if since <= 0 or reset:
            items = items[-limit:]
        else:
            items = [i for i in items if i[0] > since][-limit:]
        return {"lines": [{"n": n, "t": round(t, 1), "src": s, "text": x} for n, t, s, x in items], "next": last, "reset": reset, "capacity": self.cap}


def pump_engine_output(pipe, path: Path, sink: Optional[Callable[[str], None]] = None) -> None:
    """Copy the engine's printed lines into `path`, leaving out the one line it repeats hundreds of times (counted, and said once at the end).
    `sink` also gets each kept line the moment it is read (the live Engine output window)."""
    skipped = 0
    try:
        with open(path, "wb") as out:
            for line in iter(pipe.readline, b""):
                if NOISY_LINE in line:
                    skipped += 1
                    if skipped > 1:
                        continue
                out.write(line)
                out.flush()
                if sink is not None:
                    try:
                        sink(line.decode("utf-8", errors="replace"))
                    except Exception:
                        pass
            if skipped > 1:
                out.write(f"(the line above was printed {skipped} times; the repeats are left out)\n".encode("utf-8"))
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def make_silence_wav(seconds: float = 1.0, rate: int = 24000) -> bytes:
    """A quiet, gently moving test tone as a mono 16-bit WAV: the reference clip for the warm-up line (no real voice needed)."""
    n = int(rate * seconds)
    frames = b"".join(struct.pack("<h", int(2000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


_ENGINE_ERROR_LINE = re.compile(r"^\s*(?:(?:audiocpp_server|audio\.cpp)\s+failed:|(?:error|fatal)\b[:\s]|terminate called|what\(\):|.*\bfailed:)\s*(.*)$", re.IGNORECASE)


def engine_own_error(stdout_text: str) -> str:
    """The engine's own last error line from what it printed ("audiocpp_server failed: ..."), the reason only; "" when it printed none."""
    found = ""
    for line in (stdout_text or "").splitlines():
        m = _ENGINE_ERROR_LINE.match(line)
        if m:
            found = (m.group(1) or line).strip()
    return found


def _plain_reason(reason: str) -> str:
    """The engine's reason as one short sentence when it reads plainly (no paths, codes or long text), else ""."""
    r = (reason or "").strip().rstrip(".")
    if not r or len(r) > 100 or not r.isascii() or any(c in r for c in "\\/<>{}[]=@") or re.search(r"0x[0-9a-f]+", r, re.IGNORECASE):
        return ""
    return r[0].upper() + r[1:] + "."


def _known_cause(reason: str) -> str:
    """A short plain cause for the engine's own errors that name one (never a path or a number), else ""."""
    low = (reason or "").lower()
    if "unsupported task" in low:
        return "the voice engine doesn't know this setup's task name."
    if "reference" in low and any(h in low for h in ("missing", "not found", "no such", "cannot open", "failed to open", "failed to read")):
        return "its reference clip is missing."
    if any(h in low for h in ("no such file", "not found", "cannot open", "failed to open", "does not exist")):
        return "a file it needs is missing."
    return ""


def explain_death(log_tail: str, exit_code: Optional[int], seconds_alive: float, name: str = "Higgs voice", engine_printed: Optional[str] = None, folder: str = "") -> str:
    """One plain sentence for why the engine stopped, and what to try. `engine_printed` is what the engine itself wrote (its output file): when it said what
    went wrong, that is the cause shown; the antivirus advice is only for an engine that printed nothing at all (None: not known)."""
    low = (log_tail or "").lower()
    if any(h in low for h in ("out of memory", "cudamalloc failed", "failed to allocate", "vk_error_out_of_device_memory")):
        return "The voice ran out of graphics memory. Close other programs and start it again."
    if not IS_WINDOWS:
        m = re.search(r"error while loading shared libraries: (\S+?):", low)
        if m:
            return f"A system file Higgs voice needs ({m.group(1)}) isn't installed. Install your distribution's package for it, then start the voice again."
        if any(h in low for h in ("libvulkan.so", "vk_error_incompatible_driver", "no vulkan", "ggml_vulkan: no devices found", "registry 'vulkan' has no device")):
            return "This device's graphics driver has no Vulkan support. Install or update the graphics driver, then start the voice again."
        if exit_code in (-4, 132):
            return "This device's processor can't run this voice engine."
    if exit_code in (3221225501, -1073741795):
        return "This device's processor can't run this voice engine."
    if exit_code in (3221225781, -1073741515):
        return "A Windows file is missing. Uninstall the voice and install it again."
    own = engine_own_error(engine_printed or "")
    if own:
        known = _known_cause(own)
        if known:
            return f"{name} couldn't start: {known} Open **Log** to see why."
        why = _plain_reason(own)
        return f"{name} couldn't start with this setup." + (f" The engine said: {why}" if why else " Open **Log** to see why.")
    if engine_printed is not None and engine_printed.strip():
        return "The voice engine stopped as soon as it started. Open **Log** to see why."
    if seconds_alive < DIED_AT_ONCE_SECONDS:
        if not IS_WINDOWS:
            return f"{name} stopped as soon as it started. Check its log (use Open folder to find it). A graphics driver problem is the usual cause."
        where = f" Allow this folder, then start it again: `{folder}`" if folder else " Allow the voice engine's folder (use Open folder to find it), then start it again."
        return f"{name} stopped as soon as it started and the engine said nothing. Your antivirus or Windows Smart App Control may have blocked it." + where
    return "The voice stopped unexpectedly. Check its details."


@dataclass
class SpeechResult:
    wav: bytes
    wall_ms: Optional[float] = None
    audio_ms: Optional[float] = None
    rtf: Optional[float] = None
    path: Optional[Path] = None          # a line relayed from another device is streamed straight to this file (`wav` is then empty): never held whole in memory


@dataclass
class InstallJob:
    state: str = "running"               # running | done | error | cancelled
    step: str = "engine"                 # engine | model | warmup
    done: int = 0
    total: int = 0
    error: str = ""
    bad_file: str = ""                   # the file that failed its checksum (the Voice tab then offers "Download it again")
    reason: str = ""                     # why an unexpected failure happened, as one short plain clause ("" when unknown); the screen puts it under the red line
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    meter: Any = field(default_factory=download_meter.Meter, repr=False)      # the speed really measured: the time left comes from it

    def to_json(self) -> dict:
        return {"state": self.state, "step": self.step, "done": self.done, "total": self.total, "error": self.error, "badFile": self.bad_file, "reason": self.reason,
                "percent": int(self.done * 100 / self.total) if self.total else 0,
                "left": self.meter.text(self.done, self.total) if self.state == "running" and self.step != "warmup" else ""}


STEP_LABELS = {"engine": "Voice engine", "model": "Voice model", "warmup": "First warm-up"}


# ---------------------------------------------------------------------------------------------------------------------------------
# Which graphics card (Routing, step 8). The engine's own `--list-devices` prints `Vulkan:1 "Intel(R) Graphics" [IGPU]` (no memory size, no permanent id; checked against
# the shipped v0.9.0 builds 2026-10-02) and takes `device` (that index, per backend) in server.json. The CUDA builds list only the NVIDIA cards and honour CUDA_VISIBLE_DEVICES.
# ---------------------------------------------------------------------------------------------------------------------------------
VOICE_SAME_NAME = "Two graphics cards have the same name ({name}). Higgs voice can't tell them apart, so it's using Automatic."


def parse_voice_devices(text: str, backend: str) -> list:
    """`audiocpp_server --list-devices` -> [(index, name)] of this backend's own devices (the processor row and other backends are left out)."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r'^\s*([A-Za-z]+):(\d+)\s+"(.*)"\s+\[(\w+)\]\s*$', line)
        if m and m.group(1).lower() == backend.lower() and m.group(4).upper() in ("GPU", "IGPU"):
            out.append((int(m.group(2)), m.group(3)))
    return out


def list_voice_devices(exe, backend: str, run: Callable[..., Any] = hidden_process.run) -> list:
    try:
        r = run([str(exe), "--list-devices"], capture_output=True, text=True, timeout=30, cwd=os.path.dirname(str(exe)))
        return parse_voice_devices((r.stdout or "") + "\n" + (r.stderr or ""), backend)
    except Exception:
        return []


def voice_plan(choice: Optional[dict], cards: list, backend: str, exe, devices_fn: Callable[..., list] = list_voice_devices, voice_name: str = "Higgs voice") -> tuple:
    """(gpu_choice.Plan, the `device` number for server.json). No choice, or a processor-only build: Automatic, device 0, nothing said."""
    if not choice or not choice.get("id") or backend not in ("cuda", "vulkan"):
        return gpu_choice.Plan(), 0
    if backend == "cuda":
        return gpu_choice.plan_for(choice, cards, "cuda"), 0           # the chosen card is the only one the engine can see, so it is device 0
    name = str(choice.get("name") or "that card")
    card = next((c for c in cards if c.id == choice["id"]), None)
    if card is None:
        return gpu_choice.Plan(note=gpu_choice.CARD_GONE.format(name=name)), 0
    want = [d for d in devices_fn(exe, "vulkan") if gpu_choice._same_card(d[1], card.name)]
    if not want:
        return gpu_choice.Plan(note=gpu_choice.CARD_NOT_LISTED.format(name=card.name)), 0
    if len(want) > 1:
        return gpu_choice.Plan(note=VOICE_SAME_NAME.format(name=card.name).replace("Higgs voice", voice_name)), 0
    return gpu_choice.Plan(card=card, automatic=False), want[0][0]


# ---------------------------------------------------------------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------------------------------------------------------------
class VoiceEngine:
    def __init__(self, folder_fn: Callable[[], Path], store: DownloadStore, redact: Callable[[str], str] = lambda s: s,
                 builds: Optional[dict] = None, model: FileSpec = MODEL, launcher: Optional[Callable[[Path, Path, Path], list]] = None,
                 hardware_fn: Optional[Callable[[Path], Hardware]] = None, port_range: tuple = PORT_RANGE,
                 gpu_choice_fn: Callable[[], Optional[dict]] = lambda: None, cards_fn: Callable[[], list] = gpu_choice.cached_cards,
                 devices_fn: Callable[..., list] = list_voice_devices):
        self._folder_fn = folder_fn
        self.store = store
        self.redact = redact
        self.builds = builds if builds is not None else BUILDS
        self.model = model
        self._launcher = launcher or self._default_launcher
        self._hardware_fn = hardware_fn or detect_hardware
        self._port_range = port_range
        self._gpu_choice_fn = gpu_choice_fn     # the remembered card choice for this AI (None = Automatic)
        self._cards_fn = cards_fn
        self._devices_fn = devices_fn
        self.gpu_note = ""                      # why Automatic was used at the last start ("" = nothing to say)
        self.gpu_card: Optional[str] = None     # name of the card the last start was kept on (None = Automatic)
        self.job: Optional[InstallJob] = None
        self.state = "not_installed"
        self.detail = ""
        self.port: Optional[int] = None
        self.proc = None
        self.started_at = 0.0
        self.last_line_ms: Optional[float] = None
        self.last_rtf: Optional[float] = None
        self.lines_done = 0
        self.speed_check: Optional[dict] = None
        self.waiting_fn: Callable[[], int] = lambda: 0
        self._vram_baseline: Optional[int] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        self._clips = None
        self._asr_lock = asyncio.Lock()          # one transcription at a time (and a clip that waits behind another finds its words already there)
        self._asr_launch: tuple = (0, None)      # (card index, environment) the voice was last started with
        self.catalog = dict(VOICE_MODELS)
        self.catalog[DEFAULT_VOICE_MODEL] = replace(VOICE_MODELS[DEFAULT_VOICE_MODEL], file=model)       # (tests and other builds hand in their own Higgs file)
        self.model_key = DEFAULT_VOICE_MODEL
        self.model = model
        active = self._read_state().get("active")
        if active in self.catalog:
            self._apply_model(active)
        self._pump = None
        self._reaping = None
        self.output = OutputBuffer()
        self._refresh_state()

    # ---- which voice model ----
    @property
    def vmodel(self) -> VoiceModel:
        """The chosen voice model (its pinned file is `self.model`, which tests may swap)."""
        return replace(self.catalog[self.model_key], file=self.model)

    @property
    def voice_name(self) -> str:
        return self.vmodel.name

    def _apply_model(self, key: str) -> None:
        self.model_key, self.model = key, self.catalog[key].file

    def installed_models(self) -> list:
        """Keys of the voice models whose file is on this PC with the engine (more than one can be installed; one is chosen)."""
        d = self._read_state()
        if not (d.get("installed") and self.find_exe() is not None):
            return []
        return [k for k, vm in self.catalog.items() if (self.models_dir / vm.file.name).is_file()]

    async def select_model(self, key: str) -> None:
        """Choose which installed (or still to be installed) voice model this PC uses. A running voice is stopped first."""
        if key not in self.catalog:
            raise VoiceEngineError(400, "MultiProxy doesn't recognize that voice.")
        if key == self.model_key:
            return
        if self.job and self.job.state == "running":
            raise VoiceEngineError(409, f"{self.voice_name} is installing. Wait for it to finish first.")
        async with self._lock:
            await self._stop_quietly(keep_state=True)
            if self._recorded() is not None:
                self.log(STILL_RUNNING)
                raise VoiceEngineError(409, STILL_RUNNING)
            self._apply_model(key)
            self._write_state(active=key)
            self.state, self.detail, self.speed_check, self.job = ("stopped" if self.installed() else "not_installed"), "", None, None
        self.log(f"Voice model chosen: {self.vmodel.name}")

    # ---- places ----
    @property
    def folder(self) -> Path:
        return Path(self._folder_fn())

    @property
    def engine_dir(self) -> Path:
        return self.folder / "engine"

    @property
    def models_dir(self) -> Path:
        return self.folder / "models"

    @property
    def clips_dir(self) -> Path:
        return self.folder / "clips"

    @property
    def clips(self):
        """The player's own voice clips on this device (voice_clips.py; imported here, not at the top, because that file imports this one)."""
        if self._clips is None:
            from voice_clips import ClipStore
            self._clips = ClipStore(lambda: self.clips_dir, self.log)
        return self._clips

    @property
    def state_file(self) -> Path:
        return self.folder / "voice-state.json"

    @property
    def log_file(self) -> Path:
        return self.folder / "voice-engine.log"

    def _read_state(self) -> dict:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, **changes) -> None:
        d = self._read_state()
        d.update(changes)
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)

    def installed(self) -> bool:
        d = self._read_state()
        return bool(d.get("installed")) and self.find_exe() is not None and (self.models_dir / self.model.name).is_file()

    def find_exe(self) -> Optional[Path]:
        if not self.engine_dir.is_dir() or not safe_paths.is_local_fixed(self.engine_dir):
            return None                                   # (never a program that sits on a network share: it could be changed by anyone there)
        for p in self.engine_dir.rglob(ENGINE_EXE):
            return p
        return None

    def _refresh_state(self) -> None:
        if self.state in ("starting", "getting_ready", "ready", "too_slow", "installing", "died", "error"):
            return        # (a problem stays on show until the player starts, stops or uninstalls)
        self.state = "stopped" if self.installed() else "not_installed"
        if self.state == "not_installed":
            self.detail = ""

    # ---- log ----
    def log(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.redact(message.replace('**', ''))}"   # the stars are for the screen only
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        self.output.add("multiproxy", line[20:])           # (the same words without the time: the window shows its own)

    def output_since(self, since: int = 0, limit: int = 200) -> dict:
        """What the Engine output window shows: the lines after number `since` (the last `limit` when `since` is 0), which voice model is loaded and the engine's state."""
        d = self.output.since(since, limit)
        d["lines"] = [{**x, "text": self.redact(x["text"])} for x in d["lines"]]          # (masked again as it is read, whatever the masking was when the line was printed)
        return {**d, "model": self.model_key, "modelName": self.voice_name, "state": self.state}

    def log_tail(self, lines: int = 80) -> str:
        out = []
        for p in (self.log_file, self.folder / "engine-trace.log", self.folder / "engine-stdout.log"):      # (the engine's own errors, like a missing library, are only in the last)
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-20000:].splitlines()[-lines:]
                out.append(f"--- {p.name} ---\n" + "\n".join(self.redact(x) for x in tail))
            except OSError:
                continue
        return "\n".join(out)

    # ---- the fit check ----
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

    def model_for(self, key: Optional[str] = None) -> VoiceModel:
        """A voice model by key (None: the chosen one). Raises a plain 400 for a key MultiProxy doesn't know."""
        if key in (None, "", self.model_key):
            return self.vmodel
        if key not in self.catalog:
            raise VoiceEngineError(400, "MultiProxy doesn't recognize that voice.")
        return self.catalog[key]

    def fit_check(self, key: Optional[str] = None) -> Fit:
        fit = assess(self.hardware(), self.model_for(key), lambda specs: sum(s.size for s in specs if self.store.has(s)))
        if fit.build and fit.build not in self.builds:
            fit.build = next(iter(self.builds), None)
        return fit

    # ---- install ----
    def start_install(self, source: Optional[Source] = None) -> InstallJob:
        if self.job and self.job.state == "running":
            raise VoiceEngineError(409, f"{self.voice_name} is already installing.")
        fit = self.fit_check()
        if fit.verdict == "refuse":
            raise VoiceEngineError(400, " ".join(fit.reasons))
        build = self.builds[fit.build]
        job = InstallJob(total=sum(f.size for f in build.files) + self.model.size + self._companion_size())
        self.job = job
        self.state, self.detail = "installing", ""
        job.task = asyncio.ensure_future(self._install(job, build, source or InternetSource()))
        return job

    async def _install(self, job: InstallJob, build: Build, source: Source) -> None:
        base = {"engine": 0, "model": 0}

        def on_bytes(n: int) -> None:
            job.meter.note(n)
            job.done = max(0, job.done + n)

        try:
            self.log(f"Install started (build {build.key}, engine {ENGINE_VERSION})")
            await self._stop_quietly()
            if self._recorded() is not None:                       # the old engine would not exit: nothing under it is touched
                raise LocalModelError(409, STILL_RUNNING)
            job.step = "engine"
            archives = []
            for spec in build.files:
                archives.append(await self.store.get(spec, source, on_bytes))
            target = self.engine_dir
            if target.exists():
                await self._clear_engine_folder(target)
            for a in archives:
                await asyncio.to_thread(unpack_archive, a, target)
            exe = self.find_exe()
            if not exe:
                raise LocalModelError(502, "The download failed. Press **Install** to try again.")
            if os.name != "nt":
                os.chmod(exe, 0o755)           # the Linux tarballs have no executable permission set (measured)
            job.step = "model"
            kept = await self.store.get(self.model, source, on_bytes)
            how = await asyncio.to_thread(link_or_copy, kept, self.models_dir / self.model.name)
            self.log(f"Voice model in place ({how})")
            comp = self.vmodel.companion
            if comp is not None:                       # (it comes with the voice, in the same step: the screen counts it as part of the voice model)
                kept_asr = await self.store.get(comp, source, on_bytes)
                how = await asyncio.to_thread(link_or_copy, kept_asr, self.models_dir / comp.name)
                self.log(f"Model that works out a clip's words in place ({how})")
            self._write_state(installed=True, build=build.key, engine=ENGINE_VERSION, installed_at=int(time.time()), first_run_done=False, active=self.model_key)
            self.clips_dir.mkdir(parents=True, exist_ok=True)
            job.step = "warmup"
            await self.start()
            if self.state in ("ready", "too_slow"):
                job.state = "done"
            else:
                job.state, job.error = "error", self.detail or f"{self.voice_name} didn't start."
        except asyncio.CancelledError:
            job.state = "cancelled"
            self.state = "stopped" if self.installed() else "not_installed"
            self.log("Install cancelled (downloads so far are kept, so it carries on from there)")
            raise
        except LocalModelError as e:
            job.state, job.error = "error", (e.message if hasattr(e, "message") else str(e)).replace("Hit Download", "Hit Install")
            if "safety check" in job.error:
                job.bad_file = job.error.split(" didn't pass")[0]
            self.state = "stopped" if self.installed() else "not_installed"
            self.log(f"Install failed: {job.error}")
        except Exception as e:
            if isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32):
                job.state, job.error = "error", STILL_RUNNING
            else:
                job.state, job.error = "error", f"Something went wrong while installing {self.voice_name}. Its log has the details."
            job.reason = plain_install_reason(e)
            self.state = "stopped" if self.installed() else "not_installed"
            self.log(f"Install failed: {e.__class__.__name__}: {e}")

    async def _clear_engine_folder(self, target: Path) -> None:
        """Remove the old engine before the new one is unpacked (the engine this manager recorded was stopped first, by its own id). A file still held after a
        few tries ends the install with one plain sentence instead of dying half-way through unpacking."""
        _, left = await asyncio.to_thread(program_uninstall.remove_tree, target, 8, 0.5)
        if left:
            self.log(f"{len(left)} old engine file(s) are still in use. {STILL_RUNNING}")
            raise LocalModelError(409, STILL_RUNNING)

    def cancel_install(self) -> bool:
        if self.job and self.job.state == "running" and self.job.task:
            self.job.task.cancel()
            return True
        return False

    def _companion_size(self) -> int:
        comp = self.vmodel.companion
        return comp.size if comp is not None else 0

    # ---- run ----
    def _default_launcher(self, exe: Path, config: Path, trace: Path) -> list:
        return [str(exe), "--config", str(config), "--log", "--log-file", str(trace)]

    def _write_server_config(self, port: int, backend: str, device: int = 0) -> Path:
        vm = self.vmodel
        entry = {"id": vm.engine_id, "family": vm.family, "path": str(self.models_dir / self.model.name).replace("\\", "/"), "task": vm.task, "mode": "offline"}
        for opt_key, opt in vm.options:
            entry[opt_key] = dict(opt)
        cfg = {"host": "127.0.0.1", "port": port, "backend": backend, "device": device, "threads": 1, "lazy_load": False, "models": [entry]}
        p = self.folder / "server.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
        return p

    def _gpu_used_mb(self) -> Optional[int]:
        row = _nvidia_query("memory.used")
        try:
            return int(float(row[0])) if row else None
        except ValueError:
            return None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=3))
        return self._session

    async def start(self) -> None:
        async with self._lock:
            if self.state in ("ready", "too_slow") and self.proc and self.proc.poll() is None:
                return
            if not self.installed():
                raise VoiceEngineError(409, f"{self.voice_name} isn't installed yet.")
            await self._stop_quietly(keep_state=True)
            st = self._read_state()
            build = self.builds.get(st.get("build")) or next(iter(self.builds.values()))
            exe = self.find_exe()
            self.port = _free_port(*self._port_range)
            plan, device = gpu_choice.Plan(), 0
            if self._choice():
                try:
                    plan, device = await asyncio.to_thread(voice_plan, self._choice(), self._cards_fn(), build.backend, exe, self._devices_fn, self.voice_name)
                except Exception:
                    plan, device = gpu_choice.Plan(), 0
            self.gpu_note, self.gpu_card = plan.note, (plan.card.name if plan.card else None)
            if plan.note:
                self.log(plan.note)
            cfg = self._write_server_config(self.port, build.backend, device)
            trace = self.folder / "engine-trace.log"
            first = not st.get("first_run_done")
            self.state, self.detail = "starting", "Loading the voice…"
            self.speed_check = None
            self._vram_baseline = self._gpu_used_mb()
            self.log(f"Starting the engine on port {self.port} ({build.backend})")
            try:
                env = gpu_choice.merge_env(None, plan)
                self._asr_launch = (device, env)         # the transcription program is started on the same card, the same way
                extra = {"env": env} if env is not None else {}
                if os.name != "nt":
                    extra["start_new_session"] = True         # its own group: stopping it must never signal this program's group
                self.proc = hidden_process.popen(self._launcher(exe, cfg, trace), cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **extra)
            except OSError as e:
                self._died(None, f"couldn't be started ({e})")
                return
            self._pump = threading.Thread(target=pump_engine_output, args=(self.proc.stdout, self.folder / "engine-stdout.log", lambda t: self.output.add("engine", self.redact(t))), daemon=True)
            self._pump.start()
            self._record_pid(self.proc.pid, Path(self._launcher(exe, cfg, trace)[0]).name)
            self.started_at = time.time()
            ok = await self._wait_healthy()
            if not ok:
                return
            self.state = "getting_ready"
            self.detail = "Getting ready for the first time…" if first else "Warming up…"
            try:
                res = await self._speak("Ready.", make_silence_wav(self._warmup_clip_seconds()), timeout=WARMUP_FIRST_RUN_SECONDS if first else WARMUP_SECONDS, reference_text=WARMUP_WORDS)
            except VoiceEngineError as e:
                if self.proc.poll() is not None:
                    self._died(self.proc.returncode)
                else:
                    self.state, self.detail = "error", f"{self.voice_name} started, but its test line failed. {e.message}"
                    self.log(self.detail)
                return
            self._write_state(first_run_done=True)
            self.state, self.detail = "ready", ""
            if build.needs_speed_check:
                await self._speed_check()
            self.log(f"Ready after {time.time() - self.started_at:.1f} s")

    def _warmup_clip_seconds(self) -> float:
        """The warm-up's stand-in voice sample. The two voices that always copy a sample (Qwen3-TTS, Chatterbox) get a longer one than Higgs's 1 s: a few seconds is what they are
        built for (not yet tried on the real engine: see the handoff)."""
        return 3.0 if self.vmodel.needs_clip else 1.0

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
        self.state, self.detail = "error", "The voice took too long to load. Close other programs and try again."
        self.log(self.detail)
        kill_tree_sync(self.proc.pid)
        return False

    def _died(self, exit_code: Optional[int], because: str = "") -> None:
        alive = time.time() - self.started_at if self.started_at else 0.0
        tail = ""
        try:
            tail = self.log_tail(30)
        except Exception:
            pass
        self.state = "died"
        pump = getattr(self, "_pump", None)
        if pump is not None:
            pump.join(1.5)                      # (the last lines the engine printed are the cause)
        printed = None
        try:
            with open(self.folder / "engine-stdout.log", "r", encoding="utf-8", errors="replace") as f:
                printed = f.read()[-20000:]
        except OSError:
            pass
        self.detail = explain_death(tail, exit_code, alive, self.voice_name, printed, str(self.engine_dir)) if not because else f"{self.voice_name} {because}."
        self.log(f"Engine stopped (exit code {exit_code}, {alive:.1f} s after start): {self.detail}")

    async def _speed_check(self) -> None:
        """Vulkan only: one timed real line. Too slow for live talk -> say so and stop pretending it's usable."""
        try:
            res = await self._speak("The road north is dangerous, so stay close.", make_silence_wav(self._warmup_clip_seconds()), timeout=30, reference_text=WARMUP_WORDS)
        except VoiceEngineError:
            return
        rtf = res.rtf if res.rtf is not None else ((res.wall_ms / res.audio_ms) if res.wall_ms and res.audio_ms else None)
        ok = rtf is None or rtf <= SPEED_LIMIT_RTF
        self.speed_check = {"rtf": rtf, "limit": SPEED_LIMIT_RTF, "ok": ok}
        self.log(f"Speed check: {rtf if rtf is None else round(rtf, 2)} (limit {SPEED_LIMIT_RTF})")
        if not ok:
            self.state = "too_slow"
            self.detail = f"This graphics card is too slow for live conversation. Try another device, or uninstall {self.voice_name}."

    async def synthesize(self, text: str, ref_wav: Optional[bytes] = None, *, temperature: Optional[float] = None, seed: Optional[int] = None,
                         top_p: Optional[float] = None, busy_timeout_ms: Optional[int] = None, timeout: float = 60, voice: Optional[str] = None) -> SpeechResult:
        """One line through the engine. Raises VoiceEngineError (503) at once when the engine isn't ready, EngineBusy when the engine
        itself says the line waited too long."""
        if self.state != "ready" or not self.proc or self.proc.poll() is not None:
            if self.proc and self.proc.poll() is not None and self.state in ("ready", "getting_ready", "starting"):
                self._died(self.proc.returncode)
            raise VoiceEngineError(503, "The voice isn't running.")
        ref_wav = self.clips.resolve(voice, ref_wav)          # the player's own clip for that voice, else the first one SkyrimNet sent (voice_clips.py)
        words = await self._line_words(voice, ref_wav)
        return await self._speak(text, ref_wav, temperature=temperature, seed=seed, top_p=top_p, busy_timeout_ms=busy_timeout_ms, timeout=timeout,
                                 reference_text=words)

    # ---- the words of a clip, worked out by the voice itself (Qwen3-ASR) ----
    def can_transcribe(self) -> bool:
        """True when the chosen voice needs a clip's words and the model that works them out is installed here."""
        vm = self.vmodel
        return bool(vm.needs_words and vm.companion is not None and (self.models_dir / vm.companion.name).is_file() and self.find_exe() is not None)

    def clips_rows(self) -> list:
        """The clips list for the screen: each row also says whether this device can work out a clip's words by itself."""
        can = self.can_transcribe()
        return [{**r, "canTranscribe": can} for r in self.clips.listing()]

    async def _line_words(self, voice: Optional[str], ref_wav: Optional[bytes]) -> str:
        """The words spoken in the clip a line is made with: the typed or already-worked-out ones, else (Qwen3-TTS only) worked out now, once, and kept beside the clip.
        "" when there are none and none can be had: the line is then refused with the plain sentence, as before this existed."""
        words = self.clips.words_for(voice)
        vm = self.vmodel
        if words or ref_wav is None or not vm.needs_words or vm.companion is None:
            return words
        from voice_clips import voice_key
        key = voice_key(voice or "")
        if not key or not self.can_transcribe() or self.clips.words_failed(key, ref_wav):      # (no model here: nothing to try, and nothing to remember as a failure)
            return ""
        async with self._asr_lock:
            words = self.clips.words_for(voice)               # (another line of the same new voice may just have finished it)
            if words:
                return words
            t0 = time.time()
            try:
                text = await self.transcribe(ref_wav)
            except VoiceEngineError as e:
                self.clips.note_words_failed(key, ref_wav)
                self.log(f"Couldn't work out the words of the clip for {key} ({e.message}); the words have to be typed.")
                return ""
            self.clips.save_auto_words(key, ref_wav, text)
            self.log(f"Worked out the words of the clip for {key} in {time.time() - t0:.1f} s ({len(text)} letters, language found by the model)")
            return text

    async def transcribe(self, wav: bytes) -> str:
        """The words spoken in a clip, worked out by the Qwen3-ASR model that came with the Qwen3-TTS voice. Runs the engine's own program a second time with only that
        model, on this voice's card (the processor if the card has no room), and stops it again straight after, so the voice keeps its graphics memory. The model finds
        the language itself. Raises VoiceEngineError (a plain sentence for the log) when it can't; returns the cleaned words otherwise."""
        from voice_clips import ClipFormatError, asr_clip
        comp = self.vmodel.companion
        path = self.models_dir / comp.name if comp is not None else None
        exe = self.find_exe()
        if path is None or not path.is_file() or exe is None:
            raise VoiceEngineError(409, "The speech-to-text tool isn't installed.")
        try:
            body = await asyncio.to_thread(asr_clip, wav)
        except ClipFormatError:
            raise VoiceEngineError(400, "The clip couldn't be read.")
        build = self.builds.get(self._read_state().get("build")) or next(iter(self.builds.values()))
        device, env = self._asr_launch
        last: Optional[VoiceEngineError] = None
        for backend, dev in ((build.backend, device), ("cpu", 0)) if build.backend != "cpu" else (("cpu", 0),):
            try:
                return await self._transcribe_with(exe, path, body, backend, dev, env)
            except VoiceEngineError as e:
                last = e
                self.log(f"Transcription on {backend} failed: {e.message}")
        raise last or VoiceEngineError(502, "Couldn't understand the words in the clip.")

    async def _transcribe_with(self, exe: Path, model_path: Path, wav16: bytes, backend: str, device: int, env) -> str:
        port = _free_port(*self._port_range)
        cfg_path = self.folder / "asr-server.json"
        cfg = {"host": "127.0.0.1", "port": port, "backend": backend, "device": device, "threads": 4 if backend == "cpu" else 1, "lazy_load": False,
               "models": [{"id": ASR_ID, "family": "qwen3_asr", "path": str(model_path).replace("\\", "/"), "task": "asr", "mode": "offline"}]}
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
        out = open(self.folder / "engine-asr-stdout.log", "wb")
        proc = None
        try:
            try:
                extra = {"env": env} if env is not None else {}
                if os.name != "nt":
                    extra["start_new_session"] = True         # its own group: stopping it must never signal this program's group
                proc = hidden_process.popen(self._launcher(exe, cfg_path, self.folder / "engine-asr-trace.log"), cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                                            stdout=out, stderr=subprocess.STDOUT, **extra)
            except OSError as e:
                raise VoiceEngineError(502, f"Couldn't start speech-to-text ({e.__class__.__name__}).")
            session = await self._http()
            deadline = time.time() + ASR_LOAD_SECONDS
            while True:
                if proc.poll() is not None:
                    raise VoiceEngineError(502, "Speech-to-text crashed while starting.")
                try:
                    async with session.get(f"http://127.0.0.1:{port}/health", timeout=aiohttp.ClientTimeout(total=3)) as r:
                        if r.status == 200:
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                if time.time() > deadline:
                    raise VoiceEngineError(504, "Speech-to-text took too long to load.")
                await asyncio.sleep(0.2)
            form = aiohttp.FormData()
            form.add_field("file", wav16, filename="clip.wav", content_type="audio/wav")
            form.add_field("model", ASR_ID)
            try:
                async with session.post(f"http://127.0.0.1:{port}/v1/audio/transcriptions", data=form, timeout=aiohttp.ClientTimeout(total=ASR_RUN_SECONDS)) as r:
                    txt = await r.text()
                    if r.status != 200:
                        raise VoiceEngineError(502, f"Speech-to-text rejected the clip ({r.status}): {self.redact(txt[:200])}")
            except asyncio.TimeoutError:
                raise VoiceEngineError(504, "Reading the words from the clip took too long.")
            except aiohttp.ClientError as e:
                raise VoiceEngineError(503, f"Couldn't reach the speech-to-text tool ({e.__class__.__name__}).")
            try:
                raw = json.loads(txt).get("text", "")
            except (ValueError, AttributeError):
                raw = txt
            words = clean_transcript(raw if isinstance(raw, str) else "")
            if not words:
                raise VoiceEngineError(502, "No words were found in the clip.")
            return words
        finally:
            if proc is not None and proc.poll() is None:
                kill_tree_sync(proc.pid)                       # only the program started just now, by its own id
            try:
                out.close()
            except OSError:
                pass

    def _refusal(self, status: int, txt: str) -> VoiceEngineError:
        """The engine said no to a line: one short plain sentence for the screen; the engine's own words go to the log only."""
        raw = self.redact(txt[:300]).strip()
        self.log(f"The engine refused a line ({status}): {raw}")
        if "reference text" in txt.lower():
            return NeedsWords()
        e = VoiceEngineError(502, "The voice refused that line. Open **Log** to see why.")
        e.raw = raw
        return e

    def _speech_body(self, text: str, ref_wav: Optional[bytes], temperature, seed, top_p, busy_timeout_ms, reference_text: str = "") -> dict:
        if ref_wav is not None and len(ref_wav) > REFERENCE_MAX_BYTES:
            raise VoiceEngineError(400, "That voice clip is too big for the voice engine.")
        vm = self.vmodel
        if ref_wav is None and vm.needs_clip:
            raise VoiceEngineError(400, f"{vm.name} needs a voice clip for every voice.")
        if ref_wav is not None and vm.needs_words and not reference_text.strip():
            raise NeedsWords()
        if not vm.uses_tags:
            text = _ANY_TAG.sub("", text)                       # only Higgs knows the <|emotion:fear|> tags: anywhere else they would be read out loud
        body: dict[str, Any] = {"model": vm.engine_id, "input": text}
        if ref_wav is not None:
            body["voice_ref"] = {"type": "base64", "data": base64.b64encode(ref_wav).decode("ascii")}
        if ref_wav is not None and reference_text.strip():
            body["reference_text"] = reference_text.strip()
        if temperature is not None:
            body["temperature"] = temperature
        if top_p is not None:
            body["top_p"] = top_p
        if seed is not None:
            body["seed"] = seed
        if busy_timeout_ms is not None:
            body["busy_timeout_ms"] = max(1, int(busy_timeout_ms))
        return body

    @asynccontextmanager
    async def open_speech(self, text: str, ref_wav: Optional[bytes] = None, *, temperature: Optional[float] = None, seed: Optional[int] = None,
                          top_p: Optional[float] = None, busy_timeout_ms: Optional[int] = None, timeout: float = 60, voice: Optional[str] = None):
        """Post one line to the engine and hand back its (successful) answer to be read in pieces: `async with engine.open_speech(...) as r:`.
        Every way the engine can refuse is turned into our plain errors here (busy, ran out of room, not ready...), before any audio is read."""
        if self.state != "ready" or not self.proc or self.proc.poll() is not None:
            if self.proc and self.proc.poll() is not None and self.state in ("ready", "getting_ready", "starting"):
                self._died(self.proc.returncode)
            raise VoiceEngineError(503, "The voice isn't running.")
        ref_wav = self.clips.resolve(voice, ref_wav)
        body = self._speech_body(text, ref_wav, temperature, seed, top_p, busy_timeout_ms, await self._line_words(voice, ref_wav))
        session = await self._http()
        try:
            async with session.post(f"http://127.0.0.1:{self.port}/v1/audio/speech", json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 503:
                    try:
                        err = await r.json(content_type=None)
                    except Exception:
                        err = {}
                    if (err.get("error") or {}).get("type") == "server_busy":
                        raise EngineBusy()
                    raise VoiceEngineError(503, "The voice isn't ready.")
                if r.status != 200:
                    txt = (await r.text())[:400]
                    if "max_tokens" in txt and "before EOC" in txt:          # the engine's own words for "ran out of room before the end of the line"
                        raise EngineTokenLimit()
                    raise self._refusal(r.status, txt)
                yield r
        except asyncio.TimeoutError:
            raise VoiceEngineError(504, "The voice took too long to speak that line.")
        except aiohttp.ClientError as e:
            raise VoiceEngineError(503, f"Couldn't reach the voice ({e.__class__.__name__}).")

    def note_line_done(self, wall_ms: Optional[float], rtf: Optional[float]) -> None:
        self.last_line_ms, self.last_rtf = wall_ms, rtf
        self.lines_done += 1

    async def _speak(self, text: str, ref_wav: Optional[bytes], *, temperature: Optional[float] = None, seed: Optional[int] = None,
                     top_p: Optional[float] = None, busy_timeout_ms: Optional[int] = None, timeout: float = 60, reference_text: str = "") -> SpeechResult:
        body = self._speech_body(text, ref_wav, temperature, seed, top_p, busy_timeout_ms, reference_text)
        session = await self._http()
        t0 = time.time()
        try:
            async with session.post(f"http://127.0.0.1:{self.port}/v1/audio/speech", json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 503:
                    try:
                        err = await r.json(content_type=None)
                    except Exception:
                        err = {}
                    if (err.get("error") or {}).get("type") == "server_busy":
                        raise EngineBusy()
                    raise VoiceEngineError(503, "The voice isn't ready.")
                if r.status != 200:
                    txt = (await r.text())[:400]
                    if "max_tokens" in txt and "before EOC" in txt:          # the engine's own words for "ran out of room before the end of the line"
                        raise EngineTokenLimit()
                    raise self._refusal(r.status, txt)
                wav = await r.read()
                h = r.headers
        except asyncio.TimeoutError:
            raise VoiceEngineError(504, "The voice took too long to speak that line.")
        except aiohttp.ClientError as e:
            raise VoiceEngineError(503, f"Couldn't reach the voice ({e.__class__.__name__}).")

        def num(k):
            try:
                return float(h.get(k)) if h.get(k) is not None else None
            except ValueError:
                return None
        res = SpeechResult(wav=wav, wall_ms=num("X-AudioCPP-Wall-Ms") or (time.time() - t0) * 1000, audio_ms=num("X-AudioCPP-Audio-Duration-Ms"), rtf=num("X-AudioCPP-RTF"))
        self.last_line_ms = res.wall_ms
        self.last_rtf = res.rtf
        self.lines_done += 1
        return res

    async def _stop_quietly(self, keep_state: bool = False) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            kill_tree_sync(proc.pid)
            for _ in range(40):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.1)
        self.proc = None
        await self.stop_recorded()                  # an engine an earlier run of this manager left behind (by the id it wrote down)
        pump = getattr(self, "_pump", None)
        if pump is not None:
            await asyncio.to_thread(pump.join, 1.5)
            self._pump = None
        if not keep_state:
            self._refresh_state_after_stop()

    # ---- the engine this manager started, by its written-down id ----
    @property
    def pid_file(self) -> Path:
        return self.folder / "engine.pid"

    def _record_pid(self, pid: int, image: str) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(json.dumps({"pid": pid, "image": image}), encoding="utf-8")
        except OSError:
            pass

    def _recorded(self) -> Optional[tuple]:
        try:
            d = json.loads(self.pid_file.read_text(encoding="utf-8"))
            return int(d["pid"]), str(d.get("image") or "")
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def stop_recorded(self, wait: Optional[float] = None) -> bool:
        """Stop the engine written down in the pid file, if that id still belongs to the same program, and wait for it to exit. Never touches any other program.
        True when nothing of ours is left running; False when it would not exit within `wait` seconds (the file stays, so nothing is removed under it)."""
        rec = self._recorded()
        if rec is None:
            return True
        pid, image = rec
        wait = STOP_WAIT_SECONDS if wait is None else wait
        if not same_image(await asyncio.to_thread(process_image, pid), image):
            self.pid_file.unlink(missing_ok=True)            # gone already, or the id now belongs to something else: leave that alone
            return True
        await asyncio.to_thread(kill_tree_sync, pid)
        deadline = time.time() + wait
        while time.time() < deadline:
            if not same_image(await asyncio.to_thread(process_image, pid), image):
                self.pid_file.unlink(missing_ok=True)
                return True
            await asyncio.sleep(0.25)
        self.log(f"The engine (id {pid}) did not exit within {wait:g} s")
        return False

    async def reap_if_not_installed(self) -> None:
        """An engine that is still alive with no voice installed is stopped (by its recorded id)."""
        try:
            if self.pid_file.exists() and not self.installed():
                await self.stop_recorded()
        finally:
            self._reaping = None

    def _refresh_state_after_stop(self) -> None:
        self.state = "stopped" if self.installed() else "not_installed"
        self.detail = ""

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_quietly()
            self.log("Stopped")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def _remove_own_folder(self, d: Path) -> None:
        """Delete one of this engine's own folders (engine, models, clips), and only when it really is inside the voice folder and is not a link (a folder the player
        pointed somewhere else is left alone); links inside are removed, never followed."""
        try:
            program_uninstall.confine(self.folder, d)
        except program_uninstall.UninstallError:
            self.log("Left a folder alone: it isn't inside the voice folder")
            return
        _, left = program_uninstall.remove_tree(d, 8, 0.5)
        if left:
            self.log(f"{len(left)} file(s) in {d.name} are still in use and were left behind. {STILL_RUNNING}")

    async def uninstall(self, keep_clips: bool = True, model_key: Optional[str] = None) -> int:
        """Stop and delete the engine and the model (not the kept downloads: clear those separately; not the player's clips unless asked).
        Returns the bytes freed. With `model_key` and another voice model installed beside it, only that model's file goes: the engine, the other voice and the
        clips stay, and the other voice becomes the chosen one."""
        if model_key is not None:
            if model_key not in self.catalog:
                raise VoiceEngineError(400, "MultiProxy doesn't recognize that voice.")
            others = [k for k in self.installed_models() if k != model_key]
            if others and model_key in self.installed_models():
                return await self._uninstall_one(model_key, others)
            if model_key not in self.installed_models():         # not installed here: nothing to remove
                return 0
        async with self._lock:
            await self._stop_quietly()
        if self._recorded() is not None:                  # it would not exit: remove nothing under it
            self.log(STILL_RUNNING)
            raise VoiceEngineError(409, STILL_RUNNING)
        freed = 0
        for d in (self.engine_dir, self.models_dir) + (() if keep_clips else (self.clips_dir,)):
            if d.is_dir():
                for p in d.rglob("*"):
                    try:
                        if p.is_file() and not p.is_symlink() and (p.stat().st_nlink <= 1 or d != self.models_dir):
                            freed += p.stat().st_size
                    except OSError:
                        pass
                await asyncio.to_thread(self._remove_own_folder, d)
        for f in (self.state_file, self.folder / "server.json"):
            f.unlink(missing_ok=True)
        if self.model_key != DEFAULT_VOICE_MODEL:
            self._write_state(active=self.model_key)             # the chosen voice is remembered even with nothing installed
        self.state, self.detail, self.job = "not_installed", "", None
        self.log("Uninstalled")
        return freed

    async def _uninstall_one(self, key: str, others: list) -> int:
        """Remove one voice model's file and nothing else (only called while another voice model is installed beside it)."""
        vm = self.catalog[key]
        async with self._lock:
            if key == self.model_key:
                await self._stop_quietly()
            freed = 0
            for spec in (vm.file,) + ((vm.companion,) if vm.companion else ()):
                path = self.models_dir / spec.name
                try:
                    if path.is_file() and not path.is_symlink():
                        if path.stat().st_nlink <= 1:
                            freed += path.stat().st_size
                        path.unlink()
                except OSError:
                    pass
            if key == self.model_key:
                self._apply_model(others[0])
                self._write_state(active=others[0], first_run_done=False)
                self.state, self.detail, self.speed_check, self.job = "stopped", "", None, None
        self.log(f"Uninstalled the {vm.name} model (the {self.vmodel.name} model stays)")
        return freed

    async def close(self) -> None:
        await self._stop_quietly()
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- status ----
    def status(self) -> dict:
        self._refresh_state()
        if self.state == "not_installed" and self.proc is None and not self._reaping and self.pid_file.exists():
            try:
                self._reaping = asyncio.get_running_loop().create_task(self.reap_if_not_installed())      # (an engine still alive with no voice installed is stopped, by its own id)
            except RuntimeError:
                pass
        if self.proc is not None and self.proc.poll() is not None and self.state in ("ready", "too_slow", "getting_ready", "starting"):
            self._died(self.proc.returncode)
        st = self._read_state()
        vram = None
        if self.state in ("ready", "getting_ready", "too_slow") and self._vram_baseline is not None:
            used = self._gpu_used_mb()
            if used is not None:
                vram = max(0, used - self._vram_baseline)
        label = {"not_installed": "Not installed", "installing": "Installing", "stopped": "Stopped", "starting": "Starting", "getting_ready": "Getting ready",
                 "ready": "Running", "too_slow": "Too slow for live talk", "died": "Stopped unexpectedly", "error": "Problem"}.get(self.state, self.state)
        waiting = self.waiting_fn()
        if self.state == "ready" and waiting:
            label = f"Busy ({waiting} line{'s' if waiting != 1 else ''} waiting)"
        return {
            "state": self.state, "label": label, "detail": self.detail, "ready": self.state == "ready",
            "installed": self.installed(), "build": st.get("build"), "engineVersion": st.get("engine"),
            "model": self.model_key, "modelName": self.vmodel.name, "installedModels": self.installed_models(),
            "gpuMemoryMb": vram, "lastLineMs": round(self.last_line_ms) if self.last_line_ms else None,
            "lastLineRtf": round(self.last_rtf, 2) if self.last_rtf else None, "linesDone": self.lines_done, "linesWaiting": waiting,
            "speedCheck": self.speed_check, "gpuNote": self.gpu_note, "gpuCard": self.gpu_card, "install": self.job.to_json() if self.job else None,
            "installStep": STEP_LABELS.get(self.job.step) if self.job and self.job.state == "running" else None,
            "downloads": self.store.listing(),
        }
