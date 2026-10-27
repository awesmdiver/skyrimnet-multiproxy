"""The Courier's update helper, and the journal both sides use (standard library only; docs/security-review-update-courier-design.md M7 and M8).

The Courier verifies a signed package and unpacks it to `<install>/update/staging/<build>/` (courier_update.py), hands over to this helper, and exits.
The helper is RUN FROM THAT STAGED COPY (`python -I <staging>/courier_updater.py <old pid>`), so replacing the install files never touches the running helper, and a
bug in it is fixed by the next update. It:

  1. re-verifies the signature and every file's hash itself (update_verify.verify_staged) against the keys the OLD Courier carried (named in the journal);
  2. waits for the old Courier (by the pid it was given, never by name) to end;
  3. keeps a previous copy of every program file it will replace, and of courier.json, then replaces the files, courier.py LAST;
  4. starts the new Courier the way it was started (a direct process, or `systemctl --user restart` for the user service) and waits for the new Courier to write
     "confirmed" into the journal AFTER it has answered its own loopback report;
  5. rolls back (restores the previous copies, starts the old Courier) when there is no confirmation in time (60 s Windows, 90 s Linux), when the new process ends early,
     or after three early exits of a service.

It touches program files only: never the pairing code, settings, keys, models or logs. It never asks for administrator rights. Every path is worked out from where this
file is (`<install>/update/staging/<build>/`); the journal supplies only file NAMES from the signed manifest's list, the settings file to back up, and how to start the
Courier. courier.py has the same restore inline at its very top (before it imports anything), for a power cut while this helper was not running.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

JOURNAL_NAME = "journal.json"
BEAT_NAME = "beat"
LOG_NAME = "update.log"
LAST_NAME = "last.json"
SERVICE_UNIT = "multiproxy-courier.service"
CONFIRM_SECONDS = {"win32": 60.0, "linux": 90.0}
OLD_EXIT_SECONDS = 30.0
LOCK_RETRY_SECONDS = 10.0
BEAT_FRESH_SECONDS = 20.0          # a journal whose helper beat is newer than this is being worked on: nobody else touches it
EARLY_EXITS_ALLOWED = 3            # a service that restarts itself: this many not-running looks before confirmation means "it does not start"
SETTINGS_FILE_NAME = "courier.json"


class Cut(Exception):
    """A simulated power cut (tests only): the helper stops dead at that step and writes nothing more."""


# ---- the journal and its folder -----------------------------------------------------------------------------------------------------------------
def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_journal(update_dir: Path) -> Optional[dict]:
    return read_json(Path(update_dir) / JOURNAL_NAME)


def write_journal(update_dir: Path, journal: dict) -> None:
    write_json(Path(update_dir) / JOURNAL_NAME, journal)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_over(src: Path, dst: Path) -> None:
    """Copy src to dst through a neighbouring temp name and one rename, so dst is always either whole-old or whole-new."""
    tmp = dst.with_name(dst.name + ".new")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


_FLAT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,60}\.(py|txt|png|ico)$")
PAIRING_KEYS = ("code", "code_made_at", "paired", "paired_name")          # what a rollback keeps from the settings file as it is NOW (a new pairing code made meanwhile stays made)


def flat_name_ok(name) -> bool:
    """A name the journal may hold: flat, the plain shape of a Courier file. Anything else in a journal is ignored, never joined onto a path (security review SF2)."""
    return isinstance(name, str) and bool(_FLAT_NAME.match(name)) and ".." not in name


def settings_to_put_back(previous_file: Path, current_file: Path) -> str:
    """The text of the settings file a rollback restores: the copy kept before the update, with the pairing code (and the paired state that goes with it) of the file as it is now when
    a new code was made in between, so a rollback can never bring a revoked code back (security review SF4). The same rule is inline at the top of courier.py."""
    text = Path(previous_file).read_text(encoding="utf-8")
    try:
        data, cur = json.loads(text), json.loads(Path(current_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return text
    if isinstance(data, dict) and isinstance(cur, dict) and isinstance(cur.get("code"), str) and cur["code"] and cur["code"] != data.get("code"):
        for k in PAIRING_KEYS:
            if k in cur:
                data[k] = cur[k]
        return json.dumps(data, indent=1)
    return text


def settings_target_ok(settings) -> bool:
    """The settings file a journal names: an absolute path to a file called courier.json that exists already (a rollback overwrites the Courier's own, never creates one elsewhere)."""
    try:
        p = Path(settings)
        return isinstance(settings, str) and p.is_absolute() and p.name == SETTINGS_FILE_NAME and p.is_file()
    except (OSError, ValueError):
        return False


def put_back_settings(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".new")
    tmp.write_text(settings_to_put_back(src, dst), encoding="utf-8", newline="\n")
    os.replace(tmp, dst)


def restore_previous(install: Path, update_dir: Path, journal: dict) -> list:
    """Put the previous copies back (courier.py first), delete files the update created, restore courier.json. Safe to run twice. Returns the names that could not be restored."""
    prev = Path(update_dir) / "previous"
    failed = []
    names = [n for n in (journal.get("backed_up") or []) if flat_name_ok(n)]
    names.sort(key=lambda n: n != "courier.py")
    for n in names:
        try:
            copy_over(prev / n, Path(install) / n)
        except OSError:
            failed.append(n)
    for n in journal.get("created") or []:
        if not flat_name_ok(n):
            continue
        try:
            (Path(install) / n).unlink(missing_ok=True)
        except OSError:
            failed.append(n)
    settings = journal.get("settings_file")
    if settings_target_ok(settings) and (prev / SETTINGS_FILE_NAME).is_file():
        try:
            put_back_settings(prev / SETTINGS_FILE_NAME, Path(settings))
        except OSError:
            failed.append(SETTINGS_FILE_NAME)
    return failed


def beat_is_fresh(update_dir: Path, now: Optional[float] = None) -> bool:
    try:
        return (now if now is not None else time.time()) - (Path(update_dir) / BEAT_NAME).stat().st_mtime < BEAT_FRESH_SECONDS
    except OSError:
        return False


# ---- starting and watching the Courier ----------------------------------------------------------------------------------------------------------
class DirectLauncher:
    """A Courier that is its own process (Windows, or Linux without the user service): started detached, with the arguments the old one was started with."""

    def __init__(self, install: Path, python: str, run_args: list):
        self.install, self.python, self.run_args = Path(install), python, list(run_args)
        self.proc = None
        self.early_exits = 0

    def start(self) -> None:
        cmd = [self.python, str(self.install / "courier.py")] + self.run_args
        kw = {"cwd": str(self.install), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            flags = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            try:
                self.proc = subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)       # | CREATE_BREAKAWAY_FROM_JOB
            except OSError:
                self.proc = subprocess.Popen(cmd, creationflags=flags, **kw)                       # a job that forbids breakaway
        else:
            self.proc = subprocess.Popen(cmd, start_new_session=True, **kw)

    def looks(self) -> str:
        """"running", or "exited" (counted)."""
        if self.proc is None or self.proc.poll() is None:
            return "running"
        self.early_exits += 1
        return "exited"

    def too_many_exits(self) -> bool:
        return self.early_exits >= 1          # a process that ends before it confirmed is not coming back by itself

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(10)
            except (OSError, subprocess.SubprocessError):
                try:
                    self.proc.kill()
                except OSError:
                    pass


class SystemdLauncher:
    """The user service (Linux): started with `systemctl --user`, which restarts a crashing Courier by itself, so "early exit" means three looks at a service that is not running."""

    def __init__(self, unit: str = SERVICE_UNIT):
        self.unit = unit
        self.early_exits = 0

    def _ctl(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["systemctl", "--user", *args, self.unit], capture_output=True, text=True, timeout=60)

    def start(self) -> None:
        self._ctl("restart")

    def looks(self) -> str:
        try:
            ok = self._ctl("is-active").stdout.strip() == "active"
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return "running"
        self.early_exits += 1
        return "exited"

    def too_many_exits(self) -> bool:
        return self.early_exits >= EARLY_EXITS_ALLOWED

    def stop(self) -> None:
        try:
            self._ctl("stop")
        except (OSError, subprocess.SubprocessError):
            pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(0x00100000, False, pid)             # SYNCHRONIZE
        if not h:
            return False
        try:
            return k.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102      # WAIT_TIMEOUT: still running
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_pid(pid: int) -> None:
    """End one process by its id (never by name)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.SubprocessError):
        pass


class Env:
    """Everything the helper does to the world, in one place so tests can replace it."""

    def __init__(self, install: Path, update_dir: Path, old_pid: int, launcher, platform: str = sys.platform):
        self.install, self.update_dir, self.old_pid, self.launcher, self.platform = Path(install), Path(update_dir), old_pid, launcher, platform
        self.sleep, self.now, self.monotonic = time.sleep, time.time, time.monotonic
        self.pid_alive, self.kill_pid = pid_alive, kill_pid
        self.fault = lambda step: None          # tests raise Cut here
        self._last_beat = 0.0

    def tick(self) -> None:
        if self.monotonic() - self._last_beat >= 1.0:
            self._last_beat = self.monotonic()
            try:
                (self.update_dir / BEAT_NAME).write_text(str(self.now()), encoding="utf-8")
            except OSError:
                pass

    def wait(self, seconds: float) -> None:
        self.tick()
        self.sleep(seconds)

    def log(self, message: str) -> None:
        try:
            with open(self.update_dir / LOG_NAME, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass


# ---- the update ---------------------------------------------------------------------------------------------------------------------------------
def _finish(env: Env, j: dict, state: str, result: str, reason: str = "") -> str:
    j.update(state=state, result=result, reason=reason, finished_at=env.now())
    write_journal(env.update_dir, j)
    env.log(f"finished: {result}" + (f" ({reason})" if reason else ""))
    return result


def _wait_old_courier(env: Env) -> bool:
    deadline = env.monotonic() + OLD_EXIT_SECONDS
    while env.pid_alive(env.old_pid) and env.monotonic() < deadline:
        env.wait(0.25)
    if env.pid_alive(env.old_pid):
        env.kill_pid(env.old_pid)
        deadline = env.monotonic() + 10
        while env.pid_alive(env.old_pid) and env.monotonic() < deadline:
            env.wait(0.25)
    return not env.pid_alive(env.old_pid)


def _replace_with_retry(env: Env, src: Path, dst: Path) -> None:
    deadline = env.monotonic() + LOCK_RETRY_SECONDS
    while True:
        try:
            copy_over(src, dst)
            return
        except OSError:
            if env.monotonic() >= deadline:
                raise
            env.wait(0.5)


def rollback(env: Env, j: dict, reason: str) -> str:
    """Stop the new Courier, put the previous files back, start the old Courier. Never raises."""
    env.log(f"rolling back ({reason})")
    j.update(state="rolling_back", reason=reason)
    try:
        write_journal(env.update_dir, j)
    except OSError:
        pass
    try:
        env.launcher.stop()
    except Exception:
        pass
    failed = restore_previous(env.install, env.update_dir, j)
    if failed:
        env.log("could not restore: " + ", ".join(failed))
        _finish(env, j, "failed", "update_failed", "restore_failed")
    else:
        _finish(env, j, "rolled_back", "rolled_back", reason)
    try:
        env.launcher.start()
    except Exception:
        env.log("could not start the old Courier again")
    return j["result"]


def apply_update(env: Env) -> str:
    """The whole update. Returns the journal's final result: ok | rolled_back | update_failed."""
    import update_verify as uv
    j = read_journal(env.update_dir)
    staging = Path(env.update_dir) / "staging"
    if not j or j.get("state") != "staged" or not isinstance(j.get("to_build"), int):
        env.log("nothing staged")
        return "update_failed"
    here = staging / str(j["to_build"])
    env.tick()
    try:
        keys = [base64.b64decode(k) for k in j.get("keys") or []]
        manifest = uv.verify_staged(here, keys=keys, running_build=j.get("from_build"))
    except (uv.UpdateRejected, ValueError) as e:
        code = getattr(e, "code", "bad_signature")
        env.log(f"the staged files did not pass their check ({code})")
        _finish(env, j, "failed", "update_failed", code)
        if _wait_old_courier(env):
            env.launcher.start()
        return "update_failed"
    env.fault("verified")
    if not _wait_old_courier(env):
        env.log("the old Courier did not stop")
        _finish(env, j, "failed", "update_failed", "old_still_running")
        return "update_failed"
    env.fault("old_stopped")

    names = [f["name"] for f in manifest["files"]]
    order = sorted(n for n in names if n != "courier.py") + ["courier.py"]
    prev = Path(env.update_dir) / "previous"
    try:
        if prev.exists():
            shutil.rmtree(prev)
        prev.mkdir(parents=True)
        backed, created = [], []
        for n in names:
            if (env.install / n).is_file():
                shutil.copyfile(env.install / n, prev / n)
                backed.append(n)
            else:
                created.append(n)
        settings = j.get("settings_file")
        if settings_target_ok(settings):
            shutil.copyfile(settings, prev / SETTINGS_FILE_NAME)
    except OSError as e:
        _finish(env, j, "failed", "update_failed", "no_permission" if isinstance(e, PermissionError) else "no_space")
        env.launcher.start()
        return "update_failed"
    j.update(backed_up=backed, created=created, state="replacing")
    write_journal(env.update_dir, j)
    env.fault("journaled")

    try:
        for n in order:
            env.tick()
            env.fault(f"before:{n}")
            _replace_with_retry(env, here / n, env.install / n)
            env.fault(f"after:{n}")
        for f in manifest["files"]:
            if sha256_of(env.install / f["name"]) != f["sha256"]:
                raise OSError("a file changed after it was written")
    except OSError as e:
        return rollback(env, j, "file_locked" if isinstance(e, PermissionError) else "swap_failed")

    j.update(state="started", started_at=env.now())
    write_journal(env.update_dir, j)
    env.fault("started_written")
    try:
        env.launcher.start()
    except OSError:
        return rollback(env, j, "didnt_start")
    env.fault("launched")

    deadline = env.monotonic() + CONFIRM_SECONDS.get(env.platform, 90.0)
    while env.monotonic() < deadline:
        env.wait(0.5)
        now = read_journal(env.update_dir) or {}
        if now.get("state") == "confirmed":
            env.log("confirmed by the new Courier")
            return "ok"
        if env.launcher.looks() == "exited" and env.launcher.too_many_exits():
            return rollback(env, j, "early_exit")
    now = read_journal(env.update_dir) or {}
    if now.get("state") == "confirmed":
        return "ok"
    return rollback(env, j, "no_confirm")


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    here = Path(__file__).resolve().parent
    # Everything is worked out from where this file is: <install>/update/staging/<build>/courier_updater.py
    if here.parent.name != "staging" or here.parent.parent.name != "update" or not here.name.isdigit():
        print("This helper only runs from a staged update.", file=sys.stderr)
        return 2
    update_dir, install = here.parent.parent, here.parent.parent.parent
    sys.dont_write_bytecode = True          # nothing may leave a __pycache__ in the staged folder (the staged files are checked as exactly the manifest's list)
    sys.path.insert(0, str(install))        # the checker is the INSTALLED update_verify.py (the old Courier's, the one that already accepted this package), never the package's own copy (security review SF1)
    try:
        old_pid = int(argv[1])
    except (IndexError, ValueError):
        return 2
    j = read_journal(update_dir) or {}
    if j.get("manager") == "systemd" and sys.platform != "win32":
        launcher = SystemdLauncher()
    else:
        launcher = DirectLauncher(install, sys.executable, j.get("run_args") or ["run"])
    env = Env(install, update_dir, old_pid, launcher)
    env.log(f"helper started for build {j.get('to_build')}")
    result = apply_update(env)
    if result == "ok":
        try:
            shutil.rmtree(update_dir / "staging", ignore_errors=True)
        except OSError:
            pass
    return 0 if result == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
