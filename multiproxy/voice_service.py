"""The piece that speaks SkyrimNet's voice language (2026-10-02).

SkyrimNet's Higgs setting talks to one **Endpoint** as if it were a Gradio app. What it does is written in its own log (observed, not copied
from anyone's code), one line at a time:

  1. POST   <endpoint>/gradio_api/upload                    multipart, the voice sample (a 24 kHz mono WAV it made itself) -> a list of server paths
  2. HEAD   <endpoint>/gradio_api/file=<path>               "does that sample still exist there?" (200 = yes, so it isn't uploaded again)
  3. POST   <endpoint>/gradio_api/call/generate_audio       JSON {"data": [...]}  -> {"event_id": "..."}
  4. GET    <endpoint>/gradio_api/call/generate_audio/<id>  waits, then a server-sent event: `event: complete` + `data: [{path, url, ...}]`
  5. GET    the `url` in that answer                        the finished WAV

This module answers exactly that, and passes each line to the engine manager. The order of the values inside "data" is learned from the capture
(`scripts/voice_capture_standin.py`); until then the values are recognised by their kind (the text is the one real string that isn't a file, the
voice sample is the file reference, the temperature is the number), so a reordering by SkyrimNet doesn't break it. A request it can't make sense of
gets the plain log line below and a clean error.

The same routes are served at the main address and under `/voice`, so SkyrimNet's Endpoint can be `http://127.0.0.1:<port>` (no second
port needed) or `http://127.0.0.1:<port>/voice`.

What these routes require (2026-10-06): **nothing but being asked from this device** (`local_guard.py` checks Host and Origin before any route sees a request). No key,
no `Authorization`, no custom header: a key that is sent is never looked at, so it can't be refused. A voice sample may be a WAV at any rate from 4 kHz up (SkyrimNet's
16 kHz, 22.05, 24, 44.1, 48 kHz), mono or more channels, 8/16/24/32-bit or floating-point; it is brought to the 24 kHz mono 16-bit the engine takes (`voice_clips.prepare_reference`).
A sample that can't be read at all no longer ends the line: it is spoken without it. Extra fields in a request are ignored. The OpenAI-Compatible engine of SkyrimNet (Beta 26) is answered too (observed in MultiProxy's own log, 2026-10-06): `POST /v1/audio/voices` registers a voice (multipart `title`, `ref_audio`, `ref_text`: the clip is kept as the voice's SkyrimNet clip with its words beside it, so no transcription step is needed; the same title and sound again changes nothing), `GET /v1/audio/voices` lists them, `DELETE /v1/audio/voices/<id>` forgets one, and `POST /v1/audio/speech` (`model`, `input`, `voice` = the id or the title, `response_format`) speaks with it. `model` has to be the running voice's key or name (or `higgs`, the old alias); another of our voices' names is refused plainly. Set `MULTIPROXY_VOICE_FIELDS=1` to have one line per voice request written to the log with the field NAMES only (never the sound, a key or the text).
"""
import asyncio
import base64
import hashlib
import json
import re
import secrets
import struct
import time
import wave
import io
import array
import inspect
import os
from dataclasses import dataclass, field
from email import message_from_bytes
from email.policy import HTTP
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

import field_log
import stt_capture
from voice_clips import BAD_CLIP, REFERENCE_MAX_SECONDS, ClipFormatError, prepare_reference, voice_key  # noqa: F401  (moved there: the Courier needs it too)
from voice_engine import EngineBusy, EngineTokenLimit, REFERENCE_MAX_BYTES, VoiceEngine, VoiceEngineError

# ---------------------------------------------------------------------------------------------------------------------------------
# What SkyrimNet is allowed to say, and the plain wording of every refusal
# ---------------------------------------------------------------------------------------------------------------------------------
UNRECOGNISED = "SkyrimNet sent a voice request MultiProxy doesn't recognize. Check for a MultiProxy update."
NOT_RUNNING = "Higgs voice isn't running. Press **Start** in MultiProxy."
STALE = "Higgs voice was too busy to speak this line in time."
COULD_NOT_FINISH = "Higgs voice couldn't finish this line. Try again."
GIVEN_UP = "SkyrimNet hung up before this line was ready."
UNREADABLE_SAMPLE = "SkyrimNet sent a voice clip MultiProxy couldn't read, so this line was spoken without it."

# A runaway line (a repetition loop) is cut. The rule, from SkyrimNet's real evening of play (494 lines, 0.6 to 14 s each; the log of the
# director's laptop): speech came out at a median of 0.40 s per word (10th to 90th percentile 0.30 to 0.56; the worst were one-word shouts and
# laughs at up to ~1 s). So the expected length is 0.40 s per spoken word (the tags aren't words), a little longer with slow-speech tags and
# for pauses and sound effects; the cap is twice that plus 3 s, never below 4 s. Measured against all 494 real lines no normal line came near
# it (the closest, a one-word laugh, was under 3 s against a 4 s floor), while the one 72.8 s loop (a 17-word line, expected ~6.8 s) is cut to ~16 s.
SECONDS_PER_WORD = 0.40
CAP_FACTOR, CAP_EXTRA_SECONDS, CAP_MIN_SECONDS = 2.0, 3.0, 4.0
FADE_SECONDS = 0.12
PING_CLIP_SECONDS = 0.5
RANDOM_SEED = 4294967295                # SkyrimNet's "random" (0xFFFFFFFF): not a seed to pass on

# The engine takes lines one at a time and nothing runs in parallel: N lines at once take N times one line (measured: 4 at once on CUDA 1.72 s,
# 16 short lines 6.9 s for the last). So one at a time is the number that the measurements support; more would only make each one slower.
ENGINE_PARALLELISM = 1

# A line that has waited longer than this for the engine's turn gets an immediate clean error instead of arriving late (SkyrimNet would be
# playing it after the moment has passed, and its own Voice Fallback can take over at once). SkyrimNet's Request Timeout is 30 s (Higgs.yaml
# `timeout: 30`), and it retries once. The longest line measured took 1.4 s on a top-end card (about 12 s of speech); a slower card, a long
# line and the time to send the audio back need room, so: 30 s minus ~10 s for the line itself on a slow card and the return trip = 20 s.
STALE_AFTER_SECONDS = 20.0

# Uploaded voice samples and finished lines are kept just long enough for SkyrimNet to use them (it re-checks a sample with a HEAD request
# before reusing it, so samples stay a good while); old ones are tidied away.
HANGUP_POLL_SECONDS = 0.5            # how often a waiting voice request checks that SkyrimNet is still there
UPLOAD_KEEP = 300
RESULT_KEEP = 40
UPLOAD_MAX_BYTES = 24 * 1024 * 1024
ENROLL_MAX_BYTES = 8 * 1024 * 1024         # a voice clip SkyrimNet registers is a few hundred kilobytes; the engine itself takes at most 5 MiB of one
ENROLL_TOO_BIG = "That voice clip is too big. A clip of a few seconds is plenty."
ENROLL_NO_CLIP = "SkyrimNet registered a custom voice without a voice clip to copy."
WRONG_VOICE = "{asked} isn't the voice MultiProxy is running ({running}). Set SkyrimNet's Model to {key}, or choose that voice in MultiProxy."

# The model's own 43 control tokens (from its model card; also the exact list in SkyrimNet's Higgs.yaml `allowed_audio_tags`).
EMOTIONS = ("affection", "amusement", "anger", "arousal", "awe", "bitterness", "confusion", "contemplation", "contentment", "determination", "disgust",
            "elation", "enthusiasm", "fear", "helplessness", "longing", "pride", "relief", "sadness", "shame", "surprise")
STYLES = ("singing", "shouting", "whispering")
PROSODY = ("speed_very_slow", "speed_slow", "speed_fast", "speed_very_fast", "pitch_low", "pitch_high", "expressive_high", "expressive_low", "pause", "long_pause")
# Each sound-effect tag needs its written sound right after it, or the model reads the tag badly (measured/model card).
SFX = {"cough": "Ahem", "laughter": "Haha", "crying": "Hmm", "screaming": "Ah", "burping": "Burp", "humming": "Hmm", "sigh": "Uh", "sniff": "Sniff", "sneeze": "Achoo"}
ALLOWED_TAGS = {f"{kind}:{name}" for kind, names in (("emotion", EMOTIONS), ("style", STYLES), ("prosody", PROSODY), ("sfx", tuple(SFX))) for name in names}

_NATIVE_TAG = re.compile(r"<\|\s*([a-z]+)\s*:\s*([a-z_]+)\s*\|>", re.I)      # what SkyrimNet sends today: <|emotion:fear|>
_BRACKET_TAG = re.compile(r"\[\s*(emotion|prosody|style|sfx)\s*-\s*([a-z_]+)\s*\]", re.I)        # SkyrimNet's own spelling before it converts: [emotion-fear]
_ANY_PIPE_TAG = re.compile(r"<\|[^|>]*\|>")


def convert_tags(text: str) -> tuple:
    """(text for the engine, tags removed). Only the 43 tags the model knows pass: an unknown one would be read out loud, so it is removed. A
    sound-effect tag gets its written sound straight after it. SkyrimNet's bracket spelling is turned into the engine's spelling."""
    dropped: list = []

    def keep(m: "re.Match") -> str:
        key = f"{m.group(1).lower()}:{m.group(2).lower()}"
        if key in ALLOWED_TAGS:
            return f"<|{key}|>"
        dropped.append(m.group(0))
        return ""

    text = _NATIVE_TAG.sub(keep, text)
    text = _BRACKET_TAG.sub(keep, text)

    def leftover(m: "re.Match") -> str:             # anything else shaped like a tag (odd spelling, no colon) isn't the model's, so it isn't spoken
        if m.group(0)[2:-2] in ALLOWED_TAGS:
            return m.group(0)
        dropped.append(m.group(0))
        return ""

    text = _ANY_PIPE_TAG.sub(leftover, text)
    out, i = [], 0
    for m in re.finditer(r"<\|sfx:([a-z]+)\|>", text):
        out.append(text[i:m.end()])
        i = m.end()
        if not text[i:i + 1].isalnum():            # no written sound follows at once: add it (dropping spaces between the tag and the next word)
            rest = text[i:]
            stripped = rest.lstrip(" ")
            out.append(SFX[m.group(1)])
            if stripped[:1].isalnum():
                out.append(" ")
            i += len(rest) - len(stripped)
    out.append(text[i:])
    return re.sub(r"[ \t]{2,}", " ", "".join(out)).strip(), dropped


def expected_seconds(text: str) -> float:
    """About how long a line should take to say, from its words (the model's tags aren't words)."""
    tags = [(m.group(1).lower(), m.group(2).lower()) for m in _NATIVE_TAG.finditer(text)]
    plain = _ANY_PIPE_TAG.sub(" ", text)
    seconds = SECONDS_PER_WORD * len(re.findall(r"[A-Za-z0-9']+", plain))
    if ("prosody", "speed_very_slow") in tags:
        seconds *= 1.6
    elif ("prosody", "speed_slow") in tags:
        seconds *= 1.3
    return seconds + 0.7 * tags.count(("prosody", "pause")) + 1.5 * tags.count(("prosody", "long_pause")) + 0.6 * sum(1 for k, _ in tags if k == "sfx")


def cap_seconds(text: str) -> float:
    return max(CAP_MIN_SECONDS, CAP_FACTOR * expected_seconds(text) + CAP_EXTRA_SECONDS)


def wav_seconds(data: bytes) -> Optional[float]:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def cut_wav(data: bytes, seconds: float, fade: float = FADE_SECONDS) -> bytes:
    """The first `seconds` of a 16-bit WAV, with a short fade-out so the cut doesn't click."""
    with wave.open(io.BytesIO(data), "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(int(seconds * rate))
    return _pack_cut(ch, width, rate, frames, fade)


def wav_file_seconds(path) -> Optional[float]:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError, OSError):
        return None


def cut_wav_file(path, seconds: float, fade: float = FADE_SECONDS) -> None:
    """`cut_wav` for a file on disk (a line relayed from another device): only the part that is kept is ever read into memory."""
    with wave.open(str(path), "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(int(seconds * rate))
    out = _pack_cut(ch, width, rate, frames, fade)
    tmp = Path(str(path) + ".cut")
    tmp.write_bytes(out)
    os.replace(tmp, path)


def _pack_cut(ch: int, width: int, rate: int, frames: bytes, fade: float) -> bytes:
    if width == 2:
        n = len(frames) // 2
        samples = list(struct.unpack(f"<{n}h", frames[: n * 2]))
        fade_n = min(n // ch, int(fade * rate)) * ch
        for i in range(fade_n):
            k = n - fade_n + i
            samples[k] = int(samples[k] * (1 - i / max(1, fade_n)))
        frames = struct.pack(f"<{n}h", *samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(ch)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames)
    return buf.getvalue()


def quiet_wav(seconds: float = PING_CLIP_SECONDS, rate: int = 24000) -> bytes:
    """A short valid clip of silence (the answer to SkyrimNet's health check: no engine involved)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def strip_control_tags(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _ANY_PIPE_TAG.sub("", text)).strip()


def _fixed_seed(value) -> Optional[int]:
    """A seed to pass on, or None for "random" (SkyrimNet sends 4294967295 for that)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value >= RANDOM_SEED:
        return None
    return value


def is_health_check(parsed: dict) -> bool:
    """SkyrimNet's health check: the text `ping.` with a voice-sample entry that has no file (an empty path)."""
    return re.fullmatch(r"\s*ping\s*[.!]?\s*", parsed.get("text") or "", re.I) is not None and not parsed.get("ref")


# ---------------------------------------------------------------------------------------------------------------------------------
# Voice samples: SkyrimNet already uploads 24 kHz mono 16-bit WAV (it converts the game's files itself); anything else is brought to that or refused
# ---------------------------------------------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------------------------------------------
# The queue: arrival order, one at a time, nothing dropped silently, a stale line gets an immediate clean error
# ---------------------------------------------------------------------------------------------------------------------------------
class LineQueue:
    """First come, first served (asyncio's semaphore wakes waiters in order). `waiting` is how many lines are in the line for the engine."""

    def __init__(self, parallelism: int = ENGINE_PARALLELISM):
        self._sem = asyncio.Semaphore(parallelism)
        self.waiting = 0

    async def turn(self, stale_after: float, arrived: float) -> float:
        """Wait for this line's turn. Returns the seconds it waited, or raises EngineBusy (STALE) if it waited past `stale_after`."""
        self.waiting += 1
        try:
            remaining = stale_after - (time.time() - arrived)
            if remaining <= 0:
                raise EngineBusy(STALE)
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=remaining)
            except asyncio.TimeoutError:
                raise EngineBusy(STALE)
        finally:
            self.waiting -= 1
        return time.time() - arrived

    def release(self) -> None:
        self._sem.release()


def _line_args(fn) -> int:
    """How many arguments the `on_line` callback takes: 2 (seconds, worked), 4 (and the waiting and speaking times) or 6 (and the seconds of sound and the characters)."""
    try:
        n = len(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return 2
    return 6 if n >= 6 else 4 if n >= 4 else 2


@dataclass
class Job:
    event_id: str
    text: str
    arrived: float
    done: asyncio.Event = field(default_factory=asyncio.Event)
    result_path: Optional[str] = None
    error: str = ""
    size: int = 0
    route: str = ""                  # which of SkyrimNet's engines asked (for the log line)
    asked: Optional[str] = None      # the Model name SkyrimNet asked for (OpenAI-Compatible only)
    voice: Optional[str] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)      # the line being made (stopped when SkyrimNet hangs up on it)
    given_up: bool = False           # SkyrimNet hung up before it was ready: stopped, and not a fault of the voice
    waited: Optional[float] = None   # seconds the line stood in the queue before the engine took it (None: it never got its turn)
    spoke: Optional[float] = None    # seconds the engine spent on it (None: it never started, or failed before finishing)
    audio: Optional[float] = None    # seconds of sound the engine made for it (the engine's own figure, else the length of the file)
    chars: Optional[int] = None      # characters of the line it was given to speak


# ---------------------------------------------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------------------------------------------
_FILE_WORDS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")


def _looks_like_file_ref(v) -> Optional[str]:
    """The path inside a Gradio file reference (a bare string path, or {"path": ...}), else None."""
    if isinstance(v, dict) and isinstance(v.get("path"), str):
        return v["path"]
    if isinstance(v, str) and (v.lower().endswith(_FILE_WORDS) or "gradio_api/file=" in v):
        return v
    return None


# The order SkyrimNet 0.26.0 really sends (confirmed by a capture, 2026-10-02): a list of 30 slots, most of them empty (null). Used slots: 1 the text,
# 2 the language ("en-us"), 3 the voice sample (a file reference), 19 a decimal (4.5) and 27 a very large whole number that mean nothing here and are
# ignored, 20 top_p (0.8), 23 the temperature (0.800000011920929, a 32-bit 0.8), 26 the seed (-1 = random; the same as the 4294967295 a real
# session's log showed once it had been through a 32-bit field), 28 false. The real session's logged temperature and top_p are the values in
# slots 23 and 20; slot 19 is NOT the temperature, though it is the first decimal.
WIRE_MIN_SLOTS = 24
WIRE_TEXT, WIRE_LANGUAGE, WIRE_SAMPLE, WIRE_TOP_P, WIRE_TEMPERATURE, WIRE_SEED = 1, 2, 3, 20, 23, 26


def _slot(data, i):
    return data[i] if i < len(data) else None


def _parse_wire_layout(data: list) -> dict:
    ref = _looks_like_file_ref(data[WIRE_SAMPLE])
    t, p, lang, seed = _slot(data, WIRE_TEMPERATURE), _slot(data, WIRE_TOP_P), _slot(data, WIRE_LANGUAGE), _slot(data, WIRE_SEED)
    isnum = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    return {"text": data[WIRE_TEXT], "ref": ref, "temperature": float(t) if isnum(t) and 0 < t <= 2 else None,
            "top_p": float(p) if isnum(p) and 0 < p <= 1 else None, "seed": seed if isinstance(seed, int) and not isinstance(seed, bool) else None,
            "language": lang if isinstance(lang, str) and lang.strip() else None}


def parse_generate_data(data) -> dict:
    """Tell the values in SkyrimNet's "data" list apart by kind (a real session logged: text, the voice sample, language 'en-us', temperature 0.8,
    top_p 0.8, seed 4294967295). Returns {text, ref, temperature, top_p, seed, language}, or {} when there's no text to speak. The two decimals are
    told apart by their order (temperature first); a whole number is the seed; a short language code after the text is the language (read, not used)."""
    if not isinstance(data, list):
        return {}
    if len(data) >= WIRE_MIN_SLOTS and isinstance(data[WIRE_TEXT], str) and data[WIRE_TEXT].strip():
        return _parse_wire_layout(data)
    text, ref, temperature, top_p, seed, language = None, None, None, None, None, None
    for v in data:
        path = _looks_like_file_ref(v)
        if path is not None and ref is None:
            ref = path
        elif isinstance(v, str) and text is None and v.strip():
            text = v
        elif isinstance(v, str) and text is not None and language is None and re.fullmatch(r"[a-z]{2,3}([-_][a-z0-9]{2,8})?", v.strip(), re.I):
            language = v.strip()
        elif isinstance(v, float) and not isinstance(v, bool):
            if temperature is None and 0 < v <= 2:
                temperature = v
            elif top_p is None and 0 < v <= 1:
                top_p = v
        elif isinstance(v, int) and not isinstance(v, bool) and seed is None:
            seed = v
    return {"text": text, "ref": ref, "temperature": temperature, "top_p": top_p, "seed": seed, "language": language} if text else {}


_SPEECH_TEXT = ("input", "text", "prompt")
_SPEECH_REF = ("voice_ref", "reference_audio", "ref_audio", "speaker_audio", "voice_sample", "reference")
_SPEECH_VOICE = ("voice", "voice_id", "voice_name", "speaker")


def _bytes_of_sample(v) -> Optional[bytes]:
    """The sound in a request's reference field: {"type": "base64", "data": ...}, a base64 string or a `data:audio/...;base64,` address. Anything else: None."""
    if isinstance(v, dict):
        v = v.get("data") or v.get("audio") or v.get("base64")
    if not isinstance(v, str) or len(v) < 64:
        return None
    if v.startswith("data:") and "," in v:
        v = v.split(",", 1)[1]
    try:
        return base64.b64decode(v, validate=False) or None
    except Exception:
        return None


def parse_speech_body(body: dict) -> tuple:
    """(parsed line, reference sound or None, voice name or None) out of an OpenAI-style speech request. Every field is optional but the text; unknown ones are ignored."""
    text = next((body[k] for k in _SPEECH_TEXT if isinstance(body.get(k), str) and body[k].strip()), None)
    if text is None:
        return {}, None, None
    isnum = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    t, p, seed = body.get("temperature"), body.get("top_p"), body.get("seed")
    ref = next((b for b in (_bytes_of_sample(body.get(k)) for k in _SPEECH_REF) if b), None)
    voice = next((body[k] for k in _SPEECH_VOICE if isinstance(body.get(k), str) and body[k].strip()), None)
    parsed = {"text": text, "ref": None, "temperature": float(t) if isnum(t) and 0 < t <= 2 else None, "top_p": float(p) if isnum(p) and 0 < p <= 1 else None,
              "seed": seed if isinstance(seed, int) and not isinstance(seed, bool) else None, "language": None}
    return parsed, ref, voice


class VoiceService:
    def __init__(self, engine: VoiceEngine, work_dir_fn: Callable[[], Path], log: Callable[[str], None] = lambda m: None,
                 stale_after: float = STALE_AFTER_SECONDS):
        self.engine = engine
        self._work_dir_fn = work_dir_fn
        self.log = log
        self.stale_after = stale_after
        self.queue = LineQueue()
        self.jobs: dict = {}
        self.engine.waiting_fn = lambda: self.queue.waiting
        self.on_line: Optional[Callable[[float, bool], None]] = None      # told (seconds from arrival to finished, worked or not) for every real line (never a health check)
        self.unrecognised = 0
        self.health_checks = 0
        self.runaway_cuts = 0
        self.asked_other: Optional[dict] = None        # {asked, answered}: SkyrimNet's Model name was not the running voice (the voice row says so)
        self.enroll_hook: Optional[Callable] = None     # async (key, wav, words): keeps an enrolled clip where the voice runs when that is another device (voice_wiring.py)

    # ---- the voices SkyrimNet's OpenAI-Compatible engine registers ----
    @property
    def _enrolled_file(self) -> Path:
        return Path(self._work_dir_fn()) / "enrolled.json"

    def enrolled(self) -> dict:
        """{voice id: {title, sha, words}} (the registry kept beside the work files; never holds the sound itself)."""
        try:
            d = json.loads(self._enrolled_file.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_enrolled(self, d: dict) -> None:
        self._enrolled_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._enrolled_file.with_name("enrolled.json.part")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        os.replace(tmp, self._enrolled_file)

    async def enroll(self, title: str, data: bytes, words: str) -> str:
        """Register a voice: the clip is kept under the voice made from `title`, with its words. The same title with the same sound and words changes nothing; another
        sound for the same title replaces the old. Returns the voice id (what SkyrimNet then asks speech with). Raises VoiceEngineError with a plain sentence."""
        key = voice_key(title)
        if not key:
            raise VoiceEngineError(400, "SkyrimNet registered a voice without a name.")
        if not data:
            raise VoiceEngineError(400, ENROLL_NO_CLIP)
        if len(data) > ENROLL_MAX_BYTES:
            raise VoiceEngineError(413, ENROLL_TOO_BIG)
        prepare_reference(data)                                    # raises ClipFormatError (BAD_CLIP) for a sound the engine can't use
        words = " ".join((words or "").split())[: 600]
        sha = hashlib.sha256(data).hexdigest()
        reg = self.enrolled()
        old = reg.get(key) or {}
        if old.get("sha") == sha and old.get("words", "") == words:
            return key
        if self.enroll_hook is not None:
            await self.enroll_hook(key, data, words)
        else:
            clips = getattr(getattr(self.engine, "local", self.engine), "clips", None)
            if clips is not None:
                clips.enroll(key, data, words)
        reg[key] = {"title": title[:120], "sha": sha, "words": words}
        self._save_enrolled(reg)
        self.log(f"SkyrimNet registered the voice {key}" + (" (with its words)." if words else " (without its words)."))
        return key

    def forget(self, voice: str) -> bool:
        key = voice_key(voice)
        reg = self.enrolled()
        found = reg.pop(key, None) is not None
        if found:
            self._save_enrolled(reg)
            clips = getattr(getattr(self.engine, "local", self.engine), "clips", None)
            if clips is not None:
                clips.forget_enrolled(key)
        return found

    def wrong_voice(self, model) -> Optional[str]:
        """A plain sentence when a speech request's `model` names another of MultiProxy's voices than the one running; None when it fits (the running voice's key or name,
        `higgs` as the old alias, nothing at all, or a name that isn't one of ours: those are accepted, the running voice answers)."""
        if not isinstance(model, str) or not model.strip():
            return None
        from voice_engine import VOICE_MODELS
        norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
        st = self.engine.status()
        running = st.get("model") or "higgs"
        asked = norm(model)
        if asked in ("higgs", norm(running), norm(str(st.get("modelName") or ""))):
            return None
        for k, vm in VOICE_MODELS.items():
            if asked in (norm(k), norm(vm.name), norm(vm.label)) and k != running:
                return WRONG_VOICE.format(asked=vm.name, running=VOICE_MODELS.get(running, vm).name, key=running)
        return None

    # ---- files ----
    @property
    def uploads(self) -> Path:
        p = Path(self._work_dir_fn()) / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def results(self) -> Path:
        p = Path(self._work_dir_fn()) / "results"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _tidy(self, folder: Path, keep: int) -> None:
        try:
            files = sorted((p for p in folder.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
            for p in files[:-keep] if len(files) > keep else []:
                p.unlink(missing_ok=True)
        except OSError:
            pass

    def save_upload(self, filename: str, data: bytes) -> str:
        """Keep an uploaded voice sample; returns the server-side path SkyrimNet will quote back to us."""
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename or "voice.wav").name)[:80] or "voice.wav"
        digest = hashlib.sha256(data).hexdigest()[:16]
        dest = self.uploads / digest / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self._tidy(self.uploads, UPLOAD_KEEP)
        return f"{digest}/{safe}"

    def resolve(self, ref: str) -> Optional[Path]:
        """A path quoted back to us -> the file, only ever inside our own uploads/results folders (never an arbitrary path on disk)."""
        ref = ref.split("gradio_api/file=")[-1].replace("\\", "/")
        for base in (self.uploads, self.results):
            cand = (base / ref).resolve()
            if cand.is_file() and cand.is_relative_to(base.resolve()):
                return cand
        return None

    # ---- one line ----
    # ---- one plain log line for every line of speech (never the text, the sound, the words or a key) ----
    @staticmethod
    def _clean_name(text) -> str:
        return re.sub(r"[^A-Za-z0-9._ :/-]", "", str(text or ""))[:40].strip()

    def _answering(self) -> str:
        st = self.engine.status()
        name = st.get("modelName") or "Higgs voice"
        return f"{name} on {st['pc']}" if st.get("pc") else name

    def _running_names(self) -> set:
        from voice_engine import VOICE_MODELS
        norm = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())
        st = self.engine.status()
        running = st.get("model") or "higgs"
        names = {"higgs", norm(running), norm(st.get("modelName") or "")}
        vm = VOICE_MODELS.get(running)
        if vm is not None:
            names |= {norm(vm.key), norm(vm.name), norm(vm.label)}
        return names

    def note_asked(self, model) -> None:
        """What the voice row shows (once, quiet) when SkyrimNet's Model name is not the running voice: remembered until a request that fits comes."""
        asked = self._clean_name(model) if isinstance(model, str) else ""
        if asked and re.sub(r"[^a-z0-9]", "", asked.lower()) not in self._running_names():
            self.asked_other = {"asked": asked, "answered": self.engine.status().get("modelName") or "Higgs voice"}
        else:
            self.asked_other = None

    def _line_note(self, route: str, voice: Optional[str], asked: Optional[str], sent_ref: bool) -> str:
        """The middle of the log line: who was asked for what, with which clip and which words."""
        parts = []
        if asked:
            fits = re.sub(r"[^a-z0-9]", "", self._clean_name(asked).lower()) in self._running_names()
            parts.append(f"SkyrimNet asked for model {self._clean_name(asked)}" + ("" if fits else " (accepted as the running voice)"))
        clips = self._clips_here()
        if clips is not None:
            parts.append("clip: " + clips.describe(voice))
            info = clips.words_info(voice)[1]
            need = getattr(getattr(getattr(self.engine, "local", self.engine), "vmodel", None), "needs_words", False)
            parts.append("words: " + {"typed": "typed or given by SkyrimNet", "auto": "worked out by the voice"}.get(info, "none" if need else "not needed"))
        else:
            where = self.engine.status().get("pc") or "the other device"
            parts.append(f"clip and words: chosen on {where}" + (" (SkyrimNet's clip was sent along)" if sent_ref else ""))
        return "; ".join(parts)

    def log_line(self, route: str, voice: Optional[str], asked: Optional[str], seconds: float, error: str, audio_seconds: Optional[float], sent_ref: bool = False) -> None:
        try:
            note = self._line_note(route, voice, asked, sent_ref)
            if error:
                end = f"failed after {seconds:.1f} s: {str(error).rstrip('. ')}"
                head = f"{self._answering()} was asked"
            else:
                end = f"{seconds:.1f} s" + (f", {audio_seconds:.1f} s of speech" if audio_seconds is not None else "")
                head = f"{self._answering()} answered"
            self.log(f"Voice line ({route}): {head}; {note}; {end}.")
        except Exception:                                    # the log line is never a reason to fail a line
            pass

    def log_refused(self, route: str, asked: Optional[str], message: str) -> None:
        extra = f"; SkyrimNet asked for model {self._clean_name(asked)}" if asked else ""
        self.log(f"Voice line ({route}) refused{extra}: {str(message).rstrip('. ')}.")

    def _clips_here(self):
        eng = self.engine
        if getattr(eng, "location", "this") != "this":
            return None
        return getattr(getattr(eng, "local", eng), "clips", None)

    async def start_line(self, data) -> Job:
        parsed = parse_generate_data(data)
        if not parsed:
            self.unrecognised += 1
            self.log(UNRECOGNISED)
            raise VoiceEngineError(400, UNRECOGNISED)
        return await self.start_parsed(parsed)

    async def start_parsed(self, parsed: dict, ref_bytes: Optional[bytes] = None, voice: Optional[str] = None, route: str = "Higgs", asked: Optional[str] = None) -> Job:
        """A line already understood: {text, ref (a path we hold) or `ref_bytes` (a sample that came with the request), temperature, top_p, seed}."""
        if not self.engine.status()["ready"]:
            why = getattr(self.engine, "not_ready_message", lambda: None)()
            raise VoiceEngineError(503, why or NOT_RUNNING)          # (for another device: which device, and why)
        if is_health_check(parsed):
            return self._answer_health_check()
        if parsed.get("ref"):
            f = self.resolve(parsed["ref"])
            if f is None:
                raise VoiceEngineError(400, "SkyrimNet's voice clip for this line is missing. Try the line again.")
            voice = f.name                      # the voice is named by the file SkyrimNet uploaded: the player's own clip for it (voice_clips.py) replaces this one
            ref_bytes = f.read_bytes()
        if ref_bytes is not None:
            try:
                ref_bytes = prepare_reference(ref_bytes)
            except ClipFormatError:             # a sample that can't be read doesn't stop the line: it is spoken without it (the player's own clip, or the voice's default)
                self.log(UNREADABLE_SAMPLE)
                ref_bytes = None
        text, dropped = convert_tags(parsed["text"])
        if dropped:
            self.log(f"Removed {len(dropped)} tag(s) the voice model doesn't know: {', '.join(sorted(set(dropped)))}")
        job = Job(event_id=secrets.token_hex(16), text=text, arrived=time.time(), route=route, asked=asked, voice=voice)
        self.jobs[job.event_id] = job
        if len(self.jobs) > 500:
            for k in list(self.jobs)[:100]:
                self.jobs.pop(k, None)
        job.task = asyncio.ensure_future(self._run(job, ref_bytes, parsed["temperature"], parsed.get("top_p"), _fixed_seed(parsed.get("seed")), voice))
        return job

    def give_up(self, job: Job) -> bool:
        """SkyrimNet hung up on this line (Beta 28 cancels lines it no longer needs): stop making it, so the engine's turn goes to the next line at once. False when the line
        had already finished."""
        if job.done.is_set() or job.task is None or job.task.done():
            return False
        job.given_up = True
        job.task.cancel()
        return True

    async def wait_for_line(self, request, job: Job) -> bool:
        """Wait until `job` finishes. True when it did; False when the caller hung up first (the line is then stopped, see give_up)."""
        waiter = asyncio.ensure_future(job.done.wait())
        try:
            while True:
                done, _ = await asyncio.wait({waiter}, timeout=HANGUP_POLL_SECONDS)
                if done:
                    return True
                try:
                    gone = await request.is_disconnected()
                except Exception:
                    gone = False
                if gone:
                    self.give_up(job)
                    return False
        finally:
            waiter.cancel()

    def _answer_health_check(self) -> Job:
        """SkyrimNet's `ping.` (no voice-sample file): a short valid clip at once, the engine is not called. It is only answered while Higgs is
        ready (checked just before), so SkyrimNet's idea of whether Higgs is up stays true: a stopped Higgs gets the clean not-running error instead."""
        job = Job(event_id=secrets.token_hex(16), text="ping.", arrived=time.time())
        rel = f"{secrets.token_hex(8)}/tmp{secrets.token_hex(4)}.wav"
        dest = self.results / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        clip = quiet_wav()
        dest.write_bytes(clip)
        job.result_path, job.size = rel, len(clip)
        job.done.set()
        self.jobs[job.event_id] = job
        self.health_checks += 1
        return job

    async def _speak_with_retries(self, text: str, ref: Optional[bytes], temperature, top_p, seed, left_ms: int, voice: Optional[str] = None):
        """One line through the engine. If the engine reports it ran out of room before the end of the line: once more with a fresh random seed,
        then once more without the control tags, then a clean error. Each retry is logged once, plainly."""
        async def go(t, s):
            return await self.engine.synthesize(t, ref, temperature=temperature, top_p=top_p, seed=s, busy_timeout_ms=left_ms, timeout=60, voice=voice)
        try:
            return await go(text, seed), text
        except EngineTokenLimit:
            self.log("Higgs voice ran out of room before finishing a line; trying again with a different random start.")
        try:
            return await go(text, secrets.randbelow(2 ** 31)), text
        except EngineTokenLimit:
            stripped = strip_control_tags(text)
            if not stripped or stripped == text:
                raise VoiceEngineError(502, COULD_NOT_FINISH)
            self.log("Higgs voice ran out of room again; trying once more without the style tags.")
        try:
            return await go(stripped, secrets.randbelow(2 ** 31)), stripped
        except EngineTokenLimit:
            self.log("Higgs voice couldn't finish a line even without its style tags.")
            raise VoiceEngineError(502, COULD_NOT_FINISH)

    async def _run(self, job: Job, ref: Optional[bytes], temperature: Optional[float], top_p: Optional[float] = None, seed: Optional[int] = None,
                   voice: Optional[str] = None) -> None:
        length = None
        try:
            job.waited = await self.queue.turn(self.stale_after, job.arrived)
            started = time.time()
            try:
                left = self.stale_after - (time.time() - job.arrived)
                res, spoken = await self._speak_with_retries(job.text, ref, temperature, top_p, seed, int(max(1, left) * 1000), voice)
                job.spoke = time.time() - started
            finally:
                self.queue.release()
            cap = cap_seconds(spoken)
            length = wav_file_seconds(res.path) if res.path else wav_seconds(res.wav)
            job.audio = (res.audio_ms / 1000.0) if getattr(res, "audio_ms", None) else length
            job.chars = len(spoken or job.text or "")
            if length is not None and length > cap:
                self.log(f"A voice line ran far too long ({length:.0f} s for a line expected to take about {expected_seconds(spoken):.0f} s) and was cut at {cap:.0f} s.")
                self.runaway_cuts += 1
                length = cap
                if res.path:
                    await asyncio.to_thread(cut_wav_file, res.path, cap)
                else:
                    res.wav = cut_wav(res.wav, cap)
            if res.path:                                  # relayed from another device: already on disk, moved into place (never held whole in memory)
                rel = f"{secrets.token_hex(8)}/tmp{secrets.token_hex(4)}.wav"
                dest = self.results / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(res.path, dest)
                job.result_path, job.size = rel, dest.stat().st_size
                self._tidy(self.results, RESULT_KEEP)
                return
            name = f"tmp{secrets.token_hex(4)}.wav"
            rel = f"{secrets.token_hex(8)}/{name}"
            dest = self.results / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(res.wav)
            job.result_path, job.size = rel, len(res.wav)
            self._tidy(self.results, RESULT_KEEP)
        except EngineBusy as e:
            job.error = e.message
        except VoiceEngineError as e:
            job.error = e.message
        except asyncio.CancelledError:               # SkyrimNet hung up (give_up): nothing half-made is kept, and the voice is not blamed
            job.error = GIVEN_UP
            if job.result_path:
                (self.results / job.result_path).unlink(missing_ok=True)
                job.result_path = None
            raise
        except Exception as e:                       # never let a job vanish without its waiting request hearing why
            job.error = "Higgs voice couldn't make this line."
            self.log(f"Voice line failed: {e.__class__.__name__}")
        finally:
            if job.given_up:
                self.log(f"Voice line ({job.route or 'Higgs'}): SkyrimNet hung up after {time.time() - job.arrived:.1f} s, so the line was given up on and stopped.")
            else:
                self.log_line(job.route or "Higgs", job.voice, job.asked, time.time() - job.arrived, job.error, None if job.error else length, ref is not None)
            if self.on_line is not None and not job.given_up:
                try:
                    total, ok = time.time() - job.arrived, not job.error
                    n_args = _line_args(self.on_line)
                    if n_args == 6:
                        self.on_line(total, ok, job.waited, job.spoke, job.audio, job.chars)       # (waiting, speaking, sound made, characters): None when the line never got that far
                    elif n_args == 4:
                        self.on_line(total, ok, job.waited, job.spoke)
                    else:
                        self.on_line(total, ok)
                except Exception:                    # saving a figure must never touch the line itself
                    pass
            job.done.set()

    async def speak_test(self, text: str, voice: str) -> bytes:
        """One line for the Voice tab's Test button, made exactly the way a line from SkyrimNet is (the same queue, the same retries and the same cut for a line that runs far too
        long), with the clip of `voice` (on the device the voice runs on). The sound, as a WAV; the engine's own errors go up as they are."""
        arrived = time.time()
        try:
            return await self._speak_test(text, voice, arrived)
        except Exception as e:
            self.log_line("Test button", voice, None, time.time() - arrived, getattr(e, "message", None) or "the line couldn't be made", None)
            raise

    async def _speak_test(self, text: str, voice: str, arrived: float) -> bytes:
        await self.queue.turn(self.stale_after, arrived)
        try:
            left = self.stale_after - (time.time() - arrived)
            res, spoken = await self._speak_with_retries(text, None, None, None, None, int(max(1, left) * 1000), voice)
        finally:
            self.queue.release()
        if res.path:                                         # relayed from another device: streamed to a file, read once and removed
            try:
                wav = await asyncio.to_thread(Path(res.path).read_bytes)
            finally:
                try:
                    os.remove(res.path)
                except OSError:
                    pass
        else:
            wav = res.wav
        cap = cap_seconds(spoken)
        length = wav_seconds(wav)
        out = cut_wav(wav, cap) if length is not None and length > cap else wav
        self.log_line("Test button", voice, None, time.time() - arrived, "", wav_seconds(out))
        return out

    def status(self) -> dict:
        s = self.engine.status()
        s["linesWaiting"] = self.queue.waiting
        s["askedModel"] = self.asked_other
        s["unrecognisedRequests"] = self.unrecognised
        s["healthChecksAnswered"] = self.health_checks
        s["runawayLinesCut"] = self.runaway_cuts
        return s


# ---------------------------------------------------------------------------------------------------------------------------------
# The routes (served at the main address and under /voice)
# ---------------------------------------------------------------------------------------------------------------------------------
def _parse_multipart(content_type: str, body: bytes) -> list:
    """[(filename, bytes)] from a multipart/form-data body, with the standard library (no extra install)."""
    msg = message_from_bytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body, policy=HTTP)
    out = []
    if msg.is_multipart():
        for part in msg.iter_parts():
            fn = part.get_filename()
            if fn is None and (part.get_content_type().startswith("audio/") or part.get_content_type() == "application/octet-stream"
                               or (part.get_param("name", header="content-disposition") or "") in ("files", "file", "audio", "sample", "speaker_audio")):
                fn = "voice.wav"                  # a sample sent without a file name is still a sample
            if fn is not None:
                out.append((fn, part.get_payload(decode=True) or b""))
    return out


def _parse_form(content_type: str, body: bytes) -> dict:
    """{field name: (file name or None, bytes)} from a multipart/form-data body (the standard library only)."""
    msg = message_from_bytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body, policy=HTTP)
    out = {}
    if msg.is_multipart():
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name:
                out[name] = (part.get_filename(), part.get_payload(decode=True) or b"")
    return out


FIELDS_SWITCH = "MULTIPROXY_VOICE_FIELDS"
_KEY_HEADERS = ("authorization", "x-api-key", "api-key", "x-goog-api-key")


def describe_request(method: str, path: str, headers: dict, content_type: str, body: bytes) -> str:
    """One log line that says what a voice request was made of, by NAME only: the path, the header names that are not the usual ones (and whether a key was sent: yes/no),
    the body's field names (JSON: top-level names and the kind of each value; a "data" list: the kind of each slot; multipart: part names, file or not, size),
    never a value, never the sound, never a key."""
    usual = {"host", "user-agent", "accept", "accept-encoding", "connection", "content-length", "content-type"}
    names = sorted({k.lower() for k in headers} - usual - set(_KEY_HEADERS))
    key = "yes" if any(k.lower() in _KEY_HEADERS for k in headers) else "no"
    shape = ""
    kind = lambda v: "null" if v is None else "bool" if isinstance(v, bool) else "int" if isinstance(v, int) else "number" if isinstance(v, float) else \
        "text" if isinstance(v, str) else "list" if isinstance(v, list) else "object" if isinstance(v, dict) else type(v).__name__
    if "json" in (content_type or "").lower() or body[:1] in (b"{", b"["):
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            shape = "json fields: " + ", ".join(f"{k}={kind(v)}" for k, v in parsed.items())
            if isinstance(parsed.get("data"), list):
                shape += " | data slots: " + ", ".join(f"{i}={kind(v)}" for i, v in enumerate(parsed["data"]) if v is not None)
        elif isinstance(parsed, list):
            shape = "json list slots: " + ", ".join(f"{i}={kind(v)}" for i, v in enumerate(parsed) if v is not None)
    elif "multipart" in (content_type or "").lower():
        try:
            msg = message_from_bytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body, policy=HTTP)
            shape = "multipart parts: " + ", ".join(f"{p.get_param('name', header='content-disposition') or '?'}({'file' if p.get_filename() is not None else 'field'}, "
                                                    f"{len(p.get_payload(decode=True) or b'')} bytes)" for p in msg.iter_parts())
        except Exception:
            shape = "multipart (unreadable)"
    return f"Voice request {method} {path}: key sent {key}; other headers: {', '.join(names) or 'none'}; {shape or 'no body fields'}"


def _sse(event: str, payload) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def build_router(service: VoiceService) -> APIRouter:
    r = APIRouter()

    async def note_fields(request: Request, body: bytes) -> None:
        if field_log.wanted(FIELDS_SWITCH):
            service.log(describe_request(request.method, request.url.path, dict(request.headers), request.headers.get("content-type", ""), body))

    @r.post("/gradio_api/upload")
    async def upload(request: Request):
        body = await request.body()
        if len(body) > UPLOAD_MAX_BYTES:
            return JSONResponse({"detail": "That voice clip is too big."}, status_code=413)
        await note_fields(request, body)
        try:
            files = _parse_multipart(request.headers.get("content-type", ""), body)
        except Exception:
            files = []
        if not files:
            service.log(UNRECOGNISED)
            return JSONResponse({"detail": UNRECOGNISED}, status_code=400)
        return [service.save_upload(fn, data) for fn, data in files]

    @r.api_route("/gradio_api/file={path:path}", methods=["GET", "HEAD"])
    async def file(path: str):
        f = service.resolve(path)
        if f is None:
            return JSONResponse({"detail": "That file is missing."}, status_code=404)
        return Response(f.read_bytes(), media_type="audio/wav" if f.suffix.lower() == ".wav" else "application/octet-stream")

    @r.post("/gradio_api/call/generate_audio")
    async def call(request: Request):
        raw = await request.body()
        await note_fields(request, raw)
        try:
            body = json.loads(raw)
        except Exception:
            body = None
        data = body.get("data") if isinstance(body, dict) else body if isinstance(body, list) else None
        service.note_asked(None)
        try:
            job = await service.start_line(data)
        except VoiceEngineError as e:
            if e.message != UNRECOGNISED:
                service.log_refused("Higgs", None, e.message)
            return JSONResponse({"detail": e.message, "error": e.message}, status_code=e.status)
        return {"event_id": job.event_id}

    @r.get("/gradio_api/call/generate_audio/{event_id}")
    async def result(event_id: str, request: Request):
        job = service.jobs.get(event_id)
        if job is None:
            return JSONResponse({"detail": "That line is missing."}, status_code=404)
        if not await service.wait_for_line(request, job):
            return Response(status_code=499)
        if job.error:
            return Response(_sse("error", job.error), media_type="text/event-stream")
        prefix = request.url.path.split("/gradio_api/")[0]
        base = f"{request.url.scheme}://{request.headers.get('host') or request.url.netloc}{prefix}"
        name = job.result_path.rsplit("/", 1)[-1]
        item = {"path": job.result_path, "url": f"{base}/gradio_api/file={job.result_path}", "size": job.size, "orig_name": name,
                "mime_type": None, "is_stream": False, "meta": {"_type": "gradio.FileData"}}
        return Response(_sse("complete", [item]), media_type="text/event-stream")

    @r.post("/v1/audio/voices")
    async def enroll(request: Request):
        """SkyrimNet's OpenAI-Compatible engine registers a voice before it speaks with it: multipart `title` (the character's id), `ref_audio` (a WAV) and `ref_text`
        (the words said in it). The clip is kept with its words; the answer carries the voice's id."""
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > ENROLL_MAX_BYTES + 65536:
            return JSONResponse({"error": {"message": ENROLL_TOO_BIG}}, status_code=413)
        body = await request.body()
        if len(body) > ENROLL_MAX_BYTES + 65536:
            return JSONResponse({"error": {"message": ENROLL_TOO_BIG}}, status_code=413)
        await note_fields(request, body)
        try:
            form = _parse_form(request.headers.get("content-type", ""), body)
        except Exception:
            form = {}
        text = lambda k: form[k][1].decode("utf-8", errors="replace").strip() if k in form else ""
        title = text("title") or text("name") or text("voice") or text("id")
        clip = next((form[k][1] for k in ("ref_audio", "audio", "file", "reference_audio") if k in form and form[k][1]), b"")
        try:
            key = await service.enroll(title, clip, text("ref_text") or text("reference_text"))
        except ClipFormatError as e:
            return JSONResponse({"error": {"message": e.message}}, status_code=400)
        except VoiceEngineError as e:
            return JSONResponse({"error": {"message": e.message}}, status_code=e.status)
        return {"id": key, "voice_id": key, "voice": key, "name": key, "title": title, "object": "voice"}

    @r.get("/v1/audio/voices")
    async def voices_list():
        return {"voices": sorted(service.enrolled())}

    @r.delete("/v1/audio/voices/{voice_id}")
    async def voice_delete(voice_id: str):
        return {"id": voice_key(voice_id), "deleted": service.forget(voice_id)}

    @r.post("/v1/audio/speech")
    async def speech(request: Request):
        """An OpenAI-style speech request, answered with the finished WAV. Best guess at the shape (the engine's own server takes `input` and `voice_ref`): unknown fields are ignored,
        no key is looked at, whatever format is asked for the answer is a WAV."""
        raw = await request.body()
        await note_fields(request, raw)
        try:
            body = json.loads(raw)
        except Exception:
            body = None
        if not isinstance(body, dict):
            service.unrecognised += 1
            service.log(UNRECOGNISED)
            return JSONResponse({"error": {"message": UNRECOGNISED}}, status_code=400)
        parsed, ref_bytes, voice = parse_speech_body(body)
        if not parsed:
            service.unrecognised += 1
            service.log(UNRECOGNISED)
            return JSONResponse({"error": {"message": UNRECOGNISED}}, status_code=400)
        asked = body.get("model") if isinstance(body.get("model"), str) else None
        wrong = service.wrong_voice(asked)
        if wrong:
            service.log_refused("OpenAI-Compatible", asked, wrong)
            return JSONResponse({"error": {"message": wrong}}, status_code=400)
        service.note_asked(asked)
        try:
            job = await service.start_parsed(parsed, ref_bytes, voice, "OpenAI-Compatible", asked)
        except VoiceEngineError as e:
            service.log_refused("OpenAI-Compatible", asked, e.message)
            return JSONResponse({"error": {"message": e.message}}, status_code=e.status)
        if not await service.wait_for_line(request, job):
            return Response(status_code=499)
        if job.error:
            return JSONResponse({"error": {"message": job.error}}, status_code=503)
        f = service.resolve(job.result_path)
        if f is None:
            return JSONResponse({"error": {"message": COULD_NOT_FINISH}}, status_code=502)
        wav = f.read_bytes()
        if str(body.get("response_format") or "").lower() == "json":          # the engine's own server answers {"data": <base64 WAV>} for this
            return {"data": base64.b64encode(wav).decode("ascii")}
        return Response(wav, media_type="audio/wav")

    @r.api_route("/gradio_api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD"])
    async def other(rest: str):
        service.unrecognised += 1
        service.log(f"{UNRECOGNISED} ({rest[:60]})")
        return JSONResponse({"detail": UNRECOGNISED}, status_code=404)

    return r


def mount(app: FastAPI, service: VoiceService, xai_stt=None) -> None:
    """The voice routes at the main address (`/gradio_api/...`) and under `/voice`, plus a bare status route."""
    router = build_router(service)
    app.include_router(router)
    app.include_router(router, prefix="/voice")
    stt = stt_capture.build_router(service.log, xai_stt)             # speech to text: off = records the shape of a request, answers 501; on = stt_xai answers
    app.include_router(stt)
    app.include_router(stt, prefix="/voice")

    @app.get("/voice/status")
    async def voice_status():
        return service.status()
