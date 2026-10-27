"""Uninstalling a program MultiProxy installed on another device (program_uninstall.py, courier.py's uninstall routes, the main device's routes and the rows).

Stand-ins only: temporary folders, the same fake release server and fake Anthropic the Claude tests use, the fake llama-server the Courier tests use. Nothing real
is installed, deleted or signed in; the codes here are made up.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest

import claude_courier as cc
import claude_wiring
import local_models as lm
import other_pcs as op
import program_uninstall as pu
from tests.ui_js_file import _ui_js_file
from tests.test_claude_courier import GOOD, courier, installed, signed_in, world        # noqa: F401  (fixtures)
from tests.test_courier import env                                                         # noqa: F401  (fixture)

def tree(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if not p.name.startswith("speed_history_test.db"))          # (the suite's own speed-history file sits in every test folder and its -wal/-shm come and go)


def make_link(link: Path, target: Path) -> bool:
    """A link (a junction on Windows, a symbolic link elsewhere) from `link` to the folder `target`. False when this system won't make one."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
            return r.returncode == 0
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        return False


# ============================================================================================
# The deleting itself: only ever inside the program's own folder
# ============================================================================================
class TestConfinement:
    def make(self, tmp_path):
        root = tmp_path / "courier"
        prog = root / "claude"
        (prog / "bin").mkdir(parents=True)
        (prog / "bin" / "claude.exe").write_bytes(b"x" * 100)
        (prog / "signin.dat").write_text("a made-up sign-in code")
        (root / "courier.json").write_text("{}")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("keep me")
        return root, prog, outside

    def test_the_program_folder_goes_and_nothing_beside_it(self, tmp_path):
        root, prog, outside = self.make(tmp_path)
        freed = pu.remove_program(root, prog)
        assert not prog.exists() and freed >= 100
        assert (root / "courier.json").exists() and (outside / "precious.txt").exists()

    def test_a_folder_that_is_not_there_is_nothing_to_do(self, tmp_path):
        root, prog, outside = self.make(tmp_path)
        pu.remove_program(root, prog)
        assert pu.remove_program(root, prog) == 0

    @pytest.mark.parametrize("how", ["dotdot", "absolute", "the_root", "a_drive_root", "a_sibling"])
    def test_a_path_outside_the_folder_is_refused_and_nothing_is_deleted(self, tmp_path, how):
        root, prog, outside = self.make(tmp_path)
        target = {"dotdot": prog / ".." / ".." / "outside", "absolute": outside, "the_root": root, "a_drive_root": Path(tmp_path.anchor),
                  "a_sibling": root / ".." / "courier-two"}[how]
        before = tree(tmp_path)
        with pytest.raises(pu.UninstallError) as e:
            pu.remove_program(root, target)
        assert e.value.status == 400
        assert tree(tmp_path) == before

    def test_a_folder_the_courier_keeps_is_refused_even_when_it_is_inside(self, tmp_path):
        root, prog, outside = self.make(tmp_path)
        (prog / "models").mkdir()
        (prog / "models" / "big.gguf").write_bytes(b"m")
        before = tree(tmp_path)
        with pytest.raises(pu.UninstallError):
            pu.remove_program(root, prog, protected=(prog / "models",))          # the target would hold a protected folder
        with pytest.raises(pu.UninstallError):
            pu.remove_program(root, prog, protected=(prog,))                     # the target is a protected folder
        assert tree(tmp_path) == before

    def test_a_link_inside_is_removed_and_what_it_points_at_survives(self, tmp_path):
        root, prog, outside = self.make(tmp_path)
        if not make_link(prog / "bin" / "elsewhere", outside):
            pytest.skip("this system won't make a link")
        pu.remove_program(root, prog)
        assert not prog.exists()
        assert (outside / "precious.txt").read_text() == "keep me", "the link was followed"

    def test_a_program_folder_that_is_itself_a_link_is_refused(self, tmp_path):
        root, prog, outside = self.make(tmp_path)
        pu.remove_program(root, prog)
        if not make_link(prog, outside):
            pytest.skip("this system won't make a link")
        with pytest.raises(pu.UninstallError):
            pu.remove_program(root, prog)
        assert (outside / "precious.txt").exists()

    def test_a_locked_file_is_retried_then_named_plainly_and_the_rest_is_removed(self, tmp_path, monkeypatch):
        root, prog, outside = self.make(tmp_path)
        real = os.unlink

        def locked(p, *a, **k):
            if str(p).endswith("claude.exe"):
                raise PermissionError("in use")
            return real(p, *a, **k)
        monkeypatch.setattr(os, "unlink", locked)
        with pytest.raises(pu.UninstallError) as e:
            pu.remove_program(root, prog, tries=2, wait=0)
        assert e.value.message == "MultiProxy removed what it could from <PC>, but 1 file is in use. Close what is using it and try again."
        assert not (prog / "signin.dat").exists(), "everything that could go, went"
        assert (outside / "precious.txt").exists()


# ============================================================================================
# Claude
# ============================================================================================
@pytest.mark.asyncio
class TestClaude:
    async def test_it_removes_the_program_the_saved_sign_in_code_and_the_models(self, world):
        await signed_in(world)
        h = world.host
        sibling = world.tmp / "keep.txt"
        sibling.write_text("not Claude's")
        assert h.token_file.exists() and h.status_json()["models"]
        s = await h.uninstall()
        assert s["state"] == "not_installed" and not s["installed"] and not s["signedIn"] and s["models"] == [] and s["freedBytes"] > 0
        assert not h.folder.exists() and not h.token_file.exists(), "the sign-in code is gone from disk"
        assert sibling.exists()
        assert GOOD not in json.dumps(s) + "\n".join(world.cap.lines)

    async def test_the_status_says_how_much_room_it_frees_when_installed(self, world):
        h = await installed(world)
        assert h.status_json(False)["diskBytes"] >= len(world.releases.payload)

    async def test_something_being_answered_makes_it_wait_and_nothing_is_deleted(self, world):
        await signed_in(world)
        h = world.host
        h.inflight = 1
        before = tree(world.tmp)
        with pytest.raises(cc.ClaudeError) as e:
            await h.uninstall()
        assert e.value.status == 409 and e.value.message == "Claude is busy on <PC> right now. Try again in a moment."
        assert tree(world.tmp) == before and h.token_file.exists()

    async def test_an_install_in_progress_makes_it_wait_too(self, world):
        h = world.host
        job = h.start_install()
        with pytest.raises(cc.ClaudeError) as e:
            await h.uninstall()
        assert e.value.message == cc.LINE_BUSY
        await job.task

    async def test_it_can_be_installed_again(self, world):
        await signed_in(world)
        h = world.host
        await h.uninstall()
        await signed_in(world, GOOD)
        assert world.host.status_json()["state"] == "ready"

    async def test_a_locked_file_leaves_no_sign_in_code_and_says_so(self, world, monkeypatch):
        await signed_in(world)
        h = world.host
        real = os.unlink

        def locked(p, *a, **k):
            if str(p).endswith(h.exe.name):
                raise PermissionError("in use")
            return real(p, *a, **k)
        monkeypatch.setattr(os, "unlink", locked)
        monkeypatch.setattr(pu.time, "sleep", lambda s: None)
        with pytest.raises(cc.ClaudeError) as e:
            await h.uninstall()
        assert "is in use" in e.value.message and not h.token_file.exists()

    async def test_through_the_courier_door_and_the_report_forgets_the_models(self, world, courier):
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        await courier.call("POST", "/courier/v1/claude/signin", {"code": GOOD})
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["claude"]["models"] and rep["claude"]["diskBytes"] > 0
        out = await courier.call("POST", "/courier/v1/claude/uninstall")
        assert out["state"] == "not_installed"
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["claude"]["state"] == "not_installed" and rep["claude"]["models"] == []
        assert not (courier.courier.settings.path.parent / "claude").exists()
        assert courier.courier.settings.path.exists(), "the Courier itself is never touched"
        models = (await courier.call("GET", "/v1/models"))["data"]
        assert not [m for m in models if m.get("owned_by") == "claude"]

    async def test_an_unpaired_request_is_refused(self, world, courier):
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        async with aiohttp.ClientSession() as s:
            async with s.post(courier.base + "/courier/v1/claude/uninstall", json={}) as r:
                assert r.status == 403
            async with s.post(courier.base + "/courier/v1/claude/uninstall", json={}, headers={"Authorization": "Bearer WRONG-CODE"}) as r:
                assert r.status == 403
        assert courier.courier.claude.installed()


# ============================================================================================
# llama.cpp: the program only, never a model
# ============================================================================================
def llama(rep):
    return next(p for p in rep["programs"] if p["id"] == "llamacpp")


@pytest.mark.asyncio
class TestLlamaCpp:
    def plant(self, env):
        """A downloaded model in the models folder and a file nobody owns beside it. The custom model the fixture added lives outside the folder."""
        folder = env.courier.local.folder
        m = folder / "models" / "some-model"
        m.mkdir(parents=True)
        (m / "model.gguf").write_bytes(b"m" * 50)
        stray = folder.parent / "not-ours.txt"
        stray.write_text("keep")
        return folder, m, stray

    async def test_the_program_folder_goes_and_every_model_stays(self, env):
        folder, m, stray = self.plant(env)
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        assert llama(rep)["engine"]["removable"] is True and llama(rep)["engine"]["diskBytes"] > 0
        out = await env.call("POST", "/courier/v1/programs/llamacpp/uninstall", {})
        assert out["ok"] is True and out["freedBytes"] > 0
        assert not (folder / "engine").exists()
        assert (m / "model.gguf").read_bytes() == b"m" * 50 and folder.is_dir() and stray.exists()
        assert [x for x in env.courier.local.installed() if x.id == "den-model"], "the player's own model file is still listed"
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        p = llama(rep)
        assert not p["installed"] and p["engine"]["kind"] == "none" and p["engine"]["removable"] is False
        assert env.courier.local._cfg().get("engine") is None

    async def test_a_running_model_is_stopped_first(self, env):
        await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": "den-model", "wait": True})
        assert env.courier.local.servers["den-model"].state == "running"
        await env.call("POST", "/courier/v1/programs/llamacpp/uninstall", {})
        assert env.courier.local.servers["den-model"].state == "stopped"
        assert not (env.courier.local.folder / "engine").exists()

    async def test_a_request_being_answered_makes_it_wait_and_nothing_is_deleted(self, env):
        a = env.courier.adapters[2]
        a.inflight = 1
        before = tree(env.courier.local.folder)
        status, msg = await env.err("POST", "/courier/v1/programs/llamacpp/uninstall", {})
        assert status == 409 and msg == "llama.cpp is busy on that device right now. Try again in a moment."
        assert tree(env.courier.local.folder) == before

    async def test_nothing_in_the_request_chooses_what_is_deleted(self, env):
        folder, m, stray = self.plant(env)
        victim = env.courier.local.folder.parent / "victim"
        victim.mkdir()
        (victim / "x.txt").write_text("keep")
        before = tree(victim.parent)
        evil = {"path": str(victim), "program": "../victim", "folder": str(victim), "target": str(victim), "model": "../models"}
        await env.call("POST", "/courier/v1/programs/llamacpp/uninstall", evil)
        assert (victim / "x.txt").exists() and (m / "model.gguf").exists()
        assert [p for p in tree(victim.parent) if p.startswith("victim")] == [p for p in before if p.startswith("victim")]
        for odd in ("..%2Fvictim", "%2e%2e", "..%5Cvictim"):
            async with aiohttp.ClientSession() as s:
                async with s.post(env.base + f"/courier/v1/programs/{odd}/uninstall", json={}, headers=env.headers) as r:
                    assert r.status in (400, 404), odd
            assert victim.exists() and m.exists()

    async def test_only_the_llamacpp_program_has_this_route(self, env):
        status, msg = await env.err("POST", "/courier/v1/programs/ollama/uninstall", {})
        assert status == 400 and "wasn't installed by MultiProxy" in msg

    async def test_a_link_in_the_program_folder_does_not_take_a_folder_it_points_at(self, env):
        folder, m, stray = self.plant(env)
        if not make_link(folder / "engine" / "points-at-models", folder / "models"):
            pytest.skip("this system won't make a link")
        await env.call("POST", "/courier/v1/programs/llamacpp/uninstall", {})
        assert (m / "model.gguf").exists(), "a model was deleted through a link"

    async def test_the_models_folder_is_never_the_target_even_if_it_is_where_the_program_is(self, env):
        folder = env.courier.local.folder
        env.courier.local.set_folder(str(folder / "engine"))                 # a models folder that IS the program folder: the engine would sit under it
        status, _ = await env.err("POST", "/courier/v1/programs/llamacpp/uninstall", {})
        assert (folder / "engine").is_dir()

    async def test_an_unpaired_request_is_refused(self, env):
        status, _ = await env.err("POST", "/courier/v1/programs/llamacpp/uninstall", {}, headers={})
        assert status == 403 and (env.courier.local.folder / "engine").is_dir()
        status, _ = await env.err("POST", "/courier/v1/programs/llamacpp/uninstall", {}, headers={"Authorization": "Bearer WRONG-CODE"})
        assert status == 403 and (env.courier.local.folder / "engine").is_dir()

    async def test_the_courier_itself_is_never_removed(self, env):
        await env.call("POST", "/courier/v1/programs/llamacpp/uninstall", {})
        assert env.courier.settings.path.exists()
        status, _ = await env.err("POST", "/courier/v1/programs/courier/uninstall", {})
        assert status == 404


# ============================================================================================
# The main device's side
# ============================================================================================
@pytest.mark.asyncio
class TestGamingSide:
    async def test_claude_uninstall_is_one_more_action_and_nothing_else_new_is_allowed(self):
        assert "uninstall" in claude_wiring.ACTIONS
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        calls = []

        class Remotes:
            async def courier_claude(self, rid, action, body=None):
                calls.append((rid, action, body))
                return {"state": "not_installed"}
        app = FastAPI()
        claude_wiring.setup(app, Remotes())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/config/claude/pc/uninstall", json={"id": "msi", "path": "C:/", "code": "x"})).json() == {"state": "not_installed"}
            assert (await c.post("/config/claude/pc/delete-everything", json={"id": "msi"})).status_code == 404
        assert calls == [("msi", "uninstall", {})], "nothing but the device's name is passed on"

    async def test_the_client_posts_the_program_uninstall_to_the_programs_route(self):
        import courier_client
        sent = []

        class C(courier_client.CourierClient):
            def __init__(self): pass
            async def _call(self, method, path, body=None, timeout=30):
                sent.append((method, path, body))
                return {"ok": True}
        await C().action("llamacpp", "uninstall")
        assert sent == [("POST", "/courier/v1/programs/llamacpp/uninstall", {})]

    async def test_the_models_list_forgets_claude_once_it_is_gone(self):
        assert op.models_from_report({"programs": [], "claude": {"state": "not_installed", "models": []}}) == []

    async def test_the_dashboard_route_accepts_uninstall_and_the_original_five_still_work(self):
        import multiproxy
        import inspect
        src = inspect.getsource(multiproxy.other_pcs_courier_action)
        assert '"stop", "uninstall")' in src and '"load", "unload", "delete", "download", "cancel", "start", "restart"' in src


# ============================================================================================
# The rows and the confirm
# ============================================================================================
HARNESS = r"""
const vm = require('vm');
const ghost = new Proxy({}, { has: () => true, get: (t, k) => (k in t ? t[k] : (k === Symbol.unscopables ? undefined : (k in globalThis ? globalThis[k] : () => ''))), set: (t, k, v) => { t[k] = v; return true; } });
vm.createContext(ghost);
const noop = () => new Proxy(function () {}, { get: () => noop(), apply: () => noop() });
for (const n of ['window', 'localStorage', 'navigator', 'history', 'location']) ghost[n] = noop();
const box = { style: {}, innerHTML: '', focus() {} };
ghost.document = { getElementById: () => box, addEventListener() {}, activeElement: null };
ghost.esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
ghost.jsq = (s) => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
ghost.opDot = (d, t) => '<i class="dot ' + d + '"></i>';
ghost.lmFmt = (b) => b >= 1e8 ? (b / 1e9).toFixed(1) + ' GB' : Math.round(b / 1e6) + ' MB';
ghost.lmPost = async () => ({});
for (const src of JSON.parse(require("fs").readFileSync(process.env.UI_JS_FILE, "utf-8"))) vm.runInContext(src, ghost);
const rep = (over) => Object.assign({
  claude: { state: 'ready', installed: true, diskBytes: 240e6, models: [] },
  programs: [{ id: 'llamacpp', name: 'llama.cpp', installed: true, state: 'ready', capabilities: {}, models: [], downloads: [], engine: { removable: true, diskBytes: 1.5e9 } }],
  voice: { installed: true, state: 'ready' }, decisions: { installed: true, state: 'ready' } }, over || {});
const r = (over) => ({ id: 'den', name: 'DEN-PC', report: rep(over) });
const servers = []; ghost.__servers = servers;
const run = (code) => vm.runInContext(code, ghost);
run("opState = { servers: __servers }");
const out = {};
const pc = r();
servers.push(pc);
out.claudeRow = ghost.ccPcRow(pc);
out.llamaRow = ghost.opProgramBlock(pc, pc.report.programs[0]);
out.voiceRow = ghost.vcPcRow(pc);
out.decisionRow = ghost.dcPcRow(pc);
const ask = (program) => { ghost.opAskUninstall('den', program); return box.innerHTML; };
out.dialogs = { claude: ask('claude'), llamacpp: ask('llamacpp'), voice: ask('voice'), decisions: ask('decisions') };
ghost.opUnClose(); out.closed = box.style.display;
run("ccBusy.den = 'uninstall'"); out.claudeBusy = ghost.ccPcRow(pc); run("ccBusy.den = ''");
run("opUnBusy['den:llamacpp'] = true"); out.llamaBusy = ghost.opProgramBlock(pc, pc.report.programs[0]); run("delete opUnBusy['den:llamacpp']");
run("opUnBusy['den:voice'] = true"); out.voiceBusy = ghost.vcPcRow(pc); run("delete opUnBusy['den:voice']");
run("opUnBusy['den:decisions'] = true"); out.decisionBusy = ghost.dcPcRow(pc); run("delete opUnBusy['den:decisions']");
const gone = r({ claude: { state: 'not_installed', installed: false, models: [] }, programs: [{ id: 'llamacpp', name: 'llama.cpp', installed: false, state: 'stopped', capabilities: {}, models: [], downloads: [], engine: { removable: false } }],
  voice: { installed: false, state: 'not_installed' }, decisions: { installed: false, state: 'not_installed' } });
out.claudeGone = ghost.ccPcRow(gone); out.llamaGone = ghost.opProgramBlock(gone, gone.report.programs[0]); out.voiceGone = ghost.vcPcRow(gone); out.decisionGone = ghost.dcPcRow(gone);
const noEngine = r({ programs: [{ id: 'llamacpp', name: 'llama.cpp', installed: true, state: 'ready', capabilities: {}, models: [], downloads: [], engine: { removable: false } }] });
out.llamaOwn = ghost.opProgramBlock(noEngine, noEngine.report.programs[0]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def ui():
    import shutil
    import claude_ui
    import decision_ui
    import other_pcs_ui
    import voice_ui
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't installed")
    srcs = [other_pcs_ui.OTHER_PCS_JS, claude_ui.CLAUDE_JS, voice_ui.VOICE_JS, decision_ui.DECISION_JS]
    r = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, UI_JS_FILE=_ui_js_file(srcs)), timeout=60)
    assert r.returncode == 0, r.stderr[:1500]
    return json.loads(r.stdout)


@pytest.mark.asyncio
class TestTheRowsAndTheConfirm:
    async def test_each_installed_program_has_an_uninstall_button_that_asks_first(self, ui):
        assert "opAskUninstall(&#39;den&#39;, &#39;claude&#39;)" in ui["claudeRow"].replace("'", "&#39;")
        for key, program in (("claudeRow", "claude"), ("llamaRow", "llamacpp"), ("voiceRow", "voice"), ("decisionRow", "decisions")):
            assert ">Uninstall</button>" in ui[key] and f"opAskUninstall('den', '{program}')" in ui[key], key

    async def test_the_confirm_says_what_goes_what_stays_and_that_it_can_come_back(self, ui):
        d = ui["dialogs"]
        assert "<h3>Uninstall Claude from DEN-PC?</h3>" in d["claude"]
        assert "This removes Claude and its sign-in from DEN-PC. Your Claude account is not touched. It frees about 0.2 GB. You can install it again any time." in d["claude"]
        assert "<h3>Uninstall the model engine (llama.cpp) from DEN-PC?</h3>" in d["llamacpp"]
        assert "This removes the engine program from DEN-PC. Your downloaded models stay. It frees about 1.5 GB. You can install it again any time: loading a model gets it again." in d["llamacpp"]
        assert "<h3>Uninstall Higgs voice from DEN-PC?</h3>" in d["voice"] and "Your own voice clips stay." in d["voice"]
        assert "<h3>Uninstall the local decision model from DEN-PC?</h3>" in d["decisions"] and "Your other providers are not affected." in d["decisions"]
        for text in d.values():
            assert 'class="vc-dialog"' in text and ">Cancel</button>" in text and ">Uninstall</button>" in text and 'class="vc-ft"' in text
            assert "code" not in text.replace("sign-in code", "").replace("code>", "") or "token" not in text.lower()
        assert ui["closed"] == "none"

    async def test_while_it_runs_the_row_says_uninstalling_and_has_no_buttons_to_press(self, ui):
        for key in ("claudeBusy", "llamaBusy", "voiceBusy", "decisionBusy"):
            assert "Uninstalling" in ui[key], key
            assert ">Uninstall</button>" not in ui[key], key

    async def test_when_it_is_done_the_row_goes_back_to_not_installed(self, ui):
        assert "Not installed" in ui["claudeGone"] and ">Uninstall</button>" not in ui["claudeGone"]
        assert "not installed" in ui["voiceGone"] and "not installed" in ui["decisionGone"]
        assert ">Uninstall</button>" not in ui["voiceGone"] + ui["decisionGone"] + ui["llamaGone"]

    async def test_an_engine_the_player_already_had_is_never_offered_for_removal(self, ui):
        assert ">Uninstall</button>" not in ui["llamaOwn"]
