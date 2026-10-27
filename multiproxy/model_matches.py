"""Is a catalog model already on this device in another program? (2026-10-07)

The Local models page offers a few pinned models. A person who already has the same model in Ollama or LM Studio should be told so instead of being offered a 17 GB
download again. A match needs the model's own identity, never its name alone: the family (Gemma 4), the parameter size (12, 26 or 31 billion) AND a file size within a
small margin of the pinned one. Anything that cannot be told (a missing detail, a different family) is "no match": the row keeps its Download button.

Pure functions: multiproxy.py reads Ollama's /api/tags and LM Studio's folder (the same finder the Other devices page uses, courier_models.find) and hands the lists here.
"""
import re
from typing import Optional

SIZE_MARGIN = 0.10          # a model's size on disk may differ this much from the pinned file(s): another program may add a vision file, or round
PARAM_MARGIN = 1.5          # billions: Ollama says 25.8B for what is called 26B
_IDENT = re.compile(r"^gemma-?(\d+)-(\d+(?:\.\d+)?)b", re.I)


def identity(spec: dict) -> Optional[tuple]:
    """(family, billions of parameters) of a catalog entry, from its id ("gemma-4-26b-a4b" is ("gemma4", 26.0)); None for one that is not recognised."""
    m = _IDENT.match(str(spec.get("id") or ""))
    return (f"gemma{m.group(1)}", float(m.group(2))) if m else None


def ollama_names(catalog: list) -> list:
    """The names Ollama would hold the catalog's models under (`gemma4:26b`), for the Ollama box's suggestions. Only entries that identity() recognises."""
    out = []
    for spec in catalog or []:
        ident = identity(spec)
        if ident:
            name = f"{ident[0]}:{ident[1]:g}b"
            if name not in out:
                out.append(name)
    return out


def _sizes(spec: dict) -> list:
    files = spec.get("files") or []
    main = [f["size"] for f in files if f.get("role") == "model"]
    return [sum(f["size"] for f in files)] + main


def _size_ok(spec: dict, size) -> bool:
    return isinstance(size, (int, float)) and size > 0 and any(abs(size - s) <= SIZE_MARGIN * s for s in _sizes(spec))


def _billions(text) -> Optional[float]:
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*([bBmM])", str(text or ""))
    if not m:
        return None
    n = float(m.group(1))
    return n if m.group(2).lower() == "b" else n / 1000


def _family(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def match_ollama(spec: dict, models: Optional[list]) -> Optional[str]:
    """The name Ollama holds this model under (`gemma4:26b`), or None. `models` is /api/tags' list (None or [] when Ollama could not be read)."""
    ident = identity(spec)
    if not ident:
        return None
    for m in models or []:
        details = m.get("details") or {}
        params = _billions(details.get("parameter_size"))
        if _family(details.get("family")) == ident[0] and params is not None and abs(params - ident[1]) <= PARAM_MARGIN and _size_ok(spec, m.get("size")):
            return m.get("name") or m.get("model")
    return None


def match_lmstudio(spec: dict, files: Optional[list]) -> Optional[dict]:
    """The file LM Studio holds this model in ({"name", "size_bytes", ...} from courier_models.find), or None: the file's name must say the family and the size in billions,
    and its size (with its vision file) must be about the pinned one."""
    ident = identity(spec)
    if not ident:
        return None
    for f in files or []:
        name = str(f.get("name") or "")
        m = re.search(r"gemma-?(\d+)[-_ ]+(\d+(?:\.\d+)?)b", name, re.I)
        if m and f"gemma{m.group(1)}" == ident[0] and abs(float(m.group(2)) - ident[1]) <= PARAM_MARGIN and _size_ok(spec, f.get("size_bytes")):
            return f
    return None


def lmstudio_id(spec: dict, ids: Optional[list]) -> Optional[str]:
    """The id LM Studio's server lists this model under, when it lists one (so `Use it` can name it): same family and size in billions, and only one candidate."""
    ident = identity(spec)
    if not ident:
        return None
    found = []
    for i in ids or []:
        m = re.search(r"gemma-?(\d+)[-_ ]+(\d+(?:\.\d+)?)b", str(i), re.I)
        if m and f"gemma{m.group(1)}" == ident[0] and abs(float(m.group(2)) - ident[1]) <= PARAM_MARGIN:
            found.append(i)
    return found[0] if len(found) == 1 else None
