"""Courier: no flashing windows, and its log readable from the main device (2026-10-01).

Real problem (the director's laptop): the Courier has no console window (it's a tray app), and every helper program it ran for its
reports got a console window of its own for a split second, every few seconds. Now every program is started through
`hidden_process`, the common ones are not started at all (`native_probe`), the Courier's log can be read from the main device with
secrets masked, and a Courier device's row shows its latest problem.
"""
import ast
import io
import json
import sys
import time
from pathlib import Path

import pytest

import courier as cr
import courier_adapters as ca
import courier_client as cc
import gpu_monitor as gm
import hidden_process
import native_probe
import other_pcs as op
from tests.test_courier import env  # noqa: F401  (a running Courier with fake AI programs behind it)

ROOT = Path(__file__).resolve().parent.parent
WATCHED = ["courier.py", "courier_adapters.py", "courier_client.py", "courier_system.py", "courier_ui.py", "gpu_monitor.py", "gpu_guard.py",
           "local_models.py", "other_pcs.py", "multiproxy.py", "lmstudio_ui.py", "native_probe.py", "help_content.py", "help_ui.py", "installer_helper.py", "multiproxy_tray.py", "local_channel.py", "settings_files.py", "voice_engine.py", "voice_clips.py", "decision_engine.py", "decision_wiring.py", "voice_downloads.py", "voice_service.py", "voice_wiring.py"]
STARTERS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}


# ---- nothing may start a program except through hidden_process ----------------------------------------------------------
@pytest.mark.parametrize("name", WATCHED)
def test_no_module_starts_a_program_except_through_hidden_process(name):
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "subprocess" and node.attr in STARTERS:
                bad.append((node.lineno, f"subprocess.{node.attr}"))
            if node.value.id == "os" and node.attr in {"system", "popen", "spawnl", "spawnv", "execv"}:
                bad.append((node.lineno, f"os.{node.attr}"))
            if node.value.id == "asyncio" and node.attr.startswith("create_subprocess"):
                bad.append((node.lineno, f"asyncio.{node.attr}"))
    assert not bad, f"{name} starts a program directly (a console window would flash up on Windows): {bad}. Use hidden_process."


def test_the_guard_really_catches_a_bypass():
    tree = ast.parse("import subprocess\nsubprocess.run(['x'])\n")
    assert any(isinstance(n, ast.Attribute) and n.attr == "run" for n in ast.walk(tree))


def test_hidden_process_adds_no_window_on_windows_and_keeps_the_callers_own_flags(monkeypatch):
    seen = {}

    class FakeOs:
        name = "nt"

    monkeypatch.setattr(hidden_process, "os", FakeOs)
    monkeypatch.setattr(hidden_process.subprocess, "run", lambda *a, **kw: seen.update(kw) or "ran")
    assert hidden_process.run(["x"], creationflags=0x200) == "ran"
    assert seen["creationflags"] == 0x200 | hidden_process.CREATE_NO_WINDOW == 0x08000200
    hidden_process.run(["x"])
    assert seen["creationflags"] == hidden_process.CREATE_NO_WINDOW


def test_hidden_process_changes_nothing_elsewhere(monkeypatch):
    seen = {}

    class FakeOs:
        name = "posix"

    monkeypatch.setattr(hidden_process, "os", FakeOs)
    monkeypatch.setattr(hidden_process.subprocess, "Popen", lambda *a, **kw: seen.update(kw) or "p")
    hidden_process.popen(["x"], start_new_session=True)
    assert seen == {"start_new_session": True}


def test_it_really_runs_programs_and_streams_still_work():
    r = hidden_process.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "hi"


@pytest.mark.asyncio
async def test_the_async_version_runs_too():
    import asyncio
    p = await hidden_process.create_subprocess_exec(sys.executable, "-c", "print('hey')", stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    assert out.decode().strip() == "hey"


# ---- fewer programs: the numbers are read in-process, and read the same -----------------------------------------------------
def test_the_in_process_typeperf_text_is_read_by_the_existing_parser():
    items = [("pid_4242_luid_0x00000000_0x00015379_phys_0", 21474836480.0), ("pid_7777_luid_0x00000000_0x00015379_phys_0", 9771507712.0),
             ("pid_1111_luid_0x00000000_0x00017384_phys_0", 1048576.0)]
    text = native_probe.format_typeperf(items, "AREA-51", time.localtime())
    got = gm.parse_typeperf_gpu_process_memory(text)
    assert got == {4242: 20480, 7777: 9318}, "the busiest adapter is 'the card', same as with the real typeperf"


def test_the_in_process_process_list_is_read_by_the_existing_parser():
    text = native_probe.format_tasklist([("System", 4), ("SkyrimSE.exe", 4242), ("chrome.exe", 555)])
    assert gm.parse_tasklist_pids(text) == [4242]


def test_it_only_answers_what_it_knows_and_never_off_windows(monkeypatch):
    monkeypatch.setattr(native_probe.sys, "platform", "linux")
    assert native_probe.answer(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]) is None
    monkeypatch.setattr(native_probe.sys, "platform", "win32")
    assert native_probe.answer(["lms", "ls"]) is None
    assert native_probe.answer(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"]) is None, "a field it can't read: the real tool"
    assert native_probe.answer(["nvidia-smi"]) is None
    assert native_probe.answer(["tasklist", "/FI", "PID eq 4"]) is None
    assert native_probe.answer([]) is None


def test_any_failure_inside_it_falls_back_to_the_real_program(monkeypatch):
    monkeypatch.setattr(native_probe.sys, "platform", "win32")
    monkeypatch.setattr(native_probe, "_nvidia_smi", lambda a: (_ for _ in ()).throw(OSError("no nvml")))
    assert native_probe.answer(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"]) is None


def test_the_monitors_ask_it_first_and_only_then_start_a_program(monkeypatch):
    started = []
    monkeypatch.setattr(gm.hidden_process, "run", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(gm.native_probe, "answer", lambda cmd: "123, 456\n")
    assert gm._run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]) == "123, 456\n"
    assert started == []
    monkeypatch.setattr(gm.native_probe, "answer", lambda cmd: None)
    assert gm._run(["nvidia-smi"]) is None and len(started) == 1, "no in-process answer: the real program, hidden"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows readings")
def test_on_this_windows_pc_the_in_process_readings_are_real():
    procs = native_probe.process_list()
    assert procs and any(pid == __import__("os").getpid() for _, pid in procs)
    cards = native_probe.answer(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"])
    if cards is not None:                                                    # a device without an NVIDIA driver answers None
        import courier_system as cs
        parsed = cs.parse_nvidia_cards(cards)
        assert parsed and parsed[0]["memory_total_mb"] > 0


# ---- the Courier's log, read from the main device ----------------------------------------------------------------------------
def test_masking_hides_the_pairing_code_keys_tokens_and_chat_text():
    m = cr.mask_log_line
    assert m("Ready for your main device at 10.0.0.5:8110 (pairing code GVC-MBS).", "GVC-MBS") == "Ready for your main device at 10.0.0.5:8110 (pairing code •••-•••)."
    assert m("old code QAW-8G5 was replaced", "ZZZ-999") == "old code •••-••• was replaced", "anything shaped like a code, not just the current one"
    assert "gvc-mbs" not in m("code gvc-mbs", "GVC-MBS").lower()
    assert m("sent Authorization: Bearer abcdef123456", "") == "sent Authorization: Bearer •••" or "abcdef123456" not in m("sent Authorization: Bearer abcdef123456", "")
    assert "sk-proj-abcdefgh12345" not in m("key sk-proj-abcdefgh12345 rejected", "")
    assert "hunter2" not in m('config {"api_key": "hunter2"}', "") and "hunter2" not in m("password=hunter2", "")
    chat = m('2026-10-01 12:00:00 request {"messages": [{"role": "user", "content": "the secret plan"}]}', "")
    assert "secret plan" not in chat and chat.endswith("(chat text hidden)")
    assert m("2026-10-01 12:00:00 Ollama 0.35 started", "") == "2026-10-01 12:00:00 Ollama 0.35 started", "plain lines are left alone (dates aren't codes)"
    assert len(m("x" * 5000, "")) <= cr.LOG_MAX_LINE_CHARS + 1


def test_the_log_tail_is_capped_and_a_missing_file_is_just_empty(tmp_path):
    f = tmp_path / "courier.log"
    f.write_text("\n".join(f"line {i}" for i in range(5000)) + "\n", encoding="utf-8")
    got = cr.read_log_tail(f, 200)
    assert len(got) == 200 and got[-1] == "line 4999" and got[0] == "line 4800"
    assert cr.read_log_tail(tmp_path / "nope.log", 200) == []
    big = tmp_path / "big.log"
    big.write_bytes(b"A" * (3 * 1024 * 1024) + b"\nlast\n")
    out = cr.read_log_tail(big, 500)
    assert out[-1] == "last" and sum(len(x) for x in out) < cr.LOG_MAX_BYTES + 5000, "never more than the end of the file is read"


@pytest.mark.asyncio
async def test_the_courier_route_needs_the_pairing_code_masks_and_caps(env):
    code = env.courier.settings.code
    log_file = env.courier.settings.path.parent / "courier.log"
    log_file.write_text(f"first line\nReady (pairing code {code}).\nBearer abc123def456\n" + "\n".join(f"filler {i}" for i in range(900)) + "\n", encoding="utf-8")
    assert (await env.err("GET", "/courier/v1/log", headers={}))[0] in (401, 403)
    assert (await env.err("GET", "/courier/v1/log", headers={"Authorization": "Bearer ZZZ-999"}))[0] == 403
    data = await env.call("GET", "/courier/v1/log")
    assert 0 < len(data["lines"]) <= 200 and data["lines"][-1] == "filler 899"
    data = await env.call("GET", "/courier/v1/log?lines=900")
    assert len(data["lines"]) <= cr.LOG_MAX_LINES
    full = "\n".join((await env.call("GET", "/courier/v1/log?lines=500"))["lines"])
    assert code not in full and "abc123def456" not in full
    log_file.write_text(f"Ready (pairing code {code}).\n", encoding="utf-8")
    assert (await env.call("GET", "/courier/v1/log"))["lines"] == ["Ready (pairing code •••-•••)."]


@pytest.mark.asyncio
async def test_the_main_pcs_client_reads_it(env):
    (env.courier.settings.path.parent / "courier.log").write_text("hello\nworld\n", encoding="utf-8")
    client = cc.CourierClient(f"127.0.0.1:{env.courier.settings.port}", env.courier.settings.code, allow_loopback=True)
    assert await client.log() == ["hello", "world"]


@pytest.mark.asyncio
async def test_an_offline_pcs_log_says_so_plainly(proxy_module, monkeypatch):
    r = op.Remote(id="msi", kind="courier", name="msi", base="http://x", online=False)
    r.client = object()
    monkeypatch.setattr(proxy_module.remote_svc, "_courier", lambda rid: r)
    with pytest.raises(op.OtherPcsError) as e:
        await proxy_module.remote_svc.courier_log("msi")
    assert e.value.message == "msi is offline, so its details can't be read right now."


@pytest.mark.asyncio
async def test_a_log_that_cant_be_fetched_also_says_offline_not_a_stack_trace(proxy_module, monkeypatch):
    class Gone:
        async def log(self, lines=200):
            raise op.OtherPcsError(502, "Couldn't connect to Courier on that device.")
    r = op.Remote(id="msi", kind="courier", name="msi", base="http://x", online=True)
    r.client = Gone()
    monkeypatch.setattr(proxy_module.remote_svc, "_courier", lambda rid: r)
    with pytest.raises(op.OtherPcsError) as e:
        await proxy_module.remote_svc.courier_log("msi")
    assert e.value.message == "msi is offline, so its details can't be read right now."


@pytest.mark.asyncio
async def test_the_route_returns_lines_or_the_plain_error(proxy_module, test_client, monkeypatch):
    async def fake(rid):
        return ["a", "b"]
    monkeypatch.setattr(proxy_module.remote_svc, "courier_log", fake)
    assert test_client.get("/config/other-pcs/courier/log?id=msi").json() == {"lines": ["a", "b"]}

    async def offline(rid):
        raise op.OtherPcsError(502, op.log_offline_text(rid))
    monkeypatch.setattr(proxy_module.remote_svc, "courier_log", offline)
    r = test_client.get("/config/other-pcs/courier/log?id=msi")
    assert r.status_code == 502 and r.json()["detail"] == "msi is offline, so its details can't be read right now."


# ---- problems show on the row without opening the log -----------------------------------------------------------------------
def _courier_remote(programs, report_at=None):
    r = op.Remote(id="msi", kind="courier", name="msi", base="http://x", online=True)
    r.report = {"programs": programs}
    r.report_at = report_at if report_at is not None else time.time()
    return r


def test_a_program_that_wont_start_or_a_model_that_failed_is_the_rows_problem():
    p = op.problem_of(_courier_remote([{"id": "ollama", "name": "Ollama", "state": "error", "error": "Ollama failed to launch. Try starting it again from the Other devices tab.",
                                        "error_ago_seconds": 120, "models": []}]))
    assert p["message"].startswith("Ollama failed to launch") and 118 <= p["agoSeconds"] <= 125 and p["program"] == "Ollama"
    p = op.problem_of(_courier_remote([{"id": "llamacpp", "name": "llama.cpp", "state": "ready", "models": [{"id": "m", "state": "error", "error": "It ran out of graphics memory."}]}]))
    assert p == {"program": "llama.cpp", "message": "It ran out of graphics memory.", "agoSeconds": None}


def test_no_problem_when_all_is_well_and_it_clears_when_the_next_attempt_works():
    ok = [{"id": "ollama", "name": "Ollama", "state": "ready", "error": "", "models": [{"id": "m", "state": "loaded"}]}]
    assert op.problem_of(_courier_remote(ok)) is None
    r = op.Remote(id="den", kind="openai", name="den", base="http://x", online=True)
    assert op.problem_of(r) is None, "only a Courier reports problems"


def test_an_adapters_error_carries_when_it_happened_and_forgets_on_success():
    a = ca.OllamaAdapter(logging.getLogger("t"), exe=None)
    assert a.error == "" and a.error_ago_seconds() is None
    a.error = "Ollama couldn't load x."
    assert a.error_ago_seconds() == 0
    a._error_at -= 90
    assert 89 <= a.error_ago_seconds() <= 92
    a.error = ""
    assert a.error == "" and a.error_ago_seconds() is None


@pytest.mark.asyncio
async def test_the_state_the_tab_gets_carries_the_problem(proxy_module, test_client):
    r = _courier_remote([{"id": "ollama", "name": "Ollama", "state": "error", "error": "Ollama failed to launch.", "error_ago_seconds": 30, "models": []}])
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(proxy_module.remote_svc, "all", lambda: [r])
        row = test_client.get("/config/other-pcs").json()["servers"][0]
        assert row["problem"]["message"] == "Ollama failed to launch."
        r.online = False
        assert test_client.get("/config/other-pcs").json()["servers"][0]["problem"] is None, "an offline device says offline, not a stale problem"
    finally:
        monkey.undo()


import logging  # noqa: E402  (used by the adapter test above)
