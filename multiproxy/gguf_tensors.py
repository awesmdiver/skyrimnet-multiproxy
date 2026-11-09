"""The size of every tensor in a model file (GGUF), so the weights that really go on the card can be worked out for any number of offloaded layers.

Ported in part from gputaskman (`src/gputaskman/gguf.py`: `_read_cached`'s tensor-table walk and `GgufInfo.gpu_weight_bytes`), MIT licence:

    Copyright (c) 2026 Scott Hanselman
    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the
    Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
    and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be
    included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
    LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
    DEALINGS IN THE SOFTWARE.

What was taken (the idea and the shape of it): a tensor's size is the gap to the next tensor's offset, so no table of quantisation types is needed; the tensors are grouped by
`blk.<n>.` layer, the output tensors, the input embeddings and "other"; the input embeddings always stay in system memory; the output tensor goes up only when every layer does; a
model that ties the output to the input embeddings puts a copy of them on the card. The reading here is our own (it reuses `gguf_header`'s bounded reader, so a damaged file gives
`None` and never a huge allocation).

Read-only, never raises for a bad file.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

import gguf_header as gh

_BLK = re.compile(r"^blk\.(\d+)\.")
_MAX_TENSORS = 1_000_000
_MAX_DIMS = 8


@dataclass
class TensorSizes:
    layers: int                                    # the file's block count (0 when it has none)
    layer_bytes: dict = field(default_factory=dict)   # block index -> bytes
    token_embd_bytes: int = 0                      # the input embeddings (always in system memory)
    output_bytes: int = 0                          # output tensors, plus a copy of the input embeddings when the model ties them
    other_bytes: int = 0                           # everything else (for example Gemma's per-layer embeddings, which stay in system memory)
    total_bytes: int = 0

    def on_card_bytes(self, n_gpu_layers: Optional[int]) -> int:
        """The weight bytes the engine puts on the card with `-ngl n_gpu_layers` (None or negative = all; the output tensor goes only when every layer does)."""
        if not self.layer_bytes:                    # not a transformer (a vision file, a voice model): assume everything goes up
            return self.total_bytes
        L = self.layers or len(self.layer_bytes)
        ngl = L + 1 if n_gpu_layers is None or n_gpu_layers < 0 else int(n_gpu_layers)
        if ngl <= 0:
            return 0
        # The engine counts the output layer as one of the layers: with `-ngl N` the output goes up first, then the last N-1 blocks. gputaskman's version (the last N blocks, the
        # output only when every layer is offloaded) was measured against the engine's own "model buffer size" on this PC and is one block and the whole output tensor off.
        first_gpu = max(L + 1 - ngl, 0)
        return sum(b for i, b in self.layer_bytes.items() if i >= first_gpu) + self.output_bytes


_cache: dict = {}


def read_tensor_sizes(path) -> Optional[TensorSizes]:
    """The tensor sizes of the model file at `path`, or None when the table can't be read (missing, not a model file, damaged). Remembered while the file is unchanged."""
    path = str(path)
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    if key in _cache:
        return _cache[key]
    try:
        out = _read(path, st.st_size)
    except (OSError, gh._Bad, struct.error, ValueError, MemoryError, OverflowError):
        out = None
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = out
    return out


def _read(path: str, size: int) -> Optional[TensorSizes]:
    with open(path, "rb") as f:
        r = gh._Reader(f)
        if r.take(4) != b"GGUF":
            raise gh._Bad("not a GGUF file")
        if r.num("<I") not in (2, 3):
            raise gh._Bad("GGUF version")
        n_tensors, n_kv = r.num("<Q"), r.num("<Q")
        if n_kv > gh._MAX_KEYS or n_tensors > _MAX_TENSORS:
            raise gh._Bad("too many keys or tensors")
        arch, align, layers = None, 32, 0
        for _ in range(n_kv):
            key = r.string()
            t = r.num("<I")
            keep = key in ("general.architecture", "general.alignment") or (arch is not None and key == arch + ".block_count")
            val = gh._value(r, t, keep)
            if key == "general.architecture" and isinstance(val, str):
                arch = val
            elif key == "general.alignment" and isinstance(val, int) and val > 0:
                align = val
            elif arch is not None and key == arch + ".block_count" and isinstance(val, int):
                layers = val
        tensors = []
        for _ in range(n_tensors):
            name = r.string()
            n_dims = r.num("<I")
            if n_dims > _MAX_DIMS:
                raise gh._Bad("too many dimensions")
            for _d in range(n_dims):
                r.num("<Q")
            r.num("<I")                                         # the tensor's type: not needed, the sizes come from the offsets
            tensors.append((r.num("<Q"), name))
        data_start = (f.tell() - (len(r.buf) - r.pos) + align - 1) // align * align
    if not tensors:
        return None
    tensors.sort()
    out = TensorSizes(layers=layers)
    names = set()
    for i, (offset, name) in enumerate(tensors):
        end = tensors[i + 1][0] if i + 1 < len(tensors) else size - data_start
        nbytes = max(end - offset, 0)
        names.add(name)
        out.total_bytes += nbytes
        m = _BLK.match(name)
        if m:
            idx = int(m.group(1))
            out.layer_bytes[idx] = out.layer_bytes.get(idx, 0) + nbytes
        elif name.startswith("token_embd."):
            out.token_embd_bytes += nbytes
        elif name.startswith("output"):
            out.output_bytes += nbytes
        else:
            out.other_bytes += nbytes
    if "output.weight" not in names and out.layer_bytes:
        out.output_bytes += out.token_embd_bytes             # tied embeddings: the engine puts a copy on the card
    return out
