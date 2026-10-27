"""The Courier's update helper (courier_updater.py) and the recovery at the top of courier.py: the swap, the rollback, and a power cut at every step.

Everything runs in scratch folders with made-up packages and throwaway keys. The helper's real process is started only against a stand-in Courier in a scratch folder
(never the director's real Courier, folders, firewall or autostart), on this PC only.
"""
import base64
import copy
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import courier
import courier_updater as upd
import update_verify as uv
from tests.fixtures import update_pkg as pk

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = b'{"id": "abc", "code": "ZZZ-999", "port": 8110, "paired": true}'


class Clock:
    def __init__(self):
        self.t = 1000.0

    def mono(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def now(self):
        return 5000.0 + self.t


class Launcher:
    """Stands in for the Courier process. mode: confirm | silent | exit."""

    def __init__(self, env_ref, mode="confirm"):
        self.env_ref, self.mode, self.starts, self.stops, self.early_exits = env_ref, mode, 0, 0, 0
        self.settings = None

    def start(self):
        self.starts += 1
        if self.starts == 1 and self.settings is not None:            # the new Courier migrates its settings file as it starts
            self.settings.write_bytes(b'{"changed": "by the new Courier"}')
        if self.mode == "confirm" and self.starts == 1:
            e = self.env_ref[0]
            j = upd.read_journal(e.update_dir)
            j.update(state="confirmed", result="ok")
            upd.write_journal(e.update_dir, j)

    def looks(self):
        if self.mode == "exit" and self.starts >= 1:
            self.early_exits += 1
            return "exited"
        return "running"

    def too_many_exits(self):
        return self.early_exits >= 1

    def stop(self):
        self.stops += 1


def build_world(tmp_path, mode="confirm", platform="win32", manager="direct", **stage_kw):
    install = pk.make_install(tmp_path / "install", build=6)
    settings = tmp_path / "settings" / "courier.json"
    settings.parent.mkdir()
    settings.write_bytes(SETTINGS)
    old = pk.snapshot(install)
    pk.stage(install, build=7, settings_file=settings, manager=manager, **stage_kw)
    holder = []
    launcher = Launcher(holder, mode)
    clock = Clock()
    env = upd.Env(install, install / "update", 424242, launcher, platform=platform)
    env.sleep, env.monotonic, env.now = clock.sleep, clock.mono, clock.now
    env.pid_alive, env.kill_pid = (lambda pid: False), (lambda pid: None)
    holder.append(env)
    launcher.settings = settings
    return env, launcher, clock, old, settings


def new_files():
    return pk.files_for(7)


def installed(env, files):
    return all((env.install / n).read_bytes() == d for n, d in files.items())


def test_a_good_update_replaces_only_the_program_files_and_keeps_the_previous_copy(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path)
    launcher.settings = None
    assert upd.apply_update(env) == "ok"
    assert installed(env, new_files()) and launcher.starts == 1
    prev = env.update_dir / "previous"
    assert {n: (prev / n).read_bytes() for n in old if n in new_files()} == {n: old[n] for n in old if n in new_files()}
    assert settings.read_bytes() == SETTINGS, "the settings and the pairing code are never touched"
    assert {p.name for p in env.install.iterdir() if p.is_file()} == set(old), "no file appears that the manifest did not list"
    j = upd.read_journal(env.update_dir)
    assert j["state"] == "confirmed" and j["backed_up"] and j["from_build"] == 6 and j["to_build"] == 7


def test_the_courier_py_is_replaced_last(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path)
    steps = []
    env.fault = steps.append
    upd.apply_update(env)
    replaced = [s for s in steps if s.startswith("after:")]
    assert replaced[-1] == "after:courier.py" and len(replaced) == len(new_files())


@pytest.mark.parametrize("platform,seconds", [("win32", 60), ("linux", 90)])
def test_no_confirmation_in_time_puts_the_old_courier_back(tmp_path, platform, seconds):
    env, launcher, clock, old, settings = build_world(tmp_path, mode="silent", platform=platform)
    start = clock.t
    assert upd.apply_update(env) == "rolled_back"
    assert seconds <= clock.t - start < seconds + 40
    assert pk.snapshot(env.install) == old, "every program file is back as it was"
    assert settings.read_bytes() == SETTINGS, "and so is courier.json, which a new build may have migrated"
    j = upd.read_journal(env.update_dir)
    assert j["state"] == "rolled_back" and j["reason"] == "no_confirm" and launcher.stops == 1 and launcher.starts == 2, "the new one stopped, the old one started"


def test_a_new_courier_that_exits_at_once_is_rolled_back_without_waiting_the_full_time(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path, mode="exit")
    start = clock.t
    assert upd.apply_update(env) == "rolled_back"
    assert clock.t - start < 10 and pk.snapshot(env.install) == old
    assert upd.read_journal(env.update_dir)["reason"] == "early_exit"


class FakeService(upd.SystemdLauncher):
    def __init__(self, answers):
        super().__init__()
        self.answers, self.calls = list(answers), []

    def _ctl(self, *args):
        self.calls.append(args[0])
        out = self.answers.pop(0) if args[0] == "is-active" and self.answers else ""
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")


def test_a_service_that_keeps_restarting_is_rolled_back_after_three_early_exits(tmp_path):
    env, _, clock, old, settings = build_world(tmp_path, platform="linux", manager="systemd")
    env.launcher = FakeService(["failed", "activating", "failed"] + ["active"] * 50)
    start = clock.t
    assert upd.apply_update(env) == "rolled_back"
    assert clock.t - start < 20 and pk.snapshot(env.install) == old
    assert env.launcher.calls[0] == "restart" and env.launcher.calls[-2:] == ["stop", "restart"], "restarted as the user service, stopped, then the old one started again"
    assert upd.read_journal(env.update_dir)["reason"] == "early_exit"


def test_a_service_that_flaps_once_or_twice_and_then_confirms_is_kept(tmp_path):
    env, _, clock, old, settings = build_world(tmp_path, platform="linux", manager="systemd")
    svc = FakeService(["failed", "activating"] + ["active"] * 5)
    env.launcher = svc
    real_ctl = svc._ctl

    def ctl(*args):
        r = real_ctl(*args)
        if args[0] == "is-active" and not svc.answers:          # the third look: the new Courier has written "confirmed"
            j = upd.read_journal(env.update_dir)
            j.update(state="confirmed")
            upd.write_journal(env.update_dir, j)
        return r
    svc._ctl = ctl
    assert upd.apply_update(env) == "ok" and installed(env, new_files())


def test_a_locked_file_is_retried_then_everything_is_put_back(tmp_path, monkeypatch):
    env, launcher, clock, old, settings = build_world(tmp_path)
    real = upd.copy_over

    def locked(src, dst):
        if Path(dst).name == "other_pcs.py" and "previous" not in str(src):
            raise PermissionError("held by an antivirus scan")
        return real(src, dst)
    monkeypatch.setattr(upd, "copy_over", locked)
    start = clock.t
    assert upd.apply_update(env) == "rolled_back"
    assert clock.t - start >= upd.LOCK_RETRY_SECONDS
    assert pk.snapshot(env.install) == old
    assert upd.read_journal(env.update_dir)["reason"] == "file_locked"


def test_a_file_that_is_not_writable_at_all_does_not_leave_a_mix(tmp_path, monkeypatch):
    env, launcher, clock, old, settings = build_world(tmp_path)
    real = upd.copy_over
    calls = {"n": 0}

    def sometimes(src, dst):
        calls["n"] += 1
        if calls["n"] >= 3 and "previous" not in str(src):
            raise PermissionError("read-only folder")
        return real(src, dst)
    monkeypatch.setattr(upd, "copy_over", sometimes)
    assert upd.apply_update(env) == "rolled_back"
    assert pk.snapshot(env.install) == old


def test_a_staged_file_changed_after_it_was_checked_is_caught_before_anything_is_replaced(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path, mode="silent")
    (env.update_dir / "staging" / "7" / "other_pcs.py").write_bytes(b"import os; os.system('evil')\n")
    assert upd.apply_update(env) == "update_failed"
    assert pk.snapshot(env.install) == old and not (env.update_dir / "previous").exists()
    j = upd.read_journal(env.update_dir)
    assert j["state"] == "failed" and j["reason"] == "damaged" and launcher.starts == 1, "the old Courier is started again"


def test_a_package_signed_by_a_key_the_old_courier_did_not_carry_is_refused_by_the_helper_too(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path, mode="silent", seed=pk.OTHER_SEED)
    assert upd.apply_update(env) == "update_failed"
    assert upd.read_journal(env.update_dir)["reason"] == "bad_signature" and pk.snapshot(env.install) == old


def test_the_helper_refuses_a_build_that_is_not_newer(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path, mode="silent", from_build=7)
    assert upd.apply_update(env) == "update_failed"
    assert upd.read_journal(env.update_dir)["reason"] == "not_newer" and pk.snapshot(env.install) == old


def test_an_old_courier_that_will_not_stop_is_left_alone(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path)
    env.pid_alive = lambda pid: True
    assert upd.apply_update(env) == "update_failed"
    assert pk.snapshot(env.install) == old and upd.read_journal(env.update_dir)["reason"] == "old_still_running"


# ---- a power cut at every step ----------------------------------------------------------------------------------------------------------------------
def all_steps(tmp_path):
    env, *_ = build_world(tmp_path / "dry")
    steps = []
    env.fault = steps.append
    upd.apply_update(env)
    return steps


def test_a_power_cut_at_any_step_is_put_right_by_the_next_start(tmp_path):
    steps = all_steps(tmp_path)
    assert "before:courier.py" in steps and "launched" in steps and len(steps) >= 10
    for i, step in enumerate(steps):
        env, launcher, clock, old, settings = build_world(tmp_path / f"cut{i}")

        def cut(s, target=step):
            if s == target:
                raise upd.Cut(s)
        env.fault = cut
        with pytest.raises(upd.Cut):
            upd.apply_update(env)
        settings.write_bytes(b'{"changed": "by a new build"}')
        running = uv.read_courier_build((env.install / "courier.py").read_bytes())       # which courier.py the next start would run
        outcome = courier._recover_unfinished_update(base=env.install, build=running, now=time.time() + 600)
        now_files = pk.snapshot(env.install)
        if outcome == "" and (upd.read_journal(env.update_dir) or {}).get("state") == "confirmed":
            outcome = "complete"          # (the stand-in new Courier had already confirmed before the cut: nothing to put right)
        if outcome == "complete":
            assert step in ("after:courier.py", "started_written", "launched"), step
            assert all(now_files[n] == d for n, d in new_files().items()), f"cut at {step}: 'complete' must mean every new file is in place"
        else:
            assert outcome in ("restored", ""), (step, outcome)
            assert now_files == old, f"cut at {step}: the previous files must be back"
            if outcome == "restored":
                assert settings.read_bytes() == SETTINGS
                assert upd.read_journal(env.update_dir)["state"] == "rolled_back"


def test_nothing_is_touched_while_a_helper_is_still_working(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path)

    def cut(s):
        if s == "before:courier.py":
            raise upd.Cut(s)
    env.fault = cut
    with pytest.raises(upd.Cut):
        upd.apply_update(env)
    partial = pk.snapshot(env.install)
    (env.update_dir / upd.BEAT_NAME).write_text("x", encoding="utf-8")                # a fresh heartbeat: the helper is alive
    assert courier._recover_unfinished_update(base=env.install, build=6) == "helper_working"
    assert pk.snapshot(env.install) == partial
    assert courier._recover_unfinished_update(base=env.install, build=6, now=time.time() + 600) == "restored" and pk.snapshot(env.install) == old


def test_the_two_restores_give_the_same_result(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path / "a")

    def cut(s):
        if s == "before:courier.py":
            raise upd.Cut(s)
    env.fault = cut
    with pytest.raises(upd.Cut):
        upd.apply_update(env)
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    j = upd.read_journal(env.update_dir)
    j["settings_file"] = str(settings)
    assert upd.restore_previous(env.install, env.update_dir, j) == []
    inline = tmp_path / "b" / "install"
    jb = json.loads((inline / "update" / "journal.json").read_text())
    jb["settings_file"] = str(tmp_path / "b" / "settings" / "courier.json")
    (inline / "update" / "journal.json").write_text(json.dumps(jb))
    assert courier._recover_unfinished_update(base=inline, build=6, now=time.time() + 600) == "restored"
    assert pk.snapshot(env.install) == pk.snapshot(inline) == old


def test_a_restore_is_safe_to_run_twice_and_a_finished_update_is_never_restored(tmp_path):
    env, launcher, clock, old, settings = build_world(tmp_path)
    assert upd.apply_update(env) == "ok"
    assert courier._recover_unfinished_update(base=env.install, build=7, now=time.time() + 600) == ""
    assert installed(env, new_files())
    j = upd.read_journal(env.update_dir)
    assert upd.restore_previous(env.install, env.update_dir, j) == [] and upd.restore_previous(env.install, env.update_dir, j) == []
    assert pk.snapshot(env.install) == old


# ---- what the helper must never do -------------------------------------------------------------------------------------------------------------
def test_the_update_code_never_asks_for_more_rights_or_runs_a_shell_or_an_installer():
    for name in ("courier_updater.py", "courier_update.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"runas|ShellExecute|pkexec|sudo\b|shell\s*=\s*True|os\.system|setup-courier|install-courier|Set up this PC", text, flags=re.I), name
        assert "pip" not in re.findall(r"subprocess\.\w+\(\s*\[([^\]]*)\]", text, flags=re.S).__str__(), name


def test_only_program_files_can_ever_be_named_by_a_manifest():
    for bad in ("courier.json", "config.json", "multiproxy.ini", "courier.log", "signin.dat", "pairing.txt.json", "Set up this PC.bat", "setup-courier.ps1", "install-courier.sh", "../courier.py"):
        assert not uv.valid_name(bad), bad
    for good in ("courier.py", "requirements-courier.txt", "courier.ico", "courier-icon.png", "LICENSE-proxy.txt", "courier-build.txt"):
        assert uv.valid_name(good), good


def test_the_helper_only_runs_from_a_staged_copy():
    r = subprocess.run([sys.executable, "-I", str(ROOT / "courier_updater.py"), "123"], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert r.returncode == 2 and "only runs from a staged update" in r.stderr


def test_pid_alive_tells_a_running_process_from_a_finished_one():
    assert upd.pid_alive(__import__("os").getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not upd.pid_alive(p.pid)
    assert not upd.pid_alive(0) and not upd.pid_alive(-5)


# ---- the real helper process, starting a stand-in Courier ----------------------------------------------------------------------------------------
FAKE_COURIER = '''COURIER_VERSION = "1.0.0"
COURIER_BUILD = {build}
import json, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
(here / "ran-{build}.txt").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
MODE = "{mode}"
if MODE == "crash":
    sys.exit(3)
j = json.loads((here / "update" / "journal.json").read_text())
if MODE == "confirm" and j.get("state") == "started" and j.get("to_build") == COURIER_BUILD:
    j.update(state="confirmed", result="ok")
    (here / "update" / "journal.json").write_text(json.dumps(j))
time.sleep(4)
'''


def real_world(tmp_path, new_mode):
    install = tmp_path / "install"
    install.mkdir()
    for n, d in pk.files_for(6).items():
        (install / n).write_bytes(d)
    (install / "courier.py").write_text(FAKE_COURIER.format(build=6, mode="confirm"), encoding="utf-8")
    (install / "update_verify.py").write_bytes((ROOT / "update_verify.py").read_bytes())          # (a real install has its own checker: the helper uses that one)
    old = pk.snapshot(install)
    files = pk.files_for(7)
    files["courier.py"] = FAKE_COURIER.format(build=7, mode=new_mode).encode()
    files["courier_updater.py"] = (ROOT / "courier_updater.py").read_bytes()
    files["update_verify.py"] = (ROOT / "update_verify.py").read_bytes()
    pk.stage(install, build=7, files=files, run_args=["run", "--marker"], settings_file=tmp_path / "courier.json")
    (tmp_path / "courier.json").write_bytes(SETTINGS)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    return install, old, dead.pid


def run_real_helper(install, dead_pid):
    helper = install / "update" / "staging" / "7" / "courier_updater.py"
    return subprocess.run([sys.executable, "-I", str(helper), str(dead_pid)], capture_output=True, text=True, timeout=180, cwd=str(install))


def test_the_real_helper_swaps_starts_the_new_courier_and_keeps_it_when_it_confirms(tmp_path):
    install, old, dead = real_world(tmp_path, "confirm")
    r = run_real_helper(install, dead)
    assert r.returncode == 0, r.stderr
    assert uv.read_courier_build((install / "courier.py").read_bytes()) == 7
    j = upd.read_journal(install / "update")
    assert j["state"] == "confirmed" and j["result"] == "ok"
    assert (install / "ran-7.txt").read_text() == "run --marker", "started the way the old one was started"
    assert (tmp_path / "courier.json").read_bytes() == SETTINGS
    assert not (install / "update" / "staging").exists(), "the staged copy is cleaned up after a good update"
    time.sleep(5)                                    # (the stand-in new Courier ends by itself)


def test_the_real_helper_rolls_back_when_the_new_courier_crashes_and_starts_the_old_one(tmp_path):
    install, old, dead = real_world(tmp_path, "crash")
    r = run_real_helper(install, dead)
    assert r.returncode == 1, r.stderr
    j = upd.read_journal(install / "update")
    assert j["state"] == "rolled_back" and j["reason"] == "early_exit"
    deadline = time.time() + 20
    while time.time() < deadline and not (install / "ran-6.txt").exists():
        time.sleep(0.2)
    assert (install / "ran-6.txt").exists(), "the old Courier was started again"
    assert uv.read_courier_build((install / "courier.py").read_bytes()) == 6 and (tmp_path / "courier.json").read_bytes() == SETTINGS
    time.sleep(5)


# ---- security round two (2026-10-24): SF1, SF2, SF4, NH5 ------------------------------------------------------------------------------------------------
def test_the_helper_checks_with_the_installed_checker_not_the_packages_own(tmp_path):
    """SF1: a staged update_verify.py that says yes to everything must not be what decides."""
    install, old, dead = real_world(tmp_path, "confirm")
    staged = install / "update" / "staging" / "7"
    (staged / "update_verify.py").write_text("class UpdateRejected(Exception):\n    code = 'x'\n\ndef verify_staged(*a, **k):\n    return {'files': []}\n", encoding="utf-8")
    r = run_real_helper(install, dead)
    assert r.returncode == 1, r.stderr
    j = upd.read_journal(install / "update")
    assert j["state"] == "failed" and j["reason"] == "damaged"
    assert uv.read_courier_build((install / "courier.py").read_bytes()) == 6, "nothing was replaced"


def journal_for(tmp_path, **extra):
    install, update = tmp_path / "inst", tmp_path / "inst" / "update"
    (update / "previous").mkdir(parents=True)
    settings = tmp_path / "courier.json"
    j = {"state": "replacing", "backed_up": [], "created": [], "settings_file": str(settings)}
    j.update(extra)
    return install, update, settings, j


@pytest.mark.parametrize("bad", ["../escape.py", "..\\escape.py", "C:/Windows/evil.py", "/abs.py", "sub/x.py", "x.exe", 7, None])
def test_names_in_the_journal_that_are_not_flat_courier_file_names_are_ignored_when_restoring(tmp_path, bad):
    install, update, settings, j = journal_for(tmp_path, backed_up=["courier.py", bad], created=[bad])
    (install / "courier.py").write_text("new", encoding="utf-8")
    (update / "previous" / "courier.py").write_text("old", encoding="utf-8")
    (tmp_path / "escape.py").write_text("keep me", encoding="utf-8")
    assert upd.restore_previous(install, update, j) == []
    assert (install / "courier.py").read_text() == "old" and (tmp_path / "escape.py").read_text() == "keep me"


def test_the_inline_recovery_ignores_the_same_names(tmp_path):
    install, update, settings, j = journal_for(tmp_path, backed_up=["courier.py", "../escape.py"], created=["../escape.py"])
    (install / "courier.py").write_text("new", encoding="utf-8")
    (update / "previous" / "courier.py").write_text("old", encoding="utf-8")
    (tmp_path / "escape.py").write_text("keep me", encoding="utf-8")
    (update / "journal.json").write_text(json.dumps(j), encoding="utf-8")
    assert courier._recover_unfinished_update(base=install, build=6, now=time.time() + 600) == "restored"
    assert (install / "courier.py").read_text() == "old" and (tmp_path / "escape.py").read_text() == "keep me"


def test_a_settings_file_that_does_not_exist_or_is_not_called_courier_json_is_never_written(tmp_path):
    install, update, settings, j = journal_for(tmp_path)
    (update / "previous" / "courier.json").write_text(json.dumps({"code": "OLD"}), encoding="utf-8")
    upd.restore_previous(install, update, j)                                      # courier.json is not there: it is not created
    assert not settings.exists()
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    upd.restore_previous(install, update, {**j, "settings_file": str(other)})
    assert other.read_text() == "{}"
    assert upd.settings_target_ok("courier.json") is False, "not an absolute path"


def test_a_rollback_keeps_a_pairing_code_made_in_the_meantime(tmp_path):
    """SF4: "Make a new pairing code", then a rollback: the code made later stays; everything else comes back from the copy kept before the update."""
    install, update, settings, j = journal_for(tmp_path)
    (update / "previous" / "courier.json").write_text(json.dumps({"id": "abc", "code": "OLD-111", "paired": True, "paired_name": "Gaming PC", "port": 8110, "old_field": 1}), encoding="utf-8")
    settings.write_text(json.dumps({"id": "abc", "code": "NEW-222", "code_made_at": 99.0, "paired": False, "paired_name": "", "port": 8110, "new_field": 2}), encoding="utf-8")
    assert upd.restore_previous(install, update, j) == []
    got = json.loads(settings.read_text())
    assert got["code"] == "NEW-222" and got["paired"] is False and got["paired_name"] == "" and got["code_made_at"] == 99.0
    assert got["old_field"] == 1 and "new_field" not in got, "the rest is the settings the old Courier wrote"


def test_a_rollback_with_no_new_code_restores_the_copy_as_it_was(tmp_path):
    install, update, settings, j = journal_for(tmp_path)
    copy = json.dumps({"id": "abc", "code": "OLD-111", "paired": True}, indent=1)
    (update / "previous" / "courier.json").write_text(copy, encoding="utf-8")
    settings.write_text(json.dumps({"id": "abc", "code": "OLD-111", "paired": True, "migrated": True}), encoding="utf-8")
    upd.restore_previous(install, update, j)
    assert settings.read_text() == copy


def test_the_inline_recovery_keeps_a_newer_pairing_code_too(tmp_path):
    install, update, settings, j = journal_for(tmp_path)
    (update / "previous" / "courier.json").write_text(json.dumps({"code": "OLD-111", "paired": True, "paired_name": "Gaming PC"}), encoding="utf-8")
    settings.write_text(json.dumps({"code": "NEW-222", "paired": False, "paired_name": ""}), encoding="utf-8")
    (update / "journal.json").write_text(json.dumps(j), encoding="utf-8")
    assert courier._recover_unfinished_update(base=install, build=6, now=time.time() + 600) == "restored"
    got = json.loads(settings.read_text())
    assert got["code"] == "NEW-222" and got["paired"] is False


def test_a_backup_that_cannot_be_written_for_lack_of_permission_says_so(tmp_path, monkeypatch):
    """NH5: a permission error is no_permission, a full disk is no_space."""
    install, old, dead = real_world(tmp_path, "confirm")

    class Launcher:
        def start(self): pass
        def stop(self): pass
        def looks(self): return "running"
        def too_many_exits(self): return False

    env = upd.Env(install, install / "update", dead, Launcher())
    env.sleep = lambda s: None
    real = shutil.copyfile

    def boom(src, dst, *a, **k):
        if "previous" in str(dst):
            raise PermissionError("denied")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(shutil, "copyfile", boom)
    monkeypatch.setitem(sys.modules, "update_verify", uv)
    assert upd.apply_update(env) == "update_failed"
    assert upd.read_journal(install / "update")["reason"] == "no_permission"
