"""Which graphics card a local AI uses (Routing, step 8, 2026-10-02): the cards on a device with their permanent ids, the remembered choice, and how a choice becomes
the engine's own settings at start.

  * **Permanent ids** (docs/plans/2026-10-02-routing-measurements.md, section 6): an NVIDIA card's UUID (`GPU-...`: survives driver updates, reordering and reboots);
    any other card on Windows its `PNPDeviceID`, on Linux its PCI address. The engine's own names (`CUDA0`, `Vulkan1`) are only this boot's order, so they are never stored.
  * **What is stored** for one local AI: `{"id", "name", "memoryGb"}`, the name only so it can be shown (and said) when the card is gone. No choice stored = Automatic = exactly
    today's behaviour (the engine is started with no card choice at all).
  * **At start** `plan_for` turns a choice into this boot's settings: for an NVIDIA (CUDA) engine the environment variable `CUDA_VISIBLE_DEVICES=<uuid>`; for the
    Vulkan / ROCm engines `--device <name>` found by reading `llama-server --list-devices` and matching the card's name (and memory). A card that is gone, or that the
    installed engine can't use, makes the plan Automatic with a plain note, so a start never fails over a card choice.

Nothing here starts an engine: `local_models.py`, `decision_engine.py` and `voice_engine.py` ask for a plan and add what it says.
"""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import hidden_process

CARD_GONE = "The graphics card you chose ({name}) isn't in this device anymore, so Automatic is used."
CARD_NOT_USABLE = "The software installed here can't use {name}, so Automatic is used."
CARD_NOT_LISTED = "The software didn't list {name}, so Automatic is used."
NAME_NOISE = re.compile(r"\b(nvidia|geforce|amd|radeon|intel|r|tm|graphics|corporation|laptop|gpu|\(r\)|\(tm\))\b", re.I)


@dataclass(frozen=True)
class Card:
    id: str                       # permanent: an NVIDIA UUID, a Windows PNPDeviceID, a Linux PCI address
    name: str
    memory_mb: Optional[int]
    vendor: str                   # "nvidia" | "amd" | "intel" | "other"

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "memoryGb": round(self.memory_mb / 1024, 1) if self.memory_mb else None, "vendor": self.vendor}


def is_integrated(card: Card) -> bool:
    """A processor's built-in graphics (Intel UHD / Iris / plain "Intel(R) Graphics", AMD "Radeon(TM) Graphics"): not offered as a place for an AI, so a device with one real card and
    built-in graphics isn't asked a question it has no use for. Intel Arc and every NVIDIA card are real cards."""
    low = card.name.lower()
    if card.vendor == "intel":
        return "arc" not in low
    if card.vendor == "amd":
        return bool(re.search(r"radeon(\(tm\))?\s+graphics\s*$", low)) or "vega" in low and "radeon vii" not in low
    return False


def choosable(cards: list) -> list:
    """The cards an AI can be kept on. The choice is only shown when this has more than one."""
    return [c for c in cards if not is_integrated(c)]


def choice_of(card: Card) -> dict:
    """What is remembered for a chosen card."""
    return {"id": card.id, "name": card.name, "memoryGb": round(card.memory_mb / 1024, 1) if card.memory_mb else None}


def vendor_of(name: str) -> str:
    low = (name or "").lower()
    if "nvidia" in low or "geforce" in low or "quadro" in low or "rtx" in low:
        return "nvidia"
    if "amd" in low or "radeon" in low:
        return "amd"
    if "intel" in low:
        return "intel"
    return "other"


def short_name(name: str) -> str:
    return re.sub(r"^(NVIDIA|AMD|Intel)\s+", "", re.sub(r"^NVIDIA\s+GeForce\s+", "", str(name or ""), flags=re.I), flags=re.I).strip()


# ---------------------------------------------------------------------------------------------------------------------------------
# Reading the cards
# ---------------------------------------------------------------------------------------------------------------------------------
def parse_nvidia(csv: str) -> list:
    """`nvidia-smi --query-gpu=uuid,name,memory.total --format=csv,noheader,nounits` -> NVIDIA cards, in the tool's own order."""
    out = []
    for line in (csv or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].upper().startswith("GPU-"):
            continue
        try:
            mem = int(float(parts[-1]))
        except ValueError:
            mem = None
        out.append(Card(parts[0], ", ".join(parts[1:-1]), mem, "nvidia"))
    return out


def parse_windows_adapters(text: str) -> list:
    """`Get-CimInstance Win32_VideoController | Select Name,PNPDeviceID,AdapterRAM | ConvertTo-Json`: every display adapter with its PNPDeviceID."""
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data:
        name, pnp = str(d.get("Name") or "").strip(), str(d.get("PNPDeviceID") or "").strip()
        if not name or not pnp or "basic display" in name.lower() or "remote display" in name.lower() or "virtual" in name.lower():
            continue
        ram = d.get("AdapterRAM")
        out.append(Card("pnp:" + pnp, name, int(ram) // (1024 * 1024) if isinstance(ram, (int, float)) and ram > 0 else None, vendor_of(name)))
    return out


def parse_lspci(text: str) -> list:
    """`lspci -D -mm` lines for display adapters -> cards with their PCI address as the id."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r'^(\S+)\s+"([^"]*(?:VGA|3D|Display)[^"]*)"\s+"([^"]*)"\s+"([^"]*)"', line)
        if not m:
            continue
        name = f"{m.group(3)} {m.group(4)}".strip()
        out.append(Card("pci:" + m.group(1), name, None, vendor_of(name)))
    return out


def _same_card(a: str, b: str) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y and (x in y or y in x))


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", NAME_NOISE.sub(" ", re.sub(r"[()]", " ", name or ""))).strip().lower()


def list_cards(run: Callable[..., Any] = hidden_process.run, platform: Optional[str] = None) -> list:
    """Every graphics card on this device with its permanent id. NVIDIA cards come from nvidia-smi (UUID); the others from Windows' own adapter list or Linux's PCI list, and a card
    both lists name is shown once, with the NVIDIA tool's id and memory. An empty list is a device where no card could be read."""
    platform = platform or sys.platform
    cards: list = []
    try:
        r = run(["nvidia-smi", "--query-gpu=uuid,name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            cards = parse_nvidia(r.stdout)
    except Exception:
        pass
    others: list = []
    try:
        if platform == "win32":
            r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID,AdapterRAM | ConvertTo-Json -Compress"], capture_output=True, text=True, timeout=20)
            others = parse_windows_adapters(r.stdout) if r.returncode == 0 else []
        elif platform.startswith("linux"):
            r = run(["lspci", "-D", "-mm"], capture_output=True, text=True, timeout=10)
            others = parse_lspci(r.stdout) if r.returncode == 0 else []
    except Exception:
        pass
    left = list(cards)
    for o in others:
        match = next((c for c in left if _same_card(c.name, o.name)), None) if o.vendor == "nvidia" else None
        if match is not None:
            left.remove(match)                 # the same card: already listed, with its UUID
            continue
        if o.vendor == "nvidia" and cards:     # an NVIDIA adapter the tool didn't list (a name that doesn't match): trust the tool, not the second list
            continue
        cards.append(o)
    return cards


_cache: dict = {}


def cached_cards(max_age: float = 60.0, reader: Optional[Callable[[], list]] = None) -> list:
    """`list_cards()` remembered for a minute (it starts programs: not on every screen refresh). `reader` is for tests."""
    import time
    now = time.monotonic()
    if reader is None and _cache and now - _cache["at"] < max_age:
        return _cache["cards"]
    cards = (reader or list_cards)()
    if reader is None:
        _cache.update(at=now, cards=cards)
    return cards


# ---------------------------------------------------------------------------------------------------------------------------------
# A choice at engine start
# ---------------------------------------------------------------------------------------------------------------------------------
@dataclass
class Plan:
    env: dict = field(default_factory=dict)      # added to the engine's environment
    args: list = field(default_factory=list)     # added to its command line
    card: Optional[Card] = None                  # the card it will use (None: Automatic)
    note: str = ""                               # why Automatic is used when a choice couldn't be followed
    automatic: bool = True

    def to_json(self) -> dict:
        return {"automatic": self.automatic, "card": self.card.to_json() if self.card else None, "note": self.note}


def parse_list_devices(text: str) -> list:
    """`llama-server --list-devices` -> [(device name like "Vulkan1", description, total MiB or None)]."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([A-Za-z]+\d+):\s*(.+?)\s*\((\d[\d,]*)\s*MiB", line)
        if m:
            out.append((m.group(1), m.group(2).strip(), int(m.group(3).replace(",", ""))))
        else:
            m = re.match(r"^\s*([A-Za-z]+\d+):\s*(.+?)\s*$", line)
            if m:
                out.append((m.group(1), m.group(2).strip(), None))
    return out


def list_devices(exe, run: Callable[..., Any] = hidden_process.run) -> list:
    try:
        env = {**os.environ}
        if os.name != "nt":
            env["LD_LIBRARY_PATH"] = f"{os.path.dirname(str(exe))}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
        r = run([str(exe), "--list-devices"], capture_output=True, text=True, timeout=30, cwd=os.path.dirname(str(exe)), env=env)
        return parse_list_devices((r.stdout or "") + "\n" + (r.stderr or ""))
    except Exception:
        return []


def plan_for(choice: Optional[dict], cards: list, engine_kind: str, exe=None, devices_fn: Callable[[Any], list] = list_devices) -> Plan:
    """The engine settings for one AI's remembered `choice` (or None = Automatic). `engine_kind` is the llama.cpp build kind ("cuda-13.4", "vulkan", "rocm", "cpu"...)."""
    if not choice or not choice.get("id"):
        return Plan()
    name = str(choice.get("name") or "that card")
    card = next((c for c in cards if c.id == choice["id"]), None)
    if card is None:
        return Plan(note=CARD_GONE.format(name=name))
    kind = str(engine_kind or "")
    if kind.startswith("cuda"):
        if card.vendor != "nvidia" or not card.id.upper().startswith("GPU-"):
            return Plan(note=CARD_NOT_USABLE.format(name=card.name))
        return Plan(env={"CUDA_VISIBLE_DEVICES": card.id}, card=card, automatic=False)
    if kind in ("vulkan", "rocm", "hip", "sycl"):
        devs = devices_fn(exe) if exe is not None else []
        want = [d for d in devs if _same_card(d[1], card.name)]
        if not want:
            return Plan(note=CARD_NOT_LISTED.format(name=card.name))
        if len(want) > 1 and card.memory_mb:                      # two cards of one name: the one whose memory is closest
            want.sort(key=lambda d: abs((d[2] or 0) - card.memory_mb))
        return Plan(args=["--device", want[0][0]], card=card, automatic=False)
    return Plan(note=CARD_NOT_USABLE.format(name=card.name))


def apply_card(hw, card: Optional[Card], run: Callable[..., Any] = hidden_process.run):
    """`hw` (a `voice_engine.Hardware`) with its card and free graphics memory replaced by the chosen card's: so the fit check and "does it fit beside the game" talk about
    the card the AI will really use. Returns `hw` unchanged when there is no choice."""
    if card is None:
        return hw
    from local_models import GpuInfo
    hw.gpu = GpuInfo(card.name, card.memory_mb, card.vendor)
    if card.vendor == "nvidia" and card.id.upper().startswith("GPU-"):
        try:
            r = run(["nvidia-smi", "-i", card.id, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                hw.free_vram_bytes = int(float(r.stdout.strip().splitlines()[0])) * 1024 * 1024
                return hw
        except Exception:
            pass
    hw.free_vram_bytes = None                    # free memory of this card can't be read: the callers treat None as "unknown", never as full
    return hw


def merge_env(base: Optional[dict], plan: Plan) -> Optional[dict]:
    """The environment to start an engine with: None (inherit) when the plan adds nothing and there is no base."""
    if not plan.env:
        return base
    return {**(base if base is not None else os.environ), **plan.env}
