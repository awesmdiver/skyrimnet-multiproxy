"""Local models, part 1 (2026-09-30): run Gemma 4 and friends on THIS device with llama.cpp.

Built to design/dashboard-local-models-mockup.html (sections 1-3). Everything that isn't the web
routes lives here, in one module with no dependency on multiproxy.py (multiproxy.py imports this, never the
other way round), so it can be unit-tested without starting the proxy:

  * graphics card detection (name + REAL memory -- Windows' usual report caps at 4 GB)
  * which llama.cpp build suits the card, and the pinned build's download list
  * the curated model list (real files, real byte sizes, real checksums) and the fit check
  * resumable, checksum-verified downloads with real progress
  * starting / stopping `llama-server` per model, with crash-safe cleanup of leftovers

See TECHNICAL.md ("Local models, part 1") for the why behind the numbers.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import gpu_choice
import gpu_guard
import skyrim_peak
import hidden_process  # every program started from here goes through it: no console window flashing up
import sys
import time
import tarfile
import zipfile
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

import download_meter
import download_retry
import https_only
import local_engine_flags
import program_uninstall
import safe_paths
import local_engine_settings
import llama_builds

LOCAL_PREFIX = "local/"

# --------------------------------------------------------------------------------------------
# The pinned engine build. A constant, on purpose: llama.cpp publishes new builds several times a
# day and a model/engine pair that worked yesterday shouldn't change under a player. Bump this by
# hand after testing a newer build (and update ENGINE_FILES below -- the sizes and checksums are
# that build's own, copied from its GitHub release page).
# --------------------------------------------------------------------------------------------
LLAMA_CPP_BUILD = "b11361"
_RELEASE_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{build}/{name}"
# The first build that answers `POST /v1/systemone` itself (llama.cpp's own decision models, ggml-org/llama.cpp#29818, merged 2026-10-02). The decision model needs this one
# or a newer one; an engine downloaded before it is fetched again, once, when the decision model is installed (`engine_exe_current`).
NATIVE_DECISION_MIN_BUILD = 11361

# (file name, size in bytes, sha256) per engine kind, exactly as GitHub lists them for the pinned
# build. The NVIDIA builds come as two zips: the program, plus NVIDIA's own runtime libraries.
ENGINE_FILES: dict[str, list[tuple[str, int, str]]] = {
    "cuda-13.4": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-win-cuda-13.4-x64.zip", 152832067, "3ff36c1876fa8931d25ae5dd049397a7d489a3c5db3cacceb44ea623539de10e"),
        ("cudart-llama-bin-win-cuda-13.4-x64.zip", 423535356, "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668"),
    ],
    "cuda-12.4": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-win-cuda-12.4-x64.zip", 263353674, "e9b6a0eb9506dcabcc3dbe9b561c50e85ff06517753ab56a3fe4104c7631787f"),
        ("cudart-llama-bin-win-cuda-12.4-x64.zip", 391443627, "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"),
    ],
    "vulkan": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-win-vulkan-x64.zip", 33262554, "5a0825d7d8f0e27aaf9b1afb0712dc41c384876f36a45b81dc222c90b1b41994"),
    ],
    "cpu": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-win-cpu-x64.zip", 19333899, "0e0a61a1da9832724a292ce371361ba53bfea03c19fa72ddcfd22a4d846fc596"),
    ],
    # The faster builds for one maker's cards (Advanced, offered only when that card is found). Each carries its own runtime, so they need only a recent driver.
    "rocm": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-win-rocm-10.0-x64.zip", 256269247, "e15b021df62a80fc62fda0722205a4d8a2e41a599e4f00a9ae74eeafe00ca527"),
    ],
    "sycl": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-win-sycl-x64.zip", 147897565, "48437db6f80df5d8c3c5ca8ecd758893b6d641318d3148cda6ee06b34eea2b09"),
    ],
}
# Linux (Ubuntu-built, works on most distros), same pinned build. .tar.gz, not .zip. NVIDIA = the program
# plus NVIDIA's runtime libraries (two archives); AMD ROCm and Vulkan/CPU are one archive each.
ENGINE_FILES_LINUX: dict[str, list[tuple[str, int, str]]] = {
    "cuda-13.4": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-cuda-13.4-x64.tar.gz", 152291234, "14362dda75b01bbd0750820acc5fab901d8e313d54346b7c6715dbf460cd491b"),
        (f"cudart-llama-{LLAMA_CPP_BUILD}-bin-ubuntu-cuda-13.4-x64.tar.gz", 440236672, "40339a0fa9035d878bd6f7270f7e63aaa977f723de40eebbb143f2143b569cdb"),
    ],
    "cuda-12.8": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-cuda-12.8-x64.tar.gz", 171328579, "b8616ebde36d7fe35ce6954ed045f359469e7fb681dd577d7b8f7d4eaf041b4d"),
        (f"cudart-llama-{LLAMA_CPP_BUILD}-bin-ubuntu-cuda-12.8-x64.tar.gz", 594377705, "8eb9c6d3fdc4b799a3f8ba66c90464145fbd01e13c6f35ef96c8ec981edd2046"),
    ],
    "rocm": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-rocm-10.0-x64.tar.gz", 243082904, "5c25dba56e5ac22351dcf7e94142774524f04e46cc149ba2983b88256d502c85"),
    ],
    "vulkan": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-vulkan-x64.tar.gz", 31582369, "0d2ef1ccef53e878d94e921eee4b0554ffc58452d1a9c628f3c221cb48c15eff"),
    ],
    "cpu": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-x64.tar.gz", 17638585, "1b22cc0a6de07ff2f8a5b8cdebc75ba9cf157bab872b99f56e3ba15de6f2c469"),
    ],
}
ENGINE_FILES_LINUX_ARM64: dict[str, list[tuple[str, int, str]]] = {
    "vulkan": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-vulkan-arm64.tar.gz", 24814223, "8f5074fa0d6a702c8c9fc6e5eff6c19696d5fe55f0d56dd32497f4312149069f"),
    ],
    "cpu": [
        (f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-arm64.tar.gz", 13653725, "331e2ba102afadc33c1ce10e2bc7eaa09eaa9857ea8693bd423d91ab988fbde9"),
    ],
}


def _platform() -> str:
    """sys.platform, as a function so tests can pretend to be another operating system."""
    return sys.platform


def engine_files_table() -> dict:
    """The engine downloads for THIS computer's operating system (and, on Linux, processor type)."""
    if _platform().startswith("linux"):
        arm = platform.machine().lower() in ("aarch64", "arm64")
        return ENGINE_FILES_LINUX_ARM64 if arm else ENGINE_FILES_LINUX
    return ENGINE_FILES


def platform_supported() -> bool:
    """Windows and Linux can download and run the engine; macOS isn't covered (yet)."""
    return _platform() == "win32" or _platform().startswith("linux")


ENGINE_LABELS = {
    "cuda-13.4": ("NVIDIA version", "Fastest on NVIDIA cards."),
    "cuda-12.4": ("NVIDIA version", "Fastest on NVIDIA cards."),
    "cuda-12.8": ("NVIDIA version", "Fastest on NVIDIA cards."),
    "rocm": ("AMD version", "Can be faster on AMD cards, but needs a recent AMD driver. If it won't start, MultiProxy switches back to the works-on-anything version."),
    "sycl": ("Intel version", "Can be faster on Intel graphics, but needs a recent Intel driver. If it won't start, MultiProxy switches back to the works-on-anything version."),
    "vulkan": ("Works-on-anything version", "Runs on NVIDIA, AMD and Intel cards. A bit slower on NVIDIA."),
    "cpu": ("No graphics card", "Uses the processor only. Slow; small models only."),
}
# The faster builds for AMD and Intel cards: Advanced, never the default. When one won't start, the works-on-anything (Vulkan) build of the same release takes over.
FAST_KINDS = ("rocm", "sycl")
FALLBACK_NOTE = "The faster version wouldn't start on this card, so MultiProxy switched to the works-on-anything version."
# The oldest NVIDIA CUDA a build needs from the player's driver (their driver reports the newest
# CUDA it can run; a build needing newer than that won't start).
_CUDA_NEEDS = {"cuda-13.4": (13, 4), "cuda-12.8": (12, 8), "cuda-12.4": (12, 4)}

# --------------------------------------------------------------------------------------------
# The curated model list. Real files, confirmed on Hugging Face 2026-09-30 (sizes are the exact
# byte counts Hugging Face reports, sha256 is the file's own LFS checksum). Source:
# lmstudio-community's GGUF conversions of Google's Gemma 4 -- Q4_K_M (a good size/quality balance)
# plus the vision file (mmproj), which a vision model is useless without.
# --------------------------------------------------------------------------------------------
HF_URL = "https://huggingface.co/{repo}/resolve/main/{file}"


def _gemma(id_, name, blurb, repo, stem, size, sha, mm_size, mm_sha):
    return {
        "id": id_, "name": name, "blurb": blurb, "vision": True, "repo": repo,
        "files": [
            {"file": f"{stem}-Q4_K_M.gguf", "size": size, "sha256": sha, "role": "model"},
            {"file": f"mmproj-{stem}-BF16.gguf", "size": mm_size, "sha256": mm_sha, "role": "mmproj"},
        ],
    }


CATALOG: list[dict] = [
    # E2B and E4B added 2026-10-26: the same source and quant; size and SHA-256 are Hugging Face's own file listing (the LFS oid) for each repo on that day.
    _gemma("gemma-4-e2b", "Gemma 4 E2B", "Smallest, for the weakest PCs. Simpler answers",
           "lmstudio-community/gemma-4-E2B-it-GGUF", "gemma-4-E2B-it",
           3427880384, "71e6e8cb64a76da1a734fb8f6ba389d2784950d9902f5e87244fca0a15190c94",
           986833248, "04125f8e2a1135824e48064c32157356704e6681a9f3af669af321a3c70c39d7"),
    _gemma("gemma-4-e4b", "Gemma 4 E4B", "Small and fast, for PCs with less graphics memory",
           "lmstudio-community/gemma-4-E4B-it-GGUF", "gemma-4-E4B-it",
           5335291936, "0ffb122c8b6921f13cbc34186e052524d0b5803b17f4867b7197a561400b3770",
           991551840, "bdfc4935857658dfdc8d1adebfa3897c2fec8b3323410f4f73ade36d6efa02be"),
    _gemma("gemma-4-12b", "Gemma 4 12B", "Quick, good for dialogue and seeing pictures",
           "lmstudio-community/gemma-4-12B-it-GGUF", "gemma-4-12B-it",
           7381382944, "95d83ba36642b1f385fb906b5962a71763361be3bac930a709945f72d97473f8",
           175115296, "7fd884a5f7d9ee60f6b88e5b51287151e547454b2b60a4a6270bb5434310ad0e"),
    _gemma("gemma-4-26b-a4b", "Gemma 4 26B A4B", "Smarter, still fast. Great for dialogue",
           "lmstudio-community/gemma-4-26B-A4B-it-GGUF", "gemma-4-26B-A4B-it",
           16796017248, "e19514d9541344001cb7c8eaf88704b8bf2e5d8f4e9aac12402eed360cffdfc4",
           1194827744, "85f81a6a844d2f63c0fa64fa0899758429df791adb3886cdf09974d7429d0a3e"),
    _gemma("gemma-4-31b", "Gemma 4 31B", "The smartest. Best if this device isn't running the game at the same time",
           "lmstudio-community/gemma-4-31B-it-GGUF", "gemma-4-31B-it",
           18687063904, "8f3479643461601703f717931e399cc440dbd5ada598c025d6296c8165d94be9",
           1200725984, "5e32614a9cdeaf6e30c2f316f0ded1b8edfad1aed1150611fd0b7e0ce36d56af"),
]
CATALOG_BY_ID = {m["id"]: m for m in CATALOG}

# --------------------------------------------------------------------------------------------
# Fit check numbers (explained in TECHNICAL.md)
# --------------------------------------------------------------------------------------------
GIB = 1024 ** 3
WORKING_ROOM_BYTES = int(1.5 * GIB)        # the model's short-term memory (context) + scratch space
SKYRIM_SHARE_FRACTION = 0.25               # Skyrim's rough share of the card ...
SKYRIM_SHARE_MIN = 3 * GIB                 # ... but never assume less than 3 GB
SKYRIM_SHARE_MAX = 10 * GIB                # ... or more than 10 GB (a heavily modded 4K game on a big card measured 20 GB)
# When Skyrim's real busiest point is known (the monitor learns it), the guess above isn't used at all:
SKYRIM_EVERYTHING_ELSE = 2 * GIB           # windows, browser, overlays: what else is on the card while playing
SKYRIM_FIT_MARGIN = GIB                    # breathing room below the free memory before it's called a comfortable fit

# llama-server settings: big enough for SkyrimNet's long prompts, two lanes so a dialogue reply and
# a background job don't queue behind each other (the context is split between the lanes).
SERVER_CONTEXT = local_engine_settings.DEFAULT_CTX            # the defaults of a device that never saved settings (local_engine_settings.py)
SERVER_PARALLEL = local_engine_settings.DEFAULT_PARALLEL
# Chat launches run at llama.cpp's default log level. `-lv 4` would print the layer count and the memory breakdown (what the Courier's "how much is on the
# card" display needs), but measured 2026-10-04 on the pinned build it writes about 10x the log of the default (16 KB at start and 4.6 to 7.8 KB per request
# against 1 KB and 0.8 KB) and never stops growing, so it is NOT passed until the log is bounded. Set this to 4 only together with a size cap on the log.
CHAT_LOG_LEVEL: Optional[int] = None
SERVER_PORT_START = 18080                  # never 8000 (this proxy) or 9999
SERVER_PORT_END = 18180
ENGINE_UPDATE_CHECK_SECONDS = 3600         # how long the list of llama.cpp releases is kept before it is read again
SERVER_LOAD_TIMEOUT = 600.0                # seconds a big model may take to load from disk
HEALTH_POLL_SECONDS = 0.5                  # how often a starting model is asked "are you ready yet?"
REQUEST_WAIT_TIMEOUT = 180.0               # how long a chat request waits for a still-loading model


DOWNLOAD_INTERRUPTED = "The download was interrupted. Press Download to pick up where it left off."
ENGINE_FAILED_OFFLINE = "Couldn't download the software. Check your internet connection and try again."


class LocalModelError(Exception):
    """A plain, player-readable reason a local model can't answer, with the HTTP status to report."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class DownloadStatusError(download_retry.HttpStatus, LocalModelError):
    """A site answered a download with a status that is not the file. Both things at once: a `LocalModelError` with the usual words for a caller that calls
    `download_file` directly, and the status `download_retry` needs to decide whether to try again and what to say."""

    def __init__(self, status: int, url: str = "", retry_after=None):
        LocalModelError.__init__(self, 502, f"The download failed (the site answered {status}). Press Download to try again.")
        self.http_status, self.url, self.retry_after = status, url, retry_after


# --------------------------------------------------------------------------------------------
# Graphics card detection
# --------------------------------------------------------------------------------------------
@dataclass
class GpuInfo:
    name: str
    memory_mb: Optional[int]          # None = couldn't be read reliably -> skip the fit check
    vendor: str                       # "nvidia" | "amd" | "intel" | "other"
    cuda: Optional[tuple] = None      # newest CUDA version the driver supports, e.g. (13, 4)
    source: str = ""                  # "nvidia-smi" | "registry"

    def to_json(self) -> dict:
        return {
            "name": self.name, "memoryGb": round(self.memory_mb / 1024, 1) if self.memory_mb else None,
            "vendor": self.vendor,
        }


def vendor_of(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n:
        return "amd"
    if "intel" in n:
        return "intel"
    return "other"


def parse_nvidia_smi(query_csv: str, plain_text: str = "") -> list[GpuInfo]:
    """`nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` -> one GpuInfo per
    card (memory in MiB). `plain_text` is the plain `nvidia-smi` screen, only read for the CUDA
    version its header reports ("CUDA Version: 12.4", or "CUDA UMD Version: 13.4" on newer drivers)."""
    cuda = None
    m = re.search(r"CUDA\s+(?:UMD\s+)?Version:\s*(\d+)\.(\d+)", plain_text or "")
    if m:
        cuda = (int(m.group(1)), int(m.group(2)))
    cards = []
    for line in (query_csv or "").splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        name, _, mem = line.rpartition(",")
        try:
            mem_mb = int(float(mem.strip()))
        except ValueError:
            mem_mb = None
        name = name.strip()
        if name:
            cards.append(GpuInfo(name=name, memory_mb=mem_mb, vendor="nvidia", cuda=cuda, source="nvidia-smi"))
    return cards


def parse_rocm_smi(json_text: str) -> list[GpuInfo]:
    """`rocm-smi --showproductname --showmeminfo vram --json` -> one GpuInfo per AMD card. The key
    names differ a little between ROCm versions, so match them loosely."""
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError):
        return []
    cards = []
    if not isinstance(data, dict):
        return cards
    for key, card in data.items():
        if not str(key).lower().startswith("card") or not isinstance(card, dict):
            continue
        name, mem = None, None
        for k, v in card.items():
            kl = str(k).lower()
            if name is None and ("series" in kl or "product name" in kl or "card model" in kl):
                name = str(v).strip()
            if "vram total" in kl and "used" not in kl:
                try:
                    mem = int(int(str(v).strip()) / (1024 * 1024))
                except ValueError:
                    mem = None
        cards.append(GpuInfo(name=name or "AMD Radeon graphics card", memory_mb=mem, vendor="amd", source="rocm-smi"))
    return cards


def _lspci_gpus(run: Callable[..., Any]) -> list[GpuInfo]:
    """Last resort on Linux: the card's name from `lspci` (no memory size, so no fit check)."""
    try:
        out = run(["lspci", "-mm"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    cards = []
    for line in out.splitlines():
        low = line.lower()
        if "vga compatible" in low or "3d controller" in low or "display controller" in low:
            parts = [p for p in re.findall(r'"([^"]*)"', line)]
            if len(parts) >= 3:
                name = f"{parts[1]} {parts[2]}".strip()
                cards.append(GpuInfo(name=name, memory_mb=None, vendor=vendor_of(name), source="lspci"))
    return cards


def _registry_gpus() -> list[GpuInfo]:
    """Windows' own record of each display adapter, read from the registry. The real memory size
    lives in a 64-bit value here, unlike the old 32-bit figure most tools print (which tops out at
    4 GB). Works for NVIDIA, AMD and Intel alike."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    cards = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return []
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    try:
                        name = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                    except OSError:
                        continue
                    mem = None
                    try:
                        raw = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0]
                        if isinstance(raw, (bytes, bytearray)):
                            raw = int.from_bytes(raw[:8], "little")
                        mem = int(raw) // (1024 * 1024)
                    except (OSError, ValueError, TypeError):
                        mem = None
            except OSError:
                continue
            if "basic" in name.lower() and "adapter" in name.lower():
                continue
            cards.append(GpuInfo(name=name, memory_mb=mem or None, vendor=vendor_of(name), source="registry"))
    return cards


def pick_gpu(cards: list[GpuInfo]) -> Optional[GpuInfo]:
    """The card the models would run on: the one with the most memory (a laptop's built-in
    graphics loses to the real card), preferring a card whose memory we could read."""
    if not cards:
        return None
    return max(cards, key=lambda c: (c.memory_mb or 0, c.vendor in ("nvidia", "amd")))


def detect_gpu(run: Callable[..., Any] = hidden_process.run) -> Optional[GpuInfo]:
    """Name + real memory of this device's main graphics card, or None. NVIDIA's own tool first (most
    trustworthy, and it also tells us which CUDA the driver supports), then Windows' registry."""
    cards: list[GpuInfo] = []
    try:
        q = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10)
        if q.returncode == 0:
            plain = ""
            try:
                plain = run(["nvidia-smi"], capture_output=True, text=True, timeout=10).stdout
            except Exception:
                pass
            cards = parse_nvidia_smi(q.stdout, plain)
    except Exception:
        cards = []
    best = pick_gpu(cards)
    if best and best.memory_mb:
        return best
    if _platform().startswith("linux"):
        try:
            r = run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"], capture_output=True, text=True, timeout=15)
            amd = pick_gpu(parse_rocm_smi(r.stdout)) if r.returncode == 0 else None
        except Exception:
            amd = None
        if amd:
            return amd
        return best or pick_gpu(_lspci_gpus(run))
    reg = pick_gpu(_registry_gpus())
    return reg or best


# --------------------------------------------------------------------------------------------
# Which engine build, and does a model fit
# --------------------------------------------------------------------------------------------
def engine_url(build: str, name: str) -> str:
    return _RELEASE_URL.format(build=build, name=name)


def engine_total_size(kind: str) -> int:
    return sum(size for _, size, _ in engine_files_table()[kind])


def engine_options(gpu: Optional[GpuInfo]) -> list[dict]:
    """The builds worth offering for this device, recommended one first. NVIDIA owners get the CUDA
    build their driver can run; everyone gets the works-on-anything one; the processor-only one is
    last (and the recommendation when there's no graphics card at all)."""
    table = engine_files_table()
    kinds = []
    recommended = "cpu"
    if gpu and gpu.vendor == "nvidia":
        cuda_kind = None
        if gpu.cuda:
            for k in ("cuda-13.4", "cuda-12.8", "cuda-12.4"):
                if k in table and gpu.cuda >= _CUDA_NEEDS[k]:
                    cuda_kind = k
                    break
        if cuda_kind:
            kinds.append(cuda_kind)
            recommended = cuda_kind
        else:
            recommended = "vulkan"
    elif gpu:
        recommended = "vulkan"
    if "vulkan" in table:
        kinds.append("vulkan")
    elif recommended == "vulkan":
        recommended = "cpu"
    # The faster builds are offered, never recommended, and only for the card that is really there. On Linux ROCm also needs AMD's own tools on the device (`rocm-smi` found it).
    if gpu and gpu.vendor == "amd" and "rocm" in table and (gpu.source == "rocm-smi" or not _platform().startswith("linux")):
        kinds.append("rocm")
    if gpu and gpu.vendor == "intel" and "sycl" in table:
        kinds.append("sycl")
    kinds.append("cpu")
    out = []
    for k in kinds:
        label, blurb = ENGINE_LABELS[k]
        out.append({"kind": k, "label": label, "blurb": blurb, "sizeBytes": engine_total_size(k),
                    "recommended": k == recommended, "advanced": k in FAST_KINDS})
    out.sort(key=lambda o: not o["recommended"])
    return out


def model_total_bytes(files: list[dict]) -> int:
    return sum(f["size"] for f in files)


def fit_check(total_bytes: int, gpu: Optional[GpuInfo], reserve_skyrim: bool = True,
              skyrim_peak_mb: Optional[int] = None) -> Optional[str]:
    """"fits" / "tight" / "too_big" for a model of this total size (weights + vision file) on this
    card, or None when the card's memory couldn't be read (skip the check rather than guess).

    With Skyrim's real busiest point known: free while playing = card - that - 2 GB for everything else;
    fits if the model + working room leaves 1 GB to spare, tight if it only just fits, otherwise too big
    (while Skyrim runs). Unknown yet: a guess of a quarter of the card (3 to 10 GB)."""
    if not gpu or not gpu.memory_mb:
        return None
    card = gpu.memory_mb * 1024 * 1024
    need = total_bytes + WORKING_ROOM_BYTES
    if reserve_skyrim and skyrim_peak_mb:
        free = card - skyrim_peak_mb * 1024 * 1024 - SKYRIM_EVERYTHING_ELSE
        if need <= free - SKYRIM_FIT_MARGIN:
            return "fits"
        if need <= free:
            return "tight"
        return "too_big"
    # On a model-server device there is no Skyrim, so the whole card is the model's.
    skyrim = min(max(int(card * SKYRIM_SHARE_FRACTION), SKYRIM_SHARE_MIN), SKYRIM_SHARE_MAX) if reserve_skyrim else 0
    if need <= card - skyrim:
        return "fits"
    if need <= card:
        return "tight"
    return "too_big"


def fit_why(total_bytes: int, gpu: Optional[GpuInfo], skyrim_peak_mb: Optional[int]) -> str:
    """The numbers behind a verdict, one short line for the dot's hover: "Skyrim peak 27.5 GB, free about 2.4 GB, needs 5.9 GB". Empty when no figure was used."""
    if not gpu or not gpu.memory_mb or not skyrim_peak_mb:
        return ""
    free = max(0, gpu.memory_mb * 1024 * 1024 - skyrim_peak_mb * 1024 * 1024 - SKYRIM_EVERYTHING_ELSE)
    return f"Skyrim peak {fmt_gb(skyrim_peak_mb * 1024 * 1024)}, free about {fmt_gb(free)}, needs {fmt_gb(total_bytes + WORKING_ROOM_BYTES)}"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "model"


def fmt_gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


# --------------------------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------------------------
def unpack_archive(archive: Path, dest: Path) -> None:
    """Unpack a .zip (Windows builds) or .tar.gz (Linux builds) into `dest`, refusing any entry that
    would land outside it."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if not (root / name).resolve().is_relative_to(root):
                    raise LocalModelError(502, f"{archive.name} has an unsafe file in it, so it wasn't unpacked.")
            z.extractall(dest)
        return
    with tarfile.open(archive, "r:*") as t:
        for m in t.getmembers():
            if not (root / m.name).resolve().is_relative_to(root) or m.issym() and not (root / m.name).parent.joinpath(m.linkname).resolve().is_relative_to(root):
                raise LocalModelError(502, f"{archive.name} has an unsafe file in it, so it wasn't unpacked.")
        try:
            t.extractall(dest, filter="data")      # Python 3.12+: the safe-by-default extraction rules
        except TypeError:
            t.extractall(dest)                     # older Python: the manual checks above are what protect us


@dataclass
class Job:
    key: str                              # "engine" or "model:<id>"
    label: str
    files: list[dict]                     # {url, dest, size, sha256}
    total: int = 0
    done: int = 0
    state: str = "running"                # running | done | error | cancelled
    error: str = ""
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    after: Optional[Callable[[], None]] = field(default=None, repr=False)
    meter: Any = field(default_factory=download_meter.Meter, repr=False)      # the speed really measured: the time left comes from it
    pausing: bool = field(default=False, repr=False)                          # set by Pause so the stop is told apart from Cancel
    resume_args: Any = field(default=None, repr=False)                        # what Resume needs to start this same download again (the engine's kind and files)

    def to_json(self) -> dict:
        return {"state": self.state, "done": self.done, "total": self.total, "error": self.error,
                "percent": int(self.done * 100 / self.total) if self.total else 0,
                "left": self.meter.text(self.done, self.total) if self.state == "running" else ""}


def partial_bytes(files: list) -> int:
    """How much of these files is already on disk: complete files in full, a half-fetched one by its `.part`. What a paused download shows as "so far"."""
    n = 0
    for f in files:
        dest = Path(f["dest"])
        part = dest.with_name(dest.name + ".part")
        try:
            if dest.exists() and dest.stat().st_size == f["size"]:
                n += f["size"]
            elif part.exists():
                n += min(part.stat().st_size, f["size"])
        except OSError:
            pass
    return n


def _hash_existing(path: Path) -> "hashlib._Hash":
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h


VERIFIED_SUFFIX = ".verified"
NOT_HTTPS = "The site sent the download to an address that isn't secure, so it was stopped. Try again later."
FAILED_SAFETY = "{name} didn't pass its safety check, so it was removed. Press Download to get it again."


def _verified_note(path: Path) -> Path:
    return path.with_name(path.name + VERIFIED_SUFFIX)


def remember_verified(path: Path, sha256: str) -> None:
    """Note that this file, at this size and modification time, has the checksum the download promised (so it isn't hashed again on every start)."""
    try:
        st = path.stat()
        _verified_note(path).write_text(json.dumps({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256}), encoding="utf-8")
    except OSError:
        pass


def verify_file(path: Path, size: int, sha256: str) -> bool:
    """Is the file at `path` really the one expected: its size, and its SHA-256 (blocking: call it in a thread)? A note from an earlier check (same size, same modification
    time, same checksum) counts, so a big model is hashed once and not on every start; anything else is hashed now. A file that is the right size but the wrong bytes is not
    trusted (security review S10)."""
    try:
        st = path.stat()
        if st.st_size != size:
            return False
        try:
            note = json.loads(_verified_note(path).read_text(encoding="utf-8"))
            if note.get("size") == st.st_size and note.get("mtime_ns") == st.st_mtime_ns and note.get("sha256") == sha256:
                return True
        except (OSError, ValueError, AttributeError):
            pass
        if _hash_existing(path).hexdigest() != sha256:
            return False
    except OSError:
        return False
    remember_verified(path, sha256)
    return True


def file_state(path: Path, size: int, sha256: str) -> str:
    """What a pinned file looks like on disk, without hashing it again: `ok` (the pinned size, and no sign it is another version), `update` (a complete file that was checked
    when it was downloaded, and is another pinned version: another checksum, or another size), `none` (not there, or a wrong-sized file nothing vouches for: a partial or
    damaged one)."""
    try:
        st = path.stat()
    except OSError:
        return "none"
    note = None
    try:
        note = json.loads(_verified_note(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    vouched = isinstance(note, dict) and note.get("size") == st.st_size and note.get("mtime_ns") == st.st_mtime_ns
    if st.st_size == size:
        return "update" if vouched and note.get("sha256") != sha256 else "ok"
    return "update" if vouched else "none"


def _vouched_ok(path: Path, size: int, sha256: str) -> bool:
    """The file is there at the pinned size AND a checksum noted when it was downloaded vouches for exactly these bytes (same size, same modified time, same checksum). No hashing."""
    try:
        st = path.stat()
        note = json.loads(_verified_note(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(note, dict) and st.st_size == size and note.get("size") == size and note.get("mtime_ns") == st.st_mtime_ns and note.get("sha256") == sha256


def model_disk_state(folder: Optional[Path], spec: dict) -> str:
    """`ok`, `update` or `none` for a catalog model's files in the models folder: `ok` only when every file is there as pinned."""
    if not folder:
        return "none"
    d = folder / "models" / spec["id"]
    states = [file_state(d / f["file"], f["size"], f["sha256"]) for f in spec["files"]]
    if "none" in states:
        return "none"
    return "update" if "update" in states else "ok"


async def download_file(session: aiohttp.ClientSession, url: str, dest: Path, size: int, sha256: str,
                        on_bytes: Callable[[int], None]) -> None:
    """Download `url` to `dest`, resuming a half-finished `.part` file, and only rename it into
    place once its size and checksum are right -- so a file sitting at its final name is always
    complete. `on_bytes(n)` is called with each chunk's size (real progress). A file already at its
    final name counts only when its checksum is right too (a wrong one is removed and fetched again);
    a redirect is followed only to https."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == size:
        if await asyncio.to_thread(verify_file, dest, size, sha256):
            on_bytes(size)
            return
        dest.unlink(missing_ok=True)
        _verified_note(dest).unlink(missing_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    h = await asyncio.to_thread(_hash_existing, part) if have else hashlib.sha256()
    headers = {"Range": f"bytes={have}-"} if have else {}
    if have:
        on_bytes(have)
    if have < size:
        async with https_only.get(session, url, headers) as resp:
            if resp.status == 200 and have:
                # The site ignored our resume request and is sending the whole file again.
                on_bytes(-have)
                have = 0
                h = hashlib.sha256()
                part.unlink()
            elif resp.status not in (200, 206):
                raise DownloadStatusError(resp.status, str(resp.url), download_retry.retry_after_seconds(resp.headers.get("Retry-After")))
            with open(part, "ab") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
                    h.update(chunk)
                    on_bytes(len(chunk))
    if part.stat().st_size != size:
        raise LocalModelError(502, f"{dest.name} downloaded at the wrong size. Press Download to try again.")
    if h.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise LocalModelError(502, f"{dest.name} didn't pass its safety check (the download was damaged). Press Download to start it again.")
    os.replace(part, dest)
    remember_verified(dest, sha256)


# --------------------------------------------------------------------------------------------
# Running llama-server
# --------------------------------------------------------------------------------------------
@dataclass
class Installed:
    id: str
    name: str
    vision: bool
    path: Path
    mmproj: Optional[Path]
    size: int
    custom: bool = False

    @property
    def model_name(self) -> str:
        return f"{LOCAL_PREFIX}{self.id}"


@dataclass
class Server:
    model_id: str
    state: str = "stopped"               # stopped | starting | running | failed
    message: str = ""
    port: Optional[int] = None
    proc: Optional[asyncio.subprocess.Process] = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stopping: bool = False
    log_path: Optional[Path] = None
    task: Optional[asyncio.Task] = None
    engine_wait: bool = False            # starting, but first getting the engine (loading a model fetches it when it's missing)
    used_extras: bool = False            # the last launch included the player's own extra flags (so a failure may have come from them)
    cmd: list = field(default_factory=list)
    setup_fp: str = ""                   # fingerprint of the settings this engine STARTED with (speed numbers are only ever compared under one of these)
    setup_text: str = ""                 # the same setup in plain words ("Fastest replies · 32,768 memory · 1 at a time")
    started: Optional[dict] = None       # what the last start really did (command, engine build, layers on the card when known): reported to the screens
    served: int = 0                      # replies answered since the engine started (the first one is slow: loading, warming up: it is marked cold in the history)


_OOM_HINTS = ("out of memory", "cudamalloc failed", "failed to allocate", "unable to allocate",
              "vk::", "erroroutofdevicememory", "cudaerrormemoryallocation", "not enough memory")


# Linux: the engine needs a few system libraries. A minimal server may not have them, and the only
# sign is "error while loading shared libraries: libgomp.so.1" in the engine's log.
_LIB_FIXES = {
    "libgomp.so.1": "sudo apt install libgomp1",
    "libvulkan.so.1": "sudo apt install libvulkan1",
    "libnuma.so.1": "sudo apt install libnuma1",
    "libcuda.so.1": "install NVIDIA's graphics driver (it provides libcuda)",
}


# What the engine itself says when a SETTING is wrong (a bad value, a combination it refuses, a crash it asserts on): its own words are shown, not a guess.
_SETTINGS_ERROR = re.compile(
    r"error while handling argument|invalid argument|unknown value|invalid value|unsupported cache type|quantized v cache requires|n_ctx_seq == 0|ggml_assert|"
    r"get_sub_batch|does not support split buffers|expected value for argument|invalid device|too large to process", re.I)
_CRASH_CODES = (3221226505, -1073740791, 134, 139, -6, -11)       # the engine's own assert (Windows 0xC0000409), SIGABRT, SIGSEGV


def engine_words(log_tail: str, limit: int = 6) -> list:
    """The engine's own error lines from the end of its log (without the time stamp it starts each line with): the ones about a setting first, then any other
    error line, at most `limit`."""
    out = []
    for line in (log_tail or "").splitlines():
        t = re.sub(r"^\s*\d+\.\d+\.\d+\.\d+\s+[A-Z]\s+", "", line).strip()
        if t and (_SETTINGS_ERROR.search(t) or re.match(r"^error\b", t, re.I) or t.startswith("E ")):
            if t[:300] not in out:
                out.append(t[:300])
    specific = [t for t in out if _SETTINGS_ERROR.search(t)]
    return (specific + [t for t in out if t not in specific])[:limit]


def explain_failure(log_tail: str, exit_code: Optional[int]) -> str:
    """One plain sentence for why llama-server stopped, from the end of its own log."""
    lib = re.search(r"error while loading shared libraries: ([^\s:]+)", log_tail or "")
    if lib:
        name = lib.group(1)
        fix = _LIB_FIXES.get(name)
        how = f" On Ubuntu or Debian, run: {fix}." if fix and fix.startswith("sudo") else (f" Fix: {fix}." if fix else " Install the package that provides it.")
        return f"This device is missing a required system file ({name}).{how}"
    low = (log_tail or "").lower()
    if any(h in low for h in _OOM_HINTS):
        return "Ran out of graphics memory. Close other programs, or pick a smaller model."
    said = [w for w in engine_words(log_tail) if _SETTINGS_ERROR.search(w)]
    if said:
        return f"The engine refused these settings. It said: \u201c{' '.join(said[:2])[:240]}\u201d"
    if "failed to load model" in low or "error loading model" in low:
        return "The model file couldn't be loaded. It may be damaged or not a model file. Try downloading it again."
    if "unknown model architecture" in low:
        return "The engine software doesn't know this kind of model yet."
    if exit_code in _CRASH_CODES:
        return f"The engine crashed (code {exit_code}). That usually means a setting doesn't suit this model."
    code = f" (code {exit_code})" if exit_code not in (None, 0) else ""
    return f"The model stopped unexpectedly{code}. Check the log in the models folder."


def kill_tree_sync(pid: int) -> None:
    """Kill a process and everything it started, without waiting on asyncio."""
    try:
        if os.name == "nt":
            hidden_process.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _pid_is_llama_server(pid: int) -> bool:
    """Is this PID really a llama-server right now? (A recorded PID can be reused by an unrelated
    program after a reboot, and we must never kill one of those.)"""
    try:
        if os.name == "nt":
            out = hidden_process.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout.lower()
            return "llama-server" in out
        out = hidden_process.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=10).stdout.lower()
        return "llama-server" in out
    except Exception:
        return False


def _free_port(start: int = SERVER_PORT_START, end: int = SERVER_PORT_END) -> int:
    """A port nobody is listening on, starting at a random place in the range: a Courier and a MultiProxy on one device (or two test
    runs at once) that both look "now" would otherwise all pick the first free one, and one engine would then fail to bind."""
    span = end - start
    offset = random.randrange(span) if span > 0 else 0
    for port in [start + (offset + i) % span for i in range(span)] if span > 0 else []:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalModels:
    """Everything the dashboard and the request router need, behind one object. `load_cfg` /
    `save_cfg` read and write config.json (the `local_models` key); `pids_file` records running
    llama-server PIDs so a crash can't leave one eating graphics memory forever.

    The settings a model starts with are the DEVICE's (`engine_settings`, see local_engine_settings.py): `device_id` is `local` on this device. `HAS_GAME` says
    whether Skyrim runs on this device (a Courier device has none, which changes the default for "memory to keep free")."""

    HAS_GAME = True

    def __init__(self, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], log,
                 pids_file: str, command_builder: Optional[Callable[..., list]] = None,
                 gpu_detector: Callable[[], Optional[GpuInfo]] = detect_gpu,
                 session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
                 gpu_choice_fn: Callable[[str], Optional[dict]] = lambda model_id: None,
                 cards_fn: Callable[[], list] = gpu_choice.cached_cards,
                 devices_fn: Callable[[Any], list] = gpu_choice.list_devices):
        self._load_cfg = load_cfg
        self._save_cfg = save_cfg
        self.log = log
        self.pids_file = pids_file
        self._command_builder = command_builder or self._default_command
        self._gpu_detector = gpu_detector
        self._session_factory = session_factory or https_only.session
        self.servers: dict[str, Server] = {}
        self.jobs: dict[str, Job] = {}
        self._gpu: Optional[GpuInfo] = None
        self._gpu_read = False
        self._gpu_choice_fn = gpu_choice_fn      # model id -> the remembered card choice (None = Automatic)
        self._cards_fn = cards_fn
        self._devices_fn = devices_fn
        self.gpu_notes: dict[str, str] = {}      # model id -> why Automatic was used at its last start ("" = nothing to say)
        self.gpu_cards: dict[str, Optional[str]] = {}   # model id -> name of the card its last start was kept on (None = Automatic)
        self.gpu_note = ""                       # the note of the most recent start
        self.engine_notes: dict[str, str] = {}   # model id -> why the works-on-anything engine took over from a faster one ("" = nothing to say)
        self._start_lock = asyncio.Lock()     # models load one at a time, never all at once
        self.device_id = local_engine_settings.DEVICE_LOCAL
        self.engine_settings = local_engine_settings.EngineSettingsStore(load_cfg, save_cfg)
        self.prompt_peaks = local_engine_settings.PromptPeaks(load_cfg, save_cfg)
        self._skip_extras: set = set()        # model ids whose next start leaves the player's extra flags out ("Start without the extra flags")
        # A check every start must pass first: an async function giving None to go ahead or a plain sentence to refuse with. A Courier device sets it so that no
        # model starts before the settings its main device sent have arrived (local_engine_sync.DeviceReceiver.gate).
        self.start_gate: Optional[Callable[[], Any]] = None
        self.guard_stops: dict[str, dict] = {}   # model id -> {"at", "name"} for a model the graphics memory guard stopped (gpu_guard.py); ends on Start or when the game closes
        self._build_of: dict = {}             # engine file -> its build number (read once from `--version`)

    # ---- config ------------------------------------------------------------------------
    def _cfg(self) -> dict:
        return dict(self._load_cfg().get("local_models") or {})

    def _update(self, **changes) -> None:
        cfg = self._load_cfg()
        lm = dict(cfg.get("local_models") or {})
        for k, v in changes.items():
            if v is None:
                lm.pop(k, None)
            else:
                lm[k] = v
        cfg["local_models"] = lm
        self._save_cfg(cfg)

    @property
    def folder(self) -> Optional[Path]:
        f = self._cfg().get("folder")
        return Path(f) if f else None

    def set_folder(self, path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise LocalModelError(400, "Pick a full folder path, like D:\\AI Models.")
        if not safe_paths.is_local_fixed(p):
            raise LocalModelError(400, safe_paths.NOT_LOCAL_SENTENCE)
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".write-test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:
            raise LocalModelError(400, f"Couldn't save into that folder: {e.strerror or e}")
        self._update(folder=str(p))
        return p

    # ---- hardware ----------------------------------------------------------------------
    async def gpu(self, refresh: bool = False) -> Optional[GpuInfo]:
        if refresh or not self._gpu_read:
            self._gpu = await asyncio.to_thread(self._gpu_detector)
            self._gpu_read = True
        return self._gpu

    def _choice_for(self, model_id: str) -> Optional[dict]:
        try:
            c = self._gpu_choice_fn(model_id)
        except Exception:
            return None
        return c if isinstance(c, dict) and c.get("id") else None

    def _chosen_card(self, model_id: str):
        """The card remembered for this model if this device still has it, else None (Automatic). No choice stored: no card is read at all."""
        choice = self._choice_for(model_id)
        if not choice:
            return None
        try:
            return next((c for c in self._cards_fn() if c.id == choice["id"]), None)
        except Exception:
            return None

    async def gpu_for(self, model_id: str, refresh: bool = False) -> Optional[GpuInfo]:
        """The card a fit check for this model should talk about: its chosen card's memory, or the usual card (Automatic, or the chosen card is gone)."""
        if self._choice_for(model_id):
            card = await asyncio.to_thread(self._chosen_card, model_id)
            if card is not None:
                return GpuInfo(card.name, card.memory_mb, card.vendor)
        return await self.gpu(refresh)

    # ---- what's on disk ----------------------------------------------------------------
    def engine_dir(self) -> Optional[Path]:
        folder, eng = self.folder, self._cfg().get("engine") or {}
        if not folder or not eng.get("kind") or not eng.get("build"):
            return None
        return folder / "engine" / f"{eng['build']}-{eng['kind']}"

    def engine_builds(self) -> list:
        """Every runner build on disk under `<models folder>/engine`: [{dir, build, kind, path, bytes, current}]. The one the settings name is `current`; an older one left after
        an update is listed too."""
        folder = self.folder
        root = folder / "engine" if folder else None
        out = []
        if not root or not root.is_dir():
            return out
        cur = self.engine_dir()
        for d in sorted(root.iterdir()):
            if not d.is_dir() or "-" not in d.name:
                continue
            build, kind = d.name.split("-", 1)
            size = 0
            for p in d.rglob("*"):
                try:
                    if p.is_file() and not p.is_symlink():
                        size += p.stat().st_size
                except OSError:
                    pass
            out.append({"dir": d.name, "build": build, "kind": kind, "path": str(d), "bytes": size, "current": cur is not None and d.resolve() == cur.resolve()})
        return out

    def delete_engine_build(self, dir_name: str) -> int:
        """Delete one OLD runner build folder (never the one in use, never while a model is running). Returns the bytes freed."""
        match = next((b for b in self.engine_builds() if b["dir"] == dir_name), None)
        if match is None:
            raise LocalModelError(404, "That engine isn't installed.")
        if match["current"]:
            raise LocalModelError(409, "MultiProxy is using this engine, so it can't be deleted.")
        if any(self.state_of(m.id)[0] in ("running", "starting") for m in self.installed()):
            raise LocalModelError(409, "Stop your running models first.")
        shutil.rmtree(match["path"], ignore_errors=True)
        return match["bytes"]

    def engine_build_number(self) -> int:
        """The installed engine's build as a number ("b11303" is 11303); 0 when there is none or it can't be read."""
        m = re.match(r"^b(\d+)$", str((self._cfg().get("engine") or {}).get("build") or ""))
        return int(m.group(1)) if m else 0

    def engine_exe_current(self) -> Optional[Path]:
        """The installed engine, but only when it is new enough for llama.cpp's own decision API; an older one counts as "none" for the decision model."""
        return self.engine_exe() if self.engine_build_number() >= NATIVE_DECISION_MIN_BUILD else None

    def engine_exe(self) -> Optional[Path]:
        d = self.engine_dir()
        if not d or not d.is_dir():
            return None
        name = "llama-server.exe" if os.name == "nt" else "llama-server"
        direct = d / name
        if direct.exists():
            return direct
        found = next(d.rglob(name), None)
        return found

    def _model_dir(self, model_id: str) -> Optional[Path]:
        folder = self.folder
        return folder / "models" / model_id if folder else None

    def installed(self) -> list[Installed]:
        out = []
        removed = self._removed()
        for spec in CATALOG:
            d = self._model_dir(spec["id"])
            if not d or spec["id"] in removed:         # removed but its files kept: not in the list until it is added back
                continue
            main = next(f for f in spec["files"] if f["role"] == "model")
            mm = next((f for f in spec["files"] if f["role"] == "mmproj"), None)
            p = d / main["file"]
            mp = d / mm["file"] if mm else None
            if p.exists() and p.stat().st_size == main["size"] and (not mp or (mp.exists() and mp.stat().st_size == mm["size"])):
                out.append(Installed(spec["id"], spec["name"], spec["vision"], p, mp, model_total_bytes(spec["files"])))
        for c in self._cfg().get("custom") or []:
            p = Path(c.get("path", ""))
            mp = Path(c["mmproj"]) if c.get("mmproj") else None
            if p.exists():
                size = p.stat().st_size + (mp.stat().st_size if mp and mp.exists() else 0)
                out.append(Installed(c["id"], c.get("name") or p.stem, bool(mp), p, mp if mp and mp.exists() else None, size, custom=True))
        return out

    def _removed(self) -> list:
        """The catalog models the player removed but whose files are still in the models folder."""
        return [str(x) for x in (self._cfg().get("removed") or []) if str(x) in CATALOG_BY_ID]

    def kept(self) -> list[dict]:
        """Removed catalog models whose files are all on disk, complete, and vouched for by the checksum noted when they were downloaded. A half file, a file that changed
        since, or one copied in by hand (never checked) is not "kept": it is not offered as an instant Add."""
        folder = self.folder
        out = []
        if not folder:
            return out
        for mid in self._removed():
            spec = CATALOG_BY_ID[mid]
            d = folder / "models" / mid
            if all(_vouched_ok(d / f["file"], f["size"], f["sha256"]) for f in spec["files"]):
                out.append({"id": mid, "name": spec["name"], "vision": spec["vision"], "sizeBytes": model_total_bytes(spec["files"]), "sizeText": fmt_gb(model_total_bytes(spec["files"]))})
        return out

    async def add_kept(self, model_id: str) -> None:
        """Put a kept model back in the list without downloading anything. The files are checked in full first (size and checksum, the same check a download uses)."""
        spec = CATALOG_BY_ID.get(model_id)
        d = self._model_dir(model_id)
        if not spec or not d or model_id not in self._removed():
            raise LocalModelError(404, "That model isn't saved on this PC.")
        for f in spec["files"]:
            if not await asyncio.to_thread(verify_file, d / f["file"], f["size"], f["sha256"]):
                raise LocalModelError(400, "The saved files are incomplete. Download the model again.")
        self._update(removed=[x for x in self._removed() if x != model_id] or None)

    async def delete_kept(self, model_id: str) -> None:
        """Delete the files of a kept (removed) catalog model. Only ever called after the player said so."""
        d = self._model_dir(model_id)
        if model_id not in self._removed() or not d:
            raise LocalModelError(404, "That model isn't saved on this PC.")
        await asyncio.to_thread(shutil.rmtree, d, True)
        self._update(removed=[x for x in self._removed() if x != model_id] or None)

    def find(self, model: str) -> Optional[Installed]:
        """Match a `local/<id>` request name (case-insensitive) to an installed model. A name that
        matches a known model that isn't downloaded yet returns None too -- callers tell those
        apart with `known_model_id`."""
        mid = model[len(LOCAL_PREFIX):] if model.lower().startswith(LOCAL_PREFIX) else model
        mid = mid.lower()
        return next((m for m in self.installed() if m.id.lower() == mid), None)

    def any_installed(self) -> bool:
        return bool(self.installed())

    # ---- add / remove ------------------------------------------------------------------
    def add_custom(self, path: str, mmproj: str = "") -> Installed:
        p = Path(path.strip().strip('"'))
        if not p.is_file() or p.suffix.lower() != ".gguf":
            raise LocalModelError(400, "Pick a supported model file.")
        mp = None
        if mmproj.strip():
            mp = Path(mmproj.strip().strip('"'))
            if not mp.is_file() or mp.suffix.lower() != ".gguf":
                raise LocalModelError(400, "The vision file doesn't look right.")
        base = slugify(p.stem)
        ids = {m["id"] for m in CATALOG} | {c["id"] for c in self._cfg().get("custom") or []}
        mid, n = base, 2
        while mid in ids:
            mid, n = f"{base}-{n}", n + 1
        custom = list(self._cfg().get("custom") or [])
        custom.append({"id": mid, "name": p.stem, "path": str(p), "mmproj": str(mp) if mp else ""})
        self._update(custom=custom)
        return next(m for m in self.installed() if m.id == mid)

    async def uninstall_engine(self, protected=()) -> int:
        """Remove the downloaded llama.cpp PROGRAM (the `engine` folder inside the models folder) and nothing else: no model, not the models folder, not an engine the
        player pointed at. Stops every running model first. `program_uninstall` does the deleting and refuses anything outside that folder. Returns the bytes freed."""
        folder = self.folder
        if not folder:
            return 0
        await self.stop_all()
        freed = await asyncio.to_thread(program_uninstall.remove_program, folder, folder / "engine", tuple(protected) + (folder / "models",))
        self._update(engine=None)
        return freed

    async def remove(self, model_id: str, delete_files: bool = False) -> None:
        """Take a model out of MultiProxy. A downloaded (catalog) model keeps its files unless the player said to delete them; a player's own file is never touched."""
        await self.stop(model_id)
        self.engine_settings.forget_model(self.device_id, model_id)       # its own memory / requests-at-a-time overrides go with it
        custom = [c for c in (self._cfg().get("custom") or []) if c["id"] != model_id]
        if len(custom) != len(self._cfg().get("custom") or []):
            self._update(custom=custom)        # the player's own file stays where it is
            return
        d = self._model_dir(model_id)
        if d and model_id in CATALOG_BY_ID and d.is_dir():
            if delete_files:
                await asyncio.to_thread(shutil.rmtree, d, True)
                self._update(removed=[x for x in self._removed() if x != model_id] or None)
            elif model_id not in self._removed():
                self._update(removed=self._removed() + [model_id])

    # ---- downloads ---------------------------------------------------------------------
    def _start_job(self, job: Job) -> Job:
        live = self.jobs.get(job.key)
        if live and live.state == "running":
            return live
        self.jobs[job.key] = job
        self._set_paused(job.key, None)             # it is running again (or starting): it is no longer a paused one
        job.task = asyncio.create_task(self._run_job(job))
        return job

    # ---- Pause and Resume (2026-10-26) -------------------------------------------------
    # Pause stops fetching now and keeps the partial file; Resume is the ordinary download call, which carries on from the byte the file reached. A paused download is
    # remembered in the saved settings (not just in memory), so it still reads "Paused" after MultiProxy is closed and opened again, as long as its partial file is on disk.
    def _paused_marks(self) -> dict:
        return dict(self._cfg().get("paused") or {})

    def _set_paused(self, key: str, value) -> None:
        marks = self._paused_marks()
        if value is None:
            if key not in marks:
                return
            marks.pop(key)
        else:
            marks[key] = value
        self._update(paused=marks or None)

    async def pause(self, key: str) -> None:
        job = self.jobs.get(key)
        if not job or job.state != "running" or not job.task:
            return
        job.pausing = True
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        self.jobs.pop(key, None)
        if partial_bytes(job.files) > 0:
            self._set_paused(key, job.resume_args or True)

    def _spec_parts(self, spec: dict) -> list:
        d = self._model_dir(spec["id"])
        return [{"dest": str(d / f["file"]), "size": f["size"]} for f in spec["files"]] if d else []

    def paused_engine_json(self) -> Optional[dict]:
        args = self._paused_marks().get("engine")
        parts = args.get("parts") if isinstance(args, dict) else None
        return self.paused_json("engine", parts, sum(p["size"] for p in parts)) if parts else None

    def paused_json(self, key: str, files: list, total: int) -> Optional[dict]:
        """The bar of a paused download, read from the disk: None when it is not paused or its partial file is gone."""
        if key not in self._paused_marks() or (self.jobs.get(key) and self.jobs[key].state == "running"):
            return None
        done = partial_bytes(files)
        if done <= 0:
            self._set_paused(key, None)
            return None
        return {"state": "paused", "done": done, "total": total, "error": "", "percent": int(done * 100 / total) if total else 0, "left": ""}

    def resume(self, key: str) -> Job:
        """Carry on a paused download from the byte it reached."""
        if key == "engine":
            args = self._paused_marks().get("engine")
            if not isinstance(args, dict) or not args.get("kind"):
                raise LocalModelError(404, "There are no paused downloads here.")
            files = [tuple(x) for x in args["files"]] if args.get("files") else None
            return self.download_engine(args["kind"], args.get("build") or "", files if args.get("custom") else None)
        if key.startswith("model:"):
            return self.download_model(key[len("model:"):])
        raise LocalModelError(404, "There are no paused downloads here.")

    async def _run_job(self, job: Job) -> None:
        def on_bytes(n: int) -> None:
            job.meter.note(n)
            job.done = max(0, job.done + n)
        try:
            async with self._session_factory() as session:
                for f in job.files:
                    await download_retry.run(lambda f=f: download_file(session, f["url"], Path(f["dest"]), f["size"], f["sha256"], on_bytes), url=f["url"],
                                             what="the engine software" if job.key == "engine" else "the model's files", device="this device",
                                             file=Path(f["dest"]).name, log=self.log.warning)
            if job.after:
                await asyncio.to_thread(job.after)
            job.state = "done"
        except asyncio.CancelledError:
            job.state = "paused" if job.pausing else "cancelled"
            raise
        except LocalModelError as e:
            job.state, job.error = "error", e.message
        except download_retry.DownloadFailed as e:
            # a model download that lost its connection keeps its own words (it picks up where it left off); every other case says what really happened
            lost = download_retry.kind_of(e.cause) in ("connection", "timeout") and job.key != "engine"
            job.state, job.error = "error", NOT_HTTPS if isinstance(e.cause, https_only.RedirectRefused) else DOWNLOAD_INTERRUPTED if lost else e.message
        except https_only.RedirectRefused:
            job.state, job.error = "error", NOT_HTTPS
        except (aiohttp.ClientError, asyncio.TimeoutError):
            job.state, job.error = "error", DOWNLOAD_INTERRUPTED
        except OSError as e:
            job.state, job.error = "error", f"Couldn't write to the models folder: {e.strerror or e}"
        except Exception as e:  # never let a download task die silently
            self.log.exception("Local models download failed")
            job.state, job.error = "error", f"Something went wrong: {e}"

    def _check_space(self, need: int) -> None:
        folder = self.folder
        free = shutil.disk_usage(folder).free
        if free < need:
            raise LocalModelError(400, f"This needs about {fmt_gb(need)} free, but the drive you chose only has {fmt_gb(free)} left.")

    def _need_folder(self) -> Path:
        folder = self.folder
        if not folder:
            raise LocalModelError(400, "Pick a folder to save models in first.")
        return folder

    def download_engine(self, kind: str, build: str = "", files: Optional[list] = None) -> Job:
        """Download and switch to the `kind` engine. By default the pinned, tested build with its own listed checksums. `build` + `files` ([(name, size, sha256)] from that
        release's own list, see `update_engine`) is how a newer one comes in; it only ever happens when the player pressed Update (or picked a faster version)."""
        table = engine_files_table()
        if kind not in table and not files:
            raise LocalModelError(400, "We don't recognize this software version.")
        build = build or LLAMA_CPP_BUILD
        files_arg = files
        listed = files if files else table[kind]
        folder = self._need_folder()
        if not platform_supported():
            raise LocalModelError(400, "This automatic download only works on Windows and Linux.")
        live = self.jobs.get("engine")
        if live and live.state == "running":
            return live
        self._check_space(sum(sz for _, sz, _ in listed) * 2)       # zips, plus what they unpack to
        dl = folder / "engine" / "_downloads"
        target = folder / "engine" / f"{build}-{kind}"
        files = [{"url": engine_url(build, n), "dest": str(dl / n), "size": sz, "sha256": h}
                 for n, sz, h in listed]

        def after() -> None:
            tmp = target.with_name(target.name + ".unpacking")
            shutil.rmtree(tmp, ignore_errors=True)
            for f in files:
                unpack_archive(Path(f["dest"]), tmp)
            shutil.rmtree(target, ignore_errors=True)
            os.replace(tmp, target)
            if os.name != "nt":
                for exe in target.rglob("llama-server*"):
                    if exe.is_file() and not exe.suffix:
                        exe.chmod(exe.stat().st_mode | 0o111)
            shutil.rmtree(dl, ignore_errors=True)
            self._update(engine={"kind": kind, "build": build})
            self._prune_old_engines(target)

        job = Job("engine", "Engine", files, total=sum(f["size"] for f in files), after=after)
        job.resume_args = {"kind": kind, "build": build, "custom": bool(files_arg), "files": [list(x) for x in listed] if files_arg else None,
                           "parts": [{"dest": f["dest"], "size": f["size"]} for f in files]}
        return self._start_job(job)

    # ---- the runner copied from another device (2026-10-23): the installed runner folder, zipped on one device and unpacked on another ------------------------------
    def pack_engine(self, dest_zip: Path) -> tuple:
        """Zip the installed runner folder (names relative to it) into `dest_zip` and return (build, kind), e.g. ("b11461", "cuda-13.4"). Windows only: a zip keeps no
        "can run" mark, so a Linux engine is downloaded instead. Raises LocalModelError when there is nothing usable to send."""
        if os.name != "nt":
            raise LocalModelError(400, "The engine can only be copied from a Windows device.")
        eng = self._cfg().get("engine") or {}
        d = self.engine_dir()
        if not d or not self.engine_exe_current() or not eng.get("kind"):
            raise LocalModelError(400, "This device has no engine that can be copied.")
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest_zip.with_name(dest_zip.name + ".packing")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for f in sorted(d.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    z.write(f, f.relative_to(d).as_posix())
        os.replace(tmp, dest_zip)
        return str(eng["build"]), str(eng["kind"])

    def install_engine_copy(self, zip_path: Path, build: str, kind: str) -> None:
        """Unpack a runner another device sent and switch to it. Every name is checked (nothing may land outside the folder, no links), and the folder must hold a
        llama-server; otherwise nothing is changed. Windows only."""
        if os.name != "nt":
            raise LocalModelError(400, "A copied engine can only be unpacked on Windows.")
        n = int(re.match(r"^b(\d+)$", build).group(1)) if re.match(r"^b\d+$", build or "") else 0
        if n < NATIVE_DECISION_MIN_BUILD or kind not in engine_files_table():
            raise LocalModelError(400, "That engine isn't one this version can use.")
        folder = self._need_folder()
        target = folder / "engine" / f"{build}-{kind}"
        tmp = target.with_name(target.name + ".unpacking")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        root = tmp.resolve()
        try:
            with zipfile.ZipFile(zip_path) as z:
                for info in z.infolist():
                    if (info.external_attr >> 16) & 0o170000 == 0o120000 or not (root / info.filename).resolve().is_relative_to(root):
                        raise LocalModelError(502, "The engine that was sent has an unsafe file in it, so it wasn't unpacked.")
                z.extractall(tmp)
            if not (tmp / "llama-server.exe").exists() and next(tmp.rglob("llama-server.exe"), None) is None:
                raise LocalModelError(502, "The engine that was sent has no llama-server in it, so it wasn't used.")
        except zipfile.BadZipFile:
            shutil.rmtree(tmp, ignore_errors=True)
            raise LocalModelError(502, "The engine that was sent is damaged, so it wasn't used.")
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        shutil.rmtree(target, ignore_errors=True)
        os.replace(tmp, target)
        self._update(engine={"kind": kind, "build": build})
        self._prune_old_engines(target)

    def _prune_old_engines(self, keep: Path) -> None:
        """Remove the engine folders this one replaced (`b11361-vulkan` after an update): nothing else points at them. Not while a model is running from one."""
        if any(s.state in ("starting", "running") for s in self.servers.values()):
            return
        for d in keep.parent.iterdir():
            if d.is_dir() and d != keep and re.match(r"^b\d+-", d.name):
                shutil.rmtree(d, ignore_errors=True)

    # ---- updating the engine: only when the player presses Update ---------------------------
    @staticmethod
    def _system_key() -> str:
        if _platform().startswith("linux"):
            return "linux-arm64" if platform.machine().lower() in ("aarch64", "arm64") else "linux"
        return "win"

    async def _releases(self, force: bool = False) -> list:
        """llama.cpp's release list, kept for an hour. [] when it can't be read."""
        now = time.monotonic()
        cached = getattr(self, "_release_cache", None)
        if cached and not force and now - cached[0] < ENGINE_UPDATE_CHECK_SECONDS and cached[1]:
            return cached[1]
        async with self._session_factory() as session:
            releases = await llama_builds.fetch_releases(session)
        self._release_cache = (now, releases)
        return releases

    async def engine_update_info(self, force: bool = False) -> dict:
        """Is a newer numbered build available for the engine MultiProxy installed? Always the highest `bNNNNN` in the release list, never GitHub's "latest" (a stub).
        {"available": False} when there is nothing to offer, including when it can't be checked: a quiet check never complains."""
        none = {"available": False}
        eng = self._cfg().get("engine") or {}
        kind = str(eng.get("kind") or "")
        if not kind or not self.engine_exe() or not self.engine_build_number():
            return none
        release = llama_builds.newest_numbered(await self._releases(force))
        if not release or llama_builds.build_number(release.get("tag_name")) <= self.engine_build_number():
            return none
        try:
            files = llama_builds.files_for(release, kind, self._system_key())
        except llama_builds.BuildError as e:
            return {**none, "reason": str(e)}
        return {"available": True, "build": str(release["tag_name"]), "kind": kind, "sizeBytes": sum(sz for _, sz, _ in files), "files": files}

    async def update_engine(self) -> Job:
        """Download the newest numbered build of the same kind of engine and switch to it. Only ever called by the Update button."""
        info = await self.engine_update_info()
        if not info.get("available"):
            raise LocalModelError(400, info.get("reason") or "You already have the newest version.")
        return self.download_engine(info["kind"], build=info["build"], files=info["files"])

    def download_model(self, model_id: str) -> Job:
        spec = CATALOG_BY_ID.get(model_id)
        if not spec:
            raise LocalModelError(404, "Unknown model.")
        folder = self._need_folder()
        d = folder / "models" / model_id
        total = model_total_bytes(spec["files"])
        live = self.jobs.get(f"model:{model_id}")
        if live and live.state == "running":
            return live
        have = sum(f["size"] for f in spec["files"] if file_state(d / f["file"], f["size"], f["sha256"]) == "ok")     # files already here (kept) are not downloaded again, so their room is not asked for again
        self._check_space(total - have + 256 * 1024 * 1024)
        if model_id in self._removed():
            self._update(removed=[x for x in self._removed() if x != model_id] or None)
        files = [{"url": HF_URL.format(repo=spec["repo"], file=f["file"]), "dest": str(d / f["file"]),
                  "size": f["size"], "sha256": f["sha256"]} for f in spec["files"]]
        job = Job(f"model:{model_id}", spec["name"], files, total=total)
        return self._start_job(job)

    async def cancel(self, key: str) -> None:
        self._set_paused(key, None)
        job = self.jobs.get(key)
        if job and job.task and job.state == "running":
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        self.jobs.pop(key, None)

    # ---- servers -----------------------------------------------------------------------
    def skyrim_peak_mb(self) -> Optional[int]:
        v = skyrim_peak.figure(self._load_cfg())[0]
        return int(v) if self.HAS_GAME and v else None

    def engine_build_of(self, exe) -> int:
        """The build number of the engine at `exe`: the saved one for the engine MultiProxy downloaded, else what its own `--version` says (0 when it can't tell)."""
        d = self.engine_dir()
        try:
            if d and Path(exe).resolve().is_relative_to(d.resolve()):
                return self.engine_build_number()
        except (OSError, ValueError):
            pass
        key = str(exe)
        if key not in self._build_of:
            n = 0
            try:
                r = hidden_process.run([key, "--version"], capture_output=True, text=True, timeout=20, cwd=str(Path(key).parent))
                m = re.search(r"build\s+(\d+)", (r.stdout or "") + (r.stderr or ""))
                n = int(m.group(1)) if m else 0
            except Exception:
                n = 0
            self._build_of[key] = n
        return self._build_of[key]

    def engine_build_label(self, exe=None) -> str:
        """The build the settings are checked against: the engine in use (`b11361`), or the pinned build when none is installed yet."""
        n = self.engine_build_of(exe) if exe else self.engine_build_number()
        return f"b{n}" if n else LLAMA_CPP_BUILD

    def _spelling_known(self, exe) -> bool:
        """True for the pinned build: its flag spelling is the one in local_engine_flags. Any other engine only gets the three settings every build takes."""
        return self.engine_build_of(exe) == int(LLAMA_CPP_BUILD.lstrip("b"))

    def launch_plan(self, exe: Path, model: Installed, port: int, gpu_engine: bool) -> "local_engine_flags.LaunchPlan":
        settings, override = self.engine_settings.effective(self.device_id, model.id)
        fit, _ = local_engine_settings.resolve_fit_target_mb(settings, has_game=self.HAS_GAME, skyrim_peak_mb=self.skyrim_peak_mb(),
                                                            card_mb=self._gpu.memory_mb if self._gpu else None)
        return local_engine_flags.build_chat_command(
            exe, model.path, model.mmproj, port, model.id, settings, override, gpu_engine=gpu_engine, fit_target_mb=fit,
            skip_extras=model.id in self._skip_extras, spelling_known=self._spelling_known(exe), log_level=CHAT_LOG_LEVEL)

    def setup_of(self, model_id: str, exe=None, skip_extras: bool = False) -> tuple:
        """(fingerprint, plain words) of the setup `model_id` runs under, from the saved settings (what a start would use right now)."""
        settings, override = self.engine_settings.effective(self.device_id, model_id)
        build = self.engine_build_label(exe) if exe else self.engine_build_label(self.engine_exe())
        return local_engine_settings.setup_fingerprint(settings, override, build, skip_extras), local_engine_settings.setup_words(settings, override)

    def current_setup(self, model_id: str) -> tuple:
        """The setup the model's speed is being measured under: the running engine's own (a save only counts after a restart), else what the next start would use."""
        srv = self.servers.get(model_id)
        if srv and srv.state in ("starting", "running") and srv.setup_fp:
            return srv.setup_fp, srv.setup_text
        try:
            return self.setup_of(model_id)
        except Exception:
            return "", ""

    def note_request(self, model_id: str, success: bool) -> dict:
        """What the Speeds history saves beside a local reply: the setup it ran under and whether it was the first reply since the engine started (`cold`: slow for
        reasons that have nothing to do with the setup, so it is never averaged). `{}` for a model this program did not start."""
        srv = self.servers.get((model_id or "").lower()) or self.servers.get(model_id or "")
        if srv is None or not srv.setup_fp:
            return {}
        cold = 1 if srv.served == 0 else 0
        if success:
            srv.served += 1
        return {"setup_fp": srv.setup_fp, "cold": cold}

    def _started_info(self, model: Installed, exe: Path, cmd: list, s: "Server") -> dict:
        """What a start really did, for the screens: the command, the engine build, the settings it ran under, and how many layers are on the card when that is
        known: exactly when the layers are All / None / a number, from the engine's own dry run (the same fit it runs at start) when they are Automatic. Never guessed."""
        info = {"command": [str(c) for c in cmd], "engineBuild": self.engine_build_label(exe), "setup": s.setup_fp, "setupWords": s.setup_text,
                "startedAt": time.time(), "layers": {"mode": "unknown", "onCard": None, "total": None}}
        try:
            settings, override = self.engine_settings.effective(self.device_id, model.id)
            import gguf_header
            facts = gguf_header.read_model_facts(model.path)
            total = facts.offload_layers if facts else None
            mode = settings.get("layers", "all")
            kind = (self._cfg().get("engine") or {}).get("kind", "")
            if kind == "cpu":
                info["layers"] = {"mode": "none", "onCard": 0, "total": total}
            elif mode == "all":
                info["layers"] = {"mode": "all", "onCard": total, "total": total}
            elif mode == "none":
                info["layers"] = {"mode": "none", "onCard": 0, "total": total}
            elif isinstance(mode, int):
                info["layers"] = {"mode": "number", "onCard": min(mode, total) if total else mode, "total": total}
            elif mode == "auto" and self._command_builder == self._default_command:
                import local_engine_memory as lem
                fit, _ = local_engine_settings.resolve_fit_target_mb(settings, has_game=self.HAS_GAME, skyrim_peak_mb=self.skyrim_peak_mb(), card_mb=self._gpu.memory_mb if self._gpu else None)
                got = lem.check_with_engine(exe, model.path, settings, override, fit)
                info["layers"] = {"mode": "auto", "onCard": got.get("layersOnCard") if got else None, "total": total}
        except Exception:
            pass
        lay = info["layers"]
        info["onCardPercent"] = round(100 * lay["onCard"] / lay["total"]) if lay.get("onCard") is not None and lay.get("total") else None
        return info

    def _default_command(self, exe: Path, model: Installed, port: int, gpu_engine: bool) -> list:
        return self.launch_plan(exe, model, port, gpu_engine).cmd

    def _revalidate_settings(self, exe: Path) -> None:
        """When the engine's build is not the one the saved settings were checked against, check them again (extra flags the new build no longer takes are
        dropped, anything else invalid goes back to its default) and keep the list of what was reset for the screen."""
        rec = self.engine_settings.get(self.device_id)
        build = self.engine_build_label(exe)
        if not rec.saved or rec.build == build:
            return
        known = None
        try:
            r = hidden_process.run([str(exe), "--help"], capture_output=True, text=True, timeout=30, cwd=str(Path(exe).parent))
            known = local_engine_flags.flags_in_help((r.stdout or "") + "\n" + (r.stderr or "")) or None
        except Exception:
            known = None
        rec = self.engine_settings.revalidate(self.device_id, build, known)
        if rec.reset:
            self.log.warning(f"Local model settings were checked against engine {build}: {len(rec.reset)} setting(s) went back to their defaults")

    def _server(self, model_id: str) -> Server:
        return self.servers.setdefault(model_id, Server(model_id))

    def guard_note(self, model_id: str, state: Optional[str] = None) -> str:
        """The one line for a model the graphics memory guard stopped ("" when it didn't, or the model has been started again since)."""
        stop = self.guard_stops.get(model_id)
        if not stop or (state or self.state_of(model_id)[0]) != "stopped":
            return ""
        return gpu_guard.notice(stop["name"])

    def state_of(self, model_id: str) -> tuple[str, str]:
        s = self.servers.get(model_id)
        return (s.state, s.message) if s else ("stopped", "")

    def _record_pids(self) -> None:
        pids = {s.model_id: s.proc.pid for s in self.servers.values() if s.proc and s.proc.returncode is None}
        try:
            if pids:
                Path(self.pids_file).write_text(json.dumps(pids), encoding="utf-8")
            else:
                Path(self.pids_file).unlink(missing_ok=True)
        except OSError:
            pass

    def cleanup_leftovers(self) -> int:
        """Crash-safe cleanup: a previous run that died without stopping its models left their PIDs
        in the pids file. Kill any that are STILL a llama-server. Returns how many were killed."""
        try:
            recorded = json.loads(Path(self.pids_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        killed = 0
        for pid in (recorded.values() if isinstance(recorded, dict) else []):
            if isinstance(pid, int) and _pid_is_llama_server(pid):
                kill_tree_sync(pid)
                killed += 1
                self.log.info(f"Stopped a leftover local model engine from a previous run (process ID {pid})")
        try:
            Path(self.pids_file).unlink(missing_ok=True)
        except OSError:
            pass
        return killed

    async def start(self, model_id: str, skip_extras: bool = False) -> Server:
        """Start one model's llama-server and wait until it's ready (or has honestly failed). `skip_extras` leaves the player's own extra flags out of this one start."""
        s = self._server(model_id)
        self.guard_stops.pop(model_id, None)       # the player (or MultiProxy starting up) is starting it: the guard's "stopped to protect Skyrim" note is over
        if skip_extras and s.state not in ("starting", "running"):
            self._skip_extras.add(model_id)
        if s.state in ("starting", "running"):
            return s
        if self.start_gate is not None:
            refused = await self.start_gate()
            if refused:
                s.state, s.message = "failed", refused
                s.ready.set()
                return s
        model = next((m for m in self.installed() if m.id == model_id), None)
        if not model:
            s.state, s.message = "failed", "The model file is missing. Download it again."
            s.ready.set()
            return s
        exe = self.engine_exe()
        if not exe:
            return await self._start_after_engine(s, model)
        s.state, s.message, s.stopping = "starting", "", False
        s.ready = asyncio.Event()
        s.task = asyncio.create_task(self._launch(s, model, exe))
        return s

    async def default_engine_kind(self) -> str:
        """The engine build recommended for this device (what the Download button pre-picks)."""
        return engine_options(await self.gpu())[0]["kind"]

    async def _start_after_engine(self, s: Server, model: Installed) -> Server:
        """Loading a model is the same as installing it: when the engine isn't here yet, get it first (one time), then load the model,
        as one action. A reason the engine can't be fetched (no folder chosen, not enough room, an unsupported system) ends up on the
        model's own row."""
        try:
            job = self.download_engine(await self.default_engine_kind())
        except LocalModelError as e:
            s.state, s.message = "failed", e.message
            s.ready.set()
            return s
        s.state, s.message, s.stopping, s.engine_wait = "starting", "", False, True
        s.ready = asyncio.Event()
        s.task = asyncio.create_task(self._launch_after_engine(s, model, job))
        self.log.info(f"Getting the llama.cpp engine first (one time) so {model.model_name} can load")
        return s

    async def _launch_after_engine(self, s: Server, model: Installed, job: Job) -> None:
        if job.task:
            await asyncio.gather(job.task, return_exceptions=True)
        s.engine_wait = False
        exe = self.engine_exe()
        if s.stopping:
            s.ready.set()
            return
        if job.state != "done" or not exe:
            msg = job.error or "The software download didn't finish."
            # this message sits beside a model's Try again button, not a Download button
            s.state, s.message = "failed", ENGINE_FAILED_OFFLINE if msg == DOWNLOAD_INTERRUPTED else msg.replace("Press Download to", "Press Try again to").replace("Press Download to try again", "Press Try again")
            self.log.error(f"Local model {model.model_name} couldn't load: the engine download failed ({msg})")
            s.ready.set()
            return
        await self._launch(s, model, exe)

    async def _verify_catalog_files(self, model: Installed) -> str:
        """A model MultiProxy downloaded is started only when its files really are the ones it promised (checksum, noted after the first check so a big file isn't
        hashed on every start). A file that is the right size but the wrong bytes is removed. "" when all is well. A model the player pointed at by its own path has no
        promised checksum and is left alone."""
        spec = CATALOG_BY_ID.get(model.id)
        if not spec or model.custom:
            return ""
        for f in spec["files"]:
            p = model.path.parent / f["file"]
            if p.exists() and not await asyncio.to_thread(verify_file, p, f["size"], f["sha256"]):
                p.unlink(missing_ok=True)
                _verified_note(p).unlink(missing_ok=True)
                self.log.warning(f"{p.name} didn't match its checksum and was removed")
                return FAILED_SAFETY.format(name=p.name)
        return ""

    async def _launch(self, s: Server, model: Installed, exe: Path) -> None:
        async with self._start_lock:
            if s.stopping:
                return
            bad = await self._verify_catalog_files(model)
            if bad:
                s.state, s.message = "failed", bad
                s.ready.set()
                return
            kind = (self._cfg().get("engine") or {}).get("kind", "")
            s.port = _free_port()
            folder = self.folder
            logs = (folder / "logs") if folder else Path(self.pids_file).parent
            logs.mkdir(parents=True, exist_ok=True)
            s.log_path = logs / f"llama-server-{model.id}.log"
            if self._command_builder == self._default_command:
                await asyncio.to_thread(self._revalidate_settings, exe)
            cmd = self._command_builder(exe, model, s.port, kind != "cpu")
            skipped = model.id in self._skip_extras
            self._skip_extras.discard(model.id)
            s.used_extras = bool(self.engine_settings.effective(self.device_id, model.id)[0].get("extraArgs")) and not skipped
            s.setup_fp, s.setup_text = self.setup_of(model.id, exe, skipped)
            s.served = 0
            plan = gpu_choice.Plan()
            if self._choice_for(model.id):
                try:
                    plan = await asyncio.to_thread(lambda: gpu_choice.plan_for(self._choice_for(model.id), self._cards_fn(), kind, exe, self._devices_fn))
                except Exception:
                    plan = gpu_choice.Plan()
            self.gpu_notes[model.id] = self.gpu_note = plan.note
            self.gpu_cards[model.id] = plan.card.name if plan.card else None
            if plan.note:
                self.log.info(f"Local model {model.model_name}: {plan.note}")
            cmd = list(cmd) + list(plan.args)
            s.cmd = cmd
            flags = 0
            kwargs: dict = {}
            # Every LLAMA_ARG_* variable is removed from the engine's environment: the engine reads them too, so one the player set once in Windows would
            # silently change every setting MultiProxy leaves out.
            if os.name == "nt":
                flags = 0x08000000 | 0x00000200        # no console window | its own process group
                kwargs["creationflags"] = flags
                kwargs["env"] = local_engine_flags.clean_environment(gpu_choice.merge_env(None, plan))
            else:
                kwargs["start_new_session"] = True
                kwargs["env"] = local_engine_flags.clean_environment(
                    gpu_choice.merge_env({**os.environ, "LD_LIBRARY_PATH": f"{exe.parent}:{os.environ.get('LD_LIBRARY_PATH', '')}".rstrip(":")}, plan))
            s.started = await asyncio.to_thread(self._started_info, model, exe, cmd, s)
            self.log.info(f"Starting local model {model.model_name} on port {s.port}")
            try:
                with open(s.log_path, "wb") as logf:
                    s.proc = await hidden_process.create_subprocess_exec(
                        *cmd, stdout=logf, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
                        cwd=str(exe.parent), **kwargs)
            except OSError as e:
                s.state, s.message = "failed", f"The model wouldn't start: {e.strerror or e}"
                s.ready.set()
                return
            self._record_pids()
            ready = await self._wait_healthy(s)
            if ready:
                s.state, s.message = "running", ""
                self.log.info(f"Local model {model.model_name} is ready")
            retry_on_vulkan = bool(not ready and kind in FAST_KINDS and not s.stopping and s.state == "failed")
            if not retry_on_vulkan:
                s.ready.set()
        if retry_on_vulkan:
            await self._fall_back_to_vulkan(s, model, kind)
            return
        if ready:
            code = await s.proc.wait()
            if not s.stopping:
                s.state, s.message = "failed", explain_failure(self._log_tail(s), code)
                self.log.error(f"Local model {model.model_name} stopped on its own: {s.message}")
                self._record_pids()

    async def _fall_back_to_vulkan(self, s: Server, model: Installed, kind: str) -> None:
        """A faster build (AMD or Intel) that won't start is replaced by the works-on-anything build of the SAME release, once, and the model is started again.
        The one-line reason is kept in `engine_notes` (shown beside the model); the player can pick the faster build again in Advanced."""
        failed_message = s.message
        build = str((self._cfg().get("engine") or {}).get("build") or LLAMA_CPP_BUILD)
        self.log.warning(f"The {kind} engine ({build}) wouldn't start {model.model_name}; switching to the Vulkan one. It said: {failed_message}")
        try:
            files = None
            if build != LLAMA_CPP_BUILD:
                release = llama_builds.release_for(await self._releases(), build)
                files = llama_builds.files_for(release, "vulkan", self._system_key()) if release else None
                if not files:
                    raise LocalModelError(502, "We couldn't find the version of this download that works on any PC.")
            job = self.download_engine("vulkan", build=build, files=files)
            if job.task:
                await asyncio.gather(job.task, return_exceptions=True)
            exe = self.engine_exe()
            if job.state != "done" or not exe or s.stopping:
                raise LocalModelError(502, job.error or "The version that works on any PC didn't finish downloading.")
        except (LocalModelError, llama_builds.BuildError) as e:
            s.state, s.message = "failed", failed_message
            self.log.error(f"Couldn't switch {model.model_name} to the Vulkan engine: {getattr(e, 'message', e)}")
            s.ready.set()
            return
        self.engine_notes[model.id] = FALLBACK_NOTE
        await self._launch(s, model, exe)

    async def _wait_healthy(self, s: Server) -> bool:
        deadline = time.monotonic() + SERVER_LOAD_TIMEOUT
        url = f"http://127.0.0.1:{s.port}/health"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            while time.monotonic() < deadline and not s.stopping:
                if s.proc.returncode is not None:
                    s.state, s.message = "failed", explain_failure(self._log_tail(s), s.proc.returncode)
                    self.log.error(f"Local model {s.model_id} failed to start: {s.message}")
                    self._record_pids()
                    return False
                try:
                    async with session.get(url) as r:
                        if r.status == 200:
                            return True
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(HEALTH_POLL_SECONDS)
        if not s.stopping:
            s.state, s.message = "failed", "The model took too long to load."
            await self._kill(s)
        return False

    def _log_tail(self, s: Server, n: int = 6000) -> str:
        try:
            with open(s.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - n))
                return f.read().decode("utf-8", "replace")
        except (OSError, TypeError):
            return ""

    async def _kill(self, s: Server) -> None:
        proc = s.proc
        if proc and proc.returncode is None:
            await asyncio.to_thread(kill_tree_sync, proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
        self._record_pids()

    async def restart(self, model_id: str) -> Server:
        """Stop a model and start it again with the settings saved now ("Save and restart this model")."""
        await self.stop(model_id)
        return await self.start(model_id)

    async def restart_running(self) -> list:
        """Restart every model that is running or starting, one after another ("Save and restart running models") -> their ids."""
        ids = [mid for mid, srv in self.servers.items() if srv.state in ("starting", "running")]
        for mid in ids:
            await self.stop(mid)
        for mid in ids:
            await (await self.start(mid)).ready.wait()
        return ids

    def failure_details(self, model_id: str) -> dict:
        """What the "engine did not start" message needs: the plain reason, the engine's own last error lines (in its own words), and whether the player's own
        extra flags were part of that start (so "Start without the extra flags" makes sense)."""
        srv = self.servers.get(model_id)
        if not srv or srv.state != "failed":
            return {"failed": False}
        tail = self._log_tail(srv)
        words = engine_words(tail)
        inst = next((m for m in self.installed() if m.id == model_id), None)
        said = any(_SETTINGS_ERROR.search(w) for w in words)
        return {"failed": True, "id": model_id, "name": inst.name if inst else model_id, "message": srv.message, "engineLines": words,
                "usedExtraFlags": srv.used_extras, "canStartWithoutExtras": srv.used_extras,
                "cameFromExtraFlags": bool(srv.used_extras and said)}      # the engine named a bad setting AND the player's own flags were in that start

    async def stop(self, model_id: str) -> None:
        s = self.servers.get(model_id)
        if not s or s.state == "stopped":
            return
        s.stopping = True
        if s.engine_wait and not any(o.engine_wait and o is not s for o in self.servers.values()):
            await self.cancel("engine")
        await self._kill(s)
        if s.task and not s.task.done():
            await asyncio.gather(s.task, return_exceptions=True)
        s.state, s.message, s.port, s.engine_wait = "stopped", "", None, False
        s.ready.set()
        self.log.info(f"Local model {model_id} stopped")

    async def stop_all(self) -> None:
        for mid in list(self.servers):
            await self.stop(mid)
        self._record_pids()

    def stop_all_sync(self) -> None:
        """Last-ditch cleanup for interpreter exit, when no event loop is left to await."""
        for s in self.servers.values():
            if s.proc and s.proc.returncode is None:
                kill_tree_sync(s.proc.pid)
        try:
            Path(self.pids_file).unlink(missing_ok=True)
        except OSError:
            pass

    async def start_all(self) -> None:
        """Start every downloaded model (one after another, so they don't all load at once). Called
        in the background when the proxy starts, so they're warm by the time the game asks."""
        if not self.engine_exe():
            return
        for m in self.installed():
            s = await self.start(m.id)
            await s.ready.wait()

    # ---- the router's entry point --------------------------------------------------------
    async def ensure_ready(self, model: str) -> str:
        """Base URL (`http://127.0.0.1:PORT/v1`) for a `local/<id>` request, waiting for a model
        that's still loading. Raises LocalModelError with an honest reason otherwise."""
        inst = self.find(model)
        if not inst:
            raise LocalModelError(404, f"'{model}' isn't one of your local models. Add it on the Local device tab.")
        s = self._server(inst.id)
        if s.state in ("stopped", "failed") and not self.engine_exe():
            s = await self.start(inst.id)                    # the engine is missing: start fetching it, then wait or answer "still loading" as usual
        if s.state == "stopped":
            raise LocalModelError(503, f"{inst.model_name} is stopped. Start it from the dashboard's Local device tab.")
        if s.state == "failed":
            raise LocalModelError(503, f"{inst.model_name} isn't running: {s.message}")
        if s.state == "starting":
            try:
                await asyncio.wait_for(s.ready.wait(), timeout=REQUEST_WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                raise LocalModelError(503, f"{inst.model_name} is still loading. Try again in a moment.")
            if s.state == "failed":
                raise LocalModelError(503, f"{inst.model_name} isn't running: {s.message}")
            if s.state != "running":
                raise LocalModelError(503, f"{inst.model_name} is stopped.")
        if s.proc and s.proc.returncode is not None:
            s.state, s.message = "failed", explain_failure(self._log_tail(s), s.proc.returncode)
            raise LocalModelError(503, f"{inst.model_name} isn't running: {s.message}")
        return f"http://127.0.0.1:{s.port}/v1"

    # ---- what the dashboard shows ----------------------------------------------------------
    @property
    def model_server(self) -> bool:
        """This device is set up as a model server (no Skyrim on it)."""
        return bool(self._load_cfg().get("model_server"))

    async def snapshot(self) -> dict:
        gpu = await self.gpu()
        ms = self.model_server
        gpus = {spec["id"]: await self.gpu_for(spec["id"]) for spec in CATALOG} if any(self._choice_for(spec["id"]) for spec in CATALOG) else {}
        raw_peak, peak_day = skyrim_peak.figure(self._load_cfg())
        peak = int(raw_peak) if raw_peak and not ms else None
        eng_cfg = self._cfg().get("engine") or {}
        exe = self.engine_exe()
        job = self.jobs.get("engine")
        installed = {m.id: m for m in self.installed()}
        kept_ids = {k["id"] for k in self.kept()}
        catalog = []
        for spec in CATALOG:
            total = model_total_bytes(spec["files"])
            j = self.jobs.get(f"model:{spec['id']}")
            catalog.append({
                "id": spec["id"], "name": spec["name"], "blurb": spec["blurb"], "vision": spec["vision"],
                "sizeBytes": total, "sizeText": fmt_gb(total), "fit": fit_check(total, gpus.get(spec["id"], gpu), reserve_skyrim=not ms, skyrim_peak_mb=peak),
                "fitWhy": fit_why(total, gpus.get(spec["id"], gpu), peak) if not ms else "",
                "installed": spec["id"] in installed, "kept": spec["id"] in kept_ids,
                "download": j.to_json() if j else self.paused_json(f"model:{spec['id']}", self._spec_parts(spec), total),
                "update": model_disk_state(self.folder, spec) == "update",
            })
        ready = []
        for m in installed.values():
            state, message = self.state_of(m.id)
            ready.append({"id": m.id, "name": m.name, "vision": m.vision, "modelName": m.model_name,
                          "sizeText": fmt_gb(m.size), "state": state, "message": message, "custom": m.custom,
                          "engineWait": bool(self.servers.get(m.id) and self.servers[m.id].engine_wait),
                          "guardNote": self.guard_note(m.id, state),
                          "gpuNote": self.gpu_notes.get(m.id, ""), "engineNote": self.engine_notes.get(m.id, ""), "gpuCard": self.gpu_cards.get(m.id),
                          "started": (self.servers.get(m.id).started if self.servers.get(m.id) and state == "running" else None)})
        return {
            "supported": platform_supported(), "modelServer": ms, "hasGame": self.HAS_GAME, "skyrimPeakMb": peak, "skyrimPeakDay": peak_day if peak else None,
            "folder": str(self.folder) if self.folder else None,
            "gpu": gpu.to_json() if gpu else None,
            "gpuNote": self.gpu_note,
            "engine": {
                "installed": bool(exe), "kind": eng_cfg.get("kind") if exe else None,
                "build": eng_cfg.get("build") if exe else None, "pinnedBuild": LLAMA_CPP_BUILD,
                "options": engine_options(gpu), "download": job.to_json() if job else self.paused_engine_json(),
            },
            "catalog": catalog,
            "installed": ready,
        }

    def provider_status(self) -> dict:
        """Compact state for the 'Local' provider pill / Quick Test list."""
        rows, running = [], 0
        for m in self.installed():
            state, _ = self.state_of(m.id)
            if state == "running":
                running += 1
            rows.append([m.model_name, m.name, "Sees pictures" if m.vision else ""])
        if not rows:
            return {"state": "off", "status": "Nothing downloaded yet", "models": [], "testable": False}
        if running:
            return {"state": "ok", "status": f"{running} running", "models": rows, "testable": True}
        return {"state": "warn", "status": "Downloaded, not running", "models": rows, "testable": False}


def pick_folder_dialog(title: str = "Choose where to save local models") -> Optional[str]:
    """A real native folder picker, kept in front of every window so it can't open out of sight.
    Runs on the proxy's own device (the dashboard is local). Blocking -- call via asyncio.to_thread."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except Exception:       # no screen (a server with no desktop)
        return None
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        root.focus_force()
        chosen = filedialog.askdirectory(title=title, parent=root)
    finally:
        root.destroy()
    return chosen or None


def pick_file_dialog(title: str = "Choose a model file", filetypes: Optional[list] = None) -> Optional[str]:
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except Exception:
        return None
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        root.focus_force()
        chosen = filedialog.askopenfilename(title=title, parent=root, filetypes=filetypes or [("Model files", "*.gguf"), ("All files", "*.*")])
    finally:
        root.destroy()
    return chosen or None


def save_file_dialog(title: str, default_name: str, filetypes: Optional[list] = None, extension: str = "") -> Optional[str]:
    """A native Save dialog kept in front of every window, like the pickers above. The player always chooses where; None when they close it."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except Exception:
        return None
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        root.focus_force()
        chosen = filedialog.asksaveasfilename(title=title, parent=root, initialfile=default_name, defaultextension=extension, filetypes=filetypes or [("All files", "*.*")])
    finally:
        root.destroy()
    return chosen or None
