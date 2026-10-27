"""MultiProxy Courier: finding model files that are already on this device, and choosing where new ones are saved (2026-10-01).

Built to design/multiproxy-courier-mockup.html (Revision 2). Three jobs:

  * find()      list `.gguf` chat models in a few KNOWN folders (LM Studio's models folder, the Courier's own, one the player pointed at)
  * Scanner     the same, across every drive, in the background, only when the player asks ("Scan all drives"): it skips Windows and
                program folders, can be stopped, and adds what it finds as it goes
  * drives() / check_folder()   the drive list with free space for the "where should models be saved" chooser, and a check that a typed
                folder can really be used

Deliberately narrow: nothing here returns anything but model files (and the folder each sits in) and drives. It is not a file browser.
A model found is used where it sits, never copied (the caller adds it by path, the same as "Add a GGUF..." on the main device).
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import safe_paths

MIN_MODEL_BYTES = 50 * 1024 * 1024          # smaller than this is a tokenizer / test file, not a chat model
_SPLIT_PART = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)

# Directory names a full scan never enters: Windows itself, program folders, and per-user app data (huge, and never where models are kept).
SKIP_DIR_NAMES = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "system volume information",
                  "recovery", "$windows.~bt", "$windows.~ws", "appdata", "node_modules", ".git", "__pycache__", "site-packages"}
# ...and on Linux these whole trees.
SKIP_LINUX_TREES = ("/proc", "/sys", "/dev", "/run", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc", "/boot", "/snap", "/var/lib")


HELPER_FOLDERS = ("voice", "decisions")          # where MultiProxy keeps its own voice and decision files, inside a models folder
_NOT_TEXT_ARCH = re.compile(r"tts|asr|whisper|speech|wav2vec|chatterbox|higgs|orpheus|bark|codec|vocoder|snac|encodec|audio", re.I)
_FOLDER_KEY = re.compile(r"^(models|downloads)@", re.I)   # LM Studio's key for a file it found loose in a folder: folder name, `@`, quantisation
_GGUF_SIMPLE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def is_helper_model(model_id=None, path=None, arch=None) -> bool:
    """True for a voice, speech, decision or other helper file that LM Studio (or a folder scan) lists but that can never take a chat job.
    Goes by what is really known: a path through the `voice` or `decisions` folder, LM Studio's folder-named key (`models@f16`), or an
    architecture that is not text generation. A name alone is never guessed from."""
    if path and any(p.lower() in HELPER_FOLDERS for p in re.split(r"[\\/]+", str(path))[:-1]):
        return True
    if model_id:
        mid = str(model_id)
        if _FOLDER_KEY.match(mid):
            return True
        if any(p.lower() in HELPER_FOLDERS for p in re.split(r"[\\/]+", mid)[:-1]):
            return True
    return bool(arch and _NOT_TEXT_ARCH.search(str(arch)))


def gguf_architecture(path) -> Optional[str]:
    """`general.architecture` from a GGUF file's header, or None when it can't be read. Reads only the first few kilobytes."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF" or struct.unpack("<I", f.read(4))[0] < 2:
                return None
            f.read(8)                                            # tensor count
            count = struct.unpack("<Q", f.read(8))[0]
            for _ in range(min(count, 64)):
                klen = struct.unpack("<Q", f.read(8))[0]
                if klen > 256:
                    return None
                key = f.read(klen).decode("utf-8", "replace")
                vtype = struct.unpack("<I", f.read(4))[0]
                if vtype == 8:                                   # a string
                    slen = struct.unpack("<Q", f.read(8))[0]
                    if slen > 4096:
                        return None
                    val = f.read(slen).decode("utf-8", "replace")
                    if key == "general.architecture":
                        return val
                elif vtype in _GGUF_SIMPLE:
                    f.read(_GGUF_SIMPLE[vtype])
                else:
                    return None                                  # an array before the architecture: give up rather than guess
    except (OSError, struct.error, ValueError):
        return None
    return None


def skip_reason(name: str, size: int) -> Optional[str]:
    """Why a `.gguf` file isn't offered as a chat model (None when it is one)."""
    low = name.lower()
    if not low.endswith(".gguf"):
        return "not a model file"
    if "mmproj" in low or "projector" in low:
        return "a vision file (it goes with a model, it isn't one)"
    if "embed" in low or low.startswith("bge-"):
        return "an embedding model, not a chat model"
    if "ggml-vocab" in low:
        return "a vocabulary file"
    m = _SPLIT_PART.search(low)
    if m and int(m.group(1)) != 1:
        return "a later part of a split model"
    if size < MIN_MODEL_BYTES:
        return "too small to be a chat model"
    return None


def _projector_for(model: Path, projectors: list[Path]) -> Optional[Path]:
    """The vision file that sits beside a model: the only one, or else the one whose name shares the most with the model's."""
    if not projectors:
        return None
    if len(projectors) == 1:
        return projectors[0]
    stem = re.sub(r"[-_.](q\d.*|f16|bf16|f32|iq\d.*)$", "", model.stem.lower())
    best = max(projectors, key=lambda p: (stem in p.name.lower(), len(os.path.commonprefix([stem, p.stem.lower().replace("mmproj-", "")]))))
    return best


def _scan_files(directory: str, files: Iterable[str], source: str) -> list[dict]:
    """The chat models among one directory's files, each with the vision file beside it (if any)."""
    d = Path(directory)
    ggufs = [f for f in files if f.lower().endswith(".gguf")]
    if not ggufs:
        return []
    sizes: dict[str, int] = {}
    for f in ggufs:
        try:
            sizes[f] = (d / f).stat().st_size
        except OSError:
            pass
    projectors = [d / f for f in ggufs if "mmproj" in f.lower() or "projector" in f.lower()]
    out = []
    for f in ggufs:
        if f not in sizes or skip_reason(f, sizes[f]):
            continue
        if is_helper_model(path=str(d / f), arch=gguf_architecture(d / f)):
            continue                                       # a voice, speech or decision file: never a chat model
        mm = _projector_for(d / f, projectors)
        mm_size = 0
        if mm is not None:
            try:
                mm_size = mm.stat().st_size
            except OSError:
                mm = None
        out.append({"path": str(d / f), "name": (d / f).stem, "size_bytes": sizes[f] + mm_size, "folder": str(d), "source": source,
                    "vision": mm is not None, "mmproj": str(mm) if mm else None})
    return out


def describe(path: str) -> Optional[dict]:
    """One file as a found-model item, or None when it isn't a chat model (so a vision file can never be added by itself)."""
    p = Path(path)
    if not p.is_file():
        return None
    items = _scan_files(str(p.parent), [x.name for x in p.parent.iterdir() if x.suffix.lower() == ".gguf"], "folder")
    return next((i for i in items if os.path.normcase(i["path"]) == os.path.normcase(str(p))), None)


def find(folders: list[tuple[str, Path]], limit: int = 2000) -> list[dict]:
    """Chat models under each (source, folder), most useful first. A folder that isn't there is simply empty."""
    seen: set[str] = set()
    out: list[dict] = []
    for source, folder in folders:
        if not folder or not Path(folder).is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIR_NAMES and d.lower() not in HELPER_FOLDERS]
            for item in _scan_files(dirpath, filenames, source):
                key = os.path.normcase(item["path"])
                if key not in seen:
                    seen.add(key)
                    out.append(item)
            if len(out) >= limit:
                return out
    out.sort(key=lambda i: i["name"].lower())
    return out


# --------------------------------------------------------------------------------------------
# The full scan ("Scan all drives"), in the background
# --------------------------------------------------------------------------------------------
def drive_roots() -> list[Path]:
    """Where a full scan starts: every fixed drive on Windows; the home folder and the usual mount points elsewhere."""
    if sys.platform == "win32":
        return [Path(d["path"]) for d in drives()]
    roots = [Path.home()] + [Path(p) for p in ("/mnt", "/media", "/srv", "/data", "/models", "/opt") if Path(p).is_dir()]
    return roots


class Scanner:
    """One background scan at a time. `snapshot()` is what the main device polls: the state and everything found so far."""

    def __init__(self, roots: Callable[[], list[Path]] = drive_roots):
        self._roots = roots
        self.state = "idle"                 # idle | running | done | stopped
        self.found: list[dict] = []
        self.folders_checked = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._seen: set[str] = set()

    def start(self) -> None:
        with self._lock:
            if self.state == "running":
                return
            self.state, self.found, self.folders_checked, self._seen = "running", [], 0, set()
            self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="courier-model-scan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=5)

    def _skip(self, path: str, name: str) -> bool:
        if name.lower() in SKIP_DIR_NAMES:
            return True
        return sys.platform != "win32" and path.rstrip("/") in SKIP_LINUX_TREES

    def _run(self, stop: threading.Event) -> None:
        try:
            for root in self._roots():
                for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
                    if stop.is_set():
                        break
                    dirnames[:] = [d for d in dirnames if not self._skip(os.path.join(dirpath, d), d)]
                    self.folders_checked += 1
                    for item in _scan_files(dirpath, filenames, "scan"):
                        key = os.path.normcase(item["path"])
                        if key not in self._seen:
                            self._seen.add(key)
                            self.found.append(item)
                if stop.is_set():
                    break
        finally:
            with self._lock:
                self.state = "stopped" if stop.is_set() else "done"

    def snapshot(self) -> dict:
        return {"state": self.state, "found": list(self.found), "folders_checked": self.folders_checked}


# --------------------------------------------------------------------------------------------
# Where models are saved
# --------------------------------------------------------------------------------------------
def _windows_drives() -> list[dict]:
    import ctypes
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    out = []
    for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if k32.GetDriveTypeW(root) != 3:                       # fixed disks only (no CD, network or USB-stick surprises)
            continue
        label = ctypes.create_unicode_buffer(261)
        k32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0)
        out.append({"path": root, "name": f"{letter}:", "label": label.value or "", "suggested": f"{letter}:\\MultiProxy Models"})
    return out


def _linux_drives() -> list[dict]:
    roots = [("/", str(Path.home() / "multiproxy-models"))]
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            mount = line.split()[1].replace("\\040", " ")
            if mount.startswith(("/mnt/", "/media/", "/run/media/")) and all(mount != r[0] for r in roots):
                roots.append((mount, str(Path(mount) / "MultiProxy Models")))
    except OSError:
        pass
    return [{"path": m, "name": m, "label": "", "suggested": s} for m, s in roots]


def drives() -> list[dict]:
    """This device's drives with free space: [{"path","name","label","free_gb","total_gb","suggested"}]."""
    out = []
    for d in (_windows_drives() if sys.platform == "win32" else _linux_drives()):
        try:
            u = shutil.disk_usage(d["path"])
        except OSError:
            continue
        out.append({**d, "free_gb": round(u.free / 1024 ** 3), "total_gb": round(u.total / 1024 ** 3)})
    return out


class FolderProblem(Exception):
    """A plain reason a typed folder can't be used."""


def check_folder(text: str) -> Path:
    """The folder a player typed, made if needed and proven writable. Raises FolderProblem with a plain reason."""
    raw = (text or "").strip().strip('"')
    if not raw:
        raise FolderProblem("you didn't type a folder")
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        raise FolderProblem("that isn't a full folder path, like D:\\AI Models")
    if p.exists() and not p.is_dir():
        raise FolderProblem("that is a file, not a folder")
    if not safe_paths.is_local_fixed(p):
        raise FolderProblem(safe_paths.NOT_LOCAL_FRAGMENT)
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / f".write-test-{os.getpid()}-{int(time.time() * 1000)}"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise FolderProblem((e.strerror or str(e)).rstrip(".").lower() if e.strerror else str(e))
    return p
