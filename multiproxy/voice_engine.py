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
from local_models import GpuInfo, LocalModelError, clean_text, detect_gpu, kill_tree_sync, old_driver_notice, unpack_archive, _free_port
from voice_downloads import DownloadStore, FileSpec, InternetSource, Source, link_or_copy

# ---------------------------------------------------------------------------------------------------------------------------------
# What is installed, and where it comes from
# ---------------------------------------------------------------------------------------------------------------------------------
ENGINE_VERSION = "v0.9.5"          # pinned: tested with this release (published 2026-11-14; was v0.9.0, git 795c45fb, until 2026-11-16); upstream releases fast, so a newer one is only ever offered after it's been tested
_RELEASE = "https://github.com/0xShug0/audio.cpp/releases/download/" + ENGINE_VERSION + "/"

MODEL_NAME = "higgs-audio-v3-tts-4b-q8_0.gguf"
MODEL = FileSpec(
    name=MODEL_NAME,
    # By the repository commit of 2026-10-05, like every other voice below, NEVER "main": the publisher re-uploaded this file on `main` after v0.9.2 (2026-10-19, "Refresh embedded GGUF specs";
    # 5,095,359,136 bytes, SHA-256 cfa5cc01...) and every new install that followed `main` was refused with "downloaded at the wrong size". This commit still serves the pinned file.
    url="https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/e36610ac69b5262e914a52635324050bee8f1ad2/Higgs-Audio-v3-TTS-4B-GGUF/" + MODEL_NAME,
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

# Breeze TTS 2 (BreezeBlue; the engine's own `breeze_tts` family): the Q8_0 file, the license and the notice that sit next to it in the engine's Hugging Face directory `Breeze-TTS-2-GGUF`.
# Pinned by the repository commit of 2026-11-15 (never `main`: the publisher re-uploads files there); size and SHA-256 are Hugging Face's listing for that commit, and the model file
# and both text files were downloaded and hashed again here (2026-11-16): equal. The model is under the BreezeBlue Research and Non-Commercial License: MultiProxy never bundles or
# hosts it, the person downloads it after accepting the license on the voice card, and the license text and notice are kept beside it (as that license asks of anyone who hands the files on).
_HF_BREEZE = "https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/7a103b33f04120535bdfe97b5f65b79d3c90d708/Breeze-TTS-2-GGUF/"
BREEZE_MODEL = FileSpec(name="breeze-tts-2-q8_0.gguf", url=_HF_BREEZE + "breeze-tts-2-q8_0.gguf", size=5_079_670_336,
                        sha256="b17b89b1c21107c44e8d556ab0a856d7acd06bdd0c914e68e0e57f6e33f555a3")
# The two other files Breeze's "How it should sound" choice can run (2026-11-19), from the SAME pinned commit as the Q8_0 file above (the repository's head was still that commit on 2026-11-19);
# size and SHA-256 are Hugging Face's listing for it, and the copies used for the speed trial were hashed again here: equal. Q4_K is a finished smaller file; bf16 is the full-quality
# file that the engine converts when the voice starts (its own `weight_type` setting: q8_0 or f16).
BREEZE_Q4K = FileSpec(name="breeze-tts-2-q4_k.gguf", url=_HF_BREEZE + "breeze-tts-2-q4_k.gguf", size=4_459_531_840,
                      sha256="6d6cd66dc487c6843f8797e1e569ad0e3a2dc341e03e4be69330aae5c81920bd")
BREEZE_BF16 = FileSpec(name="breeze-tts-2-bf16.gguf", url=_HF_BREEZE + "breeze-tts-2-bf16.gguf", size=7_342_918_784,
                       sha256="5620623371e2011e9bb22389f12252d3969061adad139b3cdda6b1473a82a0d5")
BREEZE_LICENSE_FILES = (
    FileSpec(name="breeze-tts-2-LICENSE.txt", url=_HF_BREEZE + "LICENSE", size=18_718, sha256="44c5d53ee309bc36b5ea3ec2144abc6f3996ce431dedbe262f302a85956bc94b"),
    FileSpec(name="breeze-tts-2-NOTICE.txt", url=_HF_BREEZE + "NOTICE", size=146, sha256="c03e2ed6cde7844a6b4336f548b362a7bd28fa1d72b9d8f4ce886e9f0a72d8d5"),
)
# The license page on Hugging Face (the same commit): what the card's "license" link opens.
BREEZE_LICENSE_URL = "https://huggingface.co/audio-cpp/audio.cpp-gguf/blob/7a103b33f04120535bdfe97b5f65b79d3c90d708/Breeze-TTS-2-GGUF/LICENSE"

# What each Higgs-style tag means as a spoken direction, for a voice that takes a free-text `instruct` (Breeze). A tag that has no entry here (pauses, sound effects, "expressive")
# is dropped. Measured 2026-11-16: on audio.cpp v0.9.5 a direction changes the audio (a "slowly and sadly" line was 60 % longer); v0.9.0 ignored it.
DIRECTIONS = {
    "emotion:affection": "warmly and tenderly", "emotion:amusement": "amused, with a smile in the voice", "emotion:anger": "angrily", "emotion:arousal": "in a low, breathy, intimate voice",
    "emotion:awe": "in awe, hushed and amazed", "emotion:bitterness": "bitterly", "emotion:confusion": "confused and unsure", "emotion:contemplation": "thoughtfully and slowly",
    "emotion:contentment": "calm and content", "emotion:determination": "firmly, with determination", "emotion:disgust": "with disgust", "emotion:elation": "joyfully and excited",
    "emotion:enthusiasm": "enthusiastically", "emotion:fear": "fearfully, with a shaky voice", "emotion:helplessness": "helpless and defeated", "emotion:longing": "wistfully, with longing",
    "emotion:pride": "proudly", "emotion:relief": "with relief, as if letting out a breath", "emotion:sadness": "sadly", "emotion:shame": "ashamed and quiet", "emotion:surprise": "with surprise",
    "style:singing": "in a singing voice", "style:shouting": "shouting loudly", "style:whispering": "in a whisper",
    "prosody:speed_very_slow": "very slowly", "prosody:speed_slow": "slowly", "prosody:speed_fast": "quickly", "prosody:speed_very_fast": "very quickly",
    "prosody:pitch_low": "in a low pitch", "prosody:pitch_high": "in a high pitch",
}
_NATIVE_PIPE_TAG = re.compile(r"<\|\s*([a-z]+)\s*:\s*([a-z_]+)\s*\|>", re.I)


def direction_from_tags(text: str) -> str:
    """The spoken direction the tags in a line stand for ("Speak fearfully, with a shaky voice."), or "" when none of them has one. Each wording is used once, in the order the tags came."""
    parts: list = []
    for kind, name in _NATIVE_PIPE_TAG.findall(text or ""):
        words = DIRECTIONS.get(f"{kind.lower()}:{name.lower()}")
        if words and words not in parts:
            parts.append(words)
    return ("Speak " + ", ".join(parts) + ".") if parts else ""


TASKS = ("vad", "asr", "diar", "sep", "gen", "tts", "clon", "vc", "s2s", "align", "vdes", "spk", "svc", "midi")   # the task names the real engine (audio.cpp v0.9.0) accepts in server.json


@dataclass(frozen=True)
class Take:
    """One way a voice model can run ("How it should sound", Breeze only): which file, the engine settings that go with it, the graphics memory the check uses, and the words and
    rounded numbers the card shows (all measured on one RTX 5090 with the clip already prepared: docs/plans/2026-11-18-breeze-speed.md)."""
    key: str
    label: str                # the card's name for it
    one_line: str             # how it sounds, in plain words
    file: FileSpec
    options: tuple            # the engine settings for this take (same shape as VoiceModel.options; it replaces them)
    vram_bytes: int           # what the memory check uses: the measured figure plus a little room
    vram_vulkan_bytes: int
    short_line: str           # as the card shows them (rounded)
    fifteen_words: str
    memory_text: str
    converts: bool = False    # the engine converts the file when the voice starts (about 20 s on the trial card): the start is allowed longer


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
    licence_files: tuple = ()  # the license text and notice kept beside the model file (Breeze: its license asks for them to go with the files); small, pinned like any file
    directions: bool = False   # takes a free-text `instruct` ("Speak slowly and sadly."): the line's tags are turned into one (DIRECTIONS) instead of just being removed
    takes: tuple = ()          # the ways it can run (Take); empty for a voice with only one. The row's own `file`, `options` and `vram_*` are the default take's (nothing changes for anyone who never chooses)
    default_take: str = ""

    def take(self, key: Optional[str]) -> Optional[Take]:
        return next((t for t in self.takes if t.key == key), None)

    def with_take(self, key: Optional[str]) -> "VoiceModel":
        """This row as it is when run the way `key` says (its file, settings and memory figure); the row itself for the default take, for no takes and for a key it doesn't know."""
        t = self.take(key)
        if t is None or key == self.default_take:
            return self
        return replace(self, file=t.file, options=t.options, vram_bytes=t.vram_bytes, vram_vulkan_bytes=t.vram_vulkan_bytes)

    def model_files(self) -> tuple:
        """Every model file of this voice (its own, then each take's), without repeats: what "installed", "remove" and the Courier's file list have to know."""
        out, seen = [], set()
        for f in (self.file,) + tuple(t.file for t in self.takes):
            if f.name not in seen:
                seen.add(f.name)
                out.append(f)
        return tuple(out)

    def extra_files(self) -> tuple:
        """Every file that is installed WITH this voice besides its model: the companion model, then the license files."""
        return ((self.companion,) if self.companion is not None else ()) + tuple(self.licence_files)


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
        "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base", needs_clip=True, needs_words=True, companion=ASR_FILE,
        # The engine keeps the prepared form of ONE voice clip by default, so characters taking turns paid the clip's work on every line (measured 2026-11-19, RTX 5090: 3.2 to 4.1 s for a short line
        # with 10 s clips, 6 to 8 s with 20 s, 8.5 to 9.8 s with 30 s). 4 slots is the smallest number that makes every later line of up to four characters as fast as a repeat (0.6 s, 0.7 s, 1.0 s):
        # 2 slots changed nothing with four voices (they push each other out), 8 and 16 were no faster. No graphics memory cost showed (peak over what was in use with four 30 s clips: 5.0 GiB with
        # 4 slots, 5.5 GiB without; the figure above, 6.4 GiB, already covers it). The option works only WITH the family prefix (the bare name is accepted and does nothing).
        options=(("session_options", {"qwen3_tts.voice_prompt_cache_slots": 4}),)),
    "chatterbox": VoiceModel(
        "chatterbox", "Chatterbox voice", "Chatterbox", "Natural voices that copy a short clip. Needs about 5 GB of graphics memory.",
        FileSpec(name="chatterbox-q8_0.gguf", url=_HF_VOICES + "Chatterbox-GGUF/chatterbox-q8_0.gguf",
                 size=2_088_393_668, sha256="d586dd1aa59613cab8046176fb7ca5ba191c02a9b10ffa5b0d892ed22b470656"),
        "chatterbox", "clon", "chatterbox", int(4.3 * GIB), int(4.9 * GIB), 5 * GIB, "chatterbox-mit-2026-10", "Resemble AI",
        "https://github.com/resemble-ai/chatterbox/blob/master/LICENSE", needs_clip=True,
        # Same finding (2026-11-19, RTX 5090): a line of a voice that is not the one just heard cost 2.7 s (10 s clip), 3.8 s (20 s), 4.5 s (30 s); with 4 slots 1.7 to 1.8 s whatever the clip (1 to 2.8 s saved
        # a turn). 2 slots did nothing with four voices, 8 was no faster. 4 slots cost no graphics memory (4.0 GiB peak over what was in use, as without; the figure above is 4.3). The BARE spelling is used:
        # every run with it finished. (The family-prefixed spelling was not shown to be better or worse: the one run that tried it crashed with ggml's "GGML_ASSERT(ctx->mem_buffer != NULL)", but so did every
        # run of either spelling once Skyrim was running on the same PC, so the crash was the PC's memory, not the spelling.)
        options=(("session_options", {"conditionals_cache_slots": 4}),)),
    # Measured on the RTX 5090 (CUDA, Q8_0, engine v0.9.5): 4.7 to 5.1 GiB over what was already in use for a normal line, 6.7 GiB for one of 1,264 characters; the figure here sits
    # between them. The Vulkan and ordinary-memory figures are estimates (Vulkan, Linux and a Courier PC were not run).
    "breeze": VoiceModel(
        "breeze", "Breeze", "Breeze", "Natural voices that copy a short clip and take directions on how to speak. English only. Needs about 6 GB of graphics memory.",
        BREEZE_MODEL, "breeze_tts", "tts", "breeze", int(6.0 * GIB), int(6.6 * GIB), 6 * GIB, "breeze-tts-2-license-1.1-2026-09", "BreezeBlue", BREEZE_LICENSE_URL,
        "Breeze is for personal, non-commercial use only, and you need permission to copy anyone's voice. MultiProxy will download the files for you.",
        needs_clip=True, needs_words=True, companion=ASR_FILE, licence_files=BREEZE_LICENSE_FILES, directions=True,
        # The engine keeps the worked-out voice clip of ONE voice by default, so a game where two characters take turns redoes the clip's work on every line (measured 2026-11-18:
        # +0.4 s for a 10 s clip, +1.2 s for 20 s, +1.5 s for 30 s on a RTX 5090). 16 slots: same audio, no extra graphics memory seen, the second line of a voice is 2x faster.
        options=(("session_options", {"breeze_tts.reference_cache_slots": 16}),),
        # "How it should sound" (2026-11-19). The row above is Standard: nothing changes for anyone who never chooses. Memory: the measured peak over what was already in use, plus half a GB of
        # room for a normal line (Standard keeps its older, roomier 6 GB); the Vulkan figure is a tenth more, like the others (not measured). Words and numbers: design/mockup-breeze-sound-choice.html.
        takes=(
            Take("quickest", "Quickest", "A smaller, lighter version. Faster on long lines and easier on graphics memory.", BREEZE_Q4K,
                 (("session_options", {"breeze_tts.reference_cache_slots": 16}),), int(5.0 * GIB), int(5.5 * GIB), "about 0.3 s", "about 1.1 s", "4.3 GB"),
            Take("standard", "Standard", "The file you already have. A good balance of speed and sound.", BREEZE_MODEL,
                 (("session_options", {"breeze_tts.reference_cache_slots": 16}),), int(6.0 * GIB), int(6.6 * GIB), "about 0.4 s", "about 1.3 s", "5 GB"),
            Take("more", "More natural", "Uses the full-quality model to sound more natural. Same speed as Standard, but takes about 20 seconds longer to start.", BREEZE_BF16,
                 (("session_options", {"breeze_tts.reference_cache_slots": 16, "weight_type": "q8_0"}),), int(5.5 * GIB), int(6.0 * GIB), "about 0.3 s", "about 1.2 s", "5 GB", converts=True),
            Take("most", "Most natural", "The full-quality model at higher detail. A bit slower on long lines and needs much more graphics memory.", BREEZE_BF16,
                 (("session_options", {"breeze_tts.reference_cache_slots": 16, "weight_type": "f16"}),), int(8.0 * GIB), int(8.8 * GIB), "about 0.4 s", "about 1.7 s", "8 GB", converts=True),
        ), default_take="standard"),
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
        _asset("audio-v0.9.5-bin-windows-x64-cuda13.3.zip", 280_869_815, "9d413d8565ad26e1ae33743e9c884e733f1684169be6f8934e1af9ee2e57a872"),
        _asset("audio-v0.9.5-cudart-windows-x64-cuda13.3.zip", 575_457_454, "8614040110cc5852b1dd2192e80344295500f4fa7f3199a51c737c2c7549c3dc")),
        1_150_000_000),                                                  # measured: 1.11 GB unpacked from the v0.9.5 archives (was 1.1 GB)
    # Not run by us (only 13.3 was measured). Same publisher, checksums from the release page; the release notes say it adds older (Pascal) cards.
    "win-cuda12.4": Build("win-cuda12.4", "NVIDIA graphics card (older driver)", "cuda", (
        _asset("audio-v0.9.5-bin-windows-x64-cuda12.4.zip", 469_569_407, "81f38a0a62935bdf4b2605541a21d50223b2e12b661887031705dd8a369f844b"),
        _asset("audio-v0.9.5-cudart-windows-x64-cuda12.4.zip", 607_273_675, "fa55dfaff1d4b5c6e8fac460d64f380b9df76cd46435f187c39b05700f623a7e")),
        2_150_000_000),                                                  # the archives' own listing: 2.09 GB unpacked (v0.9.5; the engine was not run on this build)
    "win-vulkan": Build("win-vulkan", "other graphics card (AMD, Intel)", "vulkan", (
        _asset("audio-v0.9.5-bin-windows-x64-vulkan.zip", 85_685_046, "dc103660e5fac85860677af2b39361154d863953777ab812d891721e6e163148"),),
        260_000_000, needs_speed_check=True),                            # the archive's own listing: 254 MB unpacked (v0.9.5; was 170 MB)
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
    _asset("audio-v0.9.5-bin-ubuntu-x64-cuda12.8-colab.tar.gz", 231_408_582, "aee0c4d50f1fa7eed4daaeca791a29fddf350a0855c539c127041299293c992e"),),
    300_000_000, system="linux", min_glibc=(2, 35))                                 # unpacked size measured: 293 MB (v0.9.5; was 273 MB); the program still asks for C library 2.35 at most
# The Vulkan one is the publisher's "portable" build, not the plain one: the plain build died with an illegal instruction ("signal 4") 33 s after
# starting on the test device's processor (Core Ultra 9 285K, no AVX-512), the same crash the plain Windows CPU build has on it; the portable build loads
# the processor code that fits the machine (`libggml-cpu-*.so` next to the program), so it can't hit that.
BUILDS["linux-vulkan"] = Build("linux-vulkan", "graphics card (Vulkan)", "vulkan", (
    _asset("audio-v0.9.5-bin-ubuntu-x64-vulkan-portable.tar.gz", 90_609_676, "39be151ea211396adc6837560d00a440f8d2720b2c1e0c60778bd676335bdc06"),),
    250_000_000, system="linux", needs_speed_check=True, min_glibc=(2, 43))         # unpacked size measured: 240 MB (v0.9.5; was 184 MB). v0.9.5's program asks for C library 2.43 (v0.9.0's: 2.38), measured from its own symbol versions: only the newest Linux distributions have it

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
LOAD_TIMEOUT_CONVERT_SECONDS = 300      # a take whose file the engine converts at start (More natural, Most natural): 19 to 21 s measured on a fast card with the file in the disk cache; a slow disk reads 7.3 GB first
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


def voice_key_of(voice: Optional[str]) -> str:
    """The voice's name as the log writes it (lower case, letters and digits), or "no name"."""
    from voice_clips import voice_key
    return voice_key(str(voice or "")) or "(no name)"


class EngineRefusedBusy(VoiceEngineError):
    """The engine said no to a line at once because it was still busy with another (its own answer was not the "server_busy" one, but its words or status say busy).
    The line is not lost: the caller tries again until the line's waiting time is used up (voice_service._speak_with_retries)."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(status, message)


_BUSY_WORDS = ("busy", "in use", "already running", "already processing", "another request", "concurrent", "too many", "queue is full", "try again")
_MEMORY_WORDS = ("out of memory", "failed to allocate", "cudamalloc", "vram", "not enough memory")
_NOT_LOADED_WORDS = ("not loaded", "no model", "model not found", "unknown model", "not ready", "still loading")
_CLIP_WORDS = ("voice_ref", "reference", "clip", "decode")
REFUSAL_WORDS_MAX = 160


def explain_refusal(status: int, txt: str, line_text: str = "") -> tuple[str, str]:
    """(kind, one plain sentence) for an engine answer that said no to a line. kind: "busy" | "memory" | "not_loaded" | "clip" | "other" | "unreadable".
    The sentence says what the engine answered (its status and its own words, cut short, one line) and which of those it was. The line of speech itself is never in it:
    if the engine echoed it back, it is cut out."""
    words = ""
    try:
        data = json.loads(txt)
        err = data.get("error", data) if isinstance(data, dict) else data
        words = err.get("message") or err.get("detail") or "" if isinstance(err, dict) else err if isinstance(err, str) else ""
        if not isinstance(words, str):
            words = ""
    except (ValueError, TypeError, AttributeError):
        words = txt if isinstance(txt, str) else ""
    words = " ".join(str(words).split())
    if line_text and len(line_text.strip()) >= 4:
        words = words.replace(line_text.strip(), "…")
    if len(words) > REFUSAL_WORDS_MAX:
        words = words[:REFUSAL_WORDS_MAX].rstrip() + "…"
    low = f"{words} {txt[:300] if isinstance(txt, str) else ''}".lower()
    if status == 429 or any(w in low for w in _BUSY_WORDS):
        kind, lead = "busy", "The voice was busy with another line"
    elif any(w in low for w in _MEMORY_WORDS):
        kind, lead = "memory", "The voice ran out of memory"
    elif any(w in low for w in _NOT_LOADED_WORDS):
        kind, lead = "not_loaded", "The voice isn't loaded yet"
    elif any(w in low for w in _CLIP_WORDS):
        kind, lead = "clip", "The voice refused the voice clip"
    elif not words:
        return "unreadable", f"The voice refused that line and its answer couldn't be read (it answered {status})."
    else:
        kind, lead = "other", "The voice refused that line"
    said = f"it said: {words}" if words else "it gave no reason"
    return kind, f"{lead} (the voice program answered {status}; {said})."


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
    driver_notice: Optional[dict] = None # Windows NVIDIA whose driver reports a CUDA older than the engines need (local_models.old_driver_notice), else None


@dataclass
class Fit:
    verdict: str                         # "ok" | "warn" | "refuse"
    build: Optional[str] = None          # BUILDS key, when one fits
    reasons: list = field(default_factory=list)      # why it can't be installed (refuse)
    warnings: list = field(default_factory=list)     # install anyway, with the consequence spelled out
    need_disk_bytes: int = 0
    speed_check: bool = False
    flags: list = field(default_factory=list)        # what the warnings are about: "vram" | "ram" | "speed" (so the Voice tab can word a tight card its own way)
    need_vram_bytes: Optional[int] = None            # the graphics memory the voice (the way it runs) needs, and what is free now: for the card's own words
    free_vram_bytes: Optional[int] = None

    def to_json(self) -> dict:
        return {"verdict": self.verdict, "build": self.build, "reasons": self.reasons, "warnings": self.warnings,
                "needDiskBytes": self.need_disk_bytes, "speedCheck": self.speed_check, "flags": self.flags,
                "needVramBytes": self.need_vram_bytes, "freeVramBytes": self.free_vram_bytes}


def gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def gb_plain(n: float) -> str:
    """Gigabytes the way a sentence says them: a whole number without the ".0" ("8 GB"), else one decimal ("7.5 GB")."""
    v = round(n / 1024**3, 1)
    return f"{int(v)} GB" if v == int(v) else f"{v} GB"


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
    hw.driver_notice = old_driver_notice(hw.gpu) if hw.system == "windows" else None
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
        if hw.driver_notice and any("too old" in r for r in reasons):          # the driver's own number says it is too old: say it the one plain way every page does
            fit.reasons, fit.flags = [hw.driver_notice["sentence"]], ["driver"]
        return fit
    b = BUILDS[build]
    if b.min_glibc and hw.glibc is not None and hw.glibc < b.min_glibc:
        fit.reasons.append(f"This Linux is too old for Higgs voice (its system libraries are version {hw.glibc[0]}.{hw.glibc[1]}, but {b.min_glibc[0]}.{b.min_glibc[1]} or newer is needed). "
                           "Ubuntu 24.04 or newer works.")
        fit.verdict = "refuse"
        return fit
    fit.speed_check = b.needs_speed_check
    fit.need_disk_bytes = vm.file.size + sum(f.size for f in vm.extra_files()) + sum(f.size for f in b.files) + b.unpacked_bytes + DISK_MARGIN_BYTES
    if kept_fn is not None:               # a file already in the kept downloads is already there: only what is still missing needs room
        fit.need_disk_bytes -= kept_fn([vm.file] + list(vm.extra_files()) + list(b.files))
    # (downloads are kept, so the first install needs room for the downloads AND the unpacked engine; the model is linked, not copied)
    if hw.free_disk_bytes is not None and hw.free_disk_bytes < fit.need_disk_bytes:
        fit.reasons.append(f"There isn't enough room on that drive. {vm.name} needs about {gb(fit.need_disk_bytes)} free, but only {gb(hw.free_disk_bytes)} is free.")
        fit.verdict = "refuse"
        return fit
    if hw.driver_notice and b.backend == "cuda":          # the driver is new enough on paper to start the CUDA 12.4 build, but reports a CUDA older than it was made for: never run on one, so it is said, not hidden
        fit.warnings.append(hw.driver_notice["sentence"])
        fit.flags.append("driver")
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
# One log line per graphics card (2026-11-19): what support needs to see in ONE file to tell an old driver from anything else
# ---------------------------------------------------------------------------------------------------------------------------------
def _build_word(key: Optional[str]) -> str:
    """The engine build in the words the log uses: `cuda13.3`, `cuda12.4`, `cuda12.8`, `vulkan`, or `processor` when no graphics build can be used."""
    if not key:
        return "processor"
    return key.split("-", 1)[1] if "-" in key else key


def graphics_card_lines(cards: list, driver: Optional[str], cuda: Optional[str], system: str, device: str = "") -> list:
    """One short line per card: `Graphics card 1: NVIDIA GeForce RTX 5090, 32 GB, driver 581.xx, CUDA 13.5, Windows; engine build cuda13.3`, with `; driver too old for CUDA` when the
    old-driver notice applies. `cards` are dicts with `name`, `memory_mb` (or None) and `vendor`; `driver` and `cuda` are the texts the driver reported (None = unknown, said as `unknown`).
    `device` is a Courier device's name (the line then starts `[MSI] `). Only the name, memory, driver, CUDA and system are written: no serial number, id, user name, folder or address."""
    # Everything below may come from another device's report: no control characters (so no line break can start a second log line) and a short length for every part (security review 4.3.0, S3).
    device = clean_text(device, 40)
    driver = clean_text(driver, 24) or None
    cuda = clean_text(cuda, 12) or None
    system = clean_text(system, 16)
    tag = f"[{device}] " if device else ""
    os_word = {"windows": "Windows", "linux": "Linux"}.get(system.lower(), system.capitalize() or "unknown")
    if not cards:
        return [f"{tag}Graphics card: none found ({os_word})"]
    lines = []
    for n, c in enumerate(list(cards)[:8], 1):
        c = c if isinstance(c, dict) else {}
        name = clean_text(c.get("name") or "unknown", 80) or "unknown"
        mem = c.get("memory_mb")
        mem_text = ((f"{round(mem / 1024):d} GB" if mem >= 4096 else f"{mem / 1024:.1f} GB") if isinstance(mem, (int, float)) and mem > 0 else "memory unknown")
        vendor = clean_text(c.get("vendor"), 16)
        cuda_t = None
        try:
            cuda_t = tuple(int(x) for x in str(cuda).split(".")[:2]) if cuda else None
            driver_f = float(driver) if driver else None
        except ValueError:
            driver_f = None
        gpu = GpuInfo(name, mem if isinstance(mem, int) else None, vendor, cuda=cuda_t if vendor == "nvidia" else None)
        hw = Hardware(system=str(system or "").lower(), gpu=gpu, driver=driver_f if vendor == "nvidia" else None)
        try:
            build, _ = choose_build(hw)
        except Exception:
            build = None
        too_old = bool(vendor == "nvidia" and hw.system == "windows" and old_driver_notice(gpu))
        if vendor == "nvidia":
            facts = f"driver {driver or 'unknown'}, CUDA {cuda or 'unknown'}, {os_word}"
        else:
            facts = f"{os_word}"
        lines.append(f"{tag}Graphics card {n}: {name}, {mem_text}, {facts}; engine build {_build_word(build)}" + ("; driver too old for CUDA" if too_old else ""))
    return lines


def _cuda_text(t) -> Optional[str]:
    return "%d.%d" % (t[0], t[1]) if t else None


def log_this_pc_graphics(log, folder: Optional[Path] = None) -> list:
    """At start: write the line(s) for this device's cards to `log` (a logging.Logger or any `.info`) and return them. Never raises: a card or value that can't be read says `unknown`."""
    try:
        hw = detect_hardware(Path(folder) if folder else Path("."))
        cards = []
        try:
            cards = [{"name": c.name, "memory_mb": c.memory_mb, "vendor": c.vendor} for c in gpu_choice.cached_cards()]
        except Exception:
            cards = []
        if not cards and hw.gpu:
            cards = [{"name": hw.gpu.name, "memory_mb": hw.gpu.memory_mb, "vendor": hw.gpu.vendor}]
        row = _nvidia_query("driver_version,memory.free")          # (the same query the hardware read makes; the driver's own text, "581.80", not a number)
        driver = row[0] if row else None
        lines = graphics_card_lines(cards, driver, _cuda_text(hw.gpu.cuda) if hw.gpu else None, hw.system)
    except Exception as e:
        lines = [f"Graphics card: could not be read ({e.__class__.__name__})"]
    for line in lines:
        log.info(line)
    return lines


class DeviceGraphicsLog:
    """One line per card for each Courier device, written when the device first reports and again only if what it reports about its cards changed (never on every poll)."""

    def __init__(self, log):
        self.log = log
        self._last: dict = {}

    def on_report(self, remote) -> list:
        try:
            rep = remote.report or {}
            courier = rep.get("courier") if isinstance(rep.get("courier"), dict) else {}
            listed = rep.get("gpus")
            if listed and (not isinstance(listed, list) or not all(isinstance(g, dict) for g in listed)):
                raise ValueError("the card list is not a list of cards")          # (said as "could not be read", never as "none found")
            rows = (listed or [])[:8]
            first = rows[0] if rows else {}
            cards = [{"name": g.get("name"), "memory_mb": g.get("memory_total_mb"), "vendor": g.get("vendor") or gpu_choice.vendor_of(str(g.get("name") or ""))} for g in rows]
            system = {"win32": "windows", "linux": "linux"}.get(str(courier.get("platform") or ""), clean_text(courier.get("platform"), 16))
            lines = graphics_card_lines(cards, first.get("driver"), first.get("cuda"), system, device=str(remote.label or remote.id))
        except Exception as e:
            lines = [f"[{clean_text(getattr(remote, 'label', None) or getattr(remote, 'id', '?'), 40)}] Graphics card: could not be read ({e.__class__.__name__})"]
        key = getattr(remote, "id", "")
        if self._last.get(key) == lines:
            return []
        self._last[key] = lines
        for line in lines:
            self.log.info(line)
        return lines


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
    take: str = ""                       # step "take" only: which way of running (Take.key) the file being fetched is for
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    meter: Any = field(default_factory=download_meter.Meter, repr=False)      # the speed really measured: the time left comes from it

    def to_json(self) -> dict:
        return {"state": self.state, "step": self.step, "done": self.done, "total": self.total, "error": self.error, "badFile": self.bad_file, "reason": self.reason, "take": self.take,
                "percent": int(self.done * 100 / self.total) if self.total else 0,
                "left": self.meter.text(self.done, self.total) if self.state == "running" and self.step != "warmup" else ""}


STEP_LABELS = {"engine": "Voice engine", "model": "Voice model", "warmup": "First warm-up", "take": "Voice model"}


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
        self._own_vram_fn: Optional[Callable[[], Optional[int]]] = None    # tests: the bytes the running voice holds (None = read it from the device)
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
        self.after_take_start: Optional[Callable[[], None]] = None       # called once the voice is running again after a change of "How it should sound" (the main device uses it to prepare the saved clips again: a restart forgets them)
        self.take_key = ""                       # how the chosen voice runs ("How it should sound"; Breeze only: quickest | standard | more | most); "" for a voice with only one way
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
        vm = self.catalog[self.model_key]
        if vm.takes and self.take_key != vm.default_take:
            vm = vm.with_take(self.take_key)            # the settings and memory figure of the way it runs now (the default way is the row itself)
        return replace(vm, file=self.model)

    @property
    def voice_name(self) -> str:
        return self.vmodel.name

    def _apply_model(self, key: str) -> None:
        self.model_key = key
        vm = self.catalog[key]
        self.take_key = self._saved_take(vm)
        t = vm.take(self.take_key)
        self.model = t.file if t is not None and self.take_key != vm.default_take else vm.file

    def _saved_take(self, vm: VoiceModel) -> str:
        """The way this device runs that voice (kept per device in the state file); the default way when nothing was chosen or the saved word is not one MultiProxy knows."""
        if not vm.takes:
            return ""
        saved = (self._read_state().get("takes") or {}).get(vm.key)
        return saved if vm.take(saved) is not None else vm.default_take

    def installed_models(self) -> list:
        """Keys of the voice models whose file is on this PC with the engine (more than one can be installed; one is chosen)."""
        d = self._read_state()
        if not (d.get("installed") and self.find_exe() is not None):
            return []
        return [k for k, vm in self.catalog.items() if any((self.models_dir / f.name).is_file() for f in (vm.model_files() + ((self.model,) if k == self.model_key else ())))]

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

    def _own_vram_bytes(self) -> int:
        """The graphics memory this device's own running voice program holds right now; 0 when it is not running or the figure cannot be read (never a made-up number)."""
        try:
            if self._own_vram_fn is not None:
                return max(0, int(self._own_vram_fn() or 0))
            proc = self.proc
            if not proc or proc.poll() is not None or self.state not in ("starting", "getting_ready", "ready", "too_slow"):
                return 0
            if sys.platform == "win32":
                import gpu_counters
                mb = (gpu_counters.read_pid(proc.pid) or {}).get("dedicated_mb")
            else:
                import gpu_monitor
                out = gpu_monitor._run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"])
                mb = gpu_monitor.parse_nvidia_compute_apps(out).get(proc.pid) if out else None
            return int(mb) * 1024 * 1024 if mb else 0
        except Exception:
            return 0

    def model_for(self, key: Optional[str] = None) -> VoiceModel:
        """A voice model by key (None: the chosen one). Raises a plain 400 for a key MultiProxy doesn't know."""
        if key in (None, "", self.model_key):
            return self.vmodel
        if key not in self.catalog:
            raise VoiceEngineError(400, "MultiProxy doesn't recognize that voice.")
        return self.catalog[key]

    def fit_check(self, key: Optional[str] = None, take: Optional[str] = None) -> Fit:
        vm = self.model_for(key)
        if take:
            base = self.catalog[vm.key]
            if base.take(take) is None:
                raise VoiceEngineError(400, "MultiProxy doesn't recognize that choice.")
            vm = base.with_take(take)                 # the voice run that way: its file, its memory figure
        fit = assess(self.hardware(), vm, lambda specs: sum(s.size for s in specs if self.store.has(s)))
        if fit.build and fit.build not in self.builds:
            fit.build = next(iter(self.builds), None)
        return fit

    # ---- how it should sound (Breeze: Quickest, Standard, More natural, Most natural) ----
    def _take_or_400(self, key: str) -> tuple:
        vm = self.catalog[self.model_key]
        t = vm.take(key)
        if t is None:
            raise VoiceEngineError(400, "MultiProxy doesn't recognize that choice.")
        return vm, t

    def takes_info(self) -> list:
        """The choices for the card: what each is, how it reads, what it still needs to download (0 when its file is already on this device), and which one is in use. [] for a voice with one way."""
        vm = self.catalog[self.model_key]
        out = []
        for t in vm.takes:
            f = vm.with_take(t.key).file
            on_disk = (self.models_dir / f.name).is_file()
            out.append({"key": t.key, "label": t.label, "oneLine": t.one_line, "shortLine": t.short_line, "fifteenWords": t.fifteen_words, "memory": t.memory_text,
                        "downloadBytes": f.size, "needsDownloadBytes": 0 if on_disk else f.size, "onDisk": on_disk, "converts": t.converts, "chosen": t.key == self.take_key})
        return out

    def take_fit(self, key: str) -> Fit:
        """Can this device run the voice the way `key` says, right now: the graphics memory against that way's own figure (a warning, never a refusal) and the room for its file if it
        still has to be downloaded (a refusal)."""
        vm, t = self._take_or_400(key)
        row = vm.with_take(key)
        hw = self.hardware()
        held = self._own_vram_bytes()
        if held and hw.free_vram_bytes is not None:
            hw.free_vram_bytes += held          # this voice's own memory is free again once it is stopped for the switch, so it counts as room
        fit = Fit(verdict="ok")
        on_disk = (self.models_dir / row.file.name).is_file()
        need = 0 if on_disk or self.store.has(row.file) else row.file.size
        fit.need_disk_bytes = need + (DISK_MARGIN_BYTES if need else 0)
        if need and hw.free_disk_bytes is not None and hw.free_disk_bytes < fit.need_disk_bytes:
            fit.reasons.append(f"There isn't enough room on that drive. {vm.name} needs about {gb(fit.need_disk_bytes)} free, but only {gb(hw.free_disk_bytes)} is free.")
            fit.verdict = "refuse"
            return fit
        build = self.builds.get(self._read_state().get("build"))
        figure = row.vram_vulkan_bytes if build is not None and build.backend == "vulkan" else row.vram_bytes
        fit.need_vram_bytes, fit.free_vram_bytes = figure, hw.free_vram_bytes
        if hw.free_vram_bytes is not None and hw.free_vram_bytes < figure:
            fit.warnings.append(f"{vm.name} needs about {gb_plain(figure)}, but only {gb_plain(hw.free_vram_bytes)} of graphics memory is free. Choose another option or close other programs.")
            fit.flags.append("vram")
            fit.verdict = "warn"
        return fit

    def _set_take(self, t: Take) -> None:
        vm = self.catalog[self.model_key]
        saved = dict(self._read_state().get("takes") or {})
        saved[vm.key] = t.key
        self._write_state(takes=saved)
        self.take_key = t.key
        self.model = vm.with_take(t.key).file

    async def choose_take(self, key: str, source: Optional[Source] = None) -> None:
        """Choose how the chosen voice runs. A file that is already here is switched to at once (a running voice is stopped and started again); one that is not is downloaded first, with the
        voice still running as it is, and the switch (and the one restart) happens when the file is in place. A download that fails changes nothing: the old way stays chosen."""
        vm, t = self._take_or_400(key)
        if key == self.take_key:
            return
        if self.job and self.job.state == "running":
            raise VoiceEngineError(409, f"{self.voice_name} is installing. Wait for it to finish first.")
        if not self.installed():                                   # nothing to switch yet: remember the choice, the install fetches that file
            async with self._lock:
                self._set_take(t)
                self.state, self.detail, self.speed_check, self.job = ("stopped" if self.installed() else "not_installed"), "", None, None
            self.log(f"{vm.name} will run as {t.label}")
            return
        f = vm.with_take(key).file
        if (self.models_dir / f.name).is_file():
            await self._switch_take(t)
            return
        fit = self.take_fit(key)
        if fit.verdict == "refuse":
            raise VoiceEngineError(507, " ".join(fit.reasons))
        job = InstallJob(total=f.size, step="take", take=key)
        self.job = job
        job.task = asyncio.ensure_future(self._fetch_take(job, t, f, source or InternetSource()))

    async def _switch_take(self, t: Take) -> None:
        vm = self.catalog[self.model_key]
        was_running = bool(self.proc and self.proc.poll() is None and self.state in ("starting", "getting_ready", "ready", "too_slow"))
        async with self._lock:
            await self._stop_quietly(keep_state=True)
            if self._recorded() is not None:
                self.log(STILL_RUNNING)
                raise VoiceEngineError(409, STILL_RUNNING)
            self._set_take(t)
            self.state, self.detail, self.speed_check = ("stopped" if self.installed() else "not_installed"), "", None
        self.log(f"{vm.name} now runs as {t.label}")
        if was_running:
            await self.start()
            if self.state == "ready" and self.after_take_start is not None:
                try:
                    self.after_take_start()
                except Exception:
                    pass

    async def _fetch_take(self, job: InstallJob, t: Take, f: FileSpec, source: Source) -> None:
        def on_bytes(n: int) -> None:
            job.meter.note(n)
            job.done = max(0, job.done + n)

        try:
            self.log(f"Downloading the file for {t.label} ({gb(f.size)})")
            kept = await self.store.get(f, source, on_bytes)
            how = await asyncio.to_thread(link_or_copy, kept, self.models_dir / f.name)
            self.log(f"{t.label} file in place ({how})")
            job.state = "done"
            await self._switch_take(t)
        except asyncio.CancelledError:
            job.state = "cancelled"
            self.log("Download cancelled (what was downloaded is kept, so it carries on from there)")
            raise
        except LocalModelError as e:
            job.state, job.error = "error", (e.message if hasattr(e, "message") else str(e)).replace("Hit Download", "Hit Install")
            if "safety check" in job.error:
                job.bad_file = job.error.split(" didn't pass")[0]
            self.log(f"Download failed: {job.error}")
        except VoiceEngineError as e:
            job.state, job.error = "error", e.message
            self.log(f"Switching to {t.label} failed: {e.message}")
        except Exception as e:
            job.state, job.error = "error", f"Couldn't download the file for {t.label}. Check the log for details."
            job.reason = plain_install_reason(e)
            self.log(f"Download failed: {e.__class__.__name__}: {e}")

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
            for lic in self.vmodel.licence_files:          # the license text and notice go beside the model file
                kept_lic = await self.store.get(lic, source, on_bytes)
                await asyncio.to_thread(link_or_copy, kept_lic, self.models_dir / lic.name)
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
            job = self.job
            job.task.cancel()
            # (a task cancelled before it ever ran never reaches its own "cancelled" line: say it here, or the job would stay "running" and block every later action)
            job.task.add_done_callback(lambda _t: setattr(job, "state", "cancelled") if job.state == "running" else None)
            return True
        return False

    def _companion_size(self) -> int:
        return sum(f.size for f in self.vmodel.extra_files())          # the companion model and the license files: everything installed with the voice besides its model

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
        vm = self.catalog[self.model_key]
        t = vm.take(self.take_key)
        deadline = time.time() + (LOAD_TIMEOUT_CONVERT_SECONDS if t is not None and t.converts else LOAD_TIMEOUT_SECONDS)
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
        sent = ref_wav
        for attempt in (0, 1):
            ref_wav = self.clips.resolve(voice, sent)          # the player's own clip for that voice, else the first one SkyrimNet sent (voice_clips.py)
            words = await self._line_words(voice, ref_wav)
            try:
                return await self._speak(text, ref_wav, temperature=temperature, seed=seed, top_p=top_p, busy_timeout_ms=busy_timeout_ms, timeout=timeout,
                                         reference_text=words)
            except VoiceEngineError as e:
                if attempt or not self._retry_after_bad_clip(e, voice):
                    raise

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

    def _retry_after_bad_clip(self, e: VoiceEngineError, voice: Optional[str]) -> bool:
        """The engine said it couldn't read the WAV it was sent. True (and said in the log) when this voice has a clip here that is now worked out afresh, so the line is
        tried once more; False when there is nothing to clean (the line then fails with the engine's own words)."""
        if "failed to read wav" not in str(e.message).lower() or not self.clips.reclean(voice):
            return False
        self.log(f"The voice program couldn't read the clip for voice {voice_key_of(voice)}. The clip was worked out again as a plain WAV and the line is being tried once more.")
        return True

    def _refusal(self, status: int, txt: str, line_text: str = "") -> VoiceEngineError:
        """The engine said no to a line: one plain sentence that says why (its status, its own words cut short, and whether it was busy, out of memory, not loaded or
        refused the clip). It is the error the line ends with, so it is what the log and SkyrimNet both get. A busy answer is an EngineRefusedBusy: the line is tried again."""
        if "reference text" in txt.lower():
            self.log(f"The engine refused a line ({status}): needs the words spoken in the voice clip.")
            return NeedsWords()
        kind, sentence = explain_refusal(status, self.redact(txt[:400]), line_text)
        if kind == "busy":
            return EngineRefusedBusy(sentence)
        e = VoiceEngineError(502, sentence)
        e.kind = kind
        return e

    def _speech_body(self, text: str, ref_wav: Optional[bytes], temperature, seed, top_p, busy_timeout_ms, reference_text: str = "") -> dict:
        if ref_wav is not None and len(ref_wav) > REFERENCE_MAX_BYTES:
            raise VoiceEngineError(400, "That voice clip is too big for the voice engine.")
        vm = self.vmodel
        if ref_wav is None and vm.needs_clip:
            raise VoiceEngineError(400, f"{vm.name} needs a voice clip for every voice.")
        if ref_wav is not None and vm.needs_words and not reference_text.strip():
            raise NeedsWords()
        direction = direction_from_tags(text) if vm.directions else ""
        if not vm.uses_tags:
            text = _ANY_TAG.sub("", text)                       # only Higgs knows the <|emotion:fear|> tags: anywhere else they would be read out loud
        body: dict[str, Any] = {"model": vm.engine_id, "input": text}
        if direction:
            body["instruct"] = direction                       # Breeze: the tags turned into a spoken direction (engine v0.9.5 or newer; v0.9.0 ignored it)
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
        sent = ref_wav
        session = await self._http()
        yielded = False
        for attempt in (0, 1):
            ref_wav = self.clips.resolve(voice, sent)
            body = self._speech_body(text, ref_wav, temperature, seed, top_p, busy_timeout_ms, await self._line_words(voice, ref_wav))
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
                        raise self._refusal(r.status, txt, text)
                    yielded = True
                    yield r
                    return
            except asyncio.TimeoutError:
                raise VoiceEngineError(504, "The voice took too long to speak that line.")
            except aiohttp.ClientError as e:
                raise VoiceEngineError(503, f"Couldn't reach the voice ({e.__class__.__name__}).")
            except VoiceEngineError as e:
                if yielded or attempt or not self._retry_after_bad_clip(e, voice):
                    raise

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
                    raise self._refusal(r.status, txt, text)
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
            await asyncio.to_thread(kill_tree_sync, proc.pid)          # (off the page's loop, so the other engines stop at the same time)
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
        if self.catalog[self.model_key].takes:
            self._apply_model(self.model_key)                    # (everything was removed, so the way it runs goes back to the default one)
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
            still = {f.name for k in others for f in self.catalog[k].extra_files()}          # a file another installed voice also uses (the words model) stays
            for spec in vm.model_files() + tuple(f for f in vm.extra_files() if f.name not in still):
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
            "model": self.model_key, "modelName": self.vmodel.name, "installedModels": self.installed_models(), "take": self.take_key or None, "takes": self.takes_info(),
            "gpuMemoryMb": vram, "lastLineMs": round(self.last_line_ms) if self.last_line_ms else None,
            "lastLineRtf": round(self.last_rtf, 2) if self.last_rtf else None, "linesDone": self.lines_done, "linesWaiting": waiting,
            "speedCheck": self.speed_check, "gpuNote": self.gpu_note, "gpuCard": self.gpu_card, "install": self.job.to_json() if self.job else None,
            "installStep": STEP_LABELS.get(self.job.step) if self.job and self.job.state == "running" else None,
            "downloads": self.store.listing(),
        }
