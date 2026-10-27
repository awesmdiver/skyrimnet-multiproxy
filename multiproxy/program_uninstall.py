"""Uninstalling a program MultiProxy installed on this device, asked for from the main device (2026-10-06).

A remote delete is a security surface, so this one file is the only place the Courier removes a program's folder, and it only ever deletes **inside that
program's own folder**:

  * the caller names a program (a fixed list in `courier.py`), never a path: nothing from the request reaches this file;
  * `confine` resolves the real path and refuses anything that is not strictly inside its root (so never a drive root), a folder that is (or holds) one the Courier
    must keep (its own folder, the models folder), or a link (symbolic link or junction) as the program folder itself;
  * `remove_tree` never follows a link it finds inside: it removes the link and leaves what it pointed at;
  * a file that is locked (Windows) is retried briefly; whatever could not go is returned so the caller can say so plainly and leave the rest.
"""
import os
import stat
import time
from pathlib import Path
from typing import Iterable

REPARSE_POINT = 0x400                  # Windows: a junction, a symbolic link or any other re-parse point (st_file_attributes)
LEFT_LINE = "MultiProxy removed what it could from <PC>, but {n} {files} in use. Close what is using {them} and try again."


class UninstallError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def is_link(path) -> bool:
    """A symbolic link or a Windows junction (or other re-parse point): something whose contents are somewhere else."""
    p = Path(path)
    try:
        if p.is_symlink():
            return True
        return bool(getattr(p.lstat(), "st_file_attributes", 0) & REPARSE_POINT)
    except OSError:
        return False


def _norm(path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _under(child: str, parent: str) -> bool:
    """`child` is strictly inside `parent` (both already resolved and case-folded)."""
    return child != parent and child.startswith(parent.rstrip("\\/") + os.sep)


def confine(root, target, protected: Iterable = ()) -> Path:
    """The real path of `target`, or an UninstallError (nothing is deleted) when it is not safe to remove: it must lie strictly inside `root`, `target` must not
    be a drive root or a link, and it must be neither a `protected` folder nor one that holds one."""
    refused = UninstallError(400, "MultiProxy can't remove that.")
    r, t = _norm(root), _norm(target)
    if Path(t).parent == Path(t):
        raise refused                                         # a drive root (or "/") is never something to delete
    if is_link(target) or not _under(t, r):
        raise refused
    for p in protected:
        if not p:
            continue
        pn = _norm(p)
        if pn == t or _under(pn, t):
            raise refused                                       # the target is, or holds, a folder the Courier keeps
    return Path(os.path.realpath(str(target)))


def _remove_link(p: str) -> None:
    try:
        os.unlink(p)
    except OSError:
        os.rmdir(p)


def _sweep(path: str) -> tuple:
    """One pass over `path`: delete what can be deleted, never follow a link. Returns (bytes freed, [what is left])."""
    freed, left = 0, []
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0, [path]
    for e in entries:
        p = e.path
        try:
            if is_link(p):
                _remove_link(p)                                  # the link goes, what it points at stays
            elif e.is_dir(follow_symlinks=False):
                f, l = _sweep(p)
                freed += f
                left += l
                if not l:
                    os.rmdir(p)
            else:
                size = e.stat(follow_symlinks=False).st_size
                try:
                    os.unlink(p)
                except PermissionError:
                    os.chmod(p, stat.S_IWRITE)                   # a read-only file
                    os.unlink(p)
                freed += size
        except FileNotFoundError:
            pass
        except OSError:
            left.append(p)
    return freed, left


def remove_tree(path, tries: int = 4, wait: float = 0.4) -> tuple:
    """Delete `path` (its folder too). A locked file is tried again a few times, a moment apart. Returns (bytes freed, [files that could not be removed])."""
    p = str(path)
    if not os.path.lexists(p):
        return 0, []
    freed, left = 0, []
    for attempt in range(tries):
        f, left = _sweep(p)
        freed += f
        if not left:
            break
        if attempt < tries - 1:
            time.sleep(wait)
    if not left:
        try:
            os.rmdir(p)
        except OSError:
            left = [p]
    return freed, left


def remove_program(root, target, protected: Iterable = (), tries: int = 4, wait: float = 0.4) -> int:
    """Remove the program folder `target` (inside `root`) and return the bytes freed. Raises UninstallError: a refused path deletes nothing; a file that would not
    go says how many are left (the rest was removed)."""
    real = confine(root, target, protected)
    freed, left = remove_tree(real, tries, wait)
    if left:
        n = len(left)
        raise UninstallError(500, LEFT_LINE.format(n=n, files="file is" if n == 1 else "files are", them="it" if n == 1 else "them"))
    return freed


def folder_size(path) -> int:
    """The bytes under `path` (links are not followed). 0 when it is not there."""
    total = 0
    stack = [str(path)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if is_link(e.path):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


class SizeCache:
    """The size of a folder, remembered for a while (the status report is asked for often; walking a big folder each time would not be kind)."""
    def __init__(self, seconds: float = 60.0):
        self.seconds, self._at, self._key, self._value = seconds, 0.0, None, 0

    def get(self, path) -> int:
        now = time.monotonic()
        if self._key != str(path) or now - self._at > self.seconds:
            self._key, self._at, self._value = str(path), now, folder_size(path)
        return self._value

    def forget(self) -> None:
        self._key = None
