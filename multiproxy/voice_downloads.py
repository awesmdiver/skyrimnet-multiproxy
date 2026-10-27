"""The main device's kept downloads: "download once, share at home" (2026-10-02).

Every file the voice engine manager fetches (an engine build, the voice model) is kept here with its checksum and a small index, so a second
install (this device again, or another device later) never downloads it twice. Installing uses a kept file by hard link where the drive allows it
(no second copy on disk) and by copy only when it must, so clearing the kept downloads never breaks anything already installed.

Where a file comes from is a `Source`: the internet today (`InternetSource`), a plain folder (`FolderSource`, used by tests and by anyone
who already has the files), and, in the next item, another MultiProxy reached through the Courier. The engine manager only ever asks the
store for "this file"; it does not care which source answered.
"""
import asyncio
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

import aiohttp

import download_retry
import https_only
from local_models import LocalModelError, download_file, _hash_existing


@dataclass(frozen=True)
class FileSpec:
    name: str          # the file name it is kept under
    url: str           # where the internet has it
    size: int
    sha256: str


class Source(Protocol):
    async def fetch(self, spec: FileSpec, dest: Path, on_bytes: Callable[[int], None]) -> None:
        """Put the complete, checksum-verified file at `dest` (resuming a `.part` file if it can). Raise LocalModelError with a plain reason."""


class InternetSource:
    def __init__(self, session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None, device: str = "this device", log: Callable[[str], None] = lambda m: None,
                 what: str = "the decision model's files", sleep=asyncio.sleep):
        self._session_factory, self.device, self.log, self.what, self._sleep = session_factory, device, log, what, sleep

    async def fetch(self, spec: FileSpec, dest: Path, on_bytes: Callable[[int], None]) -> None:
        session = self._session_factory() if self._session_factory else https_only.session()
        try:
            await download_retry.run(lambda: download_file(session, spec.url, dest, spec.size, spec.sha256, on_bytes), url=spec.url, what=self.what, device=self.device,
                                     file=spec.name, log=self.log, sleep=self._sleep)
        except download_retry.DownloadFailed as e:
            raise LocalModelError(502, e.message)
        finally:
            await session.close()


class FolderSource:
    """Files that already sit in a folder under their own names (tests, or a player who downloaded them by hand). Verified like any other."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    async def fetch(self, spec: FileSpec, dest: Path, on_bytes: Callable[[int], None]) -> None:
        src = self.folder / spec.name
        if not src.is_file():
            raise LocalModelError(404, f"{spec.name} isn't in {self.folder}.")
        if src.stat().st_size != spec.size:
            raise LocalModelError(502, f"{spec.name} in {self.folder} is the wrong size.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        await asyncio.to_thread(shutil.copyfile, src, part)
        on_bytes(spec.size)
        digest = (await asyncio.to_thread(_hash_existing, part)).hexdigest()
        if digest != spec.sha256:
            part.unlink(missing_ok=True)
            raise LocalModelError(502, f"{spec.name} didn't pass its safety check because the file is damaged. Press Install to try again.")
        os.replace(part, dest)


def link_or_copy(src: Path, dest: Path) -> str:
    """Make `dest` a second name for `src`'s data (a hard link: no extra space), or a copy when the drive can't. Returns "link" or "copy"."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
        return "link"
    except OSError:
        shutil.copyfile(src, dest)
        return "copy"


# ---- where voice lives (2026-10-03): inside the player's Local models folder when there is one --------------------------------------------------
def _kept_in(folder: Path) -> bool:
    """Is anything kept (or half-downloaded) in this folder? Any file but an empty index counts."""
    try:
        for p in folder.iterdir():
            if p.is_file() and p.name != "index.json":
                return True
        data = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        return isinstance(data, dict) and bool(data)
    except (OSError, ValueError):
        return False


def default_voice_folder(settings_dir, models_folder) -> Path:
    """The voice engine, its model and the clips: `<models folder>/voice` when the player has chosen a models folder, else `<settings>/voice`.
    A voice install that already sits in the old place stays there (it is never moved on its own)."""
    old = Path(settings_dir) / "voice"
    if models_folder and not (old / "voice-state.json").is_file():
        return Path(models_folder) / "voice"
    return old


def downloads_plan(settings_dir, models_folder, explicit=None) -> dict:
    """Where the kept downloads go: {folder (in use now), target (where they should be), old (the old default), offer (move them to target?)}.
    A folder the player chose always wins. Otherwise `<models folder>/voice/downloads`; while the old default still holds kept files and the new
    one is empty the old one keeps working (the Voice tab offers the move)."""
    old = Path(settings_dir) / "voice-downloads"
    if explicit:
        target = Path(explicit)
    elif models_folder:
        target = Path(models_folder) / "voice" / "downloads"
    else:
        target = old
    folder = target
    if not explicit and target != old and _kept_in(old) and not _kept_in(target):
        folder = old
    offer = target != old and _kept_in(old) and not _kept_in(target)
    return {"folder": folder, "target": target, "old": old, "offer": offer}


def _sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class DownloadStore:
    """`folder_fn()` gives the downloads folder (a setting the player can change). The index (`index.json`) records the size and checksum
    each kept file was verified with, so a file that changed or was cut short is noticed and fetched again."""

    def __init__(self, folder_fn: Callable[[], Path]):
        self._folder_fn = folder_fn

    @property
    def folder(self) -> Path:
        return Path(self._folder_fn())

    def _index_path(self) -> Path:
        return self.folder / "index.json"

    def _read_index(self) -> dict:
        try:
            with open(self._index_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_index(self, index: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path().with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=1)
        os.replace(tmp, self._index_path())

    def path_of(self, name: str) -> Path:
        return self.folder / name

    def has(self, spec: FileSpec) -> bool:
        """Kept, complete and the one we expect (same size, same checksum as when it was verified)."""
        p = self.path_of(spec.name)
        entry = self._read_index().get(spec.name)
        try:
            return bool(entry) and p.is_file() and p.stat().st_size == spec.size and entry.get("sha256") == spec.sha256
        except OSError:
            return False

    def adopt(self, spec: FileSpec) -> None:
        """Note a file that arrived some other way (a transfer from the main device, already checked against its checksum) as kept."""
        index = self._read_index()
        index[spec.name] = {"size": spec.size, "sha256": spec.sha256, "url": spec.url, "kept": int(time.time())}
        self._write_index(index)

    def missing_bytes(self, specs: list) -> int:
        return sum(s.size for s in specs if not self.has(s))

    async def get(self, spec: FileSpec, source: Source, on_bytes: Callable[[int], None]) -> Path:
        """The kept file, fetched from `source` first when it isn't kept yet."""
        p = self.path_of(spec.name)
        if self.has(spec):
            on_bytes(spec.size)
            return p
        self.folder.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()                      # there but not the expected one: never trust it
        await source.fetch(spec, p, on_bytes)
        index = self._read_index()
        index[spec.name] = {"size": spec.size, "sha256": spec.sha256, "url": spec.url, "kept": int(time.time())}
        self._write_index(index)
        return p

    def discard(self, name: str) -> None:
        """Forget one kept file (and its half-finished copy) so the next install fetches it again. Anything else kept stays."""
        index = self._read_index()
        for p in (self.path_of(name), self.path_of(name + ".part")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        if index.pop(name, None) is not None and self.folder.is_dir():
            self._write_index(index)

    def partials(self) -> list:
        """Downloads still in progress (or cut short): [{name, size}], the `.part` files."""
        out = []
        try:
            for p in sorted(self.folder.glob("*.part")):
                out.append({"name": p.name, "size": p.stat().st_size})
        except OSError:
            pass
        return out

    def move_kept(self, dest_folder: Path) -> dict:
        """Move every kept file (and half-finished `.part` file) to `dest_folder`. Each kept file is checked against the size and checksum in the index
        before AND after the copy; the old copy goes only once the new one is checked and the new index lists it, so the index never points at
        nothing. A file that doesn't check out stays where it is. Returns {moved, left, bytes}."""
        src, dest = self.folder, Path(dest_folder)
        if src == dest:
            return {"moved": 0, "left": 0, "bytes": 0}
        dest.mkdir(parents=True, exist_ok=True)
        old_index = self._read_index()
        new_store = DownloadStore(lambda: dest)
        new_index = new_store._read_index()
        moved = left = total = 0
        for name, entry in sorted(old_index.items()):
            f, g = src / name, dest / name
            try:
                if not f.is_file() or f.stat().st_size != entry.get("size") or _sha256_of(f) != entry.get("sha256"):
                    left += 1
                    continue
                tmp = dest / (name + ".moving")
                shutil.copyfile(f, tmp)
                if tmp.stat().st_size != entry["size"] or _sha256_of(tmp) != entry["sha256"]:
                    tmp.unlink(missing_ok=True)
                    left += 1
                    continue
                os.replace(tmp, g)
                new_index[name] = entry
                new_store._write_index(new_index)
                f.unlink()
                old_index.pop(name, None)
                self._write_index(old_index)
                moved += 1
                total += entry["size"]
            except OSError:
                left += 1
        try:
            for part in list(src.glob("*.part")):       # a half-finished download carries on from where it was
                tmp = dest / (part.name + ".moving")
                shutil.copyfile(part, tmp)
                if tmp.stat().st_size == part.stat().st_size:
                    os.replace(tmp, dest / part.name)
                    part.unlink()
                else:
                    tmp.unlink(missing_ok=True)
                    left += 1
        except OSError:
            left += 1
        if not old_index and (src / "index.json").is_file():
            try:
                (src / "index.json").unlink()
                src.rmdir()
            except OSError:
                pass
        return {"moved": moved, "left": left, "bytes": total}

    def listing(self) -> dict:
        """What is kept and how much room it takes: {files: [{name, size, sha256}], totalBytes, folder, partials}."""
        index = self._read_index()
        files = []
        for name, entry in sorted(index.items()):
            p = self.path_of(name)
            if p.is_file():
                files.append({"name": name, "size": p.stat().st_size, "sha256": entry.get("sha256", "")})
        return {"files": files, "totalBytes": sum(f["size"] for f in files), "folder": str(self.folder),
                "partials": self.partials()}

    def clear(self, only: Optional[list] = None) -> int:
        """Delete the kept downloads (all, or the named ones) and return the bytes freed. Anything already installed keeps working: it
        is a hard link or a copy, never a pointer into this folder. Half-finished `.part` files go too."""
        freed = 0
        index = self._read_index()
        names = list(index) if only is None else [n for n in only if n in index]
        for name in names:
            p = self.path_of(name)
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass
            index.pop(name, None)
        if only is None and self.folder.is_dir():
            for part in self.folder.glob("*.part"):
                try:
                    freed += part.stat().st_size
                    part.unlink()
                except OSError:
                    pass
        if self.folder.is_dir():
            self._write_index(index)
        return freed
