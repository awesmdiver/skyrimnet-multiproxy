"""MultiProxy Courier (courier.py): the door, the report, pass-through and the live connection.

Real HTTP servers on this machine stand in for the home network and for the AI programs: the Courier's door binds to
127.0.0.1, a fake Ollama and a fake LM Studio answer behind it, and a fake llama-server runs behind the built-in engine.
"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

import courier as cr
import courier_adapters as ca
import courier_client as cc
import courier_system as cs
import local_models as lm
import other_pcs as op
from tests.fixtures.courier_fakes import FakeLmStudio, FakeOllama, free_port

pytestmark = pytest.mark.asyncio
FAKE_SERVER = str(Path(__file__).parent / "fixtures" / "fake_llama_server.py")
log = logging.getLogger("test-courier")

GPUS = [{"name": "RTX 3080", "vendor": "nvidia", "memory_used_mb": 8600, "memory_total_mb": 10240, "busy_percent": 64,
         "temperature_c": 71, "stats_available": True},
        {"name": "GTX 1060", "vendor": "nvidia", "memory_used_mb": 900, "memory_total_mb": 6144, "busy_percent": None,
         "temperature_c": None, "stats_available": False}]


class FakeAwake:
    def __init__(self): self.calls = []
    def start(self): self.calls.append("start"); return True
    def stop(self): self.calls.append("stop")


class FakeRegistry:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1
    def __init__(self): self.values = {}
    def OpenKey(self, root, path, res, access):
        class K:
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return K()
    def SetValueEx(self, k, name, res, typ, value): self.values[name] = value
    def QueryValueEx(self, k, name):
        if name not in self.values: raise FileNotFoundError(name)
        return self.values[name], 1
    def DeleteValue(self, k, name):
        if name not in self.values: raise FileNotFoundError(name)
        del self.values[name]


class Env:
    """A running Courier plus everything around it."""
    def __init__(self): self.sessions = []

    @property
    def base(self): return f"http://127.0.0.1:{self.courier.settings.port}"
    @property
    def headers(self): return {"Authorization": f"Bearer {self.courier.settings.code}"}

    async def call(self, method, path, body=None, headers=None, expect=200):
        async with aiohttp.ClientSession() as s:
            async with s.request(method, self.base + path, json=body, headers=headers if headers is not None else self.headers) as r:
                data = await r.json(content_type=None)
                assert r.status == expect, (r.status, data)
                return data

    async def err(self, method, path, body=None, headers=None):
        async with aiohttp.ClientSession() as s:
            async with s.request(method, self.base + path, json=body, headers=headers if headers is not None else self.headers) as r:
                return r.status, ((await r.json(content_type=None)) or {}).get("error", {}).get("message", "")


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "HEALTH_POLL_SECONDS", 0.05)
    monkeypatch.setattr(ca.shutil, "which", lambda name, mode=None, path=None: None)                 # no real llama-server / ollama / lms on the PATH
    e = Env()
    e.ollama = await FakeOllama().start()
    e.lms = FakeLmStudio()
    e.awake, e.registry = FakeAwake(), FakeRegistry()
    settings = cr.CourierSettings(tmp_path / "courier.json")
    settings.set(port=free_port())
    courier = cr.Courier(settings, log, bind_ips=["127.0.0.1"], discovery_port=free_port(), keep_awake=cs.KeepAwake(e.awake),
                         startup=cs.StartWithWindows("cmd", registry=e.registry), gpu_reader=lambda: GPUS)
    # the built-in llama.cpp engine, with a fake llama-server behind it
    folder = courier.local.set_folder(str(tmp_path / "models"))
    eng = folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-cpu"
    eng.mkdir(parents=True)
    (eng / ("llama-server.exe" if sys.platform == "win32" else "llama-server")).write_bytes(b"fake")
    courier.local._update(engine={"kind": "cpu", "build": lm.LLAMA_CPP_BUILD})
    courier.local._command_builder = lambda exe, m, port, gpu: [sys.executable, FAKE_SERVER, "--port", str(port)]
    courier.local._gpu_detector = lambda: lm.GpuInfo("Test GPU", 24576, "nvidia", cuda=(13, 4))
    gguf = tmp_path / "Den Model.gguf"
    gguf.write_bytes(b"x")
    courier.local.add_custom(str(gguf))
    e.lms_models = tmp_path / "lmmodels"
    courier.adapters = [ca.OllamaAdapter(log, base=e.ollama.base, exe="ollama"),
                        ca.LmStudioAdapter(log, base=e.lms.base, cli="lms", runner=e.lms.cli, models_dir=e.lms_models),
                        ca.LlamaCppAdapter(log, courier.local)]
    for a in courier.adapters:
        a.changed = courier.signal_change
    e.courier = courier
    await courier.start()
    yield e
    await courier.stop()
    await e.ollama.stop()
    await e.lms.stop()


# ============================================================================================
# courier.json
# ============================================================================================
class TestSettingsFile:
    async def test_first_run_makes_a_permanent_id_a_code_and_the_defaults(self, tmp_path):
        s = cr.CourierSettings(tmp_path / "courier.json")
        d = json.loads((tmp_path / "courier.json").read_text())
        assert len(d["id"]) == 32 and op.normalize_code(d["code"]) and d["keep_awake"] is True and d["start_with_windows"] is True
        assert d["port"] == cr.COURIER_PORT_DEFAULT == 8110

    async def test_the_id_and_code_survive_a_restart_and_a_router_address_change(self, tmp_path):
        a = cr.CourierSettings(tmp_path / "courier.json")
        b = cr.CourierSettings(tmp_path / "courier.json")
        assert (a.id, a.code) == (b.id, b.code)

    async def test_a_broken_file_starts_fresh_instead_of_crashing(self, tmp_path):
        (tmp_path / "courier.json").write_text("{not json")
        assert cr.CourierSettings(tmp_path / "courier.json").id

    async def test_it_is_also_the_config_the_shared_modules_read(self, tmp_path):
        s = cr.CourierSettings(tmp_path / "courier.json")
        assert s.load_cfg()["model_server"] is True, "no Skyrim on this device: the shared memory code reads this"
        local = cr.CourierLocalModels(s.load_cfg, s.save_cfg, log, pids_file=str(tmp_path / "p.json"))
        local.set_folder(str(tmp_path / "m"))
        assert json.loads((tmp_path / "courier.json").read_text())["local_models"]["folder"] == str(tmp_path / "m")


# ============================================================================================
# The door: home network only, the pairing code on every request
# ============================================================================================
class TestDoorAuth:
    async def test_no_code_is_refused_everywhere(self, env):
        for method, path in (("GET", "/courier/v1/report"), ("GET", "/v1/models"), ("POST", "/v1/chat/completions"),
                             ("POST", "/courier/v1/settings"), ("POST", "/courier/v1/pairing/new"), ("GET", "/share/info"),
                             ("POST", "/courier/v1/programs/ollama/models/load")):
            status, msg = await env.err(method, path, {}, headers={})
            assert status == 403 and "pairing code" in msg, path

    async def test_a_wrong_code_is_refused(self, env):
        status, _ = await env.err("GET", "/courier/v1/report", headers={"Authorization": "Bearer ZZZ-999"})
        assert status == 403

    async def test_the_code_works_as_a_bearer_token_or_the_older_header_and_ignores_case_and_dashes(self, env):
        code = env.courier.settings.code
        await env.call("GET", "/courier/v1/report", headers={"Authorization": f"Bearer {code}"})
        await env.call("GET", "/courier/v1/report", headers={op.CODE_HEADER: code})
        await env.call("GET", "/courier/v1/report", headers={"Authorization": f"Bearer {code.lower().replace('-', ' ')}"})

    async def test_an_address_that_is_not_the_home_network_gets_nothing_even_with_the_code(self, env, monkeypatch):
        monkeypatch.setattr(op, "ip_class", lambda ip: "other")
        status, msg = await env.err("GET", "/courier/v1/report")
        assert status == 403 and msg == "Connection refused: Courier only connects to devices on your private network or Tailscale."

    async def test_the_live_connection_is_refused_before_the_upgrade_without_the_code(self, env):
        async with aiohttp.ClientSession() as s:
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(env.base + "/courier/v1/ws")
            assert e.value.status == 403
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(env.base + "/courier/v1/ws", headers={"Authorization": "Bearer WRONG-1"})
            assert e.value.status == 403

    async def test_the_code_is_never_taken_from_the_address(self, env):
        status, _ = await env.err("GET", f"/courier/v1/report?code={env.courier.settings.code}", headers={})
        assert status == 403

    async def test_too_many_wrong_codes_and_it_stops_listening_for_a_minute(self, env):
        for _ in range(op.MAX_BAD_CODES):
            await env.err("GET", "/courier/v1/report", headers={"Authorization": "Bearer NOPE-00"})
        status, msg = await env.err("GET", "/courier/v1/report")
        assert status == 429 and "wait a minute" in msg.lower()

    async def test_the_door_binds_only_where_it_was_told_to(self, env):
        assert env.courier.addresses == [f"127.0.0.1:{env.courier.settings.port}"]

    async def test_a_new_pairing_code_locks_the_old_one_out_and_drops_whoever_was_connected(self, env):
        old = env.courier.settings.code
        async with aiohttp.ClientSession() as s:
            ws = await s.ws_connect(env.base + "/courier/v1/ws", headers=env.headers)
            await ws.receive_json()
            res = await env.call("POST", "/courier/v1/pairing/new")
            new = res["code"]
            assert new != old and env.courier.settings.code == new
            closed = await asyncio.wait_for(ws.receive(), 5)
            assert closed.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED) and ws.close_code == 4001
        assert (await env.err("GET", "/courier/v1/report", headers={"Authorization": f"Bearer {old}"}))[0] == 403
        await env.call("GET", "/courier/v1/report", headers={"Authorization": f"Bearer {new}"})
        assert json.loads(env.courier.settings.path.read_text())["code"] == new


class TestPairedAndUpdated:
    def test_a_start_with_a_different_version_notes_the_update_once(self, tmp_path):
        s = cr.CourierSettings(tmp_path / "courier.json")
        s.note_start("1.0")
        assert s.data["last_version"] == "1.0" and s.data["updated_from"] == ""      # the very first start is not an update
        s.note_start("1.1")
        assert cr.CourierSettings(tmp_path / "courier.json").data["updated_from"] == "1.0"
        s.note_start("1.1")                                                          # same version again: no update line
        assert s.data["updated_from"] == "" and s.data["last_version"] == "1.1"

    def test_a_gaming_pc_with_the_right_code_makes_this_pc_paired_and_a_new_code_clears_it(self, tmp_path):
        s = cr.CourierSettings(tmp_path / "courier.json")
        assert s.paired_name() is None
        s.note_pairing("Den PC")
        again = cr.CourierSettings(tmp_path / "courier.json")                        # survives a restart
        assert again.paired_name() == "Den PC"
        s.note_pairing("")                                                           # a poll without a name keeps the name
        assert s.paired_name() == "Den PC"
        s.clear_pairing()
        assert s.paired_name() is None and cr.CourierSettings(tmp_path / "courier.json").paired_name() is None

    async def test_pairing_over_the_door_saves_the_name_and_new_code_un_pairs(self, env, monkeypatch):
        monkeypatch.setattr(cr.other_pcs, "ip_class", lambda ip: "private")          # as if the request came from another device
        await env.call("GET", "/courier/v1/report", headers={**env.headers, "X-Courier-Client": "Den PC"})
        assert env.courier.settings.paired_name() == "Den PC"
        await env.call("POST", "/courier/v1/pairing/new")
        assert env.courier.settings.paired_name() is None

    async def test_this_pcs_own_calls_do_not_count_as_a_pairing(self, env):
        await env.call("GET", "/courier/v1/report", headers={**env.headers, "X-Courier-Client": "Den PC"})
        assert env.courier.settings.paired_name() is None

    def test_the_paired_command_prints_the_name_or_nothing(self, tmp_path, capsys):
        assert cr.main(["paired", "--config-dir", str(tmp_path)]) == 0
        assert capsys.readouterr().out.strip() == ""
        cr.CourierSettings(tmp_path / "courier.json").note_pairing("Den PC")
        cr.main(["paired", "--config-dir", str(tmp_path)])
        assert capsys.readouterr().out.strip() == "Den PC"


# ============================================================================================
# The report
# ============================================================================================
class TestReport:
    async def test_the_whole_shape(self, env):
        r = await env.call("GET", "/courier/v1/report")
        assert r["courier"]["id"] == env.courier.settings.id and r["courier"]["version"] == cr.COURIER_VERSION
        assert r["courier"]["uptime_seconds"] > 0 and r["courier"]["platform"] == sys.platform
        assert r["pc_memory"]["total_mb"] > 0 and r["disk"]["free_gb"] > 0 and "has_battery" in r["power"]
        assert set(r["settings"]) >= {"keep_awake", "start_with_windows", "models_folder", "firewall"}
        assert [p["id"] for p in r["programs"]] == ["ollama", "lmstudio", "llamacpp"]

    async def test_graphics_cards_are_a_list_and_a_missing_reading_is_null(self, env):
        r = await env.call("GET", "/courier/v1/report")
        assert [g["name"] for g in r["gpus"]] == ["RTX 3080", "GTX 1060"]
        assert r["gpus"][0]["busy_percent"] == 64 and r["gpus"][0]["temperature_c"] == 71
        assert r["gpus"][1]["busy_percent"] is None and r["gpus"][1]["temperature_c"] is None and r["gpus"][1]["stats_available"] is False

    async def test_every_program_reports_separately_with_a_simple_state(self, env):
        await env.lms.start()
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        by = {p["id"]: p for p in r["programs"]}
        assert set(by) == {"ollama", "lmstudio", "llamacpp"}
        for p in by.values():
            assert p["state"] in ca.STATES
        assert by["ollama"]["version"] == "0.12.3" and by["ollama"]["state"] == "ready"
        assert by["lmstudio"]["version"] == "0.4.1" and by["lmstudio"]["running"]

    async def test_a_program_that_is_installed_but_not_running_is_stopped(self, env):
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        lms = next(p for p in r["programs"] if p["id"] == "lmstudio")
        assert lms["installed"] and not lms["running"] and lms["state"] == "stopped"

    async def test_a_program_that_is_not_installed_is_left_out(self, env):
        await env.ollama.stop()
        env.courier.adapters[0]._exe = None
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        assert "ollama" not in [p["id"] for p in r["programs"]]

    async def test_a_program_that_was_installed_and_stopped_says_stopped_not_gone(self, env):
        await env.ollama.stop()
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        assert next(p for p in r["programs"] if p["id"] == "ollama")["state"] == "stopped"

    async def test_llamacpp_is_always_listed_and_says_whether_the_engine_is_here(self, env):
        r = await env.call("GET", "/courier/v1/report")
        p = next(p for p in r["programs"] if p["id"] == "llamacpp")
        assert p["installed"] and p["engine"]["kind"] == "builtin" and [m["id"] for m in p["models"]] == ["den-model"]
        assert p["available"] and p["available"][0]["id"] and p["models_folder"]

    async def test_the_graphics_memory_route_keeps_the_shape_the_monitor_reads(self, env, monkeypatch):
        async def fake(include_skyrim=True): return {"available": True, "card": "RTX 3080", "totalMb": 10240, "usedMb": 8600, "segments": [], "otherMb": 8600, "freeMb": 1640}
        monkeypatch.setattr(env.courier.monitor, "payload", fake)
        for path in ("/courier/v1/memory", "/share/memory"):
            d = await env.call("GET", path)
            assert d["available"] and d["totalMb"] == 10240 and d["card"] == "RTX 3080"

    async def test_the_memory_route_says_not_available_rather_than_inventing(self, env, monkeypatch):
        async def fake(include_skyrim=True): return {"available": False}
        monkeypatch.setattr(env.courier.monitor, "payload", fake)
        assert await env.call("GET", "/courier/v1/memory") == {"available": False}

    async def test_the_opening_line_is_in_the_log_then_a_plain_status_line(self, env, tmp_path, caplog):
        assert cr.OPENING_LINE == '[MultiProxy Courier] "I\'ve been looking for you..."'
        line = env.courier.status_line()
        assert line.startswith("Ready for your main device (pairing code ") and "127.0.0.1" not in line, "no addresses on the console line"
        assert "Found:" in line and "Ollama 0.12.3 (running)" in line and "Model engine (llama.cpp) (from MultiProxy)" in line
        assert "Graphics card" not in line and env.courier.settings.code in line


# ============================================================================================
# Ollama
# ============================================================================================
class TestOllama:
    async def test_lists_models_with_how_much_of_each_is_on_the_card(self, env):
        env.ollama.models["qwen3:14b"] = {"size": 9_000_000_000}
        env.ollama.models["llama3.1:8b"] = {"size": 5_000_000_000}
        env.ollama.loaded.update({"gemma4:12b": {}, "qwen3:14b": {}})
        env.ollama.vram_share.update({"gemma4:12b": 1.0, "qwen3:14b": 0.7})        # qwen spills 30% into system memory
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        by = {m["id"]: m for m in next(p for p in r["programs"] if p["id"] == "ollama")["models"]}
        assert by["gemma4:12b"]["loaded"] and by["gemma4:12b"]["on_card_percent"] == 100
        assert by["qwen3:14b"]["on_card_percent"] == 70
        assert by["llama3.1:8b"]["loaded"] is False and by["llama3.1:8b"]["on_card_percent"] is None and by["llama3.1:8b"]["state"] == "available"

    async def test_sees_pictures_comes_from_ollama(self, env):
        env.ollama.models["llava:7b"] = {"size": 4_000_000_000}
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        by = {m["id"]: m for m in next(p for p in r["programs"] if p["id"] == "ollama")["models"]}
        assert by["llava:7b"]["vision"] is True and by["gemma4:12b"]["vision"] is False

    async def test_load_keeps_the_model_loaded_and_unload_is_explicit(self, env):
        await env.call("POST", "/courier/v1/programs/ollama/models/load", {"model": "gemma4:12b", "wait": True})
        call = env.ollama.generate_calls[-1]
        assert call["model"] == "gemma4:12b" and call["keep_alive"] == -1, "stay loaded: Ollama's default drops it after 5 idle minutes"
        assert "gemma4:12b" in env.ollama.loaded
        await env.call("POST", "/courier/v1/programs/ollama/models/unload", {"model": "gemma4:12b"})
        assert env.ollama.generate_calls[-1]["keep_alive"] == 0 and not env.ollama.loaded

    async def test_loading_an_unknown_model_says_download_it_first(self, env):
        status, msg = await env.err("POST", "/courier/v1/programs/ollama/models/load", {"model": "nope:1b"})
        assert status == 404 and "Download it first" in msg

    async def test_delete_removes_it_and_a_second_delete_is_a_plain_404(self, env):
        await env.call("POST", "/courier/v1/programs/ollama/models/delete", {"model": "gemma4:12b"})
        assert env.ollama.deleted == ["gemma4:12b"]
        assert (await env.err("POST", "/courier/v1/programs/ollama/models/delete", {"model": "gemma4:12b"}))[0] == 404

    async def test_download_runs_in_the_background_with_real_progress(self, env):
        env.ollama.pull_delay = 0.6
        res = await env.call("POST", "/courier/v1/programs/ollama/models/download", {"model": "qwen3:14b"})
        assert res["download"]["state"] == "running", "the request doesn't wait for the download"
        seen = []
        for _ in range(80):
            r = await env.call("GET", "/courier/v1/report?fresh=1")
            d = next(p for p in r["programs"] if p["id"] == "ollama")["downloads"][0]
            seen.append((d["state"], d["percent"]))
            if d["state"] == "done":
                break
            await asyncio.sleep(0.1)
        assert seen[-1] == ("done", 100)
        assert any(0 < (p or 0) < 100 for s, p in seen), seen
        assert "qwen3:14b" in env.ollama.models

    async def test_a_failed_download_says_why(self, env):
        env.ollama.pull_error = "pull model manifest: file does not exist"
        await env.call("POST", "/courier/v1/programs/ollama/models/download", {"model": "nope:1b", "wait": True})
        d = (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"][0]["downloads"][0]
        assert d["state"] == "error" and "does not exist" in d["error"]

    async def test_a_download_can_be_cancelled(self, env):
        env.ollama.pull_delay = 1.0
        await env.call("POST", "/courier/v1/programs/ollama/models/download", {"model": "qwen3:14b"})
        await asyncio.sleep(0.2)
        await env.call("POST", "/courier/v1/programs/ollama/downloads/cancel", {"model": "qwen3:14b"})
        d = (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"][0]["downloads"][0]
        assert d["state"] == "cancelled"

    async def test_start_does_nothing_when_it_is_already_running(self, env):
        spawned = []
        env.courier.adapters[0]._spawn = lambda *a, **k: spawned.append(a)
        await env.call("POST", "/courier/v1/programs/ollama/start")
        assert spawned == []

    async def test_start_launches_ollama_serve_when_it_is_not_running(self, env):
        a = env.courier.adapters[0]
        a._exe = "C:\\Ollama\\ollama.exe"
        await env.ollama.stop()
        spawned = []

        def spawn(cmd, **kw):
            spawned.append(cmd)
            asyncio.get_running_loop().create_task(env.ollama.start())      # "Ollama comes up"
        a._spawn = spawn
        await env.call("POST", "/courier/v1/programs/ollama/start")
        assert spawned == [["C:\\Ollama\\ollama.exe", "serve"]]

    async def test_restart_stops_it_then_starts_it(self, env):
        a = env.courier.adapters[0]
        events = []
        a._kill = lambda: events.append("kill")
        a._spawn = lambda cmd, **kw: events.append("spawn")
        await env.call("POST", "/courier/v1/programs/ollama/restart")
        assert events[0] == "kill"                  # (it came straight back by itself in this fake, so no second spawn is needed)


# ============================================================================================
# LM Studio
# ============================================================================================
class TestLmStudio:
    async def test_lists_models_through_its_command_line_tool_and_skips_embedding_models(self, env):
        await env.lms.start()
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        p = next(p for p in r["programs"] if p["id"] == "lmstudio")
        assert [m["id"] for m in p["models"]] == ["mistral-small-3.2-24b"]
        assert p["models"][0]["name"] == "Mistral Small 3.2 24B" and p["models"][0]["on_card_percent"] is None, "LM Studio doesn't say: never guessed"

    async def test_load_and_unload(self, env):
        await env.lms.start()
        await env.call("POST", "/courier/v1/programs/lmstudio/models/load", {"model": "mistral-small-3.2-24b", "wait": True})
        assert ["load", "mistral-small-3.2-24b", "--yes"] in env.lms.cli_calls
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        assert next(p for p in r["programs"] if p["id"] == "lmstudio")["models"][0]["loaded"] is True
        await env.call("POST", "/courier/v1/programs/lmstudio/models/unload", {"model": "mistral-small-3.2-24b"})
        assert ["unload", "mistral-small-3.2-24b"] in env.lms.cli_calls and not env.lms.loaded

    async def test_start_server_through_the_tool(self, env):
        assert not env.lms.runner
        await env.call("POST", "/courier/v1/programs/lmstudio/start")
        assert ["server", "start"] in env.lms.cli_calls
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        assert next(p for p in r["programs"] if p["id"] == "lmstudio")["running"] is True

    async def test_the_tool_not_being_set_up_is_said_in_plain_words_with_what_to_click(self, env, monkeypatch):
        monkeypatch.setattr(ca, "find_lms", lambda: None)               # not on the PATH and not in its own folders
        a = ca.LmStudioAdapter(log, base=env.lms.base, cli=None, models_dir=env.lms_models)
        monkeypatch.setattr(a, "_app_installed", lambda: True)           # but LM Studio itself is there
        env.courier.adapters[1] = a
        status, msg = await env.err("POST", "/courier/v1/programs/lmstudio/models/load", {"model": "x"})
        assert status == 404 and "Install CLI" in msg
        p = next(p for p in (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"] if p["id"] == "lmstudio")
        assert p["installed"] and any("Install CLI" in n for n in p["notes"]), "said on the main device, with what to click in LM Studio"

    async def test_the_tool_is_found_in_its_own_folders_when_it_is_not_on_the_path(self, env, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".lmstudio" / "bin").mkdir(parents=True)
        exe = home / ".lmstudio" / "bin" / ("lms.exe" if sys.platform == "win32" else "lms")
        exe.write_bytes(b"x")
        monkeypatch.setattr(ca.Path, "home", staticmethod(lambda: home))
        assert ca.find_lms() == str(exe)
        assert (home / ".cache" / "lm-studio" / "bin") in ca.lms_search_dirs() and (home / ".lmstudio" / "bin") in ca.lms_search_dirs()

    def _fake_lms(self, tmp_path, lines, code=0):
        """A stand-in `lms` program (a real executable file) that prints `lines` for `get`."""
        if sys.platform == "win32":
            p = tmp_path / "fake-lms.bat"
            p.write_text("@echo off\r\n" + "".join(f"echo {ln.replace('%', '%%')}\r\n" for ln in lines) + f"exit /b {code}\r\n")
        else:
            p = tmp_path / "fake-lms.sh"
            p.write_text("#!/bin/sh\n" + "".join(f"printf '%s\\n' '{ln}'\n" for ln in lines) + f"exit {code}\n")
            p.chmod(0o755)
        return str(p)

    async def test_download_with_a_reported_percentage_shows_a_real_bar(self, env, tmp_path):
        a = ca.LmStudioAdapter(log, base=env.lms.base, cli=self._fake_lms(tmp_path, ["Resolving", "Downloading 10%", "Downloading 55%", "Downloading 100%"]),
                               runner=env.lms.cli)
        a.changed = env.courier.signal_change
        d = await a.download("qwen/qwen3-4b")
        await asyncio.gather(d.task)
        j = d.to_json()
        assert j["state"] == "done" and j["progress_known"] is True and j["percent"] == 100

    async def test_download_with_no_percentage_is_an_honest_no_bar(self, env, tmp_path):
        a = ca.LmStudioAdapter(log, base=env.lms.base, cli=self._fake_lms(tmp_path, ["Resolving", "Downloading..."]), runner=env.lms.cli)
        d = await a.download("qwen/qwen3-4b")
        assert d.progress_known is False and d.to_json()["percent"] is None
        await asyncio.gather(d.task)
        assert d.to_json()["state"] == "done" and d.progress_known is False

    async def test_a_failed_download_says_the_tools_own_last_line(self, env, tmp_path):
        a = ca.LmStudioAdapter(log, base=env.lms.base, cli=self._fake_lms(tmp_path, ["Resolving", "No such model: nope"], code=1), runner=env.lms.cli)
        d = await a.download("nope")
        await asyncio.gather(d.task)
        assert d.state == "error" and "No such model" in d.error

    async def test_delete_is_not_offered_because_lm_studio_has_no_safe_way_to_do_it(self, env):
        """Checked against the real LM Studio: `lms ls --json` gives a model KEY as its path (not a file), and `lms` has no delete."""
        await env.lms.start()
        status, msg = await env.err("POST", "/courier/v1/programs/lmstudio/models/delete", {"model": "mistral-small-3.2-24b"})
        assert status == 400 and msg == "LM Studio doesn't let other programs delete models. Delete it in LM Studio on that device."
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        assert next(p for p in r["programs"] if p["id"] == "lmstudio")["capabilities"]["delete"] is False

    async def test_the_server_not_running_means_stopped_and_no_models_are_claimed_loaded(self, env):
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        p = next(p for p in r["programs"] if p["id"] == "lmstudio")
        assert p["state"] == "stopped" and p["models"] == []


class TestParsers:
    async def test_lms_json_with_a_banner_line_or_wrapped_in_an_object(self):
        assert ca.parse_lms_json('Found models:\n[{"modelKey": "a"}]') == [{"modelKey": "a"}]
        assert ca.parse_lms_json('{"models": [{"modelKey": "b"}]}') == [{"modelKey": "b"}]
        assert ca.parse_lms_json("") == [] and ca.parse_lms_json("no json here") == [] and ca.parse_lms_json("[broken") == []

    async def test_llama_server_offload_line(self):
        assert ca.parse_offload("load_tensors: offloaded 33/33 layers to GPU") == 100
        assert ca.parse_offload("load_tensors: offloaded 20/40 layers to GPU\nlater: offloaded 10/40 layers to GPU") == 25, "the latest line wins"
        assert ca.parse_offload("nothing about layers") is None, "never guessed"
        assert ca.parse_offload("offloaded 0/0 layers to GPU") is None


# ============================================================================================
# llama.cpp (the built-in engine)
# ============================================================================================
class TestLlamaCpp:
    async def test_load_unload_and_state(self, env):
        await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": "den-model", "wait": True})
        p = next(p for p in (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"] if p["id"] == "llamacpp")
        assert p["state"] == "ready" and p["models"][0]["loaded"] is True
        await env.call("POST", "/courier/v1/programs/llamacpp/models/unload", {"model": "den-model"})
        p = next(p for p in (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"] if p["id"] == "llamacpp")
        assert p["state"] == "stopped" and p["models"][0]["loaded"] is False

    async def test_how_much_is_on_the_card_comes_from_its_own_startup_lines_or_is_unknown(self, env):
        await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": "den-model", "wait": True})
        srv = env.courier.local.servers["den-model"]
        m = (await env.courier.adapters[2].models())[0]
        assert m["on_card_percent"] is None, "the fake engine never printed an offload line: unknown, not a guess"
        Path(srv.log_path).write_text("load_tensors: offloaded 28/40 layers to GPU\n")
        assert (await env.courier.adapters[2].models())[0]["on_card_percent"] == 70

    async def test_the_catalog_says_what_can_be_downloaded(self, env):
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        av = next(p for p in r["programs"] if p["id"] == "llamacpp")["available"]
        assert {a["id"] for a in av} == {m["id"] for m in lm.CATALOG} and all(a["fit"] for a in av)

    async def test_an_unknown_model_download_is_a_plain_404(self, env):
        status, msg = await env.err("POST", "/courier/v1/programs/llamacpp/models/download", {"model": "not-a-model"})
        assert status == 404 and "isn't in MultiProxy's model list" in msg

    async def test_a_download_fetches_the_model_and_reports_progress_from_the_existing_downloader(self, env, tmp_path):
        spec = lm.CATALOG[0]
        job = lm.Job(f"model:{spec['id']}", spec["name"], [], total=1000, state="running")
        calls = []
        env.courier.local.download_model = lambda mid: (calls.append(mid), job)[1]
        res = await env.call("POST", "/courier/v1/programs/llamacpp/models/download", {"model": spec["id"]})
        assert res["download"]["state"] == "running"
        job.done = 400
        await asyncio.sleep(0.8)
        d = (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"][-1]["downloads"][0]
        assert d["percent"] == 40 and calls == [spec["id"]]
        job.done, job.state = 1000, "done"
        await asyncio.sleep(0.8)
        d = (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"][-1]["downloads"][0]
        assert d["state"] == "done" and d["percent"] == 100

    async def test_an_existing_llama_server_is_used_when_there_is_one(self, env, tmp_path, monkeypatch):
        exe = tmp_path / "mine" / "llama-server.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"x")
        env.courier.settings.set(llama_server_path=str(exe))
        assert env.courier.local.engine_exe() == exe and env.courier.local.external_engine() == exe
        p = next(p for p in (await env.call("GET", "/courier/v1/report?fresh=1"))["programs"] if p["id"] == "llamacpp")
        assert p["engine"]["kind"] == "existing"


# ============================================================================================
# Chat pass-through
# ============================================================================================
def chat(model, text="hello", stream=False, image=False):
    content = [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}] if image else text
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": content}]}


async def sse_text(resp) -> str:
    out = ""
    async for raw in resp.content:
        line = raw.decode().strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            out += json.loads(line[6:])["choices"][0]["delta"].get("content", "")
    return out


class TestPassThrough:
    async def test_a_chat_request_reaches_the_right_program(self, env):
        r = await env.call("POST", "/v1/chat/completions", chat("gemma4:12b", "hi there"))
        assert r["choices"][0]["message"]["content"] == "ollama:hi there"
        assert env.ollama.chat_calls[-1]["model"] == "gemma4:12b"

    async def test_streaming_comes_through_in_pieces(self, env):
        async with aiohttp.ClientSession() as s:
            async with s.post(env.base + "/v1/chat/completions", json=chat("gemma4:12b", "stream me", stream=True), headers=env.headers) as r:
                assert r.status == 200 and "text/event-stream" in r.headers["Content-Type"]
                assert await sse_text(r) == "ollama:stream me"

    async def test_pictures_go_through_untouched(self, env):
        r = await env.call("POST", "/v1/chat/completions", chat("gemma4:12b", "what is this", image=True))
        assert r["choices"][0]["message"]["content"] == "ollama:[picture] what is this"
        sent = env.ollama.chat_calls[-1]["messages"][0]["content"]
        assert sent[1]["image_url"]["url"] == "data:image/png;base64,AAAA"

    async def test_the_old_model_server_route_works_the_same(self, env):
        r = await env.call("POST", "/share/v1/chat/completions", chat("gemma4:12b", "old route"))
        assert r["choices"][0]["message"]["content"] == "ollama:old route"

    async def test_ollama_is_told_to_keep_the_model_loaded_before_the_first_request(self, env):
        assert "gemma4:12b" not in env.ollama.loaded
        await env.call("POST", "/v1/chat/completions", chat("gemma4:12b"))
        kept = [c for c in env.ollama.generate_calls if c["model"] == "gemma4:12b" and c.get("keep_alive") == -1]
        assert kept and "gemma4:12b" in env.ollama.loaded
        n = len(env.ollama.generate_calls)
        await env.call("POST", "/v1/chat/completions", chat("gemma4:12b"))
        assert len(env.ollama.generate_calls) == n, "already pinned and loaded: nothing more to ask"

    async def test_a_pinned_model_that_ollama_dropped_is_pinned_again(self, env):
        await env.call("POST", "/v1/chat/completions", chat("gemma4:12b"))
        env.ollama.loaded.clear()                          # e.g. Ollama restarted
        n = len(env.ollama.generate_calls)
        await env.call("POST", "/v1/chat/completions", chat("gemma4:12b"))
        assert len(env.ollama.generate_calls) == n + 1

    async def test_a_name_without_ollamas_latest_tag_still_finds_it(self, env):
        env.ollama.models["llama3"] = {"size": 1}
        env.ollama.models["llama3:latest"] = {"size": 1}
        r = await env.call("POST", "/v1/chat/completions", chat("llama3:latest"))
        assert r["choices"][0]["message"]["content"].startswith("ollama:")

    async def test_llamacpp_models_work_with_or_without_the_local_prefix_and_vision_params_are_stripped(self, env):
        for name in ("den-model", "local/den-model", "llamacpp/den-model"):
            r = await env.call("POST", "/v1/chat/completions", {**chat(name, "params?"), "thinking": {"x": 1}, "reasoning_effort": "high"})
            assert "thinking" not in r["choices"][0]["message"]["content"] and "reasoning_effort" not in r["choices"][0]["message"]["content"], name

    async def test_llamacpp_streaming_and_pictures(self, env):
        async with aiohttp.ClientSession() as s:
            async with s.post(env.base + "/v1/chat/completions", json=chat("den-model", "look", stream=True, image=True), headers=env.headers) as r:
                assert await sse_text(r) == "I see a picture. echo:look"

    async def test_lm_studio_models_route_to_lm_studio(self, env):
        await env.lms.start()
        r = await env.call("POST", "/v1/chat/completions", chat("mistral-small-3.2-24b", "yo"))
        assert r["choices"][0]["message"]["content"] == "lmstudio:yo"

    async def test_the_program_can_be_named_outright(self, env):
        r = await env.call("POST", "/v1/chat/completions", chat("ollama/gemma4:12b", "named"))
        assert r["choices"][0]["message"]["content"] == "ollama:named"

    async def test_two_programs_with_the_same_model_name_the_loaded_one_wins(self, env):
        await env.lms.start()
        env.lms.library.append({"type": "llm", "modelKey": "gemma4:12b", "displayName": "g", "path": "x", "sizeBytes": 1})
        env.lms.loaded["gemma4:12b"] = "gemma4:12b"
        await env.call("GET", "/courier/v1/report?fresh=1")
        r = await env.call("POST", "/v1/chat/completions", chat("gemma4:12b", "who"))
        assert r["choices"][0]["message"]["content"].startswith("lmstudio:")

    async def test_an_unknown_model_is_a_plain_404(self, env):
        status, msg = await env.err("POST", "/v1/chat/completions", chat("nobody-has-this"))
        assert status == 404 and msg == "This device doesn't have a model named 'nobody-has-this'."

    async def test_a_program_that_stopped_answering_is_a_plain_502(self, env):
        await env.call("GET", "/courier/v1/report?fresh=1")
        await env.ollama.stop()
        status, msg = await env.err("POST", "/v1/chat/completions", chat("gemma4:12b"))
        assert status == 502 and msg == "The program didn't answer. Try starting it again on the Other devices tab."

    async def test_the_model_list(self, env):
        d = await env.call("GET", "/v1/models")
        ids = {m["id"] for m in d["data"]}
        assert {"gemma4:12b", "den-model"} <= ids

    async def test_a_chat_makes_the_program_busy_while_it_answers(self, env):
        a = env.courier.adapters[0]
        seen = []

        async def watch():
            for _ in range(30):
                seen.append((await a.snapshot())["state"])
                await asyncio.sleep(0.05)
        env.ollama.load_delay = 0.4
        w = asyncio.create_task(watch())
        await env.call("POST", "/v1/chat/completions", chat("gemma4:12b"))
        await w
        assert "loading" in seen and seen[-1] == "ready"
        a.inflight = 1
        assert (await a.snapshot())["state"] == "busy"
        a.inflight = 0


# ============================================================================================
# Settings the player can flip
# ============================================================================================
class TestSettings:
    async def test_keep_awake_is_on_by_default_and_follows_the_setting(self, env):
        assert env.awake.calls == ["start"]
        r = await env.call("POST", "/courier/v1/settings", {"keep_awake": False})
        assert r["keep_awake"] is False and env.awake.calls == ["start", "stop"]
        assert json.loads(env.courier.settings.path.read_text())["keep_awake"] is False
        await env.call("POST", "/courier/v1/settings", {"keep_awake": True})
        assert env.awake.calls == ["start", "stop", "start"]

    async def test_start_with_windows_writes_and_removes_the_run_entry(self, env):
        assert env.registry.values == {"MultiProxy Courier": "cmd"}, "on by default"
        r = await env.call("POST", "/courier/v1/settings", {"start_with_windows": False})
        assert r["start_with_windows"] is False and env.registry.values == {}
        await env.call("POST", "/courier/v1/settings", {"start_with_windows": True})
        assert "MultiProxy Courier" in env.registry.values

    async def test_start_with_windows_where_it_is_the_installers_job_is_a_plain_400(self, env):
        env.courier.startup = None
        status, msg = await env.err("POST", "/courier/v1/settings", {"start_with_windows": False})
        assert status == 400 and "installer" in msg

    async def test_the_models_folder_can_be_changed(self, env, tmp_path):
        r = await env.call("POST", "/courier/v1/settings", {"models_folder": str(tmp_path / "elsewhere")})
        assert r["models_folder"] == str(tmp_path / "elsewhere") and (tmp_path / "elsewhere").is_dir()

    async def test_stopping_lets_go_of_the_awake_request(self, env):
        await env.courier.stop()
        assert env.awake.calls[-1] == "stop"
        await env.courier.start()                      # (the fixture stops it again)


# ============================================================================================
# The live connection
# ============================================================================================
class TestLiveConnection:
    async def test_hello_then_a_report_straight_away(self, env):
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(env.base + "/courier/v1/ws", headers={**env.headers, "X-Courier-Client": "GAMING-PC"}) as ws:
                hello = await ws.receive_json()
                first = await ws.receive_json()
        assert hello["type"] == "hello" and hello["courier"]["id"] == env.courier.settings.id
        assert first["type"] == "report" and first["reason"] == "connect" and first["report"]["programs"]

    async def test_a_state_change_is_pushed_at_once_not_at_the_next_beat(self, env, monkeypatch):
        monkeypatch.setattr(cr, "IDLE_PUSH_SECONDS", 30)
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(env.base + "/courier/v1/ws", headers=env.headers) as ws:
                await ws.receive_json(); await ws.receive_json()
                t0 = time.monotonic()
                await env.call("POST", "/courier/v1/programs/ollama/models/load", {"model": "gemma4:12b", "wait": True})
                got = None
                while time.monotonic() - t0 < 5:
                    msg = await asyncio.wait_for(ws.receive_json(), 5)
                    ollama = next(p for p in msg["report"]["programs"] if p["id"] == "ollama")
                    if any(m["loaded"] for m in ollama["models"]):
                        got = msg
                        break
                assert got and got["reason"] == "change" and time.monotonic() - t0 < 3

    async def test_an_idle_courier_still_sends_a_beat(self, env, monkeypatch):
        monkeypatch.setattr(cr, "IDLE_PUSH_SECONDS", 0.3)
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(env.base + "/courier/v1/ws", headers=env.headers) as ws:
                await ws.receive_json(); await ws.receive_json()
                msg = await asyncio.wait_for(ws.receive_json(), 3)
                assert msg["type"] == "report" and msg["reason"] == "interval"

    async def test_busy_means_the_faster_beat(self, env):
        assert env.courier.active() is False
        env.courier.adapters[0].inflight = 1
        await env.courier.report(fresh=True)
        assert env.courier.active() is True
        env.courier.adapters[0].inflight = 0

    async def test_download_progress_is_pushed_over_the_connection_not_polled(self, env):
        env.ollama.pull_delay = 0.7
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(env.base + "/courier/v1/ws", headers=env.headers) as ws:
                await ws.receive_json(); await ws.receive_json()
                await env.call("POST", "/courier/v1/programs/ollama/models/download", {"model": "qwen3:14b"})
                percents = []
                t0 = time.monotonic()
                while time.monotonic() - t0 < 10:
                    msg = await asyncio.wait_for(ws.receive_json(), 5)
                    dl = next(p for p in msg["report"]["programs"] if p["id"] == "ollama")["downloads"]
                    if dl:
                        percents.append(dl[0]["percent"])
                        if dl[0]["state"] == "done":
                            break
                assert percents[-1] == 100 and any(0 < (p or 0) < 100 for p in percents), percents

    async def test_connected_shows_who_and_it_clears_when_they_go(self, env):
        assert env.courier.connected() == (False, "")
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(env.base + "/courier/v1/ws", headers={**env.headers, "X-Courier-Client": "GAMING-PC"}) as ws:
                await ws.receive_json()
                assert env.courier.connected() == (True, "GAMING-PC")
        for _ in range(40):
            if not env.courier.clients:
                break
            await asyncio.sleep(0.05)
        assert env.courier.connected()[0] is True or env.courier.connected() == (False, "")      # (a recent plain request still counts)

    async def test_the_client_marks_the_courier_offline_at_once_when_the_connection_drops_and_reconnects_by_itself(self, env, monkeypatch):
        monkeypatch.setattr(cc, "RECONNECT_START", 0.1)
        events = []
        client = cc.CourierClient(env.base, env.courier.settings.code, "GAMING-PC", allow_loopback=True,
                                  on_report=lambda r: events.append(("report", r["courier"]["id"])),
                                  on_offline=lambda why: events.append(("offline", why)), on_online=lambda: events.append(("online", "")))
        client.start()
        for _ in range(60):
            if client.online:
                break
            await asyncio.sleep(0.05)
        assert client.online is True and client.report["courier"]["id"] == env.courier.settings.id
        t0 = time.monotonic()
        await env.courier.stop()
        for _ in range(100):
            if client.online is False:
                break
            await asyncio.sleep(0.05)
        assert client.online is False and time.monotonic() - t0 < 3, "marked offline at once, not at some later poll"
        await env.courier.start()
        for _ in range(100):
            if client.online:
                break
            await asyncio.sleep(0.1)
        assert client.online is True, "reconnected by itself"
        assert [e[0] for e in events if e[0] != "report"] == ["online", "offline", "online"]
        await client.stop()

    async def test_a_wrong_code_is_reported_plainly_and_a_new_code_is_explained(self, env, monkeypatch):
        monkeypatch.setattr(cc, "RECONNECT_MAX", 0.2)
        client = cc.CourierClient(env.base, "WRONG-1", allow_loopback=True)
        client.start()
        for _ in range(60):
            if client.online is False:
                break
            await asyncio.sleep(0.05)
        assert client.online is False and "Wrong pairing code" in client.error
        await client.stop()

    async def test_the_client_refuses_an_address_that_is_not_on_the_home_network(self):
        with pytest.raises(op.OtherPcsError):
            cc.CourierClient("8.8.8.8:8110", "KX7-42M")
        with pytest.raises(op.OtherPcsError):
            cc.CourierClient("127.0.0.1:8110", "KX7-42M")            # this device isn't "another device"

    async def test_the_client_can_drive_the_programs(self, env):
        client = cc.CourierClient(env.base, env.courier.settings.code, allow_loopback=True)
        rep = await client.fetch_report()
        assert rep["courier"]["id"] == env.courier.settings.id
        await client.action("ollama", "load", "gemma4:12b", wait=True)
        assert "gemma4:12b" in env.ollama.loaded
        await client.action("ollama", "unload", "gemma4:12b")
        assert await client.settings(keep_awake=False) is not None
        with pytest.raises(op.OtherPcsError) as e:
            await client.action("ollama", "load", "nope:1b")
        assert e.value.status == 404 and "Download it first" in e.value.message
        wrong = cc.CourierClient(env.base, "BAD-000", allow_loopback=True)
        with pytest.raises(op.OtherPcsError) as e:
            await wrong.fetch_report()
        assert e.value.status == 403


# ============================================================================================
# What an already-paired main device (a "model server" pairing) still sees
# ============================================================================================
class TestOlderRoutes:
    async def test_share_info_is_what_the_gaming_pcs_existing_check_accepts(self, env):
        d = await env.call("GET", "/share/info")
        assert d["product"] == "multiproxy" and d["instance"] == env.courier.settings.id and d["kind"] == "courier"
        assert d["gpu"]["name"] == "RTX 3080" and d["gpu"]["memoryGb"] == 10.0
        by = {m["id"]: m for m in d["models"]}
        assert by["gemma4:12b"]["state"] == "running", "Ollama loads on demand, so it's ready to use"
        assert by["den-model"]["state"] == "stopped" and by["den-model"]["program"] == "llamacpp"

    async def test_the_existing_remote_check_pairs_with_a_courier_and_uses_it(self, env, monkeypatch):
        monkeypatch.setattr(op, "check_host_allowed", lambda host, allow_loopback: None)
        store = {}
        remotes = op.RemoteServers(lambda: store, lambda d: store.update(d), log, lambda n: None, lambda: set())
        r = await remotes.add_multiproxy(f"127.0.0.1:{env.courier.settings.port}", env.courier.settings.code, "den-pc")
        assert r.online and r.instance == env.courier.settings.id
        assert "gemma4:12b" in [m["id"] for m in r.ready_models()]
        assert store["remote_servers"][0]["instance"] == env.courier.settings.id
        async with aiohttp.ClientSession() as s:
            async with s.post(r.api_base + "/chat/completions", json=chat("gemma4:12b", "via old pairing"), headers=r.headers()) as resp:
                assert (await resp.json())["choices"][0]["message"]["content"] == "ollama:via old pairing"


# ============================================================================================
# Discovery
# ============================================================================================
class TestDiscovery:
    async def test_it_announces_itself_with_the_ai_programs_it_found_to_the_pc_it_is_paired_with(self, env):
        await env.call("GET", "/courier/v1/report?fresh=1")
        env.courier.last_seen, env.courier.last_seen_ip = time.time(), "127.0.0.1"          # this host is the paired main device (it just made a request)
        found = await asyncio.to_thread(op.scan_network, 3.0, env.courier.discovery_port, "", ["127.0.0.1"])
        assert len(found) == 1
        f = found[0]
        assert f["instance"] == env.courier.settings.id and f["address"] == f"http://127.0.0.1:{env.courier.settings.port}"
        assert f["gpu"]["name"] == "RTX 3080" and f["courier"] == cr.COURIER_VERSION
        assert {(p["id"], p["version"]) for p in f["programs"]} >= {("ollama", "0.12.3"), ("llamacpp", lm.LLAMA_CPP_BUILD)}
        assert next(p for p in f["programs"] if p["id"] == "ollama")["running"] is True

    async def test_it_does_not_put_the_pairing_code_in_the_announcement(self, env):
        ans = json.dumps(env.courier._discovery_answer())
        assert env.courier.settings.code not in ans and "code" not in env.courier._discovery_answer()


class TestFollowAnAddressChange:
    async def test_a_paired_pc_that_got_a_new_address_is_followed_by_its_permanent_id(self, env, monkeypatch):
        monkeypatch.setattr(op, "check_host_allowed", lambda host, allow_loopback: None)
        store = {}
        remotes = op.RemoteServers(lambda: store, lambda d: store.update(d), log, lambda n: None, lambda: set())
        r = await remotes.add_multiproxy(f"127.0.0.1:{env.courier.settings.port}", env.courier.settings.code, "den-pc")
        r.base = f"http://127.0.0.1:{free_port()}"                           # the router handed out a new address (nothing answers there)
        await remotes._poll(r)
        assert r.online is False
        found = await asyncio.to_thread(op.scan_network, 2.5, env.courier.discovery_port, "", ["127.0.0.1"])
        moved = await remotes.follow_moved([r], scan=lambda: found)
        assert moved == 1 and r.base == f"http://127.0.0.1:{env.courier.settings.port}" and r.online is True
        assert store["remote_servers"][0]["base"] == r.base

    async def test_nothing_is_scanned_when_nothing_is_lost_or_it_has_no_id(self, env):
        store = {}
        remotes = op.RemoteServers(lambda: store, lambda d: store.update(d), log, lambda n: None, lambda: set())
        scanned = []
        assert await remotes.follow_moved([], scan=lambda: scanned.append(1) or []) == 0 and scanned == []
        old = op.Remote(id="old", kind="multiproxy", name="old", base="http://192.168.1.5:8100", online=False)      # paired before ids existed
        assert await remotes.follow_moved([old], scan=lambda: scanned.append(1) or []) == 0 and scanned == []


class TestReportIsNeverStaleAfterAnAction:
    async def test_asking_right_after_a_load_sees_the_model_loaded(self, env):
        """The report is cached for a moment; an action must not leave the next ask with the old picture (found on the real Ollama)."""
        await env.call("GET", "/courier/v1/report")
        await env.call("POST", "/courier/v1/programs/ollama/models/load", {"model": "gemma4:12b", "wait": True})
        r = await env.call("GET", "/courier/v1/report")
        assert next(p for p in r["programs"] if p["id"] == "ollama")["models"][0]["loaded"] is True


class TestWhoCountsAsConnected:
    async def test_this_pcs_own_status_calls_do_not_make_the_courier_look_connected(self, env):
        await env.call("GET", "/courier/v1/report")           # (a loopback caller: the status command, the ready window)
        assert env.courier.connected() == (False, "")


# ============================================================================================
# Quiet when idle: the programs are NOT asked what they have every second
# ============================================================================================
class _Clock:
    """Stands in for the `time` module inside courier_adapters, so five minutes pass in an instant."""
    def __init__(self): self.now = time.monotonic()
    def monotonic(self): return self.now
    def time(self): return time.time()
    def sleep(self, s): time.sleep(s)


def _lms_asks(env):
    cli = [c[0] for c in env.lms.cli_calls]
    return {"ls": cli.count("ls"), "ps": cli.count("ps"), "version": cli.count("version"),
            "models_api": env.lms.http_calls.count("GET /api/v0/models"), "up_check": env.lms.http_calls.count("GET /v1/models")}


class TestQuietWhenIdle:
    async def _idle(self, env, monkeypatch, seconds=300):
        clock = _Clock()
        monkeypatch.setattr(ca, "time", clock)
        await env.lms.start()
        await env.courier.report(fresh=True)
        for a in env.courier.adapters:                         # forget the start-up look: count from here
            a.refresh()
        env.lms.cli_calls.clear(); env.lms.http_calls.clear(); env.ollama.http_calls.clear()
        for _ in range(seconds):                               # what a connected main device makes the Courier do: a rebuild every second
            clock.now += 1.0
            await env.courier.report(max_age=0)
        return clock

    async def test_an_idle_lm_studio_is_asked_no_more_than_the_allowed_number_of_times_in_five_minutes(self, env, monkeypatch):
        await self._idle(env, monkeypatch)
        n = _lms_asks(env)
        assert n["ls"] <= 2, "downloaded models: no more than every 5 minutes"
        assert n["ps"] == 0, "its own web API answers 'which are loaded', so no second program is started for it"
        assert n["version"] <= 1, "the version never changes while the Courier runs"
        assert n["models_api"] <= 300 // 30 + 2, "loaded models: no more than every 30 seconds, one request each"
        assert n["up_check"] <= 300 // 10 + 2, "the cheap 'is it answering?' check: no more than every 10 seconds"

    async def test_an_idle_ollama_is_asked_no_more_than_the_allowed_number_of_times_in_five_minutes(self, env, monkeypatch):
        await self._idle(env, monkeypatch)
        calls = env.ollama.http_calls
        assert calls.count("GET /api/tags") <= 2 and calls.count("GET /api/ps") <= 300 // 30 + 2 and calls.count("GET /api/version") <= 300 // 10 + 2

    async def test_a_model_loaded_by_hand_in_lm_studio_still_shows_up_without_a_click(self, env, monkeypatch):
        clock = await self._idle(env, monkeypatch, seconds=5)
        env.lms.loaded["mistral-small-3.2-24b"] = "mistral-small-3.2-24b"          # the player loads it inside LM Studio itself
        clock.now += 31
        p = next(p for p in (await env.courier.report(max_age=0))["programs"] if p["id"] == "lmstudio")
        assert p["models"][0]["loaded"] is True

    async def test_a_change_made_through_the_courier_shows_immediately(self, env, monkeypatch):
        await self._idle(env, monkeypatch, seconds=3)
        await env.call("POST", "/courier/v1/programs/lmstudio/models/load", {"model": "mistral-small-3.2-24b", "wait": True})
        r = await env.call("GET", "/courier/v1/report")                             # (no fresh: the ordinary report)
        assert next(p for p in r["programs"] if p["id"] == "lmstudio")["models"][0]["loaded"] is True
        await env.call("POST", "/courier/v1/programs/lmstudio/models/unload", {"model": "mistral-small-3.2-24b"})
        r = await env.call("GET", "/courier/v1/report")
        assert next(p for p in r["programs"] if p["id"] == "lmstudio")["models"][0]["loaded"] is False

    async def test_asking_for_a_refresh_looks_again_at_once(self, env, monkeypatch):
        await self._idle(env, monkeypatch, seconds=3)
        env.lms.library.append({"type": "llm", "modelKey": "new-one", "displayName": "New One", "path": "x/new.gguf", "sizeBytes": 1})
        r = await env.call("GET", "/courier/v1/report?fresh=1")
        assert "new-one" in [m["id"] for m in next(p for p in r["programs"] if p["id"] == "lmstudio")["models"]]

    async def test_many_asking_at_once_share_one_check(self, env, monkeypatch):
        await self._idle(env, monkeypatch, seconds=1)
        a = env.courier.adapters[1]
        a.refresh()
        env.lms.http_calls.clear(); env.lms.cli_calls.clear()
        await asyncio.gather(*(a.models() for _ in range(10)))
        assert [c[0] for c in env.lms.cli_calls].count("ls") == 1 and env.lms.http_calls.count("GET /api/v0/models") == 1

    async def test_while_a_load_is_under_way_the_faster_rhythm_is_used_and_it_drops_back_after(self, env, monkeypatch):
        clock = await self._idle(env, monkeypatch, seconds=2)
        a = env.courier.adapters[1]
        a.loading.add("mistral-small-3.2-24b")
        env.lms.http_calls.clear()
        for _ in range(10):
            clock.now += 1.0
            await a.models()
        assert env.lms.http_calls.count("GET /api/v0/models") >= 4
        a.loading.clear()
        env.lms.http_calls.clear()
        for _ in range(10):
            clock.now += 1.0
            await a.models()
        assert env.lms.http_calls.count("GET /api/v0/models") <= 1

    async def test_a_connected_main_pc_for_a_few_real_seconds_adds_next_to_nothing(self, env):
        await env.lms.start()
        await env.courier.report(fresh=True)
        env.lms.cli_calls.clear(); env.lms.http_calls.clear()
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(env.base + "/courier/v1/ws", headers=env.headers) as ws:
                await ws.receive_json()                                              # hello
                await ws.receive_json()                                              # the report on connect (one forced look)
                await asyncio.sleep(3.5)                                             # the Courier's own once-a-second check runs meanwhile
        n = _lms_asks(env)
        assert n["ls"] <= 1 and n["ps"] == 0 and n["version"] == 0 and n["models_api"] <= 3 and n["up_check"] <= 3, n


class TestLlamaCppVersionAskedOnce:
    async def test_the_engine_version_is_not_a_program_start_every_second(self, env, monkeypatch):
        a = env.courier.adapters[2]
        asked = []
        monkeypatch.setattr(a, "_version", lambda exe: asked.append(exe) or "b1")
        monkeypatch.setattr(a.local, "external_engine", lambda: Path(sys.executable))
        for _ in range(5):
            await a.probe()
        assert len(asked) == 1
