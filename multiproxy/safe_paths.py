"""Where a folder the player (or another device) names may be: this device's own hard drives only (security review S8, 2026-10-06).

A models folder, a voice folder or a downloads folder on a network share (`\\\\server\\share\\...`, or a drive letter that is really a share) can hold a program that the voice
start would later run, and anything on it can change under the program. So a folder is accepted only on a fixed drive inside this device. A removable USB stick and a network
drive are refused with a plain line. On Linux and macOS nothing is refused here (there is no "mapped drive" to tell apart without reading the mount table).
"""
import ctypes
import os
import time
from pathlib import Path

DRIVE_FIXED = 3                       # Windows: GetDriveTypeW's answer for a hard drive inside the device (2 = removable, 4 = network, 5 = disc, 6 = RAM disk)

# Said where a folder is refused. `NOT_LOCAL_SENTENCE` is for the screens on the main device; the fragment goes into a sentence of its own ("Couldn't use that folder on X: ...").
NOT_LOCAL_SENTENCE = "That folder is on a network or removable drive. Pick a folder on a drive inside this device."
NOT_LOCAL_FRAGMENT = "that folder is on a network share or a removable drive; pick one on a hard drive inside the device"


def _drive_type(drive: str) -> int:
    """The Windows drive type of `drive` ("C:"); 0 when it can't be told (which counts as "not a fixed drive")."""
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(drive.rstrip("\\/") + "\\"))
    except (AttributeError, OSError):
        return 0


def is_local_fixed(path) -> bool:
    """True when `path` is on a hard drive inside this device (the real place, after a mapped drive or a link is followed)."""
    text = str(path).strip()
    if not text:
        return False
    if text.startswith("\\\\") or text.startswith("//"):
        return False                                           # \\server\share, \\?\UNC\..., //server/share
    if os.name != "nt":
        return True
    try:
        real = os.path.realpath(text)
    except (OSError, ValueError):
        return False
    if real.startswith("\\\\"):
        return False                                           # a mapped drive that really leads to a share
    for candidate in (text, real):
        drive = os.path.splitdrive(candidate)[0]
        if not drive or drive.startswith("\\\\") or _drive_type(drive) != DRIVE_FIXED:
            return False
    return True


def require_local_fixed(path) -> Path:
    """`path` as a Path when it is on a local fixed drive, otherwise ValueError(NOT_LOCAL_SENTENCE)."""
    if not is_local_fixed(path):
        raise ValueError(NOT_LOCAL_SENTENCE)
    return Path(path)


def write_private_json(path, data, indent: int = 2) -> None:
    """Write `data` as JSON to `path` all-or-nothing and for its owner only (security review S16): the text goes to a temporary file in the SAME folder (so the final move
    stays on one drive), is flushed to disk, and then replaces the real file in one step, so a crash or a second writer can never leave half a settings file. Where the system
    has file permissions (Linux, macOS) the file is created readable by its owner only; on Windows the folder's own rights (the person's profile) apply."""
    import json
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(6):                                   # (Windows refuses to replace a file that a reader has open at that very moment: try again a moment later)
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
