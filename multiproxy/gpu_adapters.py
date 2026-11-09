"""Every graphics card in this PC, whatever its maker: name, maker, memory total, and the adapter id the card counters use (`gpu_counters`).

Windows: Windows' own DXGI adapter list (no maker's tool needed): NVIDIA, AMD, Intel and any other. The software adapter ("Microsoft Basic Render Driver") is left out.
Linux: AMD cards from `/sys/class/drm` (name and memory total only; no new tool). NVIDIA on Linux stays with `nvidia-smi` (see gpu_choice / local_models).

A figure that cannot be read is None, never 0. Nothing here starts a program.

The DXGI plumbing (the ctypes structures `_LUID`, `_DESC1`, `_GUID`, the interface id, the vtable-call helper `_vcall`, and the adapter loop of `enumerate_adapters`: factory
creation, `EnumAdapters1`, `GetDesc1`, `Release`) is ported from `src/gputaskman/dxgi.py` of gputaskman, with the licence notice kept:

    MIT License

    Copyright (c) 2026 Scott Hanselman

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

The rest (the vendor words, the Linux reader, the cache, the matching helpers) is our own.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("multiproxy.gpu_adapters")

MIB = 1024 * 1024
VENDORS = {0x10DE: "nvidia", 0x1002: "amd", 0x8086: "intel"}
SOFTWARE_VENDOR = 0x1414                              # Microsoft (the Basic Render Driver / WARP)
DXGI_ADAPTER_FLAG_SOFTWARE = 2
DXGI_ERROR_NOT_FOUND = 0x887A0002


@dataclass(frozen=True)
class Adapter:
    name: str
    vendor: str                                       # "nvidia" | "amd" | "intel" | "other"
    memory_mb: Optional[int]                          # the card's own memory (dedicated video memory); None when not readable
    luid: Optional[int] = None                        # the id the card counters use (Windows); None on Linux
    vendor_id: int = 0
    device_id: int = 0
    subsys_id: int = 0
    index: int = 0                                    # position in the list among the same model (to tell two identical cards apart)
    pci: Optional[tuple] = None                       # (bus, device, function) on the PCI bus (Windows): how AMD's own driver library is tied to this card

    @property
    def stable_id(self) -> str:
        """An id that survives a restart (the luid does not): the card's PCI ids, plus its position among identical cards."""
        return f"dxgi:{self.vendor_id:04x}:{self.device_id:04x}:{self.subsys_id:08x}:{self.index}"


def vendor_of_id(vendor_id: int) -> str:
    return VENDORS.get(vendor_id, "other")


# --- Windows: DXGI (ported plumbing) -------------------------------------------------------------------------------------------------------------------------------------

def _structs():
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT), ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT),
                    ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                    ("AdapterLuid", LUID), ("Flags", wintypes.UINT)]

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    return wintypes, DESC1, GUID


def _vcall(obj, index: int, restype, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def _pci_address(luid: int):
    """The adapter's PCI (bus, device, function), asked of the graphics kernel by the adapter id; None when it will not say. Read only. Ported from `adapter_pci_address` of gputaskman's
    `dxgi.py` (the call is `D3DKMTQueryAdapterInfo` with the PCI-address query type 6; the PCI domain is not exposed)."""
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class OpenAdapter(ctypes.Structure):
        _fields_ = [("luid", LUID), ("handle", wintypes.UINT)]

    class Address(ctypes.Structure):
        _fields_ = [("bus", wintypes.UINT), ("device", wintypes.UINT), ("function", wintypes.UINT)]

    class Query(ctypes.Structure):
        _fields_ = [("handle", wintypes.UINT), ("kind", wintypes.UINT), ("data", ctypes.c_void_p), ("size", wintypes.UINT)]

    try:
        gdi = ctypes.WinDLL("gdi32")
        opened = OpenAdapter(LUID(luid & 0xFFFFFFFF, ctypes.c_int32(luid >> 32).value), 0)
        if gdi.D3DKMTOpenAdapterFromLuid(ctypes.byref(opened)) != 0:
            return None
        try:
            addr = Address()
            q = Query(opened.handle, 6, ctypes.addressof(addr), ctypes.sizeof(addr))
            if gdi.D3DKMTQueryAdapterInfo(ctypes.byref(q)) != 0:
                return None
            return (int(addr.bus), int(addr.device), int(addr.function))
        finally:
            gdi.D3DKMTCloseAdapter(ctypes.byref(wintypes.UINT(opened.handle)))
    except (OSError, AttributeError):
        return None


def _dxgi_raw() -> list:
    """The raw DXGI rows: `[{"name", "vendor_id", "device_id", "subsys_id", "dedicated", "luid", "software"}]`; [] when DXGI cannot be used."""
    wintypes, DESC1, GUID = _structs()
    try:
        dxgi = ctypes.WinDLL("dxgi")
    except OSError:
        return []
    iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))      # IDXGIFactory1
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
        return []
    out = []
    try:
        enum_adapters1 = _vcall(factory, 12, ctypes.c_long, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
        i = 0
        while True:
            adapter = ctypes.c_void_p()
            if enum_adapters1(factory, i, ctypes.byref(adapter)) != 0:                     # DXGI_ERROR_NOT_FOUND: the end of the list
                break
            try:
                desc = DESC1()
                if _vcall(adapter, 10, ctypes.c_long, ctypes.POINTER(DESC1))(adapter, ctypes.byref(desc)) == 0:
                    out.append({"name": desc.Description, "vendor_id": int(desc.VendorId), "device_id": int(desc.DeviceId), "subsys_id": int(desc.SubSysId),
                                "dedicated": int(desc.DedicatedVideoMemory), "luid": ((desc.AdapterLuid.HighPart & 0xFFFFFFFF) << 32) | desc.AdapterLuid.LowPart,
                                "software": bool(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE),
                                "pci": _pci_address(((desc.AdapterLuid.HighPart & 0xFFFFFFFF) << 32) | desc.AdapterLuid.LowPart)})
            finally:
                _vcall(adapter, 2, wintypes.ULONG)(adapter)                                # Release
            i += 1
    finally:
        _vcall(factory, 2, wintypes.ULONG)(factory)
    return out


def adapters_from_rows(rows: list) -> list:
    """The cards among the raw DXGI rows: no software adapter; the same model twice gets index 0 and 1."""
    seen: dict = {}
    out = []
    for r in rows:
        if r.get("software") or r.get("vendor_id") == SOFTWARE_VENDOR or "basic render" in str(r.get("name", "")).lower():
            continue
        key = (r["vendor_id"], r["device_id"], r["subsys_id"])
        idx = seen.get(key, 0)
        seen[key] = idx + 1
        mb = int(r["dedicated"]) // MIB if r.get("dedicated") else None
        out.append(Adapter(name=str(r["name"]).strip(), vendor=vendor_of_id(r["vendor_id"]), memory_mb=mb or None, luid=r.get("luid"),
                           vendor_id=r["vendor_id"], device_id=r["device_id"], subsys_id=r["subsys_id"], index=idx, pci=r.get("pci")))
    return out


# --- Linux: AMD from /sys/class/drm ---------------------------------------------------------------------------------------------------------------------------------------

def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


SYSFS_ROOT = "/sys/class/drm"                     # tests point this at nothing


def amd_from_sysfs(root: Optional[str] = None) -> list:
    """AMD cards: the name (`product_name` when the driver gives one, else a plain "AMD graphics card") and the memory total (`mem_info_vram_total`). Nothing else is read."""
    root = root or SYSFS_ROOT
    try:
        entries = sorted(e for e in os.listdir(root) if re.fullmatch(r"card\d+", e))
    except OSError:
        return []
    out = []
    for e in entries:
        dev = os.path.join(root, e, "device")
        vendor = _read(os.path.join(dev, "vendor"))
        if vendor is None or vendor.lower() != "0x1002":
            continue
        total = _read(os.path.join(dev, "mem_info_vram_total"))
        mb = int(total) // MIB if total and total.isdigit() and int(total) > 0 else None
        name = _read(os.path.join(dev, "product_name")) or "AMD graphics card"
        did = _read(os.path.join(dev, "device")) or "0"
        out.append(Adapter(name=name, vendor="amd", memory_mb=mb, vendor_id=0x1002, device_id=int(did, 16) if re.fullmatch(r"0x[0-9a-fA-F]+", did) else 0, index=len(out)))
    return out


# --- the list ---------------------------------------------------------------------------------------------------------------------------------------------------------------

_cache: dict = {}
_lock = threading.Lock()


def list_adapters(max_age: float = 60.0, platform: Optional[str] = None, dxgi: Optional[Callable[[], list]] = None, sysfs: Optional[Callable[[], list]] = None) -> list:
    """Every card this PC has (Windows: all makers; Linux: AMD only, see the head of this file), remembered for a minute. [] when it cannot be read (never a made-up card).
    `dxgi` / `sysfs` give the raw readings (tests)."""
    plat = platform or sys.platform
    now = time.monotonic()
    custom = dxgi is not None or sysfs is not None or platform is not None
    with _lock:
        if not custom and _cache and now - _cache["at"] < max_age:
            return list(_cache["cards"])
    try:
        if plat == "win32":
            cards = adapters_from_rows((dxgi or _dxgi_raw)())
        elif plat.startswith("linux"):
            cards = (sysfs or amd_from_sysfs)()
        else:
            cards = []
    except Exception as e:
        log.warning("The list of graphics cards could not be read: %s", e)
        cards = []
    if not custom:
        with _lock:
            _cache.update(at=now, cards=list(cards))
    return cards


def reset() -> None:
    with _lock:
        _cache.clear()


def match_by_name(adapters: list, name: str) -> Optional[Adapter]:
    """The adapter whose name is the same card as `name` (case-insensitive, either contains the other); None when none does or it is ambiguous (two different matches)."""
    low = re.sub(r"\s+", " ", (name or "").lower()).strip()
    if not low:
        return None
    hits = [a for a in adapters if a.name.lower() == low or (low in a.name.lower() or a.name.lower() in low)]
    return hits[0] if len(hits) == 1 else None
