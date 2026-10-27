"""A small reader for the header of a model file (GGUF): the few facts the local-model settings need, without loading the model.

What it gives (`ModelFacts`): how many layers the model has, the longest text it was trained on, and the numbers the graphics-memory
estimate (`local_engine_memory`) needs to work out the conversation cache exactly. The header is the first few megabytes of the file (most
of that is the word list, which is skipped); reading it takes well under a second and never opens the weights.

Read-only, never raises for a bad file: an unreadable or damaged file gives `None` and the callers say "can't tell" instead of guessing.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Any, Optional

_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_MAX_STRING = 64 * 1024 * 1024        # a longer "string" means a damaged file
_MAX_KEYS = 100_000
_KEEP_ARRAY = 1024                    # arrays up to this long are kept for the model's own keys (per-layer lists); the word list is never kept
_CHUNK = 4 * 1024 * 1024


class _Bad(Exception):
    pass


class _Reader:
    """Sequential reads from a file in big chunks (the word list is hundreds of thousands of tiny strings)."""

    def __init__(self, f):
        self.f, self.buf, self.pos = f, b"", 0

    def _need(self, n: int) -> None:
        if len(self.buf) - self.pos >= n:
            return
        more = self.f.read(max(_CHUNK, n))
        if len(more) + len(self.buf) - self.pos < n:
            raise _Bad("file ends early")
        self.buf = self.buf[self.pos:] + more
        self.pos = 0

    def take(self, n: int) -> bytes:
        self._need(n)
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def skip(self, n: int) -> None:
        have = len(self.buf) - self.pos
        if n <= have:
            self.pos += n
            return
        self.f.seek(n - have, os.SEEK_CUR)
        self.buf, self.pos = b"", 0

    def num(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        n = self.num("<Q")
        if n > _MAX_STRING:
            raise _Bad("string too long")
        return self.take(n).decode("utf-8", "replace")

    def skip_string(self) -> None:
        n = self.num("<Q")
        if n > _MAX_STRING:
            raise _Bad("string too long")
        self.skip(n)


def _value(r: _Reader, t: int, keep: bool) -> Any:
    if t in _SCALARS:
        return r.num(_SCALARS[t])
    if t == 8:
        return r.string() if keep else r.skip_string()
    if t == 9:
        et = r.num("<I")
        n = r.num("<Q")
        if et in _SCALARS:
            size = struct.calcsize(_SCALARS[et])
            if keep and n <= _KEEP_ARRAY:
                return list(struct.unpack(f"<{n}{_SCALARS[et][1]}", r.take(size * n)))
            r.skip(size * n)
            return None
        if et == 8:
            for _ in range(n):
                r.skip_string()
            return None
        raise _Bad("nested arrays are not used")
    raise _Bad(f"unknown value type {t}")


def read_metadata(path: str) -> dict:
    """Every `general.*` key and every key of the model's own architecture (`<arch>.*`), as a plain dict. Raises on a file that isn't a GGUF."""
    with open(path, "rb") as f:
        r = _Reader(f)
        if r.take(4) != b"GGUF":
            raise _Bad("not a GGUF file")
        version = r.num("<I")
        if version not in (2, 3):
            raise _Bad(f"GGUF version {version}")
        r.num("<Q")                                   # tensor count
        n = r.num("<Q")
        if n > _MAX_KEYS:
            raise _Bad("too many keys")
        kv: dict = {}
        arch = None
        for _ in range(n):
            key = r.string()
            t = r.num("<I")
            keep = key.startswith("general.") or (arch is not None and key.startswith(arch + "."))
            val = _value(r, t, keep)
            if keep:
                kv[key] = val
            if key == "general.architecture":
                arch = val
        return kv


@dataclass
class ModelFacts:
    arch: str
    layers: Optional[int]                 # transformer layers (`-ngl` counts these, plus one for the output layer)
    trained_ctx: Optional[int]            # the longest text it was trained on
    file_size: int
    kv: dict = field(default_factory=dict)

    def g(self, key: str, default=None):
        return self.kv.get(f"{self.arch}.{key}", default)

    @property
    def offload_layers(self) -> Optional[int]:
        """What `-ngl` counts: every layer plus the output layer (Gemma 4 12B has 48 layers and "49 layers" to offload)."""
        return self.layers + 1 if self.layers is not None else None


_cache: dict = {}


def read_model_facts(path) -> Optional[ModelFacts]:
    """The facts of the model file at `path`, or None when it can't be read (missing, not a model file, damaged). Remembered while the file is unchanged."""
    path = str(path)
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    if key in _cache:
        return _cache[key]
    try:
        kv = read_metadata(path)
    except (OSError, _Bad, struct.error, ValueError, MemoryError):
        return None
    arch = kv.get("general.architecture")
    if not isinstance(arch, str):
        return None
    layers, ctx = kv.get(f"{arch}.block_count"), kv.get(f"{arch}.context_length")
    facts = ModelFacts(arch, layers if isinstance(layers, int) else None, ctx if isinstance(ctx, int) else None, st.st_size, kv)
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = facts
    return facts
