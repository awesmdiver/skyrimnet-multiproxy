"""Your own voice clips (2026-10-02): a clip of the player's own for any voice, used instead of the one SkyrimNet sends.

Higgs copies a voice from a short reference clip. SkyrimNet uploads one for every line (a low-quality one, 24 kHz mono, named after the voice:
`femalenord.wav`). The clips folder lives on the device that runs Higgs, next to the engine (`<voice folder>/clips`), and is the same code on the main device and
on a Courier device (this file is shipped in the Courier package; nothing here imports MultiProxy):

    clips/                      the player's own clips: `femalenord.wav` is used for the voice SkyrimNet calls `femalenord` (case and file type ignored)
    clips/from-skyrimnet/       the FIRST clip SkyrimNet sent for each voice, kept so that voice stays the same from line to line
    clips/replaced/             a clip of the player's that was replaced or removed, moved here with a timestamp (never deleted)

What can be read: **WAV files** (any sample rate, mono or stereo, 8 to 32 bits per sample); that is what the standard library reads, so there is no extra program
to install. Any other kind of sound file in the folder is listed with "format not supported" and never reaches the engine. A file dropped into the folder is
found the next time anything looks (every line, every list), so no restart is needed. Clips never leave the player's own devices.
"""
import hashlib
import io
import json
import os
import re
import struct
import sys
import time
import wave
import array
from pathlib import Path
from typing import Callable, Optional

from voice_engine import REFERENCE_MAX_BYTES, VoiceEngineError

BAD_CLIP = "That voice clip's format isn't supported."
REFERENCE_MAX_SECONDS = 30            # longer clips are cut: the engine only needs a few seconds of a voice and encoding time grows with length
CLIP_MAX_BYTES = 24 * 1024 * 1024     # the biggest file taken as a clip (the engine gets at most 30 s of it)
SOUND_EXTENSIONS = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".wma", ".opus", ".aif", ".aiff", ".mp4", ".webm")
READABLE_EXTENSIONS = (".wav",)
FORMATS_SAY = "WAV files"             # what the screen says can be used

class ClipFormatError(VoiceEngineError):
    def __init__(self):
        super().__init__(400, BAD_CLIP)


_RATE_OUT = 24000
_PREPARED: dict = {}                  # sha-256 of a clip -> its prepared WAV (SkyrimNet quotes the same sample for every line of a voice: it is worked out once)
_PREPARED_KEEP = 24


def _riff_parts(data: bytes) -> Optional[tuple]:
    """(channels, bits, rate, is_float, raw sample bytes) from a RIFF/WAVE file the standard library refuses (floating-point samples, the 'extensible' header
    some programs write, a data size left at 0 or 0xFFFFFFFF by a program that streamed it). None when it is no such file."""
    if len(data) < 44 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        return None
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if tag == b"fmt " and size >= 16:
            fmt = struct.unpack("<HHIIHH", data[body:body + 16])
            if fmt[0] == 0xFFFE and size >= 26:
                fmt = (struct.unpack("<H", data[body + 24:body + 26])[0],) + fmt[1:]       # the real kind is the first two bytes of the sub-format
        elif tag == b"data":
            end = body + size if size not in (0, 0xFFFFFFFF) and body + size <= len(data) else len(data)
            raw = data[body:end]
            break
        pos = body + size + (size & 1)
    if not fmt or raw is None:
        return None
    kind, ch, rate, _, _, bits = fmt
    if kind not in (1, 3) or ch < 1 or bits not in (8, 16, 24, 32, 64) or (kind == 3 and bits not in (32, 64)):
        return None
    return ch, bits, rate, kind == 3, raw


def _read_wav(data: bytes) -> tuple:
    """(channels, bits, rate, is_float, raw sample bytes), reading at most REFERENCE_MAX_SECONDS of sound. Raises ClipFormatError."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if width in (1, 2, 3, 4) and ch >= 1 and rate >= 1:
                return ch, width * 8, rate, False, w.readframes(min(n, rate * REFERENCE_MAX_SECONDS))
    except (wave.Error, EOFError, struct.error):
        pass
    parts = _riff_parts(data)
    if parts is None:
        raise ClipFormatError()
    ch, bits, rate, is_float, raw = parts
    return ch, bits, rate, is_float, raw[: rate * ch * (bits // 8) * REFERENCE_MAX_SECONDS]


def _to_mono_16(ch: int, bits: int, is_float: bool, raw: bytes) -> list:
    """The samples as one channel of 16-bit numbers (several channels are averaged)."""
    width = bits // 8
    raw = raw[: len(raw) - len(raw) % (width * ch)]
    if is_float:
        arr = array.array("f" if bits == 32 else "d")
        arr.frombytes(raw)
        if sys.byteorder == "big":
            arr.byteswap()
        samples = [max(-32768, min(32767, int(v * 32767))) if v == v else 0 for v in arr]
    elif bits == 8:
        samples = [(b - 128) << 8 for b in raw]
    elif bits == 16:
        arr = array.array("h")
        arr.frombytes(raw)
        if sys.byteorder == "big":
            arr.byteswap()
        samples = list(arr)
    elif bits == 24:
        samples = [int.from_bytes(raw[i:i + 3], "little", signed=True) >> 8 for i in range(0, len(raw), 3)]
    else:
        arr = array.array("i")
        arr.frombytes(raw)
        if sys.byteorder == "big":
            arr.byteswap()
        samples = [v >> 16 for v in arr]
    if ch > 1:
        samples = [sum(samples[i:i + ch]) // ch for i in range(0, len(samples) - len(samples) % ch, ch)]
    return samples


def _resample(samples: list, rate: int, target: int = _RATE_OUT) -> list:
    """To 24 kHz. Coming down from a higher rate each new sample is the average of the old ones it covers (so the high sounds that would turn into noise are smoothed
    away); going up from a lower rate (SkyrimNet's 16 kHz) it is the straight line between the two old ones either side."""
    if rate == target or not samples:
        return samples
    ratio = rate / target
    count = int(len(samples) / ratio)
    if ratio > 1:
        total = [0]
        for v in samples:
            total.append(total[-1] + v)
        out = []
        for k in range(count):
            lo, hi = int(k * ratio), max(int(k * ratio) + 1, min(len(samples), int((k + 1) * ratio)))
            out.append((total[hi] - total[lo]) // (hi - lo))
        return out
    out = []
    last = len(samples) - 1
    for k in range(count):
        pos = k * ratio
        i0 = int(pos)
        i1 = min(i0 + 1, last)
        frac = pos - i0
        out.append(int(samples[i0] * (1 - frac) + samples[i1] * frac))
    return out


def prepare_reference(data: bytes) -> bytes:
    """A WAV the engine accepts: mono, 24 kHz, 16-bit, at most REFERENCE_MAX_SECONDS and 5 MiB. A file that is already so is returned as it is.
    Taken in: any sample rate from 4 kHz up (SkyrimNet's 16 kHz, 22.05, 24, 44.1 and 48 kHz), mono or any number of channels, 8, 16, 24, 32-bit whole-number or 32/64-bit
    floating-point samples. Anything that is not a WAV file raises ClipFormatError (the caller decides what to do about it). Done once per clip (remembered by checksum)."""
    key = hashlib.sha256(data).digest()
    if key in _PREPARED:
        return _PREPARED[key]
    out = _prepare(data)
    if len(_PREPARED) >= _PREPARED_KEEP:
        _PREPARED.pop(next(iter(_PREPARED)))
    _PREPARED[key] = out
    return out


def _prepare(data: bytes) -> bytes:
    ch, bits, rate, is_float, raw = _read_wav(data)
    if rate < 4000:
        raise ClipFormatError()
    if ch == 1 and bits == 16 and not is_float and rate == _RATE_OUT and len(data) <= 2 * _RATE_OUT * REFERENCE_MAX_SECONDS + 1024 and len(data) <= REFERENCE_MAX_BYTES:
        try:
            with wave.open(io.BytesIO(data), "rb"):
                return data                                       # already what the engine wants, and a file the standard library reads
        except (wave.Error, EOFError, struct.error):
            pass
    samples = _resample(_to_mono_16(ch, bits, is_float, raw), rate)[: _RATE_OUT * REFERENCE_MAX_SECONDS]
    if not samples:
        raise ClipFormatError()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE_OUT)
        w.writeframes(struct.pack(f"<{len(samples)}h", *[max(-32768, min(32767, v)) for v in samples]))
    out = buf.getvalue()
    if len(out) > REFERENCE_MAX_BYTES:
        raise ClipFormatError()
    return out


ASR_RATE = 16000                      # the transcription model's own sample rate (the engine's docs use 16 kHz WAV for every example)


def asr_clip(data: bytes) -> bytes:
    """The clip as the transcription model wants it: mono, 16 kHz, 16-bit WAV (the same readable-WAV rules as `prepare_reference`). Raises ClipFormatError."""
    ch, bits, rate, is_float, raw = _read_wav(data)
    samples = _resample(_to_mono_16(ch, bits, is_float, raw), rate, ASR_RATE)
    if not samples:
        raise ClipFormatError()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ASR_RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *[max(-32768, min(32767, v)) for v in samples]))
    return buf.getvalue()


def _is_big_endian_machine() -> bool:
    return struct.pack("=h", 1) == struct.pack(">h", 1)


def voice_key(name: str) -> str:
    """The name a voice goes by: the file name without its type, lower case, anything odd turned into `_` (the same turn SkyrimNet's upload gets when it is
    saved), so `FemaleNord.WAV`, `femalenord.mp3` and the upload `femalenord.wav` are all the voice `femalenord`."""
    stem = Path(str(name or "").replace("\\", "/")).name
    stem = stem.rsplit(".", 1)[0] if "." in stem and stem.rsplit(".", 1)[1].lower() in tuple(e[1:] for e in SOUND_EXTENSIONS) else stem
    return re.sub(r"[^a-z0-9._-]", "_", stem.lower())[:80].strip(" ._") or ""


def seconds_of(data: bytes) -> Optional[float]:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError, struct.error):
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class ClipStore:
    """The clips folder, read fresh every time (a dropped-in file counts at once). `folder_fn` gives the `clips` folder."""

    def __init__(self, folder_fn: Callable[[], Path], log: Callable[[str], None] = lambda m: None):
        self._folder_fn = folder_fn
        self.log = log
        self._checked: dict = {}           # (path, mtime_ns, size) -> (prepared bytes or None, seconds or None)
        self._told: set = set()            # a clip that can't be used is mentioned in the log once
        self._failed: dict = {}            # (voice, clip checksum) -> when its words last couldn't be worked out

    # ---- places ----
    @property
    def folder(self) -> Path:
        return Path(self._folder_fn())

    @property
    def kept_folder(self) -> Path:
        return self.folder / "from-skyrimnet"

    @property
    def replaced_folder(self) -> Path:
        return self.folder / "replaced"

    # ---- what is there ----
    def _yours(self) -> dict:
        """key -> the player's file (the newest when two files name the same voice). Only sound files, only in the folder itself."""
        found: dict = {}
        try:
            entries = [p for p in self.folder.iterdir() if p.is_file() and p.suffix.lower() in SOUND_EXTENSIONS and not p.name.startswith(".")]
        except OSError:
            return found
        for p in entries:
            key = voice_key(p.name)
            if not key:
                continue
            try:
                if key not in found or p.stat().st_mtime > found[key].stat().st_mtime:
                    found[key] = p
            except OSError:
                continue
        return found

    def _kept(self) -> dict:
        try:
            return {p.stem: p for p in self.kept_folder.iterdir() if p.is_file() and p.suffix == ".wav"}
        except OSError:
            return {}

    def _check(self, path: Path) -> tuple:
        """(prepared WAV the engine accepts or None, seconds or None) for a clip file. Cached until the file changes."""
        try:
            st = path.stat()
        except OSError:
            return None, None
        sig = (str(path), st.st_mtime_ns, st.st_size)
        hit = self._checked.get(sig)
        if hit is not None:
            return hit
        prepared, seconds = None, None
        if path.suffix.lower() in READABLE_EXTENSIONS and st.st_size <= CLIP_MAX_BYTES:
            try:
                raw = path.read_bytes()
                prepared, seconds = prepare_reference(raw), seconds_of(raw)
            except (ClipFormatError, OSError):
                prepared = None
        if len(self._checked) > 200:
            self._checked.clear()
        self._checked[sig] = (prepared, seconds)
        return prepared, seconds

    # ---- using them (called for every line) ----
    def resolve(self, voice: Optional[str], upload: Optional[bytes]) -> Optional[bytes]:
        """The reference clip to speak with. The player's clip for that voice when it can be used; otherwise the first clip SkyrimNet sent for the voice
        (kept the first time, so the voice stays the same). Without a voice name, or without anything kept or sent, whatever came."""
        key = voice_key(voice or "")
        if not key:
            return upload
        mine = self._yours().get(key)
        if mine is not None:
            prepared, _ = self._check(mine)
            if prepared is not None:
                self._keep_first(key, upload)
                return prepared
            if (key, mine.name) not in self._told:
                self._told.add((key, mine.name))
                self.log(f"The voice clip {mine.name} can't be used (format not supported), so SkyrimNet's clip is used for {key}.")
        kept = self._kept().get(key)
        if kept is not None:
            try:
                return kept.read_bytes()
            except OSError:
                pass
        if upload is not None:
            self._keep_first(key, upload)
        return upload

    def _keep_first(self, key: str, upload: Optional[bytes]) -> None:
        if upload is None or key in self._kept():
            return
        try:
            _write_atomic(self.kept_folder / f"{key}.wav", upload)
        except OSError:
            pass

    # ---- the words spoken in a clip (Qwen3-TTS will not copy a voice without them) ----
    # `femalenord.txt` holds them. Typed by a person: just the words. Worked out by the voice itself (Qwen3-ASR): a first line `#auto sha256=<the clip's checksum>`
    # and then the words, so a changed clip's words are known to be stale and done again. A typed file always wins over an automatic one, and a typed file is never
    # overwritten by an automatic one.
    WORDS_MAX = 600
    AUTO_MARK = "#auto sha256="
    RETRY_AFTER_SECONDS = 600             # a clip whose words couldn't be worked out is not tried again at every line, only now and then

    @staticmethod
    def clip_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _words_files(self, key: str) -> list:
        """`femalenord.txt` beside the player's clip, else beside the kept SkyrimNet clip: the files that may hold the words that clip says (the first that has a usable one wins)."""
        return [p for p in (self.folder / f"{key}.txt", self.kept_folder / f"{key}.txt") if p.is_file()]

    def current_clip(self, key: str) -> Optional[bytes]:
        """The clip a line for this voice is spoken with right now (the player's, else the kept SkyrimNet one), as the engine gets it; None when there is none."""
        mine = self._yours().get(key)
        if mine is not None:
            prepared, _ = self._check(mine)
            if prepared is not None:
                return prepared
        kept = self._kept().get(key)
        try:
            return kept.read_bytes() if kept is not None else None
        except OSError:
            return None

    def _read_words(self, p: Path) -> tuple:
        """(words, sha256 the automatic words were made for or "", typed?) from one words file."""
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "", "", False
        first, _, rest = raw.partition("\n")
        if first.strip().startswith(self.AUTO_MARK):
            return " ".join(rest.split())[: self.WORDS_MAX], first.strip()[len(self.AUTO_MARK):].strip().lower(), False
        return " ".join(raw.split())[: self.WORDS_MAX], "", True

    def words_info(self, voice: Optional[str]) -> tuple:
        """(words, "typed" | "auto" | "") for the clip used for that voice. Typed words win; automatic words count only while they were made for this very clip."""
        key = voice_key(voice or "")
        if not key:
            return "", ""
        auto, clip = "", None
        for p in self._words_files(key):
            text, made_for, typed = self._read_words(p)
            if typed:
                if text:
                    return text, "typed"
                continue
            if clip is None:
                clip = self.current_clip(key)
            if not auto and text and clip is not None and made_for == self.clip_hash(clip):
                auto = text
        return (auto, "auto") if auto else ("", "")

    def words_for(self, voice: Optional[str]) -> str:
        """The words spoken in the clip used for that voice, or "" when nobody has written them down and none were worked out."""
        return self.words_info(voice)[0]

    def set_words(self, voice: str, text: str) -> None:
        key = voice_key(voice)
        text = " ".join((text or "").split())
        if not key:
            return
        if text:
            _write_atomic(self.folder / f"{key}.txt", text[: self.WORDS_MAX].encode("utf-8"))
        else:
            (self.folder / f"{key}.txt").unlink(missing_ok=True)

    def set_words_row(self, voice: str, text: str) -> Optional[dict]:
        """Type (or clear) the words for a voice and give back its row for the list."""
        self.set_words(voice, text)
        return next((r for r in self.listing() if r["voice"] == voice_key(voice)), None)

    def save_auto_words(self, voice: str, clip: bytes, text: str) -> bool:
        """Keep words the voice worked out for this clip, beside the clip (the player's when there is one, else the kept SkyrimNet one). Never replaces words a person typed."""
        key = voice_key(voice)
        text = " ".join((text or "").split())[: self.WORDS_MAX]
        if not key or not text:
            return False
        if self.words_info(key)[1] == "typed":
            return False
        where = self.folder if key in self._yours() else self.kept_folder
        _write_atomic(where / f"{key}.txt", f"{self.AUTO_MARK}{self.clip_hash(clip)}\n{text}\n".encode("utf-8"))
        self._failed.pop((key, self.clip_hash(clip)), None)
        return True

    def note_words_failed(self, voice: str, clip: bytes) -> None:
        self._failed[(voice_key(voice), self.clip_hash(clip))] = time.monotonic()

    def words_failed(self, voice: str, clip: Optional[bytes] = None) -> bool:
        """True while the last try to work out this clip's words failed (and is not yet due to be tried again)."""
        key = voice_key(voice)
        clip = clip if clip is not None else self.current_clip(key)
        if clip is None:
            return False
        at = self._failed.get((key, self.clip_hash(clip)))
        return at is not None and time.monotonic() - at < self.RETRY_AFTER_SECONDS

    # ---- the list on the Voice tab ----
    def listing(self) -> list:
        """Every voice SkyrimNet has asked for, plus every voice the player has a file for, as dicts the screen shows."""
        yours, kept = self._yours(), self._kept()
        out = []
        for key in sorted(set(yours) | set(kept)):
            words, words_from = self.words_info(key)
            row = {"voice": key, "source": "skyrimnet", "hasWords": bool(words), "words": words, "wordsFrom": words_from, "wordsFailed": not words and self.words_failed(key), "file": None, "seconds": None, "supported": True, "hasSkyrimNet": key in kept, "skyrimnetSeconds": None}
            if key in kept:
                try:
                    row["skyrimnetSeconds"] = seconds_of(kept[key].read_bytes())
                except OSError:
                    pass
                row["seconds"] = row["skyrimnetSeconds"]
            if key in yours:
                prepared, seconds = self._check(yours[key])
                row.update(source="yours", file=yours[key].name, supported=prepared is not None, seconds=seconds)
                if prepared is None:
                    row["seconds"] = None
            out.append(row)
        return out

    def audio(self, voice: str, which: str = "") -> Optional[bytes]:
        """The clip to play: `yours` (the player's, as the engine would get it), `skyrimnet` (the kept one), or the one in use."""
        key = voice_key(voice)
        mine = self._yours().get(key)
        if which != "skyrimnet" and mine is not None:
            prepared, _ = self._check(mine)
            if prepared is not None:
                return prepared
            if which == "yours":
                return None
        kept = self._kept().get(key)
        try:
            return kept.read_bytes() if kept is not None else None
        except OSError:
            return None

    # ---- changing them ----
    def _move_aside(self, path: Path) -> Path:
        self.replaced_folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.replaced_folder / f"{path.stem}.{stamp}{path.suffix}"
        n = 1
        while dest.exists():
            dest = self.replaced_folder / f"{path.stem}.{stamp}-{n}{path.suffix}"
            n += 1
        os.replace(path, dest)
        return dest

    def add(self, voice: str, data: bytes, words: Optional[str] = None) -> dict:
        """Use this clip (WAV bytes) for the voice. A clip of the player's that was already there is moved to `replaced` first. Refused with
        "format not supported" when the engine couldn't use it: nothing is changed then."""
        key = voice_key(voice)
        if not key:
            raise VoiceEngineError(400, "Which voice is this clip for?")
        if len(data) > CLIP_MAX_BYTES:
            raise VoiceEngineError(400, "That clip is too big. A clear recording of 8 to 15 seconds is plenty.")
        prepare_reference(data)                                   # raises ClipFormatError (BAD_CLIP) when it can't be used
        old = self._yours().get(key)
        if old is not None:
            self._move_aside(old)
        _write_atomic(self.folder / f"{key}.wav", data)
        if words is not None:                                     # a new clip says new words: the old words never stay behind for it
            self.set_words(key, words)
        self.log(f"A voice clip was added for {key}.")
        return next(r for r in self.listing() if r["voice"] == key)

    def remove(self, voice: str) -> bool:
        """Back to SkyrimNet's clip: the player's clip moves to `replaced`. False when there was none."""
        old = self._yours().get(voice_key(voice))
        if old is None:
            return False
        self._move_aside(old)
        (self.folder / f"{voice_key(voice)}.txt").unlink(missing_ok=True)
        self.log(f"The voice clip for {voice_key(voice)} was removed (kept in the replaced folder).")
        return True

    def enroll(self, voice: str, data: bytes, words: str = "") -> None:
        """A voice SkyrimNet's OpenAI-Compatible engine registered (`POST /v1/audio/voices`): its clip kept as the SkyrimNet clip for the voice (replacing an earlier one),
        with the words it said the clip speaks written beside it as typed words (so no Qwen3-ASR step is needed). Without words, an old words file for the old clip goes."""
        key = voice_key(voice)
        if not key:
            raise VoiceEngineError(400, "Which voice is this clip for?")
        prepare_reference(data)                                   # raises ClipFormatError (BAD_CLIP) when it can't be used
        words = " ".join((words or "").split())[: self.WORDS_MAX]
        _write_atomic(self.kept_folder / f"{key}.wav", data)
        if words:
            _write_atomic(self.kept_folder / f"{key}.txt", words.encode("utf-8"))
        else:
            (self.kept_folder / f"{key}.txt").unlink(missing_ok=True)

    def forget_enrolled(self, voice: str) -> None:
        key = voice_key(voice)
        if key:
            (self.kept_folder / f"{key}.wav").unlink(missing_ok=True)
            (self.kept_folder / f"{key}.txt").unlink(missing_ok=True)

    def describe(self, voice: Optional[str]) -> str:
        """Which clip a line for this voice is spoken with, in words for the log: the player's own file by name, or SkyrimNet's. Never the sound."""
        key = voice_key(voice or "")
        if not key:
            return "no named voice"
        mine = self._yours().get(key)
        if mine is not None and self._check(mine)[0] is not None:
            return f"your clip {mine.name}"
        if key in self._kept():
            return f"SkyrimNet's clip for {key}"
        return f"no clip for {key}"

    def keep_skyrimnet(self, voice: str, data: bytes) -> bool:
        """Put a kept SkyrimNet clip in place (moving Higgs to another device): only when this device has none for the voice yet."""
        key = voice_key(voice)
        if not key or key in self._kept():
            return False
        try:
            prepare_reference(data)
        except ClipFormatError:
            return False
        _write_atomic(self.kept_folder / f"{key}.wav", data)
        return True
