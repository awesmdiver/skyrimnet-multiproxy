"""Check everything (2026-10-25): each check against a stand-in, all-good shows the one line, a broken or slow check shows nothing and spares the others, and a new entry just shows up."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile

import pytest

import fix_it
import overview_ui

NODE = shutil.which("node")


def run(coro):
    return asyncio.run(coro)


def ctx(**kw):
    return fix_it.Context(main_port=8000, claude_port=9999, own_pid=100, **kw)


def titles(res):
    return [p["title"] for p in res["problems"]]


def test_all_good_is_one_line_and_no_problems():
    res = run(fix_it.run_all(ctx()))
    assert res == {"problems": [], "allWell": "No problems found."}


def test_another_program_on_the_port_is_named_and_told_what_to_do_with_no_button():
    c = ctx(listener_pids=lambda port: [100, 555] if port == 8000 else [], process_name=lambda pid: "thing.exe")
    p = run(fix_it.run_all(c))["problems"]
    assert len(p) == 1 and p[0]["title"] == "Another program is using port 8000"
    assert "thing.exe" in p[0]["detail"] and "Close it, or change SkyrimNet's address." in p[0]["detail"]
    assert p[0]["action"] is None and p[0]["steps"] is None


def test_our_own_listener_is_not_a_problem_and_an_unnamed_program_says_so():
    assert run(fix_it.run_all(ctx(listener_pids=lambda port: [100]))) ["problems"] == []
    p = run(fix_it.run_all(ctx(listener_pids=lambda port: [7] if port == 9999 else [], process_name=lambda pid: None)))["problems"]
    assert p[0]["title"] == "Another program is using port 9999" and "did not say which program" in p[0]["detail"]


def test_a_real_listener_on_a_spare_port_is_found_by_the_real_lookup():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        if os.name == "nt":
            pids = fix_it.windows_listener_pids(port)
            assert os.getpid() in pids
            assert fix_it.process_name_of(os.getpid())
        else:
            pytest.skip("the Windows lookup")
    finally:
        s.close()


def test_netstat_text_is_read_for_every_listener():
    text = ("  TCP    0.0.0.0:8000     0.0.0.0:0    LISTENING    1234\n  TCP    127.0.0.1:8000   0.0.0.0:0    LISTENING    99\n"
            "  TCP    0.0.0.0:18000    0.0.0.0:0    LISTENING    5\n  TCP    10.0.0.2:8000    1.1.1.1:5   ESTABLISHED   7\n")
    assert fix_it.parse_netstat_listeners(text, 8000) == [1234, 99]


def test_an_unreachable_pc_gets_its_reason_and_numbered_steps_but_no_button():
    pcs = [{"id": "laptop", "name": "LAPTOP", "kind": "courier", "online": False, "error": "Couldn't reach Courier on that device."},
           {"id": "ok", "name": "UP", "kind": "courier", "online": True, "error": ""},
           {"id": "new", "name": "NEW", "kind": "courier", "online": None, "error": ""},
           {"id": "code", "name": "CODE", "kind": "courier", "online": False, "error": "x", "codeChanged": True},
           {"id": "lm", "name": "LM", "kind": "openai", "online": False, "error": "x"}]
    p = run(fix_it.run_all(ctx(pcs=lambda: pcs)))["problems"]
    assert len(p) == 1 and p[0]["title"] == "LAPTOP cannot be reached" and p[0]["detail"] == "Couldn't reach Courier on that device."
    assert len(p[0]["steps"]) >= 3 and "LAPTOP" in p[0]["steps"][0] and p[0]["action"] is None


def test_old_engine_has_an_update_button_only_when_an_update_exists():
    async def old():
        return {"old": True, "canUpdate": True}
    p = run(fix_it.run_all(ctx(engine=old)))["problems"]
    assert titles({"problems": p}) == ["The model engine is too old for the decision model"]
    assert p[0]["action"] == {"label": "Update", "url": "/config/local-models/engine/update"}
    p = run(fix_it.run_all(ctx(engine=lambda: {"old": True, "canUpdate": False})))["problems"]
    assert p[0]["action"] is None
    assert run(fix_it.run_all(ctx(engine=lambda: None)))["problems"] == []


def test_old_claude_code_shows_steps_and_a_current_one_nothing():
    p = run(fix_it.run_all(ctx(claude_too_old=lambda: True)))["problems"]
    assert p[0]["title"] == "Claude Code is out of date" and p[0]["steps"] and p[0]["action"] is None
    assert run(fix_it.run_all(ctx(claude_too_old=lambda: False)))["problems"] == []


def test_a_check_that_throws_or_times_out_says_nothing_and_the_others_still_run():
    def boom(c):
        raise RuntimeError("no")

    async def slow(c):
        await asyncio.sleep(5)
        return [fix_it.finding("x", "never")]

    reg = [fix_it.Check("boom", boom), fix_it.Check("slow", slow), fix_it.Check("claude", fix_it.check_claude)]
    res = run(fix_it.run_all(ctx(claude_too_old=lambda: True), registry=reg, timeout=0.2))
    assert titles(res) == ["Claude Code is out of date"] and res["allWell"] is None
    assert run(fix_it.run_all(ctx(), registry=reg[:2], timeout=0.2)) == {"problems": [], "allWell": "No problems found."}


def test_a_new_entry_in_the_list_shows_up_without_other_changes():
    fix_it.REGISTRY.append(fix_it.Check("extra", lambda c: [fix_it.finding("extra", "An extra problem", "Do this.")]))
    try:
        assert "An extra problem" in titles(run(fix_it.run_all(ctx())))
    finally:
        fix_it.REGISTRY.pop()


# ---- the page, drawn under Node ----
@pytest.mark.skipif(not NODE, reason="node is needed")
def test_the_page_shows_only_the_problems_and_the_one_good_line():
    from tests.test_overview import HARNESS, ESC
    res = {"problems": [{"key": "pc-a", "title": "LAPTOP cannot be reached", "detail": "No answer.", "steps": ["Turn it on.", "Run `claude update`"], "action": None},
                        {"key": "engine-old", "title": "Engine old", "detail": "Update it now.", "steps": None, "action": {"label": "Update", "url": "/u"}}], "allWell": None}
    src = (HARNESS + ESC + "\n" + overview_ui.JS + "\novCheck.result = " + json.dumps(res) + ";\nconst a = ovCheckBox(); ovCheck.open['pc-a'] = true; const b = ovCheckBox();\n"
           "ovCheck.result = { problems: [], allWell: 'No problems found.' }; const c = ovCheckBox();\novCheck.result = null; const d = ovCheckBox();\n"
           "console.log(JSON.stringify({a, b, c, d}));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    o = json.loads(out.stdout.decode("utf-8"))
    assert "LAPTOP cannot be reached" in o["a"] and "Show how" in o["a"] and "Update" in o["a"] and "<ol" not in o["a"]
    assert "<ol" in o["b"] and "<code>claude update</code>" in o["b"] and "Hide" in o["b"]
    assert "No problems found." in o["c"] and "<table" not in o["c"]
    assert "Nothing wrong" not in o["d"] and "Check everything" not in o["d"], "the button is on the SkyrimNet status line now"
